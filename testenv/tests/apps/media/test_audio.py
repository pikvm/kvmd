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
import time
import threading

import pytest

from kvmd.audio import AudioError
from kvmd.apps.media import audio as audio_module
from kvmd.apps.media.audio import AudioSourceError
from kvmd.apps.media.audio import AudioSource


# =====
class _FakeCapture:  # pylint: disable=too-many-instance-attributes
    last: ("_FakeCapture | None") = None
    count: int = 0
    available: bool = True
    fail_open: bool = False
    fail_read: bool = False
    empty_reads: int = 0
    real_hz: int = AudioSource.HZ

    @classmethod
    def probe(cls, device: str) -> bool:
        _ = device
        return cls.available

    def __init__(self, device: str, hz: int, channels: int, frame_ms: int) -> None:
        self.device = device
        self.hz = hz
        self.channels = channels
        self.frame_ms = frame_ms
        self.opened = False
        self.reads = 0
        self.period = 960
        self.buffer = 9600
        self.overruns = 0
        self.skipped = 0
        self.stalled = 0
        _FakeCapture.last = self
        _FakeCapture.count += 1

    def open(self) -> int:
        if _FakeCapture.fail_open:
            raise AudioError("Can't open PCM capture: No such device")
        self.opened = True
        return _FakeCapture.real_hz

    def read(self) -> bytes:
        assert self.opened
        time.sleep(0.01)  # Emulate the real-time capture
        if _FakeCapture.fail_read:
            raise AudioError("Can't capture PCM: Input/output error")
        if _FakeCapture.empty_reads > 0:
            # The device was recovered after an overrun or stalled
            _FakeCapture.empty_reads -= 1
            return b""
        self.reads += 1
        return b"RAW%04d" % (self.reads,)  # The number makes the dropped frames visible

    def close(self) -> None:
        self.opened = False


class _FakeEncoder:
    last: ("_FakeEncoder | None") = None
    available: bool = True
    fail_encode: bool = False
    fail_init: bool = False
    fails: int = 0
    thread: int = 0

    @classmethod
    def probe(cls) -> bool:
        return cls.available

    def __init__(self, hz: int, channels: int, bitrate: int) -> None:
        _FakeEncoder.thread = threading.get_ident()
        if _FakeEncoder.fail_init:
            raise AudioError("Can't create Opus encoder: no memory")
        self.hz = hz
        self.channels = channels
        self.bitrate = bitrate
        self.calls = 0
        self.closed = False
        _FakeEncoder.last = self

    def encode(self, data: bytes) -> bytes:
        if _FakeEncoder.fail_encode:
            _FakeEncoder.fails += 1
            raise AudioError("Can't encode Opus frame: invalid argument")
        self.calls += 1
        return b"E" + data

    def close(self) -> None:
        self.closed = True


class _FakeResampler:
    last: ("_FakeResampler | None") = None
    thread: int = 0

    def __init__(self, channels: int, in_hz: int, out_hz: int, in_frames: int) -> None:
        _FakeResampler.thread = threading.get_ident()
        self.channels = channels
        self.in_hz = in_hz
        self.out_hz = out_hz
        self.in_frames = in_frames
        self.closed = False
        _FakeResampler.last = self

    def process(self, data: bytes) -> bytes:
        return b"R" + data

    def close(self) -> None:
        self.closed = True


@pytest.fixture(name="src")
def _src_fixture(monkeypatch: pytest.MonkeyPatch) -> AudioSource:
    monkeypatch.setattr(audio_module, "PcmCapture", _FakeCapture)
    monkeypatch.setattr(audio_module, "OpusEncoder", _FakeEncoder)
    monkeypatch.setattr(audio_module, "Resampler", _FakeResampler)
    _FakeCapture.last = None
    _FakeCapture.count = 0
    _FakeCapture.available = True
    _FakeCapture.fail_open = False
    _FakeCapture.fail_read = False
    _FakeCapture.empty_reads = 0
    _FakeCapture.real_hz = AudioSource.HZ
    _FakeEncoder.last = None
    _FakeEncoder.available = True
    _FakeEncoder.fail_encode = False
    _FakeEncoder.fail_init = False
    _FakeEncoder.fails = 0
    _FakeEncoder.thread = 0
    _FakeResampler.last = None
    _FakeResampler.thread = 0
    # The retries are slow by design, the tests don't need to wait for the real timeouts
    monkeypatch.setattr(AudioSource, "_AudioSource__ERROR_DELAY", 0.05)
    monkeypatch.setattr(AudioSource, "_AudioSource__REPORT_AFTER", 2)
    # Without the TC358743 the source always uses the default rate
    return AudioSource("plughw:tc358743,0", "")


async def _wait(func, timeout: float=5.0) -> bool:  # type: ignore
    for _ in range(int(timeout / 0.01)):
        if func():
            return True
        await asyncio.sleep(0.01)
    return False


def _payload(frame: bytes) -> bytes:
    return frame[1:]  # The first byte is the discontinuity flag


def _gap(frame: bytes) -> bool:
    return bool(frame[0] & 1)


class _Client:
    def __init__(self) -> None:
        self.frames: list[bytes] = []
        self.errors: list[str] = []

    async def on_frame(self, data: bytes) -> None:
        self.frames.append(data)

    async def on_error(self, error: str) -> None:
        self.errors.append(error)


# =====
@pytest.mark.asyncio
async def test_ok__audio__opus(src: AudioSource) -> None:
    client = _Client()
    await src.start(client, "opus", client.on_frame, client.on_error)
    assert (await _wait(lambda: len(client.frames) >= 2))
    assert _payload(client.frames[0]).startswith(b"ERAW")

    cap = _FakeCapture.last
    assert cap is not None
    assert (cap.device, cap.hz, cap.channels, cap.frame_ms) \
        == ("plughw:tc358743,0", AudioSource.HZ, AudioSource.CHANNELS, AudioSource.FRAME_MS)
    assert _FakeResampler.last is None  # The rate matches, no resampling needed

    src.stop(client)
    enc = _FakeEncoder.last
    assert enc is not None
    assert (await _wait(lambda: enc.closed))  # The encoder is closed after the device
    assert (await _wait(lambda: not cap.opened))


@pytest.mark.asyncio
async def test_ok__audio__pcm(src: AudioSource) -> None:
    client = _Client()
    await src.start(client, "pcm", client.on_frame, client.on_error)
    assert (await _wait(lambda: len(client.frames) >= 2))
    assert _payload(client.frames[0]).startswith(b"RAW")
    src.stop(client)
    cap = _FakeCapture.last
    assert cap is not None
    assert (await _wait(lambda: not cap.opened))


@pytest.mark.asyncio
async def test_ok__audio__shared(src: AudioSource) -> None:
    (client1, client2) = (_Client(), _Client())
    await src.start(client1, "opus", client1.on_frame, client1.on_error)
    await src.start(client2, "pcm", client2.on_frame, client2.on_error)
    assert (await _wait(lambda: len(client1.frames) >= 3 and len(client2.frames) >= 3))
    assert _FakeCapture.count == 1  # The device is captured once for all the clients
    assert _payload(client1.frames[0]).startswith(b"ERAW")
    assert _payload(client2.frames[0]).startswith(b"RAW")

    enc = _FakeEncoder.last
    assert enc is not None
    src.stop(client1)  # The rest of the clients keep the capture alive
    count = len(client2.frames)
    calls = enc.calls
    assert (await _wait(lambda: len(client2.frames) > count + 2))
    assert enc.calls == calls  # Nobody needs the Opus anymore

    cap = _FakeCapture.last
    assert cap is not None
    assert cap.opened
    src.stop(client2)
    assert (await _wait(lambda: not cap.opened))


@pytest.mark.asyncio
async def test_ok__audio__resampling(src: AudioSource) -> None:
    _FakeCapture.real_hz = 44100  # The host sends the CD-quality audio
    client = _Client()
    await src.start(client, "opus", client.on_frame, client.on_error)
    assert (await _wait(lambda: len(client.frames) >= 2))
    assert _payload(client.frames[0]).startswith(b"ERRAW")  # Resampled, then encoded

    res = _FakeResampler.last
    assert res is not None
    assert (res.channels, res.in_hz, res.out_hz) == (AudioSource.CHANNELS, 44100, AudioSource.HZ)

    src.stop(client)
    assert (await _wait(lambda: res.closed))


@pytest.mark.asyncio
async def test_fail__audio__capture(src: AudioSource) -> None:
    client = _Client()
    await src.start(client, "pcm", client.on_frame, client.on_error)
    assert (await _wait(lambda: len(client.frames) >= 1))
    _FakeCapture.fail_read = True

    assert (await _wait(lambda: len(client.errors) > 0))
    assert "Input/output error" in client.errors[0]
    src.stop(client)


@pytest.mark.asyncio
async def test_fail__audio__format(src: AudioSource) -> None:
    client = _Client()
    with pytest.raises(AudioSourceError):
        await src.start(client, "mp3", client.on_frame, client.on_error)

    _FakeEncoder.available = False  # No libopus on the system
    with pytest.raises(AudioSourceError):
        await src.start(client, "opus", client.on_frame, client.on_error)


@pytest.mark.asyncio
async def test_ok__audio__info(src: AudioSource) -> None:
    info = (await src.get_info())
    assert info is not None
    assert info["formats"] == ["opus", "pcm"]
    assert (info["hz"], info["channels"]) == (AudioSource.HZ, AudioSource.CHANNELS)

    _FakeEncoder.available = False  # No libopus on the system
    info = (await src.get_info())
    assert info is not None
    assert info["formats"] == ["pcm"]

    _FakeCapture.available = False  # No HDMI capture device
    assert (await src.get_info()) is None


@pytest.mark.asyncio
async def test_fail__audio__disabled() -> None:
    src = AudioSource("", "")
    assert (await src.get_info()) is None
    client = _Client()
    with pytest.raises(AudioSourceError):
        await src.start(client, "opus", client.on_frame, client.on_error)


@pytest.mark.asyncio
async def test_ok__audio__queue_trim(src: AudioSource) -> None:
    # The stalled client gets the recent audio, not the half-second-old backlog
    gate = asyncio.Event()

    class _SlowClient(_Client):
        async def on_frame(self, data: bytes) -> None:
            await gate.wait()
            self.frames.append(data)

    (slow, fast) = (_SlowClient(), _Client())
    await src.start(slow, "pcm", slow.on_frame, slow.on_error)
    await src.start(fast, "pcm", fast.on_frame, fast.on_error)
    assert (await _wait(lambda: len(fast.frames) >= 40))  # The queue is long overflowed
    gate.set()
    assert (await _wait(lambda: len(slow.frames) >= 3))

    # The first frame was already taken from the queue by the sender before the stall,
    # the next one must be a recent frame and not the rest of the half-second backlog,
    # and it must be marked as a discontinuity for the client
    (first, second) = (int(_payload(slow.frames[0])[3:]), int(_payload(slow.frames[1])[3:]))
    assert second - first > 10
    assert _gap(slow.frames[1])
    assert not _gap(slow.frames[0])
    src.stop(slow)
    src.stop(fast)


@pytest.mark.asyncio
async def test_ok__audio__broken_frame(src: AudioSource, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(AudioSource, "_AudioSource__MAX_BROKEN", 4)
    client = _Client()
    await src.start(client, "opus", client.on_frame, client.on_error)
    assert (await _wait(lambda: len(client.frames) >= 2))
    enc = _FakeEncoder.last
    assert enc is not None

    _FakeEncoder.fail_encode = True  # A few broken frames don't restart the device
    assert (await _wait(lambda: _FakeEncoder.fails >= 2))
    _FakeEncoder.fail_encode = False
    count = len(client.frames)
    assert (await _wait(lambda: len(client.frames) > count + 2))
    assert _FakeCapture.count == 1
    assert not client.errors

    _FakeEncoder.fail_encode = True  # ... but the permanent error does
    assert (await _wait(lambda: _FakeCapture.count > 1))
    assert (await _wait(lambda: len(client.errors) > 0))
    assert "invalid argument" in client.errors[0]
    src.stop(client)


@pytest.mark.asyncio
async def test_ok__audio__empty_frames(src: AudioSource) -> None:
    # A recovered overrun gives no data, but the capture goes on
    client = _Client()
    await src.start(client, "pcm", client.on_frame, client.on_error)
    assert (await _wait(lambda: len(client.frames) >= 1))
    _FakeCapture.empty_reads = 5
    count = len(client.frames)
    assert (await _wait(lambda: len(client.frames) > count + 2))
    assert _FakeCapture.count == 1  # The device was not reopened
    assert not client.errors
    # ... and the empty reads were not sent to the client as empty frames
    assert all(_payload(frame).startswith(b"RAW") for frame in client.frames)
    src.stop(client)


@pytest.mark.asyncio
async def test_ok__audio__transient_error(src: AudioSource) -> None:
    # The device is busy for a moment when the user switches the video mode,
    # and the client should not be bothered with that
    _FakeCapture.fail_open = True
    client = _Client()
    await src.start(client, "pcm", client.on_frame, client.on_error)
    assert (await _wait(lambda: _FakeCapture.count >= 1))
    _FakeCapture.fail_open = False
    assert (await _wait(lambda: len(client.frames) >= 2))
    assert not client.errors

    # ... but a device that stays broken is reported
    _FakeCapture.fail_open = True
    _FakeCapture.fail_read = True
    assert (await _wait(lambda: len(client.errors) > 0))
    src.stop(client)


@pytest.mark.asyncio
async def test_ok__audio__codecs_in_thread(src: AudioSource) -> None:
    # Both of them load their libraries on the first use, and that runs ldconfig,
    # so they must not be constructed on the event loop
    _FakeCapture.real_hz = 44100
    client = _Client()
    await src.start(client, "opus", client.on_frame, client.on_error)
    assert (await _wait(lambda: len(client.frames) >= 2))
    assert _FakeResampler.thread not in (0, threading.get_ident())
    assert _FakeEncoder.thread not in (0, threading.get_ident())
    src.stop(client)


@pytest.mark.asyncio
async def test_ok__audio__codecs_cleanup(src: AudioSource) -> None:
    # The resampler is made first, it must not leak when the encoder fails
    _FakeCapture.real_hz = 44100
    _FakeEncoder.fail_init = True
    client = _Client()
    await src.start(client, "opus", client.on_frame, client.on_error)
    assert (await _wait(lambda: _FakeResampler.last is not None))
    res = _FakeResampler.last
    assert res is not None
    assert (await _wait(lambda: res.closed))
    src.stop(client)


@pytest.mark.asyncio
async def test_ok__audio__no_opus_logged_once(src: AudioSource, caplog: pytest.LogCaptureFixture) -> None:
    _FakeEncoder.available = False  # No libopus on the system
    with caplog.at_level("ERROR"):
        for _ in range(3):  # Every client connect asks for the info
            assert (await src.get_info()) is not None
    assert len([rec for rec in caplog.records if "No libopus" in rec.message]) == 1
