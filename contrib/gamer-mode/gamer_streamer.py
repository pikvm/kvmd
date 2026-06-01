#!/usr/bin/env python3
"""
PiKVM Gamer Mode Streamer - Low-latency WebRTC video via GStreamer webrtcbin.

Replaces the Janus->ustreamer path with a direct GStreamer pipeline:
  v4l2src -> v4l2h264enc -> rtph264pay -> webrtcbin -> browser

Two signaling transports:
  --stdio (default): JSON lines on stdin/stdout. kvmd parents this process
    and bridges to the browser via its existing /ws WebSocket.
  --port N (legacy spike mode): standalone aiohttp WS server + embedded
    HTML client. Useful for development without kvmd.

Dependencies:
  - GStreamer 1.x with gst-plugins-bad (for webrtcbin)
  - PyGObject (gi.repository.Gst, GstSdp, GstWebRTC)
  - aiohttp (only when --port is used)
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

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stderr)
logger = logging.getLogger("gamer")

Gst.init(None)


class GamerPipeline:
    def __init__(self, device: str, fps: int, bitrate: int):
        self._device = device
        self._fps = fps
        self._bitrate = bitrate
        self._pipe = None
        self._webrtcbin = None
        self._send = None  # async callable: (dict) -> awaitable
        self._loop = None
        self._signal_lost = False

    def build(self, loop: asyncio.AbstractEventLoop):
        self._loop = loop

        encoder = self._detect_encoder()
        # input-selector lets us swap between live v4l2 capture and a black
        # placeholder source on the fly, so a V4 port switch / EDID drop
        # doesn't tear down the WebRTC session. We point at video2 first;
        # the bus error handler swaps to videotestsrc on capture failure.
        # Both branches feed the same encoder->rtp->webrtcbin downstream.
        pipe_str = (
            f"input-selector name=sel "
            f"! videoconvert ! videoscale "
            f"! video/x-raw,framerate={self._fps}/1 "
            f"! {encoder} "
            f"! video/x-h264,profile=constrained-baseline "
            f"! rtph264pay config-interval=-1 pt=96 "
            f"! application/x-rtp,media=video,encoding-name=H264,payload=96 "
            f"! webrtcbin name=webrtc bundle-policy=max-bundle "
            f"  stun-server=stun://stun.l.google.com:19302 "
            f"v4l2src device={self._device} name=cap "
            f"! video/x-raw,framerate={self._fps}/1 "
            f"! sel.sink_0 "
            f"videotestsrc pattern=black is-live=true name=fallback "
            f"! video/x-raw,framerate={self._fps}/1,width=1280,height=720 "
            f"! sel.sink_1"
        )
        logger.info("Pipeline: %s", pipe_str)
        self._pipe = Gst.parse_launch(pipe_str)
        self._webrtcbin = self._pipe.get_by_name("webrtc")
        self._selector = self._pipe.get_by_name("sel")

        # Start on the live capture branch (sink_0).
        sink0 = self._selector.get_static_pad("sink_0")
        self._selector.set_property("active-pad", sink0)

        self._webrtcbin.connect("on-negotiation-needed", self._on_negotiation_needed)
        self._webrtcbin.connect("on-ice-candidate", self._on_ice_candidate)
        self._webrtcbin.connect("pad-added", self._on_pad_added)
        self._webrtcbin.set_property("latency", 0)

        # Watch the bus for v4l2src errors -> swap to black fallback,
        # then poll for the device coming back and swap back.
        bus = self._pipe.get_bus()
        bus.add_signal_watch()
        bus.connect("message::error", self._on_bus_error)

    def _on_bus_error(self, _bus, message):
        src_name = message.src.get_name() if message.src else "?"
        err, dbg = message.parse_error()
        logger.warning("Bus error from %s: %s", src_name, err)
        # If the capture source died (port switch / EDID drop), swap to the
        # black fallback branch without tearing down the encoder/webrtc.
        if src_name in ("cap", "v4l2src0") and not self._signal_lost:
            self._signal_lost = True
            self._emit({"type": "signal_lost", "src": src_name})
            sink1 = self._selector.get_static_pad("sink_1")
            self._selector.set_property("active-pad", sink1)
            # Try to recover the capture branch periodically.
            GLib.timeout_add(1000, self._try_recover_capture)

    def _try_recover_capture(self):
        cap = self._pipe.get_by_name("cap")
        if not cap:
            return False
        # Re-trigger READY->PLAYING on the capture branch.
        cap.set_state(Gst.State.READY)
        cap.set_state(Gst.State.PLAYING)
        ret, state, _pending = cap.get_state(timeout=Gst.SECOND)
        if ret == Gst.StateChangeReturn.SUCCESS and state == Gst.State.PLAYING:
            sink0 = self._selector.get_static_pad("sink_0")
            self._selector.set_property("active-pad", sink0)
            self._signal_lost = False
            self._emit({"type": "signal_restored"})
            logger.info("v4l2src recovered, swapped back to live capture")
            return False  # stop the timer
        return True  # keep polling

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

    def set_sender(self, send):
        """send(dict) -> awaitable. Called from any thread; scheduled on _loop."""
        self._send = send

    def _emit(self, msg: dict):
        if self._send and self._loop:
            asyncio.run_coroutine_threadsafe(self._send(msg), self._loop)

    def _on_negotiation_needed(self, webrtcbin):
        logger.info("Negotiation needed -- creating offer")
        promise = Gst.Promise.new_with_change_func(self._on_offer_created)
        webrtcbin.emit("create-offer", None, promise)

    def _on_offer_created(self, promise):
        promise.wait()
        reply = promise.get_reply()
        offer = reply.get_value("offer")
        self._webrtcbin.emit("set-local-description", offer, None)
        sdp_text = offer.sdp.as_text()
        logger.info("SDP offer created (%d chars)", len(sdp_text))
        self._emit({"type": "offer", "sdp": sdp_text})

    def _on_ice_candidate(self, webrtcbin, mline_index, candidate):
        self._emit({
            "type": "ice",
            "candidate": candidate,
            "sdpMLineIndex": mline_index,
        })

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
        import aiohttp
        from aiohttp import web
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        logger.info("WebSocket client connected")

        loop = asyncio.get_event_loop()

        if self._pipeline:
            self._pipeline.stop()

        self._pipeline = GamerPipeline(self._device, self._fps, self._bitrate)
        self._pipeline.build(loop)

        async def send(msg):
            await ws.send_json(msg)

        self._pipeline.set_sender(send)
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
        from aiohttp import web
        return web.Response(text=_INDEX_HTML, content_type="text/html")

    def run(self):
        from aiohttp import web
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


async def run_stdio(device: str, fps: int, bitrate: int):
    """Bridge signaling over stdin/stdout JSON-line frames.

    kvmd parents this process: it forwards browser-WS messages into stdin,
    and reads stdout for SDP offer / ICE candidates / signal-loss events
    to forward back to the browser WS. One frame per line.
    """
    loop = asyncio.get_event_loop()
    pipeline = GamerPipeline(device, fps, bitrate)
    pipeline.build(loop)

    # Writer: a coroutine that emits one JSON line to stdout.
    write_lock = asyncio.Lock()

    async def send(msg: dict) -> None:
        async with write_lock:
            sys.stdout.write(json.dumps(msg) + "\n")
            sys.stdout.flush()

    pipeline.set_sender(send)
    pipeline.start()

    # Reader: stdin lines -> pipeline. Use a thread executor since
    # sys.stdin.readline() is blocking.
    def read_line():
        return sys.stdin.readline()

    try:
        while True:
            line = await loop.run_in_executor(None, read_line)
            if not line:
                logger.info("stdin EOF, shutting down")
                break
            try:
                msg = json.loads(line.strip())
            except json.JSONDecodeError as ex:
                logger.warning("Bad JSON on stdin: %s", ex)
                continue
            await pipeline.handle_message(msg)
    finally:
        pipeline.stop()


def main():
    parser = argparse.ArgumentParser(description="PiKVM Gamer Mode Streamer")
    parser.add_argument("--device", default="/dev/video0", help="V4L2 device")
    parser.add_argument("--fps", type=int, default=60, help="Target framerate")
    parser.add_argument("--bitrate", type=int, default=5000, help="H.264 bitrate (kbps)")
    parser.add_argument("--port", type=int, default=0,
                        help="If >0, run standalone HTTP server on this port "
                             "(legacy spike mode). Default 0 = stdio mode.")
    args = parser.parse_args()

    if args.port > 0:
        # Standalone spike mode: import aiohttp on demand.
        from aiohttp import web  # noqa: F401
        server = GamerServer(args.device, args.fps, args.bitrate, args.port)
        server.run()
    else:
        # stdio mode: kvmd-managed.
        asyncio.run(run_stdio(args.device, args.fps, args.bitrate))


if __name__ == "__main__":
    main()
