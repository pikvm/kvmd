# ========================================================================== #
#                                                                            #
#    KVMD - The main PiKVM daemon.                                           #
#                                                                            #
#    Copyright (C) 2020  Maxim Devaev <mdevaev@gmail.com>                    #
#                                                                            #
#    This program is free software: you can redistribute it and/or modify    #
#    it under the terms of the GNU General Public License as published by    #
#    the Free Software Foundation, either version 3 of the License, or       #
#    (at your option) any later version.                                     #
#                                                                            #
#    This program is distributed in the hope that it will be useful,         #
#    but WITHOUT ANY WARRANTY; without even the implied warranty of          #
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the           #
#    GNU General Public License for more details.                            #
#                                                                            #
#    You should have received a copy of the GNU General Public License       #
#    along with this program.  If not, see <https://www.gnu.org/licenses/>.  #
#                                                                            #
# ========================================================================== #


import asyncio
import urllib.parse

from typing import Final
from typing import AsyncGenerator
from typing import Type
from typing import Any

from ..logging import get_logger

from .. import tools
from .. import aiotools

from ..yamlconf import make_config
from ..validators import ValidatorError

from .errors import NbdError
from .errors import NbdBoundError
from .errors import NbdProbeError

from .types import BaseNbdEvent
from .types import NbdSetupEvent
from .types import NbdStartEvent
from .types import NbdStatusEvent
from .types import NbdStopEvent
from .types import NbdStopped
from .types import NbdState

from .device import NbdDevice
from .process import NbdProcess

from .remotes import BaseNbdRemote
from .remotes.http import NbdHttpRemote


# =====
class NbdController:
    __DEVICE_BLOCK:   Final[int] = 512
    __DEVICE_TIMEOUT: Final[int] = 3600

    __REMOTES: Final[dict[str, Type[BaseNbdRemote]]] = {
        scheme: cls
        for cls in [NbdHttpRemote]
        for scheme in cls.get_schemes()
    }

    def __init__(self, path: str) -> None:
        self.__device_path = path
        self.__device = NbdDevice(path, self.__DEVICE_BLOCK, self.__DEVICE_TIMEOUT)
        self.__proc: (NbdProcess | None) = None
        self.__nr = aiotools.AioNotifier()
        self.__lock = asyncio.Lock()
        self.__state = NbdState()

    # =====

    def get_remotes(self) -> dict[str, dict[str, Any]]:
        return {
            scheme: {name: opt.default for (name, opt) in cls.get_options().items()}
            for (scheme, cls) in self.__REMOTES.items()
        }

    async def bind(self, url: str, **kwargs: Any) -> None:
        async with self.__lock:
            if self.__proc:
                raise NbdBoundError("NBD is already bound")

            scheme = urllib.parse.urlparse(url).scheme
            cls = self.__REMOTES.get(scheme)
            if cls is None:
                raise ValidatorError("Unsupported remote URL scheme")

            try:
                config = make_config({"url": url, **kwargs}, {}, cls.get_options())
            except Exception as ex:
                raise ValidatorError(f"{cls.__name__}: {tools.efmt(ex)}")

            remote = cls(**config._unpack())
            try:
                image = await remote.probe()
            except Exception as ex:
                raise NbdProbeError(f"{cls.__name__}: {tools.efmt(ex)}")

            assert self.__proc is None
            self.__nr.notify()
            self.__proc = NbdProcess(self.__device, remote, image)

    def unbind(self) -> None:
        if self.__proc:
            self.__proc.stop()

    def get_state(self) -> NbdState:
        return self.__state

    async def poll_state(self) -> AsyncGenerator[tuple[BaseNbdEvent, NbdState]]:
        async for event in self.__poll():
            match event:
                case NbdSetupEvent():
                    self.__state = NbdState(image=event.image)
                case NbdStartEvent():
                    assert self.__state.image is not None
                    assert self.__state.changed is None
                    assert self.__state.stopped is None
                    self.__state = NbdState(self.__state.image, bound=self.__device_path)
                case NbdStatusEvent():
                    assert self.__state.image is not None
                    assert self.__state.bound
                    assert self.__state.stopped is None
                    self.__state = NbdState(self.__state.image, bound=self.__device_path, changed=event)
                case NbdStopEvent():
                    assert self.__state.image is not None
                    self.__state = NbdState(stopped=NbdStopped(self.__state.image, event))
            yield (event, self.__state)

    async def __poll(self) -> AsyncGenerator[BaseNbdEvent]:
        while True:
            await self.__nr.wait()
            if self.__proc:
                yield NbdSetupEvent(self.__proc.get_image())
                stop: (NbdStopEvent | None) = None
                try:
                    async with self.__proc.running():
                        async for event in self.__proc.poll():
                            if isinstance(event, NbdStopEvent):
                                if stop is None:
                                    stop = event
                            else:
                                yield event
                except NbdError as ex:
                    get_logger(0).error("%s", tools.efmt(ex))
                except Exception:
                    get_logger(0).exception("Unexpected error in NBD poller loop")
                finally:
                    self.__proc = None
                if stop is None:
                    stop = NbdStopEvent("main", "Unknown stop reason", False)
                yield stop
