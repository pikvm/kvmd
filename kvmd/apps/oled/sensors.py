# ========================================================================== #
#                                                                            #
#    KVMD-OLED - A small OLED daemon for PiKVM.                              #
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
import socket
import itertools
import types

from typing import Self

from ...logging import get_logger

from ... import tools
from ... import aiotools
from ... import network

from ...clients.kvmd import KvmdClient


# =====
class Sensors:  # pylint: disable=too-many-instance-attributes
    def __init__(
        self,
        kvmd: (KvmdClient | None),
        fahrenheit: bool,
    ) -> None:

        self.__kvmd = kvmd
        self.__fahrenheit = fahrenheit

        self.__fqdn_task: (asyncio.Task | None) = None
        self.__iface_task: (asyncio.Task | None) = None
        self.__kvmd_task: (asyncio.Task | None) = None

        self.__clients_count = -1
        self.__s_fqdn = ""
        self.__s_iface = ""
        self.__s_ip = ""
        self.__s_uptime = ""
        self.__s_temp = ""
        self.__s_cpu = ""
        self.__s_mem = ""

        hb = itertools.cycle(r"/-\|")
        self.__sensors = {
            "hb":      (lambda: next(hb)),
            "fqdn":    (lambda: (self.__s_fqdn or "<no-fqdn>")),
            "iface":   (lambda: (self.__s_iface or "<no-iface>")),
            "ip":      (lambda: (self.__s_ip or "<no-ip>")),
            "uptime":  (lambda: (self.__s_uptime or "?d ?h ?m")),
            "temp":    (lambda: (self.__s_temp or "?")),
            "cpu":     (lambda: (self.__s_cpu or "?")),
            "mem":     (lambda: (self.__s_mem or "?")),
            "clients": (lambda: ("?" if self.__clients_count < 0 else str(self.__clients_count))),
        }

    def has_clients(self) -> int:
        return (self.__clients_count > 0)

    def render(self, text: str) -> str:
        return text.format_map(self)

    def __getitem__(self, key: str) -> str:
        return self.__sensors[key]()  # type: ignore

    async def __aenter__(self) -> Self:
        assert self.__fqdn_task is None
        self.__fqdn_task = asyncio.create_task(self.__fqdn_task_loop())
        self.__iface_task = asyncio.create_task(self.__iface_task_loop())
        if self.__kvmd:
            self.__kvmd_task = asyncio.create_task(self.__kvmd_task_loop())
        return self

    async def __aexit__(
        self,
        _exc_type: type[BaseException],
        _exc: BaseException,
        _tb: types.TracebackType,
    ) -> None:

        for task in [self.__fqdn_task, self.__iface_task, self.__kvmd_task]:
            if task:
                task.cancel()

    async def __fqdn_task_loop(self) -> None:
        while True:
            try:
                self.__s_fqdn = socket.gethostname()
            except Exception:
                self.__s_fqdn = ""
            await asyncio.sleep(3)

    async def __iface_task_loop(self) -> None:
        while True:
            try:
                fi = await aiotools.run_async(network.get_first_iface)
                self.__s_iface = fi.name
                self.__s_ip = fi.ip
            except Exception:
                self.__s_iface = ""
                self.__s_ip = ""
            await asyncio.sleep(3)

    async def __kvmd_task_loop(self) -> None:
        logger = get_logger(0)
        assert self.__kvmd
        ok = True
        while True:
            try:
                async with self.__kvmd.make_session() as session:
                    async with session.ws(stream=False) as ws:
                        logger.info("Polling KVMD ...")
                        async for (event_type, event) in ws.communicate():
                            self.__parse_kvmd_event(event_type, event)
                            ok = True
            except Exception as ex:
                self.__clients_count = -1
                self.__s_uptime = ""
                self.__s_temp = ""
                self.__s_cpu = ""
                self.__s_mem = ""
                if ok:
                    logger.error("Can't poll KVMD: %s", tools.efmt(ex))
                    ok = False
                await asyncio.sleep(1)

    def __parse_kvmd_event(self, event_type: str, event: dict) -> None:
        if event_type == "clients":
            self.__clients_count = int(event["count"])

        elif event_type == "info":
            if "health" in event:
                (deg, temp) = ("C", float(event["health"]["temp"]["cpu"]))
                if self.__fahrenheit:
                    (deg, temp) = ("F", temp * 9 / 5 + 32)
                self.__s_temp = f"{temp:.1f}\u00b0{deg}"

                self.__s_cpu = f"{event["health"]["cpu"]["percent"]}%"
                self.__s_mem = f"{event["health"]["mem"]["percent"]}%"

            if "uptime" in event:
                self.__s_uptime = "{days}d {hours}h {minutes}m".format(**event["uptime"]["parts"])
