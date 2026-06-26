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
import dataclasses
import errno
import math

from typing import Final
from typing import Generator
from typing import AsyncGenerator

from ..logging import get_logger

from .. import env
from .. import tools
from .. import aiotools

from .errors import NbdBoundError
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

_NBD_FLAG_HAS_FLAGS: Final[int] = 0b01
_NBD_FLAG_READ_ONLY: Final[int] = 0b10


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
@dataclasses.dataclass(frozen=True)
class _AttrPaths:
    pid:        str
    disconnect: str


class NbdDevice:
    __BLOCK:   Final[int] = 512
    __TIMEOUT: Final[int] = 3600

    def __init__(self, path: str, use_blkroset: bool) -> None:
        self.__path = path
        self.__use_blkroset = use_blkroset

    # =====

    def check_image(self, image: NbdImage) -> None:
        self.__get_blocks(image.size)

    def check_readiness(self) -> None:
        aps = self.__get_attr_paths()
        if not os.path.exists(self.__path):
            raise NbdDeviceError(f"Can't find NBD device: {self.__path}")
        if os.path.exists(aps.pid):
            raise NbdBoundError("NBD is already bound")

    async def force_disconnect(self) -> None:
        aps = self.__get_attr_paths()
        if os.path.exists(aps.pid):
            try:
                await aiotools.write_file(aps.disconnect, "\n")
                get_logger(0).info("Forced disconnection triggered via the Sysfs")
            except Exception as ex:
                if not tools.is_oserror(ex, errno.ENOLINK):
                    get_logger(0).error("Forced disconnection error: %s", tools.efmt(ex))

    async def wait_pid(self) -> None:
        aps = self.__get_attr_paths()
        while True:
            if os.path.exists(aps.pid):
                break
            await asyncio.sleep(1)

    async def open_close(self) -> None:
        fd = await asyncio.to_thread(os.open, self.__path, os.O_RDONLY)
        await asyncio.to_thread(os.close, fd)

    def __get_attr_paths(self) -> _AttrPaths:
        path = os.path.realpath(self.__path)
        name = os.path.basename(path)
        if not name.startswith("nbd"):
            raise NbdDeviceError(f"Can't parse nbd<N> from the device: {path}")
        return _AttrPaths(
            pid=f"{env.SYSFS_PREFIX}/sys/block/{name}/pid",
            disconnect=f"{env.SYSFS_PREFIX}/sys/devices/virtual/block/{name}/disconnect",
        )

    # =====

    @contextlib.asynccontextmanager
    async def open_prepared(self, link: NbdLink, image: NbdImage) -> AsyncGenerator[int]:
        self.check_image(image)
        self.check_readiness()
        with _wrap_exceptions():
            fd = await asyncio.to_thread(os.open, self.__path, os.O_RDWR)
            try:
                self.__prepare(fd, image, link.device_s)
                yield fd
            finally:
                try:
                    try:
                        _ioctl(fd, _NBD_CLEAR_SOCK)
                    finally:
                        os.close(fd)
                except Exception as ex:
                    get_logger(0).error("Cleanup error: %s", tools.efmt(ex))

    async def do_it(self, fd: int) -> None:
        logger = get_logger(0)
        logger.info("Running NBD_DO_IT ...")
        await asyncio.to_thread(_ioctl, fd, _NBD_DO_IT)  # Blocks here
        logger.info("Stopped NBD_DO_IT")

    def __get_blocks(self, size: int) -> int:
        # Для делящегося размера без остатка нужно прибавить 511,
        # чтобы деление его съело. Если у нас есть хотя бы +1,
        # то всё округлится до следующего целого блока.
        blocks = (size + (self.__BLOCK - 1)) // self.__BLOCK
        if blocks > 0xFF_FF_FF_FF:
            raise NbdDeviceError("The image is too big")
        return blocks

    def __prepare(self, fd: int, image: NbdImage, sock: socket.SocketType) -> None:
        logger = get_logger(0)

        blocks = self.__get_blocks(image.size)

        logger.info("Preparing %s: bytes=%s, bs=%s, blocks=%s, rw=%s ...",
                    self.__path, image.size, self.__BLOCK, blocks, image.rw)

        _ioctl(fd, _NBD_SET_BLKSIZE, self.__BLOCK)
        _ioctl(fd, _NBD_SET_SIZE_BLOCKS, blocks)

        _ioctl(fd, _NBD_CLEAR_SOCK)

        flags = _NBD_FLAG_HAS_FLAGS
        if not image.rw:
            flags |= _NBD_FLAG_READ_ONLY
        _ioctl(fd, _NBD_SET_FLAGS, flags)

        if self.__use_blkroset:
            ro_bytes = int(not image.rw).to_bytes(byteorder=sys.byteorder, length=4)  # Kinda ptr
            _ioctl(fd, _BLKROSET, ro_bytes)  # XXX: PiKVM kernel sets BLKROSET with NBD_SET_FLAGS

        _ioctl(fd, _NBD_SET_TIMEOUT, math.ceil(self.__TIMEOUT))
        _ioctl(fd, _NBD_SET_SOCK, sock.fileno())

        logger.info("Prepared")
