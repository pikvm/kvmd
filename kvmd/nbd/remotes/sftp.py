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

import paramiko

from typing import Final

from ...yamlconf import Section
from ...yamlconf import Option

from ...validators.basic import valid_number
from ...validators.net import valid_url

from ..errors import NbdRemoteError
from ..types import NbdImage

from . import NbdUrl
from . import BaseNbdRemote


# =====
@dataclasses.dataclass(frozen=True)
class _FileHandle:
    ssh:  paramiko.SSHClient
    sftp: paramiko.SFTPClient
    file: paramiko.SFTPFile
    rw:   bool


def _close(
    ssh:  paramiko.SSHClient,
    sftp: (paramiko.SFTPClient | None),
    file: (paramiko.SFTPFile | None),
) -> None:

    try:
        if file is not None:
            file.close()
    finally:
        try:
            if sftp is not None:
                sftp.close()
        finally:
            ssh.close()


class NbdSftpRemote(BaseNbdRemote):
    def __init__(self, c: Section) -> None:
        super().__init__(c)

        self.__url:     Final[NbdUrl] = NbdUrl(c.url, 22)
        self.__user:    Final[str]    = c.user
        self.__passwd:  Final[str]    = c.passwd
        self.__timeout: Final[float]  = c.timeout

        self.__fh: (_FileHandle | None) = None

    # =====

    @classmethod
    def get_schemes(cls) -> set[str]:
        return set(["sftp"])

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
            await asyncio.to_thread(_close, fh.ssh, fh.sftp, fh.file)

    async def _do_ensure(self) -> NbdImage:
        if self.__fh is None:
            self.__fh = await self.__open()
        return (await self.__probe(self.__fh))

    async def _do_close(self) -> None:
        if self.__fh is not None:
            try:
                await asyncio.to_thread(_close, self.__fh.ssh, self.__fh.sftp, self.__fh.file)
            finally:
                self.__fh = None

    async def __open(self) -> _FileHandle:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        sftp: (paramiko.SFTPClient | None) = None
        file: (paramiko.SFTPFile | None) = None

        try:
            await asyncio.to_thread(
                ssh.connect,
                hostname=self.__url.host,
                port=self.__url.port,
                username=self.__user,
                password=self.__passwd,
                banner_timeout=self.__timeout,
                auth_timeout=self.__timeout,
                channel_timeout=self.__timeout,
            )
            sftp = await asyncio.to_thread(ssh.open_sftp)
            try:
                file = await asyncio.to_thread(sftp.file, self.__url.path, "r+")
                rw = True
            except OSError:  # Without a correct errno, sigh
                file = await asyncio.to_thread(sftp.file, self.__url.path, "r")
                rw = False
        except Exception:
            await asyncio.to_thread(_close, ssh, sftp, file)
            raise

        return _FileHandle(ssh, sftp, file, rw)

    async def __probe(self, fh: _FileHandle) -> NbdImage:
        st = await asyncio.to_thread(fh.file.stat)
        if st.st_size is None:
            raise NbdRemoteError("Can't fetch file size")
        return NbdImage(
            url=self.__url.raw,
            proto="SFTP",
            name=self.__url.name,
            size=st.st_size,
            mod_ts=float(st.st_mtime or 0),
            rw=fh.rw,
        )

    async def _on_read(self, offset: int, size: int) -> bytes:
        return (await asyncio.to_thread(self.__seek_and_read, offset, size))

    def __seek_and_read(self, offset: int, size: int) -> bytes:
        assert self.__fh is not None
        self.__fh.file.seek(offset, os.SEEK_SET)
        return self.__fh.file.read(size)

    async def _on_write(self, offset: int, data: bytes) -> None:
        await asyncio.to_thread(self.__seek_and_write, offset, data)

    def __seek_and_write(self, offset: int, data: bytes) -> None:
        assert self.__fh is not None
        self.__fh.file.seek(offset, os.SEEK_SET)
        self.__fh.file.write(data)
