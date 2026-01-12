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


import sys
import os
import fcntl
import socket
import asyncio
import contextlib
import math

from typing import Final
from typing import Generator
from typing import AsyncGenerator

from ..logging import get_logger

from .. import tools

from .errors import NbdDeviceError
from .types import NbdImage
from .link import NbdLink


# =====
_NBD_CLEAR_SOCK:      Final[tuple[int, str]] = (0x0000AB04, "NBD_CLEAR_SOCK")
_NBD_DO_IT:           Final[tuple[int, str]] = (0x0000AB03, "NBD_DO_IT")
_NBD_DISCONNECT:      Final[tuple[int, str]] = (0x0000AB08, "NBD_DISCONNECT")
_NBD_SET_BLKSIZE:     Final[tuple[int, str]] = (0x0000AB01, "NBD_SET_BLKSIZE")
_NBD_SET_FLAGS:       Final[tuple[int, str]] = (0x0000AB0A, "NBD_SET_FLAGS")
_NBD_SET_SIZE_BLOCKS: Final[tuple[int, str]] = (0x0000AB07, "NBD_SET_SIZE_BLOCKS")
_NBD_SET_SOCK:        Final[tuple[int, str]] = (0x0000AB00, "NBD_SET_SOCK")
_NBD_SET_TIMEOUT:     Final[tuple[int, str]] = (0x0000AB09, "NBD_SET_TIMEOUT")
_BLKROSET:            Final[tuple[int, str]] = (0x0000125D, "BLKROSET")


def _ioctl(fd: int, ctl: tuple[int, str], value: (int | bytes)=0) -> None:
    (req, name) = ctl
    try:
        fcntl.ioctl(fd, req, value)
    except Exception as ex:
        raise NbdDeviceError(f"Ioctl {name} error", ex)


@contextlib.contextmanager
def _wrap_exceptions() -> Generator[None]:
    try:
        yield
    except NbdDeviceError:
        raise
    except Exception as ex:
        raise NbdDeviceError(tools.efmt(ex))


# =====
class NbdDevice:
    def __init__(self, path: str, block: int, timeout: float) -> None:
        self.__path = path
        self.__block = block
        self.__timeout = timeout

    # =====

    def get_path(self) -> str:
        return self.__path

    async def open_close(self) -> None:
        await asyncio.to_thread(self.__inner_open_close)

    def __inner_open_close(self) -> None:
        fd = os.open(self.__path, os.O_RDONLY)
        os.close(fd)

    @contextlib.asynccontextmanager
    async def open_prepared(self, link: NbdLink, image: NbdImage) -> AsyncGenerator[int]:
        with _wrap_exceptions():
            fd = await asyncio.to_thread(os.open, self.__path, os.O_RDWR)
            try:
                self.__cleanup(fd, close=False)
                self.__prepare(fd, image, link.device_s)
                yield fd
            finally:
                try:
                    self.__cleanup(fd, close=True)
                except Exception as ex:
                    get_logger(0).error("Cleanup error: %s", tools.efmt(ex))

    async def do_it(self, fd: int) -> None:
        logger = get_logger(0)
        logger.info("Running NBD_DO_IT ...")
        await asyncio.to_thread(_ioctl, fd, _NBD_DO_IT)  # Blocks here
        logger.info("Stopped NBD_DO_IT")

    def __prepare(self, fd: int, image: NbdImage, sock: socket.SocketType) -> None:
        logger = get_logger(0)

        blocks = (image.size + self.__block) // self.__block
        flags = (0 if image.rw else 2)  # NBD_FLAG_READ_ONLY
        ro_bytes = int(not image.rw).to_bytes(byteorder=sys.byteorder, length=4)  # Kinda ptr

        logger.info("Preparing %s: bytes=%s, bs=%s, blocks=%s, rw=%s ...",
                    self.__path, image.size, self.__block, blocks, image.rw)

        _ioctl(fd, _NBD_SET_BLKSIZE, self.__block)
        _ioctl(fd, _NBD_SET_SIZE_BLOCKS, blocks)
        _ioctl(fd, _NBD_SET_FLAGS, flags)
        _ioctl(fd, _BLKROSET, ro_bytes)
        _ioctl(fd, _NBD_SET_TIMEOUT, math.ceil(self.__timeout))
        _ioctl(fd, _NBD_SET_SOCK, sock.fileno())
        logger.info("Prepared")

    def __cleanup(self, fd: int, close: bool) -> None:
        _ioctl(fd, _NBD_DISCONNECT)  # Should be always OK ..
        _ioctl(fd, _NBD_CLEAR_SOCK)  # ... accordung to kernel sources
        if close:
            os.close(fd)
