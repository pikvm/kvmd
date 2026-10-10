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


import os
import socket
import asyncio
import struct
import contextlib

from typing import Self
from typing import Final
from typing import Generator
from typing import AsyncGenerator

from ...yamlconf import Section
from ...yamlconf import Option

from ...validators.basic import valid_number
from ...validators.net import valid_url

from ... import aiotools
from ... import aiomulti

from ..types import NbdImage
from ..types import BaseNbdEvent

from ..link import BaseNbdLink

from ..errors import NbdRemoteError

from . import NbdUrl
from . import BaseNbdRemote


# =====
_PROTO: Final[str] = "nbd"


class _Url(NbdUrl):
    def _make_name(self, path: str) -> str:
        return (os.path.basename(path) or f"{self.host}-{self.port}")

    def _make_path(self, path: str) -> str:
        # The first / is a delimiter, the second+ should be passed to an export name.
        #   - https://github.com/NetworkBlockDevice/nbd/blob/master/doc/uri.md
        if path.startswith("/"):
            path = path[1:]
        return path


def _make_image(url: _Url, size: int, writable: bool) -> NbdImage:
    return NbdImage(
        url=url.raw,
        proto=_PROTO,
        name=url.name,
        size=size,
        mod_ts=0,
        writable=writable,
    )


async def _do_handshake(
    url: _Url,
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
) -> tuple[int, bool]:

    try:
        match (await reader.readexactly(16)):
            case b"NBDMAGIC\x00\x00\x42\x02\x81\x86\x12\x53":
                st = struct.Struct(">QI124s")
                (size, flags, _) = st.unpack(await reader.readexactly(st.size))

            case b"NBDMAGICIHAVEOPT":
                await reader.readexactly(struct.Struct(">H").size)  # Server flags, ignore
                writer.write(struct.pack(">I", 0))  # Client flags
                writer.write(b"IHAVEOPT")
                export_name = url.path.encode()
                writer.write(struct.pack(">II", 1, len(export_name)))
                writer.write(export_name)
                await writer.drain()
                st = struct.Struct(">QH124s")
                (size, flags, _) = st.unpack(await reader.readexactly(st.size))

            case _:
                raise NbdRemoteError("Invalid server header")
    except (ConnectionError, asyncio.IncompleteReadError):
        raise NbdRemoteError("Server refused this request (probably export is wrong)")

    writable = (not (flags & 0x02))
    return (size, writable)


class _Link(BaseNbdLink):
    def __init__(self, url: _Url, timeout: float) -> None:
        self.__url:     Final[_Url] = url
        self.__timeout: Final[float] = timeout

        self.__sock:  (socket.SocketType | None) = None
        self.__image: (NbdImage | None) = None
        self.__stopped = False

    @property
    def device_s(self) -> socket.SocketType:
        assert self.__sock
        return self.__sock

    @property
    def image(self) -> NbdImage:
        assert self.__image
        return self.__image

    @contextlib.asynccontextmanager
    async def opened(self) -> AsyncGenerator[Self]:
        assert self.__sock is None
        assert self.__image is None

        async with asyncio.timeout(self.__timeout):
            (reader, writer) = await asyncio.open_connection(
                host=self.__url.host,
                port=self.__url.port,
            )
        self.__sock = writer.transport.get_extra_info("socket")

        try:
            (size, writable) = await _do_handshake(self.__url, reader, writer)

            # Нужно остановить вычитывание чего-либо питоном перед тем, как отдавать сокет ядру.
            # У _SelectorSocketTransport() есть такой метод.
            writer.transport.pause_reading()  # type: ignore

            # Теперь можно поставить таймаут на реальный сокет, а не обертку TransportSocket
            self.__sock._sock.settimeout(self.__timeout)  # type: ignore  # pylint: disable=protected-access

            self.__image = _make_image(self.__url, size, writable)
            yield self
        finally:
            self.__close()

    def is_stopped(self) -> bool:
        return self.__stopped

    @contextlib.contextmanager
    def shutdown_at_end(self) -> Generator[None]:
        try:
            yield
        finally:
            self.shutdown()

    def shutdown(self) -> bool:
        try:
            if self.__sock:
                self.__sock.shutdown(socket.SHUT_RDWR)
        except Exception:
            self.__stopped = False
        else:
            self.__stopped = True
        return self.__stopped

    def __close(self) -> None:
        if self.__sock:
            self.shutdown()
            try:
                self.__sock.close()
            except Exception:
                pass


class NbdKernelRemote(BaseNbdRemote):
    def __init__(self, c: Section) -> None:
        super().__init__(c)

        self.__url:     Final[_Url] = _Url(c.url, 10809)
        self.__timeout: Final[float]  = c.timeout

        self.__image: (NbdImage | None) = None

    # =====

    @classmethod
    def get_schemes(cls) -> set[str]:
        return set([_PROTO])

    @classmethod
    def get_options(cls) -> dict[str, Option]:
        return {
            "url":     Option("", type=valid_url.mk(protos=cls.get_schemes())),
            "timeout": Option(3.0, type=valid_number.mk(min=1.0, max=30.0, type=float)),
        }

    # =====

    def get_timeout(self) -> float:
        return self.__timeout

    async def explore(self) -> NbdImage:
        async with asyncio.timeout(self.__timeout):
            (reader, writer) = await asyncio.open_connection(
                host=self.__url.host,
                port=self.__url.port,
            )
        try:
            (size, writable) = await _do_handshake(self.__url, reader, writer)
            return _make_image(self.__url, size, writable)
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def probe(self) -> NbdImage:  # noqa vulture-ignore
        self.__image = await self.explore()
        return self.__image

    def make_link(self) -> _Link:
        return _Link(self.__url, self.__timeout)

    async def serve(
        self,
        link: _Link,  # type: ignore
        events_q: aiomulti.AioMpQueue[BaseNbdEvent],
    ) -> None:

        _ = events_q
        assert self.__image
        if self.__image != link.image:
            raise NbdRemoteError("Probed image mismatch")
        await aiotools.wait_infinite()

    async def cleanup(self) -> None:
        self.__image = None
