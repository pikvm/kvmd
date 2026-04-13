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


import secrets

from typing import Final

from ...yamlconf import Option

from ...validators.basic import valid_bool
from ...validators.basic import valid_number
from ...validators.os import valid_abs_path
from ...validators.auth import valid_user

from ...logging import get_logger

from ... import tools
from ... import aiotools

from . import BaseAuthService


# =====
class Plugin(BaseAuthService):
    def __init__(
        self,
        user:            str,
        passwd_len:      int,
        passwd_put_path: str,
        change_after_login: bool,
    ) -> None:  # pylint: disable=super-init-not-called

        self.__user:       Final[str] = user
        self.__path:       Final[str] = passwd_put_path
        self.__passwd_len: Final[int] = passwd_len
        self.__change_after_login: Final[bool] = change_after_login

        self.__passwd = self.__make_passwd()  # Just fill it with some valid passwd

    @classmethod
    def get_plugin_options(cls) -> dict:
        return {
            "user":       Option("onetime", type=valid_user),
            "passwd_len": Option(8, type=valid_number.mk(min=3, max=32)),
            "passwd_put": Option("/run/kvmd/otpasswd", type=valid_abs_path, unpack_as="passwd_put_path"),
            "change_after_login": Option(False, type=valid_bool),
        }

    async def sysprep(self) -> None:
        await self.__regen_passwd()

    async def authorize(self, user: str, passwd: str) -> bool:
        assert len(self.__passwd) == self.__passwd_len
        ok = ((user == self.__user) and (passwd == self.__passwd))
        if ok and self.__change_after_login:
            await self.__regen_passwd()
        return ok

    async def __regen_passwd(self) -> None:
        logger = get_logger(0)
        passwd = self.__make_passwd()
        try:
            await aiotools.write_file(self.__path, passwd)
        except Exception as ex:
            logger.error("Can't write passwd of user %r to %s: %s",
                         self.__user, self.__path, tools.efmt(ex))
        else:
            logger.info("New one-time passwd of user %r was written to %s",
                        self.__user, self.__path)
            self.__passwd = passwd

    def __make_passwd(self) -> str:
        chars = "23479ACDEFHJKLMNPQRTWXYZ"
        passwd = "".join(secrets.choice(chars) for _ in range(self.__passwd_len))
        return passwd
