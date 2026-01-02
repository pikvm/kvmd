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
import dataclasses

from typing import Generator
from typing import AsyncGenerator


# =====
@dataclasses.dataclass(frozen=True)
class NbdLink:
    device_s:  socket.SocketType
    remote_r:  asyncio.StreamReader
    remote_w:  asyncio.StreamWriter
    _remote_s: socket.SocketType

    @classmethod
    @contextlib.asynccontextmanager
    async def opened(cls) -> AsyncGenerator["NbdLink"]:
        (device_s, remote_s) = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM, 0)

        def close() -> None:
            for sock in [device_s, remote_s]:
                try:
                    sock.close()
                except Exception:
                    pass

        try:
            (remote_r, remote_w) = await asyncio.open_connection(sock=remote_s)
        except:  # noqa: E722
            close()
            raise

        try:
            yield NbdLink(device_s, remote_r, remote_w, remote_s)
        finally:
            # На самом деле мы должны использовать aiotools.close_writer(remote_w),
            # но для простоты обработки CancelledError этим можно пренебречь,
            # особенно с учетом того, что всё это живет в подпроцессе, который
            # будет отстрелян по завершении работы.
            #   device_s.close(); aiotools.close_writer(remote_w);
            close()

    @contextlib.contextmanager
    def shutdown_at_end(self) -> Generator[None]:
        try:
            yield
        finally:
            self.shutdown()

    def shutdown(self) -> None:
        for sock in [self.device_s, self._remote_s]:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
