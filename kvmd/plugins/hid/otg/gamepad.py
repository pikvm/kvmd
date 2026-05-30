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


from typing import Generator
from typing import Any

from ....logging import get_logger

from .device import BaseDeviceProcess

from .events import BaseEvent
from .events import ClearEvent
from .events import ResetEvent
from .events import GamepadStateEvent
from .events import make_gamepad_report


# =====
# Neutral snapshot: sticks centered (128), triggers released (0), hat centered (8), no buttons
_NEUTRAL = (0, 128, 128, 128, 128, 0, 0, 8)


class GamepadProcess(BaseDeviceProcess):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(
            name="gamepad",
            read_size=0,
            initial_state={},
            **kwargs,
        )

    async def cleanup(self) -> None:
        try:
            await self._stop()
        finally:
            get_logger().info("Clearing HID-gamepad events ...")
            self._cleanup_write(make_gamepad_report(*_NEUTRAL))  # Release all buttons and center axes

    def send_clear_event(self) -> None:
        self._clear_queue()
        self._queue_event(ClearEvent())

    def send_reset_event(self) -> None:
        self._clear_queue()
        self._queue_event(ResetEvent())

    def send_state_event(  # pylint: disable=too-many-arguments
        self,
        buttons: int,
        lx: int, ly: int, rx: int, ry: int,
        lt: int, rt: int,
        hat: int,
    ) -> None:

        self._queue_event(GamepadStateEvent(buttons, lx, ly, rx, ry, lt, rt, hat))

    # =====

    def _process_event(self, event: BaseEvent) -> Generator[bytes]:
        if isinstance(event, (ClearEvent, ResetEvent)):
            yield make_gamepad_report(*_NEUTRAL)
        elif isinstance(event, GamepadStateEvent):
            yield make_gamepad_report(
                event.buttons,
                event.lx, event.ly, event.rx, event.ry,
                event.lt, event.rt,
                event.hat,
            )
        else:
            raise RuntimeError(f"Not implemented event: {event}")
