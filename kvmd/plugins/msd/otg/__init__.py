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


import asyncio
import contextlib
import dataclasses
import copy

from typing import Generator
from typing import AsyncGenerator
from typing import Any

from ....logging import get_logger

from ....inotify import Inotify

from ....yamlconf import Section
from ....yamlconf import Option

from ....clients.nbd import NbdClient

from ....validators.basic import valid_number
from ....validators.os import valid_command

from .... import aiotools

from .. import MsdIsBusyError
from .. import MsdOfflineError
from .. import MsdConnectedError
from .. import MsdDisconnectedError
from .. import MsdImageNotSelected
from .. import MsdUnknownImageError
from .. import MsdImageStaticError
from .. import BaseMsd
from .. import MsdFileReader
from .. import MsdFileWriter

from .storage import Image
from .storage import Storage
from .drive import Drive


# =====
@dataclasses.dataclass
class _VirtualDrive:
    image:     (Image | None)
    connected: bool
    cdrom:     bool
    rw:        bool

    def get_state(self) -> dict:
        state = dataclasses.asdict(self)
        if state["image"]:
            del state["image"]["path"]
        return state


class _State:
    def __init__(self, nr: aiotools.AioNotifier) -> None:
        self.__nr = nr

        self.__storage: (Storage | None) = None
        self.__vd: (_VirtualDrive | None) = None

        self.__region = aiotools.AioExclusiveRegion(MsdIsBusyError)
        self.__lock = asyncio.Lock()

    @property
    def storage(self) -> (Storage | None):
        assert self.__lock.locked()
        return self.__storage

    @property
    def vd(self) -> (_VirtualDrive | None):
        assert self.__lock.locked()
        return self.__vd

    def is_busy(self) -> bool:
        return self.__region.is_busy()

    @contextlib.asynccontextmanager
    async def locked_only(self) -> AsyncGenerator[None]:
        async with self.__lock:
            yield

    # =====

    @contextlib.contextmanager
    def busy_unlocked(self) -> Generator[None]:
        try:
            with self.__region:
                self.__nr.notify()
                yield
        finally:
            self.__nr.notify()

    @contextlib.asynccontextmanager
    async def locked_under_busy(self) -> AsyncGenerator[None]:
        assert self.is_busy()
        async with self.__lock:
            yield

    @contextlib.asynccontextmanager
    async def busy_locked(self) -> AsyncGenerator[None]:
        with self.busy_unlocked():
            async with self.locked_under_busy():
                yield

    # =====

    def check_online_connected(self, drive: Drive) -> tuple[Storage, _VirtualDrive]:
        assert self.is_busy()
        assert self.__lock.locked()
        if self.vd is None:
            raise MsdOfflineError()
        if not (self.vd.connected or drive.get_image_path()):
            raise MsdDisconnectedError()
        assert self.storage
        return (self.storage, self.vd)

    def check_online_disconnected(self, drive: Drive) -> tuple[Storage, _VirtualDrive]:
        assert self.is_busy()
        assert self.__lock.locked()
        if self.vd is None:
            raise MsdOfflineError()
        if self.vd.connected or drive.get_image_path():
            raise MsdConnectedError()
        assert self.storage
        return (self.storage, self.vd)

    # =====

    async def set_offline(self) -> None:
        assert self.__lock.locked()
        self.__storage = None
        self.__vd = None

    async def set_online(self, storage: Storage, real_vd: _VirtualDrive) -> None:
        assert self.__lock.locked()
        self.__storage = storage

        if real_vd.image:
            # При подключенном образе виртуальный стейт заменяется реальным
            assert real_vd.connected
            self.__vd = real_vd
        else:
            if self.__vd is None:
                # Если раньше MSD был отключен
                self.__vd = real_vd

            image = self.__vd.image
            if image and (not image.in_storage or not (await image.exists())):
                # Если только что отключили ручной образ вне хранилища или ранее выбранный образ был удален
                self.__vd.image = None

            self.__vd.connected = False


# =====
class Plugin(BaseMsd):  # pylint: disable=too-many-instance-attributes
    def __init__(self, c: Section, nbd: NbdClient) -> None:
        super().__init__(c, nbd)

        self.__read_chunk_size = c.read_chunk_size
        self.__write_chunk_size = c.write_chunk_size
        self.__sync_chunk_size = c.sync_chunk_size

        self.__drive = Drive(instance=0, lun=0)
        self.__storage = Storage(c.remount_cmd)

        self.__reader: (MsdFileReader | None) = None
        self.__writer: (MsdFileWriter | None) = None

        self.__nr = aiotools.AioNotifier()
        self.__state = _State(self.__nr)
        self.__reset = False

    @classmethod
    def get_plugin_options(cls) -> dict:
        return {
            "read_chunk_size":   Option(65536,   type=valid_number.mk(min=1024)),
            "write_chunk_size":  Option(65536,   type=valid_number.mk(min=1024)),
            "sync_chunk_size":   Option(4194304, type=valid_number.mk(min=1024)),

            "remount_cmd": Option([
                "/usr/bin/sudo", "--non-interactive",
                "/usr/bin/kvmd-helper-otgmsd-remount", "{mode}",
            ], type=valid_command),
        }

    # =====

    async def sysprep(self) -> None:
        get_logger(0).info("Using OTG drive %s as MSD ...", self.__drive.get_name())

    async def get_state(self) -> dict:
        async with self.__state.locked_only():
            storage: (dict | None) = None
            if self.__state.storage:
                assert self.__state.vd
                storage = self.__state.storage.get_state()
                storage["downloading"] = (self.__reader.get_state() if self.__reader else None)
                storage["uploading"] = (self.__writer.get_state() if self.__writer else None)

            vd: (dict | None) = None
            if self.__state.vd:
                assert self.__state.storage
                vd = self.__state.vd.get_state()

            return {
                "enabled": True,
                "online":  (bool(vd) and self.__drive.is_enabled()),
                "busy":    self.__state.is_busy(),
                "storage": storage,
                "drive":   vd,
            }

    async def trigger_state(self) -> None:
        self.__nr.notify(1)

    async def poll_state(self) -> AsyncGenerator[dict]:
        prev: dict = {}
        while True:
            if (await self.__nr.wait()) > 0:
                prev = {}
            new = await self.get_state()
            if not prev or (prev.get("online") != new["online"]):
                prev = copy.deepcopy(new)
                yield new
            else:
                diff: dict = {}
                for sub in ["busy", "drive"]:
                    if prev.get(sub) != new[sub]:
                        diff[sub] = new[sub]
                for sub in ["images", "parts", "downloading", "uploading"]:
                    if (prev.get("storage") or {}).get(sub) != (new["storage"] or {}).get(sub):
                        if "storage" not in diff:
                            diff["storage"] = {}
                        diff["storage"][sub] = new["storage"][sub]
                if diff:
                    prev = copy.deepcopy(new)
                    yield diff

    @aiotools.atomic_fg
    async def reset(self) -> None:
        async with self.__state.busy_locked():
            try:
                self.__reset = True
                self.__drive.set_image_path("")
                self.__drive.set_cdrom_flag(False)
                self.__drive.set_rw_flag(False)
                await self.__storage.remount_ro()
            except Exception:
                get_logger(0).exception("Can't reset MSD properly")

    # =====

    @aiotools.atomic_fg
    async def set_params(
        self,
        name: (str | None)=None,
        cdrom: (bool | None)=None,
        rw: (bool | None)=None,
        remote_params: (dict[str, Any] | None)=None,
    ) -> None:

        async with self.__state.busy_locked():
            (storage, vd) = self.__state.check_online_disconnected(self.__drive)

            if name is not None:
                if name:
                    vd.image = await storage.get_image_by_name(name)
                else:
                    vd.image = None

            if cdrom is not None:
                vd.cdrom = cdrom

            if rw is not None:
                vd.rw = rw

            if vd.rw and (vd.cdrom or (vd.image and not vd.image.writable)):
                vd.rw = False

    @aiotools.atomic_fg
    async def set_connected(self, connected: bool) -> None:
        async with self.__state.busy_locked():
            if connected:
                (storage, vd) = self.__state.check_online_disconnected(self.__drive)

                if vd.image is None:
                    raise MsdImageNotSelected()

                if not (await vd.image.exists()):
                    raise MsdUnknownImageError()

                if not vd.image.in_storage:
                    # Машина состояний не должна допускать того, чтобы в виртуальной конфигурации
                    # привода находился образ вне хранилища, но всё же перепроверим.
                    raise MsdUnknownImageError()

                if vd.rw:
                    await storage.remount_rw(vd.image)
                self.__drive.set_rw_flag(vd.rw)
                self.__drive.set_cdrom_flag(vd.cdrom)
                self.__drive.set_image_path(vd.image.path)
                vd.connected = True

            else:
                (storage, vd) = self.__state.check_online_connected(self.__drive)
                self.__drive.set_image_path("")
                vd.connected = False
                await storage.remount_ro()

    @contextlib.asynccontextmanager
    async def read_image(self, name: str) -> AsyncGenerator[MsdFileReader]:
        with self.__state.busy_unlocked():
            try:
                async with self.__state.locked_under_busy():
                    (storage, _) = self.__state.check_online_disconnected(self.__drive)
                    image = await storage.get_image_by_name(name)

                    self.__reader = await MsdFileReader(
                        nr=self.__nr,
                        name=image.name,
                        path=image.path,
                        chunk_size=self.__read_chunk_size,
                    ).open()

                self.__nr.notify()
                yield self.__reader

            finally:
                await aiotools.shield_fg(self.__close_reader())

    @contextlib.asynccontextmanager
    async def write_image(
        self,
        name: str,
        size: int,
        remove_incomplete: bool,
    ) -> AsyncGenerator[MsdFileWriter]:

        image: (Image | None) = None
        complete = False

        async def finish_writing() -> None:
            # Делаем под блокировкой, чтобы эвент айнотифи не был обработан
            # до того, как мы не закончим все процедуры.
            async with self.__state.locked_under_busy():
                try:
                    await self.__close_writer()
                finally:
                    if image:
                        (storage, _) = self.__state.check_online_disconnected(self.__drive)
                        try:
                            await image.set_complete(complete)
                        finally:
                            try:
                                if remove_incomplete and not complete:
                                    await storage.remove_image(image, fatal=False)
                            finally:
                                await storage.remount_ro()

        with self.__state.busy_unlocked():
            try:
                async with self.__state.locked_under_busy():
                    (storage, _) = self.__state.check_online_disconnected(self.__drive)
                    image = await storage.make_image(name)

                    await storage.remount_rw(image)
                    await image.set_complete(False)
                    self.__writer = await MsdFileWriter(
                        nr=self.__nr,
                        name=image.name,
                        path=image.path,
                        file_size=size,
                        sync_size=self.__sync_chunk_size,
                        chunk_size=self.__write_chunk_size,
                    ).open()

                self.__nr.notify()
                yield self.__writer
                complete = await self.__writer.finish()

            finally:
                await aiotools.shield_fg(finish_writing())

    @aiotools.atomic_fg
    async def remove(self, name: str) -> None:
        async with self.__state.busy_locked():
            (storage, vd) = self.__state.check_online_disconnected(self.__drive)
            image = await storage.get_image_by_name(name)

            if not image.removable:
                raise MsdImageStaticError()

            if vd.image == image:
                vd.image = None
            try:
                await storage.remount_rw(image)
                await storage.remove_image(image, fatal=True)
            finally:
                await aiotools.shield_fg(storage.remount_ro())

    # =====

    async def __close_reader(self) -> None:
        if self.__reader:
            try:
                await self.__reader.close()
            finally:
                self.__reader = None

    async def __close_writer(self) -> None:
        if self.__writer:
            try:
                await self.__writer.close()
            finally:
                self.__writer = None

    # =====

    @aiotools.atomic_fg
    async def cleanup(self) -> None:
        try:
            await self.__close_reader()
        finally:
            await self.__close_writer()

    async def systask(self) -> None:
        while True:
            try:
                await self.__systask_single()
            except Exception:
                get_logger(0).exception("Unexpected MSD watcher error")
                await asyncio.sleep(1)

    async def __systask_single(self) -> None:
        while (
            not self.__drive.is_enabled()
            or not (await self.__storage.is_probably_enabled())
        ):
            await asyncio.sleep(1)

        with Inotify() as inotify:
            for path in self.__drive.get_watchable_paths():
                await inotify.watch_all_changes(path)
            storage_wds = await self.__reload(inotify, True)

            while True:
                async with self.__state.locked_only():
                    if self.__state.vd is None:
                        return

                need_reload = False
                reload_storage = False
                for event in (await inotify.get_series()):
                    # get_logger(0).info("+++++ EVENT: %s", event)
                    if event.restart:
                        get_logger(0).info("Got restart event: %s", event)
                        return
                    need_reload = True
                    if event.wd in storage_wds:
                        reload_storage = True

                if need_reload:
                    await self.__reload(inotify, reload_storage)
                elif self.__writer:  # Таймаут
                    # При загрузке файла обновляем статистику раз в секунду (по таймауту).
                    # Это не нужно при обычном релоаде, потому что там и так проверяются все разделы.
                    async with self.__state.locked_only():
                        await self.__storage.reload_parts()
                    self.__nr.notify()

    async def __reload(self, inotify: Inotify, reload_storage: bool) -> set[int]:
        storage_wds: set[int] = set()
        async with self.__state.locked_only():
            try:
                if self.__state.storage is None or reload_storage:
                    # get_logger(0).info("+++++ Reloading storage ...")
                    async for path in self.__storage.reload():
                        storage_wds.add(await inotify.watch_all_changes(path))

                real_vd = await self.__get_real_vd()

                if self.__state.vd is None and real_vd.image is None:
                    # Если только что включились и образ не подключен - попробовать
                    # перемонтировать хранилище (и накатить всякие фиксы по необходимости).
                    get_logger(0).info("Probing to remount storage ...")
                    await self.__storage.remount_probe()

            except Exception:
                get_logger(0).exception("Error while reloading MSD state; switching offline")
                await self.__state.set_offline()
            else:
                await self.__state.set_online(self.__storage, real_vd)
        self.__nr.notify()
        return storage_wds

    async def __get_real_vd(self) -> _VirtualDrive:
        path = self.__drive.get_image_path()
        image: (Image | None) = None
        if path:
            image = await self.__storage.get_image_by_path(path)
        return _VirtualDrive(
            image=image,
            connected=bool(path),
            cdrom=self.__drive.get_cdrom_flag(),
            rw=self.__drive.get_rw_flag(),
        )
