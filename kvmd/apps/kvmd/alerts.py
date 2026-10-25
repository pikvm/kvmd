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


import os
import asyncio
import contextlib
import json
import time

from typing import Final
from typing import AsyncGenerator

import aiofiles.os

from ...logging import get_logger

from ...clients.pst import PstClient

from ... import tools
from ... import aiotools


# =====
class _StorageContext:
    __S_EVENT: Final = "@event.json"
    __S_ACK:   Final = "@ack.json"

    def __init__(self, path: str, rw: bool) -> None:
        self.__path: Final = path
        self.__rw:   Final = rw

    async def set_acked(self, name: str) -> None:
        assert self.__rw
        tools.check_name(name)
        get_logger(0).info("Acking '%s' ...", name)
        path = os.path.join(self.__path, name + self.__S_ACK)
        data = {"ack_ts": int(time.time())}
        await aiotools.write_file(path, json.dumps(data))

    async def is_raised(self, name: str) -> bool:
        tools.check_name(name)

        # XXX: Currently we ignore ack_ts
        path = os.path.join(self.__path, name + self.__S_ACK)
        if (await aiofiles.os.path.exists(path)):
            return False

        path = os.path.join(self.__path, name + self.__S_EVENT)
        try:
            data = json.loads(await aiotools.read_file(path))
            start_ts = float(data.get("start_ts", 0))
        except FileNotFoundError:
            return False
        except Exception:
            start_ts = 0
        if start_ts > time.time():
            return False
        return True


class _Storage:
    def __init__(self, pst: PstClient) -> None:
        self.__pst:  Final = pst
        self.__lock: Final = asyncio.Lock()

    @contextlib.asynccontextmanager
    async def readable(self) -> AsyncGenerator[_StorageContext]:
        # No locks here
        path = await self.__pst.get_path()
        yield _StorageContext(path, False)

    @contextlib.asynccontextmanager
    async def writable(self) -> AsyncGenerator[_StorageContext]:
        async with self.__lock:
            async with self.__pst.writable() as path:
                yield _StorageContext(path, True)


class Alerts:
    __A_DONATE: Final = "donate"
    __KNOWN:    Final = frozenset([__A_DONATE])

    def __init__(self, pst: PstClient) -> None:
        self.__storage:  Final = _Storage(pst)
        self.__notifier: Final = aiotools.AioNotifier()

    async def ack(self, names: list[str]) -> None:
        names = [
            name
            for name in sorted(set(names))
            if name in self.__KNOWN
        ]
        if names:
            async with self.__storage.writable() as ctx:
                for name in names:
                    await ctx.set_acked(name)

    async def get_state(self) -> dict:
        try:
            async with self.__storage.readable() as ctx:
                if (await ctx.is_raised(self.__A_DONATE)):
                    return {"active": {self.__A_DONATE: {}}}
        except Exception:
            pass
        return {"active": {}}

    async def trigger_state(self) -> None:
        self.__notifier.notify()

    async def poll_state(self) -> AsyncGenerator[dict]:
        while True:
            await self.__notifier.wait()
            yield (await self.get_state())
