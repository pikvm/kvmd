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
import math
import ctypes
import ctypes.util

from ctypes import c_int
from ctypes import c_uint64
from ctypes import c_size_t
from ctypes import c_char_p
from ctypes import c_void_p
from ctypes import POINTER
from ctypes import Structure

from typing import Final


# =====
_Context = c_void_p
_FileHandle = c_void_p


class NfsStat(Structure):
    _fields_ = [
        ("nfs_dev",        c_uint64),
        ("nfs_ino",        c_uint64),
        ("nfs_mode",       c_uint64),
        ("nfs_nlink",      c_uint64),
        ("nfs_uid",        c_uint64),
        ("nfs_gid",        c_uint64),
        ("nfs_rdev",       c_uint64),
        ("nfs_size",       c_uint64),
        ("nfs_blksize",    c_uint64),
        ("nfs_blocks",     c_uint64),
        ("nfs_atime",      c_uint64),
        ("nfs_mtime",      c_uint64),
        ("nfs_ctime",      c_uint64),
        ("nfs_atime_nsec", c_uint64),
        ("nfs_mtime_nsec", c_uint64),
        ("nfs_ctime_nsec", c_uint64),
        ("nfs_used",       c_uint64),
    ]


def _load_libnfs() -> ctypes.CDLL:
    path = ctypes.util.find_library("nfs")
    if not path:
        raise RuntimeError("Where is libnfs?")
    assert path
    lib = ctypes.CDLL(path)
    for (name, restype, argtypes) in [
        ("nfs_init_context",     _Context, []),
        ("nfs_destroy_context",  None,     [_Context]),
        ("nfs_get_error",        c_char_p, [_Context]),
        ("nfs_set_version",      c_int,    [_Context, c_int]),
        ("nfs_set_timeout",      None,     [_Context, c_int]),
        ("nfs_set_poll_timeout", None,     [_Context, c_int]),
        ("nfs_set_retrans",      None,     [_Context, c_int]),
        ("nfs_set_nfsport",      None,     [_Context, c_int]),
        ("nfs_mount",   c_int, [_Context, c_char_p, c_char_p]),
        ("nfs_umount",  c_int, [_Context]),
        ("nfs_open",    c_int, [_Context, c_char_p, c_int, POINTER(_FileHandle)]),
        ("nfs_close",   c_int, [_Context, _FileHandle]),
        ("nfs_pread",   c_int, [_Context, _FileHandle, c_void_p, c_size_t, c_uint64]),
        ("nfs_pwrite",  c_int, [_Context, _FileHandle, c_void_p, c_size_t, c_uint64]),
        ("nfs_fsync",   c_int, [_Context, _FileHandle]),
        ("nfs_fstat64", c_int, [_Context, _FileHandle, POINTER(NfsStat)]),
    ]:
        func = getattr(lib, name)
        if not func:
            raise RuntimeError(f"Where is libnfs.{name}?")
        setattr(func, "restype", restype)
        setattr(func, "argtypes", argtypes)
    return lib


_libnfs = _load_libnfs()


def _check_retval(ctx: _Context, name: str, retval: int) -> None:
    if retval < 0:
        text = _libnfs.nfs_get_error(ctx)
        if text:
            text = text.decode(errors="replace")
        raise OSError(-retval, f"{name}(): {text}")


_FS_FALLBACK_ENCODING: Final = "utf-8"
_FS_ENCODING:          Final = (sys.getfilesystemencoding() or _FS_FALLBACK_ENCODING)


def _fs_encode(path: str) -> bytes:
    try:
        return path.encode(_FS_ENCODING, "strict")
    except UnicodeEncodeError:
        return path.encode(_FS_FALLBACK_ENCODING, "strict")


class NfsFile:
    def __init__(self, ctx: _Context, fh: _FileHandle) -> None:
        self.__ctx = ctx
        self.__fh = fh

    def fstat(self) -> NfsStat:
        st = NfsStat()
        retval = _libnfs.nfs_fstat64(self.__ctx, self.__fh, ctypes.byref(st))
        _check_retval(self.__ctx, "nfs_fstat64", retval)
        return st

    def read(self, offset: int, size: int) -> bytes:
        buf = ctypes.create_string_buffer(size)
        retval = _libnfs.nfs_pread(self.__ctx, self.__fh, buf, size, offset)
        _check_retval(self.__ctx, "nfs_pread", retval)
        assert retval >= 0
        return buf.raw[:retval]

    def write(self, offset: int, data: bytes) -> None:
        buf = ctypes.create_string_buffer(data)
        retval = _libnfs.nfs_pwrite(self.__ctx, self.__fh, buf, len(data), offset)
        _check_retval(self.__ctx, "nfs_pwrite", retval)

    def fsync(self) -> None:
        retval = _libnfs.nfs_fsync(self.__ctx, self.__fh)
        _check_retval(self.__ctx, "nfs_fsync", retval)

    def close(self) -> None:
        retval = _libnfs.nfs_close(self.__ctx, self.__fh)
        _check_retval(self.__ctx, "nfs_close", retval)


class NfsClient:  # pylint: disable=too-many-instance-attributes
    def __init__(
        self,
        host: str,
        port: int,
        path: str,  # exportpath
        retrans: int,
        timeout: float,
    ) -> None:

        self.__host: Final = host
        self.__port: Final = port
        self.__path: Final = path

        self.__retrans: Final = min(1, retrans)
        self.__timeout: Final = math.ceil(min(1, timeout) * 1000)

        self.__ctx: (_Context | None) = None
        self.__files: list[NfsFile] = []

    def open(self) -> None:
        assert self.__ctx is None
        ctx = _libnfs.nfs_init_context()
        if ctx is None:
            raise RuntimeError("Can't initialize NFS context")

        try:
            _libnfs.nfs_set_nfsport(ctx, self.__port)
            _libnfs.nfs_set_retrans(ctx, self.__retrans)
            _libnfs.nfs_set_timeout(ctx, self.__timeout)
            _libnfs.nfs_set_poll_timeout(ctx, self.__timeout)

            host = _fs_encode(self.__host)
            path = _fs_encode(self.__path)
            retval = 0
            for version in [3, 4]:
                if _libnfs.nfs_set_version(ctx, version) != 0:
                    raise RuntimeError(f"Can't set NFS version {version}")
                retval = _libnfs.nfs_mount(ctx, host, path)
                if retval == 0:
                    break
            _check_retval(ctx, f"nfs_mount[NFSv{version}]", retval)

        except:  # noqa: E722
            _libnfs.nfs_destroy_context(ctx)
            raise
        self.__ctx = ctx

    def close(self) -> None:
        if self.__ctx:
            try:
                try:
                    for file in self.__files:
                        try:
                            file.close()
                        except Exception:
                            pass
                finally:
                    try:
                        _libnfs.nfs_umount(self.__ctx)
                    finally:
                        _libnfs.nfs_destroy_context(self.__ctx)
            except Exception:
                pass
            finally:
                self.__files = []
                self.__ctx = None

    def open_file(self, path: str, flags: int) -> NfsFile:
        assert self.__ctx
        fh = _FileHandle()
        retval = _libnfs.nfs_open(self.__ctx, _fs_encode(path), flags, ctypes.byref(fh))
        _check_retval(self.__ctx, "nfs_open", retval)
        file = NfsFile(self.__ctx, fh)
        self.__files.append(file)
        return file
