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


import contextlib
import struct

import typing
from typing import Callable
from typing import AsyncGenerator

import aiohttp

from .. import aiotools
from .. import htclient
from .. import htserver

from . import BaseHttpClient
from . import BaseHttpClientSession


# =====
class _BaseApiPart:
    def __init__(self, ensure_http_session: Callable[[], aiohttp.ClientSession]) -> None:
        self._ensure_http_session = ensure_http_session

    async def _set_params(self, handle: str, **params: (int | str | None)) -> None:
        session = self._ensure_http_session()
        async with session.post(
            url=handle,
            params={
                key: value
                for (key, value) in params.items()
                if value is not None
            },
        ) as resp:
            htclient.raise_not_200(resp)


class _AuthApiPart(_BaseApiPart):
    async def check(self, user: str, passwd: str) -> bool:
        session = self._ensure_http_session()
        try:
            async with session.get("/auth/check", headers={
                "X-KVMD-User":   user,
                "X-KVMD-Passwd": passwd,
            }) as resp:

                htclient.raise_not_200(resp)
                return (resp.status == 200)  # Just for my paranoia

        except aiohttp.ClientResponseError as ex:
            if ex.status in [400, 401, 403]:
                return False
            raise
        typing.assert_never("We should't be here")


class _StreamerApiPart(_BaseApiPart):
    async def get_state(self) -> dict:
        session = self._ensure_http_session()
        async with session.get("/streamer") as resp:
            htclient.raise_not_200(resp)
            return (await resp.json())["result"]

    async def set_params(self, quality: (int | None)=None, desired_fps: (int | None)=None) -> None:
        await self._set_params(
            "/streamer/set_params",
            quality=quality,
            desired_fps=desired_fps,
        )


class _HidApiPart(_BaseApiPart):
    async def get_keymaps(self) -> tuple[str, set[str]]:
        session = self._ensure_http_session()
        async with session.get("/hid/keymaps") as resp:
            htclient.raise_not_200(resp)
            result = (await resp.json())["result"]
            return (result["keymaps"]["default"], set(result["keymaps"]["available"]))

    async def print(self, text: str, limit: int, keymap_name: str) -> None:
        session = self._ensure_http_session()
        async with session.post(
            url="/hid/print",
            params={"limit": limit, "keymap": keymap_name},
            data=text,
        ) as resp:
            htclient.raise_not_200(resp)

    async def set_params(self, keyboard_output: (str | None)=None, mouse_output: (str | None)=None) -> None:
        await self._set_params(
            "/hid/set_params",
            keyboard_output=keyboard_output,
            mouse_output=mouse_output,
        )


class _AtxApiPart(_BaseApiPart):
    async def get_state(self) -> dict:
        session = self._ensure_http_session()
        async with session.get("/atx") as resp:
            htclient.raise_not_200(resp)
            return (await resp.json())["result"]

    async def switch_power(self, action: str) -> bool:
        session = self._ensure_http_session()
        try:
            async with session.post(
                url="/atx/power",
                params={"action": action},
            ) as resp:
                htclient.raise_not_200(resp)
                return True
        except aiohttp.ClientResponseError as ex:
            if ex.status == 409:
                return False
            raise


class _SwitchApiPart(_BaseApiPart):
    async def set_active_prev(self) -> None:
        session = self._ensure_http_session()
        async with session.post("/switch/set_active_prev") as resp:
            htclient.raise_not_200(resp)

    async def set_active_next(self) -> None:
        session = self._ensure_http_session()
        async with session.post("/switch/set_active_next") as resp:
            htclient.raise_not_200(resp)

    async def set_active(self, port: float) -> None:
        session = self._ensure_http_session()
        async with session.post(
            url="/switch/set_active",
            params={"port": port},
        ) as resp:
            htclient.raise_not_200(resp)


# =====
class KvmdClientWs:
    def __init__(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        self.__ws = ws
        self.__communicated = False

    async def communicate(self) -> AsyncGenerator[tuple[str, dict], None]:  # pylint: disable=too-many-branches
        assert not self.__communicated
        self.__communicated = True
        try:
            async for msg in self.__ws:
                match msg.type:
                    case aiohttp.WSMsgType.TEXT:
                        yield htserver.parse_ws_event(msg.data)
                    case aiohttp.WSMsgType.CLOSE:
                        await self.__ws.close()
                    case aiohttp.WSMsgType.CLOSED:
                        break
                    case _:
                        raise RuntimeError(f"Unhandled WS message type: {msg!r}")
        finally:
            try:
                await aiotools.shield_fg(self.__ws.close())
            except Exception:
                pass
            finally:
                self.__communicated = False

    async def send_key_event(self, key: int, state: bool) -> None:
        mask = (0b10000000 | int(bool(state)))
        await self.__send_struct(">BBH", 1, mask, key)

    async def send_mouse_button_event(self, button: int, state: bool) -> None:
        mask = (0b10000000 | int(bool(state)))
        await self.__send_struct(">BBH", 2, mask, button)

    async def send_mouse_move_event(self, to_x: int, to_y: int) -> None:
        await self.__send_struct(">Bhh", 3, to_x, to_y)

    async def send_mouse_relative_event(self, delta_x: int, delta_y: int) -> None:
        await self.__send_struct(">BBbb", 4, 0, delta_x, delta_y)

    async def send_mouse_wheel_event(self, delta_x: int, delta_y: int) -> None:
        await self.__send_struct(">BBbb", 5, 0, delta_x, delta_y)

    async def __send_struct(self, fmt: str, *values: int) -> None:
        if not self.__communicated:
            return
        data = struct.pack(fmt, *values)
        try:
            await self.__ws.send_bytes(data)
        except Exception:
            # XXX: We don't care about any connection errors
            # since they will be handled with communication()
            pass


class KvmdClientSession(BaseHttpClientSession):
    def __init__(self, make_http_session: Callable[[], aiohttp.ClientSession]) -> None:
        super().__init__(make_http_session)
        self.auth = _AuthApiPart(self._ensure_http_session)
        self.streamer = _StreamerApiPart(self._ensure_http_session)
        self.hid = _HidApiPart(self._ensure_http_session)
        self.atx = _AtxApiPart(self._ensure_http_session)
        self.switch = _SwitchApiPart(self._ensure_http_session)

    @contextlib.asynccontextmanager
    async def ws(self, stream: bool=True) -> AsyncGenerator[KvmdClientWs, None]:
        session = self._ensure_http_session()
        async with session.ws_connect("/ws", params={"stream": int(stream)}) as ws:
            yield KvmdClientWs(ws)


class KvmdClient(BaseHttpClient):
    def make_session(self) -> KvmdClientSession:
        return KvmdClientSession(self._make_http_session)
