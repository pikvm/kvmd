# ========================================================================== #
#                                                                            #
#    KVMD - The main PiKVM daemon.                                           #
#                                                                            #
#    Copyright (C) 2018-2022  Maxim Devaev <mdevaev@gmail.com>               #
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
import contextlib

from typing import Generator
from typing import Any

import serial

from ...yamlconf import Option
from ...logging import get_logger

from ...validators.basic import valid_float_f01
from ...validators.os import valid_abs_path
from ...validators.hw import valid_tty_speed

from ._ch9329 import BasePhyConnection
from ._ch9329 import BasePhy
from ._ch9329 import BaseMcuHid


# =====
class _SerialPhyConnection(BasePhyConnection):
    def __init__(self, tty: serial.Serial) -> None:
        self.__tty = tty

    def send(self, request: list) -> bytes:
        get_logger(0).info(f"SerialPhy : request = {request}")
        #assert len(request) == 8
        #assert request[0] == 0x33
        if self.__tty.in_waiting:
            self.__tty.read_all()
        self.__tty.write(serial.to_bytes(request))
        data = list(self.__tty.read(5))
        if data and data[4] :
            more_data = list(self.__tty.read(data[4] + 1))
            data.extend(more_data)
            get_logger(0).info(f"SerialPhy : data = {data}")
            return data
        else :
            return b""
        #for x in range(7):
        #    byte = self.__tty.read()
        #    if byte : data.append(int.from_bytes(byte))
        #    get_logger(0).info(f"SerialPhy : byte = {byte}")
        #data = self.__tty.read(4)
        #    if data[0] == 0x34:  # New response protocol
        #        data += self.__tty.read(4)
        #        if len(data) != 8:
        #            return b""
        #    return data


class _SerialPhy(BasePhy):
    def __init__(
        self,
        device_path: str,
        speed: int,
        read_timeout: float,
    ) -> None:

        self.__device_path = device_path
        self.__speed = speed
        self.__read_timeout = read_timeout

    def has_device(self) -> bool:
        return os.path.exists(self.__device_path)

    @contextlib.contextmanager
    def connected(self) -> Generator[_SerialPhyConnection, None, None]:  # type: ignore
        with serial.Serial(self.__device_path, self.__speed, timeout=self.__read_timeout) as tty:
            yield _SerialPhyConnection(tty)


# =====
class Plugin(BaseMcuHid):
    def __init__(self, **kwargs: Any) -> None:
        phy_kwargs: dict = {
            (option.unpack_as or key): kwargs.pop(option.unpack_as or key)
            for (key, option) in self.__get_phy_options().items()
        }
        super().__init__(phy=_SerialPhy(**phy_kwargs), **kwargs)

    @classmethod
    def get_plugin_options(cls) -> dict:
        return {
            **cls.__get_phy_options(),
            **BaseMcuHid.get_plugin_options(),
        }

    @classmethod
    def __get_phy_options(cls) -> dict:
        return {
            "device":       Option("/dev/kvmd-hid", type=valid_abs_path, unpack_as="device_path"),
            "speed":        Option(9600, type=valid_tty_speed),
            "read_timeout": Option(0.3,    type=valid_float_f01),
        }
