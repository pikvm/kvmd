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


from evdev import ecodes

import pytest

from kvmd import aiomulti

from kvmd.plugins.hid.otg import Plugin
from kvmd.plugins.hid.otg.consumer import ConsumerProcess
from kvmd.plugins.hid.otg.events import BaseEvent
from kvmd.plugins.hid.otg.events import ClearEvent
from kvmd.plugins.hid.otg.events import ConsumerEvent
from kvmd.plugins.hid.otg.events import is_consumer_key
from kvmd.plugins.hid.otg.events import make_consumer_event
from kvmd.plugins.hid.otg.events import make_consumer_report


_CONSUMER_PROFILE_PATH = "/sys/kernel/config/usb_gadget/kvmd/configs/c.1/hid.usb3"


# =====
@pytest.mark.parametrize(("key", "usage"), [
    (ecodes.KEY_MUTE, 0xE2),
    (ecodes.KEY_VOLUMEUP, 0xE9),
    (ecodes.KEY_VOLUMEDOWN, 0xEA),
])
def test_ok__consumer_event(key: int, usage: int) -> None:
    assert is_consumer_key(key)
    assert make_consumer_event(key, True) == ConsumerEvent(usage, True)
    assert make_consumer_report(usage) == usage.to_bytes(2, "little")


def test_ok__regular_key() -> None:
    assert not is_consumer_key(ecodes.KEY_A)
    with pytest.raises(KeyError):
        make_consumer_event(ecodes.KEY_A, True)


# pylint: disable=protected-access
def test_ok__consumer_process() -> None:
    process = ConsumerProcess(
        notifier=aiomulti.AioMpNotifier(),
        noop=True,
        device_path="/dev/null",
        select_timeout=0.1,
        queue_timeout=0.1,
        write_retries=1,
    )

    assert list(process._process_event(ConsumerEvent(0xE9, True))) == [b"\xE9\x00"]
    assert list(process._process_event(ConsumerEvent(0xEA, True))) == [b"\xEA\x00"]
    assert list(process._process_event(ConsumerEvent(0xE9, False))) == [b"\xEA\x00"]
    assert list(process._process_event(ConsumerEvent(0xEA, False))) == [b"\x00\x00"]

    assert list(process._process_event(ConsumerEvent(0xE9, True))) == [b"\xE9\x00"]
    assert list(process._process_event(ConsumerEvent(0xEA, True))) == [b"\xEA\x00"]
    assert list(process._process_event(ConsumerEvent(0xEA, False))) == [b"\xE9\x00"]
    assert list(process._process_event(ConsumerEvent(0xE9, False))) == [b"\x00\x00"]

    assert list(process._process_event(ClearEvent())) == [b"\x00\x00"]

    with pytest.raises(RuntimeError):
        list(process._process_event(BaseEvent()))


@pytest.mark.parametrize(("consumer_available", "keyboard_events", "consumer_events"), [
    (True, [(ecodes.KEY_A, True)], [(ecodes.KEY_VOLUMEUP, True)]),
    (False, [(ecodes.KEY_A, True), (ecodes.KEY_VOLUMEUP, True)], []),
])
def test_ok__plugin_key_routing(  # type: ignore
    monkeypatch,
    consumer_available: bool,
    keyboard_events: list[tuple[int, bool]],
    consumer_events: list[tuple[int, bool]],
) -> None:
    class _Recorder:
        def __init__(self) -> None:
            self.events: list[tuple[int, bool]] = []

        def send_key_event(self, key: int, state: bool) -> None:
            self.events.append((key, state))

    keyboard = _Recorder()
    consumer = _Recorder()
    plugin = Plugin.__new__(Plugin)
    setattr(plugin, "_Plugin__keyboard_proc", keyboard)
    setattr(plugin, "_Plugin__consumer_proc", consumer)
    setattr(plugin, "_Plugin__consumer_profile_path", _CONSUMER_PROFILE_PATH)
    checked_paths: list[str] = []

    def exists(path: str) -> bool:
        checked_paths.append(path)
        return consumer_available

    monkeypatch.setattr("kvmd.plugins.hid.otg.os.path.exists", exists)

    plugin._send_key_event(ecodes.KEY_A, True)
    plugin._send_key_event(ecodes.KEY_VOLUMEUP, True)

    assert keyboard.events == keyboard_events
    assert consumer.events == consumer_events
    assert checked_paths == [_CONSUMER_PROFILE_PATH]


@pytest.mark.parametrize(("press_available", "release_available", "keyboard_events", "consumer_events"), [
    (
        True,
        False,
        [(ecodes.KEY_VOLUMEUP, False)],
        [(ecodes.KEY_VOLUMEUP, True), (ecodes.KEY_VOLUMEUP, False)],
    ),
    (
        False,
        True,
        [(ecodes.KEY_VOLUMEUP, True), (ecodes.KEY_VOLUMEUP, False)],
        [(ecodes.KEY_VOLUMEUP, False)],
    ),
])
def test_ok__plugin_key_release_clears_both_outputs(  # type: ignore
    monkeypatch,
    press_available: bool,
    release_available: bool,
    keyboard_events: list[tuple[int, bool]],
    consumer_events: list[tuple[int, bool]],
) -> None:
    class _Recorder:
        def __init__(self) -> None:
            self.events: list[tuple[int, bool]] = []

        def send_key_event(self, key: int, state: bool) -> None:
            self.events.append((key, state))

    keyboard = _Recorder()
    consumer = _Recorder()
    plugin = Plugin.__new__(Plugin)
    setattr(plugin, "_Plugin__keyboard_proc", keyboard)
    setattr(plugin, "_Plugin__consumer_proc", consumer)
    setattr(plugin, "_Plugin__consumer_profile_path", _CONSUMER_PROFILE_PATH)
    availability = [press_available]
    checks: list[bool] = []

    def exists(_path: str) -> bool:
        checks.append(availability[0])
        return availability[0]

    monkeypatch.setattr("kvmd.plugins.hid.otg.os.path.exists", exists)

    plugin._send_key_event(ecodes.KEY_VOLUMEUP, True)
    availability[0] = release_available
    plugin._send_key_event(ecodes.KEY_VOLUMEUP, False)

    assert checks == [press_available]
    assert keyboard.events == keyboard_events
    assert consumer.events == consumer_events
# pylint: enable=protected-access


@pytest.mark.parametrize("usage", [-1, 0x400])
def test_fail__consumer_report(usage: int) -> None:
    with pytest.raises(AssertionError):
        make_consumer_report(usage)
