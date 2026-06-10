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


# A Sony DualSense (PS5) controller emulator via FunctionFS.
# PC-only: PS5 console auth requires a Sony signing IC we can't emulate.
# Steam/SDL/Linux hid-playstation all recognize it without auth.
#
# hid-playstation probes via GET_REPORT feature reports on the control pipe
# (not interrupt OUT like Switch Pro), requesting:
#   0x09 (20 bytes) = MAC/pairing info
#   0x20 (64 bytes) = firmware version
#   0x05 (41 bytes) = IMU calibration
# Then streams 0x01 input reports on the interrupt IN endpoint.


import os
import struct
import threading
import multiprocessing
import queue
import errno

from typing import Any

from .... import aiomulti
from .... import tools
from .... import usb

from ....logging import get_logger

from . import ffs


# ===== DualSense USB HID Report Descriptor =====
_HID_REPORT_DESC = bytes([
    0x05, 0x01, 0x09, 0x05, 0xa1, 0x01,
    0x85, 0x01,
    0x09, 0x30, 0x09, 0x31, 0x09, 0x32, 0x09, 0x35, 0x09, 0x33, 0x09, 0x34,
    0x15, 0x00, 0x26, 0xff, 0x00, 0x75, 0x08, 0x95, 0x06, 0x81, 0x02,
    0x06, 0x00, 0xff, 0x09, 0x20, 0x95, 0x01, 0x81, 0x02,
    0x05, 0x01, 0x09, 0x39,
    0x15, 0x00, 0x25, 0x07, 0x35, 0x00, 0x46, 0x3b, 0x01, 0x65, 0x14,
    0x75, 0x04, 0x95, 0x01, 0x81, 0x42, 0x65, 0x00,
    0x05, 0x09, 0x19, 0x01, 0x29, 0x0f, 0x15, 0x00, 0x25, 0x01,
    0x75, 0x01, 0x95, 0x0f, 0x81, 0x02,
    0x06, 0x00, 0xff, 0x09, 0x21, 0x95, 0x0d, 0x81, 0x02,
    0x06, 0x00, 0xff, 0x09, 0x22,
    0x15, 0x00, 0x26, 0xff, 0x00, 0x75, 0x08, 0x95, 0x34, 0x81, 0x02,
    0x85, 0x02, 0x09, 0x23, 0x95, 0x2f, 0x91, 0x02,
    0x85, 0x05, 0x09, 0x33, 0x95, 0x28, 0xb1, 0x02,
    0x85, 0x08, 0x09, 0x34, 0x95, 0x2f, 0xb1, 0x02,
    0x85, 0x09, 0x09, 0x24, 0x95, 0x13, 0xb1, 0x02,
    0x85, 0x20, 0x09, 0x26, 0x95, 0x3f, 0xb1, 0x02,
    0x85, 0x22, 0x09, 0x40, 0x95, 0x3f, 0xb1, 0x02,
    0x85, 0x80, 0x09, 0x28, 0x95, 0x3f, 0xb1, 0x02,
    0x85, 0x81, 0x09, 0x29, 0x95, 0x3f, 0xb1, 0x02,
    0x85, 0x82, 0x09, 0x2a, 0x95, 0x09, 0xb1, 0x02,
    0x85, 0x83, 0x09, 0x2b, 0x95, 0x3f, 0xb1, 0x02,
    0x85, 0xf1, 0x09, 0x31, 0x95, 0x3f, 0xb1, 0x02,
    0x85, 0xf2, 0x09, 0x32, 0x95, 0x0f, 0xb1, 0x02,
    0x85, 0xf0, 0x09, 0x30, 0x95, 0x3f, 0xb1, 0x02,
    0xc0,
])

_MAC_ADDR = bytes([0xA0, 0xB6, 0xE9, 0x11, 0x22, 0x33])


def make_dualsense_report(buttons: int, lx: int, ly: int, rx: int, ry: int,
                          lt: int, rt: int, hat: int, counter: int = 0) -> bytes:
    d = bytearray(64)
    d[0] = 0x01  # report ID

    # Sticks: 8-bit 0-255, center=128
    d[1] = lx & 0xFF   # LX
    d[2] = ly & 0xFF   # LY
    d[3] = rx & 0xFF   # RX
    d[4] = ry & 0xFF   # RY
    d[5] = lt & 0xFF   # L2 trigger analog
    d[6] = rt & 0xFF   # R2 trigger analog
    d[7] = counter & 0xFF  # vendor byte (sequence)

    # Hat switch + buttons
    # d[8] bits[3:0] = hat (0-7, 8=null), bits[7:4] = square(4), cross(5), circle(6), triangle(7)
    def bit(index: int) -> int:
        return (buttons >> index) & 1

    hat_val = hat if hat <= 7 else 0x08
    # Gamepad API: 0=A(cross) 1=B(circle) 2=X(square) 3=Y(triangle)
    d[8] = (hat_val & 0x0F) | (bit(2) << 4) | (bit(0) << 5) | (bit(1) << 6) | (bit(3) << 7)

    # d[9]: L1(0) R1(1) L2btn(2) R2btn(3) share(4) options(5) L3(6) R3(7)
    d[9] = (bit(4) | bit(5) << 1 |
            (1 if lt > 0 else 0) << 2 | (1 if rt > 0 else 0) << 3 |
            bit(8) << 4 | bit(9) << 5 | bit(10) << 6 | bit(10) << 7)

    # d[10]: PS button(0), touchpad click(1), mute(2)
    d[10] = 0

    return bytes(d)


_NEUTRAL = make_dualsense_report(0, 128, 128, 128, 128, 0, 0, 8)


# =====
class DualSenseProcess:
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

        self.__proc = aiomulti.AioMpProcess("hid-dualsense", self.__subprocess)
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
            self.__unbind()
            await self.__proc.async_join()

    def send_state_event(
        self,
        buttons: int,
        lx: int, ly: int, rx: int, ry: int,
        lt: int, rt: int,
        hat: int,
    ) -> None:
        self.__state_q.put_nowait(make_dualsense_report(buttons, lx, ly, rx, ry, lt, rt, hat))

    def send_clear_event(self) -> None:
        self.__state_q.put_nowait(bytes(_NEUTRAL))

    def send_reset_event(self) -> None:
        self.send_clear_event()

    # =====

    def __subprocess(self) -> None:
        logger = get_logger(0)
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

        state = bytearray(_NEUTRAL)

        class _INEndpoint(ffs.EndpointINFile):
            def onComplete(self, buffer_list: Any, user_data: Any, status: int) -> Any:
                if status < 0:
                    if status == -errno.ESHUTDOWN:
                        return False
                    raise IOError(-status)
                return True

        class _OUTEndpoint(ffs.EndpointOUTFile):
            def onComplete(self, data: Any, status: int) -> Any:
                return None

        class _DualSense(ffs.Function):
            def __init__(self, path: str) -> None:
                (fs_list, hs_list, ss_list) = ffs.getInterfaceInAllSpeeds(
                    interface={
                        "bInterfaceClass": 0x03,
                        "bInterfaceSubClass": 0x00,
                        "bInterfaceProtocol": 0x00,
                        "iInterface": 1,
                    },
                    endpoint_list=[
                        {"endpoint": {"bEndpointAddress": ffs.USB_DIR_IN,
                                      "bmAttributes": ffs.USB_ENDPOINT_XFER_INT,
                                      "wMaxPacketSize": 64, "bInterval": 1}},
                        {"endpoint": {"bEndpointAddress": ffs.USB_DIR_OUT,
                                      "bmAttributes": ffs.USB_ENDPOINT_XFER_INT,
                                      "wMaxPacketSize": 64, "bInterval": 1}},
                    ],
                    class_descriptor_list=[
                        {
                            "bDescriptorType": 0x21,
                            "data": struct.pack("<HBBBH",
                                                0x0111, 0, 1, 0x22,
                                                len(_HID_REPORT_DESC)),
                        },
                    ],
                )
                super().__init__(path, fs_list=fs_list, hs_list=hs_list, ss_list=ss_list,
                                 lang_dict={0x0409: ["Wireless Controller"]},
                                 hid_report_desc=_HID_REPORT_DESC)

            def onSetup(self, request_type: int, request: int, value: int,
                        index: int, length: int) -> None:
                if ((request_type & ffs.USB_TYPE_MASK) == ffs.USB_TYPE_CLASS
                        and (request_type & ffs.USB_DIR_IN)
                        and request == 0x01):
                    report_id = value & 0xFF
                    if report_id == 0x09:
                        resp = bytearray(20)
                        resp[0] = 0x09
                        resp[1:7] = _MAC_ADDR
                        os.write(self._ep0_fd, bytes(resp[:length]))
                        return
                    elif report_id == 0x20:
                        resp = bytearray(64)
                        resp[0] = 0x20
                        struct.pack_into("<I", resp, 1, 0x00000400)
                        struct.pack_into("<I", resp, 5, 0x00000400)
                        os.write(self._ep0_fd, bytes(resp[:length]))
                        return
                    elif report_id == 0x05:
                        resp = bytearray(41)
                        resp[0] = 0x05
                        os.write(self._ep0_fd, bytes(resp[:length]))
                        return
                super().onSetup(request_type, request, value, index, length)

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
            with _DualSense(self.__ffs_path) as function:
                self.__bind()
                threading.Thread(target=drain, daemon=True).start()
                function.processEventsForever()
        except Exception:
            logger.exception("Unexpected HID-dualsense error")
        finally:
            # Only unbind on intentional shutdown: the UDC is shared with the
            # keyboard, mouse and sibling gamepad slots, so a crashing slot
            # must not yank the whole gadget off the bus.
            if stop_event.is_set():
                self.__unbind()
            state_flags.update(online=False)

    def __udc_path(self) -> str:
        return os.path.join(self.__gadget_path, "UDC")

    def __bind(self) -> None:
        udc = usb.find_udc(self.__udc)
        ffs.wait_bind_udc(self.__gadget_path, udc, self.__stop_event, get_logger(0))

    def __unbind(self) -> None:
        try:
            with open(self.__udc_path(), "w") as file:
                file.write("\n")
        except OSError as ex:
            if not tools.is_oserror(ex, errno.ENODEV):
                get_logger(0).error("HID-dualsense: can't unbind UDC: %s", tools.efmt(ex))
