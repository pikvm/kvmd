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

import math

from ....mouse import MouseRange
from ....logging import get_logger

class Mouse:
    def __init__(self) -> None:
        self._active = 'usb'
        self._button = ''
        self._clicked = False
        self._to_x = [0,0]
        self._to_y = [0,0]
        self._wheel_y = 0
        self._delta_x = 0
        self._delta_y = 0

    def button(self, button: str, clicked: bool) -> list:
        self._button = button
        self._clicked = clicked
        self._wheel_y = 0
        if self._active == 'usb':
            return self._absolute()
        else :
            return self._relative()

    def move(self, to_x: int, to_y: int) -> list:
        get_logger(0).info(f"HID : Mouse move to_x = {to_x} to_y = {to_y}")
        self._to_x = self._to_fixed(to_x)
        self._to_y = self._to_fixed(to_y)
        self._wheel_y = 0
        return self._absolute()

    def wheel(self, to_x: int, to_y: int) -> list:
        self._wheel_y = 1 if to_y > 0 else 255
        return self._absolute()

    def relative(self, delta_x: int, delta_y: int) -> list:
        delta_x = math.ceil(delta_x / 4)
        delta_y = math.ceil(delta_y / 4)
        self._delta_x = delta_x if delta_x > 0 else 256 + delta_x
        self._delta_y = delta_y if delta_y > 0 else 256 + delta_y
        return self._relative()

    def _absolute(self) -> list:
        code = 0x00;
        if self._clicked:
            code = self._button_code(self._button)
        get_logger(0).info(f"HID : MouseEvent to_x = {self._to_x} to_y = {self._to_y}")
        cmd = [0x00, 0x04, 0x07, 0x02, code, 0x00, 0x00, 0x00, 0x00, 0x00]
        if len(self._to_x) == 2:
            cmd[6] = self._to_x[0]
            cmd[5] = self._to_x[1]
        if len(self._to_y) == 2:
            cmd[8] = self._to_y[0]
            cmd[7] = self._to_y[1]
        if self._wheel_y:
            cmd[9] = self._wheel_y
        return cmd

    def _relative(self) -> list:
        code = 0x00;
        if self._clicked:
            code = self._button_code(self._button)
        cmd = [0x00, 0x05, 0x05, 0x01, code, 0x00, 0x00, 0x00]
        if self._delta_x : cmd[5] = self._delta_x
        if self._delta_y : cmd[6] = self._delta_y
        return cmd

    def _button_code(self, name: str) -> bytes:
        match name:
            case 'left':
                return 0x01
            case 'right':
                return 0x02
            case 'middle':
                return 0x04

    def _to_fixed(self, num: int) -> list:
        to_fixed = math.ceil(MouseRange.remap(num, 0, MouseRange.MAX) / 8)
        return [ to_fixed >> 8, to_fixed & 0xFF ]
