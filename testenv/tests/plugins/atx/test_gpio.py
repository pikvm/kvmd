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
import contextlib

from collections.abc import AsyncIterator
from collections.abc import Callable
from types import SimpleNamespace

import pytest

from kvmd.plugins.atx import BaseAtx
from kvmd.plugins.atx import gpio
from kvmd.plugins.atx.disabled import Plugin as DisabledPlugin
from kvmd.yamlconf import make_config

from ...gpio_fakes import _FakeLineRequest
from ...gpio_fakes import _patch_request


_START = 1_000_000_000_000_000_037
_SECOND = 1_000_000_000


async def _wait_until(predicate: Callable[[], bool]) -> None:
    async def wait() -> None:
        while not predicate():
            await asyncio.sleep(0)

    await asyncio.wait_for(wait(), timeout=1)


@contextlib.asynccontextmanager
async def _run_plugin(plugin: gpio.Plugin, line_req: _FakeLineRequest) -> AsyncIterator[None]:
    before = asyncio.all_tasks()
    task = asyncio.create_task(plugin.systask())
    try:
        yield
    finally:
        task.cancel()
        await asyncio.sleep(0)
        line_req.push()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        # AioReader's existing debounce consumers outlive poll(); clean up only these new tasks.
        consumers = asyncio.all_tasks() - before
        for consumer in consumers:
            consumer.cancel()
        await asyncio.gather(*consumers, return_exceptions=True)


def _make_plugin(inverted: bool=False) -> gpio.Plugin:
    return gpio.Plugin(make_config({}, {
        "power_led_inverted": inverted,
        "power_led_debounce": 0,
        "hdd_led_debounce": 0,
    }, gpio.Plugin.get_plugin_options()))


async def _assert_state(plugin: gpio.Plugin, power: bool, hdd: bool) -> dict:
    # Both methods must finish in the same event-loop turn, as in the future HTTP handler.
    state = await plugin.get_state()
    history = await plugin.get_history()
    assert state == {
        "enabled": True,
        "busy": False,
        "acts": {"power": False, "reset": False},
        "leds": {"power": power, "hdd": hdd},
    }
    assert history["power"][29] == state["leds"]["power"]
    return history


async def _change_state(states: AsyncIterator[dict], line_req: _FakeLineRequest, pin: int, value: bool) -> dict:
    pending = asyncio.ensure_future(anext(states))
    line_req.push((pin, value))
    return await asyncio.wait_for(pending, timeout=1)


@pytest.mark.asyncio
@pytest.mark.parametrize("inverted", [False, True])
async def test_ok__power_history_and_state(monkeypatch: pytest.MonkeyPatch, inverted: bool) -> None:
    line_req = _patch_request(monkeypatch, {24: False, 22: False})
    now = _START
    monkeypatch.setattr(gpio, "time", SimpleNamespace(monotonic_ns=lambda: now))
    recorded: list[tuple[bool, int]] = []
    original = gpio._LedHistory.record  # pylint: disable=protected-access

    def record(history: gpio._LedHistory, value: bool, changes: int, timestamp: int) -> None:  # pylint: disable=protected-access
        original(history, value, changes, timestamp)
        recorded.append((value, changes))

    monkeypatch.setattr(gpio._LedHistory, "record", record)  # pylint: disable=protected-access
    plugin = _make_plugin(inverted)
    assert await plugin.get_history() == {
        "interval_ms": 100,
        "complete": False,
        "power": [None] * 30,
        "transitions": [],
    }
    states = plugin.poll_state()
    async with _run_plugin(plugin, line_req):
        try:
            await _wait_until(lambda: len(recorded) == 1)
            assert recorded == [(inverted, 0)]
            state = await asyncio.wait_for(anext(states), timeout=1)
            assert state["leds"] == {"power": inverted, "hdd": False}
            assert "history" not in state
            history = await _assert_state(plugin, inverted, False)
            assert history["complete"] is False

            now += 3 * _SECOND
            history = await _assert_state(plugin, inverted, False)
            assert history["complete"] is True
            assert history["power"] == [int(inverted)] * 30

            expected: list[list[float | bool]] = []
            for (changes, raw) in enumerate([True, False, True], start=1):
                now += 500_000_000
                state = await _change_state(states, line_req, 24, raw)
                assert len(recorded) == changes + 1
                value = raw ^ inverted
                assert state["leds"] == {"power": value, "hdd": False}
                assert "history" not in state
                history = await _assert_state(plugin, value, False)
                expected = [[offset - 0.5, val] for (offset, val) in expected] + [[0.0, value]]
                assert history["transitions"] == expected
                assert history["complete"] is True

            # HDD events still reach poll_state(), without changing Power LED history.
            for raw in [True, False]:
                state = await _change_state(states, line_req, 22, raw)
                assert state["leds"] == {"power": (True ^ inverted), "hdd": raw}
                assert "history" not in state
                assert await plugin.get_history() == history
                assert len(recorded) == 4
        finally:
            await states.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("followup", [False, True])
async def test_fail__recording_does_not_stop_state(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    followup: bool,
) -> None:
    line_req = _patch_request(monkeypatch, {24: False, 22: False})
    now = _START
    monkeypatch.setattr(gpio, "time", SimpleNamespace(monotonic_ns=lambda: now))
    attempted: list[int] = []
    original = gpio._LedHistory.record  # pylint: disable=protected-access

    def record(history: gpio._LedHistory, value: bool, changes: int, timestamp: int) -> None:  # pylint: disable=protected-access
        attempted.append(changes)
        if changes == 1:
            raise RuntimeError("one history callback failed")
        original(history, value, changes, timestamp)

    monkeypatch.setattr(gpio._LedHistory, "record", record)  # pylint: disable=protected-access
    plugin = _make_plugin()
    states = plugin.poll_state()
    async with _run_plugin(plugin, line_req):
        try:
            await _wait_until(lambda: attempted == [0])
            assert (await asyncio.wait_for(anext(states), timeout=1))["leds"]["power"] is False
            now += 3 * _SECOND
            assert (await plugin.get_history())["complete"] is True

            now += _SECOND
            assert (await _change_state(states, line_req, 24, True))["leds"]["power"] is True
            assert attempted == [0, 1]
            assert (await plugin.get_state())["leds"]["power"] is True
            assert len(caplog.records) == 1
            assert "one history callback failed" in caplog.text

            # Either the next callback or get_history() detects the missing count.
            current = True
            if followup:
                now += 100_000_000
                assert (await _change_state(states, line_req, 24, False))["leds"]["power"] is False
                assert attempted == [0, 1, 2]
                current = False
            history = await _assert_state(plugin, current, False)
            assert history["power"] == [None] * 29 + [int(current)]
            assert history["transitions"] == []
            assert history["complete"] is False
            restart = now

            # Subsequent state delivery and successful recording continue after the failure.
            now += 500_000_000
            state = await _change_state(states, line_req, 24, not current)
            assert state["leds"]["power"] is (not current)
            history = await _assert_state(plugin, not current, False)
            assert history["transitions"] == [[0.0, not current]]
            assert history["complete"] is False
            now = restart + 3 * _SECOND - 1
            assert (await plugin.get_history())["complete"] is False
            now += 1
            assert (await plugin.get_history())["complete"] is True
        finally:
            await states.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("inverted", [False, True])
async def test_fail__initial_record_without_changes(
    monkeypatch: pytest.MonkeyPatch,
    inverted: bool,
) -> None:
    line_req = _patch_request(monkeypatch, {24: False, 22: False})
    now = _START
    monkeypatch.setattr(gpio, "time", SimpleNamespace(monotonic_ns=lambda: now))
    attempted: list[int] = []
    original = gpio._LedHistory.record  # pylint: disable=protected-access

    def record(history: gpio._LedHistory, value: bool, changes: int, timestamp: int) -> None:  # pylint: disable=protected-access
        attempted.append(changes)
        if attempted == [0]:
            raise RuntimeError("initial history callback failed")
        original(history, value, changes, timestamp)

    monkeypatch.setattr(gpio._LedHistory, "record", record)  # pylint: disable=protected-access
    plugin = _make_plugin(inverted)
    async with _run_plugin(plugin, line_req):
        await _wait_until(lambda: attempted == [0])
        history = await _assert_state(plugin, inverted, False)
        assert history["power"] == [None] * 29 + [int(inverted)]
        assert history["transitions"] == []
        assert history["complete"] is False
        now += 3 * _SECOND - 1
        assert (await plugin.get_history())["complete"] is False
        now += 1
        history = await _assert_state(plugin, inverted, False)
        assert history["power"] == [int(inverted)] * 30
        assert history["transitions"] == []
        assert history["complete"] is True
        assert attempted == [0]


@pytest.mark.asyncio
@pytest.mark.parametrize("cls", [BaseAtx, DisabledPlugin])
async def test_ok__base_history(cls: type[BaseAtx]) -> None:
    plugin = cls(make_config({}, {}, cls.get_plugin_options()))
    assert await plugin.get_history() == {}
