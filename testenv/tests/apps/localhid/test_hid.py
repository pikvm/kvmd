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


import asyncio

from collections.abc import AsyncGenerator

import evdev
from evdev import ecodes

import pytest

from kvmd.apps.localhid.hid import Hid


# =====
class _InputDevice:
    def __init__(self, caps: dict[int, list[int]], events: list[evdev.InputEvent] | None=None) -> None:
        self.path = "/dev/input/event-test"
        self.name = "Test device"
        self.phys = "test/input0"

        self.__caps = caps
        self.__events = (events or [])

        self.grabbed = False
        self.closed = False

    def capabilities(self, *, absinfo: bool) -> dict[int, list[int]]:
        assert not absinfo
        return self.__caps

    def grab(self) -> None:  # noqa: vulture-unused
        self.grabbed = True

    def ungrab(self) -> None:  # noqa: vulture-unused
        self.grabbed = False

    def close(self) -> None:
        self.closed = True

    async def async_read_loop(self) -> AsyncGenerator[evdev.InputEvent]:
        for event in self.__events:
            yield event


def _make_hid(monkeypatch, device: _InputDevice) -> Hid:  # type: ignore
    monkeypatch.setattr(evdev, "InputDevice", lambda _path: device)
    return Hid(device.path)


def _make_event(event_type: int, code: int, value: int) -> evdev.InputEvent:
    return evdev.InputEvent(0, 0, event_type, code, value)


# =====
@pytest.mark.parametrize(("keys", "rels", "suitable"), [
    ([ecodes.KEY_LEFTCTRL], [], True),
    ([ecodes.KEY_VOLUMEUP, ecodes.KEY_VOLUMEDOWN], [], True),
    ([ecodes.KEY_POWER, ecodes.KEY_SLEEP, ecodes.KEY_WAKEUP], [], False),
    ([ecodes.KEY_PLAYPAUSE], [], False),
    ([ecodes.BTN_LEFT], [ecodes.REL_X], True),
])
def test_ok__suitable(monkeypatch, keys: list[int], rels: list[int], suitable: bool) -> None:  # type: ignore
    hid = _make_hid(monkeypatch, _InputDevice({
        ecodes.EV_KEY: keys,
        ecodes.EV_REL: rels,
    }))
    assert hid.is_suitable() is suitable


@pytest.mark.asyncio
async def test_ok__consumer_keys(monkeypatch) -> None:  # type: ignore
    device = _InputDevice(
        caps={
            ecodes.EV_SYN: [ecodes.SYN_REPORT],
            ecodes.EV_KEY: [
                ecodes.KEY_MUTE,
                ecodes.KEY_VOLUMEUP,
                ecodes.KEY_VOLUMEDOWN,
                ecodes.KEY_PLAYPAUSE,
                ecodes.KEY_POWER,
            ],
        },
        events=[
            _make_event(ecodes.EV_KEY, ecodes.KEY_VOLUMEUP, 1),
            _make_event(ecodes.EV_KEY, ecodes.KEY_VOLUMEUP, 0),
            _make_event(ecodes.EV_KEY, ecodes.KEY_PLAYPAUSE, 1),
            _make_event(ecodes.EV_KEY, ecodes.KEY_POWER, 1),
        ],
    )
    hid = _make_hid(monkeypatch, device)
    queue: asyncio.Queue[tuple[int, tuple]] = asyncio.Queue()

    hid.set_grabbed(True)
    await hid.poll_to_queue(queue)

    assert device.grabbed
    assert [queue.get_nowait(), queue.get_nowait()] == [
        (Hid.KEY, (ecodes.KEY_VOLUMEUP, True)),
        (Hid.KEY, (ecodes.KEY_VOLUMEUP, False)),
    ]
    assert queue.empty()


@pytest.mark.asyncio
async def test_ok__consumer_keys_while_ungrabbed(monkeypatch) -> None:  # type: ignore
    device = _InputDevice(
        caps={
            ecodes.EV_KEY: [
                ecodes.KEY_MUTE,
                ecodes.KEY_VOLUMEUP,
                ecodes.KEY_VOLUMEDOWN,
            ],
        },
        events=[
            _make_event(ecodes.EV_REL, ecodes.KEY_VOLUMEUP, 1),
            _make_event(ecodes.EV_KEY, ecodes.KEY_VOLUMEUP, 2),
            _make_event(ecodes.EV_KEY, ecodes.KEY_PLAYPAUSE, 1),
            _make_event(ecodes.EV_KEY, ecodes.KEY_VOLUMEDOWN, 3),
            _make_event(ecodes.EV_KEY, ecodes.KEY_MUTE, 1),
        ],
    )
    hid = _make_hid(monkeypatch, device)
    queue: asyncio.Queue[tuple[int, tuple]] = asyncio.Queue()

    await hid.poll_to_queue(queue)

    assert [queue.get_nowait(), queue.get_nowait()] == [
        (Hid.KEY, (ecodes.KEY_VOLUMEDOWN, True)),
        (Hid.KEY, (ecodes.KEY_MUTE, True)),
    ]
    assert queue.empty()


def test_ok__consumer_string(monkeypatch) -> None:  # type: ignore
    hid = _make_hid(monkeypatch, _InputDevice({
        ecodes.EV_KEY: [ecodes.KEY_VOLUMEUP],
    }))

    assert str(hid) == "Hid('/dev/input/event-test', 'Test device', 'test/input0', consumer)"


@pytest.mark.asyncio
async def test_ok__keyboard_keeps_power_key(monkeypatch) -> None:  # type: ignore
    device = _InputDevice(
        caps={
            ecodes.EV_SYN: [ecodes.SYN_REPORT],
            ecodes.EV_KEY: [ecodes.KEY_LEFTCTRL, ecodes.KEY_POWER],
        },
        events=[
            _make_event(ecodes.EV_KEY, ecodes.KEY_POWER, 1),
            _make_event(ecodes.EV_KEY, ecodes.KEY_POWER, 0),
        ],
    )
    hid = _make_hid(monkeypatch, device)
    queue: asyncio.Queue[tuple[int, tuple]] = asyncio.Queue()

    hid.set_grabbed(True)
    await hid.poll_to_queue(queue)

    assert [queue.get_nowait(), queue.get_nowait()] == [
        (Hid.KEY, (ecodes.KEY_POWER, True)),
        (Hid.KEY, (ecodes.KEY_POWER, False)),
    ]
    assert queue.empty()


@pytest.mark.asyncio
async def test_ok__relative_mouse_keeps_key_events(monkeypatch) -> None:  # type: ignore
    device = _InputDevice(
        caps={
            ecodes.EV_SYN: [ecodes.SYN_REPORT],
            ecodes.EV_KEY: [ecodes.BTN_LEFT, ecodes.KEY_PLAYPAUSE],
            ecodes.EV_REL: [ecodes.REL_X],
        },
        events=[
            _make_event(ecodes.EV_KEY, ecodes.KEY_PLAYPAUSE, 1),
            _make_event(ecodes.EV_KEY, ecodes.KEY_PLAYPAUSE, 0),
        ],
    )
    hid = _make_hid(monkeypatch, device)
    queue: asyncio.Queue[tuple[int, tuple]] = asyncio.Queue()

    hid.set_grabbed(True)
    await hid.poll_to_queue(queue)

    assert [queue.get_nowait(), queue.get_nowait()] == [
        (Hid.KEY, (ecodes.KEY_PLAYPAUSE, True)),
        (Hid.KEY, (ecodes.KEY_PLAYPAUSE, False)),
    ]
    assert queue.empty()
