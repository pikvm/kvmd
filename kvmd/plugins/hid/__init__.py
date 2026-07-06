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
import math
import random
import time

from typing import Final
from typing import Iterable
from typing import Callable
from typing import AsyncGenerator
from typing import Any

from evdev import ecodes

from ...logging import get_logger

from ...yamlconf import Section
from ...yamlconf import Option

from ...validators.basic import valid_bool
from ...validators.basic import valid_int_f1
from ...validators.basic import valid_string_list
from ...validators.hid import valid_hid_key
from ...validators.hid import valid_hid_mouse_move

from ...keyboard.mappings import WEB_TO_EVDEV
from ...keyboard.mappings import EvdevModifiers
from ...mouse import MouseRange
from ...mouse import MouseDelta

from .. import BasePlugin
from .. import get_plugin_class


# =====
# Jiggler motion tuning. These are internal constants (no config knobs) so the
# jiggling stays a single "universal" algorithm that just looks human.
_JIGGLE_STEPS:      Final[int]   = 40     # points traced along each Bezier segment
_JIGGLE_STEP_DELAY: Final[float] = 0.02   # ~50 Hz, close to a real pointer's update rate
_JIGGLE_MOVE_MIN:   Final[int]   = 50     # min nudge amplitude (HID units)
_JIGGLE_MOVE_MAX:   Final[int]   = 400    # max nudge amplitude (HID units)


# =====
class BaseHid(BasePlugin):  # pylint: disable=too-many-instance-attributes
    def __init__(self, c: Section) -> None:
        super().__init__(c)

        self.__ignore_keys: Final[frozenset[int]] = frozenset([WEB_TO_EVDEV[key] for key in c.ignore_keys])

        self.__mouse_x_range: Final[tuple[int, int]] = (c.mouse_x_range.min, c.mouse_x_range.max)
        self.__mouse_y_range: Final[tuple[int, int]] = (c.mouse_y_range.min, c.mouse_y_range.max)

        self.__j_enabled:  Final[bool]  = c.jiggler.enabled
        self.__j_interval: Final[float] = c.jiggler.interval

        self.__j_active: bool = c.jiggler.active
        self.__j_next_interval: float = self.__roll_interval()

        self.__j_absolute = True
        self.__j_activity_ts = self.__get_monotonic_seconds()
        self.__j_last_x = 0
        self.__j_last_y = 0

    @classmethod
    def get_plugin_options(cls) -> dict[str, Any]:
        return {
            "ignore_keys": Option([], type=valid_string_list.mk(subval=valid_hid_key)),
            "mouse_x_range": {
                "min": Option(MouseRange.MIN, type=valid_hid_mouse_move),
                "max": Option(MouseRange.MAX, type=valid_hid_mouse_move),
            },
            "mouse_y_range": {
                "min": Option(MouseRange.MIN, type=valid_hid_mouse_move),
                "max": Option(MouseRange.MAX, type=valid_hid_mouse_move),
            },
            "jiggler": {
                "enabled":  Option(True,  type=valid_bool),
                "active":   Option(False, type=valid_bool),
                "interval": Option(60,    type=valid_int_f1),
            },
        }

    # =====

    async def sysprep(self) -> None:
        raise NotImplementedError

    async def get_state(self) -> dict:
        raise NotImplementedError

    async def trigger_state(self) -> None:
        raise NotImplementedError

    async def poll_state(self) -> AsyncGenerator[dict]:
        # ==== Granularity table ====
        #   - enabled   -- Full
        #   - online    -- Partial
        #   - busy      -- Partial
        #   - connected -- Partial, nullable
        #   - keyboard.online  -- Partial
        #   - keyboard.outputs -- Partial
        #   - keyboard.leds    -- Partial
        #   - mouse.online     -- Partial
        #   - mouse.outputs    -- Partial, follows with absolute
        #   - mouse.absolute   -- Partial, follows with outputs
        # ===========================

        yield {}
        raise NotImplementedError

    async def reset(self) -> None:
        raise NotImplementedError

    async def cleanup(self) -> None:
        pass

    def set_params(
        self,
        keyboard_output: (str | None)=None,
        mouse_output: (str | None)=None,
        jiggler: (bool | None)=None,
    ) -> None:

        raise NotImplementedError

    def set_connected(self, connected: bool) -> None:
        _ = connected

    # =====

    def get_inactivity_seconds(self) -> int:
        return (self.__get_monotonic_seconds() - self.__j_activity_ts)

    # =====

    async def send_key_events(
        self,
        keys: Iterable[tuple[int, bool]],
        no_ignore_keys: bool=False,
        delay: float=0.0,
    ) -> None:

        for (key, state) in keys:
            if no_ignore_keys or key not in self.__ignore_keys:
                if delay > 0:
                    await asyncio.sleep(delay)
                self.send_key_event(key, state, False)

    def send_key_event(self, key: int, state: bool, finish: bool) -> None:
        self._send_key_event(key, state)
        if state and finish and (key not in EvdevModifiers.ALL and key != ecodes.KEY_SYSRQ):
            # Считаем что PrintScreen это модификатор для Alt+SysRq+...
            # По-хорошему надо учитывать факт нажатия на Alt, но можно и забить.
            self._send_key_event(key, False)
        self.__bump_activity()

    def _send_key_event(self, key: int, state: bool) -> None:
        raise NotImplementedError

    # =====

    def send_mouse_button_event(self, button: int, state: bool) -> None:
        self._send_mouse_button_event(button, state)
        self.__bump_activity()

    def _send_mouse_button_event(self, button: int, state: bool) -> None:
        raise NotImplementedError

    # =====

    def send_mouse_move_event(self, to_x: int, to_y: int) -> None:
        self.__j_last_x = to_x
        self.__j_last_y = to_y
        if self.__mouse_x_range != MouseRange.RANGE:
            to_x = MouseRange.remap(to_x, *self.__mouse_x_range)
        if self.__mouse_y_range != MouseRange.RANGE:
            to_y = MouseRange.remap(to_y, *self.__mouse_y_range)
        self._send_mouse_move_event(to_x, to_y)
        self.__bump_activity()

    def _send_mouse_move_event(self, to_x: int, to_y: int) -> None:
        _ = to_x  # XXX: NotImplementedError
        _ = to_y

    # =====

    def send_mouse_relative_events(self, deltas: Iterable[tuple[int, int]], squash: bool) -> None:
        self.__process_mouse_delta_event(deltas, squash, self.send_mouse_relative_event)

    def send_mouse_relative_event(self, delta_x: int, delta_y: int) -> None:
        self._send_mouse_relative_event(delta_x, delta_y)
        self.__bump_activity()

    def _send_mouse_relative_event(self, delta_x: int, delta_y: int) -> None:
        _ = delta_x  # XXX: NotImplementedError
        _ = delta_y

    # =====

    def send_mouse_wheel_events(self, deltas: Iterable[tuple[int, int]], squash: bool) -> None:
        self.__process_mouse_delta_event(deltas, squash, self.send_mouse_wheel_event)

    def send_mouse_wheel_event(self, delta_x: int, delta_y: int) -> None:
        self._send_mouse_wheel_event(delta_x, delta_y)
        self.__bump_activity()

    def _send_mouse_wheel_event(self, delta_x: int, delta_y: int) -> None:
        raise NotImplementedError

    # =====

    def clear_events(self) -> None:
        self._clear_events()  # Don't bump activity here

    def _clear_events(self) -> None:
        raise NotImplementedError

    # =====

    def __process_mouse_delta_event(
        self,
        deltas: Iterable[tuple[int, int]],
        squash: bool,
        handler: Callable[[int, int], None],
    ) -> None:

        if squash:
            prev = (0, 0)
            for cur in deltas:
                if abs(prev[0] + cur[0]) > 127 or abs(prev[1] + cur[1]) > 127:
                    handler(*prev)
                    prev = cur
                else:
                    prev = (prev[0] + cur[0], prev[1] + cur[1])
            if prev[0] or prev[1]:
                handler(*prev)
        else:
            for xy in deltas:
                handler(*xy)

    def __bump_activity(self) -> None:
        self.__j_activity_ts = self.__get_monotonic_seconds()

    def __get_monotonic_seconds(self) -> int:
        return int(time.monotonic())

    def _set_jiggler_absolute(self, absolute: bool) -> None:
        self.__j_absolute = absolute

    def _set_jiggler_active(self, active: bool) -> None:
        if self.__j_enabled:
            self.__j_active = active

    def _get_jiggler_state(self) -> dict:
        return {
            "jiggler": {
                "enabled":  self.__j_enabled,
                "active":   self.__j_active,
                "interval": self.__j_interval,
            },
        }

    # ===== Jiggler =====

    def __roll_interval(self) -> float:
        # Jitter the wait by +/-25% of the configured interval so the jiggle
        # timing is not a fixed period an idle/anti-AFK detector can fingerprint
        # (e.g. 60 -> [45, 75], 120 -> [90, 150], 30 -> [23, 37]). Re-rolled
        # after every jiggle. No extra config knob -- derived from `interval`.
        jitter = self.__j_interval * 0.25
        return max(1.0, random.uniform(self.__j_interval - jitter, self.__j_interval + jitter))

    async def __jiggle(self, absolute: bool) -> None:
        # A single "universal" human-like anti-idle action. Instead of a fixed
        # zig-zag we pick one of a few natural gestures at random; the pointer
        # always returns to where it started, so the operator's cursor doesn't
        # drift over time.
        action = random.choice(("move", "move", "move", "jitter", "scroll"))
        if action == "move":
            await self.__jiggle_move(absolute)
        elif action == "jitter":
            await self.__jiggle_jitter(absolute)
        else:
            await self.__jiggle_scroll()

    async def __jiggle_move(self, absolute: bool) -> None:
        # Glide to a nearby random point and back along curved (cubic Bezier)
        # paths with ease-in/out timing -- the two legs use independent random
        # arcs, so it looks like a hand nudging the mouse rather than a machine.
        amplitude = random.randint(_JIGGLE_MOVE_MIN, _JIGGLE_MOVE_MAX)
        angle = random.uniform(0.0, math.tau)
        dest = (round(amplitude * math.cos(angle)), round(amplitude * math.sin(angle)))
        path = self.__bezier((0, 0), dest) + self.__bezier(dest, (0, 0))
        if absolute:
            (base_x, base_y) = (self.__j_last_x, self.__j_last_y)
            for (off_x, off_y) in path:
                self.send_mouse_move_event(
                    MouseRange.normalize(base_x + off_x),
                    MouseRange.normalize(base_y + off_y),
                )
                await asyncio.sleep(_JIGGLE_STEP_DELAY)
        else:
            (prev_x, prev_y) = (0, 0)
            for (off_x, off_y) in path:
                self.send_mouse_relative_event(
                    MouseDelta.normalize(off_x - prev_x),
                    MouseDelta.normalize(off_y - prev_y),
                )
                (prev_x, prev_y) = (off_x, off_y)
                await asyncio.sleep(_JIGGLE_STEP_DELAY)

    async def __jiggle_jitter(self, absolute: bool) -> None:
        # A handful of tiny twitches in place, then settle back to the start.
        (base_x, base_y) = (self.__j_last_x, self.__j_last_y)
        for _ in range(random.randint(3, 6)):
            (off_x, off_y) = (random.randint(-2, 2), random.randint(-2, 2))
            if absolute:
                self.send_mouse_move_event(
                    MouseRange.normalize(base_x + off_x),
                    MouseRange.normalize(base_y + off_y),
                )
            else:
                self.send_mouse_relative_event(off_x, off_y)
            await asyncio.sleep(random.uniform(0.05, 0.15))
        if absolute:
            self.send_mouse_move_event(base_x, base_y)  # Settle exactly back

    async def __jiggle_scroll(self) -> None:
        # A small wheel nudge one way and back (net-zero).
        direction = random.choice((-1, 1))
        self.send_mouse_wheel_event(0, direction)
        await asyncio.sleep(random.uniform(0.1, 0.3))
        self.send_mouse_wheel_event(0, -direction)

    def __bezier(self, start: tuple[int, int], end: tuple[int, int]) -> list[tuple[int, int]]:
        # Cubic Bezier from start to end with a random perpendicular "bow" and
        # ease-in/out timing, returned as integer points. Control points sit at
        # 25% and 75% along the straight line, pushed sideways by a random
        # fraction of the distance so the trajectory curves like a real hand.
        (sx, sy) = start
        (ex, ey) = end
        (vx, vy) = (ex - sx, ey - sy)
        dist = math.hypot(vx, vy)
        if dist < 1:
            return [end]
        (perp_x, perp_y) = (-vy / dist, vx / dist)
        bow = random.uniform(-0.3, 0.3) * dist
        c1 = (sx + vx * 0.25 + perp_x * bow, sy + vy * 0.25 + perp_y * bow)
        c2 = (sx + vx * 0.75 + perp_x * bow, sy + vy * 0.75 + perp_y * bow)
        points: list[tuple[int, int]] = []
        for i in range(1, _JIGGLE_STEPS + 1):
            t = i / _JIGGLE_STEPS
            # EaseInOutQuad: accelerate to the midpoint, then decelerate.
            e = (2 * t * t) if t < 0.5 else (-1 + (4 - 2 * t) * t)
            mt = 1 - e
            x = mt ** 3 * sx + 3 * mt ** 2 * e * c1[0] + 3 * mt * e ** 2 * c2[0] + e ** 3 * ex
            y = mt ** 3 * sy + 3 * mt ** 2 * e * c1[1] + 3 * mt * e ** 2 * c2[1] + e ** 3 * ey
            points.append((round(x), round(y)))
        return points

    async def systask(self) -> None:
        while True:
            if self.__j_active and (self.__j_activity_ts + self.__j_next_interval < self.__get_monotonic_seconds()):
                get_logger(0).info("Jiggling mouse (interval=%.1f seconds) ...", self.__j_next_interval)
                await self.__jiggle(self.__j_absolute)
                self.__j_next_interval = self.__roll_interval()
            await asyncio.sleep(1)


# =====
def get_hid_class(name: str) -> type[BaseHid]:
    return get_plugin_class("hid", name)  # type: ignore
