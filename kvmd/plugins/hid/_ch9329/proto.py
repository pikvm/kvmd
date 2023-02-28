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


import dataclasses
import struct
import math

from ....keyboard.mappings import KEYMAP

from ....mouse import MouseRange

from ....logging import get_logger

from .... import tools


# =====
class BaseEvent:
    def make_request(self) -> bytes:
        raise NotImplementedError


# =====
_KEYBOARD_NAMES_TO_CODES = {
    "disabled": 0b00000000,
    "usb":      0b00000001,
    "ps2":      0b00000011,
}
_KEYBOARD_CODES_TO_NAMES = tools.swapped_kvs(_KEYBOARD_NAMES_TO_CODES)


def get_active_keyboard(outputs: int) -> str:
    return _KEYBOARD_CODES_TO_NAMES.get(outputs & 0b00000111, "disabled")

@dataclasses.dataclass(frozen=True)
class SetKeyboardOutputEvent(BaseEvent):
    keyboard: str

    def __post_init__(self) -> None:
        assert not self.keyboard or self.keyboard in _KEYBOARD_NAMES_TO_CODES

    def make_request(self) -> list:
        code = _KEYBOARD_NAMES_TO_CODES.get(self.keyboard, 0)
        return _make_request([0x10,0x00,0x00,0x00,0x00])


# =====
_MOUSE_NAMES_TO_CODES = {
    "disabled":  0b00000000,
    "usb":       0b00001000,
    "usb_rel":   0b00010000,
    "ps2":       0b00011000,
    "usb_win98": 0b00100000,
}
_MOUSE_CODES_TO_NAMES = tools.swapped_kvs(_MOUSE_NAMES_TO_CODES)


def get_active_mouse(outputs: int) -> str:
    return _MOUSE_CODES_TO_NAMES.get(outputs & 0b00111000, "disabled")


@dataclasses.dataclass(frozen=True)
class SetMouseOutputEvent(BaseEvent):
    mouse: str

    def __post_init__(self) -> None:
        assert not self.mouse or self.mouse in _MOUSE_NAMES_TO_CODES

    def make_request(self) -> list:
        return _make_request([0x10,0x00,0x00,0x00,0x00])


# =====
@dataclasses.dataclass(frozen=True)
class SetConnectedEvent(BaseEvent):
    connected: bool

    def make_request(self) -> list:
        return _make_request([0x10,0x00,0x00,0x00,0x00])


# =====
class ClearEvent(BaseEvent):
    def make_request(self) -> list:
        return _make_request([0x10,0x00,0x00,0x00,0x00])


@dataclasses.dataclass(frozen=True)
class KeyEvent(BaseEvent):
    active_keys : list
    name: str
    state: bool

    def __post_init__(self) -> None:
        assert self.name in KEYMAP

    def make_request(self) -> list:
        get_logger(0).info(f"HID : KeyEvent name = {self.name} state = {self.state}, active_keys {self.active_keys}")
        code = KEYMAP[self.name].usb.code if self.state else 0
        get_logger(0).info(f"HID : KeyEvent code = {code:x}")
        command = [0x00, 0x02, 0x08, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]
        if self.state :
            if len(self.active_keys) > 1:
                for idx, key in enumerate(self.active_keys) :
                    if key[1] : command[3+idx] = KEYMAP[key[0]].usb.code
            command[5] = code
        return _make_request(command)


@dataclasses.dataclass(frozen=True)
class MouseEvent(BaseEvent):
    name: str
    state: bool
    to_x: list
    to_y: list
    wheel_y: int

    def make_request(self) -> list:
        get_logger(0).info(f"HID : MouseEvent name = {self.name} state = {self.state}")
        code = 0x00;
        if self.state:
            match self.name:
                case 'left':
                    code = 0x01
                case 'right':
                    code = 0x02
                case 'middle':
                    code = 0x04
        get_logger(0).info(f"HID : MouseEvent to_x = {self.to_x} to_y = {self.to_y}")
        command = [0x00, 0x04, 0x07, 0x02, code, 0x00, 0x00, 0x00, 0x00, 0x00]
        if len(self.to_x) == 2:
            command[6] = self.to_x[0]
            command[5] = self.to_x[1]
        if len(self.to_y) == 2:
            command[8] = self.to_y[0]
            command[7] = self.to_y[1]
        if self.wheel_y:
            command[9] = self.wheel_y
        return _make_request(command)



@dataclasses.dataclass(frozen=True)
class MouseRelativeEvent(BaseEvent):
    delta_x: int
    delta_y: int

    def __post_init__(self) -> None:
        assert -127 <= self.delta_x <= 127
        assert -127 <= self.delta_y <= 127


    def make_request(self) -> list:
        cmd = [0x00, 0x05, 0x05, 0x01, 0x00, 0x00, 0x00, 0x00]
        if self.delta_x :
            delta_x = math.ceil(self.delta_x / 4)
            cmd[5] = delta_x if delta_x > 0 else 255 + delta_x
        if self.delta_y :
            delta_y = math.ceil(self.delta_y / 4)
            cmd[6] = delta_y if delta_y > 0 else 255 + delta_y
        return _make_request(cmd)


# =====


def check_response(response: list) -> bool:
    assert len(response) in (4, 10), response
    res_sum = response.pop()
    return (_checksum(response) == res_sum)

def key_modifier(key: str) -> bool:
     return KEYMAP[key].usb.is_modifier

def mouse_pos(num: int) -> list:
    to_fixed = math.ceil(MouseRange.remap(num, 0, MouseRange.MAX) / 8)
    return [ to_fixed >> 8, to_fixed & 0xFF ]

def mouse_wheel(num: int) -> int:
    return 1 if num > 0 else 255 - 1

def _make_request(command: list) -> list:
    command.insert(0, 0xAB)
    command.insert(0, 0x57)
    sum = _checksum(command)
    command.append(sum)
    return command

def _checksum(command: list) -> int:
    return sum(command) % 256

# =====
RESET = _make_request([0x00,0x0F,0x00])
GET_INFO = _make_request([0x00,0x01,0x00])
#REQUEST_PING = _make_request(b"\x01\x00\x00\x00\x00")
#REQUEST_REPEAT = _make_request(b"\x02\x00\x00\x00\x00")

#RESPONSE_LEGACY_OK = b"\x33\x20" + struct.pack(">H", _make_crc16(b"\x33\x20"))
