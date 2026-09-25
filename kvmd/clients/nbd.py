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


from typing import AsyncGenerator
from typing import Any

import aiohttp

from ..nbd.types import NbdImage
from ..nbd.types import NbdRunningEvent
from ..nbd.types import NbdStoppedEvent
from ..nbd.types import NbdStateBinding
from ..nbd.types import NbdState

from ..nbd.errors import NbdIsBusyError
from ..nbd.errors import NbdBindError
from ..nbd.errors import NbdProbeError

from ..validators import ValidatorError

from .. import htclient
from .. import htserver


# =====
class NbdClientError(Exception):
    pass


# =====
class NbdClient:
    def __init__(
        self,
        unix_path: str,
        timeout: float,
        user_agent: str,
    ) -> None:

        self.__unix_path = unix_path
        self.__timeout = timeout
        self.__user_agent = user_agent

    async def get_remotes(self) -> dict[str, Any]:
        async with self.__make_session() as session:
            async with session.get("/remotes") as resp:
                htclient.raise_not_200(resp)
                remotes = (await resp.json())["result"]
                assert isinstance(remotes, dict)
                return remotes

    async def explore(self, url: str, **params: Any) -> NbdImage:
        result = await self.__explore_or_bind("/explore", url, **params)
        return NbdImage(**result["image"])

    async def bind(self, url: str, **params: Any) -> NbdState:
        result = await self.__explore_or_bind("/bind", url, **params)
        return self.__parse_state(result)

    async def __explore_or_bind(self, handle: str, url: str, **params: Any) -> dict:
        async with self.__make_session() as session:
            data: dict[str, str] = {}
            for key in ["passwd"]:
                if key in params:
                    data[key] = params.pop(key)

            async with session.post(
                handle,
                params={"url": url, **params},
                data=(data or None),
            ) as resp:

                await htclient.raise_known_not_200(
                    resp,
                    NbdIsBusyError,
                    NbdBindError,
                    NbdProbeError,
                    ValidatorError,
                )
                return (await resp.json())["result"]

    def __parse_state(self, result: dict) -> NbdState:
        binding: (NbdStateBinding | None) = None
        if result["binding"] is not None:
            rb = result["binding"]
            info: (NbdRunningEvent | NbdStoppedEvent | None) = None
            match rb["status"]:
                case "running":
                    info = NbdRunningEvent(**rb["info"])
                case "stopped":
                    info = NbdStoppedEvent(**rb["info"])
            binding = NbdStateBinding(
                id=rb["id"],
                image=NbdImage(**rb["image"]),
                status=rb["status"],
                info=info,
            )
        return NbdState(result["device"], binding)

    async def unbind(self) -> None:
        async with self.__make_session() as session:
            async with session.post("/unbind") as resp:
                htclient.raise_not_200(resp)

    async def poll_state(self) -> AsyncGenerator[NbdState]:
        async with self.__make_session() as session:
            async with session.ws_connect("/ws") as ws:
                async for msg in ws:
                    if msg.type != aiohttp.WSMsgType.TEXT:
                        raise NbdClientError(f"Unexpected message type: {msg!r}")
                    (event_type, event) = htserver.parse_ws_event(msg.data)
                    if event_type == "nbd":
                        yield self.__parse_state(event)

    def __make_session(self) -> aiohttp.ClientSession:
        return aiohttp.ClientSession(
            base_url="http://localhost:0",
            headers={aiohttp.hdrs.USER_AGENT: self.__user_agent},
            connector=aiohttp.UnixConnector(path=self.__unix_path),
            timeout=aiohttp.ClientTimeout(total=self.__timeout),
        )
