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
import struct
import errno

from typing import Final

from ...yamlconf import Option

from ... import tools
from ... import aiomulti

from ..errors import NbdRemoteError
from ..errors import NbdIoConnectionError
from ..errors import NbdIoProtocolError

from ..types import NbdImage
from ..types import BaseNbdEvent
from ..types import NbdRemoteEvent

from ..link import NbdLink


# =====
class BaseNbdRemote:
    # https://github.com/NetworkBlockDevice/nbd/blob/master/doc/proto.md
    # https://github.com/NetworkBlockDevice/nbd/blob/master/nbd-client.c
    # https://github.com/mirror/busybox/blob/master/networking/nbd-client.c
    # https://elixir.bootlin.com/linux/v6.12/source/drivers/block/nbd.c

    __MAGIC_RECV: Final[int] = 0x25609513
    __MAGIC_SEND: Final[int] = 0x67446698

    __OP_READ:  Final[int] = 0
    __OP_WRITE: Final[int] = 1
    __OP_STOP:  Final[int] = 2

    def __init__(self) -> None:
        self.__recv_st = struct.Struct(">IHHQQI")
        self.__send_st = struct.Struct(">IIQ")

        self.__image: (NbdImage | None) = None
        self.__events_q: (aiomulti.AioMpQueue[BaseNbdEvent] | None) = None

    # =====

    @classmethod
    def get_schemes(cls) -> set[str]:
        raise NotImplementedError

    @classmethod
    def get_options(cls) -> dict[str, Option]:
        return {}

    # =====

    async def _do_probe(self) -> NbdImage:
        raise NotImplementedError

    async def _do_again(self) -> NbdImage:
        raise NotImplementedError

    async def _on_read(self, offset: int, size: int) -> bytes:
        raise NotImplementedError

    async def _on_write(self, offset: int, data: bytes) -> None:
        raise NotImplementedError

    async def _do_cleanup(self) -> None:
        raise NotImplementedError

    # =====

    async def _send_status_ok(self) -> None:
        await self.__send_remote_event(True, "Online")

    async def _send_status_error(self, msg: str) -> None:
        await self.__send_remote_event(False, msg)

    async def __send_remote_event(self, online: bool, msg: str) -> None:
        assert self.__events_q is not None
        try:
            self.__events_q.put_nowait(NbdRemoteEvent(online, msg))
        except Exception as ex:
            raise NbdRemoteError(f"Can't send status event: {tools.efmt(ex)}")

    async def _probe_again(self) -> None:
        assert self.__image
        image = await self._do_again()
        if self.__image.rw is True and not image.rw:
            raise NbdRemoteError("The source permissions changed: RW -> RO")
        if self.__image.size != image.size:
            raise NbdRemoteError(f"The source file has a new size: {self.__image.size} -> {image.size}")

    # =====

    async def probe(self) -> NbdImage:
        assert self.__events_q is None  # Not running
        self.__image = await self._do_probe()
        return self.__image

    async def serve(
        self,
        link: NbdLink,
        events_q: aiomulti.AioMpQueue[BaseNbdEvent],
    ) -> None:

        assert self.__image
        assert self.__events_q is None
        self.__events_q = events_q

        await self._probe_again()  # Validate NbdImage after first probing
        await self._send_status_ok()

        while True:
            (op, cookie, offset, size, data) = await self.__recv_request(link.remote_r)
            result: (tuple[int, bytes] | None) = None
            match op:
                case self.__OP_READ:
                    result = await self.__handle_read(offset, size)
                case self.__OP_WRITE:
                    result = await self.__handle_write(offset, data)
                case self.__OP_STOP:
                    raise NbdIoConnectionError("Closed by kernel")
                case _:
                    raise NbdIoProtocolError(f"Unknown OP received: 0x{op:X}")
            assert result is not None
            await self.__send_response(link.remote_w, cookie, *result)

    async def cleanup(self) -> None:
        try:
            await self._do_cleanup()
        finally:
            self.__events_q = None
            self.__image = None

    async def __recv_request(
        self,
        reader: asyncio.StreamReader,
    ) -> tuple[int, int, int, int, bytes]:

        try:
            header = await reader.readexactly(self.__recv_st.size)
            (magic, flags, op, cookie, offset, size) = self.__recv_st.unpack(header)
            data = b""
            if op == self.__OP_WRITE and size > 0:
                data = await reader.readexactly(size)
        except (ConnectionError, asyncio.IncompleteReadError) as ex:
            raise NbdIoConnectionError("Can't receive request", ex)

        if magic != self.__MAGIC_RECV:
            raise NbdIoProtocolError(f"Invalid request magic: 0x{magic:X}")
        if flags:
            raise NbdIoProtocolError(f"Got non-zero request flags: 0x{flags:X}")
        return (op, cookie, offset, size, data)

    async def __send_response(
        self,
        writer: asyncio.StreamWriter,
        cookie: int, error: int, data: bytes=b"",
    ) -> None:

        try:
            header = self.__send_st.pack(self.__MAGIC_SEND, error, cookie)
            writer.write(header)
            if error == 0 and len(data) > 0:
                writer.write(data)
            await writer.drain()
        except ConnectionError as ex:
            raise NbdIoConnectionError("Can't send response", ex)

    async def __handle_read(self, offset: int, size: int) -> tuple[int, bytes]:
        assert self.__image
        if offset >= self.__image.size:
            return (errno.EINVAL, b"")

        data = await self._on_read(offset, size)
        if len(data) < size:
            if offset + size > self.__image.size:
                data += b"\x00" * (size - len(data))
            else:
                raise NbdIoProtocolError("Insufficient READ data")
        elif len(data) > size:
            raise NbdIoProtocolError("Too much READ data")

        return (0, data)

    async def __handle_write(self, offset: int, data: bytes) -> tuple[int, bytes]:
        assert self.__image
        if not self.__image.rw:
            return (errno.EPERM, b"")
        if offset >= self.__image.size:
            return (errno.ENOSPC, b"")
        await self._on_write(offset, data)
        return (0, b"")
