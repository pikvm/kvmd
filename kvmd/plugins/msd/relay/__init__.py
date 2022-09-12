# ========================================================================== #
#                                                                            #
#    KVMD - The main PiKVM daemon.                                           #
#                                                                            #
#    Copyright (C) 2018-2022  Maxim Devaev <mdevaev@gmail.com>               #
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
import functools

from typing import AsyncGenerator

from ....logging import get_logger

from .... import aiotools

from ....yamlconf import Option

from ....validators.basic import valid_bool
from ....validators.basic import valid_number
from ....validators.basic import valid_int_f1
from ....validators.basic import valid_float_f01
from ....validators.os import valid_abs_path
from ....validators.hw import valid_gpio_pin

from .. import MsdError
from .. import MsdIsBusyError
from .. import MsdOfflineError
from .. import MsdConnectedError
from .. import MsdDisconnectedError
from .. import MsdMultiNotSupported
from .. import MsdCdromNotSupported
from .. import MsdRwNotSupported
from .. import BaseMsdReader
from .. import BaseMsd
from .. import MsdFileWriter

from .gpio import Gpio

from .drive import DeviceInfo


# =====
class Plugin(BaseMsd):  # pylint: disable=too-many-instance-attributes
    def __init__(  # pylint: disable=super-init-not-called,too-many-arguments
        self,
        upload_chunk_size: int,
        sync_chunk_size: int,

        gpio_device_path: str,
        target_pin: int,
        reset_inverted: bool,
        reset_pin: int,

        device_path: str,
        init_delay: float,
        init_retries: int,
        reset_delay: float,
    ) -> None:

        self.__upload_chunk_size = upload_chunk_size
        self.__sync_chunk_size = sync_chunk_size

        self.__device_path = device_path
        self.__init_delay = init_delay
        self.__init_retries = init_retries

        self.__gpio = Gpio(gpio_device_path, target_pin, reset_pin, reset_inverted, reset_delay)

        self.__device_info: (DeviceInfo | None) = None
        self.__connected = False

        self.__device_writer: (MsdFileWriter | None) = None

        self.__notifier = aiotools.AioNotifier()
        self.__region = aiotools.AioExclusiveRegion(MsdIsBusyError, self.__notifier)

    @classmethod
    def get_plugin_options(cls) -> dict:
        return {
            "upload_chunk_size": Option(65536,   type=functools.partial(valid_number, min=1024)),
            "sync_chunk_size":   Option(4194304, type=functools.partial(valid_number, min=1024)),

            "gpio_device":    Option("/dev/gpiochip0", type=valid_abs_path, unpack_as="gpio_device_path"),
            "target_pin":     Option(-1,    type=valid_gpio_pin),
            "reset_pin":      Option(-1,    type=valid_gpio_pin),
            "reset_inverted": Option(False, type=valid_bool),

            "device":       Option("",  type=valid_abs_path, unpack_as="device_path"),
            "init_delay":   Option(1.0, type=valid_float_f01),
            "init_retries": Option(5,   type=valid_int_f1),
            "reset_delay":  Option(1.0, type=valid_float_f01),
        }

    def sysprep(self) -> None:
        logger = get_logger(0)
        self.__gpio.open()
        logger.info("Using %r as MSD", self.__device_path)
        try:
            aiotools.run_sync(self.__load_device_info())
        except Exception as err:
            log = (logger.error if isinstance(err, MsdError) else logger.exception)
            log("MSD is offline: %s", err)

    async def get_state(self) -> dict:
        storage: (dict | None) = None
        drive: (dict | None) = None
        if self.__device_info:
            storage = {
                "size": self.__device_info.size,
                "free": self.__device_info.free,
                "uploading": (self.__device_writer.get_state() if self.__device_writer else None),
            }
            drive = {
                "image": (self.__device_info.image and dataclasses.asdict(self.__device_info.image)),
                "connected": self.__connected,
            }
        return {
            "enabled": True,
            "online": bool(self.__device_info),
            "busy": self.__region.is_busy(),
            "storage": storage,
            "drive": drive,
            "features": {
                "multi": False,
                "cdrom": False,
                "rw": False,
            },
        }

    async def poll_state(self) -> AsyncGenerator[dict, None]:
        prev_state: dict = {}
        while True:
            state = await self.get_state()
            if state != prev_state:
                yield state
                prev_state = state
            await self.__notifier.wait()

    @aiotools.atomic_fg
    async def reset(self) -> None:
        await aiotools.run_region_task(
            "Can't reset MSD or operation was not completed",
            self.__region, self.__inner_reset,
        )

    @aiotools.atomic_fg
    async def __inner_reset(self) -> None:
        await self.__gpio.reset()
        self.__gpio.switch_to_local()
        self.__connected = False
        await self.__load_device_info()
        get_logger(0).info("MSD reset has been successful")

    @aiotools.atomic_fg
    async def cleanup(self) -> None:
        try:
            await self.__close_device_writer()
        finally:
            self.__gpio.close()

    # =====

    @aiotools.atomic_fg
    async def set_params(
        self,
        name: (str | None)=None,
        cdrom: (bool | None)=None,
        rw: (bool | None)=None,
    ) -> None:

        async with self.__working():
            if name is not None:
                raise MsdMultiNotSupported()
            if cdrom is not None:
                raise MsdCdromNotSupported()
            if rw is not None:
                raise MsdRwNotSupported()

    @aiotools.atomic_fg
    async def set_connected(self, connected: bool) -> None:
        async with self.__working():
            async with self.__region:
                if connected:
                    if self.__connected:
                        raise MsdConnectedError()
                    self.__gpio.switch_to_server()
                    get_logger(0).info("MSD switched to Server")
                else:
                    if not self.__connected:
                        raise MsdDisconnectedError()
                    self.__gpio.switch_to_local()
                    try:
                        await self.__load_device_info()
                    except Exception:
                        if self.__connected:
                            self.__gpio.switch_to_server()
                        raise
                    get_logger(0).info("MSD switched to KVM: %s", self.__device_info)
                self.__connected = connected

    @contextlib.asynccontextmanager
    async def read_image(self, name: str) -> AsyncGenerator[BaseMsdReader, None]:
        async with self.__working():
            if self is not None:  # XXX: Vulture and pylint hack
                raise MsdMultiNotSupported()
        yield BaseMsdReader()

    @contextlib.asynccontextmanager
    async def write_image(self, name: str, size: int, remove_incomplete: (bool | None)) -> AsyncGenerator[MsdFileWriter, None]:
        async with self.__working():
            if remove_incomplete is not None:
                raise MsdMultiNotSupported()
            async with self.__region:
                try:
                    assert self.__device_info
                    if self.__connected:
                        raise MsdConnectedError()

                    self.__device_writer = await MsdFileWriter(
                        notifier=self.__notifier,
                        path=self.__device_info.path,
                        file_size=size,
                        sync_size=self.__sync_chunk_size,
                        chunk_size=self.__upload_chunk_size,
                    ).open()

                    await self.__write_image_info(False)
                    self.__notifier.notify()
                    yield self.__device_writer
                    await self.__write_image_info(True)
                finally:
                    try:
                        await aiotools.shield_fg(self.__close_device_writer())
                    finally:
                        await aiotools.shield_fg(self.__load_device_info())

    @aiotools.atomic_fg
    async def remove(self, name: str) -> None:
        async with self.__working():
            raise MsdMultiNotSupported()

    # =====

    @contextlib.asynccontextmanager
    async def __working(self) -> AsyncGenerator[None, None]:
        if not self.__device_info:
            raise MsdOfflineError()
        yield

    # =====

    async def __write_image_info(self, complete: bool) -> None:
        assert self.__device_writer
        assert self.__device_info
        if not (await self.__device_info.write_image_info(self.__device_writer, complete)):
            get_logger().error("Can't write image info because device is full")

    async def __close_device_writer(self) -> None:
        if self.__device_writer:
            await self.__device_writer.close()  # type: ignore
            self.__device_writer = None

    async def __load_device_info(self) -> None:
        retries = self.__init_retries
        while True:
            await asyncio.sleep(self.__init_delay)
            try:
                self.__device_info = await DeviceInfo.read(self.__device_path)
                break
            except Exception:
                if retries == 0:
                    self.__device_info = None
                    raise MsdError("Can't load device info")
                get_logger().exception("Can't load device info; retries=%d", retries)
                retries -= 1
