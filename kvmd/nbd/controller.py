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
import dataclasses
import urllib.parse
import time

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
from .errors import NbdIsBusyError
from .errors import NbdBoundError
from .errors import NbdBindError
from .errors import NbdProbeError

from .types import NbdImage
from .types import BaseNbdEvent
from .types import NbdStartingEvent
from .types import NbdRunningEvent
from .types import NbdStoppedEvent
from .types import NbdStateDevice
from .types import NbdStateBinding
from .types import NbdState

from .device import NbdDevice
from .process import NbdProcess

from .remotes import BaseNbdRemote
from .remotes.http import NbdHttpRemote
from .remotes.smb import NbdSmbRemote
from .remotes.sftp import NbdSftpRemote


# =====
@dataclasses.dataclass(frozen=True)
class _Plan:
    url:    str
    params: dict[str, Any]


@dataclasses.dataclass(frozen=True)
class _Job:
    proc:          NbdProcess
    init_event:    asyncio.Event  # Running or stopped
    stopped_event: asyncio.Event


class NbdController:
    __REMOTES: Final[dict[str, Type[BaseNbdRemote]]] = {
        scheme: cls
        for cls in [NbdHttpRemote, NbdSmbRemote, NbdSftpRemote]
        for scheme in cls.get_schemes()
    }

    def __init__(self, path: str, use_blkroset: bool) -> None:
        self.__device = NbdDevice(path, use_blkroset)

        self.__region = aiotools.AioExclusiveRegion(NbdIsBusyError)
        self.__state = NbdState(time.monotonic(), NbdStateDevice(path), None)
        self.__nr = aiotools.AioNotifier()

        self.__plan: (_Plan | None) = None
        self.__job: (_Job | None) = None

    # =====

    async def force_disconnect(self) -> None:
        await self.__device.force_disconnect()

    def get_remotes(self) -> dict[str, dict[str, Any]]:
        return {
            scheme: {
                name: opt.default
                for (name, opt) in cls.get_options().items()
                if name != "url"
            }
            for (scheme, cls) in self.__REMOTES.items()
        }

    async def explore(self, url: str, **params: Any) -> NbdImage:
        params.pop("url", None)
        (_, image) = await self.__resolve("explore", url, **params)
        return image

    async def plan(self, url: str, **params: Any) -> NbdState:
        params.pop("url", None)
        with self.__region:
            if self.__job:
                raise NbdBoundError()
            (_, image) = await self.__resolve("explore", url, **params)
            self.__plan = _Plan(url, params)
            self.__update_binding(NbdStateBinding("", image, "planned", None))
            self.__nr.notify()
            return self.__state

    async def unplan(self) -> NbdState:
        with self.__region:
            if self.__job:
                raise NbdBoundError("NBD is still bound")
            self.__plan = None
            self.__update_binding(None)
            self.__nr.notify()
            return self.__state

    async def __resolve(self, func: str, url: str, **params: Any) -> tuple[BaseNbdRemote, NbdImage]:
        scheme = urllib.parse.urlparse(url).scheme
        cls = self.__REMOTES.get(scheme)
        if cls is None:
            raise ValidatorError("Unsupported remote URL scheme")

        assert "url" not in params
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

    async def bind(self) -> NbdState:
        with self.__region:
            self.__device.check_readiness()
            if self.__job:
                raise NbdBoundError()
            if self.__plan is None:
                raise NbdBindError("No planned NBD bindings")

            (remote, image) = await self.__resolve("probe", self.__plan.url, **self.__plan.params)
            # if image != self.__plan.image:
            #     raise NbdBindError("NBD planned and actual images mismatched")

            assert self.__job is None
            self.__nr.notify()
            self.__job = _Job(
                proc=NbdProcess(self.__device, remote, image),
                init_event=asyncio.Event(),
                stopped_event=asyncio.Event(),
            )
            proc = self.__job.proc
            try:
                try:
                    await asyncio.wait_for(self.__job.init_event.wait(), timeout=proc.get_timeout())
                except Exception as ex:
                    raise NbdBindError("NBD can't bind an image in time (timeout)", ex)
                if self.__state.binding is None:
                    raise NbdBindError("No NBD binding found")
                if self.__state.binding.id != proc.get_binding()[0]:
                    raise NbdBindError("NBD binding ID mismatch")
                if self.__state.binding.status not in ["running", "stopped"]:
                    raise NbdBindError("NBD can't bind an image in time (bad status)")
            except BaseException:
                proc.stop()
                raise
            return self.__state

    async def unbind(self) -> NbdState:
        job = self.__job
        if job:
            job.proc.stop()
            try:
                await asyncio.wait_for(job.stopped_event.wait(), timeout=job.proc.get_timeout())
            except Exception as ex:
                raise NbdBoundError("NBD can't unbind an image in time (timeout)", ex)
        return self.__state

    def get_state(self) -> NbdState:
        return self.__state

    async def poll_state(self) -> AsyncGenerator[NbdState]:
        logger = get_logger(0)
        async for event in self.__poll():
            if event:
                logger.info("NBD-EVENT: %s", event)

            match event:
                case None:
                    # Просто провернуться для plan/unplan
                    assert self.__job is None

                case NbdStartingEvent():
                    assert self.__job
                    self.__update_binding(NbdStateBinding(event.binding_id, event.image, "starting", None))

                case NbdRunningEvent():
                    assert self.__job
                    assert self.__state.binding
                    binding = self.__state.binding
                    self.__update_binding(NbdStateBinding(binding.id, binding.image, "running", event))
                    self.__job.init_event.set()

                case NbdStoppedEvent():
                    assert self.__job
                    assert self.__state.binding
                    binding = self.__state.binding
                    self.__update_binding(NbdStateBinding(binding.id, binding.image, "stopped", event))
                    self.__job.init_event.set()
                    self.__job.stopped_event.set()
                    self.__job = None

            yield self.__state

    def __update_binding(self, binding: (NbdStateBinding | None)) -> None:
        self.__state = NbdState(time.monotonic(), self.__state.device, binding)

    async def __poll(self) -> AsyncGenerator[BaseNbdEvent | None]:
        logger = get_logger(0)
        while True:
            await self.__nr.wait()
            if self.__job:
                yield NbdStartingEvent(*self.__job.proc.get_binding())
                stop: (NbdStoppedEvent | None) = None
                try:
                    async with self.__job.proc.running():
                        async for event in self.__job.proc.poll():
                            if isinstance(event, NbdStoppedEvent):
                                if stop is None:
                                    stop = event
                            else:
                                yield event
                except NbdError as ex:
                    logger.error("%s", tools.efmt(ex))
                except Exception:
                    logger.exception("Unexpected error in NBD poller loop")
                await self.__device.force_disconnect()
                if stop is None:
                    stop = NbdStoppedEvent("main", "Unknown stop reason", False)
                yield stop
            else:  # Просто провернуться для plan/unplan
                yield None
