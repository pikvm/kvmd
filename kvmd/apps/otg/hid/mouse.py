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


from . import Hid


# =====
def make_mouse_hid(absolute: bool, horizontal_wheel: bool, report_id: (int | None)=None) -> Hid:
    maker = (_make_absolute_hid if absolute else _make_relative_hid)
    return maker(horizontal_wheel, report_id)


_HORIZONTAL_WHEEL = [
    0x05, 0x0C,  # USAGE PAGE (Consumer Devices)
    0x0A, 0x38, 0x02,  # USAGE (AC Pan)
    0x15, 0x81,  # LOGICAL_MINIMUM (-127)
    0x25, 0x7F,  # LOGICAL_MAXIMUM (127)
    0x75, 0x08,  # REPORT_SIZE (8)
    0x95, 0x01,  # REPORT_COUNT (1)
    0x81, 0x06,  # INPUT (Data,Var,Rel)
]


def _make_absolute_hid(horizontal_wheel: bool, report_id: (int | None)) -> Hid:
    return Hid(
        protocol=0,  # None protocol
        subclass=0,  # No subclass

        report_length=(7 if horizontal_wheel else 6),

        report_descriptor=bytes([
            # https://github.com/NicoHood/HID/blob/0835e6a/src/SingleReport/SingleAbsoluteMouse.cpp
            # Репорт взят отсюда ^^^, но изменен диапазон значений координат перемещений.
            # Автор предлагает использовать -32768...32767, но семерка почему-то не хочет работать
            # с отрицательными значениями координат, как не хочет хавать 65536 и 32768.
            # Так что мы ей скармливаем диапазон 0...32767, и передаем рукожопам из микрософта привет,
            # потому что линуксы прекрасно работают с любыми двухбайтовыми диапазонами.

            # Absolute mouse
            0x05, 0x01,  # USAGE_PAGE (Generic Desktop)
            0x09, 0x02,  # USAGE (Mouse)
            0xA1, 0x01,  # COLLECTION (Application)

            # Report ID
            *([0x85, report_id] if report_id is not None else []),

            # Pointer and Physical are required by Apple Recovery
            0x09, 0x01,  # USAGE (Pointer)
            0xA1, 0x00,  # COLLECTION (Physical)

            # 8 Buttons
            0x05, 0x09,  # USAGE_PAGE (Button)
            0x19, 0x01,  # USAGE_MINIMUM (Button 1)
            0x29, 0x08,  # USAGE_MAXIMUM (Button 8)
            0x15, 0x00,  # LOGICAL_MINIMUM (0)
            0x25, 0x01,  # LOGICAL_MAXIMUM (1)
            0x95, 0x08,  # REPORT_COUNT (8)
            0x75, 0x01,  # REPORT_SIZE (1)
            0x81, 0x02,  # INPUT (Data,Var,Abs)

            # X, Y
            0x05, 0x01,  # USAGE_PAGE (Generic Desktop)
            0x09, 0x30,  # USAGE (X)
            0x09, 0x31,  # USAGE (Y)
            0x16, 0x00, 0x00,  # LOGICAL_MINIMUM (0)
            0x26, 0xFF, 0x7F,  # LOGICAL_MAXIMUM (32767)
            0x75, 0x10,  # REPORT_SIZE (16)
            0x95, 0x02,  # REPORT_COUNT (2)
            0x81, 0x02,  # INPUT (Data,Var,Abs)

            # Wheel
            0x09, 0x38,  # USAGE (Wheel)
            0x15, 0x81,  # LOGICAL_MINIMUM (-127)
            0x25, 0x7F,  # LOGICAL_MAXIMUM (127)
            0x75, 0x08,  # REPORT_SIZE (8)
            0x95, 0x01,  # REPORT_COUNT (1)
            0x81, 0x06,  # INPUT (Data,Var,Rel)

            *(_HORIZONTAL_WHEEL if horizontal_wheel else []),

            # End
            0xC0,  # END_COLLECTION (Physical)
            0xC0,  # END_COLLECTION
        ]),
    )


def _make_relative_hid(horizontal_wheel: bool, report_id: (int | None)) -> Hid:
    return Hid(
        protocol=2,  # Mouse protocol
        subclass=1,  # Boot interface subclass

        report_length=(5 if horizontal_wheel else 4),

        report_descriptor=bytes([
            # https://github.com/NicoHood/HID/blob/0835e6a/src/SingleReport/BootMouse.cpp

            # Relative mouse
            0x05, 0x01,  # USAGE_PAGE (Generic Desktop)
            0x09, 0x02,  # USAGE (Mouse)
            0xA1, 0x01,  # COLLECTION (Application)

            # Report ID
            *([0x85, report_id] if report_id is not None else []),

            # Pointer and Physical are required by Apple Recovery
            0x09, 0x01,  # USAGE (Pointer)
            0xA1, 0x00,  # COLLECTION (Physical)

            # 8 Buttons
            0x05, 0x09,  # USAGE_PAGE (Button)
            0x19, 0x01,  # USAGE_MINIMUM (Button 1)
            0x29, 0x08,  # USAGE_MAXIMUM (Button 8)
            0x15, 0x00,  # LOGICAL_MINIMUM (0)
            0x25, 0x01,  # LOGICAL_MAXIMUM (1)
            0x95, 0x08,  # REPORT_COUNT (8)
            0x75, 0x01,  # REPORT_SIZE (1)
            0x81, 0x02,  # INPUT (Data,Var,Abs)

            # X, Y
            0x05, 0x01,  # USAGE_PAGE (Generic Desktop)
            0x09, 0x30,  # USAGE (X)
            0x09, 0x31,  # USAGE (Y)

            # Wheel
            0x09, 0x38,  # USAGE (Wheel)
            0x15, 0x81,  # LOGICAL_MINIMUM (-127)
            0x25, 0x7F,  # LOGICAL_MAXIMUM (127)
            0x75, 0x08,  # REPORT_SIZE (8)
            0x95, 0x03,  # REPORT_COUNT (3)
            0x81, 0x06,  # INPUT (Data,Var,Rel)

            *(_HORIZONTAL_WHEEL if horizontal_wheel else []),

            # End
            0xC0,  # END_COLLECTION (Physical)
            0xC0,  # END_COLLECTION
        ]),
    )
