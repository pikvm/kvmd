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


# Minimal pure-Python FunctionFS implementation for PiKVM gamepad emulation.
# Replaces python-functionfs + libaio + ioctl_opt with zero external deps.
# Only uses Python stdlib: os, struct, errno, threading.
#
# Based on the Linux kernel FunctionFS v2 protocol:
#   linux/usb/functionfs.h
#   linux/usb/ch9.h


import errno
import os
import struct
import threading


# ===== USB ch9 constants =====
USB_DIR_OUT = 0x00
USB_DIR_IN = 0x80
USB_ENDPOINT_XFER_INT = 0x03

USB_TYPE_STANDARD = 0x00
USB_TYPE_CLASS = 0x20
USB_TYPE_MASK = 0x60
USB_RECIP_INTERFACE = 0x01
USB_REQ_GET_DESCRIPTOR = 0x06

HID_DT_REPORT = 0x22


# ===== FunctionFS v2 wire format =====
_DESCS_MAGIC_V2 = 3
_STRINGS_MAGIC = 2
_HAS_FS_DESC = 1
_HAS_HS_DESC = 2

# Event types
ENABLE = 2
DISABLE = 3
SETUP = 4
_EVENT_SIZE = 12  # sizeof(usb_functionfs_event)


# ===== UDC binding =====

def wait_bind_udc(gadget_path, udc_name, stop_event, logger=None):
    # A gadget binds to the UDC only after every FunctionFS function in it has
    # its descriptors written, and only one servicer's write can succeed, so:
    # retry while sibling gamepad slots come up, and treat EBUSY with the
    # gadget already bound as a sibling having won the race.
    udc_path = os.path.join(gadget_path, "UDC")
    attempts = 0
    while not stop_event.is_set():
        try:
            with open(udc_path, "w") as file:
                file.write(udc_name)
            if logger:
                logger.info("FFS: bound gadget to UDC %s", udc_name)
            return True
        except OSError as ex:
            if ex.errno == errno.EBUSY:
                try:
                    with open(udc_path) as file:
                        if file.read().strip():
                            return True
                except OSError:
                    pass
            attempts += 1
            if logger and attempts % 20 == 0:
                logger.warning("FFS: still waiting to bind UDC (%s): %s", udc_name, ex)
        stop_event.wait(0.25)
    return False


# ===== Descriptor helpers =====

def _pack_ep(addr, attrs, max_pkt, interval):
    return struct.pack("<BBBBHB", 7, 0x05, addr, attrs, max_pkt, interval)


def getInterfaceInAllSpeeds(interface, endpoint_list, class_descriptor_list=()):
    num_eps = len(endpoint_list)
    iface = struct.pack(
        "<BBBBBBBBB", 9, 0x04, 0, 0, num_eps,
        interface.get("bInterfaceClass", 0),
        interface.get("bInterfaceSubClass", 0),
        interface.get("bInterfaceProtocol", 0),
        interface.get("iInterface", 0),
    )

    class_descs = b""
    class_count = 0
    for cd in (class_descriptor_list or ()):
        data = cd["data"]
        class_descs += struct.pack("<BB", 2 + len(data), cd["bDescriptorType"]) + data
        class_count += 1

    eps = b""
    for i, ep in enumerate(endpoint_list, 1):
        e = ep["endpoint"]
        addr = e["bEndpointAddress"]
        if (addr & 0x7F) == 0:
            addr = i | (addr & USB_DIR_IN)
        eps += _pack_ep(addr, e["bmAttributes"], e["wMaxPacketSize"], e["bInterval"])

    raw = iface + class_descs + eps
    count = 1 + class_count + num_eps
    return (raw, count), (raw, count), None


# ===== Endpoint classes =====

class EndpointINFile:
    def __init__(self, fd):
        self._fd = fd
        self._running = False

    def submit(self, buffer_list, user_data=None):
        if self._running:
            return
        self._buf = self._unwrap(buffer_list)
        self._udata = user_data
        self._running = True
        t = threading.Thread(target=self._loop, daemon=True)
        t.start()

    @staticmethod
    def _unwrap(x):
        while not isinstance(x, (bytes, bytearray, memoryview)):
            x = x[0]
        return x

    def _loop(self):
        while self._running:
            try:
                n = os.write(self._fd, bytes(self._buf))
            except OSError as exc:
                self.onComplete(None, self._udata, -exc.errno)
                break
            result = self.onComplete((self._buf,), self._udata, n)
            if result is False:
                break
            elif result is True:
                continue
            elif result:
                self._buf = self._unwrap(result)
        # Allow a later submit() to restart the loop (e.g. after a USB
        # reset shuts the endpoint down and the host re-enables us).
        self._running = False

    def stop(self):
        self._running = False

    def onComplete(self, buffer_list, user_data, status):
        return False


class EndpointOUTFile:
    def __init__(self, fd, max_pkt=64):
        self._fd = fd
        self._max_pkt = max_pkt
        self._running = False

    def start(self):
        if self._running:
            return
        self._running = True
        t = threading.Thread(target=self._loop, daemon=True)
        t.start()

    def _loop(self):
        while self._running:
            try:
                data = os.read(self._fd, self._max_pkt)
            except OSError:
                break
            if data:
                self.onComplete(data, 0)

    def stop(self):
        self._running = False

    def onComplete(self, data, status):
        pass


# ===== Function (FunctionFS context manager + event loop) =====

class Function:
    def __init__(self, path, fs_list, hs_list, ss_list=None,
                 lang_dict=None, hid_report_desc=None):
        self._path = path
        self._fs_raw, self._fs_count = fs_list
        self._hs_raw, self._hs_count = hs_list
        self._lang_dict = dict(lang_dict) if lang_dict else {}
        self._hid_report_desc = hid_report_desc
        self._ep0_fd = -1
        self._ep_fds = []
        self._endpoints = {}
        self._open = False

    def __enter__(self):
        ep0_path = os.path.join(self._path, "ep0")
        self._ep0_fd = os.open(ep0_path, os.O_RDWR)
        try:
            os.write(self._ep0_fd, self._build_descs())
            os.write(self._ep0_fd, self._build_strings())
            for i, (addr, _attrs, max_pkt) in enumerate(self._parse_eps(), 1):
                ep_path = os.path.join(self._path, "ep%d" % i)
                fd = os.open(ep_path, os.O_RDWR)
                self._ep_fds.append(fd)
                is_in = bool(addr & USB_DIR_IN)
                klass = self.getEndpointClass(is_in, None)
                self._endpoints[i] = klass(fd) if is_in else klass(fd, max_pkt)
        except Exception:
            self._teardown()
            raise
        self._open = True
        return self

    def __exit__(self, *args):
        self._open = False
        self._teardown()

    def _teardown(self):
        for ep in self._endpoints.values():
            ep.stop()
        self._endpoints.clear()
        for fd in self._ep_fds:
            try:
                os.close(fd)
            except OSError:
                pass
        self._ep_fds.clear()
        if self._ep0_fd >= 0:
            try:
                os.close(self._ep0_fd)
            except OSError:
                pass
            self._ep0_fd = -1

    # ----- public API (matches python-functionfs) -----

    def getEndpointClass(self, is_in, descriptor):
        return EndpointINFile if is_in else EndpointOUTFile

    def getEndpoint(self, index):
        return self._endpoints[index]

    def onEnable(self):
        for ep in self._endpoints.values():
            if isinstance(ep, EndpointOUTFile):
                ep.start()

    def onDisable(self):
        pass

    def onSetup(self, request_type, request, value, index, length):
        if (self._hid_report_desc is not None
                and (request_type & USB_DIR_IN)
                and (request_type & USB_TYPE_MASK) == USB_TYPE_STANDARD
                and request == USB_REQ_GET_DESCRIPTOR
                and (value >> 8) == HID_DT_REPORT):
            os.write(self._ep0_fd, self._hid_report_desc[:length])
            return
        self._stall(request_type)

    def processEventsForever(self):
        while self._open:
            try:
                data = os.read(self._ep0_fd, 4 * _EVENT_SIZE)
            except OSError as exc:
                if exc.errno == errno.EINTR:
                    continue
                break
            if not data:
                break
            off = 0
            while off + _EVENT_SIZE <= len(data):
                evt_type = data[off + 8]
                if evt_type == ENABLE:
                    self.onEnable()
                elif evt_type == DISABLE:
                    self.onDisable()
                elif evt_type == SETUP:
                    rt, rq, val, idx, ln = struct.unpack_from("<BBHHH", data, off)
                    self.onSetup(rt, rq, val, idx, ln)
                off += _EVENT_SIZE

    # ----- private -----

    def _stall(self, request_type):
        try:
            if request_type & USB_DIR_IN:
                os.read(self._ep0_fd, 0)
            else:
                os.write(self._ep0_fd, b"")
        except OSError:
            pass

    def _build_descs(self):
        flags = _HAS_FS_DESC | _HAS_HS_DESC
        counts = struct.pack("<II", self._fs_count, self._hs_count)
        body = self._fs_raw + self._hs_raw
        total_len = 12 + len(counts) + len(body)
        header = struct.pack("<III", _DESCS_MAGIC_V2, total_len, flags)
        return header + counts + body

    def _build_strings(self):
        if not self._lang_dict:
            return struct.pack("<IIII", _STRINGS_MAGIC, 16, 0, 0)
        str_count = len(next(iter(self._lang_dict.values())))
        lang_data = b""
        for lang_id, strings in self._lang_dict.items():
            lang_data += struct.pack("<H", lang_id)
            for s in strings:
                lang_data += s.encode("utf-8") + b"\x00"
        total = 16 + len(lang_data)
        return struct.pack("<IIII", _STRINGS_MAGIC, total, str_count, len(self._lang_dict)) + lang_data

    def _parse_eps(self):
        eps = []
        i = 0
        raw = self._fs_raw
        while i + 1 < len(raw):
            bl = raw[i]
            dt = raw[i + 1]
            if bl < 2:
                break
            if dt == 0x05 and bl >= 7:
                addr = raw[i + 2]
                attrs = raw[i + 3]
                max_pkt = raw[i + 4] | (raw[i + 5] << 8)
                eps.append((addr, attrs, max_pkt))
            i += bl
        return eps
