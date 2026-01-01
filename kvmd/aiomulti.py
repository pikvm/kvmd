# ========================================================================== #
#                                                                            #
#    KVMD - The main PiKVM daemon.                                           #
#                                                                            #
#    Copyright (C) 2018-2024  Maxim Devaev <mdevaev@gmail.com>               #
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
import multiprocessing
import multiprocessing.queues
import multiprocessing.connection
import queue

from typing import Type
from typing import TypeVar
from typing import Generic

from . import aiotools


# =====
class AioMpQueue[T](multiprocessing.queues.Queue[T]):
    def __init__(self, maxsize: int=0) -> None:
        super().__init__(maxsize=maxsize, ctx=multiprocessing.get_context())

    def get_reader(self) -> multiprocessing.connection.Connection:
        return self._reader  # type: ignore  # pylint: disable=protected-access

    def get_reader_fd(self) -> int:
        return self.get_reader().fileno()

    async def async_fetch(self, timeout: float=0.0) -> tuple[bool, (T | None)]:
        return (await self.__async_get(timeout, False))

    async def async_fetch_last(self, timeout: float=0.0) -> tuple[bool, (T | None)]:
        return (await self.__async_get(timeout, True))

    async def __async_get(self, timeout: float, last_only: bool) -> tuple[bool, (T | None)]:
        loop = asyncio.get_running_loop()
        fut = asyncio.Future()  # type: ignore
        fd = self.get_reader_fd()

        try:
            loop.add_reader(fd, fut.set_result, None)
            fut.add_done_callback(lambda _: loop.remove_reader(fd))
            if timeout > 0:
                await asyncio.wait_for(fut, timeout)
            else:
                await fut

            if not last_only:
                return (True, self.get(False))

            got = False
            item: (T | None) = None
            while not self.empty():
                item = self.get(False)
                await asyncio.sleep(0)  # Switch task to prevent hanging in a loop
            return (got, item)
        except (TimeoutError, queue.Empty):
            return (False, None)
        finally:
            loop.remove_reader(fd)

    def fetch_last(self, timeout: float=0.0) -> tuple[bool, (T | None)]:
        try:
            item = self.get(timeout=timeout)
            while not self.empty():
                item = self.get()
            return (True, item)
        except queue.Empty:
            return (False, None)

    def clear_current(self) -> None:
        for _ in range(self.qsize()):
            try:
                self.get_nowait()
            except queue.Empty:
                break


# =====
class AioMpNotifier:
    def __init__(self) -> None:
        self.__queue: AioMpQueue[int] = AioMpQueue()

    def notify(self, mask: int=0) -> None:
        self.__queue.put_nowait(mask)

    async def wait(self, timeout: float=0) -> int:
        (got, mask) = await self.__queue.async_fetch(timeout)
        if not got:  # Timeout
            return -1
        assert mask is not None
        if got:
            while not self.__queue.empty():
                mask |= self.__queue.get()
                await asyncio.sleep(0)
        return mask


# =====
_SharedFlagT = TypeVar("_SharedFlagT", int, bool)


class AioSharedFlags(Generic[_SharedFlagT]):
    def __init__(
        self,
        initial: dict[str, _SharedFlagT],
        notifier: AioMpNotifier,
        type: Type[_SharedFlagT]=bool,  # pylint: disable=redefined-builtin
    ) -> None:

        self.__notifier = notifier
        self.__type: Type[_SharedFlagT] = type

        self.__flags = {
            key: multiprocessing.RawValue("i", int(value))  # type: ignore
            for (key, value) in initial.items()
        }

        self.__lock = multiprocessing.Lock()

    def update(self, **kwargs: _SharedFlagT) -> None:
        changed = False
        with self.__lock:
            for (key, value) in kwargs.items():
                value = int(value)  # type: ignore
                if self.__flags[key].value != value:
                    self.__flags[key].value = value
                    changed = True
        if changed:
            self.__notifier.notify()

    async def get(self) -> dict[str, _SharedFlagT]:
        return (await aiotools.run_async(self.__inner_get))

    def __inner_get(self) -> dict[str, _SharedFlagT]:
        with self.__lock:
            return {
                key: self.__type(shared.value)
                for (key, shared) in self.__flags.items()
            }
