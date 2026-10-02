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

import aiohttp

from ....logging import get_logger

from ....inotify import Inotify

from ....yamlconf import Section
from ....yamlconf import Option

from ....clients.nbd import NbdClient
from ....nbd.types import NbdImage

from ....validators.os import valid_command

from .... import tools
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

from .storage import FileImage
from .storage import Storage

from .remote import Nbd

from .drive import Drive


# =====
@dataclasses.dataclass
class _VirtualDrive:
    image:     (FileImage | NbdImage | None)
    connected: bool
    cdrom:     bool
    rw:        bool


class _State:
    def __init__(self, nr: aiotools.AioNotifier) -> None:
        self.__nr = nr
        self.__vd: (_VirtualDrive | None) = None
        self.__region = aiotools.AioExclusiveRegion(MsdIsBusyError)
        self.__lock = asyncio.Lock()

    def __p_get_vd(self) -> (_VirtualDrive | None):
        assert self.__lock.locked()
        return self.__vd

    def __p_set_vd(self, vd: (_VirtualDrive | None)) -> None:
        assert self.__lock.locked()
        self.__vd = vd

    vd = property(__p_get_vd, __p_set_vd)

    def is_busy(self) -> bool:
        return self.__region.is_busy()

    @contextlib.contextmanager
    def busy_only(self) -> Generator[None]:
        try:
            with self.__region:
                self.__nr.notify()
                yield
        finally:
            self.__nr.notify()

    @contextlib.asynccontextmanager
    async def locked_only(self) -> AsyncGenerator[None]:
        async with self.__lock:
            yield

    @contextlib.asynccontextmanager
    async def busy_and_locked(self) -> AsyncGenerator[None]:
        with self.busy_only():
            async with self.locked_only():
                yield

    # =====

    def check_online_connected(self, drive: Drive) -> _VirtualDrive:
        assert self.is_busy()
        assert self.__lock.locked()
        if self.vd is None:
            raise MsdOfflineError()
        if not (self.vd.connected or drive.get_image_path()):
            raise MsdDisconnectedError()
        return self.vd

    def check_online_disconnected(self, drive: Drive) -> _VirtualDrive:
        assert self.is_busy()
        assert self.__lock.locked()
        if self.vd is None:
            raise MsdOfflineError()
        if self.vd.connected or drive.get_image_path():
            raise MsdConnectedError()
        return self.vd


# =====
class Plugin(BaseMsd):  # pylint: disable=too-many-instance-attributes
    def __init__(self, c: Section, nbd: NbdClient) -> None:
        super().__init__(c, nbd)

        self.__nbd = Nbd(nbd)

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
            vd: (dict | None) = None
            storage: (dict | None) = None

            if self.__state.vd:
                vd = dataclasses.asdict(self.__state.vd)
                if vd["image"]:
                    vd["image"].pop("path", None)  # FileImage
                    vd["image"].setdefault("url", None)  # FileImage
                    vd["image"].setdefault("in_storage", False)  # NbdImage
                    vd["image"].setdefault("removable", False)  # NbdImage
                    vd["image"].setdefault("complete", True)  # NbdImage

                storage = self.__storage.get_state()
                storage["downloading"] = (self.__reader.get_state() if self.__reader else None)
                storage["uploading"] = (self.__writer.get_state() if self.__writer else None)

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
        async with self.__state.busy_and_locked():
            try:
                self.__reset = True
                self.__drive.set_image_path("")
                self.__drive.set_cdrom_flag(False)
                self.__drive.set_rw_flag(False)
                await self.__storage.remount_ro()
            except Exception:
                get_logger(0).exception("Can't reset MSD properly")

    # =====

    async def set_params(
        self,
        name: (str | None)=None,
        guess: (bool | None)=None,
        cdrom: (bool | None)=None,
        rw: (bool | None)=None,
        remote_url: (str | None)=None,
        remote_params: (dict[str, Any] | None)=None,
    ) -> None:

        with self.__state.busy_only():
            async with self.__state.locked_only():
                self.__state.check_online_disconnected(self.__drive)

            # Это делается не под блокировкой, чтобы get_state() не подвисал
            if remote_url:
                await self.__nbd.unbind()
                await self.__nbd.plan(remote_url, remote_params)
            elif name is not None and self.__nbd.image:
                await self.__nbd.unbind()
                await self.__nbd.unplan()

            async with self.__state.locked_only():
                vd = self.__state.check_online_disconnected(self.__drive)

                # Если где-то прилетит CancelledError - не страшно,
                # настройка образа из хранилища идет первой операцией
                # и зафейлится сразу всё.

                if self.__nbd.image:
                    vd.image = self.__nbd.image
                elif name is not None:
                    if name:
                        vd.image = await self.__storage.get_image_by_name(name)
                    else:
                        vd.image = None

                if guess is not None and cdrom is None and vd.image:
                    vd.cdrom = vd.image.name.lower().endswith(".iso")

                if cdrom is not None:
                    vd.cdrom = cdrom

                if rw is not None:
                    vd.rw = rw

                if vd.rw and (vd.cdrom or (vd.image and not vd.image.writable)):
                    vd.rw = False

    async def set_connected(self, connected: bool) -> None:
        with self.__state.busy_only():
            if connected:
                if self.__nbd.image and self.__nbd.asserted_ready_to_bind:
                    await self.__nbd.bind()
                await self.__unsafe_connect()
            else:
                await self.__unsafe_disconnect()

    @aiotools.atomic_fg
    async def __unsafe_connect(self) -> None:
        async with self.__state.locked_only():
            vd = self.__state.check_online_disconnected(self.__drive)
            match vd.image:
                case FileImage():
                    if not (await vd.image.exists()):
                        raise MsdUnknownImageError()
                    if not vd.image.in_storage:
                        # Машина состояний не должна допускать того, чтобы в виртуальной конфигурации
                        # привода находился образ вне хранилища, но всё же перепроверим.
                        raise MsdUnknownImageError()
                    if vd.rw:
                        await self.__storage.remount_rw(vd.image)
                    path = vd.image.path

                case NbdImage():
                    if self.__nbd.image is None or not self.__nbd.asserted_running:
                        # Рассинхрон, засинхронится само в __systask_nbd()
                        raise MsdImageNotSelected()
                    vd.image = self.__nbd.image
                    path = self.__nbd.asserted_path

                case _:  # None
                    raise MsdImageNotSelected()

            self.__drive.set_rw_flag(vd.rw)
            self.__drive.set_cdrom_flag(vd.cdrom)
            self.__drive.set_image_path(path)
            vd.connected = True

    @aiotools.atomic_fg
    async def __unsafe_disconnect(self) -> None:
        disconnected = False
        try:
            async with self.__state.locked_only():
                vd = self.__state.check_online_connected(self.__drive)
                self.__drive.set_image_path("")
                vd.connected = False
                disconnected = True
                if isinstance(vd.image, FileImage):
                    await self.__storage.remount_ro()
        finally:
            # Не под блокировкой, чтобы не get_state() не подвис в ожидании unbind()
            if disconnected:
                # Не идеально, но сойдет
                if self.__nbd.image and self.__nbd.asserted_running:
                    await self.__nbd.unbind()

    @contextlib.asynccontextmanager
    async def read_image(self, name: str) -> AsyncGenerator[MsdFileReader]:
        with self.__state.busy_only():
            try:
                async with self.__state.locked_only():
                    self.__state.check_online_disconnected(self.__drive)
                    image = await self.__storage.get_image_by_name(name)

                    self.__reader = await MsdFileReader(
                        nr=self.__nr,
                        name=image.name,
                        path=image.path,
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

        image: (FileImage | None) = None
        complete = False

        async def finish_writing() -> None:
            # Делаем под блокировкой, чтобы эвент айнотифи не был обработан
            # до того, как мы не закончим все процедуры.
            async with self.__state.locked_only():
                try:
                    await self.__close_writer()
                finally:
                    if image:
                        self.__state.check_online_disconnected(self.__drive)
                        try:
                            await image.set_complete(complete)
                        finally:
                            try:
                                if remove_incomplete and not complete:
                                    await self.__storage.remove_image(image, fatal=False)
                            finally:
                                await self.__storage.remount_ro()

        with self.__state.busy_only():
            try:
                async with self.__state.locked_only():
                    self.__state.check_online_disconnected(self.__drive)
                    image = await self.__storage.make_image(name)

                    await self.__storage.remount_rw(image)
                    await image.set_complete(False)
                    self.__writer = await MsdFileWriter(
                        nr=self.__nr,
                        name=image.name,
                        path=image.path,
                        file_size=size,
                    ).open()

                self.__nr.notify()
                yield self.__writer
                complete = await self.__writer.finish()

            finally:
                await aiotools.shield_fg(finish_writing())

    @aiotools.atomic_fg
    async def remove(self, name: str) -> None:
        async with self.__state.busy_and_locked():
            vd = self.__state.check_online_disconnected(self.__drive)
            image = await self.__storage.get_image_by_name(name)

            if not image.removable:
                raise MsdImageStaticError()

            if vd.image == image:
                vd.image = None
            try:
                await self.__storage.remount_rw(image)
                await self.__storage.remove_image(image, fatal=True)
            finally:
                await aiotools.shield_fg(self.__storage.remount_ro())

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
        await aiotools.spawn_and_follow(
            self.__systask_inotify(),
            self.__systask_nbd(),
        )

    async def __systask_inotify(self) -> None:
        logger = get_logger(0)
        while True:
            try:
                # logger.info("+++++ Reloading storage ...")
                while not (self.__drive.is_enabled() and (await self.__storage.is_enabled())):
                    await asyncio.sleep(1)

                with Inotify() as inotify:
                    for path in self.__drive.get_watchable_paths():
                        await inotify.watch_all_changes(path)

                    async with self.__state.locked_only():
                        # Если только что включились и образ не подключен - протестить хранилище
                        if self.__state.vd is None and not self.__drive.get_image_path():
                            logger.info("Probing to remount storage ...")
                            await self.__storage.remount_probe()

                        storage_wds: set[int] = set()
                        async for path in self.__storage.reload():
                            storage_wds.add(await inotify.watch_all_changes(path))

                        await self.__update_vd()

                    while True:
                        reload = await self.__handle_inotify_events(inotify, storage_wds)
                        if reload or self.__reset:
                            break

            except Exception:
                logger.exception("Unexpected inotify watcher error")
                async with self.__state.locked_only():
                    self.__offline_vd()
                await asyncio.sleep(1)

    async def __handle_inotify_events(self, inotify: Inotify, storage_wds: set[int]) -> bool:
        vd_changed = False
        for event in (await inotify.get_series()):
            # get_logger(0).info("+++++ EVENT: %s", event)
            if event.restart:
                get_logger(0).info("Got restart event: %s", event)
                return True
            if event.wd in storage_wds:
                return True
            vd_changed = True

        if vd_changed:
            async with self.__state.locked_only():
                await self.__update_vd()

        elif self.__writer:  # Таймаут
            # При загрузке файла обновляем статистику раз в секунду (по таймауту).
            # Это не нужно при обычном релоаде, потому что там и так проверяются все разделы.
            async with self.__state.locked_only():
                await self.__storage.reload_parts()
            self.__nr.notify()

        return False

    def __offline_vd(self) -> None:
        self.__state.vd = None
        self.__nr.notify()

    async def __update_vd(self) -> None:
        path = self.__drive.get_image_path()  # Ядро всегда отдает realpath
        cdrom = self.__drive.get_cdrom_flag()
        rw = self.__drive.get_rw_flag()

        image: (FileImage | NbdImage | None) = None
        if path:
            if self.__nbd.image and path == self.__nbd.asserted_path:  # Это тоже realpath
                image = self.__nbd.image
            else:
                image = await self.__storage.get_image_by_path(path)
        else:
            if self.__state.vd:
                image = self.__state.vd.image
                cdrom = self.__state.vd.rw
                rw = self.__state.vd.rw
            if self.__nbd.image:
                image = self.__nbd.image
            elif isinstance(image, NbdImage):
                # Если был запланирован NBD, но KVMD-NBD рестартнули
                image = None

        self.__state.vd = _VirtualDrive(image, bool(path), cdrom, rw)
        self.__nr.notify()

    async def __systask_nbd(self) -> None:
        # Мы игнорируем /bind на KVMD-NBD, но реагируем на остальные действия. Так проще.
        logger = get_logger(0)
        ok = True
        while True:
            try:
                was_bound = False
                try:
                    async for _ in self.__nbd.poll_for_changes():
                        path = self.__drive.get_image_path()
                        was_bound = (self.__nbd.image is not None and path == self.__nbd.asserted_path)
                        if self.__nbd.image and not self.__nbd.asserted_running:
                            self.__drive.set_image_path("")
                        else:
                            self.__drive.trigger_image_inotify()
                        if not ok:
                            logger.info("NBD online")
                            ok = True
                except Exception:
                    if ok:
                        if was_bound:
                            self.__drive.set_image_path("")
                        else:
                            self.__drive.trigger_image_inotify()
                    raise
            except Exception as ex:
                if ok:
                    if isinstance(ex, aiohttp.ClientError):
                        logger.info("NBD is not available: %s", tools.efmt(ex))
                    else:
                        logger.exception("Unexpected NBD watcher error; disabling remote for now")
                    ok = False
                await asyncio.sleep(1)
