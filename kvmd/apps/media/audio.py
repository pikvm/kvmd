# ========================================================================== #
#                                                                            #
#    KVMD - The main PiKVM daemon.                                           #
#                                                                            #
#    Copyright (C) 2020  Maxim Devaev <mdevaev@gmail.com>                    #
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
import fcntl
import struct
import asyncio
import contextlib
import dataclasses

from typing import Callable
from typing import Awaitable

from ...logging import get_logger

from ... import tools
from ... import aiotools

from ...audio import AudioError
from ...audio import PcmCapture
from ...audio import OpusEncoder
from ...audio import Resampler


# =====
class AudioSourceError(Exception):
    pass


# =====
_VIDIOC_G_CTRL = 0xC008561B  # _IOWR("V", 27, struct v4l2_control)
_TC358743_CID_AUDIO_SAMPLING_RATE = 0x00981980
_TC358743_CID_AUDIO_PRESENT = 0x00981981


def _get_v4l2_ctrl(fd: int, cid: int) -> int:
    buf = struct.pack("Ii", cid, 0)
    return struct.unpack("Ii", fcntl.ioctl(fd, _VIDIOC_G_CTRL, buf))[1]


@dataclasses.dataclass
class _Sub:
    fmt:      str
    on_frame: Callable[[bytes], Awaitable[None]]
    on_error: Callable[[str], Awaitable[None]]
    queue:    asyncio.Queue[bytes]
    task:     (asyncio.Task | None) = None
    gap:      bool = False  # Something was dropped before the next frame


# =====
class AudioSource:
    # Captures the host audio from the HDMI capture device and sends it to the clients.
    # The device is captured once and the audio is shared between all of them.

    HZ = 48000
    CHANNELS = 2
    FRAME_MS = 20
    BITRATE = 128000  # The same as uStreamer uses for the WebRTC audio

    FORMAT_OPUS = "opus"
    FORMAT_PCM = "pcm"
    FORMATS = [FORMAT_OPUS, FORMAT_PCM]

    __QUEUE_SIZE = 25  # 0.5 seconds of the audio
    __QUEUE_TRIM = 5   # ... and 0.1 seconds to keep when the client can't eat it
    __MAX_BROKEN = 10  # Broken frames (a good one heals a single failure) before restarting
    __BROKEN_LOG = 25  # ... and how often to complain about them
    __ERROR_DELAY = 1.0
    __REPORT_AFTER = 5  # Failed attempts before bothering the client with an error
    __ERROR_LOG = 30    # ... and how often to repeat it in the log

    def __init__(self, device: str, tc358743: str) -> None:
        self.__device = device
        self.__tc358743 = tc358743

        self.__subs: dict[object, _Sub] = {}
        self.__task: (asyncio.Task | None) = None
        self.__drops = 0
        self.__no_opus = False

    # =====

    async def get_info(self) -> (dict | None):
        if not self.__device:
            return None
        if not (await asyncio.to_thread(PcmCapture.probe, self.__device)):
            return None
        formats = list(self.FORMATS)
        if not (await asyncio.to_thread(OpusEncoder.probe)):
            # Without libopus the client should not even try to ask for Opus
            if not self.__no_opus:
                self.__no_opus = True
                get_logger(0).error("No libopus, the audio will use the raw PCM")
            formats.remove(self.FORMAT_OPUS)
        return {
            "formats":  formats,
            "hz":       self.HZ,
            "channels": self.CHANNELS,
            "frame_ms": self.FRAME_MS,
        }

    async def start(
        self,
        owner: object,
        fmt: str,
        on_frame: Callable[[bytes], Awaitable[None]],
        on_error: Callable[[str], Awaitable[None]],
    ) -> None:

        if not self.__device:
            raise AudioSourceError("The audio is not configured")
        if fmt not in self.FORMATS:
            raise AudioSourceError(f"Unsupported audio format: {fmt}")
        if fmt == self.FORMAT_OPUS and not (await asyncio.to_thread(OpusEncoder.probe)):
            raise AudioSourceError("There is no Opus support on the server")

        self.stop(owner)  # Restart if the client has changed the format
        sub = _Sub(fmt, on_frame, on_error, asyncio.Queue(self.__QUEUE_SIZE))
        sub.task = asyncio.create_task(self.__sending(sub))
        self.__subs[owner] = sub
        if self.__task is None or self.__task.done():
            self.__task = asyncio.create_task(self.__capturing())

    def stop(self, owner: object) -> None:
        sub = self.__subs.pop(owner, None)
        if sub is not None and sub.task is not None:
            sub.task.cancel()

    # =====

    def __feed(self, sub: _Sub, data: bytes) -> None:
        try:
            sub.queue.put_nowait(data)
        except asyncio.QueueFull:
            # The client can't eat the audio fast enough. Dropping the oldest frames restores
            # the latency, but the recent ones are kept: they are the sound the user will hear.
            with contextlib.suppress(asyncio.QueueEmpty):
                while sub.queue.qsize() > self.__QUEUE_TRIM:
                    sub.queue.get_nowait()
                    self.__drops += 1
                    sub.gap = True
            with contextlib.suppress(asyncio.QueueFull):
                sub.queue.put_nowait(data)

    async def __sending(self, sub: _Sub) -> None:
        while True:
            data = (await sub.queue.get())
            # The first byte marks the discontinuity, the client can't detect it itself
            flags = (1 if sub.gap else 0)
            sub.gap = False
            with contextlib.suppress(Exception):
                # The dead client will be removed by the server
                await sub.on_frame(flags.to_bytes() + data)

    async def __report(self, error: str) -> None:
        for sub in list(self.__subs.values()):
            with contextlib.suppress(Exception):
                await sub.on_error(error)

    def __get_host_hz(self) -> int:
        # The HDMI audio rate is defined by the host, zero means no audio at all
        if not self.__tc358743:
            return self.HZ
        fd = os.open(self.__tc358743, os.O_RDWR)
        try:
            if not _get_v4l2_ctrl(fd, _TC358743_CID_AUDIO_PRESENT):
                return 0
            return max(_get_v4l2_ctrl(fd, _TC358743_CID_AUDIO_SAMPLING_RATE), 0)
        finally:
            os.close(fd)

    async def __capturing(self) -> None:
        logger = get_logger(0)
        reported = ""
        quiet = False
        fails = 0
        try:
            while self.__subs:
                try:
                    hz = (await asyncio.to_thread(self.__get_host_hz))
                    if hz <= 0:
                        if not quiet:
                            quiet = True
                            logger.info("The host doesn't send any audio")
                        await asyncio.sleep(self.__ERROR_DELAY)
                        continue
                    quiet = False
                    await self.__capture(hz)
                    (reported, fails) = ("", 0)
                except (AudioError, OSError) as ex:
                    error = tools.efmt(ex)
                    fails += 1
                    if fails % self.__ERROR_LOG == 1:
                        logger.error("Audio capture error: %s", error)
                    if fails >= self.__REPORT_AFTER and reported != error:
                        # The device stays busy for a few seconds when the user switches
                        # the video mode, so the transient errors are not worth an alert
                        reported = error
                        await self.__report(error)
                    await asyncio.sleep(self.__ERROR_DELAY)
                except Exception:
                    logger.exception("Unexpected audio capture error")
                    await asyncio.sleep(self.__ERROR_DELAY)
        finally:
            logger.info("Audio capture finished")

    def __make_codecs(self, real: int) -> "tuple[Resampler | None, OpusEncoder | None]":
        res: (Resampler | None) = None
        if real != self.HZ:
            # Opus can't eat an arbitrary rate, but the host can send 44.1 kHz for example
            res = Resampler(self.CHANNELS, real, self.HZ, (real * self.FRAME_MS) // 1000)
        try:
            enc = (OpusEncoder(self.HZ, self.CHANNELS, self.BITRATE) if OpusEncoder.probe() else None)
        except Exception:
            if res is not None:
                res.close()
            raise
        return (res, enc)

    def __distribute(self, data: bytes, enc: (OpusEncoder | None)) -> None:
        encoded: (bytes | None) = None
        for sub in list(self.__subs.values()):
            if sub.fmt != self.FORMAT_OPUS:
                self.__feed(sub, data)
            elif enc is not None:
                if encoded is None:
                    encoded = enc.encode(data)
                self.__feed(sub, encoded)

    async def __capture(self, hz: int) -> None:
        logger = get_logger(0)
        cap = PcmCapture(self.__device, hz, self.CHANNELS, self.FRAME_MS)
        real = (await asyncio.to_thread(cap.open))
        res: (Resampler | None) = None
        enc: (OpusEncoder | None) = None
        try:
            # Both of them load their libraries on the first use, and that runs ldconfig
            (res, enc) = (await asyncio.to_thread(self.__make_codecs, real))
            logger.info("Audio capture started on %s: %d Hz%s; ALSA period=%d buffer=%d",
                        self.__device, real, ("" if res is None else f" -> {self.HZ} Hz"),
                        cap.period, cap.buffer)
            await self.__capturing_loop(cap, res, enc, hz)
        finally:
            try:
                await aiotools.shield_fg(asyncio.to_thread(cap.close))
            except Exception:
                logger.exception("Can't close the audio capture")
            finally:
                if res is not None:
                    res.close()
                if enc is not None:
                    enc.close()
            logger.info("Audio capture stopped; overruns: %d, skipped: %d,"
                        " stalled: %d, dropped: %d",
                        cap.overruns, cap.skipped, cap.stalled, self.__drops)
            self.__drops = 0

    async def __capturing_loop(
        self,
        cap: PcmCapture,
        res: (Resampler | None),
        enc: (OpusEncoder | None),
        hz: int,
    ) -> None:

        logger = get_logger(0)
        (count, broken) = (0, 0)
        while self.__subs:
            data = (await aiotools.shield_fg(asyncio.to_thread(cap.read)))
            if data:
                try:
                    self.__distribute((data if res is None else res.process(data)), enc)
                except AudioError as ex:
                    # A single broken frame is not a reason to reopen the device,
                    # but a permanently failing encoder is
                    broken += 1
                    if broken % self.__BROKEN_LOG == 1:
                        logger.error("Can't process the audio frame: %s", tools.efmt(ex))
                    if broken >= self.__MAX_BROKEN:
                        raise
                    continue
                broken = max(broken - 1, 0)
            count += 1
            if count * self.FRAME_MS >= 1000:  # Check the host rate every second
                count = 0
                if (await asyncio.to_thread(self.__get_host_hz)) != hz:
                    logger.info("The host has changed the audio rate")
                    break
