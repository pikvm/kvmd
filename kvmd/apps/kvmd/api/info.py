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


from aiohttp.web import Request
from aiohttp.web import Response

from ....htserver import exposed_http
from ....htserver import make_json_response

from ....validators.basic import valid_bool
from ....validators.kvm import valid_info_fields

from ..info import InfoManager


# =====
class InfoApi:
    def __init__(self, info_manager: InfoManager) -> None:
        self.__info_manager = info_manager

    # =====

    @exposed_http("GET", "/info")
    async def __state_handler(self, req: Request) -> Response:
        legacy = valid_bool(req.query.get("legacy", True))

        available = self.__info_manager.get_subs()
        if legacy:
            available.add("hw")
        default = set(available)
        if legacy:
            default.remove("health")

        fields = sorted(valid_info_fields(
            arg=req.query.get("fields", ",".join(default)),
            variants=available,
        ) or available)

        if legacy:
            handler = self.__info_manager.get_state_legacy
        else:
            handler = self.__info_manager.get_state
        return make_json_response(await handler(fields))
