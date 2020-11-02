# ========================================================================== #
#                                                                            #
#    KVMD - The main Pi-KVM daemon.                                          #
#                                                                            #
#    Copyright (C) 2018  Maxim Devaev <mdevaev@gmail.com>                    #
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


from typing import Callable
from typing import Any

import pytest

from kvmd.keyboard.mappings import KEYMAP

from kvmd.validators import ValidatorError
from kvmd.validators.kvm import valid_atx_power_action
from kvmd.validators.kvm import valid_atx_button
from kvmd.validators.kvm import valid_info_fields
from kvmd.validators.kvm import valid_log_seek
from kvmd.validators.kvm import valid_stream_quality
from kvmd.validators.kvm import valid_stream_fps
from kvmd.validators.kvm import valid_stream_resolution
from kvmd.validators.kvm import valid_hid_key
from kvmd.validators.kvm import valid_hid_mouse_move
from kvmd.validators.kvm import valid_hid_mouse_button
from kvmd.validators.kvm import valid_hid_mouse_delta
from kvmd.validators.kvm import valid_ugpio_driver
from kvmd.validators.kvm import valid_ugpio_channel
from kvmd.validators.kvm import valid_ugpio_mode
from kvmd.validators.kvm import valid_ugpio_view_table

from kvmd.plugins.ugpio import UserGpioModes


# =====
@pytest.mark.parametrize("arg", ["ON ", "OFF ", "OFF_HARD ", "RESET_HARD "])
def test_ok__valid_atx_power_action(arg: Any) -> None:
    assert valid_atx_power_action(arg) == arg.strip().lower()


@pytest.mark.parametrize("arg", ["test", "", None])
def test_fail__valid_atx_power_action(arg: Any) -> None:
    with pytest.raises(ValidatorError):
        print(valid_atx_power_action(arg))


# =====
@pytest.mark.parametrize("arg", ["POWER ", "POWER_LONG ", "RESET "])
def test_ok__valid_atx_button(arg: Any) -> None:
    assert valid_atx_button(arg) == arg.strip().lower()


@pytest.mark.parametrize("arg", ["test", "", None])
def test_fail__valid_atx_button(arg: Any) -> None:
    with pytest.raises(ValidatorError):
        print(valid_atx_button(arg))


# =====
@pytest.mark.parametrize("arg", [" foo ", "bar", "foo, ,bar,", " ", " , ", ""])
def test_ok__valid_info_fields(arg: Any) -> None:
    value = valid_info_fields(arg, set(["foo", "bar"]))
    assert type(value) == set  # pylint: disable=unidiomatic-typecheck
    assert value == set(filter(None, map(str.strip, str(arg).split(","))))


@pytest.mark.parametrize("arg", ["xxx", "yyy", "foo,xxx", None])
def test_fail__valid_info_fields(arg: Any) -> None:
    with pytest.raises(ValidatorError):
        print(valid_info_fields(arg, set(["foo", "bar"])))


# =====
@pytest.mark.parametrize("arg", ["0 ", 0, 1, 13])
def test_ok__valid_log_seek(arg: Any) -> None:
    value = valid_log_seek(arg)
    assert type(value) == int  # pylint: disable=unidiomatic-typecheck
    assert value == int(str(arg).strip())


@pytest.mark.parametrize("arg", ["test", "", None, -1, -13, 1.1])
def test_fail__valid_log_seek(arg: Any) -> None:
    with pytest.raises(ValidatorError):
        print(valid_log_seek(arg))


# =====
@pytest.mark.parametrize("arg", ["1 ", 20, 100])
def test_ok__valid_stream_quality(arg: Any) -> None:
    value = valid_stream_quality(arg)
    assert type(value) == int  # pylint: disable=unidiomatic-typecheck
    assert value == int(str(arg).strip())


@pytest.mark.parametrize("arg", ["test", "", None, 0, 101, 1.1])
def test_fail__valid_stream_quality(arg: Any) -> None:
    with pytest.raises(ValidatorError):
        print(valid_stream_quality(arg))


# =====
@pytest.mark.parametrize("arg", ["1 ", 120])
def test_ok__valid_stream_fps(arg: Any) -> None:
    value = valid_stream_fps(arg)
    assert type(value) == int  # pylint: disable=unidiomatic-typecheck
    assert value == int(str(arg).strip())


@pytest.mark.parametrize("arg", ["test", "", None, 121, 1.1])
def test_fail__valid_stream_fps(arg: Any) -> None:
    with pytest.raises(ValidatorError):
        print(valid_stream_fps(arg))


# =====
@pytest.mark.parametrize("arg", ["1280x720 ", "1x1"])
def test_ok__valid_stream_resolution(arg: Any) -> None:
    value = valid_stream_resolution(arg)
    assert type(value) == str  # pylint: disable=unidiomatic-typecheck
    assert value == str(arg).strip()


@pytest.mark.parametrize("arg", ["x", None, "0x0", "0x1", "1x0", "1280", "1280x", "1280x720x"])
def test_fail__valid_stream_resolution(arg: Any) -> None:
    with pytest.raises(ValidatorError):
        print(valid_stream_resolution(arg))


# =====
def test_ok__valid_hid_key() -> None:
    for key in KEYMAP:
        print(valid_hid_key(key))
        print(valid_hid_key(key + " "))


@pytest.mark.parametrize("arg", ["test", "", None, "keya"])
def test_fail__valid_hid_key(arg: Any) -> None:
    with pytest.raises(ValidatorError):
        print(valid_hid_key(arg))


# =====
@pytest.mark.parametrize("arg", [-20000, "1 ", "-1", 1, -1, 0, "20000 "])
def test_ok__valid_hid_mouse_move(arg: Any) -> None:
    assert valid_hid_mouse_move(arg) == int(str(arg).strip())


def test_ok__valid_hid_mouse_move__m50000() -> None:
    assert valid_hid_mouse_move(-50000) == -32768


def test_ok__valid_hid_mouse_move__p50000() -> None:
    assert valid_hid_mouse_move(50000) == 32767


@pytest.mark.parametrize("arg", ["test", "", None, 1.1])
def test_fail__valid_hid_mouse_move(arg: Any) -> None:
    with pytest.raises(ValidatorError):
        print(valid_hid_mouse_move(arg))


# =====
@pytest.mark.parametrize("arg", ["LEFT ", "RIGHT ", "Up ", " Down", " MiDdLe "])
def test_ok__valid_hid_mouse_button(arg: Any) -> None:
    assert valid_hid_mouse_button(arg) == arg.strip().lower()


@pytest.mark.parametrize("arg", ["test", "", None])
def test_fail__valid_hid_mouse_button(arg: Any) -> None:
    with pytest.raises(ValidatorError):
        print(valid_hid_mouse_button(arg))


# =====
@pytest.mark.parametrize("arg", [-100, "1 ", "-1", 1, -1, 0, "100 "])
def test_ok__valid_hid_mouse_delta(arg: Any) -> None:
    assert valid_hid_mouse_delta(arg) == int(str(arg).strip())


def test_ok__valid_hid_mouse_delta__m200() -> None:
    assert valid_hid_mouse_delta(-200) == -127


def test_ok__valid_hid_mouse_delta__p200() -> None:
    assert valid_hid_mouse_delta(200) == 127


@pytest.mark.parametrize("arg", ["test", "", None, 1.1])
def test_fail__valid_hid_mouse_delta(arg: Any) -> None:
    with pytest.raises(ValidatorError):
        print(valid_hid_mouse_delta(arg))


# =====
@pytest.mark.parametrize("validator", [valid_ugpio_driver, valid_ugpio_channel])
@pytest.mark.parametrize("arg", [
    "test-",
    "glados",
    "test",
    "_",
    "_foo_bar_",
    " aix",
    "a" * 255,
])
def test_ok__valid_ugpio_item(validator: Callable[[Any], str], arg: Any) -> None:
    assert validator(arg) == arg.strip()


@pytest.mark.parametrize("validator", [valid_ugpio_driver, valid_ugpio_channel])
@pytest.mark.parametrize("arg", [
    "тест",
    "-molestia",
    "te~st",
    "-",
    "-foo_bar",
    "foo bar",
    "a" * 256,
    "  ",
    "",
    None,
])
def test_fail__valid_ugpio_item(validator: Callable[[Any], str], arg: Any) -> None:
    with pytest.raises(ValidatorError):
        print(validator(arg))


# =====
@pytest.mark.parametrize("arg", ["foo", " bar", " baz "])
def test_ok__valid_ugpio_driver_variants(arg: Any) -> None:
    value = valid_ugpio_driver(arg, set(["foo", "bar", "baz"]))
    assert type(value) == str  # pylint: disable=unidiomatic-typecheck
    assert value == str(arg).strip()


@pytest.mark.parametrize("arg", ["BAR", " ", "", None])
def test_fail__valid_ugpio_driver_variants(arg: Any) -> None:
    with pytest.raises(ValidatorError):
        print(valid_ugpio_driver(arg, set(["foo", "bar", "baz"])))


# =====
@pytest.mark.parametrize("arg", ["Input ", " OUTPUT "])
def test_ok__valid_ugpio_mode(arg: Any) -> None:
    assert valid_ugpio_mode(arg, UserGpioModes.ALL) == arg.strip().lower()


@pytest.mark.parametrize("arg", ["test", "", None])
def test_fail__valid_ugpio_mode(arg: Any) -> None:
    with pytest.raises(ValidatorError):
        print(valid_ugpio_mode(arg, UserGpioModes.ALL))


# =====
@pytest.mark.parametrize("arg,retval", [
    ([],                     []),
    ({},                     []),
    ([[]],                   [[]]),
    ([{}],                   [[]]),
    ([[[]]],                 [["[]"]]),
    ("",                     []),
    ("ab",                   [["a"], ["b"]]),
    ([[1, 2], [None], "ab", {}, [3, 4]],   [["1", "2"], ["None"], ["a", "b"], [], ["3", "4"]]),
])
def test_ok__valid_ugpio_view_table(arg: Any, retval: Any) -> None:
    assert valid_ugpio_view_table(arg) == retval


@pytest.mark.parametrize("arg", [None, [None], 1])
def test_fail__valid_ugpio_view_table(arg: Any) -> None:
    with pytest.raises(ValidatorError):
        print(valid_ugpio_view_table(arg))
