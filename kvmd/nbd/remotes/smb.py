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
import asyncio
import dataclasses
import urllib.parse

import smbc

from typing import Final

from ...yamlconf import Section
from ...yamlconf import Option

from ...validators.basic import valid_number
from ...validators.net import valid_url

from ..errors import NbdRemoteError
from ..types import NbdImage

from . import BaseNbdRemote


# =====
@dataclasses.dataclass(frozen=True)
class _SmbFile:
    handle: smbc.File
    name:   str
    rw:     bool


class NbdSmbRemote(BaseNbdRemote):
    def __init__(self, c: Section) -> None:
        super().__init__(c)

        self.__url:     Final[str]   = c.url
        self.__user:    Final[str]   = c.user
        self.__passwd:  Final[str]   = c.passwd
        self.__timeout: Final[float] = c.timeout

        self.__file: (_SmbFile | None) = None

    # =====

    @classmethod
    def get_schemes(cls) -> set[str]:
        return set(["smb"])

    @classmethod
    def get_options(cls) -> dict[str, Option]:
        return {
            "url":     Option("", type=valid_url.mk(protos=cls.get_schemes())),
            "user":    Option(""),
            "passwd":  Option(""),
            "timeout": Option(3.0, type=valid_number.mk(min=1.0, max=30.0, type=float)),
            **BaseNbdRemote.get_options(),
        }

    # =====

    def get_timeout(self) -> float:
        return self.__timeout

    async def _do_probe(self) -> NbdImage:
        file = await self.__open_file()
        try:
            return (await self.__probe(file))
        finally:
            await asyncio.to_thread(file.handle.close)

    async def _do_ensure(self) -> NbdImage:
        if self.__file is None:
            self.__file = await self.__open_file()
        return (await self.__probe(self.__file))

    async def _do_close(self) -> None:
        if self.__file is not None:
            try:
                await asyncio.to_thread(self.__file.handle.close)
            finally:
                self.__file = None

    async def __open_file(self) -> _SmbFile:
        parsed = urllib.parse.urlparse(self.__url)
        if len(parsed.path) == 0:
            raise NbdRemoteError("Can't parse SMB filename from URL")
        name = os.path.basename(parsed.path)
        if len(name) == 0:
            raise NbdRemoteError("Zero-length SMB filename")

        ctx = smbc.Context()  # pylint: disable=no-member
        if self.__user:
            cb = (lambda *args: (args[2], self.__user, self.__passwd))  # args[2] is a workgroup
            ctx.optionNoAutoAnonymousLogin = True
            ctx.functionAuthData = cb

        try:
            handle = await asyncio.to_thread(ctx.open, self.__url, os.O_RDWR)
            rw = True
        except smbc.PermissionError:  # pylint: disable=no-member
            handle = await asyncio.to_thread(ctx.open, self.__url, os.O_RDONLY)
            rw = False
        return _SmbFile(handle, name, rw)

    async def __probe(self, file: _SmbFile) -> NbdImage:
        st = await asyncio.to_thread(file.handle.fstat)
        return NbdImage(
            url=self.__url,
            proto="SMB",
            name=file.name,
            size=st[6],
            mod_ts=float(st[8]),
            rw=file.rw,
        )

    async def _on_read(self, offset: int, size: int) -> bytes:
        return (await asyncio.to_thread(self.__seek_and_read, offset, size))

    def __seek_and_read(self, offset: int, size: int) -> bytes:
        assert self.__file is not None
        self.__file.handle.lseek(offset, os.SEEK_SET)
        return self.__file.handle.read(size)

    async def _on_write(self, offset: int, data: bytes) -> None:
        await asyncio.to_thread(self.__seek_and_write, offset, data)

    def __seek_and_write(self, offset: int, data: bytes) -> bytes:
        assert self.__file is not None
        self.__file.handle.lseek(offset, os.SEEK_SET)
        return self.__file.handle.write(data)
