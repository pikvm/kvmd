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


# An Xbox 360 (XInput) controller cannot be expressed as a Linux f_hid gadget
# function the way the keyboard/mouse/generic-gamepad are: it is a USB
# vendor-specific interface (class 0xFF / subclass 0x5D / protocol 0x01) with no
# HID report descriptor. It is therefore implemented as a FunctionFS function
# whose endpoints are serviced from user space by the process below.
#
# Lifecycle note: a FunctionFS function only becomes "ready" once its descriptors
# have been written to ep0, and the gadget can only be bound to the UDC after
# that. So kvmd-otg creates the ffs function and mounts it but does NOT bind the
# UDC; this process writes the descriptors and then performs the bind itself.
# The report layout and mapping are validated against the Linux xpad driver.


import os
import struct
import threading
import multiprocessing
import queue
import errno

from typing import Any

from .... import aiomulti
from .... import tools

from ....logging import get_logger

try:
    import functionfs
    import functionfs.ch9 as ch9
    _IMPORT_ERROR: (Exception | None) = None
except Exception as _ex:  # pragma: no cover -- only importable on the gadget host
    functionfs = None
    ch9 = None
    _IMPORT_ERROR = _ex


# =====
def make_xinput_report(buttons: int, lx: int, ly: int, rx: int, ry: int, lt: int, rt: int, hat: int) -> bytes:
    # The 20-byte input report an Xbox 360 wired controller sends on its
    # interrupt-IN endpoint. Sticks are up-positive (the web Gamepad API is
    # down-positive, so the Y axes are inverted here).
    def axis(value: int, invert: bool=False) -> int:
        signed = int(round((value - 128) / 127.0 * 32767))
        return max(-32768, min(32767, -signed if invert else signed))

    up    = int(hat in (7, 0, 1))
    right = int(hat in (1, 2, 3))
    down  = int(hat in (3, 4, 5))
    left  = int(hat in (5, 6, 7))

    def bit(index: int) -> int:
        return (buttons >> index) & 1

    b2 = up | down << 1 | left << 2 | right << 3 | bit(7) << 4 | bit(6) << 5 | bit(8) << 6 | bit(9) << 7
    b3 = bit(4) | bit(5) << 1 | bit(10) << 2 | bit(0) << 4 | bit(1) << 5 | bit(2) << 6 | bit(3) << 7
    return struct.pack("<BBBBBBhhhh6x", 0x00, 0x14, b2, b3, lt, rt,
                       axis(lx), axis(ly, True), axis(rx), axis(ry, True))


_NEUTRAL = make_xinput_report(0, 128, 128, 128, 128, 0, 0, 8)


# =====
class XInputProcess:  # pylint: disable=too-many-instance-attributes
    def __init__(
        self,
        notifier: aiomulti.AioMpNotifier,
        ffs_path: str,
        gadget_path: str,
        udc: str,
        noop: bool,
    ) -> None:

        self.__ffs_path = ffs_path
        self.__gadget_path = gadget_path
        self.__udc = udc
        self.__noop = noop

        self.__proc = aiomulti.AioMpProcess("hid-xinput", self.__subprocess)
        self.__state_q: aiomulti.AioMpQueue[bytes] = aiomulti.AioMpQueue()
        self.__state_flags = aiomulti.AioSharedFlags({"online": False}, notifier)
        self.__stop_event = multiprocessing.Event()

    def start(self) -> None:
        self.__proc.start()

    async def get_state(self) -> dict:
        return (await self.__state_flags.get())

    async def cleanup(self) -> None:
        if self.__proc.is_alive():
            self.__stop_event.set()
            self.__unbind()  # Disconnect so processEventsForever() returns in the subprocess
            await self.__proc.async_join()

    # =====

    def send_state_event(  # pylint: disable=too-many-arguments
        self,
        buttons: int,
        lx: int, ly: int, rx: int, ry: int,
        lt: int, rt: int,
        hat: int,
    ) -> None:

        self.__state_q.put_nowait(make_xinput_report(buttons, lx, ly, rx, ry, lt, rt, hat))

    def send_clear_event(self) -> None:
        self.__state_q.put_nowait(bytes(_NEUTRAL))

    def send_reset_event(self) -> None:
        self.send_clear_event()

    # =====

    def __subprocess(self) -> None:
        logger = get_logger(0)
        if functionfs is None:
            logger.error("HID-xinput requires python-functionfs: %s", _IMPORT_ERROR)
            return

        state_q = self.__state_q
        stop_event = self.__stop_event
        state_flags = self.__state_flags

        if self.__noop:
            while not stop_event.is_set():
                try:
                    state_q.get(timeout=0.1)
                except queue.Empty:
                    pass
            return

        # The shared 20-byte report buffer. The drain thread mutates it in place
        # and the IN endpoint keeps resubmitting it, so each USB poll sends the
        # latest state. Must be a bytearray -- libaio requires a writable buffer.
        state = bytearray(_NEUTRAL)

        class _INEndpoint(functionfs.EndpointINFile):  # type: ignore
            def onComplete(self, buffer_list: Any, user_data: Any, status: int) -> Any:
                if status < 0:
                    if status == -errno.ESHUTDOWN:
                        return False
                    raise IOError(-status)
                return True

        class _OUTEndpoint(functionfs.EndpointOUTFile):  # type: ignore
            def onComplete(self, data: Any, status: int) -> Any:
                return None  # Discard rumble / LED writes from the host

        class _XInput(functionfs.Function):  # type: ignore
            def __init__(self, path: str) -> None:
                (fs_list, hs_list, ss_list) = functionfs.getInterfaceInAllSpeeds(
                    interface={
                        "bInterfaceClass": 0xFF,
                        "bInterfaceSubClass": 0x5D,
                        "bInterfaceProtocol": 0x01,
                        "iInterface": 1,
                    },
                    endpoint_list=[
                        {"endpoint": {"bEndpointAddress": ch9.USB_DIR_IN,
                                      "bmAttributes": ch9.USB_ENDPOINT_XFER_INT,
                                      "wMaxPacketSize": 0x20, "bInterval": 1}},
                        {"endpoint": {"bEndpointAddress": ch9.USB_DIR_OUT,
                                      "bmAttributes": ch9.USB_ENDPOINT_XFER_INT,
                                      "wMaxPacketSize": 0x20, "bInterval": 1}},
                    ],
                )
                super().__init__(path, fs_list=fs_list, hs_list=hs_list, ss_list=ss_list,
                                 lang_dict={0x0409: ["Controller"]})

            def getEndpointClass(self, is_in: bool, descriptor: Any) -> Any:
                return _INEndpoint if is_in else _OUTEndpoint

            def onEnable(self) -> None:
                super().onEnable()
                state_flags.update(online=True)
                self.getEndpoint(1).submit((state,))

            def onDisable(self) -> None:
                state_flags.update(online=False)
                super().onDisable()

        def drain() -> None:
            while not stop_event.is_set():
                try:
                    report = state_q.get(timeout=0.1)
                except queue.Empty:
                    continue
                state[:] = report

        try:
            with _XInput(self.__ffs_path) as function:
                # Descriptors are written now, so the function is ready -> bind.
                self.__bind()
                threading.Thread(target=drain, daemon=True).start()
                function.processEventsForever()
        except Exception:
            logger.exception("Unexpected HID-xinput error")
        finally:
            self.__unbind()
            state_flags.update(online=False)

    def __udc_path(self) -> str:
        return os.path.join(self.__gadget_path, "UDC")

    def __bind(self) -> None:
        udc = self.__udc or sorted(os.listdir("/sys/class/udc"))[0]
        with open(self.__udc_path(), "w") as file:
            file.write(udc)
        get_logger(0).info("HID-xinput: bound gadget to UDC %s", udc)

    def __unbind(self) -> None:
        try:
            with open(self.__udc_path(), "w") as file:
                file.write("\n")
        except OSError as ex:
            if not tools.is_oserror(ex, errno.ENODEV):
                get_logger(0).error("HID-xinput: can't unbind UDC: %s", tools.efmt(ex))
