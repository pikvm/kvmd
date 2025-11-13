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


import asyncio

from typing import cast

from aiohttp.web import Application
from aiohttp.web import HTTPNotFound

from aiohttp.web import Request
from aiohttp.web import Response

from ...logging import get_logger

from ...htserver import HttpExposed
from ...htserver import HttpRouterBase

from .auth import AuthManager
from .api.auth import check_request_auth


class HttpRouter(HttpRouterBase):
    def __init__(self, auth_manager: AuthManager) -> None:
        super().__init__()
        self.__auth_manager = auth_manager
        self._app = Application(
            loop=asyncio.get_event_loop(),
        )

    # =====

    def add_exposed(self, *objs: object) -> None:
        self._add_exposed(*objs)

    async def dispatch(self, req: Request, subpath: str) -> Response:
        assert self._app is not None
        subreq = req.clone(rel_url='/' + subpath.removeprefix('/'))
        match_info = await self._app.router.resolve(subreq)
        if match_info.handler is None:
            # XXX can this happen?
            get_logger().error(
                "Unexpected: nested router for %r: no handler in %r",
                subreq.path, match_info,
            )
            raise HTTPNotFound()
        subreq._match_info = match_info  # pylint: disable=protected-access
        return cast(Response, await match_info.handler(subreq))

    # =====

    async def _check_request_auth(self, exposed: HttpExposed, req: Request) -> None:
        return await check_request_auth(self.__auth_manager, exposed, req)
