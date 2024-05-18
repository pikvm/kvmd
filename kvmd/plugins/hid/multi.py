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
import functools
import pathlib

from typing import Any
from typing import AsyncGenerator
from typing import Iterable
from pkgutil import iter_modules

from ...yamlconf import Option
from ...yamlconf import make_config

from . import get_hid_class
from . import BaseHid


def all_hid_plugins() -> list[str]:
    current_directory = pathlib.Path(__file__).parent.resolve()
    return [name for _, name, _ in iter_modules([str(current_directory)]) if name != "multi"]


def valid_hid_plugin(key: str, value: list) -> list:
    all_plugins = all_hid_plugins()
    if not isinstance(value, dict) or "type" not in value or value["type"] not in all_plugins:
        raise ValueError("Invalid hid plugin")
    device_class = get_hid_class(value["type"])
    opts = {
        **device_class.get_plugin_options(),
        "type": Option("", type=str)
    }
    return make_config(value, opts, ("kvmd", "hid", key))


async def merge_generators(gens: Iterable[AsyncGenerator[Any, None]]) -> AsyncGenerator[Any, None]:
    pending_tasks = {asyncio.ensure_future(anext(g)): g for g in gens}
    while len(pending_tasks) > 0:
        done, _ = await asyncio.wait(pending_tasks.keys(), return_when="FIRST_COMPLETED")
        for completed_task in done:
            try:
                result = completed_task.result()
                yield result
                dg = pending_tasks[completed_task]
                pending_tasks[asyncio.ensure_future(anext(dg))] = dg
            except StopAsyncIteration as sai:
                print("Exception in getting result", sai)
            finally:
                del pending_tasks[completed_task]


class Plugin(BaseHid):
    def __init__(
        self,
        keyboard_device: dict[str, Any],
        mouse_device: dict[str, Any]
    ):
        super().__init__(False, False, 0)

        keyboard_type = keyboard_device["type"]
        mouse_type = mouse_device["type"]
        del keyboard_device["type"]
        del mouse_device["type"]
        self.__keyboard = get_hid_class(keyboard_type)(**keyboard_device)
        self.__mouse = get_hid_class(mouse_type)(**mouse_device)

    @classmethod
    def get_plugin_options(cls) -> dict:
        return {
            "keyboard_device": Option({
                "type": "otg"
            }, type=functools.partial(valid_hid_plugin, "keyboard_device")),
            "mouse_device": Option({
                "type": "otg"
            }, type=functools.partial(valid_hid_plugin, "mouse_device")),
        }

    def sysprep(self) -> None:
        self.__keyboard.sysprep()
        self.__mouse.sysprep()

    async def get_state(self) -> dict:
        keyboard_state, mouse_state = await asyncio.gather(self.__keyboard.get_state(), self.__mouse.get_state())
        return {
            "online": keyboard_state["online"] and mouse_state["online"],
            "busy": keyboard_state["busy"] or mouse_state["busy"],
            "connected": keyboard_state["connected"] or mouse_state["connected"],
            "keyboard": keyboard_state["keyboard"],
            "mouse": mouse_state["mouse"],
            "jiggler": mouse_state["jiggler"]
        }

    async def poll_state(self) -> AsyncGenerator[dict, None]:
        async for state in merge_generators([self.__keyboard.poll_state(), self.__mouse.poll_state()]):
            yield state

    async def reset(self) -> None:
        async with asyncio.TaskGroup() as tg:
            tg.create_task(self.__keyboard.reset())
            tg.create_task(self.__mouse.reset())

    async def cleanup(self) -> None:
        async with asyncio.TaskGroup() as tg:
            tg.create_task(self.__keyboard.cleanup())
            tg.create_task(self.__mouse.cleanup())

    def send_key_events(self, keys: Iterable[tuple[str, bool]]) -> None:
        self.__keyboard.send_key_events(keys)

    def send_mouse_button_event(self, button: str, state: bool) -> None:
        self.__mouse.send_mouse_button_event(button, state)

    def send_mouse_move_event(self, to_x: int, to_y: int) -> None:
        self.__mouse.send_mouse_move_event(to_x, to_y)

    def send_mouse_relative_event(self, delta_x: int, delta_y: int) -> None:
        self.__mouse.send_mouse_relative_event(delta_x, delta_y)

    def send_mouse_wheel_event(self, delta_x: int, delta_y: int) -> None:
        self.__mouse.send_mouse_wheel_event(delta_x, delta_y)

    def set_params(
        self,
        keyboard_output: (str | None)=None,
        mouse_output: (str | None)=None,
        jiggler: (bool | None)=None,
    ) -> None:
        self.__keyboard.set_params(keyboard_output, mouse_output, jiggler)
        self.__mouse.set_params(keyboard_output, mouse_output, jiggler)

    def set_connected(self, connected: bool) -> None:
        self.__keyboard.set_connected(connected)
        self.__mouse.set_connected(connected)

    def clear_events(self) -> None:
        self.__keyboard.clear_events()
        self.__mouse.clear_events()
