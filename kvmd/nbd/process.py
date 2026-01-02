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
import signal
import contextlib

from typing import Final
from typing import Generator
from typing import AsyncGenerator

from ..logging import get_logger

from .. import tools
from .. import aiotools
from .. import aiomulti

from .errors import NbdError

from .types import NbdImage
from .types import BaseNbdEvent
from .types import NbdStartEvent
from .types import NbdStopEvent

from .device import NbdDevice
from .remotes import BaseNbdRemote
from .link import NbdLink


# =====
class NbdProcess:
    __QUEUE_SIZE:    Final[int] = 128
    __REACT_TIMEOUT: Final[int] = 3

    def __init__(
        self,
        device: NbdDevice,
        remote: BaseNbdRemote,
        image: NbdImage,
    ) -> None:

        self.__device = device
        self.__remote = remote
        self.__image = image

        self.__events_q: aiomulti.AioMpQueue[BaseNbdEvent] = aiomulti.AioMpQueue(self.__QUEUE_SIZE)
        self.__proc = aiomulti.AioMpProcess("NBD", "nbd", self.__subprocess)
        self.__ready_nr = aiomulti.AioMpNotifier()

    def stop(self) -> None:
        self.__proc.send_sigterm()

    @contextlib.asynccontextmanager
    async def running(self) -> AsyncGenerator[None]:
        logger = get_logger(0)
        logger.info("Starting NBD process ...")

        self.__proc.start()
        try:
            ready = await self.__ready_nr.wait(self.__image.timeout + self.__REACT_TIMEOUT)
            if ready < 0:  # pylint: disable=no-else-raise
                # No events - not started
                raise NbdError("NBD process did not respond in time at start")
            elif ready == 0:
                # Failed to start in time, but notified - wait for exiting
                await self.__proc.async_join(self.__REACT_TIMEOUT)
                return  # FIXME: defunc

            yield

        finally:
            try:
                if self.__proc.is_alive():
                    logger.info("Stopping NBD process with SIGTERM ...")
                    self.__proc.send_sigterm()
                    await self.__proc.async_join(self.__REACT_TIMEOUT)
            finally:
                if self.__proc.is_alive():
                    logger.info("Killing NBD process with SIGKILL ...")
                    self.__proc.sendpg_sigkill()

                alive = await self.__proc.async_join(self.__REACT_TIMEOUT)
                if not alive:
                    logger.info("NBD process stopped: retcode=%s", self.__proc.exitcode)
                else:
                    logger.error("Can't stop NBD process")

    async def poll(self) -> AsyncGenerator[BaseNbdEvent]:
        while self.__proc.is_alive():
            (got, event) = await self.__events_q.async_fetch(1)
            if got:
                assert event is not None
                yield event
        while not self.__events_q.empty():
            await asyncio.sleep(0)
            yield self.__events_q.get_nowait()

    def __subprocess(self) -> None:
        with self.__catch_exceptions("main", subtask=False):
            aiotools.run(self.__subprocess_loop())

    async def __subprocess_loop(self) -> None:
        async with NbdLink.opened() as link:
            tasks: list[asyncio.Task] = []

            def stop() -> None:
                # Сначала сделаем cancel(), чтобы таски завершились без ошибок,
                # а потом прибьем через shutdown(), чтоб наверняка.
                with link.shutdown_at_end():
                    for task in tasks:
                        task.cancel()
                self.__queue_event_noex(NbdStopEvent("main", "Shutdown"))

            for signum in [signal.SIGTERM, signal.SIGINT]:
                asyncio.get_running_loop().add_signal_handler(signum, stop)

            prepared = aiotools.AioStage()

            await aiotools.spawn_and_follow(
                self.__sub_device_server(link, prepared),
                self.__sub_remote_server(link),
                self.__sub_checker(link, prepared),
                tasks=tasks,
            )

    async def __sub_device_server(self, link: NbdLink, prepared: aiotools.AioStage) -> None:
        with self.__catch_exceptions("device_server"):
            with link.shutdown_at_end():
                async with self.__device.open_prepared(link, self.__image) as fd:
                    prepared.set_passed()
                    await self.__device.do_it(fd)

    async def __sub_remote_server(self, link: NbdLink) -> None:
        try:
            with self.__catch_exceptions("remote_server"):
                with link.shutdown_at_end():
                    await self.__remote.serve(link, self.__events_q)
        finally:
            await self.__remote.cleanup()

    async def __sub_checker(self, link: NbdLink, prepared: aiotools.AioStage) -> None:
        with self.__catch_exceptions("checker"):
            with link.shutdown_at_end():
                try:
                    await prepared.wait_passed()
                    await asyncio.wait_for(
                        self.__device.open_close(),
                        timeout=self.__image.timeout,
                    )
                except BaseException as ex:
                    self.__ready_nr.notify(0)
                    if isinstance(ex, TimeoutError):
                        raise NbdError("Can't open+close device in time")
                    raise
                self.__ready_nr.notify(1)
                self.__events_q.put_nowait(NbdStartEvent(self.__image, self.__device.get_path()))
                await aiotools.wait_infinite()

    @contextlib.contextmanager
    def __catch_exceptions(self, src: str, subtask: bool=True) -> Generator[None]:
        logger = get_logger(0)
        if subtask:
            logger.info("Starting subtask %s ...", src)
        msg = ""
        try:
            yield
        except asyncio.CancelledError:
            pass  # Normally we don't interested in this as a reason
        except NbdError as ex:
            msg = tools.efmt(ex)
            logger.error("%s", msg)
        except Exception as ex:
            msg = tools.efmt(ex)
            logger.exception("Unhandled exception")
        finally:
            if msg:
                self.__queue_event_noex(NbdStopEvent(src, msg))
            if subtask:
                logger.info("Subtask %s finished", src)

    def __queue_event_noex(self, event: BaseNbdEvent) -> None:
        try:
            self.__events_q.put_nowait(event)
        except Exception as ex:
            get_logger(0).error("Can't queue stop event: %s", tools.efmt(ex))
