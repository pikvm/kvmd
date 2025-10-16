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


import textwrap

from typing import Generator
from typing import Any

import yaml

from .. import tools

from . import Section


# =====
def make_config_dump(config: Section, only_changed: bool) -> str:
    return "\n".join(_inner_make_dump(config, only_changed))


_INDENT = 4


def _inner_make_dump(
    config: Section,
    only_changed: bool,
    _level: int=0,
) -> Generator[str, None, None]:

    for (key, value) in tools.sorted_kvs(config):
        if isinstance(value, Section):
            prefix = " " * _INDENT * _level
            lines = list(_inner_make_dump(value, only_changed, _level + 1))
            if lines:
                yield f"{prefix}{key}:"
                yield from lines
                yield ""
        else:
            default = config._get_default(key)  # pylint: disable=protected-access
            comment = config._get_help(key)  # pylint: disable=protected-access
            if default == value:
                if not only_changed:
                    yield _make_yaml_kv(key, value, _level, comment=comment)
            else:
                yield _make_yaml_kv(key, default, _level, comment=comment, commented=True)
                yield _make_yaml_kv(key, value, _level)


def _make_yaml_kv(
    key: str,
    value: Any,
    level: int,
    comment: str="",
    commented: bool=False,
) -> str:

    text = yaml.dump(value, indent=_INDENT, allow_unicode=True)
    text = text.replace("\n...\n", "").strip()
    if (
        isinstance(value, dict) and text[0] != "{"
        or isinstance(value, list) and text[0] != "["
    ):
        text = "\n" + textwrap.indent(text, prefix=" " * _INDENT)
    else:
        text = " " + text

    prefix = " " * _INDENT * level
    if commented:
        prefix = prefix + "# "
    text = textwrap.indent(f"{key}:{text}", prefix=prefix)

    if comment:
        lines = text.split("\n")
        lines[0] += "  # " + comment
        text = "\n".join(lines)
    return text
