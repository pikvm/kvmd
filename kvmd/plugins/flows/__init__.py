# ========================================================================== #
#                                                                            #
#    KVMD - The main PiKVM daemon.                                           #
#                                                                            #
#    Copyright (C) 2025 Ivan Shapovalov <intelfx@intelfx.name>               #
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


from typing import Any
from typing import TYPE_CHECKING

from aiohttp.web import Request
from aiohttp.web import Response

from .. import BasePlugin
from .. import get_plugin_class

if TYPE_CHECKING:
    from ...apps.kvmd import AuthManager


# =====
# FIXME: perhaps we want to have a BaseRouterPlugin (see dispatch() and its impl in oauth.Plugin)?
class BaseAuthFlowService(BasePlugin):
    # FIXME: should be able to use a Protocol here instead of declaring a ctor that does nothing
    #        and only exists to define the proper signature of descendant constructors,
    #        but you can't express "type that both implements a protocol A and is a subclass of B"
    # pylint: disable=super-init-not-called,unused-argument
    def __init__(self, *, manager: "AuthManager", **_: Any) -> None:
        pass

    async def dispatch(self, req: Request, subpath: str | None = None) -> Response:
        raise NotImplementedError


# =====
def get_auth_flow_service_class(name: str) -> type[BaseAuthFlowService]:
    return get_plugin_class("flows", name)  # type: ignore
