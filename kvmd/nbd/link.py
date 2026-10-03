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
import socket
import contextlib

from typing import Self
from typing import Generator
from typing import AsyncGenerator


# =====
class BaseNbdLink:
    @property
    def device_s(self) -> socket.SocketType:
        raise NotImplementedError

    @contextlib.asynccontextmanager
    async def opened(self) -> AsyncGenerator[Self]:
        if self:  # XXX: Vulture and pylint hack
            raise NotImplementedError
        yield self

    def is_stopped(self) -> bool:
        raise NotImplementedError

    @contextlib.contextmanager
    def shutdown_at_end(self) -> Generator[None]:
        if self:  # XXX: Vulture and pylint hack
            raise NotImplementedError
        yield None

    def shutdown(self) -> bool:
        raise NotImplementedError


class NbdUserLink(BaseNbdLink):
    def __init__(self) -> None:
        self.__device_s: (socket.SocketType | None) = None
        self.__remote_s: (socket.SocketType | None) = None

        self.__remote_r: (asyncio.StreamReader | None) = None
        self.__remote_w: (asyncio.StreamWriter | None) = None

        self.__stopped = False

    @property
    def device_s(self) -> socket.SocketType:
        assert self.__device_s
        return self.__device_s

    @property
    def remote_r(self) -> asyncio.StreamReader:
        assert self.__remote_r
        return self.__remote_r

    @property
    def remote_w(self) -> asyncio.StreamWriter:
        assert self.__remote_w
        return self.__remote_w

    @contextlib.asynccontextmanager
    async def opened(self) -> AsyncGenerator[Self]:
        assert self.__device_s is None
        assert self.__remote_s is None
        assert self.__remote_r is None
        assert self.__remote_w is None

        (device_s, remote_s) = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM, 0)
        try:
            (self.__remote_r, self.__remote_w) = await asyncio.open_connection(sock=remote_s)
        except:  # noqa: E722
            for sock in [device_s, remote_s]:
                try:
                    sock.close()
                except Exception:
                    pass
            raise
        (self.__device_s, self.__remote_s) = (device_s, remote_s)

        try:
            yield self
        finally:
            # На самом деле мы должны использовать aiotools.close_writer(remote_w),
            # но для простоты обработки CancelledError этим можно пренебречь,
            # особенно с учетом того, что всё это живет в подпроцессе, который
            # будет отстрелян по завершении работы.
            #   device_s.close(); aiotools.close_writer(remote_w);
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
        ok = True
        for sock in [self.__device_s, self.__remote_s]:
            if sock:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except Exception:
                    ok = False
        self.__stopped = ok
        return ok

    def __close(self) -> None:
        self.shutdown()
        for sock in [self.__device_s, self.__remote_s]:
            if sock:
                try:
                    sock.close()
                except Exception:
                    pass
