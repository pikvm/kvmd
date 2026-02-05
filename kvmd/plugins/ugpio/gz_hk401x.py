# ========================================================================== #
#                                                                            #
#    KVMD - The main PiKVM daemon.                                           #
#                                                                            #
#    Copyright (C) 2018-2024  Maxim Devaev <mdevaev@gmail.com>               #
#                  2021-2021  Sebastian Goscik <sebastian.goscik@live.co.uk> #
#                  2023-2026  Up <up@gomen-yui.icu>                          #
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


import re
import multiprocessing
import errno
import time

from typing import Callable
from typing import Any

import serial

from ...logging import get_logger

from ... import aiotools
from ... import aiomulti

from ...yamlconf import Option

from ...validators.basic import valid_number
from ...validators.basic import valid_float_f01
from ...validators.os import valid_abs_path
from ...validators.hw import valid_tty_speed

from . import GpioDriverOfflineError
from . import BaseUserGpioDriver


# =====
class Plugin(BaseUserGpioDriver):  # pylint: disable=too-many-instance-attributes
    
    MIN_CHANNEL = 0
    MAX_CHANNEL = 3

    def __init__(
        self,
        instance_name: str,
        notifier: aiotools.AioNotifier,

        device_path: str,
        speed: int,
        read_timeout: float,
    ) -> None:

        super().__init__(instance_name, notifier)

        self.__device_path = device_path
        self.__speed = speed
        self.__read_timeout = read_timeout

        self.__ctl_q: aiomulti.AioMpQueue[int] = aiomulti.AioMpQueue()
        self.__channel_q: aiomulti.AioMpQueue[int | None] = aiomulti.AioMpQueue()
        self.__channel: (int | None) = -1

        self.__proc = aiomulti.AioMpProcess(f"gpio-gz-hk401x-{self._instance_name}", self.__serial_worker)
        self.__stop_event = multiprocessing.Event()

    @classmethod
    def get_plugin_options(cls) -> dict:
        return {
            "device":       Option("",    type=valid_abs_path, unpack_as="device_path"),
            "speed":        Option(9600, type=valid_tty_speed),
            "read_timeout": Option(2.0,   type=valid_float_f01),
        }

    @classmethod
    def get_pin_validator(cls) -> Callable[[Any], Any]:
        return valid_number.mk(min=0, max=3, name="GZ-HK401x channel")

    async def prepare(self) -> None:
        self.__proc.start()

    async def run(self) -> None:
        while True:
            (got, channel) = await self.__channel_q.async_fetch_last(1)
            if got and self.__channel != channel:
                self.__channel = channel
                self._notifier.notify()

    async def cleanup(self) -> None:
        if self.__proc.is_alive():
            self.__stop_event.set()
            await self.__proc.async_join()

    async def read(self, pin: str) -> bool:
        if not self.__is_online():
            raise GpioDriverOfflineError(self)
        return (self.__channel == int(pin))

    async def write(self, pin: str, state: bool) -> None:
        if not self.__is_online():
            raise GpioDriverOfflineError(self)
        if state:
            self.__ctl_q.put_nowait(int(pin))

    # =====

    def __is_online(self) -> bool:
        return (
            self.__proc.is_alive()
            and self.__channel is not None
        )

    def __serial_worker(self) -> None:
        logger = get_logger(0)
        while not self.__stop_event.is_set():
            try:
                with self.__get_serial() as tty:
                    data = b""
                    self.__channel_q.put_nowait(-1)

                    # Wait for first port heartbeat to set correct channel (~2 sec max).
                    # Only for the classic switch with protocol version 1.

                    while not self.__stop_event.is_set():
                        (channel, data) = self.__recv_channel(tty, data)
                        if channel is not None:
                            self.__channel_q.put_nowait(channel)

                        (got, channel) = self.__ctl_q.fetch_last(0.1)
                        if got:
                            assert channel is not None
                            self.__send_channel(tty, channel)
                            

            except Exception as ex:
                self.__channel_q.put_nowait(None)
                if isinstance(ex, serial.SerialException) and ex.errno == errno.ENOENT:  # pylint: disable=no-member
                    logger.error("Missing %s serial device: %s", self, self.__device_path)
                else:
                    logger.exception("Unexpected %s error", self)
                time.sleep(1)

    def __get_serial(self) -> serial.Serial:
        return serial.Serial(self.__device_path, self.__speed, timeout=self.__read_timeout)

    def __recv_channel(self, tty: serial.Serial, data: bytes) -> tuple[(int | None), bytes]:
        channel: (int | None) = None
        if tty.in_waiting:
            data += tty.read_all()
            get_logger(0).debug('Driver %s received serial data" %s', self._instance_name, data)
            if len(data) != 1:
                get_logger(0).warning('Driver %s received invalid data: "%s" .', self._instance_name, data)
            else:
                response = int.from_bytes(data, 'little', signed=False)
                if response < Plugin.MIN_CHANNEL + 1 or response > Plugin.MAX_CHANNEL + 1:
                    get_logger(0).warning('Driver %s received invalid serial data: "%s" .', self._instance_name, data)
                else:
                    channel = response - 1
            data = b""
        return (channel, data)

    def __send_channel(self, tty: serial.Serial, channel: int) -> None:
        get_logger(0).info('Sending channel %s', channel)
        assert 0 <= channel <= 3
        channel += 1
        channel_byte = 0x30 + channel
        cmd = bytearray(b'\xfe\x00\x33')
        cmd.append(channel_byte)
        cmd.append(0xaa)
        tty.write(bytes(cmd))
        tty.flush()

    def __str__(self) -> str:
        return f"GZ-HK401X({self._instance_name})"

    __repr__ = __str__
