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
def make_consumer_hid() -> Hid:
    return Hid(
        protocol=0,  # None protocol
        subclass=0,  # No subclass

        report_length=2,

        report_descriptor=bytes([
            # https://learn.microsoft.com/en-us/windows-hardware/drivers/hid/display-brightness-control

            0x05, 0x0C,  # USAGE_PAGE (Consumer Devices)
            0x09, 0x01,  # USAGE (Consumer Control)
            0xA1, 0x01,  # COLLECTION (Application)
            0x15, 0x00,  # LOGICAL_MINIMUM (0)
            0x26, 0xFF, 0x03,  # LOGICAL_MAXIMUM (0x3FF)
            0x19, 0x00,  # USAGE_MINIMUM (Unassigned)
            0x2A, 0xFF, 0x03,  # USAGE_MAXIMUM (0x3FF)
            0x75, 0x10,  # REPORT_SIZE (16)
            0x95, 0x01,  # REPORT_COUNT (1)
            0x81, 0x00,  # INPUT (Data,Array,Abs)
            0xC0,  # END_COLLECTION
        ]),
    )
