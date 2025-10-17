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


from typing import Mapping
from typing import Any

import ruamel.yaml.compat


# =====
def yaml_merge(dest: Mapping, src: Mapping, src_name: str="") -> None:
    """ Merges the source dictionary into the destination dictionary. """

    # Checking if destination is None
    if dest is None:
        # We can't merge into a None
        raise ValueError(f"Could not merge {src_name or 'config'} into None. The destination cannot be None")

    # Checking if source is None or empty
    if not src:
        # If src is None or empty, there's nothing to merge
        return

    _merge(dest, src)


# ======
def _is_dict(obj: Any) -> bool:
    # OrderedDict in ruamel is inherited from dict, but we want to check it explicitly.
    return isinstance(obj, (dict, ruamel.yaml.compat.OrderedDict))


def _merge(dest: Mapping, src: Mapping) -> None:
    for key in src:
        if key in dest:
            if _is_dict(dest[key]) and _is_dict(src[key]):
                _merge(dest[key], src[key])
                continue
        dest[key] = src[key]  # type: ignore
