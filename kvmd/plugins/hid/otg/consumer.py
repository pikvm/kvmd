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


from typing import Any
from typing import Generator

from ....logging import get_logger

from .device import BaseDeviceProcess

from .events import BaseEvent
from .events import ClearEvent
from .events import ResetEvent
from .events import ConsumerEvent
from .events import make_consumer_event
from .events import make_consumer_report


# =====
class ConsumerProcess(BaseDeviceProcess):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(
            name="consumer",
            read_size=0,
            initial_state={},
            **kwargs,
        )

        self.__pressed_usages: list[int] = []

    async def cleanup(self) -> None:
        try:
            await self._stop()
        finally:
            get_logger().info("Clearing HID-consumer events ...")
            self._cleanup_write(make_consumer_report(0))

    def send_clear_event(self) -> None:
        self._clear_queue()
        self._queue_event(ClearEvent())

    def send_reset_event(self) -> None:
        self._clear_queue()
        self._queue_event(ResetEvent())

    def send_key_event(self, key: int, state: bool) -> None:
        self._queue_event(make_consumer_event(key, state))

    # =====

    def _process_event(self, event: BaseEvent) -> Generator[bytes]:
        if isinstance(event, (ClearEvent, ResetEvent)):
            self.__pressed_usages = []
        elif isinstance(event, ConsumerEvent):
            if event.state:
                if event.usage not in self.__pressed_usages:
                    self.__pressed_usages.append(event.usage)
            elif event.usage in self.__pressed_usages:
                self.__pressed_usages.remove(event.usage)
        else:
            raise RuntimeError(f"Not implemented event: {event}")
        usage = (self.__pressed_usages[-1] if self.__pressed_usages else 0)
        yield make_consumer_report(usage)
