#!/usr/bin/env python3
"""
PiKVM Gamer Mode Streamer - Low-latency WebRTC video via GStreamer webrtcbin.

Replaces the Janus→ustreamer path with a direct GStreamer pipeline:
  v4l2src → v4l2h264enc → rtph264pay → webrtcbin → browser

WebRTC signaling is handled via a simple aiohttp WebSocket server.
The browser connects, exchanges SDP offer/answer and ICE candidates,
and receives the H.264 stream directly — no Janus middleman.

Usage:
  python3 gamer_streamer.py [--device /dev/video0] [--port 8765] [--fps 60]

Dependencies:
  - GStreamer 1.x with gst-plugins-bad (for webrtcbin)
  - PyGObject (gi.repository.Gst, GstSdp, GstWebRTC)
  - aiohttp
"""

import argparse
import asyncio
import json
import logging
import sys

import gi
gi.require_version("Gst", "1.0")
gi.require_version("GstSdp", "1.0")
gi.require_version("GstWebRTC", "1.0")
from gi.repository import Gst, GstSdp, GstWebRTC, GLib

import aiohttp
from aiohttp import web

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("gamer")

Gst.init(None)


class GamerPipeline:
    def __init__(self, device: str, fps: int, bitrate: int):
        self._device = device
        self._fps = fps
        self._bitrate = bitrate
        self._pipe = None
        self._webrtcbin = None
        self._ws = None
        self._loop = None

    def build(self, loop: asyncio.AbstractEventLoop):
        self._loop = loop

        encoder = self._detect_encoder()
        pipe_str = (
            f"v4l2src device={self._device} "
            f"! video/x-raw,framerate={self._fps}/1 "
            f"! videoconvert "
            f"! {encoder} "
            f"! video/x-h264,profile=constrained-baseline "
            f"! rtph264pay config-interval=-1 pt=96 "
            f"! application/x-rtp,media=video,encoding-name=H264,payload=96 "
            f"! webrtcbin name=webrtc bundle-policy=max-bundle "
            f"  stun-server=stun://stun.l.google.com:19302"
        )
        logger.info("Pipeline: %s", pipe_str)
        self._pipe = Gst.parse_launch(pipe_str)
        self._webrtcbin = self._pipe.get_by_name("webrtc")

        self._webrtcbin.connect("on-negotiation-needed", self._on_negotiation_needed)
        self._webrtcbin.connect("on-ice-candidate", self._on_ice_candidate)
        self._webrtcbin.connect("pad-added", self._on_pad_added)

        # Disable jitter buffer for minimum latency
        self._webrtcbin.set_property("latency", 0)

    def _detect_encoder(self) -> str:
        hw = Gst.ElementFactory.find("v4l2h264enc")
        if hw:
            logger.info("Using v4l2h264enc (Pi hardware encoder)")
            return (
                f"v4l2h264enc extra-controls=\"encode,h264_profile=1,h264_level=11,"
                f"video_bitrate={self._bitrate * 1000}\" "
                f"! video/x-h264,level=(string)4"
            )
        logger.info("v4l2h264enc not available, falling back to x264enc (software)")
        return (
            f"x264enc tune=zerolatency bitrate={self._bitrate} "
            f"speed-preset=ultrafast key-int-max={self._fps}"
        )

    def start(self):
        self._pipe.set_state(Gst.State.PLAYING)
        logger.info("Pipeline started")

    def stop(self):
        if self._pipe:
            self._pipe.set_state(Gst.State.NULL)
            logger.info("Pipeline stopped")

    def set_ws(self, ws):
        self._ws = ws

    def _on_negotiation_needed(self, webrtcbin):
        logger.info("Negotiation needed — creating offer")
        promise = Gst.Promise.new_with_change_func(self._on_offer_created)
        webrtcbin.emit("create-offer", None, promise)

    def _on_offer_created(self, promise):
        promise.wait()
        reply = promise.get_reply()
        offer = reply.get_value("offer")
        self._webrtcbin.emit("set-local-description", offer, None)
        sdp_text = offer.sdp.as_text()
        logger.info("SDP offer created (%d chars)", len(sdp_text))
        if self._ws and self._loop:
            asyncio.run_coroutine_threadsafe(
                self._ws.send_json({"type": "offer", "sdp": sdp_text}),
                self._loop,
            )

    def _on_ice_candidate(self, webrtcbin, mline_index, candidate):
        if self._ws and self._loop:
            asyncio.run_coroutine_threadsafe(
                self._ws.send_json({
                    "type": "ice",
                    "candidate": candidate,
                    "sdpMLineIndex": mline_index,
                }),
                self._loop,
            )

    def _on_pad_added(self, webrtcbin, pad):
        logger.info("Pad added: %s", pad.get_name())

    async def handle_message(self, msg: dict):
        if msg["type"] == "answer":
            sdp = msg["sdp"]
            logger.info("Received SDP answer (%d chars)", len(sdp))
            res, sdpmsg = GstSdp.SDPMessage.new()
            GstSdp.sdp_message_parse_buffer(bytes(sdp, "utf-8"), sdpmsg)
            answer = GstWebRTC.WebRTCSessionDescription.new(
                GstWebRTC.WebRTCSDPType.ANSWER, sdpmsg
            )
            self._webrtcbin.emit("set-remote-description", answer, None)

        elif msg["type"] == "ice":
            candidate = msg["candidate"]
            sdp_mline_index = msg["sdpMLineIndex"]
            self._webrtcbin.emit("add-ice-candidate", sdp_mline_index, candidate)


class GamerServer:
    def __init__(self, device: str, fps: int, bitrate: int, port: int):
        self._device = device
        self._fps = fps
        self._bitrate = bitrate
        self._port = port
        self._pipeline = None

    async def _ws_handler(self, request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        logger.info("WebSocket client connected")

        loop = asyncio.get_event_loop()

        # Build a fresh pipeline for each connection (1:1 streaming)
        if self._pipeline:
            self._pipeline.stop()

        self._pipeline = GamerPipeline(self._device, self._fps, self._bitrate)
        self._pipeline.build(loop)
        self._pipeline.set_ws(ws)
        self._pipeline.start()

        try:
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    await self._pipeline.handle_message(data)
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    logger.error("WS error: %s", ws.exception())
                    break
        finally:
            logger.info("WebSocket client disconnected")
            self._pipeline.stop()
            self._pipeline = None

        return ws

    async def _index_handler(self, request):
        return web.Response(text=_INDEX_HTML, content_type="text/html")

    def run(self):
        app = web.Application()
        app.router.add_get("/", self._index_handler)
        app.router.add_get("/ws", self._ws_handler)
        logger.info("Gamer Mode streamer on port %d (device=%s, fps=%d, bitrate=%d)",
                     self._port, self._device, self._fps, self._bitrate)
        web.run_app(app, port=self._port)


_INDEX_HTML = """<!DOCTYPE html>
<html>
<head>
<title>PiKVM Gamer Mode</title>
<style>
body { margin: 0; background: #000; display: flex; justify-content: center; align-items: center; height: 100vh; }
video { max-width: 100vw; max-height: 100vh; }
#status { position: fixed; top: 10px; left: 10px; color: #0f0; font-family: monospace; font-size: 14px; }
</style>
</head>
<body>
<div id="status">Connecting...</div>
<video id="video" autoplay playsinline muted></video>
<script>
const video = document.getElementById('video');
const status = document.getElementById('status');
let pc = null;

function connect() {
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const ws = new WebSocket(proto + '//' + location.host + '/ws');

    ws.onopen = () => { status.textContent = 'WebSocket connected, waiting for offer...'; };

    ws.onmessage = async (ev) => {
        const msg = JSON.parse(ev.data);

        if (msg.type === 'offer') {
            status.textContent = 'Got offer, creating answer...';
            pc = new RTCPeerConnection({
                iceServers: [{ urls: 'stun:stun.l.google.com:19302' }]
            });

            pc.ontrack = (event) => {
                status.textContent = 'Stream active!';
                video.srcObject = event.streams[0];
            };

            pc.onicecandidate = (event) => {
                if (event.candidate) {
                    ws.send(JSON.stringify({
                        type: 'ice',
                        candidate: event.candidate.candidate,
                        sdpMLineIndex: event.candidate.sdpMLineIndex
                    }));
                }
            };

            pc.oniceconnectionstatechange = () => {
                status.textContent = 'ICE: ' + pc.iceConnectionState;
            };

            // Modify SDP to disable jitter buffer (low latency)
            let sdp = msg.sdp;

            await pc.setRemoteDescription({ type: 'offer', sdp: sdp });
            const answer = await pc.createAnswer();
            await pc.setLocalDescription(answer);
            ws.send(JSON.stringify({ type: 'answer', sdp: answer.sdp }));
            status.textContent = 'Answer sent, waiting for stream...';
        }

        else if (msg.type === 'ice') {
            if (pc && msg.candidate) {
                await pc.addIceCandidate({
                    candidate: msg.candidate,
                    sdpMLineIndex: msg.sdpMLineIndex
                });
            }
        }
    };

    ws.onclose = () => {
        status.textContent = 'Disconnected. Reconnecting in 2s...';
        setTimeout(connect, 2000);
    };

    ws.onerror = () => { ws.close(); };
}

connect();
</script>
</body>
</html>
"""


def main():
    parser = argparse.ArgumentParser(description="PiKVM Gamer Mode Streamer")
    parser.add_argument("--device", default="/dev/video0", help="V4L2 device")
    parser.add_argument("--port", type=int, default=8765, help="WebSocket port")
    parser.add_argument("--fps", type=int, default=60, help="Target framerate")
    parser.add_argument("--bitrate", type=int, default=5000, help="H.264 bitrate (kbps)")
    args = parser.parse_args()

    server = GamerServer(args.device, args.fps, args.bitrate, args.port)
    server.run()


if __name__ == "__main__":
    main()
