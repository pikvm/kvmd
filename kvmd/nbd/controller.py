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

from .types import NbdImage
from .types import BaseNbdEvent
from .types import NbdStartingEvent
from .types import NbdRunningEvent
from .types import NbdStoppedEvent
from .types import NbdStateBinding
from .types import NbdState

from .device import NbdDevice
from .process import NbdProcess

from .remotes import BaseNbdRemote
from .remotes.http import NbdHttpRemote
from .remotes.smb import NbdSmbRemote
from .remotes.sftp import NbdSftpRemote


# =====
class NbdController:
    __REMOTES: Final[dict[str, Type[BaseNbdRemote]]] = {
        scheme: cls
        for cls in [NbdHttpRemote, NbdSmbRemote, NbdSftpRemote]
        for scheme in cls.get_schemes()
    }

    def __init__(self, path: str, use_blkroset: bool) -> None:
        self.__device = NbdDevice(path, use_blkroset)
        self.__proc: (NbdProcess | None) = None
        self.__nr = aiotools.AioNotifier()
        self.__lock = asyncio.Lock()
        self.__state = NbdState(path, None)

    # =====

    async def force_disconnect(self) -> None:
        await self.__device.force_disconnect()

    def get_remotes(self) -> dict[str, dict[str, Any]]:
        return {
            scheme: {
                name: opt.default
                for (name, opt) in cls.get_options().items()
            }
            for (scheme, cls) in self.__REMOTES.items()
        }

    async def explore(self, url: str, **params: Any) -> NbdImage:
        (_, image) = await self.__resolve("explore", url, **params)
        return image

    async def __resolve(self, func: str, url: str, **params: Any) -> tuple[BaseNbdRemote, NbdImage]:
        scheme = urllib.parse.urlparse(url).scheme
        cls = self.__REMOTES.get(scheme)
        if cls is None:
            raise ValidatorError("Unsupported remote URL scheme")

        try:
            config = make_config({"url": url, **params}, {}, cls.get_options())
        except Exception as ex:
            raise ValidatorError(f"{cls.__name__}: {tools.efmt(ex)}")

        remote = cls(config)
        try:
            image = await getattr(remote, func)()
        except Exception as ex:
            raise NbdProbeError(f"{cls.__name__}: {tools.efmt(ex)}")

        self.__device.check_image(image)
        return (remote, image)

    async def bind(self, url: str, **params: Any) -> tuple[str, NbdImage]:
        async with self.__lock:
            self.__device.check_readiness()
            if self.__proc:
                raise NbdBoundError("NBD is already bound")

            (remote, image) = await self.__resolve("probe", url, **params)

            assert self.__proc is None
            self.__nr.notify()
            self.__proc = NbdProcess(self.__device, remote, image)
            return self.__proc.get_binding()

    async def unbind(self) -> None:
        if self.__proc:
            self.__proc.stop()

    def get_state(self) -> NbdState:
        return self.__state

    async def poll_state(self) -> AsyncGenerator[tuple[BaseNbdEvent, NbdState]]:
        async for event in self.__poll():
            match event:
                case NbdStartingEvent():
                    self.__state = NbdState(
                        self.__state.device,
                        NbdStateBinding(event.binding_id, event.image, "starting", None),
                    )
                case NbdRunningEvent():
                    assert self.__state.binding is not None
                    self.__state = NbdState(
                        self.__state.device,
                        NbdStateBinding(self.__state.binding.id, self.__state.binding.image, "running", event),
                    )
                case NbdStoppedEvent():
                    assert self.__state.binding is not None
                    self.__state = NbdState(
                        self.__state.device,
                        NbdStateBinding(self.__state.binding.id, self.__state.binding.image, "stopped", event),
                    )
            yield (event, self.__state)

    async def __poll(self) -> AsyncGenerator[BaseNbdEvent]:
        while True:
            await self.__nr.wait()
            if self.__proc:
                yield NbdStartingEvent(*self.__proc.get_binding())
                stop: (NbdStoppedEvent | None) = None
                try:
                    async with self.__proc.running():
                        async for event in self.__proc.poll():
                            if isinstance(event, NbdStoppedEvent):
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
                await self.__device.force_disconnect()
                if stop is None:
                    stop = NbdStoppedEvent("main", "Unknown stop reason", False)
                yield stop
