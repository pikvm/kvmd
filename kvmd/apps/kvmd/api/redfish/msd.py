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
from .....validators.basic import valid_stripped_string
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
        image: (dict | None) = None
        path: (str | None) = None
        name: (str | None) = None
        media_types = ["USBStick", "CD", "DVD"]
        if state["online"]:
            drive = state["drive"]
            if drive:
                image = drive["image"]
                if image:
                    if image["proto"] == "file":
                        path = image["name"]
                        name = os.path.basename(path)
                    else:
                        path = image["url"]
                        name = image["name"]
                if drive["connected"]:
                    if drive["cdrom"]:
                        media_types = ["CD", "DVD"]
                    else:
                        media_types = ["USBStick"]

        return make_json_response({
            "@odata.id":      "/redfish/v1/Managers/BMC/VirtualMedia/MSD",
            "@odata.type":    "#VirtualMedia.v1_4_0.VirtualMedia",
            "Id":             "MSD",
            "Name":           "Virtual CD/DVD/Flash Drive",
            "Description":    "PiKVM Virtual CD/DVD/Flash Drive",
            "MediaTypes":     media_types,
            "TransferMethod": (image and ("Upload" if image["proto"] == "file" else "Stream")),
            # "TransferProtocolType": ["CIFS", "HTTP", "HTTPS", "SFTP"],
            "Image":          path,
            "ImageName":      name,
            "ConnectedVia":   (drive and (("Oem" if image["proto"] == "file" else "URI") if image else "NotConnected")),
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
            query = await req.json()
        except Exception:
            raise HttpError("Invalid body", 400)

        params: dict = {
            "guess": True,  # "cdrom" has a priority over "guess" and "rw"
            "rw":    valid_bool(query.get("WriteProtected", True)),
            "remote_params": {
                # Standard options for NBD remotes
                "user":   query.get("UserName", ""),
                "passwd": query.get("Password", ""),
                "verify": valid_bool(query.get("VerifyCertificate", True)),
            },
        }

        has_optical = ("DriveOptical" in query.get("Oem", {}).get("PiKVM", {}))
        if has_optical:
            params["cdrom"] = valid_bool(query["Oem"]["PiKVM"]["DriveOptical"])

        image = valid_stripped_string(query.get("Image"), name="MSD image name or URL")
        if self.__msd.is_remote_url(image):
            # XXX: We don't validate a URL, it should be passed as-is to the lower level.
            # remote_params are not validated too.
            params["remote_url"] = image
        else:
            params["name"] = valid_msd_image_name(image, allow_eject=True)

        connect = valid_bool(query.get("Inserted", True))

        state = await self.__msd.get_state()
        if state.get("drive", {}).get("connected"):
            await self.__msd.set_connected(False)
            await self.__msd.set_params(name="")

        await self.__msd.set_params(**params)  # type: ignore
        if connect:
            await self.__msd.set_connected(True)
        return Response(body=None, status=204)

    @exposed_http("POST", "/redfish/v1/Managers/BMC/VirtualMedia/MSD/Actions/VirtualMedia.EjectMedia")
    async def __msd_eject_handler(self, _: Request) -> Response:
        await self.__msd.set_connected(False)
        await self.__msd.set_params(name="")
        return Response(body=None, status=204)
