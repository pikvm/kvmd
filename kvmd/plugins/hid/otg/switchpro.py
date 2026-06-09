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


# A Nintendo Switch Pro Controller emulator via FunctionFS. Implements the
# full USB handshake that the Linux hid-nintendo driver (and the Switch
# console itself) expects: 0x80-series USB commands, 0x01-series subcommands
# with SPI flash calibration reads, and 0x30 standard input reports.
#
# Like XInputProcess, this is a FunctionFS function whose endpoints are
# serviced from user space. The gamepad state is received via the same
# send_state_event / send_clear_event / send_reset_event interface.


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

try:
    import functionfs
    import functionfs.ch9 as ch9
    _IMPORT_ERROR: (Exception | None) = None
except Exception as _ex:
    functionfs = None
    ch9 = None
    _IMPORT_ERROR = _ex


# ===== Switch Pro HID Report Descriptor (209 bytes) =====
_HID_REPORT_DESC = bytes([
    0x05, 0x01, 0x15, 0x00, 0x09, 0x04, 0xa1, 0x01,
    0x85, 0x30, 0x05, 0x01, 0x05, 0x09, 0x19, 0x01,
    0x29, 0x0a, 0x15, 0x00, 0x25, 0x01, 0x75, 0x01,
    0x95, 0x0a, 0x55, 0x00, 0x65, 0x00, 0x81, 0x02,
    0x05, 0x09, 0x19, 0x0b, 0x29, 0x0e, 0x15, 0x00,
    0x25, 0x01, 0x75, 0x01, 0x95, 0x04, 0x81, 0x02,
    0x75, 0x01, 0x95, 0x02, 0x81, 0x03, 0x0b, 0x01,
    0x00, 0x01, 0x00, 0xa1, 0x00, 0x0b, 0x30, 0x00,
    0x01, 0x00, 0x0b, 0x31, 0x00, 0x01, 0x00, 0x0b,
    0x32, 0x00, 0x01, 0x00, 0x0b, 0x35, 0x00, 0x01,
    0x00, 0x15, 0x00, 0x27, 0xff, 0xff, 0x00, 0x00,
    0x75, 0x10, 0x95, 0x04, 0x81, 0x02, 0xc0, 0x0b,
    0x39, 0x00, 0x01, 0x00, 0x15, 0x00, 0x25, 0x07,
    0x35, 0x00, 0x46, 0x3b, 0x01, 0x65, 0x14, 0x75,
    0x04, 0x95, 0x01, 0x81, 0x42, 0x65, 0x00, 0x95,
    0x01, 0x81, 0x01, 0x05, 0x09, 0x19, 0x0f, 0x29,
    0x12, 0x15, 0x00, 0x25, 0x01, 0x75, 0x01, 0x95,
    0x04, 0x81, 0x02, 0x75, 0x08, 0x95, 0x34, 0x81,
    0x03, 0x06, 0x00, 0xff, 0x85, 0x21, 0x09, 0x01,
    0x75, 0x08, 0x95, 0x3f, 0x81, 0x03, 0x85, 0x81,
    0x09, 0x02, 0x75, 0x08, 0x95, 0x3f, 0x81, 0x03,
    0x85, 0x01, 0x09, 0x03, 0x75, 0x08, 0x95, 0x3f,
    0x91, 0x83, 0x85, 0x10, 0x09, 0x04, 0x75, 0x08,
    0x95, 0x3f, 0x91, 0x83, 0x85, 0x80, 0x09, 0x05,
    0x75, 0x08, 0x95, 0x3f, 0x91, 0x83, 0x85, 0x82,
    0x09, 0x06, 0x75, 0x08, 0x95, 0x3f, 0x91, 0x83,
    0xc0,
])

_MAC_ADDR = bytes([0x98, 0xB6, 0xE9, 0x11, 0x22, 0x33])


def _pack12(a: int, b: int) -> bytes:
    return bytes([a & 0xFF, ((a >> 8) & 0x0F) | ((b & 0x0F) << 4), (b >> 4) & 0xFF])


# center=2048, range=±1500
_STICK_CAL_LEFT = _pack12(0x5DC, 0x5DC) + _pack12(0x800, 0x800) + _pack12(0x5DC, 0x5DC)
_STICK_CAL_RIGHT = _pack12(0x800, 0x800) + _pack12(0x5DC, 0x5DC) + _pack12(0x5DC, 0x5DC)


def _pack_stick(x: int, y: int) -> bytes:
    return _pack12(x & 0xFFF, y & 0xFFF)


def make_switchpro_report(buttons: int, lx: int, ly: int, rx: int, ry: int,
                          lt: int, rt: int, hat: int, timer: int = 0) -> bytes:
    d = bytearray(64)
    d[0] = 0x30
    d[1] = timer & 0xFF
    d[2] = 0x90  # battery full + USB

    # Button mapping: web Gamepad API → Switch Pro button bytes
    # d[3] = right buttons: Y(0), X(1), B(2), A(3), SR(4), SL(5), R(6), ZR(7)
    # d[4] = shared: minus(0), plus(1), rstick(2), lstick(3), home(4), capture(5)
    # d[5] = left buttons: down(0), up(1), right(2), left(3), SR(4), SL(5), L(6), ZL(7)
    def bit(index: int) -> int:
        return (buttons >> index) & 1

    # Map browser Gamepad buttons: 0=A 1=B 2=X 3=Y 4=L 5=R 6=ZL 7=ZR
    #   8=minus 9=plus 10=lstick
    d[3] = (bit(3) | bit(2) << 1 | bit(1) << 2 | bit(0) << 3 |
            bit(5) << 6 | bit(7) << 7)
    d[4] = bit(8) | bit(9) << 1 | bit(10) << 2 | bit(10) << 3
    d[5] = bit(4) << 6 | bit(6) << 7

    # Hat switch → d-pad in d[5]
    up    = int(hat in (7, 0, 1))
    right = int(hat in (1, 2, 3))
    down  = int(hat in (3, 4, 5))
    left  = int(hat in (5, 6, 7))
    d[5] |= down | up << 1 | right << 2 | left << 3

    # Sticks: 12-bit values packed into 3 bytes each (center=2048)
    def scale_axis(val: int) -> int:
        return int(round((val / 255.0) * 0xFFF))

    lx12 = scale_axis(lx)
    ly12 = 0xFFF - scale_axis(ly)  # invert Y
    rx12 = scale_axis(rx)
    ry12 = 0xFFF - scale_axis(ry)  # invert Y
    ls = _pack_stick(lx12, ly12)
    rs = _pack_stick(rx12, ry12)
    d[6] = ls[0]; d[7] = ls[1]; d[8] = ls[2]
    d[9] = rs[0]; d[10] = rs[1]; d[11] = rs[2]
    return bytes(d)


_NEUTRAL = make_switchpro_report(0, 128, 128, 128, 128, 0, 0, 8)


def _build_subcmd_reply(subcmd_id: int, data: bytes = b"", timer: int = 0) -> bytearray:
    buf = bytearray(64)
    buf[0] = 0x21
    buf[1] = timer & 0xFF
    buf[2] = 0x90  # battery full + USB
    ls = _pack_stick(0x800, 0x800)
    rs = _pack_stick(0x800, 0x800)
    buf[6] = ls[0]; buf[7] = ls[1]; buf[8] = ls[2]
    buf[9] = rs[0]; buf[10] = rs[1]; buf[11] = rs[2]
    buf[13] = 0x80 | subcmd_id
    buf[14] = subcmd_id
    if data:
        buf[15:15 + len(data)] = data[:49]
    return buf


def _build_usb_reply(cmd: int) -> bytearray:
    buf = bytearray(64)
    buf[0] = 0x81
    buf[1] = cmd
    if cmd == 0x01:
        buf[2] = 0x00
        buf[3] = 0x03  # Pro Controller
        buf[4:10] = _MAC_ADDR
    return buf


def _handle_spi_read(addr: int, length: int) -> bytes:
    data = bytearray(5 + length)
    data[0] = addr & 0xFF
    data[1] = (addr >> 8) & 0xFF
    data[2] = (addr >> 16) & 0xFF
    data[3] = (addr >> 24) & 0xFF
    data[4] = length
    rdata = memoryview(data)[5:]

    if addr == 0x6020 and length >= 24:
        rdata[:24] = bytes(24)  # IMU cal (zeros = default)
    elif addr == 0x603D and length >= 9:
        rdata[:9] = _STICK_CAL_LEFT
    elif addr == 0x6046 and length >= 9:
        rdata[:9] = _STICK_CAL_RIGHT
    elif addr == 0x6050 and length >= 12:
        rdata[:3] = b"\x32\x32\x32"  # body color (dark grey)
        rdata[3:6] = b"\xFF\xFF\xFF"  # button color (white)
        rdata[6:9] = b"\x32\x32\x32"  # left grip
        rdata[9:12] = b"\x32\x32\x32"  # right grip
    elif addr in (0x8010, 0x801B, 0x8026):
        for i in range(length):
            rdata[i] = 0xFF  # user cal not set
    elif addr == 0x6080 and length >= 24:
        pass  # IMU cal zeros = default
    else:
        pass  # unknown: return zeros
    return bytes(data)


# =====
class SwitchProProcess:
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

        self.__proc = aiomulti.AioMpProcess("hid-switchpro", self.__subprocess)
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
        self.__state_q.put_nowait(make_switchpro_report(buttons, lx, ly, rx, ry, lt, rt, hat))

    def send_clear_event(self) -> None:
        self.__state_q.put_nowait(bytes(_NEUTRAL))

    def send_reset_event(self) -> None:
        self.send_clear_event()

    # =====

    def __subprocess(self) -> None:
        logger = get_logger(0)
        if functionfs is None:
            logger.error("HID-switchpro requires python-functionfs: %s", _IMPORT_ERROR)
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

        state = bytearray(_NEUTRAL)
        timer = [0]
        reply_queue: queue.Queue[bytearray] = queue.Queue()

        class _INEndpoint(functionfs.EndpointINFile):  # type: ignore
            def onComplete(self, buffer_list: Any, user_data: Any, status: int) -> Any:
                if status < 0:
                    if status == -errno.ESHUTDOWN:
                        return False
                    raise IOError(-status)
                # Check if there's a pending handshake reply to send
                try:
                    reply = reply_queue.get_nowait()
                    return [(reply,)]
                except queue.Empty:
                    pass
                # Otherwise send the current gamepad state
                timer[0] = (timer[0] + 1) & 0xFF
                state[1] = timer[0]
                return True

        class _OUTEndpoint(functionfs.EndpointOUTFile):  # type: ignore
            def onComplete(self, data: Any, status: int) -> Any:
                if status < 0:
                    return None
                d = bytes(data)
                if not d:
                    return None
                if d[0] == 0x80 and len(d) >= 2:
                    reply_queue.put(_build_usb_reply(d[1]))
                elif d[0] == 0x01 and len(d) >= 11:
                    subcmd = d[10]
                    timer[0] = (timer[0] + 1) & 0xFF
                    if subcmd == 0x02:  # device info
                        info = bytearray(12)
                        info[0] = 0x04; info[1] = 0x01  # fw 4.1
                        info[2] = 0x03  # Pro Controller
                        info[3] = 0x02
                        info[4:10] = _MAC_ADDR
                        info[10] = 0x03; info[11] = 0x01
                        reply_queue.put(_build_subcmd_reply(subcmd, bytes(info), timer[0]))
                    elif subcmd == 0x10 and len(d) >= 16:  # SPI read
                        addr = d[11] | (d[12] << 8) | (d[13] << 16) | (d[14] << 24)
                        rlen = d[15]
                        spi_data = _handle_spi_read(addr, rlen)
                        reply_queue.put(_build_subcmd_reply(subcmd, spi_data, timer[0]))
                    else:
                        reply_queue.put(_build_subcmd_reply(subcmd, b"", timer[0]))
                return None

        class _SwitchPro(functionfs.Function):  # type: ignore
            def __init__(self, path: str) -> None:
                (fs_list, hs_list, ss_list) = functionfs.getInterfaceInAllSpeeds(
                    interface={
                        "bInterfaceClass": 0x03,  # HID
                        "bInterfaceSubClass": 0x00,
                        "bInterfaceProtocol": 0x00,
                        "iInterface": 1,
                    },
                    endpoint_list=[
                        {"endpoint": {"bEndpointAddress": ch9.USB_DIR_IN,
                                      "bmAttributes": ch9.USB_ENDPOINT_XFER_INT,
                                      "wMaxPacketSize": 64, "bInterval": 8}},
                        {"endpoint": {"bEndpointAddress": ch9.USB_DIR_OUT,
                                      "bmAttributes": ch9.USB_ENDPOINT_XFER_INT,
                                      "wMaxPacketSize": 64, "bInterval": 8}},
                    ],
                    # FunctionFS needs a class-specific descriptor for HID
                    class_descriptor_list=[
                        {
                            "bDescriptorType": 0x21,  # HID
                            "data": struct.pack("<HBBH",
                                                0x0111,  # bcdHID
                                                0,       # bCountryCode
                                                1,       # bNumDescriptors
                                                len(_HID_REPORT_DESC)),  # wDescriptorLength
                        },
                    ],
                )
                super().__init__(path, fs_list=fs_list, hs_list=hs_list, ss_list=ss_list,
                                 lang_dict={0x0409: ["Pro Controller"]})

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
            with _SwitchPro(self.__ffs_path) as function:
                self.__bind()
                threading.Thread(target=drain, daemon=True).start()
                function.processEventsForever()
        except Exception:
            logger.exception("Unexpected HID-switchpro error")
        finally:
            self.__unbind()
            state_flags.update(online=False)

    def __udc_path(self) -> str:
        return os.path.join(self.__gadget_path, "UDC")

    def __bind(self) -> None:
        udc = usb.find_udc(self.__udc)
        with open(self.__udc_path(), "w") as file:
            file.write(udc)
        get_logger(0).info("HID-switchpro: bound gadget to UDC %s", udc)

    def __unbind(self) -> None:
        try:
            with open(self.__udc_path(), "w") as file:
                file.write("\n")
        except OSError as ex:
            if not tools.is_oserror(ex, errno.ENODEV):
                get_logger(0).error("HID-switchpro: can't unbind UDC: %s", tools.efmt(ex))
