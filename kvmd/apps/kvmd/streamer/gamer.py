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
Gamer Mode Streamer — low-latency WebRTC video via GStreamer webrtcbin.

Replaces the ustreamer + Janus pipeline with a direct GStreamer pipeline
that establishes a 1:1 P2P WebRTC connection to the browser. No SFU
middleman, no jitter buffers, minimum latency.

The pipeline runs as a subprocess managed by kvmd. WebRTC signaling
(SDP offer/answer, ICE candidates) is bridged through kvmd's existing
WebSocket connection to the browser.

Pipeline: v4l2src → v4l2h264enc (or x264enc fallback) → rtph264pay → webrtcbin

Requires: GStreamer 1.x, gst-plugins-bad (webrtcbin), PyGObject.
"""

import asyncio
import json
import logging
import subprocess
import os
import signal

from typing import Any

from ....logging import get_logger


class GamerStreamer:
    """Manages the gamer-mode GStreamer subprocess and bridges signaling."""

    def __init__(
        self,
        device: str = "/dev/video0",
        fps: int = 60,
        bitrate: int = 5000,
    ) -> None:
        self.__device = device
        self.__fps = fps
        self.__bitrate = bitrate
        self.__proc: (subprocess.Popen | None) = None  # type: ignore
        self.__ws_send = None  # callback to send to browser WS
        self.__reader_task = None

    async def get_state(self) -> dict:
        return {
            "streamer": {
                "online": self.__proc is not None and self.__proc.poll() is None,
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

    async def ensure_start(self, ws_send_callback: Any = None) -> None:
        """Start the GStreamer subprocess if not already running."""
        if self.__proc and self.__proc.poll() is None:
            return

        self.__ws_send = ws_send_callback

        script_dir = os.path.dirname(os.path.abspath(__file__))
        script = os.path.join(script_dir, "..", "..", "..", "contrib", "gamer-mode", "gamer_streamer.py")

        # The subprocess runs the standalone gamer_streamer.py which handles
        # GStreamer + its own signaling WebSocket on a local port. kvmd proxies
        # the browser's WebRTC signaling messages to this local port.
        cmd = [
            "python3", script,
            "--device", self.__device,
            "--fps", str(self.__fps),
            "--bitrate", str(self.__bitrate),
            "--port", "8765",
        ]

        logger = get_logger(0)
        logger.info("Starting gamer-mode streamer: %s", " ".join(cmd))
        self.__proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

    async def ensure_stop(self) -> None:
        if self.__proc:
            logger = get_logger(0)
            logger.info("Stopping gamer-mode streamer (pid=%d)", self.__proc.pid)
            self.__proc.send_signal(signal.SIGTERM)
            try:
                self.__proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.__proc.kill()
            self.__proc = None

    async def ensure_restart(self) -> None:
        await self.ensure_stop()
        await self.ensure_start(self.__ws_send)

    def set_params(self, params: dict) -> None:
        if "desired_fps" in params:
            self.__fps = params["desired_fps"]
        if "h264_bitrate" in params:
            self.__bitrate = params["h264_bitrate"]

    def get_params(self) -> dict:
        return {
            "desired_fps": self.__fps,
            "h264_bitrate": self.__bitrate,
        }

    async def poll_state(self):
        """Yield state changes (placeholder for compatibility)."""
        while True:
            yield await self.get_state()
            await asyncio.sleep(1.0)
