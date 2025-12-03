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

from aiohttp.web import Request
from aiohttp.web import Response

from .....htserver import HttpError
from .....htserver import exposed_http
from .....htserver import make_json_response

from .....plugins.msd import BaseMsd

from .....validators.basic import valid_bool
from .....validators.kvm import valid_msd_image_name


# =====
class RedfishMsdApi:
    # https://pubs.lenovo.com/tsm/get_virtual_media_collection
    # https://developer.avermedia.com/oob/fw-1.0.3.1/user-guide/13-virtualmedia

    def __init__(self, msd: BaseMsd) -> None:
        self.__msd = msd

    # =====

    @exposed_http("GET", "/redfish/v1/Managers")
    async def __managers_handler(self, _: Request) -> Response:
        return make_json_response({
            "@odata.id":   "/redfish/v1/Managers",
            "@odata.type": "#ManagerCollection.ManagerCollection",
            "Name":        "Manager Collection",
            "Members": [{"@odata.id": "/redfish/v1/Managers/BMC"}],
            "Members@odata.count": 1,
        }, wrap_result=False)

    @exposed_http("GET", "/redfish/v1/Managers/BMC")
    async def __managers_bmc_handler(self, _: Request) -> Response:
        return make_json_response({
            "@odata.id":    "/redfish/v1/Managers/BMC",
            "@odata.type":  "#Manager.v1_15_0.Manager",
            "Id":           "BMC",
            "Name":         "PiKVM Manager",
            "Description":  "PiKVM Baseboard Management Controller",
            "ManagerType":  "BMC",
            "VirtualMedia": {"@odata.id": "/redfish/v1/Managers/BMC/VirtualMedia"},
        }, wrap_result=False)

    @exposed_http("GET", "/redfish/v1/Managers/BMC/VirtualMedia")
    async def __managers_bmc_vm_handler(self, _: Request) -> Response:
        return make_json_response({
            "@odata.id":   "/redfish/v1/Managers/BMC/VirtualMedia",
            "@odata.type": "#VirtualMediaCollection.VirtualMediaCollection",
            "Name":        "Virtual Media Collection",
            "Members": [{"@odata.id": "/redfish/v1/Managers/BMC/VirtualMedia/MSD"}],
            "Members@odata.count": 1,
        }, wrap_result=False)

    # =====

    @exposed_http("GET", "/redfish/v1/Managers/BMC/VirtualMedia/MSD")
    async def __msd_handler(self, _: Request) -> Response:
        state = (await self.__msd.get_state())

        drive: (dict | None) = None
        path: (str | None) = None
        if state["online"]:
            drive = state["drive"]
            path = (drive and drive["image"] and drive["image"]["name"])  # type: ignore

        return make_json_response({
            "@odata.id":      "/redfish/v1/Managers/BMC/VirtualMedia/MSD",
            "@odata.type":    "#VirtualMedia.v1_4_0.VirtualMedia",
            "Id":             "MSD",
            "Name":           "Virtual CD/DVD/Flash Drive",
            "Description":    "PiKVM Virtual CD/DVD/Flash Drive",
            "MediaTypes":     ["USBStick", "CD", "DVD"],
            "Image":          path,
            "ImageName":      (path and os.path.basename(path)),
            "ConnectedVia":   (drive and ("Oem" if drive["image"] else "NotConnected")),
            "Inserted":       (drive and drive["connected"]),
            "WriteProtected": (drive and drive["rw"]),
            "Oem": {
                "PiKVM": {
                    "@odata.context": "/redfish/v1/$metadata#PiKVMVirtualMedia.PiKVMVirtualMedia",
                    "@odata.type":    "#PiKVMVirtualMedia.v1_0_0.PiKVMVirtualMedia",
                    "MsdEnabled":     state["enabled"],
                    "MsdOnline":      state["online"],
                    "MsdBusy":        state["busy"],
                    "DriveOptical":   (drive and drive["cdrom"]),
                },
            },
            "Actions": {
                "#VirtualMedia.InsertMedia": {
                    "target": "/redfish/v1/Managers/BMC/VirtualMedia/MSD/Actions/VirtualMedia.InsertMedia",
                    "Image@Redfish.AllowableValues": ["URI"],
                },
                "#VirtualMedia.EjectMedia": {
                    "target": "/redfish/v1/Managers/BMC/VirtualMedia/MSD/Actions/VirtualMedia.EjectMedia",
                },
            },
        }, wrap_result=False)

    @exposed_http("POST", "/redfish/v1/Managers/BMC/VirtualMedia/MSD/Actions/VirtualMedia.InsertMedia")
    async def __msd_insert_handler(self, req: Request) -> Response:
        try:
            params = await req.json()
        except Exception:
            raise HttpError("Invalid body", 400)
        name = valid_msd_image_name(params.get("Image"))
        cdrom = name.lower().startswith(".iso")
        connect = valid_bool(params.get("Inserted", True))
        rw = (not valid_bool(params.get("WriteProtected", True)))

        state = await self.__msd.get_state()
        if state.get("drive", {}).get("connected"):
            await self.__msd.set_connected(False)
            await self.__msd.set_params(name="")

        await self.__msd.set_params(name=name, cdrom=cdrom, rw=rw)
        if connect:
            await self.__msd.set_connected(True)
        return Response(body=None, status=204)

    @exposed_http("POST", "/redfish/v1/Managers/BMC/VirtualMedia/MSD/Actions/VirtualMedia.EjectMedia")
    async def __msd_eject_handler(self, _: Request) -> Response:
        await self.__msd.set_connected(False)
        await self.__msd.set_params(name="")
        return Response(body=None, status=204)
