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


import json
import os

from pathlib import Path
from types import SimpleNamespace

from typing import Any
from typing import cast

import pytest

from kvmd.apps import otg
from kvmd.apps._scheme import make_config_scheme

from kvmd.apps.otg.hid.consumer import make_consumer_hid

from kvmd.yamlconf import Section


class _Section(SimpleNamespace):
    def _unpack(self, ignore: (list[str] | str | None)=None) -> dict[str, Any]:
        if isinstance(ignore, str):
            ignore = [ignore]
        return {
            key: value
            for (key, value) in vars(self).items()
            if key not in (ignore or [])
        }


def _make_config(endpoints: int, mouse_alt_device: str, consumer_start: bool=True) -> _Section:
    inquiry_string = _Section(vendor=None, product="Drive", revision="1.00")
    return _Section(
        kvmd=_Section(
            hid=_Section(
                type="otg",
                mouse=_Section(absolute=True, horizontal_wheel=True),
                mouse_alt=_Section(device=mouse_alt_device),
            ),
            msd=_Section(type="otg"),
        ),
        otg=_Section(
            vendor_id=0x1D6B,
            product_id=0x0104,
            usb_version=0x0200,
            device_version=-1,
            manufacturer="PiKVM",
            product="PiKVM Composite Device",
            serial=None,
            config=None,
            max_power=250,
            remote_wakeup=True,
            udc="",
            meta="/run/kvmd/otg",
            endpoints=endpoints,
            init_delay=0,
            user="root",
            devices=_Section(
                hid=_Section(
                    keyboard=_Section(start=True),
                    mouse=_Section(start=True),
                    mouse_alt=_Section(start=True),
                    consumer=_Section(start=consumer_start),
                ),
                msd=_Section(
                    start=True,
                    default=_Section(
                        image_path="",
                        stall=False,
                        cdrom=True,
                        rw=False,
                        removable=True,
                        fua=True,
                        inquiry_string=_Section(cdrom=inquiry_string, flash=inquiry_string),
                    ),
                ),
                drives=_Section(enabled=False),
                ethernet=_Section(enabled=False),
                serial=_Section(enabled=False),
                audio=_Section(
                    enabled=False,
                    speakers=_Section(enabled=False),
                    mic=_Section(enabled=False),
                ),
                camera=_Section(enabled=False),
            ),
        ),
    )


# =====
def test_ok__consumer_hid() -> None:
    hid = make_consumer_hid()

    assert hid.protocol == 0
    assert hid.subclass == 0
    assert hid.report_length == 2
    assert hid.report_descriptor == bytes.fromhex(
        "05 0C 09 01 A1 01 15 00 26 FF 03 19 00 2A FF 03 75 10 95 01 81 00 C0"
    )


def test_ok__consumer_default_off() -> None:
    scheme = make_config_scheme()

    assert scheme["otg"]["devices"]["hid"]["consumer"]["start"].default is False


@pytest.mark.parametrize(("endpoints", "mouse_alt_device", "consumer_start", "started"), [
    (3, "/dev/kvmd-hid-mouse-alt", True, {"hid.usb0", "hid.usb1", "hid.usb2"}),
    (5, "/dev/kvmd-hid-mouse-alt", True, {"hid.usb0", "hid.usb1", "hid.usb2", "mass_storage.usb0"}),
    (5, "", True, {"hid.usb0", "hid.usb1", "mass_storage.usb0", "hid.usb3"}),
    (5, "", False, {"hid.usb0", "hid.usb1", "mass_storage.usb0"}),
])
# pylint: disable=protected-access
def test_ok__consumer_preserves_existing_allocations(  # type: ignore
    monkeypatch,
    endpoints: int,
    mouse_alt_device: str,
    consumer_start: bool,
    started: set[str],
) -> None:
    meta: dict[str, dict] = {}
    links: set[str] = set()
    writes: dict[str, (str | bytes | int)] = {}

    def record_write(path: str, value: (str | bytes | int), optional: bool=False) -> None:
        del optional
        writes[path] = value
        if path.endswith("@meta.json"):
            meta[os.path.basename(path).removesuffix("@meta.json")] = json.loads(str(value))

    monkeypatch.setattr(otg, "_mkdir", lambda _path: None)
    monkeypatch.setattr(otg, "_write", record_write)
    monkeypatch.setattr(otg, "_write_bytes", lambda _path, _data: None)
    monkeypatch.setattr(otg, "_chown", lambda _path, _user: None)
    monkeypatch.setattr(otg, "_symlink", lambda _src, dest: links.add(os.path.basename(dest)))
    monkeypatch.setattr(otg.os.path, "exists", lambda _path: False)
    monkeypatch.setattr(otg.usb, "get_gadget_path", lambda: "/sys/kernel/config/usb_gadget/kvmd")
    monkeypatch.setattr(otg.usb, "find_udc", lambda _udc: "fe980000.usb")
    monkeypatch.setattr(otg.time, "sleep", lambda _delay: None)

    otg._cmd_start(cast(Section, _make_config(endpoints, mouse_alt_device, consumer_start)))

    assert meta["hid.usb2"]["description"] == "Relative Mouse"
    assert meta["hid.usb3"]["description"] == "Consumer Control"
    assert meta["hid.usb3"]["starter"] == ["otg", "devices", "hid", "consumer", "start"]
    assert writes["/sys/kernel/config/usb_gadget/kvmd/bcdDevice"] == "0x0100"
    assert links == started


def test_ok__consumer_udev_mapping() -> None:
    rules_dir = Path(__file__).resolve().parents[4] / "configs/os/udev"
    rules = [
        path
        for path in rules_dir.glob("*.rules")
        if "kvmd-hid-mouse-alt" in path.read_text()
    ]

    assert len(rules) == 7
    for path in rules:
        text = path.read_text()
        assert 'KERNEL=="hidg2", GROUP="kvmd", SYMLINK+="kvmd-hid-mouse-alt"' in text
        assert 'KERNEL=="hidg3", GROUP="kvmd", SYMLINK+="kvmd-hid-consumer"' in text
# pylint: enable=protected-access
