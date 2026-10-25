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


from typing import Final

from aiohttp.web import Request
from aiohttp.web import Response

from ....htserver import exposed_http
from ....htserver import make_json_response

from ....validators.basic import valid_string_list
from ....validators.kvm import valid_alert

from ..alerts import Alerts


# =====
class AlertsApi:
    def __init__(self, alerts: Alerts) -> None:
        self.__alerts: Final = alerts

    # =====

    @exposed_http("GET", "/alerts")
    async def __state_handler(self, _: Request) -> Response:
        return make_json_response(await self.__alerts.get_state())

    @exposed_http("POST", "/alerts/ack")
    async def __ack_handler(self, req: Request) -> Response:
        alerts = valid_string_list(req.query.get("alerts"), subval=valid_alert)
        await self.__alerts.ack(alerts)
        return make_json_response()
