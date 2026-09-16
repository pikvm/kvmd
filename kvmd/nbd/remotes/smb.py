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

import smbc

from typing import Final

from ...yamlconf import Section
from ...yamlconf import Option

from ...validators.basic import valid_number
from ...validators.net import valid_url

from ..types import NbdImage

from . import NbdUrl
from . import BaseNbdRemote


# =====
@dataclasses.dataclass(frozen=True)
class _FileHandle:
    file: smbc.File
    rw:   bool


class NbdSmbRemote(BaseNbdRemote):
    def __init__(self, c: Section) -> None:
        super().__init__(c)

        self.__url:     Final[NbdUrl] = NbdUrl(c.url, 445)
        self.__user:    Final[str]    = c.user
        self.__passwd:  Final[str]    = c.passwd
        self.__timeout: Final[float]  = c.timeout

        self.__fh: (_FileHandle | None) = None

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
        fh = await self.__open()
        try:
            return (await self.__probe(fh))
        finally:
            await asyncio.to_thread(fh.file.close)

    async def _do_ensure(self) -> NbdImage:
        if self.__fh is None:
            self.__fh = await self.__open()
        return (await self.__probe(self.__fh))

    async def _do_close(self) -> None:
        if self.__fh is not None:
            try:
                await asyncio.to_thread(self.__fh.file.close)
            finally:
                self.__fh = None

    async def __open(self) -> _FileHandle:
        ctx = smbc.Context()  # pylint: disable=no-member
        ctx.port = self.__url.port
        if self.__user:
            cb = (lambda *args: (args[2], self.__user, self.__passwd))  # args[2] is a workgroup
            ctx.optionNoAutoAnonymousLogin = True  # noqa vulture-ignore
            ctx.functionAuthData = cb  # noqa vulture-ignore
        try:
            file = await asyncio.to_thread(ctx.open, self.__url.raw, os.O_RDWR)
            rw = True
        except smbc.PermissionError:  # pylint: disable=no-member
            file = await asyncio.to_thread(ctx.open, self.__url.raw, os.O_RDONLY)
            rw = False
        return _FileHandle(file, rw)

    async def __probe(self, fh: _FileHandle) -> NbdImage:
        st = await asyncio.to_thread(fh.file.fstat)
        return NbdImage(
            url=self.__url.raw,
            proto="SMB",
            name=self.__url.name,
            size=st[6],
            mod_ts=float(st[8]),
            rw=fh.rw,
        )

    async def _on_read(self, offset: int, size: int) -> bytes:
        return (await asyncio.to_thread(self.__seek_and_read, offset, size))

    def __seek_and_read(self, offset: int, size: int) -> bytes:
        assert self.__fh is not None
        self.__fh.file.lseek(offset, os.SEEK_SET)
        return self.__fh.file.read(size)

    async def _on_write(self, offset: int, data: bytes) -> None:
        await asyncio.to_thread(self.__seek_and_write, offset, data)

    def __seek_and_write(self, offset: int, data: bytes) -> None:
        assert self.__fh is not None
        self.__fh.file.lseek(offset, os.SEEK_SET)
        self.__fh.file.write(data)
