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
from ..nbd.types import NbdStatusEvent
from ..nbd.types import NbdStopped
from ..nbd.types import NbdState

from ..nbd.errors import NbdBoundError
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
            async with session.get("/state") as resp:
                htclient.raise_not_200(resp)
                remotes = (await resp.json())["result"]
                assert isinstance(remotes, dict)
                return remotes

    async def probe(self, url: str, **params: Any) -> None:
        async with self.__make_session() as session:
            async with session.post("/probe", params={"url": url, **params}) as resp:
                await self.__parse_response(resp)

    async def bind(self, url: str, **params: Any) -> str:
        async with self.__make_session() as session:
            async with session.post("/bind", params={"url": url, **params}) as resp:
                result = await self.__parse_response(resp)
                return result["device"]

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
                        yield NbdState(
                            image=(None if event["image"] is None else NbdImage(**event["image"])),
                            bound=event["bound"],
                            changed=(None if event["changed"] is None else NbdStatusEvent(**event["changed"])),
                            stopped=(None if event["stopped"] is None else NbdStopped(**event["stopped"])),
                        )

    async def __parse_response(self, resp: aiohttp.ClientResponse) -> dict:
        await htclient.raise_known_not_200(resp, NbdBoundError, NbdProbeError, ValidatorError)
        return (await resp.json())["result"]

    def __make_session(self) -> aiohttp.ClientSession:
        return aiohttp.ClientSession(
            base_url="http://localhost:0",
            headers={aiohttp.hdrs.USER_AGENT: self.__user_agent},
            connector=aiohttp.UnixConnector(path=self.__unix_path),
            timeout=aiohttp.ClientTimeout(total=self.__timeout),
        )
