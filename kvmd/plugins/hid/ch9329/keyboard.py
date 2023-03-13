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

from .... import aiomulti

from ....keyboard.mappings import KEYMAP


class Keyboard:
    def __init__(self) -> None:

        self.__notifier = aiomulti.AioProcessNotifier()
        self.__leds = aiomulti.AioSharedFlags({
            "num": False,
            "caps": False,
            "scroll": False,
        }, self.__notifier, type=bool)

        self.__active_keys: list[list] = []

    def key(self, key: str, state: bool) -> list:
        if state:
            self.__active_keys.append([key, self.__is_modifier(key)])
        else:
            self.__active_keys.remove([key, self.__is_modifier(key)])
        return self.__key()

    async def leds(self) -> dict:
        leds = await self.__leds.get()
        return leds

    def set_leds(self, led_byte: int) -> None:
        num = bool(led_byte & 1)
        caps = bool((led_byte >> 1) & 1)
        scroll = bool((led_byte >> 2) & 1)
        self.__leds.update(num=num, caps=caps, scroll=scroll)

    def __key(self) -> list:
        cmd = [0x00, 0x02, 0x08, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]
        counter = 0
        for key in self.__active_keys:
            if key[1]:
                cmd[3 + counter] = self.__keycode(key[0])
            else:
                cmd[5 + counter] = self.__keycode(key[0])
            counter += 1
        return cmd

    def __keycode(self, key: str) -> int:
        return KEYMAP[key].usb.code

    def __is_modifier(self, key: str) -> bool:
        return KEYMAP[key].usb.is_modifier
