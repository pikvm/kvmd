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

from typing import Final
from typing import Callable
from typing import Any

import aiofiles.os

from ...logging import get_logger

from ...inotify import Inotify

from ... import aiotools
from ... import usb

from ...yamlconf import Section
from ...yamlconf import Option

from ...validators.basic import valid_float_f01
from ...validators.basic import valid_stripped_string
from ...validators.basic import valid_stripped_string_not_empty

from . import BaseUserGpioDriver


# =====
class Plugin(BaseUserGpioDriver):
    def __init__(
        self,
        instance_name: str,
        notifier: aiotools.AioNotifier,
        c: Section,
    ) -> None:

        super().__init__(instance_name, notifier, c)

        self.__init_delay: Final[float] = c.init_delay
        self.__udc: str = c.udc

        self.__udc_path = usb.get_gadget_path(usb.G_UDC)
        self.__functions_path = usb.get_gadget_path(usb.G_FUNCTIONS)
        self.__profile_path = usb.get_gadget_path(usb.G_PROFILE)

        self.__lock = asyncio.Lock()

    @classmethod
    def get_plugin_options(cls) -> dict[str, Option]:
        return {
            "udc":        Option("",  type=valid_stripped_string),
            "init_delay": Option(3.0, type=valid_float_f01),
        }

    @classmethod
    def get_pin_validator(cls) -> Callable[[Any], Any]:
        return valid_stripped_string_not_empty

    async def prepare(self) -> None:
        self.__udc = usb.find_udc(self.__udc)
        get_logger().info("Using UDC %s", self.__udc)

    async def run(self) -> None:
        logger = get_logger(0)
        while True:
            try:
                while True:
                    self._notifier.notify()
                    if (await aiofiles.os.path.isfile(self.__udc_path)):
                        break
                    await asyncio.sleep(5)

                with Inotify() as inotify:
                    await inotify.watch_all_changes(os.path.dirname(self.__udc_path))
                    await inotify.watch_all_changes(self.__profile_path)
                    self._notifier.notify()
                    while True:
                        restart = await inotify.consume_until_restart()
                        if restart:
                            break
                        elif restart is not None:
                            self._notifier.notify()
            except Exception:
                logger.exception("Unexpected OTG-bind watcher error")
                await asyncio.sleep(1)

    async def read(self, pin: str) -> bool:
        if pin == "udc":
            return (await self.__is_udc_enabled())
        return (await aiofiles.os.path.exists(self.__get_fdest_path(pin)))

    async def write(self, pin: str, state: bool) -> None:
        if pin == "udc":
            await self.__write_udc(state)
        else:
            await self.__write_function(pin, state)

    async def __write_udc(self, state: bool) -> None:
        async with self.__lock:
            enabled = await self.__is_udc_enabled()
            if enabled == state:
                return
            if state:
                if (await self.__recreate_profile()):
                    await self.__set_udc_enabled(True)
            else:
                await self.__set_udc_enabled(False)

    async def __write_function(self, func: str, state: bool) -> None:
        async with self.__lock:
            enabled = await aiofiles.os.path.exists(self.__get_fdest_path(func))
            if enabled == state:
                return
            if (await self.__is_udc_enabled()):
                await self.__set_udc_enabled(False)
            try:
                if state:
                    await aiofiles.os.symlink(
                        self.__get_fsrc_path(func),
                        self.__get_fdest_path(func),
                    )
                else:
                    await aiofiles.os.unlink(self.__get_fdest_path(func))
            except (FileNotFoundError, FileExistsError):
                pass
            finally:
                if (await self.__recreate_profile()):
                    try:
                        await asyncio.sleep(self.__init_delay)
                    finally:
                        await self.__set_udc_enabled(True)

    async def __recreate_profile(self) -> bool:
        # XXX: See pikvm/pikvm#1235
        # After unbind and bind, the gadgets stop working,
        # unless we recreate their links in the profile.
        # Some kind of kernel bug.
        has_funcs = False
        for func in (await aiofiles.os.listdir(self.__profile_path)):
            path = self.__get_fdest_path(func)
            if (await aiofiles.os.path.islink(path)):
                has_funcs = True
                try:
                    await aiofiles.os.unlink(path)
                    await aiofiles.os.symlink(self.__get_fsrc_path(func), path)
                except (FileNotFoundError, FileNotFoundError):
                    pass
        return has_funcs

    def __get_fsrc_path(self, func: str) -> str:
        return os.path.join(self.__functions_path, func)

    def __get_fdest_path(self, func: str) -> str:
        return os.path.join(self.__profile_path, func)

    async def __set_udc_enabled(self, enabled: bool) -> None:
        udc = (self.__udc if enabled else "\n")
        await aiotools.write_file(self.__udc_path, udc)

    async def __is_udc_enabled(self) -> bool:
        udc = await aiotools.read_file(self.__udc_path)
        return bool(udc.strip())

    def __str__(self) -> str:
        return f"GPIO({self._instance_name})"

    __repr__ = __str__
