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


from evdev import ecodes

from . import tools


# =====
class MouseRange:
    MIN = -32768
    MAX = 32767
    RANGE = (MIN, MAX)

    @classmethod
    def remap(cls, value: int, out_min: int, out_max: int) -> int:
        return tools.remap(value, cls.MIN, cls.MAX, out_min, out_max)

    @classmethod
    def normalize(cls, value: int) -> int:
        return min(max(cls.MIN, value), cls.MAX)


class MouseDelta:
    MIN = -127
    MAX = 127
    RANGE = (MIN, MAX)

    @classmethod
    def normalize(cls, value: int) -> int:
        return min(max(cls.MIN, value), cls.MAX)


# =====
MOUSE_TO_EVDEV = {
    "left":   ecodes.BTN_LEFT,
    "right":  ecodes.BTN_RIGHT,
    "middle": ecodes.BTN_MIDDLE,
    "up":     ecodes.BTN_BACK,
    "down":   ecodes.BTN_FORWARD,
}
