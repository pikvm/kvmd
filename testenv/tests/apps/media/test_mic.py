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
import threading

import pytest

from kvmd.audio import AudioError
from kvmd.apps.media import mic as mic_module
from kvmd.apps.media.mic import MicError
from kvmd.apps.media.mic import MicBusyError
from kvmd.apps.media.mic import MicSink


# =====
class _FakePcm:  # pylint: disable=too-many-instance-attributes
    last: ("_FakePcm | None") = None
    fail_open: bool = False
    available: bool = True
    gate: ("threading.Event | None") = None

    @classmethod
    def probe(cls, device: str) -> bool:
        _ = device
        return cls.available

    def __init__(self, device: str, hz: int, channels: int, latency: float) -> None:
        self.device = device
        self.hz = hz
        self.channels = channels
        self.latency = latency
        self.opened = False
        self.written: list[bytes] = []
        self.fail_write = False
        self.underruns = 0
        self.stalled = 0
        _FakePcm.last = self

    def open(self) -> None:
        if _FakePcm.fail_open:
            raise AudioError("Can't open PCM playback: No such device")
        if _FakePcm.gate is not None:
            _FakePcm.gate.wait(5)  # Bounded, so a failed test can't hang the runner
        self.opened = True

    def write(self, data: bytes) -> None:
        assert self.opened
        if self.fail_write:
            raise AudioError("Can't play PCM: Broken pipe")
        self.written.append(data)

    def close(self) -> None:
        self.opened = False


class _FakeDecoder:
    available: bool = True
    fail_decode: bool = False

    @classmethod
    def probe(cls) -> bool:
        return cls.available

    def __init__(self, hz: int, channels: int) -> None:
        self.hz = hz
        self.channels = channels
        self.closed = False

    def decode(self, data: bytes) -> bytes:
        if _FakeDecoder.fail_decode:
            raise AudioError("Can't decode Opus frame: corrupted stream")
        return b"D" + data

    def close(self) -> None:
        self.closed = True


@pytest.fixture(name="sink")
def _sink_fixture(monkeypatch: pytest.MonkeyPatch) -> MicSink:
    monkeypatch.setattr(mic_module, "PcmPlayback", _FakePcm)
    monkeypatch.setattr(mic_module, "OpusDecoder", _FakeDecoder)
    _FakePcm.last = None
    _FakePcm.fail_open = False
    _FakePcm.available = True
    _FakePcm.gate = None
    _FakeDecoder.available = True
    _FakeDecoder.fail_decode = False
    return MicSink("plughw:UAC2Gadget,0", 0.1)


async def _wait(func, timeout: float=5.0) -> bool:  # type: ignore
    for _ in range(int(timeout / 0.01)):
        if func():
            return True
        await asyncio.sleep(0.01)
    return False


async def _noop(error: str) -> None:
    _ = error


# =====
@pytest.mark.asyncio
async def test_ok__mic__opus(sink: MicSink) -> None:
    ws = object()
    await sink.start(ws, "opus", _noop)
    pcm = _FakePcm.last
    assert pcm is not None
    assert pcm.opened
    assert (pcm.device, pcm.hz, pcm.channels) == ("plughw:UAC2Gadget,0", MicSink.HZ, MicSink.CHANNELS)

    sink.feed(ws, b"123")
    assert (await _wait(lambda: pcm.written == [b"D123"]))

    await sink.start(ws, "opus", _noop)  # The same client doesn't restart the playback
    assert _FakePcm.last is pcm

    sink.stop(ws)
    assert (await _wait(lambda: not pcm.opened))


@pytest.mark.asyncio
async def test_ok__mic__pcm(sink: MicSink) -> None:
    ws = object()
    await sink.start(ws, "pcm", _noop)
    pcm = _FakePcm.last
    assert pcm is not None

    sink.feed(ws, b"123")
    assert (await _wait(lambda: pcm.written == [b"123"]))

    sink.stop(ws)
    assert (await _wait(lambda: not pcm.opened))


@pytest.mark.asyncio
async def test_fail__mic__busy(sink: MicSink) -> None:
    (ws1, ws2) = (object(), object())
    await sink.start(ws1, "opus", _noop)
    pcm = _FakePcm.last
    assert pcm is not None

    with pytest.raises(MicBusyError):
        await sink.start(ws2, "opus", _noop)

    sink.feed(ws2, b"123")  # The foreign client can't feed the playback
    sink.stop(ws2)  # ... and can't stop it too
    await asyncio.sleep(0.1)
    assert pcm.written == []
    assert pcm.opened

    sink.stop(ws1)
    assert (await _wait(lambda: not pcm.opened))

    await sink.start(ws2, "opus", _noop)  # The mic is free now
    assert _FakePcm.last is not pcm
    sink.stop(ws2)


@pytest.mark.asyncio
async def test_fail__mic__format(sink: MicSink) -> None:
    with pytest.raises(MicError):
        await sink.start(object(), "mp3", _noop)


@pytest.mark.asyncio
async def test_fail__mic__no_device(sink: MicSink) -> None:
    _FakePcm.fail_open = True
    with pytest.raises(AudioError):
        await sink.start(object(), "opus", _noop)
    _FakePcm.fail_open = False
    await sink.start(object(), "opus", _noop)  # The failed start doesn't lock the mic


@pytest.mark.asyncio
async def test_fail__mic__playback(sink: MicSink) -> None:
    errors: list[str] = []

    async def on_error(error: str) -> None:
        errors.append(error)

    ws = object()
    await sink.start(ws, "pcm", on_error)
    pcm = _FakePcm.last
    assert pcm is not None
    pcm.fail_write = True

    sink.feed(ws, b"123")
    assert (await _wait(lambda: len(errors) > 0))
    assert "Broken pipe" in errors[0]
    assert not pcm.opened

    await sink.start(object(), "pcm", _noop)  # The broken session doesn't lock the mic


@pytest.mark.asyncio
async def test_ok__mic__info(sink: MicSink) -> None:
    info = (await sink.get_info())
    assert info is not None
    assert info["formats"] == ["opus", "pcm"]
    assert (info["hz"], info["channels"]) == (MicSink.HZ, MicSink.CHANNELS)

    _FakeDecoder.available = False  # No libopus on the system
    info = (await sink.get_info())
    assert info is not None
    assert info["formats"] == ["pcm"]

    _FakePcm.available = False  # No USB audio gadget
    assert (await sink.get_info()) is None


@pytest.mark.asyncio
async def test_fail__mic__disabled() -> None:
    sink = MicSink("", 0.1)
    assert (await sink.get_info()) is None
    with pytest.raises(MicError):
        await sink.start(object(), "opus", _noop)


@pytest.mark.asyncio
async def test_ok__mic__broken_frame(sink: MicSink) -> None:
    # A corrupted frame is skipped, the session goes on
    ws = object()
    await sink.start(ws, "opus", _noop)
    pcm = _FakePcm.last
    assert pcm is not None

    _FakeDecoder.fail_decode = True
    sink.feed(ws, b"123")
    await asyncio.sleep(0.1)
    assert pcm.written == []
    assert pcm.opened  # Not a reason to stop the playback

    _FakeDecoder.fail_decode = False
    sink.feed(ws, b"456")
    assert (await _wait(lambda: pcm.written == [b"D456"]))
    sink.stop(ws)


@pytest.mark.asyncio
async def test_ok__mic__bad_frames(sink: MicSink) -> None:
    # The empty frame is the internal stop signal, the client can't send it;
    # a huge one would occupy the device for its whole duration
    ws = object()
    await sink.start(ws, "pcm", _noop)
    pcm = _FakePcm.last
    assert pcm is not None

    sink.feed(ws, b"")
    sink.feed(ws, b"x" * 100500)
    await asyncio.sleep(0.1)
    assert pcm.written == []
    assert pcm.opened

    sink.feed(ws, b"123")
    assert (await _wait(lambda: pcm.written == [b"123"]))
    sink.stop(ws)


@pytest.mark.asyncio
async def test_ok__mic__queue_trim(sink: MicSink) -> None:
    # The client that sends the audio faster than the real time loses the oldest frames
    ws = object()
    await sink.start(ws, "pcm", _noop)
    pcm = _FakePcm.last
    assert pcm is not None

    # The playing task can't run between the feeds, so the queue overflows
    for index in range(100):
        sink.feed(ws, b"%03d" % (index,))

    assert (await _wait(lambda: len(pcm.written) >= 3))
    await asyncio.sleep(0.1)
    assert len(pcm.written) < 100  # Something was dropped
    assert pcm.written[-1] == b"099"  # ... but the recent audio is kept
    sink.stop(ws)


@pytest.mark.asyncio
async def test_fail__mic__format_change(sink: MicSink) -> None:
    ws = object()
    await sink.start(ws, "opus", _noop)
    with pytest.raises(MicError):
        await sink.start(ws, "pcm", _noop)  # The decoder is already made
    sink.stop(ws)


@pytest.mark.asyncio
async def test_ok__mic__cancelled_open(sink: MicSink) -> None:
    # The cancellation arrives when the device is already open: it must be closed,
    # otherwise the microphone stays busy for everybody until the service restarts
    _FakePcm.gate = threading.Event()
    task = asyncio.create_task(sink.start(object(), "pcm", _noop))
    assert (await _wait(lambda: _FakePcm.last is not None))
    task.cancel()
    _FakePcm.gate.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    pcm = _FakePcm.last
    assert pcm is not None
    assert (await _wait(lambda: not pcm.opened))  # Closed, not leaked

    _FakePcm.gate = None
    ws = object()
    await sink.start(ws, "pcm", _noop)  # ... so the device is free for the next client
    sink.stop(ws)
