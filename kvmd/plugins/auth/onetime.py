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

from ...validators.basic import valid_number
from ...validators.os import valid_abs_path
from ...validators.auth import valid_user

from ... import aiotools

from . import BaseAuthService


# =====
class Plugin(BaseAuthService):
    __MIN_LEN: Final[int] = 3

    def __init__(
        self,
        user: str,
        passwd_len: int,
        passwd_put_path: str,
    ) -> None:  # pylint: disable=super-init-not-called

        self.__user: Final[str] = user
        self.__path: Final[str] = passwd_put_path
        self.__passwd: Final[str] = self.__make_passwd(passwd_len)

    @classmethod
    def get_plugin_options(cls) -> dict:
        return {
            "user":       Option("onetime", type=valid_user),
            "passwd_len": Option(8, type=valid_number.mk(min=cls.__MIN_LEN, max=32)),
            "passwd_put": Option("/run/kvmd/otpasswd", type=valid_abs_path, unpack_as="passwd_put_path"),
        }

    async def sysprep(self) -> None:
        await aiotools.write_file(self.__path, self.__passwd)

    async def authorize(self, user: str, passwd: str) -> bool:
        assert user == user.strip()
        assert user
        return ((user == self.__user) and (passwd == self.__passwd))

    def __make_passwd(self, length: int) -> str:
        chars = "23479ACDEFHJKLMNPQRTWXYZ"
        passwd = "".join(secrets.choice(chars) for _ in range(length))
        assert len(passwd) >= self.__MIN_LEN
        return passwd
