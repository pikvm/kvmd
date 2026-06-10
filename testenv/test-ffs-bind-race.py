# Two-slot FFS bind race test: replicates the tester's topology (two gamepad
# FunctionFS instances in one gadget) and verifies wait_bind_udc coordination.
# Run as root inside the Lima VM with dummy_hcd loaded. ffs.py must sit next to it.
import multiprocessing
import os
import struct
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ffs  # noqa: E402

G = "/sys/kernel/config/usb_gadget/bindtest"
TEST_UID = 501

REPORT_DESC = bytes([0x05, 0x01, 0x09, 0x05, 0xA1, 0x01, 0x15, 0x00, 0x26, 0xFF, 0x00,
                     0x75, 0x08, 0x95, 0x40, 0x09, 0x01, 0x81, 0x02,
                     0x95, 0x40, 0x09, 0x02, 0x91, 0x02, 0xC0])


def setup_gadget():
    for d in [G, f"{G}/strings/0x409", f"{G}/configs/c.1", f"{G}/configs/c.1/strings/0x409",
              f"{G}/functions/ffs.bt0", f"{G}/functions/ffs.bt1"]:
        os.makedirs(d, exist_ok=True)
    open(f"{G}/idVendor", "w").write("0x1d6b")
    open(f"{G}/idProduct", "w").write("0x0104")
    open(f"{G}/strings/0x409/manufacturer", "w").write("test")
    open(f"{G}/strings/0x409/product", "w").write("bindtest")
    open(f"{G}/configs/c.1/strings/0x409/configuration", "w").write("c1")
    for inst in ["bt0", "bt1"]:
        os.symlink(f"{G}/functions/ffs.{inst}", f"{G}/configs/c.1/ffs.{inst}")
        mnt = f"/tmp/ffs-{inst}"
        os.makedirs(mnt, exist_ok=True)
        subprocess.check_call(["mount", "-t", "functionfs",
                               "-o", f"uid={TEST_UID},gid={TEST_UID}", inst, mnt])
    # kvmd-otg chowns the UDC attribute to the kvmd user (apps/otg line ~553)
    os.chown(f"{G}/UDC", TEST_UID, TEST_UID)


def teardown():
    subprocess.call(["bash", "-c", f"echo '' > {G}/UDC"], stderr=subprocess.DEVNULL)
    for inst in ["bt0", "bt1"]:
        subprocess.call(["umount", f"/tmp/ffs-{inst}"], stderr=subprocess.DEVNULL)
        subprocess.call(["rmdir", f"/tmp/ffs-{inst}"], stderr=subprocess.DEVNULL)
        subprocess.call(["rm", f"{G}/configs/c.1/ffs.{inst}"], stderr=subprocess.DEVNULL)
        subprocess.call(["rmdir", f"{G}/functions/ffs.{inst}"], stderr=subprocess.DEVNULL)
    subprocess.call(["bash", "-c",
                     f"rmdir {G}/configs/c.1/strings/0x409 {G}/configs/c.1 {G}/strings/0x409 {G}"],
                    stderr=subprocess.DEVNULL)


class _Logger:
    def __init__(self, slot):
        self.slot = slot

    def info(self, msg, *args):
        print(f"[slot{self.slot}] INFO " + (msg % args), flush=True)

    def warning(self, msg, *args):
        print(f"[slot{self.slot}] WARN " + (msg % args), flush=True)


class _TestFunc(ffs.Function):
    def __init__(self, path):
        (fs_list, hs_list, ss_list) = ffs.getInterfaceInAllSpeeds(
            interface={"bInterfaceClass": 0x03, "bInterfaceSubClass": 0x00,
                       "bInterfaceProtocol": 0x00, "iInterface": 1},
            endpoint_list=[
                {"endpoint": {"bEndpointAddress": ffs.USB_DIR_IN,
                              "bmAttributes": ffs.USB_ENDPOINT_XFER_INT,
                              "wMaxPacketSize": 64, "bInterval": 8}},
                {"endpoint": {"bEndpointAddress": ffs.USB_DIR_OUT,
                              "bmAttributes": ffs.USB_ENDPOINT_XFER_INT,
                              "wMaxPacketSize": 64, "bInterval": 8}},
            ],
            class_descriptor_list=[
                {"bDescriptorType": 0x21,
                 "data": struct.pack("<HBBBH", 0x0111, 0, 1, 0x22, len(REPORT_DESC))},
            ],
        )
        super().__init__(path, fs_list=fs_list, hs_list=hs_list, ss_list=ss_list,
                         lang_dict={0x0409: ["bindtest"]}, hid_report_desc=REPORT_DESC)

    def getEndpointClass(self, is_in, descriptor):
        return ffs.EndpointINFile if is_in else ffs.EndpointOUTFile


def servicer(slot, delay, udc_name, result_q):
    os.setgid(TEST_UID)
    os.setuid(TEST_UID)  # run as non-root like the kvmd daemon
    stop_event = multiprocessing.Event()
    time.sleep(delay)  # stagger descriptor writes: slot1 comes up late
    try:
        with _TestFunc(f"/tmp/ffs-bt{slot}"):
            ok = ffs.wait_bind_udc(G, udc_name, stop_event, _Logger(slot))
            result_q.put((slot, ok))
            time.sleep(8)  # hold the function open while parent checks the binding
    except Exception as ex:  # noqa: BLE001
        result_q.put((slot, f"EXC {ex!r}"))


def main():
    udc_name = os.listdir("/sys/class/udc")[0]
    setup_gadget()
    try:
        q = multiprocessing.Queue()
        p0 = multiprocessing.Process(target=servicer, args=(0, 0.0, udc_name, q))
        p1 = multiprocessing.Process(target=servicer, args=(1, 3.0, udc_name, q))
        p0.start()
        p1.start()
        results = sorted([q.get(timeout=30), q.get(timeout=30)])
        bound = open(f"{G}/UDC").read().strip()  # read while servicers still hold ep0
        p0.join(15)
        p1.join(15)
        print(f"results={results} udc_file={bound!r}")
        assert results == [(0, True), (1, True)], f"bad results: {results}"
        assert bound == udc_name, f"gadget not bound: {bound!r}"
        print("PASS: both slots report bound, gadget bound exactly once, no EBUSY crash")
    finally:
        teardown()


if __name__ == "__main__":
    main()
