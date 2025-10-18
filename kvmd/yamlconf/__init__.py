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


import contextlib
import json

from typing import Generator
from typing import Callable
from typing import Any


# =====
class ConfigError(ValueError):
    pass


# =====
def build_raw_from_options(options: list[str]) -> dict[str, Any]:
    raw: dict[str, Any] = {}
    for option in options:
        key: str
        (key, value) = (option.split("=", 1) + [None])[:2]  # type: ignore
        if len(key.strip()) == 0:
            raise ConfigError(f"Empty option key (required 'key=value' instead of {option!r})")
        if value is None:
            raise ConfigError(f"No value for key {key!r}")

        section = raw
        subs = list(filter(None, map(str.strip, key.split("/"))))
        for sub in subs[:-1]:
            section.setdefault(sub, {})
            section = section[sub]
        section[subs[-1]] = _parse_value(value)
    return raw


def _parse_value(value: str) -> Any:
    value = value.strip()
    if (
        not value.isdigit()
        and value not in ["true", "false", "null"]
        and not value.startswith(("{", "[", "\""))
    ):
        value = f"\"{value}\""
    return json.loads(value)


# =====
class Section(dict):
    def __init__(self) -> None:
        dict.__init__(self)
        self.__options: dict[str, "Option"] = {}

    def _unpack(self, ignore: (list[str] | None)=None) -> dict[str, Any]:
        if ignore is None:
            ignore = []
        unpacked: dict[str, Any] = {}
        for (key, value) in self.items():
            if key not in ignore:
                if isinstance(value, Section):
                    unpacked[key] = value._unpack()
                else:  # Option
                    unpacked[self._get_unpack_as(key)] = value  # pylint: disable=protected-access
        return unpacked

    def _set_option(self, key: str, option: "Option") -> None:
        self.__options[key] = option

    def _get_default(self, key: str) -> Any:
        return self.__options[key].default

    def _get_unpack_as(self, key: str) -> str:
        return (self.__options[key].unpack_as or key)

    def _get_hint(self, key: str) -> str:
        return self.__options[key].hint

    def __getattribute__(self, key: str) -> Any:
        if key in self:
            return self[key]
        else:  # For pickling
            return dict.__getattribute__(self, key)


class Stub:
    pass


class Option:
    __type = type

    def __init__(
        self,
        default: Any,
        type: (Callable[[Any], Any] | None)=None,  # pylint: disable=redefined-builtin
        if_none: Any=Stub,
        if_empty: Any=Stub,
        unpack_as: str="",
        hint: str="",
    ) -> None:

        self.default = default
        self.type: Callable[[Any], Any] = (type or (self.__type(default) if default is not None else str))  # type: ignore
        self.if_none = if_none
        self.if_empty = if_empty
        self.unpack_as = unpack_as
        self.hint = hint

    def __repr__(self) -> str:
        return (
            f"<Option(default={self.default}, type={self.type}, if_none={self.if_none},"
            f" if_empty={self.if_empty}, unpack_as={self.unpack_as}, hint={self.hint})>"
        )


# =====
@contextlib.contextmanager
def manual_validated(value: Any, *path: str) -> Generator[None, None, None]:
    try:
        yield
    except (TypeError, ValueError) as ex:
        raise ConfigError(f"Invalid value {value!r} for key {'/'.join(path)!r}: {ex}")


def make_config(
    main: Any,
    override: Any,
    scheme: dict,
    _path: tuple[str, ...]=(),
) -> Section:

    if not isinstance(main, dict):
        raise ConfigError(f"The node {('/'.join(_path) or '/')!r} of main must be a dictionary")
    if not isinstance(override, dict):
        raise ConfigError(f"The node {('/'.join(_path) or '/')!r} of override must be a dictionary")

    config = Section()

    def make_full_path(key: str) -> tuple[str, ...]:
        return _path + (key,)

    def make_full_name(key: str) -> str:
        return "/".join(make_full_path(key))

    for key in scheme:
        if isinstance(scheme[key], Option):
            option: Option = scheme[key]
            if key in main and option.default != main[key]:
                option.default = main[key]

            value = override.get(key, option.default)
            if option.if_none != Stub and value is None:
                value = option.if_none
            elif option.if_empty != Stub and not value:
                value = option.if_empty
            else:
                try:
                    value = option.type(value)
                except (TypeError, ValueError) as ex:
                    raise ConfigError(f"Invalid value {value!r} for key {make_full_name(key)!r}: {ex}")

            config[key] = value
            config._set_option(key, option)  # pylint: disable=protected-access

        elif isinstance(scheme[key], dict):
            config[key] = make_config(
                main=main.get(key, {}),
                override=override.get(key, {}),
                scheme=scheme[key],
                _path=make_full_path(key),
            )

        else:
            raise RuntimeError(f"Incorrect scheme definition for key {make_full_name(key)!r}:"
                               f" the value is {type(scheme[key])!r}, not dict() or Option()")
    return config
