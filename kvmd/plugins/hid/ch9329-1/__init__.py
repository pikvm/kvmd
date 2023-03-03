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


import multiprocessing
import contextlib
import queue
import time

from typing import Iterable
from typing import Generator
from typing import AsyncGenerator

from ....logging import get_logger

from .... import tools
from .... import aiotools
from .... import aiomulti
from .... import aioproc

from ....yamlconf import Option

from ....validators.basic import valid_bool
from ....validators.basic import valid_int_f0
from ....validators.basic import valid_int_f1
from ....validators.basic import valid_float_f01
from ....validators.os import valid_abs_path
from ....validators.hw import valid_tty_speed

from .. import BaseHid

from .tty import TTY
from .mouse import Mouse
from .keyboard import Keyboard

from .tty import RESET
from .tty import GET_INFO


# =====
class _RequestError(Exception):
    def __init__(self, msg: str) -> None:
        super().__init__(msg)
        self.msg = msg


class _PermRequestError(_RequestError):
    pass


class _TempRequestError(_RequestError):
    pass



class Plugin(BaseHid, multiprocessing.Process):  # pylint: disable=too-many-instance-attributes
    def __init__(  # pylint: disable=too-many-arguments,super-init-not-called
        self,
        device_path: str,
        speed: int,
        read_timeout: float,
        reset_inverted: bool,
        reset_delay: float,
        read_retries: int,
        common_retries: int,
        retries_delay: float,
        errors_threshold: int,
        noop: bool,
    ) -> None:

        multiprocessing.Process.__init__(self, daemon=True)

        self.__device_path = device_path
        self.__speed = speed
        self.__read_timeout = read_timeout
        self.__read_retries = read_retries
        self.__common_retries = common_retries
        self.__retries_delay = retries_delay
        self.__errors_threshold = errors_threshold
        self.__noop = noop

        self.__reset_required_event = multiprocessing.Event()
        self.__cmd_queue: "multiprocessing.Queue[list]" = multiprocessing.Queue()

        self.__notifier = aiomulti.AioProcessNotifier()
        self.__state_flags = aiomulti.AioSharedFlags({
            "online": 0,
            "busy": 0,
            "status": 0,
        }, self.__notifier, type=int)

        self.__stop_event = multiprocessing.Event()

        self.keyboard_leds = {
            "caps" : False,
            "scroll" : False,
            "num" : False
        }


    @classmethod
    def get_plugin_options(cls) -> dict:
        return {
            "device":           Option("/dev/kvmd-hid", type=valid_abs_path, unpack_as="device_path"),
            "speed":            Option(9600,  type=valid_tty_speed),
            "read_timeout":     Option(0.3,   type=valid_float_f01),
            "reset_inverted":   Option(False, type=valid_bool),
            "reset_delay":      Option(0.1,   type=valid_float_f01),
            "read_retries":     Option(5,     type=valid_int_f1),
            "common_retries":   Option(5,     type=valid_int_f1),
            "retries_delay":    Option(0.5,   type=valid_float_f01),
            "errors_threshold": Option(5,     type=valid_int_f0),
            "noop":             Option(False, type=valid_bool),
        }

    def sysprep(self) -> None:
        get_logger(0).info("Starting HID daemon ...")
        self.tty = TTY(self.__device_path, self.__speed, self.__read_timeout)
        self.mouse = Mouse()
        self.keyboard = Keyboard()
        self.start()

    async def get_state(self) -> dict:
        state = await self.__state_flags.get()
        online = bool(state["online"])
        active_mouse = self.mouse._active
        absolute = ( active_mouse == 'usb')

        return {
            "online": True,
            "busy": False,
            "connected": None,
            "keyboard": {
                "online": True,
                "leds": self.keyboard_leds,
                "outputs": {"available": [], "active": ""},
            },
            "mouse": {
                "online": True,
                "absolute": absolute,
                "outputs": {
                    "available" : ["usb", "usb_rel"],
                    "active" : active_mouse
                },
            },
        }

    async def poll_state(self) -> AsyncGenerator[dict, None]:
        prev_state: dict = {}
        while True:
            state = await self.get_state()
            if state != prev_state:
                yield state
                prev_state = state
            await self.__notifier.wait()

    async def reset(self) -> None:
        self.__reset_required_event.set()

    @aiotools.atomic_fg
    async def cleanup(self) -> None:
        if self.is_alive():
            get_logger(0).info("Stopping HID daemon ...")
            self.__stop_event.set()
        if self.is_alive() or self.exitcode is not None:
            self.join()

    # =====

    def send_key_events(self, keys: Iterable[tuple[str, bool]]) -> None:
        for (key, state) in keys:
            self.__queue_cmd(self.keyboard.key(key, state))

    def send_mouse_button_event(self, button: str, state: bool) -> None:
        self.__queue_cmd(self.mouse.button(button, state))

    def send_mouse_move_event(self, to_x: int, to_y: int) -> None:
        self.__queue_cmd(self.mouse.move(to_x, to_y))

    def send_mouse_wheel_event(self, delta_x: int, delta_y: int) -> None:
        self.__queue_cmd(self.mouse.wheel(delta_x, delta_y))

    def send_mouse_relative_event(self, delta_x: int, delta_y: int) -> None:
        self.__queue_cmd(self.mouse.relative(delta_x, delta_y))

    def set_params(self, keyboard_output: (str | None)=None, mouse_output: (str | None)=None) -> None:
        if mouse_output is not None:
            get_logger(0).info(f"HID : mouse output = {mouse_output}")
            self.mouse._active = mouse_output
            self.__notifier.notify()

    def set_connected(self, connected: bool) -> None:
        get_logger(0).info(f"HID : set_connected = {connected}")

    def clear_events(self) -> None:
        tools.clear_queue(self.__cmd_queue)

    def __queue_cmd(self, cmd: list, clear: bool=False) -> None:
        if not self.__stop_event.is_set():
            if clear:
                # FIXME: Если очистка производится со стороны процесса хида, то возможна гонка между
                # очисткой и добавлением нового события. Неприятно, но не смертельно.
                # Починить блокировкой после перехода на асинхронные очереди.
                tools.clear_queue(self.__cmd_queue)
            self.__cmd_queue.put_nowait(cmd)

    def run(self) -> None:  # pylint: disable=too-many-branches
        logger = aioproc.settle("HID", "hid")
        self.tty.connect()
        while not self.__stop_event.is_set():
            try:
                #with self.__gpio:
                self.__hid_loop()
                #self.tty.loop()
                    #if self.__phy.has_device():
                        #logger.info("Clearing HID events ...")
                        #try:
                        #    with self.__phy.connected() as conn:
                                #self.__process_request(conn, ClearEvent().make_request())
                        #except Exception:
                        #    logger.exception("Can't clear HID events")
            except Exception:
                logger.exception("Unexpected error in the GPIO loop")
                time.sleep(1)

    def __hid_loop(self) -> None:
        while not self.__stop_event.is_set():
            try:
                conn = self.tty
                while not (self.__stop_event.is_set() and self.__cmd_queue.qsize() == 0):
                    if self.__reset_required_event.is_set():
                        try:
                            self.__set_state_busy(True)
                            #self.__process_request(conn, RESET)
                        finally:
                            self.__reset_required_event.clear()
                    try:
                        cmd = self.__cmd_queue.get(timeout=0.1)
                        get_logger(0).info(f"HID : cmd = {cmd}")
                    except queue.Empty:
                        get_logger(0).info("HID : nothing in queue")
                        #self.__process_request(conn, GET_INFO)
                    else:
                        conn.send(cmd)
            except Exception:
                self.clear_events()
                get_logger(0).exception("Unexpected error in the HID loop")
                time.sleep(1)


    def __process_request(self, conn: TTY, request: bytes) -> bool:  # pylint: disable=too-many-branches
        logger = get_logger()
        error_messages: list[str] = []
        live_log_errors = False

        common_retries = self.__common_retries
        read_retries = self.__read_retries
        error_retval = False
        get_logger(0).info(f"HID request = {request!r}")

        while common_retries and read_retries:
            response = conn.send(request)
            try:
                get_logger(0).info(f"HID response = {response}")
                #return True
                #if len(response) < 4:
                #    read_retries -= 1
                #    raise _TempRequestError(f"No response from HID: request={request!r}")

                #if not check_response(response):
                #    request = REQUEST_REPEAT
                #    raise _TempRequestError("Invalid response checksum ...")

                code = response[3]
                #if code == 0x48:  # Request timeout  # pylint: disable=no-else-raise
                #    raise _TempRequestError(f"Got request timeout from HID: request={request!r}")
                #elif code == 0x40:  # CRC Error
                #    raise _TempRequestError(f"Got CRC error of request from HID: request={request!r}")
                #elif code == 0x45:  # Unknown command
                #    raise _PermRequestError(f"HID did not recognize the request={request!r}")
                #elif code == 0x24:  # Rebooted?
                #    raise _PermRequestError("No previous command state inside HID, seems it was rebooted")
                #elif code == 0x20:  # Legacy done
                self.__set_state_online(True)
                return True
                #elif code & 0x80:  # Pong/Done with state
                #    self.__set_state_pong(response)
                #    return True
                #raise _TempRequestError(f"Invalid response from HID: request={request!r}, response=0x{response!r}")

            except _RequestError as err:
                common_retries -= 1

                if live_log_errors:
                    logger.error(err.msg)
                else:
                    error_messages.append(err.msg)
                    if len(error_messages) > self.__errors_threshold:
                        for msg in error_messages:
                            logger.error(msg)
                        error_messages = []
                        live_log_errors = True

                if isinstance(err, _PermRequestError):
                    error_retval = True
                    break

                self.__set_state_online(False)

                if common_retries and read_retries:
                    time.sleep(self.__retries_delay)

        for msg in error_messages:
            logger.error(msg)
        if not (common_retries and read_retries):
            logger.error("Can't process HID request due many errors: %r", request)
        return error_retval

    def __set_state_online(self, online: bool) -> None:
        self.__state_flags.update(online=int(online))

    def __set_state_busy(self, busy: bool) -> None:
        self.__state_flags.update(busy=int(busy))
