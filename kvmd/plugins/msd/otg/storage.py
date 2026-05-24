# ========================================================================== #
#                                                                            #
#    KVMD - The main PiKVM daemon.                                           #
#                                                                            #
#    Copyright (C) 2018-2024  Maxim Devaev <mdevaev@gmail.com>               #
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

from typing import AsyncGenerator

import aiofiles
import aiofiles.os

from .... import tools
from .... import fstab
from .... import aiohelpers

from .. import MsdError
from .. import MsdOfflineError
from .. import MsdUnknownImageError
from .. import MsdImageExistsError

from . import fs


# =====
@dataclasses.dataclass(frozen=True)
class _ImageDc:
    name:       str
    path:       str
    in_storage: bool  = dataclasses.field(compare=False)
    # For _reload():
    complete:   bool  = dataclasses.field(init=False, compare=False)
    removable:  bool  = dataclasses.field(init=False, compare=False)
    size:       int   = dataclasses.field(init=False, compare=False)
    mod_ts:     float = dataclasses.field(init=False, compare=False)


class Image(_ImageDc):
    def __init__(self, name: str, path: str, in_storage: bool, adopted: bool) -> None:
        tools.check_abs(path)
        path = os.path.normpath(path)
        if not in_storage:
            assert not adopted

        super().__init__(name, path, in_storage)

        self.__adopted = adopted
        (self.__dir_path, file_name) = os.path.split(path)
        self.__incomplete_path = os.path.join(self.__dir_path, f".__{file_name}.incomplete")

    async def _reload(self) -> None:
        complete = await self.__is_complete()
        removable = await self.__is_removable()
        (size, mod_ts) = await self.__get_stat()
        object.__setattr__(self, "complete", complete)
        object.__setattr__(self, "removable", removable)
        object.__setattr__(self, "size", size)
        object.__setattr__(self, "mod_ts", mod_ts)

    async def __is_complete(self) -> bool:
        if not self.in_storage:
            return True
        return (not (await aiofiles.os.path.exists(self.__incomplete_path)))

    async def __is_removable(self) -> bool:
        if not self.in_storage:
            return False
        if not self.__adopted:
            return True
        return (await aiofiles.os.access(self.__dir_path, os.W_OK))

    async def __get_stat(self) -> tuple[int, float]:
        try:
            st = (await aiofiles.os.stat(self.path))
            return (st.st_size, st.st_mtime)
        except Exception:
            return (0, 0.0)

    # =====

    async def exists(self) -> bool:
        return (await aiofiles.os.path.exists(self.path))

    async def _remove(self, fatal: bool) -> None:
        assert self.in_storage
        assert self.removable
        removed = False
        try:
            await aiofiles.os.remove(self.path)
            removed = True
        except FileNotFoundError:
            pass
        except Exception:
            if fatal:
                raise
        finally:
            # Удаляем .incomplete вместе с файлом
            if removed:
                await self.set_complete(True)

    async def set_complete(self, flag: bool) -> None:
        assert self.in_storage
        if flag:
            try:
                await aiofiles.os.remove(self.__incomplete_path)
            except FileNotFoundError:
                pass
        else:
            async with aiofiles.open(self.__incomplete_path, "w"):
                pass
        await self._reload()


async def _make_image(name: str, path: str, in_storage: bool, adopted: bool) -> Image:
    image = Image(name, path, in_storage, adopted)
    await image._reload()  # pylint: disable=protected-access
    return image


# =====
@dataclasses.dataclass(frozen=True)
class _PartDc:
    name:     str
    # For _reload():
    size:     int  = dataclasses.field(init=False, compare=False)
    free:     int  = dataclasses.field(init=False, compare=False)
    writable: bool = dataclasses.field(init=False, compare=False)


class _Part(_PartDc):
    def __init__(self, name: str, path: str) -> None:
        assert not name.startswith("/")
        assert not name.endswith("/")
        tools.check_abs(path)
        path = os.path.normpath(path)

        super().__init__(name)

        self.__path = path

    async def _reload(self) -> None:  # Only for Storage()
        st = await aiofiles.os.statvfs(self.__path)
        if self.name == "":
            writable = True  # Специальный случай для корневого раздела хранилища
        else:
            writable = (await aiofiles.os.access(self.__path, os.W_OK))
        object.__setattr__(self, "size", st.f_blocks * st.f_frsize)
        object.__setattr__(self, "free", st.f_bavail * st.f_frsize)
        object.__setattr__(self, "writable", writable)


async def _make_part(name: str, path: str) -> _Part:
    part = _Part(name, path)
    await part._reload()  # pylint: disable=protected-access
    return part


# =====
def _check_image_name(name: str) -> None:
    if (
        not name
        or name.startswith((".", "/"))
        or name.endswith("/")
        or ".." in name.split("/")
    ):
        raise ValueError(f"Invalid relative image name: {name}")


class Storage:
    def __init__(self, remount_cmd: list[str]) -> None:
        self.__remount_cmd = remount_cmd

        self.__root_path: (str | None) = None
        self.__images: (dict[str, Image] | None) = None
        self.__parts: (dict[str, _Part] | None) = None

    def __get_root_path(self) -> str:  # Only for Image()
        if self.__root_path is None:
            raise MsdOfflineError()
        return self.__root_path

    def __get_images(self) -> dict[str, Image]:
        if self.__images is None:
            raise MsdOfflineError()
        return dict(self.__images)

    def __get_parts(self) -> dict[str, _Part]:
        if self.__parts is None:
            raise MsdOfflineError()
        return dict(self.__parts)

    async def is_probably_enabled(self) -> bool:
        try:
            root_path = fstab.find_msd().root_path
        except Exception:
            return False
        return (await aiofiles.os.access(root_path, (os.R_OK | os.X_OK)))

    def get_state(self) -> dict:
        images: dict = self.__get_images()
        for name in list(images):
            images[name] = dataclasses.asdict(images[name])
            del images[name]["name"]
            del images[name]["path"]
            del images[name]["in_storage"]
        parts: dict = self.__get_parts()
        for name in list(parts):
            parts[name] = dataclasses.asdict(parts[name])
            del parts[name]["name"]
        return {"images": images, "parts": parts}

    async def reload(self) -> AsyncGenerator[str]:
        self.__root_path = None
        self.__images = None
        self.__parts = None

        root_path = fstab.find_msd().root_path
        images: dict[str, Image] = {}
        parts: dict[str, _Part] = {}

        async for (dir_path, files) in fs.walk_storage(root_path):
            if files is None:
                yield dir_path
                continue

            mnt_path = await fs.find_closest_mountpoint(dir_path)
            for file_path in files:
                name = os.path.relpath(file_path, root_path)
                _check_image_name(name)
                images[name] = await _make_image(name, file_path, True, (mnt_path != root_path))

            if dir_path != root_path and os.path.ismount(dir_path):
                name = os.path.relpath(dir_path, root_path)
                _check_image_name(name)
                parts[name] = await _make_part(name, dir_path)

        parts[""] = await _make_part("", root_path)

        self.__root_path = root_path
        self.__images = images
        self.__parts = parts

    async def reload_parts(self) -> None:
        await asyncio.gather(*[
            part._reload()  # pylint: disable=protected-access
            for part in self.__get_parts().values()
        ])

    # =====

    async def remove_image(self, image: Image, fatal: bool) -> None:
        assert image.in_storage
        assert image.removable
        if image.name in self.__get_images():
            await image._remove(fatal)  # pylint: disable=protected-access

    async def make_image(self, name: str) -> Image:
        _check_image_name(name)
        root_path = self.__get_root_path()
        path = os.path.join(root_path, name)
        mnt_path = await fs.find_closest_mountpoint(path)
        image = await _make_image(name, path, True, (mnt_path != root_path))
        if image.name in self.__get_images() or (await image.exists()):
            raise MsdImageExistsError()
        return image

    async def get_image_by_name(self, name: str) -> Image:
        _check_image_name(name)
        image = self.__get_images().get(name)
        if image is None or not (await image.exists()):
            raise MsdUnknownImageError()
        assert image.in_storage
        return image

    async def get_image_by_path(self, path: str) -> Image:
        tools.check_abs(path)
        path = os.path.normpath(path)
        for image in self.__get_images().values():
            if image.path == path:
                return image
        name = os.path.basename(path)
        return (await _make_image(name, path, False, False))

    # =====

    async def remount_rw(self, image: Image) -> None:
        assert image.in_storage
        root_path = self.__get_root_path()
        mnt_path = await fs.find_closest_mountpoint(image.path)
        if mnt_path == root_path:
            await self.__remount(rw=True, fatal=True)

    async def remount_ro(self) -> None:
        await self.__remount(rw=False, fatal=False)

    async def remount_probe(self) -> None:
        await self.__remount(rw=True, fatal=True)
        await self.__remount(rw=False, fatal=True)

    async def __remount(self, rw: bool, fatal: bool) -> None:
        if not (await aiohelpers.remount("MSD", self.__remount_cmd, rw)):
            if fatal:
                raise MsdError("Can't execute remount helper")
