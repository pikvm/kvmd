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


import json
import argparse

from typing import Any

from ... import tools

from ...yamlconf import ConfigError
from ...yamlconf.merger import yaml_merge

from .. import init
from .. import override_checked


# =====
def _parse_value(value: str) -> Any:
    value = value.strip()
    if (
        not value.isdigit()
        and value not in ["true", "false", "null"]
        and not value.startswith(("{", "[", "\""))
    ):
        value = f"\"{value}\""
    return json.loads(value)


def _build_raw(options: list[str]) -> dict:
    raw: dict = {}
    for option in options:
        key: str
        (key, value) = (option.split("=", 1) + [None])[:2]  # type: ignore
        if len(key.strip()) == 0:
            raise ConfigError(f"Empty option key (required 'key=value' instead of {option!r})")
        if value is None:
            raise ConfigError(f"No value for key {key!r}")

        path = list(filter(None, map(str.strip, key.split("/"))))
        if len(path) == 0:
            raise ConfigError("Writing to the root is not supported")

        sub = raw
        for key in path[:-1]:
            sub.setdefault(key, {})
            sub = sub[key]
        sub[path[-1]] = _parse_value(value)
    return raw


# =====
def main() -> None:
    ia = init(
        add_help=False,
        cli_logging=True,
    )
    parser = argparse.ArgumentParser(
        prog="kvmd-override",
        description="Writes some override configuration and validates the result",
        parents=[ia.parser],
    )
    parser.add_argument("-s", "--set", default=[], nargs="+",
                        help="Validate and write override values (list like sec/sub/opt=value ...)", metavar="<k=v>")
    options = parser.parse_args(ia.args)

    if not options.set:
        return

    try:
        raw = _build_raw(options.set)
    except ConfigError as ex:
        raise SystemExit(tools.efmt(ex))
    try:
        with override_checked(ia.paths) as doc:
            yaml_merge(doc, raw)
    except ConfigError as ex:
        raise SystemExit(tools.efmt(ex))
