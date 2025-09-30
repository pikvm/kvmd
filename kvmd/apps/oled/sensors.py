#!/usr/bin/env python3
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
import functools
import itertools
import types
import datetime
import time

from typing import Self

import netifaces
import psutil

from ...logging import get_logger

from ... import tools

from ...clients.kvmd import KvmdClient


# =====
class Sensors:
    def __init__(
        self,
        kvmd: (KvmdClient | None),
        fahrenheit: bool,
    ) -> None:

        self.__kvmd = kvmd
        self.__fahrenheit = fahrenheit

        self.__kvmd_task: (asyncio.Task | None) = None

        hb = itertools.cycle(r"/-\|")
        self.__clients_count = -1
        self.__sensors = {
            "hb":      (lambda: next(hb)),
            "fqdn":    self.__get_fqdn,
            "iface":   self.__get_iface,
            "ip":      self.__get_ip,
            "uptime":  self.__get_uptime,
            "temp":    self.__get_temp,
            "cpu":     self.__get_cpu,
            "mem":     self.__get_mem,
            "clients": (lambda: ("?" if self.__clients_count < 0 else str(self.__clients_count))),
        }

    async def __aenter__(self) -> Self:
        if self.__kvmd:
            self.__kvmd_task = asyncio.create_task(self.__kvmd_task_loop())
        return self

    async def __aexit__(
        self,
        _exc_type: type[BaseException],
        _exc: BaseException,
        _tb: types.TracebackType,
    ) -> None:

        if self.__kvmd_task:
            self.__kvmd_task.cancel()

    async def __kvmd_task_loop(self) -> None:
        logger = get_logger()
        assert self.__kvmd
        while True:
            try:
                async with self.__kvmd.make_session() as session:
                    async with session.ws(stream=False) as ws:
                        logger.info("Polling KVMD ...")
                        async for (event_type, event) in ws.communicate():
                            if event_type == "clients":
                                self.__clients_count = int(event["count"])
            except Exception as ex:
                self.__clients_count = -1
                logger.error("Can't poll KVMD: %s", tools.efmt(ex))
                await asyncio.sleep(5)

    # =====

    def get_clients_count(self) -> int:
        return self.__clients_count

    def render(self, text: str) -> str:
        return text.format_map(self)

    def __getitem__(self, key: str) -> str:
        return self.__sensors[key]()  # type: ignore

    # =====

    def __get_fqdn(self) -> str:
        return self.__inner_get_fqdn(int(time.monotonic()) // 3)

    def __inner_get_fqdn(self, ts: int) -> str:
        _ = ts
        return socket.getfqdn()

    # =====

    def __get_iface(self) -> str:
        return self.__inner_get_netconf(int(time.monotonic()) // 3)[0]

    def __get_ip(self) -> str:
        return self.__inner_get_netconf(int(time.monotonic()) // 3)[1]

    @functools.lru_cache(maxsize=1)
    def __inner_get_netconf(self, ts: int) -> tuple[str, str]:
        _ = ts
        try:
            gws = netifaces.gateways()
            if "default" in gws:
                for proto in [socket.AF_INET, socket.AF_INET6]:
                    if proto in gws["default"]:
                        iface = gws["default"][proto][1]
                        addrs = netifaces.ifaddresses(iface)
                        return (iface, addrs[proto][0]["addr"])

            for iface in netifaces.interfaces():
                if not iface.startswith(("lo", "docker")):
                    addrs = netifaces.ifaddresses(iface)
                    for proto in [socket.AF_INET, socket.AF_INET6]:
                        if proto in addrs:
                            return (iface, addrs[proto][0]["addr"])
        except Exception:
            # _logger.exception("Can't get iface/IP")
            pass
        return ("<no-iface>", "<no-ip>")

    # =====

    def __get_uptime(self) -> str:
        return self.__inner_get_uptime(int(time.monotonic()))

    @functools.lru_cache(maxsize=1)
    def __inner_get_uptime(self, ts: int) -> str:
        _ = ts
        uptime = datetime.timedelta(seconds=int(time.time() - psutil.boot_time()))
        pl = {"days": uptime.days}
        (pl["hours"], rem) = divmod(uptime.seconds, 3600)
        (pl["mins"], pl["secs"]) = divmod(rem, 60)
        return "{days}d {hours}h {mins}m".format(**pl)

    # =====

    def __get_temp(self) -> str:
        return self.__inner_get_temp(int(time.monotonic()) // 3)

    @functools.lru_cache(maxsize=1)
    def __inner_get_temp(self, ts: int) -> str:
        _ = ts
        try:
            with open("/sys/class/thermal/thermal_zone0/temp") as file:
                temp = int(file.read().strip()) / 1000
                if self.__fahrenheit:
                    temp = temp * 9 / 5 + 32
                    return f"{temp:.1f}\u00b0F"
                return f"{temp:.1f}\u00b0C"
        except Exception:
            # _logger.exception("Can't read temp")
            return "<no-temp>"

    # =====

    def __get_cpu(self) -> str:
        return self.__inner_get_cpu(int(time.monotonic()))

    @functools.lru_cache(maxsize=1)
    def __inner_get_cpu(self, ts: int) -> str:
        _ = ts
        st = psutil.cpu_times_percent()
        user = st.user - st.guest
        nice = st.nice - st.guest_nice
        idle_all = st.idle + st.iowait
        system_all = st.system + st.irq + st.softirq
        virtual = st.guest + st.guest_nice
        total = max(1, user + nice + system_all + idle_all + st.steal + virtual)
        percent = int(
            st.nice / total * 100
            + st.user / total * 100
            + system_all / total * 100
            + (st.steal + st.guest) / total * 100
        )
        return f"{percent}%"

    def __get_mem(self) -> str:
        return self.__inner_get_mem(int(time.monotonic()))

    @functools.lru_cache(maxsize=1)
    def __inner_get_mem(self, ts: int) -> str:
        _ = ts
        return f"{int(psutil.virtual_memory().percent)}%"
