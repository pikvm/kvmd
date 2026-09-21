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


import multiprocessing
import errno
import time

from typing import Final
from typing import Callable
from typing import Any

import serial

from ...logging import get_logger

from ... import aiotools
from ... import aiomulti

from ...yamlconf import Section
from ...yamlconf import Option

from ...validators.basic import valid_number
from ...validators.basic import valid_float_f01
from ...validators.os import valid_abs_path
from ...validators.hw import valid_tty_speed

from . import GpioDriverOfflineError
from . import BaseUserGpioDriver


# =====
class Plugin(BaseUserGpioDriver):  # pylint: disable=too-many-instance-attributes
    __CH_MIN: Final[int] = 0
    __CH_MAX: Final[int] = 3

    def __init__(
        self,
        instance_name: str,
        notifier: aiotools.AioNotifier,
        c: Section,
    ) -> None:

        super().__init__(instance_name, notifier, c)

        self.__device_path: Final[str] = c.device
        self.__speed: Final[int] = c.speed
        self.__read_timeout: Final[int] = c.read_timeout

        self.__ctl_q: aiomulti.AioMpQueue[int] = aiomulti.AioMpQueue()
        self.__ch_q: aiomulti.AioMpQueue[int | None] = aiomulti.AioMpQueue()
        self.__ch: (int | None) = -1

        self.__proc = aiomulti.AioMpProcess(f"gpio-gz-hk401x-{self._instance_name}", self.__serial_worker)
        self.__stop_event = multiprocessing.Event()

    @classmethod
    def get_plugin_options(cls) -> dict:
        return {
            "device":       Option("",   type=valid_abs_path),
            "speed":        Option(9600, type=valid_tty_speed),
            "read_timeout": Option(2.0,  type=valid_float_f01),
        }

    @classmethod
    def get_pin_validator(cls) -> Callable[[Any], Any]:
        return valid_number.mk(min=cls.__CH_MIN, max=cls.__CH_MAX, name="GZ-HK401x channel")

    async def prepare(self) -> None:
        self.__proc.start()

    async def run(self) -> None:
        while True:
            (got, ch) = await self.__ch_q.async_fetch_last(1)
            if got and self.__ch != ch:
                self.__ch = ch
                self._notifier.notify()

    async def cleanup(self) -> None:
        if self.__proc.is_alive():
            self.__stop_event.set()
            await self.__proc.async_join()

    async def read(self, pin: str) -> bool:
        if not self.__is_online():
            raise GpioDriverOfflineError(self)
        return (self.__ch == int(pin))

    async def write(self, pin: str, state: bool) -> None:
        if not self.__is_online():
            raise GpioDriverOfflineError(self)
        if state:
            self.__ctl_q.put_nowait(int(pin))

    # =====

    def __is_online(self) -> bool:
        return (
            self.__proc.is_alive()
            and self.__ch is not None
        )

    def __serial_worker(self) -> None:
        logger = get_logger(0)
        while not self.__stop_event.is_set():
            try:
                with self.__get_serial() as tty:
                    data = b""
                    self.__ch_q.put_nowait(-1)

                    # Wait for first port heartbeat to set correct channel (~2 sec max).
                    # Only for the classic switch with protocol version 1.

                    while not self.__stop_event.is_set():
                        (ch, data) = self.__recv_channel(tty, data)
                        if ch is not None:
                            self.__ch_q.put_nowait(ch)

                        (got, ch) = self.__ctl_q.fetch_last(0.1)
                        if got:
                            assert ch is not None
                            self.__send_channel(tty, ch)

            except Exception as ex:
                self.__ch_q.put_nowait(None)
                if isinstance(ex, serial.SerialException) and ex.errno == errno.ENOENT:  # pylint: disable=no-member
                    logger.error("Missing %s serial device: %s", self, self.__device_path)
                else:
                    logger.exception("Unexpected %s error", self)
                time.sleep(1)

    def __get_serial(self) -> serial.Serial:
        return serial.Serial(self.__device_path, self.__speed, timeout=self.__read_timeout)

    def __recv_channel(self, tty: serial.Serial, data: bytes) -> tuple[(int | None), bytes]:
        ch: (int | None) = None
        if tty.in_waiting:
            data += tty.read_all()
            get_logger(0).debug("Driver %s received serial data %r", self, data)
            if len(data) != 1:
                get_logger(0).warning("Driver %s received invalid data: %r", self, data)
            else:
                ch = data[0] - 1
                if not (self.__CH_MIN <= ch <= self.__CH_MAX):
                    ch = None
                    get_logger(0).warning("Driver %s received invalid serial data: %r", self, data)
            data = b""
        return (ch, data)

    def __send_channel(self, tty: serial.Serial, ch: int) -> None:
        get_logger(0).info("Sending channel %s", ch)
        assert self.__CH_MIN <= ch <= self.__CH_MAX
        ch += 1
        ch_byte = 0x30 + ch
        cmd = bytearray(b"\xfe\x00\x33")
        cmd.append(ch_byte)
        cmd.append(0xAA)
        tty.write(bytes(cmd))
        tty.flush()

    def __str__(self) -> str:
        return f"GZ-HK401X({self._instance_name})"

    __repr__ = __str__
