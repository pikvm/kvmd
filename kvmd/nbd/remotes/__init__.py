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
import struct
import errno

from typing import Final
from typing import AsyncGenerator

from ...yamlconf import Section
from ...yamlconf import Option

from ...validators.basic import valid_number

from ... import tools
from ... import aiomulti

from ..errors import NbdError
from ..errors import NbdRemoteError
from ..errors import NbdIoConnectionError
from ..errors import NbdIoProtocolError

from ..types import NbdImage
from ..types import BaseNbdEvent
from ..types import NbdStatusEvent

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

    def __init__(self, c: Section) -> None:
        self.__retries_delay: Final[float] = c.retries_delay

        self.__recv_st = struct.Struct(">IHHQQI")
        self.__send_st = struct.Struct(">IIQ")

        self.__image: (NbdImage | None) = None
        self.__opened: (bool | None) = None
        self.__events_q: (aiomulti.AioMpQueue[BaseNbdEvent] | None) = None

    # =====

    @classmethod
    def get_schemes(cls) -> set[str]:
        raise NotImplementedError

    @classmethod
    def get_options(cls) -> dict[str, Option]:
        return {
            "retries_delay": Option(5.0, type=valid_number.mk(min=1.0, max=30.0, type=float)),
        }

    # =====

    def get_timeout(self) -> float:
        raise NotImplementedError

    async def _do_probe(self) -> NbdImage:
        raise NotImplementedError

    async def _do_ensure(self) -> NbdImage:
        raise NotImplementedError

    async def _do_close(self) -> None:
        raise NotImplementedError

    async def _on_read(self, offset: int, size: int) -> bytes:
        raise NotImplementedError

    async def _on_write(self, offset: int, data: bytes) -> None:
        raise NotImplementedError

    # =====

    async def explore(self) -> NbdImage:
        return (await self._do_probe())

    async def probe(self) -> NbdImage:  # noqa vulture-ignore
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

        async with self.__ensured_for_io("VALIDATE", False):
            pass  # Validate NbdImage after first probing

        while True:
            (op, cookie, offset, size, data) = await self.__recv_request(link.remote_r)
            op_error: (int | None) = None
            op_data = b""
            match op:
                case self.__OP_READ:
                    (op_error, op_data) = await self.__handle_read(offset, size)
                case self.__OP_WRITE:
                    op_error = await self.__handle_write(offset, data)
                case self.__OP_STOP:
                    raise NbdIoConnectionError("Closed by kernel")
                case _:
                    raise NbdIoProtocolError(f"Unknown OP received: 0x{op:X}")
            assert op_error is not None
            await self.__send_response(link.remote_w, cookie, op_error, op_data)

    async def cleanup(self) -> None:
        try:
            await self._do_close()
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
        cookie: int, error: int, data: bytes,
    ) -> None:

        try:
            header = self.__send_st.pack(self.__MAGIC_SEND, error, cookie)
            writer.write(header)
            if error == 0 and len(data) > 0:
                writer.write(data)
            await writer.drain()
        except ConnectionError as ex:
            raise NbdIoConnectionError("Can't send response", ex)

    # =====

    async def __handle_read(self, offset: int, size: int) -> tuple[int, bytes]:
        assert offset >= 0
        assert size >= 0
        assert self.__image

        if offset >= self.__image.size:
            return (errno.EINVAL, b"")
        if size == 0:
            return (0, b"")

        while True:
            try:
                async with self.__ensured_for_io("READ", True):
                    data = await self._on_read(offset, size)
                    break
            except NbdError:
                raise
            except Exception:
                await asyncio.sleep(self.__retries_delay)

        if len(data) < size:
            if offset + size > self.__image.size:
                data += b"\x00" * (size - len(data))
            else:
                raise NbdIoProtocolError("Insufficient READ data")
        elif len(data) > size:
            raise NbdIoProtocolError("Too much READ data")
        return (0, data)

    async def __handle_write(self, offset: int, data: bytes) -> int:
        assert offset >= 0
        assert self.__image

        if not self.__image.rw:
            return errno.EPERM
        if offset >= self.__image.size:
            return errno.ENOSPC

        if len(data) > 0:
            while True:
                try:
                    async with self.__ensured_for_io("WRITE", True):
                        await self._on_write(offset, data)
                        break
                except NbdError:
                    raise
                except Exception:
                    await asyncio.sleep(self.__retries_delay)
        return 0

    @contextlib.asynccontextmanager
    async def __ensured_for_io(self, action: str, send_event: bool) -> AsyncGenerator[None]:
        assert self.__image
        try:
            if not self.__opened:
                image = await self._do_ensure()
                if self.__image.rw is True and not image.rw:
                    raise NbdRemoteError("The source permissions changed: RW -> RO")
                if self.__image.size != image.size:
                    raise NbdRemoteError(f"The source file has a new size: {self.__image.size} -> {image.size}")
            yield
        except Exception as ex:
            self.__opened = False
            try:
                await self._do_close()
            except Exception:
                pass
            if send_event and not isinstance(ex, NbdError):
                msg = f"{action}: {tools.efmt(ex)}; Retrying ..."
                await self.__send_event(NbdStatusEvent(False, msg))
            raise
        else:
            if send_event and self.__opened is False:  # Ignore for None
                await self.__send_event(NbdStatusEvent(True, "Online"))
            self.__opened = True

    async def __send_event(self, event: BaseNbdEvent) -> None:
        assert self.__events_q is not None
        try:
            self.__events_q.put_nowait(event)
        except Exception as ex:
            raise NbdRemoteError(f"Can't send event: {tools.efmt(ex)}")
