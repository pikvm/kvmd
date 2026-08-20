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


import asyncio
import contextlib

from typing import Callable
from typing import Awaitable

from ...logging import get_logger

from ... import tools
from ... import aiotools

from ...audio import AudioError
from ...audio import PcmPlayback
from ...audio import OpusDecoder


# =====
class MicError(Exception):
    pass


class MicBusyError(MicError):
    def __init__(self) -> None:
        super().__init__("The microphone is busy by another client")


# =====
class MicSink:  # pylint: disable=too-many-instance-attributes
    # Plays the client's microphone to the USB audio gadget.
    # Only one client can use the playback device at the same time.

    HZ = 48000
    CHANNELS = 1
    FRAME_MS = 20

    FORMAT_OPUS = "opus"
    FORMAT_PCM = "pcm"
    FORMATS = [FORMAT_OPUS, FORMAT_PCM]

    __QUEUE_SIZE = 25  # 0.5 seconds of the audio
    __QUEUE_TRIM = 5   # ... and 0.1 seconds to keep when the client sends it too fast
    __MAX_FRAME = 12288  # 120ms of the raw mono audio, the longest Opus frame is shorter
    # The websocket handlers are called one by one, so a long wait here would block
    # the client's pings and the browser would drop the connection
    __STOP_TIMEOUT = 0.5

    def __init__(self, device: str, latency: float) -> None:
        self.__device = device
        self.__latency = latency

        self.__owner: (object | None) = None
        self.__fmt = ""
        self.__no_opus = False
        self.__drops = 0
        self.__queue: asyncio.Queue[bytes] = asyncio.Queue(self.__QUEUE_SIZE)
        self.__task: (asyncio.Task | None) = None
        self.__lock = asyncio.Lock()

    # =====

    async def get_info(self) -> (dict | None):
        if not self.__device:
            return None
        if not (await asyncio.to_thread(PcmPlayback.probe, self.__device)):
            return None
        formats = list(self.FORMATS)
        if not (await asyncio.to_thread(OpusDecoder.probe)):
            # Without libopus the client should not even try to send Opus
            if not self.__no_opus:
                self.__no_opus = True
                get_logger(0).error("No libopus, the microphone will use the raw PCM")
            formats.remove(self.FORMAT_OPUS)
        return {
            "formats":  formats,
            "hz":       self.HZ,
            "channels": self.CHANNELS,
            "frame_ms": self.FRAME_MS,
        }

    async def start(self, owner: object, fmt: str, on_error: Callable[[str], Awaitable[None]]) -> None:
        if not self.__device:
            raise MicError("The microphone is not configured")
        if fmt not in self.FORMATS:
            raise MicError(f"Unsupported microphone format: {fmt}")
        async with self.__lock:
            if self.__owner is owner:
                if fmt != self.__fmt:
                    raise MicError("The microphone is already started with another format")
                return
            if self.__owner is not None:
                raise MicBusyError()
            if self.__task is not None:
                # The previous session is stopping right now, it should be fast
                (done, _) = await asyncio.wait([self.__task], timeout=self.__STOP_TIMEOUT)
                if not done:
                    raise MicBusyError()
                self.__task = None

            pcm = PcmPlayback(self.__device, self.HZ, self.CHANNELS, self.__latency)
            dec = (OpusDecoder(self.HZ, self.CHANNELS) if fmt == self.FORMAT_OPUS else None)
            try:
                # Shielded: a cancelled open() would leave the device to nobody.
                # The cancellation arrives after the thread is done, so BaseException.
                await aiotools.shield_fg(asyncio.to_thread(pcm.open))
            except BaseException:
                pcm.close()
                if dec is not None:
                    dec.close()
                raise

            self.__owner = owner
            self.__fmt = fmt
            self.__drain()
            self.__task = asyncio.create_task(self.__playing(pcm, dec, on_error))

    def stop(self, owner: object) -> None:
        if self.__owner is not None and self.__owner is owner:
            self.__owner = None
            self.__drain()
            self.__queue.put_nowait(b"")  # Stop signal for the playing task

    def feed(self, owner: object, data: bytes) -> None:
        # A huge frame would occupy the playback device for its whole duration,
        # and the device is shared between all the clients
        if self.__owner is not owner or not (0 < len(data) <= self.__MAX_FRAME):
            return
        try:
            self.__queue.put_nowait(data)
        except asyncio.QueueFull:
            # The client sends the audio faster than we can play it. Dropping the oldest frames
            # restores the latency, but the recent ones are kept: they are the actual speech.
            with contextlib.suppress(asyncio.QueueEmpty):
                while self.__queue.qsize() > self.__QUEUE_TRIM:
                    self.__queue.get_nowait()
                    self.__drops += 1
            with contextlib.suppress(asyncio.QueueFull):
                self.__queue.put_nowait(data)

    # =====

    def __drain(self) -> None:
        with contextlib.suppress(asyncio.QueueEmpty):
            while True:
                self.__queue.get_nowait()

    async def __playing(
        self,
        pcm: PcmPlayback,
        dec: (OpusDecoder | None),
        on_error: Callable[[str], Awaitable[None]],
    ) -> None:

        logger = get_logger(0)
        logger.info("Microphone playback started on %s", self.__device)
        error = ""
        broken = 0
        try:
            while True:
                data = await self.__queue.get()
                if len(data) == 0:  # Stop signal
                    break
                if dec is not None:
                    try:
                        data = dec.decode(data)
                    except AudioError as ex:
                        # The broken frame is not a reason to stop the playback
                        broken += 1
                        if broken == 1:
                            logger.error("Can't decode the microphone frame: %s", tools.efmt(ex))
                        continue
                await aiotools.shield_fg(asyncio.to_thread(pcm.write, data))
        except AudioError as ex:
            error = tools.efmt(ex)
            logger.error("Microphone playback error: %s", error)
        except Exception as ex:
            error = tools.efmt(ex)
            logger.exception("Unexpected microphone playback error")
        finally:
            self.__owner = None
            try:
                await aiotools.shield_fg(asyncio.to_thread(pcm.close))
            except Exception:
                logger.exception("Can't close the microphone playback")
            finally:
                if dec is not None:
                    dec.close()
            logger.info("Microphone playback stopped; broken: %d, dropped: %d,"
                        " underruns: %d, stalled: %d",
                        broken, self.__drops, pcm.underruns, pcm.stalled)
            self.__drops = 0
        if error:
            with contextlib.suppress(Exception):
                await on_error(error)
