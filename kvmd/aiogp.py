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
import threading
import dataclasses
import functools
import time
import typing

from collections.abc import Callable

import gpiod

from .logging import get_logger

from . import aiotools


# =====
@dataclasses.dataclass(frozen=True)
class AioReaderPinParams:
    inverted: bool
    debounce: float


class AioReader:  # pylint: disable=too-many-instance-attributes
    def __init__(
        self,
        path: str,
        consumer: str,
        pins: dict[int, AioReaderPinParams],
        notifier: aiotools.AioNotifier,
        on_change: (Callable[[int, bool, int], None] | None)=None,
    ) -> None:

        self.__path = path
        self.__consumer = consumer
        self.__pins = dict(pins)
        self.__notifier = notifier
        self.__on_change = on_change
        self.__on_change_failures = 0
        self.__on_change_logged = 0.0

        self.__values: (dict[int, _DebouncedValue] | None) = None

        self.__thread = threading.Thread(target=self.__run, daemon=True)
        self.__stop_event = threading.Event()

        self.__loop: (asyncio.AbstractEventLoop | None) = None

    def get(self, pin: int) -> bool:
        value = (self.__values[pin].get() if self.__values is not None else False)
        return (value ^ self.__pins[pin].inverted)

    def get_changes(self, pin: int) -> (int | None):
        return (self.__values[pin].get_changes() if self.__values is not None else None)

    async def poll(self) -> None:
        if not self.__pins:
            await aiotools.wait_infinite()
        else:
            assert self.__loop is None
            self.__loop = asyncio.get_running_loop()
            self.__thread.start()
            try:
                await asyncio.to_thread(self.__thread.join)
            finally:
                self.__stop_event.set()
                await asyncio.to_thread(self.__thread.join)

    def __run(self) -> None:
        assert self.__values is None
        assert self.__loop

        pins = sorted(self.__pins)
        with gpiod.request_lines(
            self.__path,
            consumer=self.__consumer,
            config={tuple(pins): gpiod.LineSettings(edge_detection=gpiod.line.Edge.BOTH)},
        ) as line_req:

            line_req.wait_edge_events(0.1)
            self.__values = {
                pin: _DebouncedValue(
                    initial=bool(value.value),
                    debounce=self.__pins[pin].debounce,
                    notifier=self.__notifier,
                    loop=self.__loop,
                    on_change=(
                        functools.partial(self.__call_on_change, pin)
                        if self.__on_change is not None else None
                    ),
                )
                for (pin, value) in zip(pins, line_req.get_values(pins))
            }
            self.__loop.call_soon_threadsafe(self.__notifier.notify)
            if self.__on_change is not None:
                self.__loop.call_soon_threadsafe(self.__report_initial)

            while not self.__stop_event.is_set():
                if line_req.wait_edge_events(1):
                    new: dict[int, bool] = {}
                    for event in line_req.read_edge_events():
                        (pin, state) = self.__parse_event(event)
                        new[pin] = state
                    for (pin, state) in new.items():
                        self.__values[pin].set(state)
                else:  # Timeout
                    # XXX: Лимит был актуален для 1.6. Надо проверить, поменялось ли это в 2.x.
                    # Размер буфера ядра - 16 эвентов на линии. При превышении этого числа,
                    # новые эвенты потеряются. Это не баг, это фича, как мне объяснили в LKML.
                    # Штош. Будем с этим жить и синхронизировать состояния при таймауте.
                    for (pin, value) in zip(pins, line_req.get_values(pins)):
                        self.__values[pin].set(bool(value.value))  # type: ignore

    def __report_initial(self) -> None:
        assert self.__values is not None
        for (pin, value) in sorted(self.__values.items()):
            self.__call_on_change(pin, value.get(), value.get_changes())

    def __call_on_change(self, pin: int, raw: bool, changes: int) -> None:
        assert self.__on_change is not None
        try:
            self.__on_change(pin, raw ^ self.__pins[pin].inverted, changes)
        except Exception:
            self.__on_change_failures += 1
            now = time.monotonic()
            if self.__on_change_failures == 1:
                get_logger(0).exception("Unhandled exception in GPIO on_change callback: pin=%d", pin)
            elif (now - self.__on_change_logged) >= 60:
                get_logger(0).error("GPIO on_change callback has failed %d times", self.__on_change_failures)
            else:
                return
            self.__on_change_logged = now

    def __parse_event(self, event: gpiod.EdgeEvent) -> tuple[int, bool]:
        match event.event_type:
            case event.Type.RISING_EDGE:
                return (event.line_offset, True)
            case event.Type.FALLING_EDGE:
                return (event.line_offset, False)
        typing.assert_never(event.event_type)


class _DebouncedValue:  # pylint: disable=too-many-instance-attributes
    def __init__(
        self,
        initial: bool,
        debounce: float,
        notifier: aiotools.AioNotifier,
        loop: asyncio.AbstractEventLoop,
        on_change: (Callable[[bool, int], None] | None)=None,
    ) -> None:

        self.__value = initial
        self.__changes = 0
        self.__debounce = debounce
        self.__notifier = notifier
        self.__loop = loop
        self.__on_change = on_change

        self.__queue: "asyncio.Queue[bool]" = asyncio.Queue()  # type: ignore
        self.__task = asyncio.run_coroutine_threadsafe(self.__consumer_task_loop(), loop)

    def set(self, value: bool) -> None:
        if self.__loop.is_running():
            self.__check_alive()
            self.__loop.call_soon_threadsafe(self.__queue.put_nowait, value)

    def get(self) -> bool:
        return self.__value

    def get_changes(self) -> int:
        return self.__changes

    def __check_alive(self) -> None:
        if self.__task.done() and not self.__task.cancelled():
            raise RuntimeError("Dead debounce consumer")

    async def __consumer_task_loop(self) -> None:
        while True:
            value = await self.__queue.get()
            while not self.__queue.empty():
                value = await self.__queue.get()
            if self.__value != value:
                self.__value = value
                self.__changes += 1
                self.__notifier.notify()
                if self.__on_change is not None:
                    self.__on_change(value, self.__changes)
            await asyncio.sleep(self.__debounce)
