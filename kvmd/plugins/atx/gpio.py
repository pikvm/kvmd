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
import collections
import copy
import time

from typing import Final
from typing import AsyncGenerator

import gpiod

from ...logging import get_logger

from ... import aiotools
from ... import aiogp

from ...yamlconf import Section
from ...yamlconf import Option

from ...validators.basic import valid_bool
from ...validators.basic import valid_float_f0
from ...validators.basic import valid_float_f01
from ...validators.os import valid_abs_path
from ...validators.hw import valid_gpio_pin

from . import AtxIsBusyError
from . import BaseAtx


# =====
_HISTORY_WINDOW_NS = 3_000_000_000
_HISTORY_INTERVAL_NS = 100_000_000
_HISTORY_SAMPLES = 30
_HISTORY_MAX_TRANSITIONS = 64


class _LedHistory:  # pylint: disable=too-many-instance-attributes
    def __init__(self) -> None:
        self.__started: (int | None) = None
        self.__base: (tuple[int, bool] | None) = None
        self.__transitions: collections.deque[tuple[int, bool]] = collections.deque()
        self.__last: (bool | None) = None
        self.__seen: (int | None) = None
        self.__gaps = 0
        self.__gap_logged = 0

    def record(self, value: bool, changes: int, now: int) -> None:
        if self.__seen is None or changes != self.__seen + 1 or value == self.__last:
            self.__restart(value, changes, now)
            return
        self.__prune(now)
        if len(self.__transitions) >= _HISTORY_MAX_TRANSITIONS:
            self.__base = self.__transitions.popleft()
        self.__transitions.append((now, value))
        self.__last = value
        # Commit the counter last so a partial failure is detected on the next call or read.
        self.__seen = changes

    def make_report(self, now: int, current: bool, changes: (int | None)) -> dict:
        if (
            changes is not None
            and (self.__seen is None or self.__seen != changes or self.__last != current)
        ):
            self.__gaps += 1
            if self.__gaps == 1:
                get_logger(0).error("ATX power LED history has a gap; restarting it")
                self.__gap_logged = now
            elif now - self.__gap_logged >= 60_000_000_000:
                get_logger(0).error("ATX power LED history has had %d gaps; restarting it", self.__gaps)
                self.__gap_logged = now
            self.__restart(current, changes, now)
        self.__prune(now)
        begin = now - _HISTORY_WINDOW_NS
        if self.__base is None:
            return self.__report(False, [None] * _HISTORY_SAMPLES, now)
        items = [self.__base, *self.__transitions]
        power: list[int | None] = []
        index = 0
        for sample in range(_HISTORY_SAMPLES):
            ts = begin + (sample + 1) * _HISTORY_INTERVAL_NS
            if ts < self.__base[0]:
                power.append(None)
                continue
            while index + 1 < len(items) and items[index + 1][0] <= ts:
                index += 1
            power.append(int(items[index][1]))
        return self.__report((self.__base[0] <= begin), power, now)

    def __report(self, complete: bool, power: list[int | None], now: int) -> dict:
        return {
            "interval_ms": _HISTORY_INTERVAL_NS // 1_000_000,
            "complete": complete,
            "power": power,
            "transitions": [
                [-((now - ts) // 1_000_000) / 1000, value]
                for (ts, value) in self.__transitions
            ],
        }

    def __prune(self, now: int) -> None:
        limit = now - _HISTORY_WINDOW_NS
        while self.__transitions and self.__transitions[0][0] <= limit:
            self.__base = self.__transitions.popleft()

    def __restart(self, value: bool, changes: int, now: int) -> None:
        if self.__started is None:
            self.__started = now
        self.__base = (now, value)
        self.__transitions.clear()
        self.__last = value
        self.__seen = changes


# =====
class Plugin(BaseAtx):  # pylint: disable=too-many-instance-attributes
    def __init__(self, c: Section) -> None:
        super().__init__(c)

        self.__device_path: Final[str] = c.device

        self.__power_led_pin:    Final[int] = c.power_led_pin
        self.__hdd_led_pin:      Final[int] = c.hdd_led_pin
        self.__power_switch_pin: Final[int] = c.power_switch_pin
        self.__reset_switch_pin: Final[int] = c.reset_switch_pin

        self.__click_delay:      Final[float] = c.click_delay
        self.__long_click_delay: Final[float] = c.long_click_delay

        self.__notifier = aiotools.AioNotifier()
        self.__power_region = aiotools.AioExclusiveRegion(AtxIsBusyError, self.__notifier)
        self.__reset_region = aiotools.AioExclusiveRegion(AtxIsBusyError, self.__notifier)

        self.__line_req: (gpiod.LineRequest | None) = None

        self.__power_history = _LedHistory()
        self.__reader = aiogp.AioReader(
            path=self.__device_path,
            consumer="kvmd::atx",
            pins={
                c.power_led_pin: aiogp.AioReaderPinParams(c.power_led_inverted, c.power_led_debounce),
                c.hdd_led_pin: aiogp.AioReaderPinParams(c.hdd_led_inverted, c.hdd_led_debounce),
            },
            notifier=self.__notifier,
            on_change=self.__on_led_change,
        )

    @classmethod
    def get_plugin_options(cls) -> dict:
        return {
            "device": Option("/dev/kvmd-gpio", type=valid_abs_path),

            "power_led_pin":      Option(24,    type=valid_gpio_pin),
            "power_led_inverted": Option(False, type=valid_bool),
            "power_led_debounce": Option(0.1,   type=valid_float_f0),

            "hdd_led_pin":      Option(22,    type=valid_gpio_pin),
            "hdd_led_inverted": Option(False, type=valid_bool),
            "hdd_led_debounce": Option(0.1,   type=valid_float_f0),

            "power_switch_pin": Option(23,  type=valid_gpio_pin),
            "reset_switch_pin": Option(27,  type=valid_gpio_pin),
            "click_delay":      Option(0.1, type=valid_float_f01),
            "long_click_delay": Option(5.5, type=valid_float_f01),
        }

    async def sysprep(self) -> None:
        assert self.__line_req is None
        self.__line_req = gpiod.request_lines(
            self.__device_path,
            consumer="kvmd::atx",
            config={
                (self.__power_switch_pin, self.__reset_switch_pin): gpiod.LineSettings(
                    direction=gpiod.line.Direction.OUTPUT,
                    output_value=gpiod.line.Value(False),
                ),
            },
        )

    async def get_state(self) -> dict:
        power_busy = self.__power_region.is_busy()
        reset_busy = self.__reset_region.is_busy()
        return {
            "enabled": True,
            "busy": (power_busy or reset_busy),
            "acts": {
                "power": power_busy,
                "reset": reset_busy,
            },
            "leds": {
                "power": self.__reader.get(self.__power_led_pin),
                "hdd": self.__reader.get(self.__hdd_led_pin),
            },
        }

    async def get_history(self) -> dict:
        pin = self.__power_led_pin
        return self.__power_history.make_report(
            time.monotonic_ns(), self.__reader.get(pin), self.__reader.get_changes(pin),
        )

    async def trigger_state(self) -> None:
        self.__notifier.notify(1)

    async def poll_state(self) -> AsyncGenerator[dict]:
        prev: dict = {}
        while True:
            if (await self.__notifier.wait()) > 0:
                prev = {}
            new = await self.get_state()
            if new != prev:
                prev = copy.deepcopy(new)
                yield new

    async def systask(self) -> None:
        await self.__reader.poll()

    async def cleanup(self) -> None:
        if self.__line_req:
            try:
                self.__line_req.release()
            except Exception:
                pass

    # =====

    async def power_on(self, wait: bool) -> None:
        if not (await self.__get_power()):
            await self.click_power(wait)

    async def power_off(self, wait: bool) -> None:
        if (await self.__get_power()):
            await self.click_power(wait)

    async def power_off_hard(self, wait: bool) -> None:
        if (await self.__get_power()):
            await self.click_power_long(wait)

    async def power_reset_hard(self, wait: bool) -> None:
        if (await self.__get_power()):
            await self.click_reset(wait)

    # =====

    async def click_power(self, wait: bool) -> None:
        await self.__click("power", self.__power_region, self.__power_switch_pin, self.__click_delay, wait)

    async def click_power_long(self, wait: bool) -> None:
        await self.__click("power_long", self.__power_region, self.__power_switch_pin, self.__long_click_delay, wait)

    async def click_reset(self, wait: bool) -> None:
        await self.__click("reset", self.__reset_region, self.__reset_switch_pin, self.__click_delay, wait)

    # =====

    def __on_led_change(self, pin: int, value: bool, changes: int) -> None:
        if pin == self.__power_led_pin:
            self.__power_history.record(value, changes, time.monotonic_ns())

    async def __get_power(self) -> bool:
        return (await self.get_state())["leds"]["power"]

    @aiotools.atomic_fg
    async def __click(self, name: str, region: aiotools.AioExclusiveRegion, pin: int, delay: float, wait: bool) -> None:
        if wait:
            with region:
                await self.__inner_click(name, pin, delay)
        else:
            await aiotools.run_region_task(
                f"Can't perform ATX {name} click or operation was not completed",
                region, self.__inner_click, name, pin, delay,
            )

    @aiotools.atomic_fg
    async def __inner_click(self, name: str, pin: int, delay: float) -> None:
        assert self.__line_req
        try:
            self.__line_req.set_value(pin, gpiod.line.Value(True))
            await asyncio.sleep(delay)
        finally:
            self.__line_req.set_value(pin, gpiod.line.Value(False))
            await asyncio.sleep(1)
        get_logger(0).info("Clicked ATX button %r", name)
