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

from ....keyboard.mappings import KEYMAP
from ....logging import get_logger

class Keyboard:
    def __init__(self) -> None:
        self.leds = {
            "caps" : False,
            "scroll" : False,
            "num" : False
        }
        self._active_keys = []

    def key(self, key: str, state: bool) -> list:
        if state : self._active_keys.append([key, self._is_modifier(key)])
        else : self._active_keys.remove([key, self._is_modifier(key)])
        get_logger(0).info(f"HID : KeyEvent name = {key} state = {state}, active_keys {self._active_keys}")
        code = KEYMAP[key].usb.code if state else 0
        get_logger(0).info(f"HID : KeyEvent code = {code}")
        cmd = [0x00, 0x02, 0x08, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]
        if state :
            if len(self._active_keys) > 1:
                for idx, key in enumerate(self._active_keys) :
                    if key[1] : cmd[3+idx] = KEYMAP[key[0]].usb.code
            cmd[5] = code
        return cmd



    def _is_modifier(self, key: str) -> bool:
        return KEYMAP[key].usb.is_modifier
