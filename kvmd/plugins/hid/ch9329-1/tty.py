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
import serial
import time

from ....logging import get_logger


class TTY:
    def __init__(self, device_path, speed, read_timeout) -> None:
        self._device_path = device_path
        self._speed = speed
        self._read_timeout = read_timeout

    def has_device(self) -> bool:
        return os.path.exists(self._device_path)

    def connect(self) -> None:
        get_logger(0).info(f"TTY : inside connect")
        self._tty = serial.Serial(self._device_path, self._speed, timeout=self._read_timeout)

    def send(self, request: list) -> list:
        cmd = self._wrap_cmd(request)
        get_logger(0).info(f"TTY : request = {cmd}")
        self._tty.write(serial.to_bytes(cmd))
        data = list(self._tty.read(5))
        if data and data[4] :
            more_data = list(self._tty.read(data[4] + 1))
            data.extend(more_data)
            get_logger(0).info(f"TTY : data = {data}")
            return data
        else :
            return []

    def _wrap_cmd(self, cmd: list) -> list:
        cmd.insert(0, 0xAB)
        cmd.insert(0, 0x57)
        sum = self._checksum(cmd)
        cmd.append(sum)
        return cmd

    def _checksum(self, cmd: list) -> int:
        return sum(cmd) % 256

RESET = [0x00,0x0F,0x00]
GET_INFO = [0x00,0x01,0x00]
