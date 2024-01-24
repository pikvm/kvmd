# ========================================================================== #
#                                                                            #
#    KVMD - The main PiKVM daemon.                                           #
#                                                                            #
#    Copyright (C) 2018-2023  Maxim Devaev <mdevaev@gmail.com>               #
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
from yarl import URL

from .. import BasePlugin
from .. import get_plugin_class


# =====
class BaseAuthService(BasePlugin):
    async def authorize(self, user: str, passwd: str) -> bool:
        raise NotImplementedError  # pragma: nocover

    async def cleanup(self) -> None:
        pass


class OAuthService(BasePlugin):
    def __init__(
            self,
            short_name: str,
            long_name: str,
    ) -> None:
        self.__short_name = short_name
        self.__long_name = long_name

    def get_long_name(self) -> str:
        return self.__long_name

    def get_short_name(self) -> str:
        return self.__short_name

    def is_redirect_from_provider(self, request_query: dict) -> bool:
        raise NotImplementedError  # pragma: nocover

    def get_authorize_url(self, redirect_url: URL, session: dict) -> str:
        raise NotImplementedError  # pragma: nocover

    async def get_user_info(
            self,
            oauth_session: dict,
            request_query: dict,
            redirect_url: URL
    ) -> str:
        raise NotImplementedError

    def register_new_session(self) -> dict:
        raise NotImplementedError

    def is_valid_session(self, oauth_session: dict) -> bool:
        raise NotImplementedError


class OAuthException(Exception):
    def __init__(self, message: str):
        self.message = message


# =====
def get_auth_service_class(name: str) -> type[BaseAuthService]:
    return get_plugin_class("auth", name)  # type: ignore


def get_oauth_service_class(name: str) -> type[OAuthService]:
    return get_plugin_class("auth", name)  # type: ignore
