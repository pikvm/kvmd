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


import io
import textwrap
import contextlib

from typing import Callable
from typing import Generator
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.nodes import ScalarNode
from ruamel.yaml.nodes import CollectionNode
from ruamel.yaml.nodes import SequenceNode
from ruamel.yaml.nodes import MappingNode
from ruamel.yaml.comments import CommentedMap
from ruamel.yaml.representer import RoundTripRepresenter

import pygments
import pygments.lexers.data
import pygments.formatters

from .. import tools

from . import Section


# =====
class YamlHexInt(int):
    pass


class YamlOctInt(int):
    pass


class YamlInlinedItemsList(list):
    pass


class _SimpleRepresenter(RoundTripRepresenter):
    def ignore_aliases(self, data: Any) -> bool:
        return True

    def _represent_hex_int(self, value: YamlHexInt) -> ScalarNode:
        if value > 0:
            value: str = f"0x{value:X}"  # type: ignore
        return self.represent_scalar("tag:yaml.org,2002:int", str(value))

    def _represent_oct_int(self, value: YamlOctInt) -> ScalarNode:
        if value > 0:
            value: str = f"0o{value:o}"  # type: ignore
        return self.represent_scalar("tag:yaml.org,2002:int", str(value))

    def _represent_inlined_items_list(self, seq: YamlInlinedItemsList) -> SequenceNode:
        node = self.represent_sequence("tag:yaml.org,2002:seq", seq)
        for child in node.value:
            if isinstance(child, CollectionNode):
                child.flow_style = True
        return node


_SimpleRepresenter.add_representer(YamlHexInt, _SimpleRepresenter._represent_hex_int)  # pylint: disable=protected-access
_SimpleRepresenter.add_representer(YamlOctInt, _SimpleRepresenter._represent_oct_int)  # pylint: disable=protected-access
_SimpleRepresenter.add_representer(YamlInlinedItemsList, _SimpleRepresenter._represent_inlined_items_list)  # pylint: disable=protected-access


_INDENT = 4


class _ConfigRepresenter(_SimpleRepresenter):
    def __init__(self, *args, **kwargs) -> None:  # type: ignore
        super().__init__(*args, **kwargs)

        self.only_changed = False
        self.__depth = 0

        # This is used only for dumping default values.
        # They should not have Sections() inside.
        # Avoid potential recursion too.
        self.__handler = _YamlHandler()
        self.__handler.Representer = _SimpleRepresenter

    def _represent_section(self, config: Section) -> MappingNode:
        self.__depth += 1
        com = CommentedMap(dict(config))
        sections: set[str] = set()
        for (key, value) in config.items():
            if isinstance(value, Section):
                sections.add(key)
            else:
                hint = config._get_hint(key)  # pylint: disable=protected-access
                com[key] = self.__get_hinted(value, hint)
                default = config._get_default(key)  # pylint: disable=protected-access
                if value != default:
                    comment = self.__make_comment(default, hint)
                    if "\n" in comment:
                        com.yaml_set_comment_before_after_key(
                            key=key,
                            after=comment,
                            after_indent=(self.__depth * _INDENT),
                        )
                    else:
                        com.yaml_add_eol_comment(comment, key, column=0)
                elif self.only_changed:
                    com.pop(key)

        node = self.represent_mapping("tag:yaml.org,2002:map", com)
        if self.only_changed:
            node.value = [
                (k_node, v_node)
                for (k_node, v_node) in node.value
                if (
                    not isinstance(v_node, MappingNode)
                    or not isinstance(v_node.value, list)
                    or len(v_node.value) != 0
                )
            ]
        self.__depth -= 1
        return node

    def __get_hinted(self, value: Any, hint: str) -> Any:
        match hint:
            case "hex" if isinstance(value, int):
                return YamlHexInt(value)
            case "oct" if isinstance(value, int):
                return YamlOctInt(value)
            case "inlined_items" if isinstance(value, list):
                return YamlInlinedItemsList(value)
        return value

    def __make_comment(self, default: Any, hint: str) -> str:
        text = self.__handler.dump_as_string(self.__get_hinted(default, hint))
        text = text.rstrip()
        if text.endswith("\n..."):
            text = text[:-4].rstrip()
        text = textwrap.dedent(text)
        nl = ("\n" if "\n" in text else " ")  # Multiline or single-line
        return f"### Default:{nl}{text}"


_ConfigRepresenter.add_representer(Section, _ConfigRepresenter._represent_section)  # pylint: disable=protected-access


class _YamlHandler(YAML):
    def __init__(self) -> None:
        super().__init__()
        self.preserve_quotes = True
        self.indent(mapping=_INDENT, sequence=_INDENT, offset=_INDENT)
        # ruamel.yaml ignores oOyYnN by default: https://stackoverflow.com/questions/36463531

    def dump_as_string(self, data: Any) -> str:
        with io.StringIO() as file:
            self.dump(data, file)
            return file.getvalue()


def dump_yaml(data: Any, only_changed: bool=False, colored: bool=False) -> str:
    handler = _YamlHandler()
    handler.Representer = _ConfigRepresenter
    handler.representer.only_changed = only_changed
    text = handler.dump_as_string(data)
    if colored:
        text = pygments.highlight(
            text,
            pygments.lexers.data.YamlLexer(),
            pygments.formatters.TerminalFormatter(bg="dark"),  # pylint: disable=no-member
        )
    return text


@contextlib.contextmanager
def override_yaml_file(path: str, validator: Callable[[str], None]) -> Generator[Any]:
    handler = _YamlHandler()
    handler.Representer = _ConfigRepresenter
    with tools.atomic_file_edit(path) as tmp_path:
        # It seems ruamel.yaml can't keep comments for empty file
        # without any significant data, se we add an empty dict
        # to be an "anchor" for our future commits.
        # FIXME: Is there a good way to handle this?
        empty = True
        with open(tmp_path) as file:
            for line in map(str.strip, file.readlines()):
                if len(line) == 0 or line.startswith("#"):
                    continue
                empty = False
                break
        if empty:
            with open(tmp_path, "a") as file:
                file.write("\n{}")

        with open(tmp_path) as file:
            doc = handler.load(file)

        try:  # pylint: disable=no-else-raise
            yield doc
        except Exception:  # pylint: disable=try-except-raise
            raise
        else:  # Makes pylint happy
            with open(tmp_path, "w") as file:
                file.write(handler.dump_as_string(doc))
            validator(tmp_path)
