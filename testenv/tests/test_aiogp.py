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
import threading

from collections.abc import AsyncIterator
from collections.abc import Callable
from types import SimpleNamespace

import pytest

from kvmd import aiogp
from kvmd import aiotools

from .gpio_fakes import _FakeLineRequest
from .gpio_fakes import _patch_request


# =====
class _RecordingNotifier(aiotools.AioNotifier):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0
        self.order: list[str] = []

    def notify(self, mask: int=0) -> None:
        self.calls += 1
        self.order.append("notify")
        super().notify(mask)


async def _wait_until(predicate: Callable[[], bool]) -> None:
    async def wait() -> None:
        while not predicate():
            await asyncio.sleep(0)

    await asyncio.wait_for(wait(), timeout=1)


async def _wait_changes(reader: aiogp.AioReader, pin: int, changes: int) -> None:
    await _wait_until(lambda: reader.get_changes(pin) == changes)


@contextlib.asynccontextmanager
async def _poll_reader(reader: aiogp.AioReader, line_req: _FakeLineRequest) -> AsyncIterator[None]:
    before = asyncio.all_tasks()
    task = asyncio.create_task(reader.poll())
    try:
        yield
    finally:
        task.cancel()
        await asyncio.sleep(0)  # Let poll() set its stop event before waking the GPIO thread.
        line_req.push()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        # Existing AioReader leaves its debounce consumers running; cancel only this reader's tasks.
        consumers = asyncio.all_tasks() - before
        for consumer in consumers:
            consumer.cancel()
        await asyncio.gather(*consumers, return_exceptions=True)


# =====
@pytest.mark.asyncio
async def test_ok__without_callback(monkeypatch: pytest.MonkeyPatch) -> None:
    line_req = _patch_request(monkeypatch, {3: False, 5: True})
    notifier = _RecordingNotifier()
    reader = aiogp.AioReader(
        path="/fake/gpiochip0",
        consumer="test",
        pins={3: aiogp.AioReaderPinParams(False, 0), 5: aiogp.AioReaderPinParams(True, 0)},
        notifier=notifier,
        on_change=None,
    )
    assert reader.get(3) is False
    assert reader.get(5) is True
    assert reader.get_changes(3) is None
    assert reader.get_changes(5) is None

    async with _poll_reader(reader, line_req):
        await _wait_until(lambda: notifier.calls == 1)
        assert reader.get(3) is False
        assert reader.get(5) is False
        assert reader.get_changes(3) == reader.get_changes(5) == 0
        assert await asyncio.wait_for(notifier.wait(), timeout=1) == 0

        line_req.push((3, True))
        await _wait_until(lambda: reader.get_changes(3) == 1)
        assert reader.get(3) is True
        assert notifier.calls == 2
        assert await asyncio.wait_for(notifier.wait(), timeout=1) == 0

        line_req.push((3, True), (5, True))
        await line_req.flush()
        line_req.push((5, False))
        await _wait_until(lambda: reader.get_changes(5) == 1)
        assert reader.get(5) is True
        assert reader.get_changes(3) == 1
        assert notifier.calls == 3

        line_req.push((3, False))
        await _wait_until(lambda: reader.get_changes(3) == 2)
        assert reader.get(3) is False
        assert notifier.calls == 4


@pytest.mark.asyncio
async def test_ok__initial_before_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    line_req = _patch_request(monkeypatch, {5: True, 3: False})
    notifier = _RecordingNotifier()
    received: list[tuple[int, bool, int]] = []
    observed: list[tuple[bool, int | None, int]] = []
    loop_thread = threading.get_ident()

    def on_change(pin: int, value: bool, changes: int) -> None:
        received.append((pin, value, changes))
        observed.append((reader.get(pin), reader.get_changes(pin), threading.get_ident()))
        notifier.order.append("change")

    reader = aiogp.AioReader(
        path="/fake/gpiochip0",
        consumer="test",
        pins={5: aiogp.AioReaderPinParams(True, 0), 3: aiogp.AioReaderPinParams(False, 0)},
        notifier=notifier,
        on_change=on_change,
    )
    # Queue edges before the reader starts to exercise the initialization ordering race.
    line_req.push((3, True), (5, False))
    async with _poll_reader(reader, line_req):
        await _wait_until(lambda: len(received) == 4)
        assert received[:2] == [(3, False, 0), (5, False, 0)]
        assert sorted(received[2:]) == [(3, True, 1), (5, True, 1)]
        assert observed == [(value, changes, loop_thread) for (_, value, changes) in received]
        assert line_req.thread_id != loop_thread
        assert notifier.calls == 3
        assert notifier.order == ["notify", "change", "change", "notify", "change", "notify", "change"]


@pytest.mark.asyncio
async def test_ok__debounced_roundtrip(monkeypatch: pytest.MonkeyPatch) -> None:
    line_req = _patch_request(monkeypatch, {3: False})
    notifier = _RecordingNotifier()
    received: list[tuple[int, bool, int]] = []
    pauses: asyncio.Queue[asyncio.Future[None]] = asyncio.Queue()
    real_sleep = asyncio.sleep

    async def gated_sleep(delay: float) -> None:
        if delay == 0.001:
            resume: asyncio.Future[None] = asyncio.get_running_loop().create_future()
            pauses.put_nowait(resume)
            await resume
        else:
            await real_sleep(delay)

    # Hold the debounce wait explicitly so thread scheduling cannot stretch the bounce past it.
    monkeypatch.setattr(aiogp.asyncio, "sleep", gated_sleep)

    def on_change(pin: int, value: bool, changes: int) -> None:
        received.append((pin, value, changes))

    reader = aiogp.AioReader(
        path="/fake/gpiochip0",
        consumer="test",
        pins={3: aiogp.AioReaderPinParams(False, 0.001)},
        notifier=notifier,
        on_change=on_change,
    )
    async with _poll_reader(reader, line_req):
        await _wait_until(lambda: len(received) == 1)
        line_req.push((3, True))
        resume = await asyncio.wait_for(pauses.get(), timeout=1)
        assert received == [(3, False, 0), (3, True, 1)]
        assert notifier.calls == 2

        # Two separate batches reach the debounce queue during the same wait.
        line_req.push((3, False))
        line_req.push((3, True))
        await line_req.flush()
        resume.set_result(None)
        resume = await asyncio.wait_for(pauses.get(), timeout=1)
        assert received == [(3, False, 0), (3, True, 1)]
        assert notifier.calls == 2
        assert reader.get_changes(3) == 1

        line_req.push((3, True))
        await line_req.flush()
        resume.set_result(None)
        resume = await asyncio.wait_for(pauses.get(), timeout=1)
        assert len(received) == 2
        assert notifier.calls == 2

        line_req.push((3, False))
        await line_req.flush()
        resume.set_result(None)
        await asyncio.wait_for(pauses.get(), timeout=1)
        assert received[-1] == (3, False, 2)
        assert reader.get(3) is False
        assert reader.get_changes(3) == 2
        assert notifier.calls == 3


@pytest.mark.asyncio
async def test_fail__callback_continues(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    line_req = _patch_request(monkeypatch, {3: False})
    notifier = _RecordingNotifier()
    received: list[tuple[int, bool, int]] = []

    def on_change(pin: int, value: bool, changes: int) -> None:
        received.append((pin, value, changes))
        notifier.order.append("change")
        if changes != 2:
            raise RuntimeError("callback failed")

    reader = aiogp.AioReader(
        path="/fake/gpiochip0",
        consumer="test",
        pins={3: aiogp.AioReaderPinParams(False, 0)},
        notifier=notifier,
        on_change=on_change,
    )
    async with _poll_reader(reader, line_req):
        await _wait_until(lambda: len(received) == 1)
        for (changes, value) in enumerate([True, False, True], start=1):
            line_req.push((3, value))
            await _wait_changes(reader, 3, changes)
            assert received[-1] == (3, value, changes)
            assert reader.get(3) == value
            assert reader.get_changes(3) == changes
            assert notifier.calls == changes + 1
        assert notifier.order == ["notify", "change"] * 4
        assert len(caplog.records) == 1
        assert caplog.records[0].exc_info is not None


@pytest.mark.asyncio
async def test_fail__callback_log_rate(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    line_req = _patch_request(monkeypatch, {3: False})
    notifier = _RecordingNotifier()
    now = 0.0
    # Patch the module reference rather than asyncio's shared monotonic clock.
    monkeypatch.setattr(aiogp, "time", SimpleNamespace(monotonic=lambda: now))

    def on_change(_pin: int, _value: bool, _changes: int) -> None:
        raise RuntimeError("callback failed")

    reader = aiogp.AioReader(
        path="/fake/gpiochip0",
        consumer="test",
        pins={3: aiogp.AioReaderPinParams(False, 0)},
        notifier=notifier,
        on_change=on_change,
    )
    async with _poll_reader(reader, line_req):
        await _wait_until(lambda: len(caplog.records) == 1)
        assert len(caplog.records) == 1
        assert caplog.records[0].exc_info is not None
        steps = [
            (0.0, True, 1),
            (59.999, False, 1),
            (60.0, True, 2),
            (60.0, False, 2),
            (119.999, True, 2),
            (120.0, False, 3),
        ]
        for (changes, (now, value, logs)) in enumerate(steps, start=1):
            line_req.push((3, value))
            await _wait_changes(reader, 3, changes)
            assert notifier.calls == changes + 1
            assert len(caplog.records) == logs
        assert [record.levelname for record in caplog.records] == ["ERROR"] * 3
        assert all(record.exc_info is None for record in caplog.records[1:])
        assert "failed 4 times" in caplog.records[1].getMessage()
        assert "failed 7 times" in caplog.records[2].getMessage()
