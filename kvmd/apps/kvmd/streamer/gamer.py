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


"""
Gamer Mode Streamer -- low-latency WebRTC video via GStreamer webrtcbin.

Replaces the ustreamer + Janus pipeline with a direct GStreamer pipeline
that establishes a 1:1 P2P WebRTC connection to the browser. No SFU
middleman, no jitter buffers, minimum latency.

The pipeline runs as a subprocess (contrib/gamer-mode/gamer_streamer.py)
managed by kvmd. Signaling is over the subprocess's stdin/stdout (JSON
lines): kvmd bridges those frames to/from the browser via its existing
/ws WebSocket.

Pipeline: v4l2src -> v4l2h264enc (or x264enc fallback) -> rtph264pay -> webrtcbin

Requires on the host: GStreamer 1.x with gst-plugins-bad (webrtcbin)
and PyGObject (gi.repository.Gst, GstSdp, GstWebRTC).
"""

import asyncio
import json
import os
import signal
import sys

from typing import Any
from typing import Awaitable
from typing import Callable

from ....logging import get_logger


SignalSender = Callable[[dict], Awaitable[None]]


class GamerStreamer:
    """Manages the gamer-mode GStreamer subprocess and bridges signaling.

    The expected lifecycle:
      1. ensure_start() with a send-to-browser callback.
      2. handle_browser_message(msg) for each SDP answer / ICE from the browser.
      3. ensure_stop() on teardown.
    """

    def __init__(
        self,
        device: str = "/dev/video0",
        fps: int = 60,
        bitrate: int = 5000,
        script_path: str = "",
    ) -> None:
        self.__device = device
        self.__fps = fps
        self.__bitrate = bitrate

        if script_path:
            self.__script_path = script_path
        else:
            # contrib/gamer-mode/gamer_streamer.py relative to this file.
            here = os.path.dirname(os.path.abspath(__file__))
            self.__script_path = os.path.normpath(os.path.join(
                here, "..", "..", "..", "..",
                "contrib", "gamer-mode", "gamer_streamer.py",
            ))

        self.__proc: (asyncio.subprocess.Process | None) = None
        self.__reader_task: (asyncio.Task | None) = None
        self.__send_to_browser: (SignalSender | None) = None

    # =====

    async def get_state(self) -> dict:
        alive = (self.__proc is not None and self.__proc.returncode is None)
        return {
            "streamer": {
                "online": alive,
                "encoder": "gstreamer-webrtcbin",
            },
            "features": {
                "quality": False,
                "resolution": False,
                "h264": True,
            },
            "params": {
                "desired_fps": self.__fps,
                "h264_bitrate": self.__bitrate,
            },
        }

    async def ensure_start(self, send_to_browser: SignalSender) -> None:
        """Start the GStreamer subprocess. send_to_browser is called for
        each SDP offer / ICE candidate / signal event from the pipeline."""
        if self.__proc and self.__proc.returncode is None:
            return

        self.__send_to_browser = send_to_browser
        cmd = [
            sys.executable, self.__script_path,
            "--device", self.__device,
            "--fps", str(self.__fps),
            "--bitrate", str(self.__bitrate),
            # No --port arg -> stdio mode.
        ]
        logger = get_logger(0)
        logger.info("Starting gamer-mode streamer: %s", " ".join(cmd))
        self.__proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self.__reader_task = asyncio.create_task(self.__read_loop())

    async def ensure_stop(self) -> None:
        logger = get_logger(0)
        if self.__reader_task:
            self.__reader_task.cancel()
            try:
                await self.__reader_task
            except asyncio.CancelledError:
                pass
            self.__reader_task = None
        if self.__proc and self.__proc.returncode is None:
            logger.info("Stopping gamer-mode streamer (pid=%d)", self.__proc.pid)
            try:
                self.__proc.send_signal(signal.SIGTERM)
                await asyncio.wait_for(self.__proc.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning("Gamer-mode streamer didn't exit, killing")
                self.__proc.kill()
                await self.__proc.wait()
        self.__proc = None
        self.__send_to_browser = None

    async def ensure_restart(self) -> None:
        sender = self.__send_to_browser
        await self.ensure_stop()
        if sender:
            await self.ensure_start(sender)

    async def handle_browser_message(self, msg: dict) -> None:
        """Forward an SDP answer / ICE candidate from the browser into
        the GStreamer subprocess via its stdin."""
        if not self.__proc or not self.__proc.stdin:
            return
        line = (json.dumps(msg) + "\n").encode("utf-8")
        try:
            self.__proc.stdin.write(line)
            await self.__proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            get_logger(0).warning("Gamer-mode streamer stdin closed")

    # =====

    def set_params(self, params: dict) -> None:
        if "desired_fps" in params:
            self.__fps = int(params["desired_fps"])
        if "h264_bitrate" in params:
            self.__bitrate = int(params["h264_bitrate"])

    def get_params(self) -> dict:
        return {
            "desired_fps": self.__fps,
            "h264_bitrate": self.__bitrate,
        }

    # =====

    async def __read_loop(self) -> None:
        """Read JSON lines from the subprocess stdout and forward to the browser."""
        logger = get_logger(0)
        assert self.__proc and self.__proc.stdout
        try:
            while True:
                line = await self.__proc.stdout.readline()
                if not line:
                    logger.info("Gamer-mode streamer stdout EOF")
                    break
                try:
                    msg = json.loads(line.decode("utf-8").strip())
                except json.JSONDecodeError as ex:
                    logger.warning("Bad JSON from gamer-mode streamer: %s", ex)
                    continue
                if self.__send_to_browser:
                    try:
                        await self.__send_to_browser(msg)
                    except Exception:  # pylint: disable=broad-except
                        logger.exception("send_to_browser failed")
        except asyncio.CancelledError:
            raise
        except Exception:  # pylint: disable=broad-except
            logger.exception("Gamer-mode reader loop crashed")
