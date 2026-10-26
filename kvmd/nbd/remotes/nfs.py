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
import errno
import dataclasses

from typing import Final

from ...yamlconf import Section
from ...yamlconf import Option

from ...validators.basic import valid_number
from ...validators.net import valid_url

from ... import tools

from ..types import NbdImage
from ..url import NbdUrl

from . import BaseNbdUserRemote

from ._nfs import NfsClient
from ._nfs import NfsFile


# =====
@dataclasses.dataclass(frozen=True)
class _FileHandle:
    client:   NfsClient
    file:     NfsFile
    writable: bool


class NbdNfsRemote(BaseNbdUserRemote):
    __PROTO: Final = "nfs"

    def __init__(self, c: Section) -> None:
        super().__init__(c)

        self.__url:     Final[NbdUrl] = NbdUrl(c.url, 2049)
        self.__timeout: Final[float]  = c.timeout

        self.__fh: (_FileHandle | None) = None

    # =====

    @classmethod
    def get_schemes(cls) -> set[str]:
        return set([cls.__PROTO])

    @classmethod
    def get_options(cls) -> dict[str, Option]:
        return {
            "url":     Option("", type=valid_url.mk(protos=cls.get_schemes())),
            "timeout": Option(3.0, type=valid_number.mk(min=1.0, max=30.0, type=float)),
            **BaseNbdUserRemote.get_options(),
        }

    # =====

    def get_timeout(self) -> float:
        return self.__timeout

    async def _do_probe(self) -> NbdImage:
        fh = await self.__open()
        try:
            return (await self.__probe(fh))
        finally:
            await asyncio.to_thread(fh.client.close)

    async def _do_ensure(self) -> NbdImage:
        if self.__fh is None:
            self.__fh = await self.__open()
        return (await self.__probe(self.__fh))

    async def _do_close(self) -> None:
        if self.__fh is not None:
            try:
                await asyncio.to_thread(self.__fh.client.close)
            finally:
                self.__fh = None

    async def __open(self) -> _FileHandle:
        client = NfsClient(
            host=self.__url.host,
            port=self.__url.port,
            path=os.path.dirname(self.__url.path),
            retrans=3,
            timeout=self.__timeout,
        )
        await asyncio.to_thread(client.open)
        try:
            try:
                file = await asyncio.to_thread(client.open_file, self.__url.name, os.O_RDWR)
                writable = True
            except Exception as ex:
                if not tools.is_oserror(ex, errno.ERANGE):
                    raise
                file = await asyncio.to_thread(client.open_file, self.__url.name, os.O_RDONLY)
                writable = False
        except:  # noqa: E722
            await asyncio.to_thread(client.close)
            raise
        return _FileHandle(client, file, writable)

    async def __probe(self, fh: _FileHandle) -> NbdImage:
        st = await asyncio.to_thread(fh.file.fstat)
        return NbdImage(
            url=self.__url.raw,
            proto=self.__PROTO,
            name=self.__url.name,
            size=st.nfs_size,
            mod_ts=float(st.nfs_mtime),
            writable=fh.writable,
        )

    async def _on_read(self, offset: int, size: int) -> bytes:
        assert self.__fh
        return (await asyncio.to_thread(self.__fh.file.read, offset, size))

    async def _on_write(self, offset: int, data: bytes) -> None:
        assert self.__fh
        await asyncio.to_thread(self.__fh.file.write, offset, data)
