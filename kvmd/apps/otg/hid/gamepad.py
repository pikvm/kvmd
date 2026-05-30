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


from . import Hid


# =====
def make_gamepad_hid() -> Hid:
    return Hid(
        protocol=0,  # None
        subclass=0,  # None

        report_length=9,

        report_descriptor=bytes([
            # A generic 16-button gamepad with two analog sticks, two analog triggers
            # and a D-pad (hat switch). The report layout maps 1:1 to the W3C "standard
            # gamepad" exposed by the browser Gamepad API:
            #
            #   byte 0: X   (left stick X)    0..255, center 128
            #   byte 1: Y   (left stick Y)    0..255, center 128
            #   byte 2: Rx  (right stick X)   0..255, center 128
            #   byte 3: Ry  (right stick Y)   0..255, center 128
            #   byte 4: Z   (left trigger)    0..255, released 0
            #   byte 5: Rz  (right trigger)   0..255, released 0
            #   byte 6: hat (D-pad), low nibble 0..7 clockwise from up, 8 = centered
            #   byte 7: buttons 1..8  (bit 0 == button 1)
            #   byte 8: buttons 9..16
            #
            # Parsed/verified using https://eleccelerator.com/usbdescreqparser

            0x05, 0x01,        # USAGE_PAGE (Generic Desktop)
            0x09, 0x05,        # USAGE (Game Pad)
            0xA1, 0x01,        # COLLECTION (Application)

            # Two analog sticks + two analog triggers: six 8-bit absolute axes
            0x05, 0x01,        # USAGE_PAGE (Generic Desktop)
            0x09, 0x30,        # USAGE (X)
            0x09, 0x31,        # USAGE (Y)
            0x09, 0x33,        # USAGE (Rx)
            0x09, 0x34,        # USAGE (Ry)
            0x09, 0x32,        # USAGE (Z)
            0x09, 0x35,        # USAGE (Rz)
            0x15, 0x00,        # LOGICAL_MINIMUM (0)
            0x26, 0xFF, 0x00,  # LOGICAL_MAXIMUM (255)
            0x75, 0x08,        # REPORT_SIZE (8)
            0x95, 0x06,        # REPORT_COUNT (6)
            0x81, 0x02,        # INPUT (Data,Var,Abs)

            # D-pad as a 4-bit hat switch with a null (centered) state
            0x05, 0x01,        # USAGE_PAGE (Generic Desktop)
            0x09, 0x39,        # USAGE (Hat switch)
            0x15, 0x00,        # LOGICAL_MINIMUM (0)
            0x25, 0x07,        # LOGICAL_MAXIMUM (7)
            0x35, 0x00,        # PHYSICAL_MINIMUM (0)
            0x46, 0x3B, 0x01,  # PHYSICAL_MAXIMUM (315)
            0x65, 0x14,        # UNIT (Eng Rot: Degrees)
            0x75, 0x04,        # REPORT_SIZE (4)
            0x95, 0x01,        # REPORT_COUNT (1)
            0x81, 0x42,        # INPUT (Data,Var,Abs,Null)

            # 4 bits of padding to byte-align the hat
            0x65, 0x00,        # UNIT (None)
            0x75, 0x04,        # REPORT_SIZE (4)
            0x95, 0x01,        # REPORT_COUNT (1)
            0x81, 0x03,        # INPUT (Cnst,Var,Abs)

            # 16 digital buttons
            0x05, 0x09,        # USAGE_PAGE (Button)
            0x19, 0x01,        # USAGE_MINIMUM (Button 1)
            0x29, 0x10,        # USAGE_MAXIMUM (Button 16)
            0x15, 0x00,        # LOGICAL_MINIMUM (0)
            0x25, 0x01,        # LOGICAL_MAXIMUM (1)
            0x75, 0x01,        # REPORT_SIZE (1)
            0x95, 0x10,        # REPORT_COUNT (16)
            0x81, 0x02,        # INPUT (Data,Var,Abs)

            0xC0,              # END_COLLECTION
        ]),
    )
