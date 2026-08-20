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
import dataclasses

from aiohttp.web import Request
from aiohttp.web import Response
from aiohttp.web import WebSocketResponse

from ...logging import get_logger

from ... import tools
from ... import aiotools

from ...htserver import exposed_http
from ...htserver import exposed_ws
from ...htserver import make_json_response
from ...htserver import WsSession
from ...htserver import HttpServer

from ...clients.streamer import StreamerError
from ...clients.streamer import StreamerPermError
from ...clients.streamer import StreamerFormats
from ...clients.streamer import BaseStreamerClient

from ...audio import AudioError

from ...validators import ValidatorError
from ...validators.basic import valid_stripped_string

from .audio import AudioSourceError
from .audio import AudioSource

from .mic import MicError
from .mic import MicSink


# =====
@dataclasses.dataclass
class _Source:
    streamer:     BaseStreamerClient
    meta:         dict = dataclasses.field(default_factory=dict)
    clients:      dict[WsSession, "_Client"] = dataclasses.field(default_factory=dict)
    key_required: bool = dataclasses.field(default=False)

    def is_diff(self) -> bool:
        return StreamerFormats.is_diff(self.streamer.get_format())


@dataclasses.dataclass
class _Client:
    ws:     WsSession
    src:    _Source
    sender: asyncio.Task
    _queue: asyncio.Queue[dict] = dataclasses.field(default_factory=(lambda: asyncio.Queue(32)))

    async def get_frame(self) -> dict:
        return (await self._queue.get())

    async def put_frame(self, frame: dict) -> bool:  # Overflow/wipe flag
        try:
            self._queue.put_nowait(frame)
        except asyncio.QueueFull:
            # Если какой-то из клиентов не справляется, очищаем ему очередь и запрашиваем кейфрейм.
            # Я вижу у такой логики кучу минусов, хз как себя покажет, но лучше пока ничего не придумал.
            for _ in range(self._queue.qsize()):
                try:
                    self._queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
            return True
        except Exception:
            pass
        return False


class MediaServer(HttpServer):
    __EV_MEDIA = "media"
    __EV_AUDIO_STATE = "audio_state"
    __EV_MIC_STATE = "mic_state"

    __T_VIDEO = "video"
    __T_AUDIO = "audio"
    __T_MIC = "mic"

    __F_H264 = "h264"
    __F_JPEG = "jpeg"

    def __init__(
        self,
        h264_streamer: (BaseStreamerClient | None),
        jpeg_streamer: (BaseStreamerClient | None),
        audio: AudioSource,
        mic: MicSink,
    ) -> None:

        super().__init__()

        self.__audio = audio
        self.__mic = mic

        self.__media: dict[str, dict[str, _Source]] = {self.__T_VIDEO: {}}
        if h264_streamer:
            self.__media[self.__T_VIDEO][self.__F_H264] = _Source(h264_streamer, {"profile_level_id": "42E01F"})
        if jpeg_streamer:
            self.__media[self.__T_VIDEO][self.__F_JPEG] = _Source(jpeg_streamer)

    # ===== HTTP

    @exposed_http("GET", "/")
    async def __root_handler(self, _: Request) -> Response:
        return make_json_response({
            self.__EV_MEDIA: (await self.__get_media_info()),
        })

    # ===== WEBSOCKET

    @exposed_http("GET", "/ws")
    async def __ws_handler(self, req: Request) -> WebSocketResponse:
        v_fmt = valid_stripped_string(req.query.get(self.__T_VIDEO, ""))
        if v_fmt not in ["", *self.__media[self.__T_VIDEO]]:
            raise ValidatorError("Unsupported video type")

        async with self._ws_session(req, pure=bool(v_fmt)) as ws:
            if v_fmt:  # Pure request for simplified API without any info
                if not self.__start_stream(ws, self.__T_VIDEO, v_fmt):
                    raise RuntimeError("We shouldn't be here")
            else:
                await ws.send_event(self.__EV_MEDIA, (await self.__get_media_info()))
            return (await self._ws_loop(ws))

    @exposed_ws(0)
    async def __ws_bin_ping_handler(self, ws: WsSession, _: bytes) -> None:
        if not ws.kwargs["pure"]:  # Don't spoil pure data
            await ws.send_bin(255, b"")  # Ping-pong

    @exposed_ws(1)
    async def __ws_bin_key_handler(self, ws: WsSession, _: bytes) -> None:
        for srcs in self.__media.values():
            for src in srcs.values():
                if ws in src.clients:
                    if src.is_diff():
                        src.key_required = True
                    break

    @exposed_ws(2)
    async def __ws_bin_mic_handler(self, ws: WsSession, data: bytes) -> None:
        self.__mic.feed(ws, data)

    @exposed_ws("start")
    async def __ws_start_handler(self, ws: WsSession, event: dict) -> None:
        try:
            m_type = str(event.get("type"))
            m_fmt = str(event.get("format"))
        except Exception:
            return
        self.__start_stream(ws, m_type, m_fmt)  # TODO: Handle discard

    @exposed_ws("audio_start")
    async def __ws_audio_start_handler(self, ws: WsSession, event: dict) -> None:
        if ws.kwargs["pure"]:  # Don't spoil pure data
            return
        error = ""
        try:
            fmt = valid_stripped_string(event.get("format"))
            await self.__audio.start(
                ws, fmt,
                (lambda data: ws.send_bin(3, data)),
                (lambda err: self.__send_audio_state(ws, False, err)),
            )
            if not ws.is_alive():
                # The client has disconnected while we were starting the capture,
                # so _on_ws_removed() has already passed by
                self.__audio.stop(ws)
                return
        except (AudioSourceError, ValidatorError, AudioError) as ex:
            error = tools.efmt(ex)
        except Exception as ex:
            error = tools.efmt(ex)
            get_logger(0).exception("Can't start the audio for %s", ws)
        await self.__send_audio_state(ws, (not error), error)

    @exposed_ws("audio_stop")
    async def __ws_audio_stop_handler(self, ws: WsSession, _: dict) -> None:
        if ws.kwargs["pure"]:  # Don't spoil pure data
            return
        self.__audio.stop(ws)
        await self.__send_audio_state(ws, False, "")

    async def __send_audio_state(self, ws: WsSession, started: bool, error: str) -> None:
        with contextlib.suppress(Exception):
            await ws.send_event(self.__EV_AUDIO_STATE, {"started": started, "error": error})

    @exposed_ws("mic_start")
    async def __ws_mic_start_handler(self, ws: WsSession, event: dict) -> None:
        if ws.kwargs["pure"]:  # Don't spoil pure data
            return
        error = ""
        try:
            fmt = valid_stripped_string(event.get("format"))
            await self.__mic.start(ws, fmt, (lambda err: self.__send_mic_state(ws, False, err)))
            if not ws.is_alive():
                # The client has disconnected while we were opening the device,
                # so _on_ws_removed() has already passed by
                self.__mic.stop(ws)
                return
        except (MicError, ValidatorError, AudioError) as ex:
            error = tools.efmt(ex)
        except Exception as ex:
            error = tools.efmt(ex)
            get_logger(0).exception("Can't start the microphone for %s", ws)
        await self.__send_mic_state(ws, (not error), error)

    @exposed_ws("mic_stop")
    async def __ws_mic_stop_handler(self, ws: WsSession, _: dict) -> None:
        if ws.kwargs["pure"]:  # Don't spoil pure data
            return
        self.__mic.stop(ws)
        await self.__send_mic_state(ws, False, "")

    async def __send_mic_state(self, ws: WsSession, started: bool, error: str) -> None:
        with contextlib.suppress(Exception):
            await ws.send_event(self.__EV_MIC_STATE, {"started": started, "error": error})

    async def __get_media_info(self) -> dict:
        info: dict = {}
        for (m_type, srcs) in self.__media.items():
            info[m_type] = {}
            for (m_fmt, src) in srcs.items():
                info[m_type][m_fmt] = src.meta
        audio = (await self.__audio.get_info())
        if audio is not None:
            info[self.__T_AUDIO] = audio
        mic = (await self.__mic.get_info())
        if mic is not None:
            info[self.__T_MIC] = mic
        return info

    def __start_stream(self, ws: WsSession, m_type: str, m_fmt: str) -> bool:
        src: (_Source | None) = self.__media.get(m_type, {}).get(m_fmt)
        if src is None:
            return False
        client = _Client(ws, src, None)  # type: ignore
        client.sender = aiotools.create_deadly_task(str(ws), self.__sender(client))
        src.clients[ws] = client
        get_logger(0).info("Streaming %s to %s ...", src.streamer, ws)
        return True

    # =====

    async def _init_app(self) -> None:
        logger = get_logger(0)
        for srcs in self.__media.values():
            for src in srcs.values():
                logger.info("Starting streamer %s ...", src.streamer)
                aiotools.create_deadly_task(str(src.streamer), self.__streamer(src))
        self._add_exposed(self)

    async def _on_shutdown(self) -> None:
        logger = get_logger(0)
        logger.info("Stopping system tasks ...")
        await aiotools.stop_all_deadly_tasks()
        logger.info("Disconnecting clients ...")
        await self._close_all_wss()
        logger.info("On-Shutdown complete")

    def _on_ws_removed(self, ws: WsSession) -> None:
        self.__audio.stop(ws)
        self.__mic.stop(ws)
        for srcs in self.__media.values():
            for src in srcs.values():
                client = src.clients.pop(ws, None)
                if client and client.sender:
                    get_logger(0).info("Closed stream for %s", ws)
                    client.sender.cancel()
                    return

    # =====

    async def __sender(self, client: _Client) -> None:
        need_key = client.src.is_diff()
        if need_key:
            client.src.key_required = True

        has_key = False
        while True:
            frame = await client.get_frame()
            has_key = (not need_key or has_key or frame["key"])
            if has_key:
                try:
                    if client.ws.kwargs["pure"]:  # Simplified interface for a scripting
                        await client.ws.send_bin_raw(frame["data"])
                    else:  # Regular interface for the Web UI
                        await client.ws.send_bin(1, frame["key"].to_bytes() + frame["data"])
                except Exception:
                    pass

    async def __streamer(self, src: _Source) -> None:
        logger = get_logger(0)
        while True:
            if len(src.clients) == 0:
                await asyncio.sleep(1)
                continue

            try:
                async with src.streamer.reading() as read_frame:
                    while len(src.clients) > 0:
                        frame = await read_frame(src.key_required)
                        if frame["key"]:
                            src.key_required = False
                        for client in src.clients.values():
                            if (await client.put_frame(frame)):
                                # Overflowed and cleaned up, need a keyframe
                                src.key_required = True

            except StreamerError as ex:
                if isinstance(ex, StreamerPermError):
                    logger.exception("Streamer failed: %s", src.streamer)
                else:
                    logger.error("Streamer error: %s: %s", src.streamer, tools.efmt(ex))
            except Exception:
                get_logger(0).exception("Unexpected streamer error: %s", src.streamer)
            await asyncio.sleep(1)
