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


import os

from .. import tools

from typing import Generator
from typing import NoReturn
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.constructor import RoundTripConstructor


# =====
class _Constructor(RoundTripConstructor):
    def check_mapping_key(  # noqa vulture-ignore
        self,
        node: Any,
        key_node: Any,
        mapping: Any,
        key: Any,
        value: Any,
    ) -> bool:

        # https://yaml.dev/doc/ruamel.yaml/api/#Duplicate_keys
        # allow_duplicate_keys=True keeps the first value,
        # but we need to overwrite it and keep the last one
        # for backward compatibility with PyYAML.

        _ = node
        _ = key_node
        _ = mapping
        _ = key
        _ = value
        return True

    def _construct_include(self, node: Any) -> NoReturn:
        _ = node
        raise ValueError("The !include directive is not supported anymore:"
                         " https://docs.pikvm.org/config/#atomic-configuration-deployment")


_Constructor.add_constructor("!include", _Constructor._construct_include)  # pylint: disable=protected-access


def load_yaml_file(path: str) -> Any:
    # ruamel.yaml ignores oOyYnN by default: https://stackoverflow.com/questions/36463531
    handler = YAML()
    handler.Constructor = _Constructor  # noqa vulture-ignore
    with open(path) as file:
        content = file.read()
        try:
            return handler.load(content)
        except Exception as ex:
            # Reraise internal exception as standard ValueError and show the incorrect file
            raise ValueError(f"Invalid YAML in the file {path!r}:\n{tools.efmt(ex)}") from None


def listed_yaml_dir(path: str) -> Generator[str]:
    for name in sorted(os.listdir(path)):
        # TODO: We want to handle *.yaml or even *.yml,
        # but but previously we didn't have such filters
        # so we need to keep unfildered list processing
        # for backward compatibility.
        file_path = os.path.join(path, name)
        if os.path.isfile(file_path) or os.path.islink(file_path):
            yield file_path
