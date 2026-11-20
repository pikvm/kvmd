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
import queue
import threading

from types import SimpleNamespace
from unittest.mock import Mock

import gpiod
import pytest

from kvmd import aiogp


# =====
class _FakeLineRequest:
    def __init__(self, initial: dict[int, bool]) -> None:
        self.__values = dict(initial)
        self.__loop = asyncio.get_running_loop()
        self.__initial_wait = True
        self.__pending: queue.Queue[list[SimpleNamespace] | asyncio.Event | threading.Event] = queue.Queue()
        self.__events: list[SimpleNamespace] = []
        self.thread_id = 0

    def push(self, *values: tuple[int, bool]) -> None:
        self.__pending.put([
            SimpleNamespace(
                Type=gpiod.EdgeEvent.Type,
                event_type=(gpiod.EdgeEvent.Type.RISING_EDGE if value else gpiod.EdgeEvent.Type.FALLING_EDGE),
                line_offset=pin,
            )
            for (pin, value) in values
        ])

    async def flush(self) -> None:
        # The acknowledgement follows all queue.put_nowait callbacks submitted by the reader.
        done = asyncio.Event()
        self.__pending.put(done)
        await asyncio.wait_for(done.wait(), timeout=1)

    def flush_blocking(self) -> None:
        # Wait for the worker to submit callbacks while holding the event loop.
        done = threading.Event()
        self.__pending.put(done)
        assert done.wait(2)

    def wait_edge_events(self, timeout: float) -> bool:
        self.thread_id = threading.get_ident()
        if self.__initial_wait:
            self.__initial_wait = False
            return False
        while True:
            try:
                item = self.__pending.get(timeout=timeout)
            except queue.Empty:
                return False
            if isinstance(item, asyncio.Event):
                self.__loop.call_soon_threadsafe(item.set)
            elif isinstance(item, threading.Event):
                item.set()
            else:
                self.__events = item
                return True

    def read_edge_events(self) -> list[SimpleNamespace]:
        for event in self.__events:
            self.__values[event.line_offset] = (event.event_type == gpiod.EdgeEvent.Type.RISING_EDGE)
        return self.__events

    def get_values(self, pins: list[int]) -> list[gpiod.line.Value]:
        return [gpiod.line.Value.ACTIVE if self.__values[pin] else gpiod.line.Value.INACTIVE for pin in pins]


def _patch_request(monkeypatch: pytest.MonkeyPatch, initial: dict[int, bool]) -> _FakeLineRequest:
    line_req = _FakeLineRequest(initial)
    monkeypatch.setattr(aiogp.gpiod, "request_lines", Mock(return_value=contextlib.nullcontext(line_req)))
    return line_req
