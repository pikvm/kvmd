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
    def __init__(
        self,
        instance_name: str,
        notifier: aiotools.AioNotifier,

        device_path: str,
        speed: int,
        read_timeout: float,
        protocol: int,
    ) -> None:

        super().__init__(instance_name, notifier)

        self.__device_path = device_path
        self.__speed = speed
        self.__read_timeout = read_timeout
        self.__protocol = protocol

        self.__ctl_q: aiomulti.AioMpQueue[int] = aiomulti.AioMpQueue()
        self.__ch_q: aiomulti.AioMpQueue[int | None] = aiomulti.AioMpQueue()
        self.__ch: (int | None) = -1

        self.__proc = aiomulti.AioMpProcess(f"gpio-ezcoo-{self._instance_name}", self.__serial_worker)
        self.__stop_event = multiprocessing.Event()

    @classmethod
    def get_plugin_options(cls) -> dict:
        return {
            "device":       Option("",     type=valid_abs_path, unpack_as="device_path"),
            "speed":        Option(115200, type=valid_tty_speed),
            "read_timeout": Option(2.0,    type=valid_float_f01),
            "protocol":     Option(1,      type=valid_number.mk(min=1, max=2)),
        }

    @classmethod
    def get_pin_validator(cls) -> Callable[[Any], Any]:
        return valid_number.mk(min=0, max=3, name="Ezcoo channel")

    def prepare(self) -> None:
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

                    # Get actual state without modifying the current
                    if self.__protocol <= 1:
                        tty.write(b"GET OUT1 VS\n" * 2)  # Twice because of some bugs
                    else:
                        tty.write(b"EZG OUT1 VS\n" * 2)
                    tty.flush()

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
            found = list(re.finditer(b"(OUT1 VS \\d+)|(V[0-9a-fA-F]{2}S)", data))
            if found:
                last = found[-1]
                ch = {
                    b"V0CS": 0,  # Switching retval (manual or via the TTY)
                    b"V18S": 1,
                    b"V5ES": 2,
                    b"V08S": 3,
                    b"OUT1 VS 1": 0,  # "EZG OUT1 VS" return value
                    b"OUT1 VS 2": 1,
                    b"OUT1 VS 3": 2,
                    b"OUT1 VS 4": 3,
                }.get(last[0], -1)
                data = data[last.end(0):]
        return (ch, data)

    def __send_channel(self, tty: serial.Serial, ch: int) -> None:
        assert 0 <= ch <= 3
        cmd = b"%s OUT1 VS IN%d\n" % (
            (b"SET" if self.__protocol == 1 else b"EZS"),
            ch + 1,
        )
        tty.write(cmd * 2)  # Twice because of ezcoo bugs
        tty.flush()

    def __str__(self) -> str:
        return f"Ezcoo({self._instance_name})"

    __repr__ = __str__
