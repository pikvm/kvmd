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

import pytest

from kvmd.keyboard.keysym import SymmapModifiers
from kvmd.keyboard.printer import text_to_evdev_keys
from kvmd.keyboard.printer import _ch_to_keysym  # pylint: disable=protected-access


# =====
# A minimal symmap that mimics the relevant keys of layouts like Swedish:
#   "}" is typed as AltGr + "0", and the double quote as Shift + "2".
# See https://github.com/pikvm/pikvm/issues/1549
_SYMMAP = {
    _ch_to_keysym("}"): {SymmapModifiers.ALTGR: ecodes.KEY_0},
    _ch_to_keysym("\""): {SymmapModifiers.SHIFT: ecodes.KEY_2},
    _ch_to_keysym("a"): {0: ecodes.KEY_A},
}


# =====
@pytest.mark.parametrize("text", ["}\"", "\"}", "}a\"", "}\"}\"a"])
def test_ok__printer_no_modifier_overlap(text: str) -> None:
    # Shift and AltGr must never be held at the same time while switching
    # between keys, otherwise many layouts produce a wrong symbol.
    held: set[int] = set()
    for (key, state) in text_to_evdev_keys(text, _SYMMAP):
        if state:
            held.add(key)
        else:
            held.discard(key)
        assert not (ecodes.KEY_LEFTSHIFT in held and ecodes.KEY_RIGHTALT in held)
    assert len(held) == 0  # Everything must be released at the end


def test_ok__printer_release_before_press() -> None:
    # AltGr must be released before Shift is pressed when going from "}" to the quote.
    assert list(text_to_evdev_keys("}\"", _SYMMAP)) == [
        (ecodes.KEY_RIGHTALT, True),
        (ecodes.KEY_0, True),
        (ecodes.KEY_0, False),
        (ecodes.KEY_RIGHTALT, False),
        (ecodes.KEY_LEFTSHIFT, True),
        (ecodes.KEY_2, True),
        (ecodes.KEY_2, False),
        (ecodes.KEY_LEFTSHIFT, False),
    ]


def test_ok__printer_keeps_shared_modifier_pressed() -> None:
    # A modifier shared by consecutive characters is kept pressed (not toggled per key).
    assert list(text_to_evdev_keys("\"\"", _SYMMAP)) == [
        (ecodes.KEY_LEFTSHIFT, True),
        (ecodes.KEY_2, True),
        (ecodes.KEY_2, False),
        (ecodes.KEY_2, True),
        (ecodes.KEY_2, False),
        (ecodes.KEY_LEFTSHIFT, False),
    ]
