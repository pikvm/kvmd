# ========================================================================== #
#                                                                            #
#    KVMD - The main PiKVM daemon.                                           #
#                                                                            #
#    Copyright (C) 2018-2022  Maxim Devaev <mdevaev@gmail.com>               #
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


from typing import Dict

from ...yamlconf import Option

from ...validators.os import valid_abs_file

from . import BaseAuthService

import radius

# =====
class Plugin(BaseAuthService):
    def __init__(  # pylint: disable=super-init-not-called
        self,
        hostsrv: str,
        port: int,
        secret: str,
        user: str,
        passwd: str,
        timeout: float,
    ) -> None:

        self.__hostsrv = hostsrv
        self.__port = port
        self.__secret = secret
        self.__user = user
        self.__passwd = passwd
        self.__timeout = timeout

    @classmethod
    def get_plugin_options(cls) -> Dict:
        return {
            "hostsrv":     Option("localhost"),
            "port":  Option(1812),
            "secret":  Option(""),
            "user":    Option(""),
            "passwd":  Option(""),
            "timeout": Option(5.0, type=valid_float_f01),
        }

    async def authorize(self, user: str, passwd: str) -> bool:
        r = radius.Radius(self.__secret, self.__hostsrv, self.__port)
        return r.authenticate(self.__user,  self.__passwd)
