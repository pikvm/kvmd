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


import aiohttp
import aiohttp.web

from typing import Final

from ...yamlconf import Option

from ...validators.basic import valid_bool
from ...validators.basic import valid_float_f01

from ...logging import get_logger

from ... import htclient

from . import BaseAuthService


# =====
class Plugin(BaseAuthService):
    def __init__(  # pylint: disable=super-init-not-called
        self,
        url:     str,
        verify:  bool,
        secret:  str,
        user:    str,
        passwd:  str,
        timeout: float,
    ) -> None:

        self.__url:      Final[str]   = url
        self.__verify:   Final[bool]  = verify
        self.__secret:   Final[str]   = secret
        self.__h_user:   Final[str]   = user
        self.__h_passwd: Final[str]   = passwd
        self.__timeout:  Final[float] = timeout

        self.__session: (aiohttp.ClientSession | None) = None

    @classmethod
    def get_plugin_options(cls) -> dict:
        return {
            "url":     Option("http://localhost/auth"),
            "verify":  Option(True, type=valid_bool),
            "secret":  Option(""),
            "user":    Option(""),
            "passwd":  Option(""),
            "timeout": Option(5.0, type=valid_float_f01),
        }

    async def authorize(self, user: str, passwd: str) -> bool:
        session = self.__ensure_session()
        try:
            async with session.post(
                url=self.__url,
                json={
                    "user":   user,
                    "passwd": passwd,
                    "secret": self.__secret,
                },
                headers={
                    aiohttp.hdrs.USER_AGENT: htclient.make_user_agent("KVMD"),
                    "X-KVMD-User": user,
                },
            ) as resp:
                htclient.raise_not_200(resp)
                return True
        except Exception:
            get_logger().exception("Failed HTTP auth request for user %r", user)
            return False

    async def cleanup(self) -> None:
        if self.__session:
            try:
                await self.__session.close()
            finally:
                self.__session = None

    def __ensure_session(self) -> aiohttp.ClientSession:
        if not self.__session:
            self.__session = self.__make_session()
        return self.__session

    def __make_session(self) -> aiohttp.ClientSession:
        return aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(ssl=self.__verify),
            auth=(aiohttp.BasicAuth(self.__h_user, self.__h_passwd) if self.__h_user else None),
            timeout=aiohttp.ClientTimeout(total=self.__timeout),
        )
