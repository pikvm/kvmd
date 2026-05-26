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
import asyncio
import json
import contextlib

from typing import AsyncGenerator

from ....logging import get_logger

from ....clients.pst import PstClient

from .... import aiotools

from .types import Edid
from .types import Edids
from .types import Dummies
from .types import Color
from .types import Colors
from .types import PortNames
from .types import AtxClickPowerDelays
from .types import AtxClickPowerLongDelays
from .types import AtxClickResetDelays


# =====
class StorageContext:
    __F_EDIDS_ALL = "edids_all.json"
    __F_EDIDS_PORT = "edids_port.json"

    __F_DUMMIES = "dummies.json"

    __F_COLORS = "colors.json"

    __F_PORT_NAMES = "port_names.json"

    __F_ATX_CP_DELAYS = "atx_click_power_delays.json"
    __F_ATX_CPL_DELAYS = "atx_click_power_long_delays.json"
    __F_ATX_CR_DELAYS = "atx_click_reset_delays.json"

    def __init__(self, path: str, rw: bool) -> None:
        self.__path = path
        self.__rw = rw

    # =====

    async def write_edids(self, edids: Edids) -> None:  # noqa vulture-ignore
        await self.__write_json_keyvals(self.__F_EDIDS_ALL, {
            edid_id.lower(): {"name": edid.name, "data": edid.as_text()}
            for (edid_id, edid) in edids.all.items()
            if edid_id != Edids.DEFAULT_ID
        })
        await self.__write_json_keyvals(self.__F_EDIDS_PORT, edids.port)

    async def write_dummies(self, dummies: Dummies) -> None:  # noqa vulture-ignore
        await self.__write_json_keyvals(self.__F_DUMMIES, dummies.kvs)

    async def write_colors(self, colors: Colors) -> None:  # noqa vulture-ignore
        await self.__write_json_keyvals(self.__F_COLORS, {
            role: {
                comp: getattr(getattr(colors, role), comp)
                for comp in Color.COMPONENTS
            }
            for role in Colors.ROLES
        })

    async def write_port_names(self, port_names: PortNames) -> None:  # noqa vulture-ignore
        await self.__write_json_keyvals(self.__F_PORT_NAMES, port_names.kvs)

    async def write_atx_cp_delays(self, delays: AtxClickPowerDelays) -> None:  # noqa vulture-ignore
        await self.__write_json_keyvals(self.__F_ATX_CP_DELAYS, delays.kvs)

    async def write_atx_cpl_delays(self, delays: AtxClickPowerLongDelays) -> None:  # noqa vulture-ignore
        await self.__write_json_keyvals(self.__F_ATX_CPL_DELAYS, delays.kvs)

    async def write_atx_cr_delays(self, delays: AtxClickResetDelays) -> None:  # noqa vulture-ignore
        await self.__write_json_keyvals(self.__F_ATX_CR_DELAYS, delays.kvs)

    async def __write_json_keyvals(self, name: str, kvs: dict) -> None:
        assert self.__rw
        kvs = {str(key): value for (key, value) in kvs.items()}
        if (await self.__read_json_keyvals(name)) == kvs:
            return  # Don't write the same data
        path = os.path.join(self.__path, name)
        get_logger(0).info("Writing '%s' ...", name)
        await aiotools.write_file(path, json.dumps(kvs))

    # =====

    async def read_edids(self) -> Edids:  # noqa vulture-ignore
        all_edids = {
            edid_id.lower(): Edid.from_data(edid["name"], edid["data"])
            for (edid_id, edid) in (await self.__read_json_keyvals(self.__F_EDIDS_ALL)).items()
        }
        port_edids = await self.__read_json_keyvals_int(self.__F_EDIDS_PORT)
        return Edids(all_edids, port_edids)

    async def read_dummies(self) -> Dummies:  # noqa vulture-ignore
        kvs = await self.__read_json_keyvals_int(self.__F_DUMMIES)
        return Dummies({key: bool(value) for (key, value) in kvs.items()})

    async def read_colors(self) -> Colors:  # noqa vulture-ignore
        raw = await self.__read_json_keyvals(self.__F_COLORS)
        return Colors(**{  # type: ignore
            role: Color(**{comp: raw[role][comp] for comp in Color.COMPONENTS})
            for role in Colors.ROLES
            if role in raw
        })

    async def read_port_names(self) -> PortNames:  # noqa vulture-ignore
        return PortNames(await self.__read_json_keyvals_int(self.__F_PORT_NAMES))

    async def read_atx_cp_delays(self) -> AtxClickPowerDelays:  # noqa vulture-ignore
        return AtxClickPowerDelays(await self.__read_json_keyvals_int(self.__F_ATX_CP_DELAYS))

    async def read_atx_cpl_delays(self) -> AtxClickPowerLongDelays:  # noqa vulture-ignore
        return AtxClickPowerLongDelays(await self.__read_json_keyvals_int(self.__F_ATX_CPL_DELAYS))

    async def read_atx_cr_delays(self) -> AtxClickResetDelays:  # noqa vulture-ignore
        return AtxClickResetDelays(await self.__read_json_keyvals_int(self.__F_ATX_CR_DELAYS))

    async def __read_json_keyvals_int(self, name: str) -> dict:
        return (await self.__read_json_keyvals(name, int_keys=True))

    async def __read_json_keyvals(self, name: str, int_keys: bool=False) -> dict:
        path = os.path.join(self.__path, name)
        try:
            kvs: dict = json.loads(await aiotools.read_file(path))
        except FileNotFoundError:
            kvs = {}
        if int_keys:
            kvs = {int(key): value for (key, value) in kvs.items()}
        return kvs


class Storage:
    def __init__(self, pst: PstClient) -> None:
        self.__pst = pst
        self.__lock = asyncio.Lock()

    @contextlib.asynccontextmanager
    async def readable(self) -> AsyncGenerator[StorageContext]:
        async with self.__lock:
            path = await self.__pst.get_path()
            yield StorageContext(path, False)

    @contextlib.asynccontextmanager
    async def writable(self) -> AsyncGenerator[StorageContext]:
        async with self.__lock:
            async with self.__pst.writable() as path:
                yield StorageContext(path, True)
