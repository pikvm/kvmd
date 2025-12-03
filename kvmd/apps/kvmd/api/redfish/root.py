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

from .....htserver import exposed_http
from .....htserver import make_json_response


# =====
class RedfishRootApi:
    # https://github.com/DMTF/Redfishtool
    # https://github.com/DMTF/Redfish-Mockup-Server
    # https://redfish.dmtf.org/redfish/v1
    # https://www.dmtf.org/documents/redfish-spmf/redfish-mockup-bundle-20191
    # https://www.dmtf.org/sites/default/files/Redfish_School-Sessions.pdf
    # https://www.ibm.com/support/knowledgecenter/POWER9/p9ej4/p9ej4_kickoff.htm
    # https://www.dmtf.org/sites/default/files/standards/documents/DSP2046_2025.1.pdf
    #
    # Quick examples:
    #    redfishtool -S Never -u admin -p admin -r localhost:8080 Systems
    #    redfishtool -S Never -u admin -p admin -r localhost:8080 Systems reset ForceOff

    @exposed_http("GET", "/redfish/v1", auth_required=False)
    async def __root_handler(self, _: Request) -> Response:
        return make_json_response({
            "@odata.id":      "/redfish/v1",
            "@odata.type":    "#ServiceRoot.v1_6_0.ServiceRoot",
            "Id":             "RootService",
            "Name":           "Root Service",
            "RedfishVersion": "1.6.0",
            "Systems":        {"@odata.id": "/redfish/v1/Systems"},  # ATX
            "Managers":       {"@odata.id": "/redfish/v1/Managers"},  # MSD
        }, wrap_result=False)
