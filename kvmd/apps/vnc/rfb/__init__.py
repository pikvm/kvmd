# ========================================================================== #
#                                                                            #
#    KVMD - The main Pi-KVM daemon.                                          #
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
import ssl

from typing import Tuple
from typing import List
from typing import Dict
from typing import Callable
from typing import Coroutine

from ....logging import get_logger

from .... import aiotools

from .errors import RfbError
from .errors import RfbConnectionError

from .encodings import RfbEncodings
from .encodings import RfbClientEncodings

from .crypto import rfb_make_challenge
from .crypto import rfb_encrypt_challenge

from .stream import RfbClientStream


# =====
class RfbClient(RfbClientStream):  # pylint: disable=too-many-instance-attributes
    # https://github.com/rfbproto/rfbproto/blob/master/rfbproto.rst
    # https://www.toptal.com/java/implementing-remote-framebuffer-server-java
    # https://github.com/TigerVNC/tigervnc

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        tls_ciphers: str,
        tls_timeout: float,

        width: int,
        height: int,
        name: str,
        vnc_passwds: List[str],
        none_auth_only: bool,
    ) -> None:

        super().__init__(reader, writer)

        self.__tls_ciphers = tls_ciphers
        self.__tls_timeout = tls_timeout

        self._width = width
        self._height = height
        self.__name = name
        self.__vnc_passwds = vnc_passwds
        self.__none_auth_only = none_auth_only

        self.__rfb_version = 0
        self._encodings = RfbClientEncodings(frozenset())

        get_logger(0).info("Connected client: %s", self._remote)

    # =====

    async def _run(self, **coros: Coroutine) -> None:
        tasks = list(map(asyncio.create_task, [
            self.__wrapper(name, coro)
            for (name, coro) in {"main": self.__main_task_loop(), **coros}.items()
        ]))
        try:
            await aiotools.wait_first(*tasks)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await self._close()
            get_logger(0).info("Connection closed: %s", self._remote)

    async def __wrapper(self, name: str, coro: Coroutine) -> None:
        logger = get_logger(0)
        try:
            await coro
            raise RuntimeError("Subtask just finished without any exception")
        except asyncio.CancelledError:
            logger.info("[%s] Client %s: Cancelling subtask ...", name, self._remote)
            raise
        except RfbConnectionError as err:
            logger.info("[%s] Client %s: Gone: %s", name, self._remote, str(err))
        except (RfbError, ssl.SSLError) as err:
            logger.error("[%s] Client %s: Error: %s", name, self._remote, str(err))
        except Exception:
            logger.exception("[%s] Unhandled exception with client %s", name, self._remote)

    async def __main_task_loop(self) -> None:
        await self.__handshake_version()
        await self.__handshake_security()
        await self.__handshake_init()
        await self.__main_loop()

    # =====

    async def _authorize_userpass(self, user: str, passwd: str) -> bool:
        raise NotImplementedError

    async def _on_authorized_vnc_passwd(self, passwd: str) -> str:
        raise NotImplementedError

    async def _on_authorized_none(self) -> bool:
        return True
    # =====

    async def _on_key_event(self, code: int, state: bool) -> None:
        raise NotImplementedError

    async def _on_pointer_event(self, buttons: Dict[str, bool], wheel: Dict[str, int], move: Dict[str, int]) -> None:
        raise NotImplementedError

    async def _on_cut_event(self, text: str) -> None:
        raise NotImplementedError

    async def _on_set_encodings(self) -> None:
        raise NotImplementedError

    async def _on_fb_update_request(self) -> None:
        raise NotImplementedError

    # =====

    async def _send_fb(self, jpeg: bytes) -> None:
        assert self._encodings.has_tight
        assert self._encodings.tight_jpeg_quality > 0
        assert len(jpeg) <= 4194303, len(jpeg)
        await self._write_fb_update(self._width, self._height, RfbEncodings.TIGHT, drain=False)
        length = len(jpeg)
        if length <= 127:
            await self._write_struct("", bytes([0b10011111, length & 0x7F]), jpeg)
        elif length <= 16383:
            await self._write_struct("", bytes([0b10011111, length & 0x7F | 0x80, length >> 7 & 0x7F]), jpeg)
        else:
            await self._write_struct("", bytes([0b10011111, length & 0x7F | 0x80, length >> 7 & 0x7F | 0x80, length >> 14 & 0xFF]), jpeg)

    async def _send_resize(self, width: int, height: int) -> None:
        assert self._encodings.has_resize
        await self._write_fb_update(width, height, RfbEncodings.RESIZE)
        self._width = width
        self._height = height

    async def _send_rename(self, name: str) -> None:
        assert self._encodings.has_rename
        await self._write_fb_update(0, 0, RfbEncodings.RENAME, drain=False)
        await self._write_reason(name)
        self.__name = name

    async def _send_leds_state(self, caps: bool, scroll: bool, num: bool) -> None:
        assert self._encodings.has_leds_state
        await self._write_fb_update(0, 0, RfbEncodings.LEDS_STATE, drain=False)
        await self._write_struct("B", 0x1 & scroll | 0x2 & num | 0x4 & caps)

    # =====

    async def __handshake_version(self) -> None:
        # The only published protocol versions at this time are 3.3, 3.7, 3.8.
        # Version 3.5 was wrongly reported by some clients, but it should be
        # interpreted by all servers as 3.3

        await self._write_struct("", b"RFB 003.008\n")

        response = await self._read_text(12)
        if (
            not response.startswith("RFB 003.00")
            or not response.endswith("\n")
            or response[-2] not in ["3", "5", "7", "8"]
        ):
            raise RfbError(f"Invalid version response: {response!r}")

        try:
            version = int(response[-2])
        except ValueError:
            raise RfbError(f"Invalid version response: {response!r}")
        self.__rfb_version = (3 if version == 5 else version)
        get_logger(0).info("[main] Client %s: Using RFB version 3.%d", self._remote, self.__rfb_version)

    # =====

    async def __handshake_security(self) -> None:
        sec_types: Dict[int, Tuple[str, Callable]] = {}
        if self.__rfb_version > 3:
            sec_types[19] = ("VeNCrypt", self.__handshake_security_vencrypt)
        if self.__none_auth_only:
            sec_types[1] = ("None", self.__handshake_security_none)
        elif self.__vnc_passwds:
            sec_types[2] = ("VNCAuth", self.__handshake_security_vnc_auth)
        if not sec_types:
            msg = "The client uses a very old protocol 3.3 and VNCAuth or NoneAuth is disabled"
            await self._write_struct("L", 0, drain=False)  # Refuse old clients using the invalid security type
            await self._write_reason(msg)
            raise RfbError(msg)

        await self._write_struct("B" + "B" * len(sec_types), len(sec_types), *sec_types)  # Keep dict priority

        sec_type = await self._read_number("B")
        if sec_type not in sec_types:
            raise RfbError(f"Invalid security type: {sec_type}")

        (sec_name, handler) = sec_types[sec_type]
        get_logger(0).info("[main] Client %s: Using %s security type", self._remote, sec_name)
        await handler()

    async def __handshake_security_vencrypt(self) -> None:
        await self._write_struct("BB", 0, 2)  # VeNCrypt 0.2

        vencrypt_version = "%d.%d" % (await self._read_struct("BB"))
        if vencrypt_version != "0.2":
            await self._write_struct("B", 1)  # Unsupported
            raise RfbError(f"Unsupported VeNCrypt version: {vencrypt_version}")

        await self._write_struct("B", 0)

        if self.__none_auth_only:
            auth_types = {
                1: ("VeNCrypt/None", False, self.__handshake_security_none),
                257: ("VeNCrypt/TLSNone", True, self.__handshake_security_none),
            }
        else:
            auth_types = {
                256: ("VeNCrypt/Plain", False, self.__handshake_security_vencrypt_userpass),
                259: ("VeNCrypt/TLSPlain", True, self.__handshake_security_vencrypt_userpass),
            }
            if self.__vnc_passwds:
                # Vinagre не умеет работать с VNC Auth через VeNCrypt, но это его проблемы,
                # так как он своеобразно трактует рекомендации VeNCrypt.
                # Подробнее: https://bugzilla.redhat.com/show_bug.cgi?id=692048
                # Hint: используйте любой другой нормальный VNC-клиент.
                auth_types.update({
                    2: ("VeNCrypt/VNCAuth", False, self.__handshake_security_vnc_auth),
                    258: ("VeNCrypt/TLSVNCAuth", True, self.__handshake_security_vnc_auth),
                })

        await self._write_struct("B" + "L" * len(auth_types), len(auth_types), *auth_types)

        auth_type = await self._read_number("L")
        if auth_type not in auth_types:
            raise RfbError(f"Invalid VeNCrypt auth type: {auth_type}")

        (auth_name, tls, handler) = auth_types[auth_type]
        get_logger(0).info("[main] Client %s: Using %s auth type", self._remote, auth_name)

        if tls:
            await self._write_struct("B", 1)  # Ack
            ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ssl_context.set_ciphers(self.__tls_ciphers)
            await self._start_tls(ssl_context, self.__tls_timeout)

        await handler()

    async def __handshake_security_vencrypt_userpass(self) -> None:
        (user_length, passwd_length) = await self._read_struct("LL")
        user = (await self._read_text(user_length)).strip()
        passwd = await self._read_text(passwd_length)

        allow = await self._authorize_userpass(user, passwd)
        if allow:
            assert user
        await self.__handshake_security_send_result(
            allow=allow,
            allow_msg=f"Access granted for user {user!r}",
            deny_msg=f"Access denied for user {user!r}",
            deny_reason="Invalid username or password",
        )

    async def __handshake_security_none(self) -> None:
        allow = await self._on_authorized_none()
        await self.__handshake_security_send_result(
            allow=allow,
            allow_msg="NoneAuth access granted",
            deny_msg="NoneAuth access denied",
            deny_reason="Access denied",
        )

    async def __handshake_security_vnc_auth(self) -> None:
        challenge = rfb_make_challenge()
        await self._write_struct("", challenge)

        user = ""
        response = (await self._read_struct("16s"))[0]
        for passwd in self.__vnc_passwds:
            passwd_bytes = passwd.encode("utf-8", errors="ignore")
            if rfb_encrypt_challenge(challenge, passwd_bytes) == response:
                user = await self._on_authorized_vnc_passwd(passwd)
                if user:
                    assert user == user.strip()
                break

        await self.__handshake_security_send_result(
            allow=bool(user),
            allow_msg="VNCAuth access granted for user {user!r}",
            deny_msg="VNCAuth access denied (user not found)",
            deny_reason="Invalid password",
        )

    async def __handshake_security_send_result(self, allow: bool, allow_msg: str, deny_msg: str, deny_reason: str) -> None:
        if allow:
            get_logger(0).info("[main] Client %s: %s", self._remote, allow_msg)
            await self._write_struct("L", 0)
        else:
            await self._write_struct("L", 1, drain=(self.__rfb_version < 8))
            if self.__rfb_version >= 8:
                await self._write_reason(deny_reason)
            raise RfbError(deny_msg)

    # =====

    async def __handshake_init(self) -> None:
        await self._read_number("B")  # Shared flag, ignored

        await self._write_struct("HH", self._width, self._height, drain=False)
        await self._write_struct(
            "BB?? HHH BBB xxx",
            32,     # Bits per pixel
            24,     # Depth
            False,  # Big endian
            True,   # True color
            255,    # Red max
            255,    # Green max
            255,    # Blue max
            16,     # Red shift
            8,      # Green shift
            0,      # Blue shift
            drain=False,
        )
        await self._write_reason(self.__name)

    # =====

    async def __main_loop(self) -> None:
        handlers = {
            0: self.__handle_set_pixel_format,
            2: self.__handle_set_encodings,
            3: self.__handle_fb_update_request,
            4: self.__handle_key_event,
            5: self.__handle_pointer_event,
            6: self.__handle_client_cut_text,
        }
        while True:
            msg_type = await self._read_number("B")
            if (handler := handlers.get(msg_type)) is not None:  # noqa: E203,E231
                await handler()  # type: ignore  # mypy bug
            else:
                raise RfbError(f"Unknown message type: {msg_type}")

    async def __handle_set_pixel_format(self) -> None:
        # JpegCompression may only be used when bits-per-pixel is either 16 or 32
        bits_per_pixel = (await self._read_struct("xxx BB?? HHH BBB xxx"))[0]
        if bits_per_pixel not in [16, 32]:
            raise RfbError(f"Requested unsupported {bits_per_pixel=} for Tight JPEG; required 16 or 32")

    async def __handle_set_encodings(self) -> None:
        encodings_count = (await self._read_struct("x H"))[0]
        if encodings_count > 1024:
            raise RfbError(f"Too many encodings: {encodings_count}")
        self._encodings = RfbClientEncodings(frozenset(await self._read_struct("l" * encodings_count)))
        get_logger(0).info("[main] Client %s: Features: resize=%d; rename=%d; leds=%d",
                           self._remote, self._encodings.has_resize, self._encodings.has_rename, self._encodings.has_leds_state)
        self.__check_tight_jpeg()
        await self._on_set_encodings()

    async def __handle_fb_update_request(self) -> None:
        self.__check_tight_jpeg()  # If we don't receive SetEncodings from client
        await self._read_struct("? HH HH")  # Ignore any arguments, just perform the full update
        await self._on_fb_update_request()

    async def __handle_key_event(self) -> None:
        (state, code) = await self._read_struct("? xx L")
        await self._on_key_event(code, state)  # type: ignore

    async def __handle_pointer_event(self) -> None:
        (buttons, to_x, to_y) = await self._read_struct("B HH")
        await self._on_pointer_event(
            buttons={
                "left": bool(buttons & 0x1),
                "right": bool(buttons & 0x4),
                "middle": bool(buttons & 0x2),
            },
            wheel={
                "x": (-4 if buttons & 0x40 else (4 if buttons & 0x20 else 0)),
                "y": (-4 if buttons & 0x10 else (4 if buttons & 0x8 else 0)),
            },
            move={
                "x": round(to_x / self._width * 65535 + -32768),
                "y": round(to_y / self._height * 65535 + -32768),
            },
        )

    async def __handle_client_cut_text(self) -> None:
        length = (await self._read_struct("xxx L"))[0]
        text = await self._read_text(length)
        await self._on_cut_event(text)

    def __check_tight_jpeg(self) -> None:
        # JpegCompression may only be used when the client has advertized
        # a quality level using the JPEG Quality Level Pseudo-encoding
        if not self._encodings.has_tight or self._encodings.tight_jpeg_quality == 0:
            raise RfbError(f"Tight JPEG encoding is not supported by client: {self._encodings}")
