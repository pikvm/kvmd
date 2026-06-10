#!/usr/bin/env python3
# Standalone Switch Pro Controller gadget test for PiKVM hardware.
#
# Builds a SINGLE-FUNCTION f_hid gadget (mzyy94's exact layout: 057e:2009,
# "Nintendo Co., Ltd." / "Pro Controller", one HID interface) and speaks the
# same protocol as kvmd's switchpro servicer. No kvmd, no browser, no
# physical controller needed: it presses A every 5 seconds by itself.
# Every packet in both directions is hex-dumped to stdout.
#
# This isolates the protocol from kvmd's composite gadget topology: if this
# works against the console and kvmd's switchpro mode doesn't, the composite
# gadget is the culprit; if this fails too, the protocol still has a gap and
# the hexdump shows exactly where.
#
# Usage (on the PiKVM, as root):
#   systemctl stop kvmd kvmd-otg
#   python3 procon-standalone.py
#   ... watch the console; Ctrl+C when done ...
#   systemctl start kvmd-otg kvmd

import os
import subprocess
import sys
import threading
import time

G = "/sys/kernel/config/usb_gadget/procon"
DEV = "/dev/hidg0"

# mzyy94's captured Pro Controller report descriptor (203 bytes)
REPORT_DESC = bytes.fromhex(
    "050115000904a1018530050105091901290a150025017501950a5500650081020509190b"
    "290e150025017501950481027501950281030b01000100a1000b300001000b310001000b"
    "320001000b35000100150027ffff0000751095048102c00b39000100150025073500463b"
    "0165147504950181020509190f2912150025017501950481027508953481030600ff8521"
    "09017508953f8103858109027508953f8103850109037508953f9183851009047508953f"
    "9183858009057508953f9183858209067508953f9183c0"
)

MAC = bytes([0x98, 0xB6, 0xE9, 0x11, 0x22, 0x33])

SPI_ROM_DATA = {
    0x60: bytes([
        0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff,
        0xff, 0xff, 0x03, 0xa0, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0x02, 0xff, 0xff, 0xff, 0xff,
        0xf0, 0xff, 0x89, 0x00, 0xf0, 0x01, 0x00, 0x40, 0x00, 0x40, 0x00, 0x40, 0xf9, 0xff, 0x06, 0x00,
        0x09, 0x00, 0xe7, 0x3b, 0xe7, 0x3b, 0xe7, 0x3b, 0xff, 0xff, 0xff, 0xff, 0xff, 0xba, 0x15, 0x62,
        0x11, 0xb8, 0x7f, 0x29, 0x06, 0x5b, 0xff, 0xe7, 0x7e, 0x0e, 0x36, 0x56, 0x9e, 0x85, 0x60, 0xff,
        0x32, 0x32, 0x32, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff,
        0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff,
        0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff,
        0x50, 0xfd, 0x00, 0x00, 0xc6, 0x0f, 0x0f, 0x30, 0x61, 0x96, 0x30, 0xf3, 0xd4, 0x14, 0x54, 0x41,
        0x15, 0x54, 0xc7, 0x79, 0x9c, 0x33, 0x36, 0x63, 0x0f, 0x30, 0x61, 0x96, 0x30, 0xf3, 0xd4, 0x14,
        0x54, 0x41, 0x15, 0x54, 0xc7, 0x79, 0x9c, 0x33, 0x36, 0x63, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff,
    ]),
    0x80: bytes([
        0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff,
        0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff,
        0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xb2, 0xa1, 0xbe, 0xff, 0x3e, 0x00, 0xf0, 0x01, 0x00, 0x40,
        0x00, 0x40, 0x00, 0x40, 0xfe, 0xff, 0xfe, 0xff, 0x08, 0x00, 0xe7, 0x3b, 0xe7, 0x3b, 0xe7, 0x3b,
    ]),
}


def sh(cmd: str) -> None:
    subprocess.check_call(cmd, shell=True)


def setup_gadget() -> None:
    if os.path.exists(G):
        teardown_gadget()
    os.makedirs(f"{G}/strings/0x409", exist_ok=True)
    os.makedirs(f"{G}/configs/c.1/strings/0x409", exist_ok=True)
    os.makedirs(f"{G}/functions/hid.usb0", exist_ok=True)

    def w(path: str, value: str) -> None:
        with open(f"{G}/{path}", "w") as f:
            f.write(value)

    w("idVendor", "0x057e")
    w("idProduct", "0x2009")
    w("bcdDevice", "0x0200")
    w("bcdUSB", "0x0200")
    w("bDeviceClass", "0x00")
    w("bDeviceSubClass", "0x00")
    w("bDeviceProtocol", "0x00")
    w("strings/0x409/serialnumber", "000000000001")
    w("strings/0x409/manufacturer", "Nintendo Co., Ltd.")
    w("strings/0x409/product", "Pro Controller")
    w("configs/c.1/strings/0x409/configuration", "Nintendo Switch Pro Controller")
    w("configs/c.1/MaxPower", "500")
    w("configs/c.1/bmAttributes", "0xa0")
    w("functions/hid.usb0/protocol", "0")
    w("functions/hid.usb0/subclass", "0")
    w("functions/hid.usb0/report_length", "64")
    with open(f"{G}/functions/hid.usb0/report_desc", "wb") as f:
        f.write(REPORT_DESC)
    if not os.path.exists(f"{G}/configs/c.1/hid.usb0"):
        os.symlink(f"{G}/functions/hid.usb0", f"{G}/configs/c.1/hid.usb0")
    udc = sorted(os.listdir("/sys/class/udc"))[0]
    with open(f"{G}/UDC", "w") as f:
        f.write(udc)
    print(f"[gadget] bound to UDC {udc}; waiting for {DEV} ...")
    for _ in range(50):
        if os.path.exists(DEV):
            return
        time.sleep(0.1)
    raise RuntimeError(f"{DEV} did not appear")


def teardown_gadget() -> None:
    subprocess.call(f"echo '' > {G}/UDC", shell=True, stderr=subprocess.DEVNULL)
    subprocess.call(f"rm -f {G}/configs/c.1/hid.usb0", shell=True)
    for d in [f"{G}/configs/c.1/strings/0x409", f"{G}/configs/c.1",
              f"{G}/functions/hid.usb0", f"{G}/strings/0x409", G]:
        subprocess.call(f"rmdir {d}", shell=True, stderr=subprocess.DEVNULL)


def hexdump(prefix: str, data: bytes) -> None:
    trimmed = data.rstrip(b"\x00")
    show = data[:max(len(trimmed), 4)]
    print(f"{prefix} {show.hex(' ')}", flush=True)


class ProCon:
    def __init__(self) -> None:
        self.fd = os.open(DEV, os.O_RDWR)
        self.timer = 0
        self.hid_enabled = False
        self.buttons3 = 0  # byte3 of the report (A bit 3)
        self.lock = threading.Lock()
        self.replies: "list[bytes]" = []
        self.cond = threading.Condition(self.lock)
        self.stop = False

    # === report builders (identical logic to kvmd switchpro.py) ===

    @staticmethod
    def _pack12(a: int, b: int) -> bytes:
        return bytes([a & 0xFF, ((a >> 8) & 0x0F) | ((b & 0x0F) << 4), (b >> 4) & 0xFF])

    def _input_body(self) -> bytes:
        stick = self._pack12(0x800, 0x800)
        return bytes([0x81, self.buttons3, 0x00, 0x00]) + stick + stick

    def input_report(self) -> bytes:
        self.timer = (self.timer + 1) & 0xFF
        buf = bytearray(64)
        buf[0] = 0x30
        buf[1] = self.timer
        buf[2:13] = self._input_body() + b"\x00"
        return bytes(buf)

    def subcmd_reply(self, subcmd: int, data: bytes = b"", nack: bool = False) -> bytes:
        self.timer = (self.timer + 1) & 0xFF
        buf = bytearray(64)
        buf[0] = 0x21
        buf[1] = self.timer
        buf[2:13] = self._input_body() + b"\x00"
        buf[13] = 0x00 if nack else (0x80 | (subcmd if data else 0))
        buf[14] = subcmd
        buf[15:15 + len(data)] = data[:49]
        return bytes(buf)

    def usb_reply(self, cmd: int) -> bytes:
        buf = bytearray(64)
        buf[0] = 0x81
        buf[1] = cmd
        if cmd == 0x01:
            buf[2] = 0x00
            buf[3] = 0x03
            buf[4:10] = MAC
        return bytes(buf)

    def queue(self, pkt: bytes) -> None:
        with self.cond:
            self.replies.append(pkt)
            self.cond.notify()

    # === protocol ===

    def handle(self, d: bytes) -> None:
        if not d:
            return
        if d[0] == 0x80 and len(d) >= 2:
            hexdump("<- host", d)
            cmd = d[1]
            if cmd == 0x05:
                self.hid_enabled = False
                print("[proto] HID stream OFF (0x80 0x05)", flush=True)
            elif cmd == 0x04:
                self.hid_enabled = True
                print("[proto] HID stream ON (0x80 0x04)", flush=True)
            else:
                self.queue(self.usb_reply(cmd))
        elif d[0] == 0x01 and len(d) >= 11:
            hexdump("<- host", d)
            subcmd = d[10]
            if subcmd == 0x02:  # device info
                info = bytearray(12)
                info[0] = 0x03; info[1] = 0x48
                info[2] = 0x03
                info[3] = 0x02
                info[4:10] = bytes(reversed(MAC))
                info[10] = 0x03; info[11] = 0x01
                self.queue(self.subcmd_reply(subcmd, bytes(info)))
            elif subcmd == 0x10 and len(d) >= 16:  # SPI read
                addr = d[11] | (d[12] << 8) | (d[13] << 16) | (d[14] << 24)
                rlen = d[15]
                page = (addr >> 8) & 0xFF
                off = addr & 0xFF
                echo = bytes([d[11], d[12], d[13], d[14], rlen])
                rom = SPI_ROM_DATA.get(page)
                if rom is None or off + rlen > len(rom):
                    print(f"[proto] SPI NACK 0x{addr:04x}[{rlen}]", flush=True)
                    self.queue(self.subcmd_reply(subcmd, echo, nack=True))
                else:
                    self.queue(self.subcmd_reply(subcmd, echo + rom[off:off + rlen]))
            elif subcmd == 0x01:  # manual pairing
                self.queue(self.subcmd_reply(subcmd, b"\x03\x01"))
            elif subcmd == 0x21:  # NFC/IR config
                self.queue(self.subcmd_reply(subcmd, bytes([0x01, 0x00, 0xFF, 0x00, 0x03, 0x00, 0x05, 0x01])))
            else:
                self.queue(self.subcmd_reply(subcmd))
        elif d[0] == 0x10:
            pass  # rumble-only, ignore
        else:
            hexdump("<- host (??)", d)

    def reader(self) -> None:
        while not self.stop:
            try:
                d = os.read(self.fd, 64)
            except OSError as ex:
                print(f"[reader] read error: {ex}", flush=True)
                time.sleep(0.2)
                continue
            self.handle(d)

    def _write(self, pkt: bytes, dump: bool = True) -> None:
        if dump:
            hexdump("-> host", pkt)
        try:
            os.write(self.fd, pkt)
        except OSError as ex:
            print(f"[writer] write error: {ex}", flush=True)
            time.sleep(0.2)

    def writer(self) -> None:
        # hello packets first, exactly like nscon
        hello1 = bytearray(64); hello1[0] = 0x81; hello1[1] = 0x03
        hello2 = bytearray(64); hello2[0] = 0x81; hello2[1] = 0x01; hello2[3] = 0x03
        for pkt in (hello1, hello2):
            self._write(bytes(pkt))
        while not self.stop:
            with self.cond:
                if not self.replies:
                    self.cond.wait(timeout=0.008)
                pkt = self.replies.pop(0) if self.replies else None
            if pkt is not None:
                self._write(pkt)
            elif self.hid_enabled:
                self._write(self.input_report(), dump=False)  # 120Hz, not dumped

    def autopress(self) -> None:
        # Press A for 250ms every 5 seconds once streaming is on
        while not self.stop:
            time.sleep(5)
            if self.hid_enabled:
                print("[input] pressing A", flush=True)
                self.buttons3 = 0x08  # A
                time.sleep(0.25)
                self.buttons3 = 0x00


def main() -> None:
    if os.geteuid() != 0:
        sys.exit("run as root")
    setup_gadget()
    pc = ProCon()
    threads = [threading.Thread(target=f, daemon=True)
               for f in (pc.reader, pc.writer, pc.autopress)]
    for t in threads:
        t.start()
    print("[main] running -- plug into the Switch; Ctrl+C to exit", flush=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        pc.stop = True
        time.sleep(0.3)
        os.close(pc.fd)
        teardown_gadget()
        print("[main] gadget removed; restart kvmd-otg + kvmd to restore PiKVM")


if __name__ == "__main__":
    main()
