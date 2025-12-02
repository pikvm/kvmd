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


import asyncio
import re

from aiohttp.web import Request
from aiohttp.web import Response

from .....htserver import HttpError
from .....htserver import exposed_http
from .....htserver import make_json_response

from .....plugins.atx import BaseAtx

from .....validators import check_string_in_list
from .....validators.basic import valid_int_f0

from ...info import InfoManager
from ...switch import Switch


# =====
class RedfishAtxApi:
    __SWITCH_PREFIX = "SwitchPort"

    def __init__(self, im: InfoManager, atx: BaseAtx, switch: Switch) -> None:
        self.__im = im

        self.__atx = atx
        self.__atx_actions = {
            "On":               self.__atx.power_on,
            "ForceOff":         self.__atx.power_off_hard,
            "GracefulShutdown": self.__atx.power_off,
            "ForceRestart":     self.__atx.power_reset_hard,
            "ForceOn":          self.__atx.power_on,
            "PushPowerButton":  self.__atx.click_power,
        }

        self.__switch = switch
        self.__switch_actions = {
            "On":               self.__switch.atx_power_on,
            "ForceOff":         self.__switch.atx_power_off_hard,
            "GracefulShutdown": self.__switch.atx_power_off,
            "ForceRestart":     self.__switch.atx_power_reset_hard,
            "ForceOn":          self.__switch.atx_power_on,
            "PushPowerButton":  self.__switch.atx_click_power,
        }

        assert set(self.__atx_actions) == set(self.__switch_actions)

    # =====

    @exposed_http("GET", "/redfish/v1/Systems")
    async def __systems_handler(self, _: Request) -> Response:
        (atx_state, switch_state) = await asyncio.gather(*[
            self.__atx.get_state(),
            self.__switch.get_state(),
        ])

        members: list[str] = []
        if atx_state["enabled"]:
            members.append("0")

        members.extend(
            f"{self.__SWITCH_PREFIX}{port}"
            for port in range(len(switch_state["model"]["ports"]))
        )

        return make_json_response({
            "@odata.id":   "/redfish/v1/Systems",
            "@odata.type": "#ComputerSystemCollection.ComputerSystemCollection",
            "Name":        "Computer System Collection",
            "Members": [
                {"@odata.id": f"/redfish/v1/Systems/{member}"}
                for member in members
            ],
            "Members@odata.count": len(members),
        }, wrap_result=False)

    @exposed_http("GET", "/redfish/v1/Systems/{sid}")
    async def __systems_server_handler(self, req: Request) -> Response:
        (sid, port) = self.__valid_server_id(req)
        host: str
        power: bool
        if port < 0:
            (atx_state, host) = await asyncio.gather(*[  # type: ignore
                self.__atx.get_state(),
                self.__im.get_meta_server_host(),
            ])
            power = atx_state["leds"]["power"]  # type: ignore

        else:
            switch_state = await self.__switch.get_state()
            if port >= len(switch_state["model"]["ports"]):
                raise HttpError("Non-existent Switch Port ID", 400)
            host = (switch_state["model"]["ports"][port]["name"] or sid)  # type: ignore
            power = switch_state["atx"]["leds"]["power"][port]

        host = re.sub(r"[^a-zA-Z0-9_\.]", "_", host)
        return make_json_response({
            "@odata.id":   f"/redfish/v1/Systems/{sid}",
            "@odata.type": "#ComputerSystem.v1_10_0.ComputerSystem",
            "Id":          sid,
            "HostName":    host,
            "PowerState":  ("On" if power else "Off"),
            "Actions": {
                "#ComputerSystem.Reset": {  # XXX: Same actions list for ATX and Switch
                    "ResetType@Redfish.AllowableValues": list(self.__atx_actions),
                    "target": f"/redfish/v1/Systems/{sid}/Actions/ComputerSystem.Reset",
                },
                "#ComputerSystem.SetDefaultBootOrder": {  # https://github.com/pikvm/pikvm/issues/1525
                    "target": f"/redfish/v1/Systems/{sid}/Actions/ComputerSystem.SetDefaultBootOrder",
                },
            },
            "Boot": {
                "BootSourceOverrideEnabled": "Disabled",
                "BootSourceOverrideTarget": None,
            },
        }, wrap_result=False)

    @exposed_http("PATCH", "/redfish/v1/Systems/{sid}")
    async def __systems_server_patch_handler(self, _: Request) -> Response:
        # https://github.com/pikvm/pikvm/issues/1525
        # XXX: We don't care about sid validation here, because nothing to do
        return Response(body=None, status=204)

    @exposed_http("POST", "/redfish/v1/Systems/{sid}/Actions/ComputerSystem.Reset")
    async def __systems_server_power_handler(self, req: Request) -> Response:
        (_, port) = self.__valid_server_id(req)
        try:
            # XXX: Same actions list for ATX and Switch
            action = check_string_in_list(
                arg=(await req.json()).get("ResetType"),
                variants=set(self.__atx_actions),
                name="Redfish ResetType",
                lower=False,
            )
        except Exception:
            raise HttpError("Missing or invalid ResetType", 400)
        if port < 0:
            if (await self.__atx.get_state())["enabled"]:
                await self.__atx_actions[action](False)
        else:
            await self.__switch_actions[action](port)
        return Response(body=None, status=204)

    # =====

    def __valid_server_id(self, req: Request) -> tuple[str, int]:
        try:
            sid = req.match_info["sid"].strip()
            if sid == "0":  # Legacy name for PiKVM itself
                return ("0", -1)
            if sid.startswith(self.__SWITCH_PREFIX):
                sid = sid[len(self.__SWITCH_PREFIX):]
                port = valid_int_f0(sid)
                return (f"{self.__SWITCH_PREFIX}{port}", port)
        except Exception:
            pass
        raise HttpError("Missing or invalid Server ID", 400)
