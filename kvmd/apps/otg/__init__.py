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


import os
import re
import shutil
import json
import math
import errno
import time
import argparse

from os.path import join  # pylint: disable=ungrouped-imports

from typing import Final

from ...logging import get_logger

from ...yamlconf import Section

from ...validators import ValidatorError

from ... import usb

from .. import init

from .hid import Hid
from .hid.keyboard import make_keyboard_hid
from .hid.mouse import make_mouse_hid


# =====
def _mkdir(path: str) -> None:
    get_logger().info("MKDIR --- %s", path)
    os.mkdir(path)


def _chown(path: str, user: str) -> None:
    get_logger().info("CHOWN --- %s - %s", user, path)
    shutil.chown(path, user)


def _symlink(src: str, dest: str) -> None:
    get_logger().info("SYMLINK - %s --> %s", dest, src)
    os.symlink(src, dest)


def _rmdir(path: str) -> None:
    get_logger().info("RMDIR --- %s", path)
    if os.path.isdir(path):
        os.rmdir(path)


def _unlink(path: str, optional: bool=False) -> None:
    logger = get_logger()
    if optional and not os.access(path, os.F_OK):
        logger.info("RM ------ [SKIPPED] %s", path)
        return
    logger.info("RM ------ %s", path)
    os.unlink(path)


def _write(path: str, value: (str | bytes | int), optional: bool=False) -> None:
    logger = get_logger()
    if optional and not os.access(path, os.F_OK):
        logger.info("WRITE --- [SKIPPED] %s", path)
        return
    logger.info("WRITE --- %s", path)
    is_bin = isinstance(value, bytes)
    with open(path, ("wb" if is_bin else "w")) as file:
        file.write(value if is_bin else str(value))


def _write_bytes(path: str, data: bytes) -> None:
    get_logger().info("WRITE --- %s", path)
    with open(path, "wb") as file:
        file.write(data)


# =====
class _GadgetConfig:
    # https://www.kernel.org/doc/Documentation/usb/gadget_configfs.txt

    def __init__(
        self,
        gadget_path: str,
        profile_path: str,
        meta_path: str,
        eps_max: int,
    ) -> None:

        self.__gadget_path:  Final[str] = gadget_path
        self.__profile_path: Final[str] = profile_path
        self.__meta_path:    Final[str] = meta_path
        self.__eps_max:      Final[int] = eps_max

        self.__eps_used = 0
        self.__hid_instance = 0
        self.__msd_instance = 0

        _mkdir(meta_path)

    def add_camera(
        self,
        starter: list[str],
        start: bool,
        ct_mask: int,
        pu_mask: int,
    ) -> None:

        """
        Camera Terminal						Processing Unit
        ------------------------------------------------------------------------
        D0:  Scanning Mode					D0: Brightness
        D1:  Auto-Exposure Mode				D1: Contrast
        D2:  Auto-Exposure Priority			D2: Hue
        D3:  Exposure Time (Absolute)		D3: Saturation
        D4:  Exposure Time (Relative)		D4: Sharpness
        D5:  Focus (Absolute)				D5: Gamma
        D6:  Focus (Relative)				D6: White Balance Temperature
        D7:  Iris (Absolute)				D7: White Balance Component
        D8:  Iris (Relative)				D8: Backlight Compensation
        D9:  Zoom (Absolute)				D9: Gain
        D10: Zoom (Relative)				D10: Power Line Frequency
        D11: PanTilt (Absolute)				D11: Hue, Auto
        D12: PanTilt (Relative)				D12: White Balance Temperature, Auto
        D13: Roll (Absolute)				D13: White Balance Component, Auto
        D14: Roll (Relative)				D14: Digital Multiplier
        D15:								D15: Digital Multiplier Limit
        D16:								D16: Analog Video Standard
        D17: Focus, Auto					D17: Analog Video Lock Status
        D18: Privacy						D18: Contrast, Auto
        D19: Focus, Simple
        D20: Window
        D21: Region of Interest
        """

        func = "uvc.usb0"
        func_path = self.__create_function(func)

        _mkdir(join(func_path, "streaming/mjpeg/m"))
        for (width, height, framerates) in [  # TODO: Make it configurable
            # (1920, 1080, [30]),
            # (1280, 720,  [30]),
            # (800,  600,  [30]),
            (640,  480,  [30]),
            # (640,  360,  [30]),
            # (320,  240,  [30]),
            # (320,  180,  [30]),
        ]:
            if framerates:
                fmt_path = join(func_path, f"streaming/mjpeg/m/{height}p")
                _mkdir(fmt_path)
                _write(join(fmt_path, "wWidth"), width)
                _write(join(fmt_path, "wHeight"), height)
                _write(join(fmt_path, "dwMaxVideoFrameBufferSize"), width * height)  # Should be fine
                _write(join(fmt_path, "dwFrameInterval"), "\n".join(
                    str(math.floor(1 / fps * 10_000_000))  # 30 -> 333333, 100ns units
                    for fps in framerates
                ))

        path = join(func_path, "streaming/header/h")
        _mkdir(path)
        _symlink(join(func_path, "streaming/mjpeg/m"), join(func_path, "streaming/header/h/m"))
        for speed in os.listdir(join(func_path, "streaming/class")):
            _symlink(path, join(func_path, f"streaming/class/{speed}/h"))

        path = join(func_path, "control/header/h")
        _mkdir(path)
        for speed in os.listdir(join(func_path, "control/class")):
            _symlink(path, join(func_path, f"control/class/{speed}/h"))

        for (mask, mask_len, path) in [
            (ct_mask, 3, "control/terminal/camera/default/bmControls"),
            (pu_mask, 2, "control/processing/default/bmControls"),
        ]:
            _write(join(func_path, path), "\n".join(map(str, mask.to_bytes(mask_len, "big"))))

        _write(join(func_path, "streaming_maxpacket"), 2048)  # Maximum for USB 2.0

        self.__setup_function(func, "Camera", 2, starter, start)  # TODO: Check eps number

    def add_audio(self, starter: list[str], start: bool, speakers: bool, mic: bool) -> None:
        assert speakers or mic
        desc: list[str] = []
        func = "uac2.usb0"
        func_path = self.__create_function(func)
        if speakers:
            _write(join(func_path, "c_chmask"), 0b11)
            _write(join(func_path, "c_srate"), 48000)
            _write(join(func_path, "c_ssize"), 2)
            desc.append("Speakers")
        else:
            _write(join(func_path, "c_chmask"), 0)
        if mic:
            _write(join(func_path, "p_chmask"), 0b11)
            _write(join(func_path, "p_srate"), 48000)
            _write(join(func_path, "p_ssize"), 2)
            desc.append("Microphone")
        else:
            _write(join(func_path, "p_chmask"), 0)
        self.__setup_function(func, "+".join(desc), len(desc) + 1, starter, start)

    def add_serial(self, starter: list[str], start: bool) -> None:
        func = "acm.usb0"
        self.__create_function(func)
        self.__setup_function(func, "Serial Port", 3, starter, start)

    def add_ethernet(self, starter: list[str], start: bool, driver: str, host_mac: str, kvm_mac: str) -> None:
        if host_mac and kvm_mac and host_mac == kvm_mac:
            get_logger().error("Ethernet will not be created: host_mac should not be equal to kvm_mac")
        real_driver = driver
        if driver == "rndis5":
            real_driver = "rndis"
        func = f"{real_driver}.usb0"
        func_path = self.__create_function(func)
        if host_mac:
            _write(join(func_path, "host_addr"), host_mac)
        if kvm_mac:
            _write(join(func_path, "dev_addr"), kvm_mac)
        if driver in ["ncm", "rndis"]:
            _write(join(self.__gadget_path, "os_desc/use"), "1")
            _write(join(self.__gadget_path, "os_desc/b_vendor_code"), "0xCD")
            _write(join(self.__gadget_path, "os_desc/qw_sign"), "MSFT100")
            if driver == "ncm":
                _write(join(func_path, "os_desc/interface.ncm/compatible_id"), "WINNCM")
            elif driver == "rndis":
                # On Windows 7 and later, the RNDIS 5.1 driver would be used by default,
                # but it does not work very well. The RNDIS 6.0 driver works better.
                # In order to get this driver to load automatically, we have to use
                # a Microsoft-specific extension of USB.
                _write(join(func_path, "os_desc/interface.rndis/compatible_id"), "RNDIS")
                _write(join(func_path, "os_desc/interface.rndis/sub_compatible_id"), "5162001")
            _symlink(self.__profile_path, join(self.__gadget_path, "os_desc", usb.G_PROFILE_NAME))
        self.__setup_function(func, "Ethernet", 3, starter, start)

    def add_keyboard(self, starter: list[str], start: bool, remote_wakeup: bool) -> None:
        self.__add_hid("Keyboard", starter, start, remote_wakeup, make_keyboard_hid())

    def add_mouse(self, starter: list[str], start: bool, remote_wakeup: bool, absolute: bool, horizontal_wheel: bool) -> None:
        desc = ("Absolute" if absolute else "Relative") + " Mouse"
        self.__add_hid(desc, starter, start, remote_wakeup, make_mouse_hid(absolute, horizontal_wheel))

    def __add_hid(self, desc: str, starter: list[str], start: bool, remote_wakeup: bool, hid: Hid) -> None:
        func = f"hid.usb{self.__hid_instance}"
        func_path = self.__create_function(func)
        _write(join(func_path, "no_out_endpoint"), "1", optional=True)
        if remote_wakeup:
            _write(join(func_path, "wakeup_on_write"), "1", optional=True)
        _write(join(func_path, "protocol"), hid.protocol)
        _write(join(func_path, "subclass"), hid.subclass)
        _write(join(func_path, "report_length"), hid.report_length)
        _write_bytes(join(func_path, "report_desc"), hid.report_descriptor)
        self.__setup_function(func, desc, 1, starter, start)
        self.__hid_instance += 1

    def add_msd(  # pylint: disable=too-many-arguments,too-many-locals
        self,
        starter: list[str],
        start: bool,
        user: str,
        image_path: str,
        stall: bool,
        cdrom: bool,
        rw: bool,
        removable: bool,
        fua: bool,
        inquiry_string_cdrom: str,
        inquiry_string_flash: str,
    ) -> None:

        func = f"mass_storage.usb{self.__msd_instance}"
        func_path = self.__create_function(func)
        _write(join(func_path, "stall"), int(stall))  # https://github.com/raspberrypi/linux/issues/7452
        if image_path:
            _write(join(func_path, "lun.0/file"), image_path)
        _write(join(func_path, "lun.0/cdrom"), int(cdrom))
        _write(join(func_path, "lun.0/ro"), int(not rw))
        _write(join(func_path, "lun.0/removable"), int(removable))
        _write(join(func_path, "lun.0/nofua"), int(not fua))
        _write(join(func_path, "lun.0/inquiry_string_cdrom"), inquiry_string_cdrom)
        _write(join(func_path, "lun.0/inquiry_string"), inquiry_string_flash)
        if user != "root":
            _chown(join(func_path, "lun.0/cdrom"), user)
            _chown(join(func_path, "lun.0/ro"), user)
            _chown(join(func_path, "lun.0/file"), user)
            _chown(join(func_path, "lun.0/forced_eject"), user)
        desc = ("Mass Storage Drive" if self.__msd_instance == 0 else f"Extra Drive #{self.__msd_instance}")
        # Endpoints number depends on transport_type but we can consider that this is 2
        # because transport_type is always USB_PR_BULK by default if CONFIG_USB_FILE_STORAGE_TEST
        # is not defined. See drivers/usb/gadget/function/storage_common.c
        self.__setup_function(func, desc, 2, starter, start)
        self.__msd_instance += 1

    def __create_function(self, func: str) -> str:
        func_path = join(self.__gadget_path, "functions", func)
        _mkdir(func_path)
        return func_path

    def __setup_function(self, func: str, desc: str, eps: int, starter: list[str], start: bool) -> None:
        self.__create_meta(func, desc, eps, starter)
        if start:
            self.__start_function(func, eps)

    def __start_function(self, func: str, eps: int) -> None:
        func_path = join(self.__gadget_path, "functions", func)
        if self.__eps_max - self.__eps_used >= eps:
            _symlink(func_path, join(self.__profile_path, func))
            self.__eps_used += eps
        else:
            get_logger().info("Function %r not be started: No available endpoints", func)

    def __create_meta(self, func: str, desc: str, eps: int, starter: list[str]) -> None:
        _write(join(self.__meta_path, f"{func}@meta.json"), json.dumps({
            "function": func,
            "description": desc,
            "endpoints": eps,
            "starter": ["otg", "devices", *starter, "start"],
        }))


def _check_config(config: Section) -> bool:
    cod = config.otg.devices
    return (
        cod.camera.enabled
        or not (cod.audio.enabled and (cod.audio.speakers.enabled or cod.audio.mic.enabled))
        or not cod.serial.enabled
        or not cod.ethernet.enabled
        or config.kvmd.hid.type != "otg"
        or config.kvmd.msd.type != "otg"
    )


def _cmd_start(config: Section) -> None:  # pylint: disable=too-many-statements,too-many-branches
    logger = get_logger()

    if not _check_config(config):
        logger.info("Nothing to do")
        return

    gadget_path = usb.get_gadget_path()
    if os.path.exists(gadget_path):
        logger.info("Already started/prepared, nothing to do")
        return

    udc = usb.find_udc(config.otg.udc)
    logger.info("Using UDC %s", udc)

    logger.info("Creating the gadget: %s ...", gadget_path)
    _mkdir(gadget_path)

    _write(join(gadget_path, "idVendor"), f"0x{config.otg.vendor_id:04X}")
    _write(join(gadget_path, "idProduct"), f"0x{config.otg.product_id:04X}")
    _write(join(gadget_path, "bcdUSB"), f"0x{config.otg.usb_version:04X}")

    # bcdDevice should be incremented any time there are breaking changes
    # to this script so that the host OS sees it as a new device
    # and re-enumerates everything rather than relying on cached values.
    device_version = config.otg.device_version
    if device_version < 0:
        device_version = 0x0100
        if config.otg.devices.ethernet.enabled:
            if config.otg.devices.ethernet.driver == "ncm":
                device_version = 0x0102
            elif config.otg.devices.ethernet.driver == "rndis":
                device_version = 0x0101
    _write(join(gadget_path, "bcdDevice"), f"0x{device_version:04X}")

    lang_path = join(gadget_path, "strings/0x409")
    _mkdir(lang_path)
    _write(join(lang_path, "manufacturer"), config.otg.manufacturer)
    _write(join(lang_path, "product"), config.otg.product)
    if config.otg.serial is not None:
        _write(join(lang_path, "serialnumber"), config.otg.serial)

    profile_path = join(gadget_path, usb.G_PROFILE)
    _mkdir(profile_path)
    if config.otg.config is not None:
        _mkdir(join(profile_path, "strings/0x409"))
        _write(join(profile_path, "strings/0x409/configuration"), config.otg.config)
    _write(join(profile_path, "MaxPower"), config.otg.max_power)
    if config.otg.remote_wakeup:
        # XXX: Should we use MaxPower=100 with Remote Wakeup?
        _write(join(profile_path, "bmAttributes"), "0xA0")

    gc = _GadgetConfig(gadget_path, profile_path, config.otg.meta, config.otg.endpoints)
    cod = config.otg.devices

    if config.kvmd.hid.type == "otg":
        logger.info("===== HID-Keyboard =====")
        gc.add_keyboard(["hid", "keyboard"], cod.hid.keyboard.start, config.otg.remote_wakeup)
        logger.info("===== HID-Mouse =====")
        ckhm = config.kvmd.hid.mouse
        gc.add_mouse(["hid", "mouse"], cod.hid.mouse.start,
                     config.otg.remote_wakeup, ckhm.absolute, ckhm.horizontal_wheel)
        if config.kvmd.hid.mouse_alt.device:
            logger.info("===== HID-Mouse-Alt =====")
            gc.add_mouse(["hid", "mouse_alt"], cod.hid.mouse_alt.start,
                         config.otg.remote_wakeup, (not ckhm.absolute), ckhm.horizontal_wheel)

    def make_inquiry_string(isc: Section) -> str:
        kwargs = isc._unpack()
        if kwargs["vendor"] is None:
            kwargs["vendor"] = config.otg.manufacturer
        return usb.make_inquiry_string(**kwargs)

    if config.kvmd.msd.type == "otg":
        logger.info("===== MSD =====")
        gc.add_msd(
            starter=["msd"],
            start=cod.msd.start,
            user=config.otg.user,
            inquiry_string_cdrom=make_inquiry_string(cod.msd.default.inquiry_string.cdrom),
            inquiry_string_flash=make_inquiry_string(cod.msd.default.inquiry_string.flash),
            **cod.msd.default._unpack(ignore="inquiry_string"),
        )
        if cod.drives.enabled:
            for count in range(cod.drives.count):
                logger.info("===== MSD Extra: %d =====", count + 1)
                gc.add_msd(
                    starter=["drives"],
                    start=cod.drives.start,
                    user="root",
                    image_path="",
                    inquiry_string_cdrom=make_inquiry_string(cod.drives.default.inquiry_string.cdrom),
                    inquiry_string_flash=make_inquiry_string(cod.drives.default.inquiry_string.flash),
                    **cod.drives.default._unpack(ignore="inquiry_string"),
                )

    if cod.ethernet.enabled:
        logger.info("===== Ethernet =====")
        gc.add_ethernet(["ethernet"], **cod.ethernet._unpack(ignore=["enabled"]))

    if cod.serial.enabled:
        logger.info("===== Serial =====")
        gc.add_serial(["serial"], cod.serial.start)

    if cod.audio.enabled and (cod.audio.speakers.enabled or cod.audio.mic.enabled):
        logger.info("===== Audio =====")
        gc.add_audio(["audio"], cod.audio.start, cod.audio.speakers.enabled, cod.audio.mic.enabled)

    if cod.camera.enabled:
        logger.info("===== Camera =====")
        gc.add_camera(["camera"], cod.camera.start, cod.camera.controls.ct_mask, cod.camera.controls.pu_mask)

    logger.info("===== Preparing complete =====")

    logger.info("Enabling the gadget ...")
    _write(join(gadget_path, "UDC"), udc)
    time.sleep(config.otg.init_delay)
    _chown(join(gadget_path, "UDC"), config.otg.user)
    _chown(profile_path, config.otg.user)

    logger.info("Ready to work")


# =====
def _cmd_stop(config: Section) -> None:  # pylint: disable=too-many-branches
    logger = get_logger()

    gadget_path = usb.get_gadget_path()
    if not os.path.exists(gadget_path):
        logger.info("Already stopped, nothing to do")
        return

    logger.info("Disabling the gadget ...")
    try:
        _write(join(gadget_path, "UDC"), "\n")
    except OSError as ex:
        if ex.errno != errno.ENODEV:
            raise

    _unlink(join(gadget_path, "os_desc", usb.G_PROFILE_NAME), optional=True)

    profile_path = join(gadget_path, usb.G_PROFILE)
    for func in os.listdir(profile_path):
        if re.search(r"\.usb\d+$", func):
            _unlink(join(profile_path, func))
    _rmdir(join(profile_path, "strings/0x409"))
    _rmdir(profile_path)

    funcs_path = join(gadget_path, "functions")
    for func in os.listdir(funcs_path):
        if re.search(r"\.usb\d+$", func):
            logger.info("===== %s =====", func)
            if func.startswith("uvc."):
                uvc_path = join(funcs_path, func)

                for speed in os.listdir(join(uvc_path, "control/class")):
                    _unlink(join(uvc_path, "control/class", speed, "h"))
                _rmdir(join(uvc_path, "control/header/h"))

                for speed in os.listdir(join(uvc_path, "streaming/class")):
                    _unlink(join(uvc_path, "streaming/class", speed, "h"))
                _unlink(join(uvc_path, "streaming/header/h/m"))
                _rmdir(join(uvc_path, "streaming/header/h"))

                for res in os.listdir(join(uvc_path, "streaming/mjpeg/m")):
                    res_path = join(uvc_path, "streaming/mjpeg/m", res)
                    if os.path.isdir(res_path):
                        _rmdir(res_path)
                _rmdir(join(uvc_path, "streaming/mjpeg/m"))

            _rmdir(join(funcs_path, func))

    _rmdir(join(gadget_path, "strings/0x409"))
    _rmdir(gadget_path)

    for meta in os.listdir(config.otg.meta):
        _unlink(join(config.otg.meta, meta))
    _rmdir(config.otg.meta)

    logger.info("Successfully stopped")


# =====
def main() -> None:
    ia = init(
        add_help=False,
        load_hid=True,
        load_atx=True,
        load_msd=True,
    )
    parser = argparse.ArgumentParser(
        prog="kvmd-otg",
        description="Control KVMD OTG device",
        parents=[ia.parser],
    )
    parser.set_defaults(cmd=(lambda *_: parser.print_help()))
    subparsers = parser.add_subparsers()

    cmd_start_parser = subparsers.add_parser("start", help="Start OTG")
    cmd_start_parser.set_defaults(cmd=_cmd_start)

    cmd_stop_parser = subparsers.add_parser("stop", help="Stop OTG")
    cmd_stop_parser.set_defaults(cmd=_cmd_stop)

    options = parser.parse_args(ia.args)
    try:
        options.cmd(ia.config)
    except ValidatorError as ex:
        raise SystemExit(str(ex))
