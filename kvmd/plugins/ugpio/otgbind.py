# ========================================================================== #
#                                                                            #
#    KVMD - The main Pi-KVM daemon.                                          #
#                                                                            #
#    Copyright (C) 2018-2021  Maxim Devaev <mdevaev@gmail.com>               #
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

from typing import Optional

from ...logging import get_logger

from ...inotify import InotifyMask
from ...inotify import Inotify

from ... import env
from ... import aiotools

from . import BaseUserGpioDriver


# =====
class Plugin(BaseUserGpioDriver):
    def __init__(
        self,
        instance_name: str,
        notifier: aiotools.AioNotifier,

        udc: str,  # XXX: Not from options, see /kvmd/apps/kvmd/__init__.py for details
    ) -> None:

        super().__init__(instance_name, notifier)

        self.__udc = udc

    def register_input(self, pin: int, debounce: float) -> None:
        _ = pin
        _ = debounce

    def register_output(self, pin: int, initial: Optional[bool]) -> None:
        _ = pin
        _ = initial

    def prepare(self) -> None:
        candidates = sorted(os.listdir(f"{env.SYSFS_PREFIX}/sys/class/udc"))
        if not self.__udc:
            if len(candidates) == 0:
                raise RuntimeError("Can't find any UDC")
            self.__udc = candidates[0]
        elif self.__udc not in candidates:
            raise RuntimeError(f"Can't find selected UDC: {self.__udc}")
        get_logger().info("Using UDC %s", self.__udc)

    async def run(self) -> None:
        logger = get_logger(0)
        while True:
            try:
                while True:
                    await self._notifier.notify()
                    if os.path.isdir(self.__get_driver_path()):
                        break
                    await asyncio.sleep(5)

                with Inotify() as inotify:
                    inotify.watch(self.__get_driver_path(), InotifyMask.ALL_MODIFY_EVENTS)
                    await self._notifier.notify()
                    while True:
                        need_restart = False
                        need_notify = False
                        for event in (await inotify.get_series(timeout=1)):
                            need_notify = True
                            if event.mask & (InotifyMask.DELETE_SELF | InotifyMask.MOVE_SELF | InotifyMask.UNMOUNT):
                                logger.warning("Got fatal inotify event: %s; reinitializing OTG-bind ...", event)
                                need_restart = True
                                break
                        if need_restart:
                            break
                        if need_notify:
                            await self._notifier.notify()
            except Exception:
                logger.exception("Unexpected OTG-bind watcher error")

    def cleanup(self) -> None:
        pass

    async def read(self, pin: int) -> bool:
        _ = pin
        return os.path.islink(self.__get_driver_path(self.__udc))

    async def write(self, pin: int, state: bool) -> None:
        _ = pin
        with open(self.__get_driver_path("bind" if state else "unbind"), "w") as ctl_file:
            ctl_file.write(f"{self.__udc}\n")

    def __get_driver_path(self, name: str="") -> str:
        path = f"{env.SYSFS_PREFIX}/sys/bus/platform/drivers/dwc2"
        return (os.path.join(path, name) if name else path)

    def __str__(self) -> str:
        return f"GPIO({self._instance_name})"

    __repr__ = __str__
