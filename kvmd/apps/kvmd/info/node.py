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


import socket
import copy

from typing import AsyncGenerator

from .... import aiotools

from .base import BaseInfoSubmanager


# =====
class NodeInfoSubmanager(BaseInfoSubmanager):
    def __init__(self) -> None:
        self.__notifier = aiotools.AioNotifier()

    async def get_state(self) -> dict:
        return {"host": socket.gethostname()}

    async def trigger_state(self) -> None:
        self.__notifier.notify(1)

    async def poll_state(self) -> AsyncGenerator[(dict | None), None]:
        prev: dict = {}
        while True:
            if (await self.__notifier.wait(timeout=1)) > 0:
                prev = {}
            new = await self.get_state()
            pure = copy.deepcopy(new)
            if pure != prev:
                prev = pure
                yield new
