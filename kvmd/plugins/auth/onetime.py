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
import secrets
import json

from typing import Final

from ...yamlconf import Option
from ...yamlconf import Hint

from ...validators.basic import valid_bool
from ...validators.basic import valid_number
from ...validators.os import valid_abs_path
from ...validators.os import valid_unix_mode
from ...validators.auth import valid_user

from ...logging import get_logger

from ... import tools

from . import BaseAuthService


# =====
class Plugin(BaseAuthService):
    def __init__(
        self,
        user:       str,
        passwd_len: int,
        path:       str,
        mode:       int,
        change_after_login: bool,
    ) -> None:  # pylint: disable=super-init-not-called

        self.__user:       Final[str] = user
        self.__passwd_len: Final[int] = passwd_len
        self.__path:       Final[str] = path
        self.__mode:       Final[int] = mode
        self.__change_after_login: Final[bool] = change_after_login

        self.__passwd = self.__make_passwd()  # Just fill it with some valid passwd

    @classmethod
    def get_plugin_options(cls) -> dict:
        return {
            "user":       Option("onetime", type=valid_user),
            "passwd_len": Option(8, type=valid_number.mk(min=3, max=32)),
            "file":       Option("/run/kvmd/creds.json", type=valid_abs_path, unpack_as="path"),
            "file_mode":  Option(0o640, type=valid_unix_mode, hint=Hint.OCT, unpack_as="mode"),
            "change_after_login": Option(False, type=valid_bool),
        }

    async def sysprep(self) -> None:
        self.__regen_passwd()

    async def cleanup(self) -> None:
        try:
            os.remove(self.__path)
        except FileNotFoundError:
            pass
        except Exception as ex:
            get_logger(0).info("Can't remove credentials file %s: %s", self.__path, tools.efmt(ex))

    async def authorize(self, user: str, passwd: str) -> bool:
        assert len(self.__passwd) == self.__passwd_len
        ok = ((user == self.__user) and (passwd == self.__passwd))
        if ok and self.__change_after_login:
            self.__regen_passwd()
        return ok

    def __regen_passwd(self) -> None:
        logger = get_logger(0)
        passwd = self.__make_passwd()
        try:
            with tools.atomic_file_put(self.__path, self.__mode) as path:
                with open(path, "w") as file:
                    json.dump({
                        "user":   self.__user,
                        "passwd": passwd,
                    }, file)
        except Exception as ex:
            logger.error("Can't write credentials to %s: %s", self.__path, tools.efmt(ex))
        else:
            logger.info("New one-time credentials was written to %s", self.__path)
            self.__passwd = passwd

    def __make_passwd(self) -> str:
        chars = "23479ACDEFHJKLMNPQRTWXYZ"
        passwd = "".join(secrets.choice(chars) for _ in range(self.__passwd_len))
        return passwd
