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

import aiohttp

from ... import tools
from ... import htclient

from ...yamlconf import Option

from ...validators.basic import valid_bool
from ...validators.basic import valid_number
from ...validators.net import valid_url

from ..errors import NbdError
from ..errors import NbdRemoteError

from ..types import NbdImage

from . import BaseNbdRemote


# =====
class NbdHttpRemote(BaseNbdRemote):
    def __init__(
        self,
        url: str,
        verify: bool,
        user: str,
        passwd: str,
        timeout: float,
        retries_delay: float,
    ) -> None:

        super().__init__()

        self.__url = url
        self.__verify = verify
        self.__user = user
        self.__passwd = passwd
        self.__timeout = timeout
        self.__retries_delay = retries_delay

        self.__session: (aiohttp.ClientSession | None) = None

    # =====

    @classmethod
    def get_schemes(cls) -> set[str]:
        return set(["http", "https"])

    @classmethod
    def get_options(cls) -> dict[str, Option]:
        return {
            "url":           Option("", type=valid_url),
            "verify":        Option(True, type=valid_bool),
            "user":          Option(""),
            "passwd":        Option(""),
            "timeout":       Option(3.0, type=valid_number.mk(min=1.0, max=30.0, type=float)),
            "retries_delay": Option(5.0, type=valid_number.mk(min=1.0, max=30.0, type=float)),
        }

    # =====

    async def _do_probe(self) -> NbdImage:
        async with self.__make_session() as session:
            return (await self.__probe(session))

    async def _do_again(self) -> NbdImage:
        session = self.__ensure_session()
        return (await self.__probe(session))

    async def __probe(self, session: aiohttp.ClientSession) -> NbdImage:
        async with session.head(self.__url) as resp:
            htclient.raise_not_200(resp)
            cl = resp.content_length
            if not isinstance(cl, int) or cl < 0:
                raise NbdRemoteError(f"Invalid Content-Length: {cl}")
            return NbdImage(
                size=cl,
                rw=False,
                timeout=self.__timeout,
            )

    # =====

    async def _on_read(self, offset: int, size: int) -> bytes:
        errors = 0
        while True:
            try:
                if errors > 0:
                    await self._probe_again()
                data = (await self.__read(offset, size))
                if errors > 0:
                    await self._send_status_ok()
                    errors = 0
                return data
            except NbdError:
                raise
            except Exception as ex:
                errors += 1
                msg = f"READ: {tools.efmt(ex)}; Retrying ({errors}) ..."
                await self._send_status_error(msg)
                await asyncio.sleep(self.__retries_delay)

    async def __read(self, offset: int, size: int) -> bytes:
        session = self.__ensure_session()
        async with session.get(
            url=self.__url,
            headers={aiohttp.hdrs.RANGE: f"bytes={offset}-{offset + size}"},
        ) as resp:

            resp.raise_for_status()  # 206 partial is OK here
            return (await resp.read())[:size]

    async def _on_write(self, offset: int, data: bytes) -> None:
        _ = offset
        _ = data
        raise RuntimeError("WRITE should not be called for HTTP")

    # =====

    async def _do_cleanup(self) -> None:
        if self.__session:
            try:
                await self.__session.close()
            finally:
                self.__session = None

    # =====

    def __ensure_session(self) -> aiohttp.ClientSession:
        if self.__session is None:
            self.__session = self.__make_session()
        return self.__session

    def __make_session(self) -> aiohttp.ClientSession:
        return aiohttp.ClientSession(
            headers={aiohttp.hdrs.USER_AGENT: htclient.make_user_agent("KVMD-NBD")},
            connector=aiohttp.TCPConnector(ssl=self.__verify),
            auth=(aiohttp.BasicAuth(self.__user, self.__passwd) if self.__user else None),
            timeout=aiohttp.ClientTimeout(total=self.__timeout),

            # Don't ask for compression: https://github.com/aio-libs/aiohttp/issues/5513
            skip_auto_headers=frozenset([aiohttp.hdrs.ACCEPT_ENCODING]),
        )
