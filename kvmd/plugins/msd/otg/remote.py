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
import contextlib

from typing import Generator
from typing import AsyncGenerator
from typing import Any

import aiohttp

from .... import tools

from ....clients.nbd import NbdClient
from ....nbd.types import NbdImage
from ....nbd.types import NbdState
from ....nbd.errors import NbdError

from .. import MsdOperationError


# =====
class MsdRemoteError(MsdOperationError):
    pass


# =====
class Nbd:
    def __init__(self, client: NbdClient) -> None:
        self.__client = client
        self.__state: (NbdState | None) = None

    @property
    def image(self) -> (NbdImage | None):
        # Возвращает None, если NBD недоступен
        if self.__state and self.__state.binding:
            return self.__state.binding.image
        return None

    @property
    def asserted_ready_to_bind(self) -> bool:
        assert self.__state
        if self.__state.binding:
            return (self.__state.binding.status in ["planned", "stopped"])
        return False

    @property
    def asserted_running(self) -> bool:
        assert self.__state
        if self.__state.binding:
            return (self.__state.binding.status == "running")
        return False

    @property
    def asserted_path(self) -> str:
        assert self.__state
        return os.path.realpath(self.__state.device.path)

    async def plan(self, url: str, params: (dict[str, Any] | None)) -> NbdState:
        self.__check()
        with self.__catch():
            return self.__update(await self.__client.plan(url, (params or {})))[0]

    async def unplan(self) -> NbdState:
        self.__check()
        with self.__catch():
            return self.__update(await self.__client.unplan())[0]

    async def bind(self) -> NbdState:
        self.__check()
        with self.__catch():
            return self.__update(await self.__client.bind())[0]

    async def unbind(self) -> NbdState:
        self.__check()
        with self.__catch():
            return self.__update(await self.__client.unbind())[0]

    def __check(self) -> None:
        if self.__state is None:
            raise MsdRemoteError("Underlying KVMD-NBD service is offline")

    @contextlib.contextmanager
    def __catch(self) -> Generator[None]:
        try:
            yield
        except (aiohttp.ClientError, NbdError) as ex:
            raise MsdRemoteError(tools.efmt(ex))

    async def poll_for_changes(self) -> AsyncGenerator[None]:
        try:
            async for state in self.__client.poll_state():
                if self.__update(state)[1]:
                    yield None
        except BaseException:
            self.__state = None
            raise

    def __update(self, state: NbdState) -> tuple[NbdState, bool]:
        changed = False
        if bool(self.__state) ^ bool(state) or (self.__state and state and state.ts > self.__state.ts):
            self.__state = state
            changed = True
        assert self.__state
        return (self.__state, changed)
