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


import sys
import os
import json
import contextlib
import dataclasses
import textwrap
import argparse
import time

from typing import Generator

import pyudev
import usb.core
import usb.util

from ...yamlconf.dumper import YamlHexInt
from ...yamlconf.dumper import YamlInlinedItemsList
from ...yamlconf.dumper import dump_yaml
from ...yamlconf.dumper import override_yaml_file
from ...yamlconf.merger import yaml_merge

from ...validators.basic import valid_stripped_string_not_empty

from ... import usb
from ... import env

from .. import init


# =====
@dataclasses.dataclass(frozen=True)
class _Function:
    name:    str
    desc:    str
    eps:     int
    enabled: bool


class _GadgetControl:
    def __init__(
        self,
        meta_path: str,
        gadget: str,
        udc: str,
        eps: int,
        init_delay: float,
    ) -> None:

        self.__meta_path = meta_path
        self.__gadget = gadget
        self.__udc = udc
        self.__eps = eps
        self.__init_delay = init_delay

    @contextlib.contextmanager
    def __udc_stopped(self) -> Generator[None, None, None]:
        udc = usb.find_udc(self.__udc)
        udc_path = usb.get_gadget_path(self.__gadget, usb.G_UDC)
        with open(udc_path) as file:
            enabled = bool(file.read().strip())
        if enabled:
            with open(udc_path, "w") as file:
                file.write("\n")
        try:
            yield
        finally:
            self.__clear_profile(recreate=True)
            time.sleep(self.__init_delay)
            with open(udc_path, "w") as file:
                file.write(udc)

    def __clear_profile(self, recreate: bool) -> None:
        # XXX: See pikvm/pikvm#1235
        # After unbind and bind, the gadgets stop working,
        # unless we recreate their links in the profile.
        # Some kind of kernel bug.
        for func in os.listdir(self.__get_fdest_path()):
            path = self.__get_fdest_path(func)
            if os.path.islink(path):
                try:
                    os.unlink(path)
                    if recreate:
                        os.symlink(self.__get_fsrc_path(func), path)
                except (FileNotFoundError, FileExistsError):
                    pass

    def __read_metas(self) -> Generator[_Function, None, None]:
        for name in sorted(os.listdir(self.__meta_path)):
            with open(os.path.join(self.__meta_path, name)) as file:
                meta = json.loads(file.read())
                enabled = os.path.exists(self.__get_fdest_path(meta["function"]))
                yield _Function(
                    name=meta["function"],
                    desc=meta["description"],
                    eps=meta["endpoints"],
                    enabled=enabled,
                )

    def __get_fsrc_path(self, func: str) -> str:
        return usb.get_gadget_path(self.__gadget, usb.G_FUNCTIONS, func)

    def __get_fdest_path(self, func: (str | None)=None) -> str:
        if func is None:
            return usb.get_gadget_path(self.__gadget, usb.G_PROFILE)
        return usb.get_gadget_path(self.__gadget, usb.G_PROFILE, func)

    def change_functions(self, enable: set[str], disable: set[str]) -> None:
        funcs = list(self.__read_metas())
        new: set[str] = set(func.name for func in funcs if func.enabled)
        new = (new - disable) | enable
        eps_req = sum(func.eps for func in funcs if func.name in new)
        if eps_req > self.__eps:
            raise SystemExit(f"No available endpoints for this config: {eps_req} required, {self.__eps} is maximum")
        with self.__udc_stopped():
            self.__clear_profile(recreate=False)
            for func in new:
                try:
                    os.symlink(self.__get_fsrc_path(func), self.__get_fdest_path(func))
                except FileExistsError:
                    pass

    def list_functions(self) -> None:
        funcs = list(self.__read_metas())
        eps_used = sum(func.eps for func in funcs if func.enabled)
        print(f"# Endpoints used: {eps_used} of {self.__eps}")
        print(f"# Endpoints free: {self.__eps - eps_used}")
        for func in funcs:
            print(f"{'+' if func.enabled else '-'} {func.name}  # [{func.eps}] {func.desc}")

    def make_gpio_config(self) -> None:
        config = {
            "drivers": {"otgconf": {"type": "otgconf"}},
            "scheme": {},
            "view": {"table": YamlInlinedItemsList()},
        }
        for func in self.__read_metas():
            config["scheme"][func.name] = {  # type: ignore
                "driver": "otgconf",
                "pin": func.name,
                "mode": "output",
                "pulse": False,
            }
            config["view"]["table"].append([  # type: ignore
                "#" + func.desc,
                "#" + func.name,
                func.name,
            ])
        print(dump_yaml({"kvmd": {"gpio": config}}, colored=sys.stdout.isatty()))

    def reset(self) -> None:
        with self.__udc_stopped():
            pass


# =====
@dataclasses.dataclass(frozen=True)
class _Donor:
    vendor_id:      int
    product_id:     int
    manufacturer:   str
    product:        str
    serial:         (str | None)
    config:         (str | None)
    device_version: int


def _find_inputs() -> set[str]:
    found: set[str] = set()
    ctx = pyudev.Context()
    for device in ctx.list_devices(subsystem="input"):
        props = device.properties
        if props.get("ID_INPUT") == "1" and props.get("ID_BUS") == "usb":
            parent = device.find_parent("usb", "usb_device")
            if parent is not None:
                path = parent.properties.get("DEVPATH")
                if path:
                    found.add(f"{env.SYSFS_PREFIX}/sys{path}")
    return found


def _parse_hex(arg: str) -> int:
    return int(arg.strip(), 16)


def _parse_str(arg: str) -> str:
    return arg.strip()


def _find_donor() -> (_Donor | None):
    for path in _find_inputs():
        kvs: dict = {}
        for (key, name, parser, nullable) in [
            ("vendor_id",      "idVendor",      _parse_hex, False),
            ("product_id",     "idProduct",     _parse_hex, False),
            ("manufacturer",   "manufacturer",  _parse_str, False),
            ("product",        "product",       _parse_str, False),
            ("serial",         "serial",        _parse_str, True),  # See _Donor definition
            ("config",         "configuration", _parse_str, True),
            ("device_version", "bcdDevice",     _parse_hex, False),
        ]:
            try:
                with open(os.path.join(path, name)) as file:
                    kvs[key] = parser(file.read())
            except Exception as ex:
                if isinstance(ex, FileNotFoundError) and nullable:
                    kvs[key] = None
                else:
                    kvs = {}
                    break
        if kvs:
            return _Donor(**kvs)
    return None


def _print_donor_info(donor: _Donor) -> None:
    print(f"VendorID:      0x{donor.vendor_id:04X}")
    print(f"ProductID:     0x{donor.product_id:04X}")
    print(f"Manufacturer:  {donor.manufacturer}")
    print(f"Product:       {donor.product}")
    if donor.serial is not None:  # See _Donor definition
        print(f"Serial:        {donor.serial}")
    if donor.config is not None:
        print(f"Config:        {donor.config}")
    print(f"DeviceVersion: 0x{donor.device_version:04X}")


def _make_donor_config(donor: _Donor) -> dict:
    cdrom = {
        "vendor":   None,
        "product":  "Optical Drive",
        "revision": "1.00",
    }
    flash = {**cdrom, "product": "Flash Drive"}
    config = {
        "vendor_id":      YamlHexInt(donor.vendor_id),
        "product_id":     YamlHexInt(donor.product_id),
        "manufacturer":   donor.manufacturer,
        "product":        donor.product,
        "serial":         donor.serial,
        "config":         donor.config,
        "device_version": (donor.device_version or YamlHexInt(donor.device_version)),
        "devices": {
            "msd": {"default": {"inquiry_string": {
                "cdrom": cdrom,
                "flash": flash,
            }}},
            "drives": {"default": {"inquiry_string": {
                "cdrom": cdrom,
                "flash": flash,
            }}},
        },
    }
    return {"otg": config}


def _print_donor_tip(path: str) -> None:
    if sys.stdout.isatty() and sys.stderr.isatty():
        reset = "\033[39m"
        gray = f"{reset}\033[30;1m"
        blue = f"{reset}\033[34m"
    else:
        gray = blue = reset = ""
    print(file=sys.stderr)
    print(textwrap.dedent(f"""
        {gray}# Note: The config has been stored in the following path:
        #    {blue}{path}{gray}
        # You can also manually edit this file for your needs.
        # Please note that a {blue}reboot{gray} is required to apply this.{reset}
    """).strip(), file=sys.stderr)


def _import_usb_ids(path: str) -> None:
    donor = _find_donor()
    if donor is None:
        raise SystemExit("Can't find any appropriate USB device connected to PiKVM like keyboard or mouse")
    _print_donor_info(donor)
    with override_yaml_file(path) as config:
        yaml_merge(config, _make_donor_config(donor))
    _print_donor_tip(path)


# =====
def main(argv: (list[str] | None)=None) -> None:
    (parent_parser, argv, config) = init(
        add_help=False,
        cli_logging=True,
        argv=argv,
    )
    parser = argparse.ArgumentParser(
        prog="kvmd-otgconf",
        description="KVMD OTG low-level runtime configuration tool",
        parents=[parent_parser],
    )
    parser.add_argument("-l", "--list-functions", action="store_true", help="List functions")
    parser.add_argument("-e", "--enable-function", nargs="+", default=[], metavar="<name>", help="Enable function(s)")
    parser.add_argument("-d", "--disable-function", nargs="+", default=[], metavar="<name>", help="Disable function(s)")
    parser.add_argument("-r", "--reset-gadget", action="store_true", help="Reset gadget")
    parser.add_argument("--import-usb-ids", action="store_true",
                        help="Find a local USB HID device and take its IDs and write it to [--usb-ids] file")
    parser.add_argument("--override", dest="override_path", default="/etc/kvmd/override.yaml",
                        help="A place for config generated by [--import-usb-ids]", metavar="file")
    parser.add_argument("--make-gpio-config", action="store_true")
    options = parser.parse_args(argv[1:])

    if options.import_usb_ids:
        _import_usb_ids(options.override_path)
        return

    gc = _GadgetControl(
        meta_path=config.otg.meta,
        gadget=config.otg.gadget,
        udc=config.otg.udc,
        eps=config.otg.endpoints,
        init_delay=config.otg.init_delay,
    )

    if options.list_functions:
        gc.list_functions()

    elif options.enable_function or options.disable_function:
        enable = set(map(valid_stripped_string_not_empty, options.enable_function))
        disable = set(map(valid_stripped_string_not_empty, options.disable_function))
        gc.change_functions(enable, disable)
        gc.list_functions()

    elif options.reset_gadget:
        gc.reset()

    elif options.make_gpio_config:
        gc.make_gpio_config()

    else:
        gc.list_functions()
