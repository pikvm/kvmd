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


import functools
import requests
import json

from typing import Dict
from typing import Optional

from ...logging import get_logger

from ... import aiotools

from ...yamlconf import Option

from ...validators.net import valid_ip

from . import GpioDriverOfflineError
from . import BaseUserGpioDriver


# =====
class Plugin(BaseUserGpioDriver):  # pylint: disable=too-many-instance-attributes
    def __init__(  # pylint: disable=super-init-not-called
        self,
        instance_name: str,
        notifier: aiotools.AioNotifier,

        ip: str,
        username: int,
        device: str,
    ) -> None:

        super().__init__(instance_name, notifier)

        self.__ip = ip
        self.__username = username
        self.__device = device
        self.__channel: Optional[int] = -1

    @classmethod
    def get_plugin_options(cls) -> Dict:
        return {
            "ip":   Option("255.255.255.255", type=functools.partial(valid_ip, v6=False)),
            "username": Option(""),
            "device":  Option(""),
        }

    def register_input(self, pin: int, debounce: float) -> None:
        _ = pin
        _ = debounce

    def register_output(self, pin: int, initial: Optional[bool]) -> None:
        _ = pin
        _ = initial

    def prepare(self) -> None:
        get_logger(0).info("Probing driver %s on Device %s and IP %s ...", self, self.__device, self.__ip)

    async def run(self) -> None:
        await aiotools.wait_infinite()

    async def cleanup(self) -> None:
        pass

    async def read(self, pin: int) -> bool:
        _ = pin
        try:
           url_status = f"http://{self.__ip}/api/{self.__username}/lights/{self.__device}"
           r = requests.get(url_status, timeout=5)
           data = r.json()
           state = data['state']['on']
           if state == True:
              return True
           else:
              return False
        except:
           return False

    async def write(self, pin: int, state: bool) -> None:
        _ = pin
        if not state:
            return

        try:
            url_status = f"http://{self.__ip}/api/{self.__username}/lights/{self.__device}"
            url_set = f"http://{self.__ip}/api/{self.__username}/lights/{self.__device}/state"

            r = requests.get(url_status, timeout=5)
            data = r.json()
            name = data['name']
            state = data['state']['on']

            if state == True:
               get_logger(0).info(f"Smartplug (Device: %s with name: %s) Power State: %s -> Switch to False", self.__device, name, state)
               data_set = {"on":False}
            else:
               get_logger(0).info(f"Smartplug (Device: %s with name: %s) Power State: %s -> Switch to True", self.__device, name, state)
               data_set = {"on":True}
            r = requests.put(url_set, json.dumps(data_set), timeout=5)

        except Exception:
            get_logger(0).exception("Can't send to HUE Api on IP: %s", self.__ip)
            raise GpioDriverOfflineError(self)


    def __str__(self) -> str:
        return f"Hue({self._instance_name})"

    __repr__ = __str__
