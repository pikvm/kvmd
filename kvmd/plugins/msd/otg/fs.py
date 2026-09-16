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
import operator

from typing import AsyncGenerator

import aiofiles.os

from .... import tools


# =====
async def find_closest_mountpoint(path: str) -> str:
    return (await asyncio.to_thread(_find_closest_mountpoint, path))


def _find_closest_mountpoint(path: str) -> str:
    tools.check_abs(path)
    while not os.path.ismount(path):
        path = os.path.dirname(path)
    return path


# =====
async def walk_storage(dir_path: str) -> AsyncGenerator[tuple[str, (list[str] | None)]]:
    tools.check_abs(dir_path)
    if not (await aiofiles.os.path.isdir(dir_path)):
        raise NotADirectoryError(dir_path)

    # Итак, зачем это всё. Мы хотим установить обработчик inotify на каталог,
    # чтобы следить за его содержимым. Но если мы сначала считаем дерево
    # и лишь затем установим вотчеры, то у нас есть гонка между сканом и вотчем.
    # То есть, в промежутке между этими действиями на файловой системе мог
    # появиться файл, и мы его пропустим. Поэтому мы сначала делаем yield
    # имени каталога, чтобы завотчить его. И только потом углубляемся в его кишки.
    # Таким образом, после первой проходки дерева мы получим эвенты для всех
    # изменений, которые не вошли в изначальный скан дерева.
    yield (dir_path, None)

    (dirs, files) = await asyncio.to_thread(_list_dir, dir_path)
    yield (dir_path, files)

    for path in dirs:
        async for result in walk_storage(path):
            yield result


def _list_dir(dir_path: str) -> tuple[list[str], list[str]]:
    dirs: list[str] = []
    files: list[str] = []
    with os.scandir(dir_path) as dir_iter:
        for item in sorted(dir_iter, key=operator.attrgetter("name")):
            if (item.name.startswith(".") or item.name == "lost+found"):
                continue
            try:
                if item.is_dir(follow_symlinks=False):
                    item.stat()  # Проверяем, не сдохла ли смонтированная NFS
                    dirs.append(item.path)
                elif item.is_file(follow_symlinks=False):
                    files.append(item.path)
            except Exception:
                pass
    return (dirs, files)
