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

from kvmd.plugins.hid.ch9329.mouse import Mouse


# =====
def test_ok__ch9329__relative_move_is_not_repeated() -> None:
    mouse = Mouse()
    mouse.set_absolute(False)
    assert mouse.process_relative(9, 9)[5:7] == bytes([3, 3])
    assert mouse.process_button(ecodes.BTN_LEFT, True)[4:7] == bytes([1, 0, 0])
    assert mouse.process_wheel(0, 1)[5:7] == bytes([0, 0])


def test_ok__ch9329__relative_move_is_symmetric() -> None:
    mouse = Mouse()
    mouse.set_absolute(False)
    for (delta, value) in [(1, 1), (3, 1), (4, 2), (127, 43)]:
        assert mouse.process_relative(delta, -delta)[5:7] == bytes([value, 256 - value])


def test_ok__ch9329__wheel() -> None:
    mouse = Mouse()
    assert mouse.process_wheel(0, 5)[-1] == 1
    assert mouse.process_wheel(0, -5)[-1] == 255
    assert mouse.process_wheel(5, 0)[-1] == 0
