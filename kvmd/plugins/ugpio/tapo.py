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
import functools
import tapo

from typing import Callable
from typing import Any

import serial_asyncio

from ...logging import get_logger

from ... import tools
from ... import aiotools

from ...yamlconf import Option

from ...validators.basic import valid_number
from ...validators.basic import valid_float_f0
from ...validators.basic import valid_float_f01
from ...validators.basic import valid_int_f1
from ...validators.basic import valid_stripped_string_not_empty
from ...validators.net import valid_ip


from . import BaseUserGpioDriver
from . import GpioDriverOfflineError


# =====
class Plugin(BaseUserGpioDriver):  # pylint: disable=too-many-instance-attributes
    def __init__(
        self,
        instance_name: str,
        notifier: aiotools.AioNotifier,

        ip: str,
        email: str,
        password: str,

        timeout: int,
        switch_delay: float,
        state_poll: float,
    ) -> None:

        super().__init__(instance_name, notifier)

        self.__ip = ip
        self.__email = email
        self.__password = password

        self.__timeout = timeout
        self.__switch_delay = switch_delay
        self.__state_poll = state_poll

        self.__switch_on: bool = False
        self.__update_notifier = aiotools.AioNotifier()

        self.__client = tapo.ApiClient(self.__email, self.__password, timeout_s=self.__timeout)
        self.__device = None

    @classmethod
    def get_plugin_options(cls) -> dict:
        return {
            "ip":         Option("",   type=valid_ip),
            "email":      Option("",   type=valid_stripped_string_not_empty),
            "password":      Option("",   type=valid_stripped_string_not_empty),
            "timeout":      Option(5,  type=valid_int_f1),
            "switch_delay": Option(0.5,  type=valid_float_f0),
            "state_poll":   Option(10.0, type=valid_float_f01),
        }

    @classmethod
    def get_pin_validator(cls) -> Callable[[Any], Any]:
        return str

    async def run(self) -> None:
        prev_switch_on = False

        try:
          self.__device = await self.__client.generic_device(self.__ip)
        except Exception as err:
            get_logger(0).error("Can't initialise Tapo Plug [%s]: %s",
                                self.__ip, tools.efmt(err))
            raise GpioDriverOfflineError(self)

        while True:
            try:
                status = await self.__device.get_device_info_json()
                self.__switch_on = True if status['device_on'] else False
            except Exception as err:
                get_logger(0).error("Can't get Tapo Plug status [%s]: %s",
                                self.__ip, tools.efmt(err))
                raise GpioDriverOfflineError(self)

            if self.__switch_on != prev_switch_on:
                self._notifier.notify()
                prev_switch_on = self.__switch_on

            await self.__update_notifier.wait(self.__state_poll)

    async def cleanup(self) -> None:
        self.__switch_on = False

    async def read(self, pin: str) -> bool:
        _ = pin
        return self.__switch_on

    async def write(self, pin: str, state: bool) -> None:
        _ = pin
        
        try:
            if state:
                await self.__device.on()
            else:
                await self.__device.off()
            
            await asyncio.sleep(self.__switch_delay)  # Slowdown
            self.__update_notifier.notify()
        except Exception as err:
            get_logger(0).error("Can't switch Tapo Plug [%s]: %s",
                                self.__ip, tools.efmt(err))
            raise GpioDriverOfflineError(self)

    def __str__(self) -> str:
        return f"Tapo({self._instance_name})"

    __repr__ = __str__
