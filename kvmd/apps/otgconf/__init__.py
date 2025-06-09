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
import json
import contextlib
import dataclasses
import argparse
import time

from typing import Generator

import yaml

from ...validators.basic import valid_stripped_string_not_empty

from ... import usb

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
            raise RuntimeError(f"No available endpoints for this config: {eps_req} required, {self.__eps} is maximum")
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
        class Dumper(yaml.Dumper):
            def increase_indent(self, flow: bool=False, indentless: bool=False) -> None:
                _ = indentless
                super().increase_indent(flow, False)

            def ignore_aliases(self, data) -> bool:  # type: ignore
                _ = data
                return True

        class InlineList(list):
            pass

        def represent_inline_list(dumper: yaml.Dumper, data):  # type: ignore
            return dumper.represent_sequence("tag:yaml.org,2002:seq", data, flow_style=True)

        Dumper.add_representer(InlineList, represent_inline_list)

        config = {
            "drivers": {"otgconf": {"type": "otgconf"}},
            "scheme": {},
            "view": {"table": []},
        }
        for func in self.__read_metas():
            config["scheme"][func.name] = {  # type: ignore
                "driver": "otgconf",
                "pin": func.name,
                "mode": "output",
                "pulse": False,
            }
            config["view"]["table"].append(InlineList([  # type: ignore
                "#" + func.desc,
                "#" + func.name,
                func.name,
            ]))
        print(yaml.dump({"kvmd": {"gpio": config}}, indent=4, Dumper=Dumper))

    def reset(self) -> None:
        with self.__udc_stopped():
            pass


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
    parser.add_argument("--make-gpio-config", action="store_true")
    options = parser.parse_args(argv[1:])

    gc = _GadgetControl(config.otg.meta, config.otg.gadget, config.otg.udc, config.otg.endpoints, config.otg.init_delay)

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
