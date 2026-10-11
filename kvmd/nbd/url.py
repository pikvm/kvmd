# ========================================================================== #
#                                                                            #
#    KVMD - The main PiKVM daemon.                                           #
#                                                                            #
#    Copyright (C) 2020  Maxim Devaev <mdevaev@gmail.com>                    #
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
import dataclasses
import urllib.parse

from .errors import NbdRemoteError


# =====
@dataclasses.dataclass(frozen=True)
class NbdUrl:
    raw:  str
    port: int = dataclasses.field(compare=False)
    host: str = dataclasses.field(init=False, compare=False)
    name: str = dataclasses.field(init=False, compare=False)
    path: str = dataclasses.field(init=False, compare=False)

    def __post_init__(self) -> None:
        parsed = urllib.parse.urlparse(self.raw)

        if not parsed.hostname:
            raise NbdRemoteError("Empty hostname in URL")
        object.__setattr__(self, "host", parsed.hostname)

        try:
            port = (parsed.port or self.port)
        except Exception:
            raise NbdRemoteError("Invalid port in URL")
        object.__setattr__(self, "port", port)

        object.__setattr__(self, "name", self._make_name(parsed.path))
        object.__setattr__(self, "path", self._make_path(parsed.path))

    def _make_name(self, path: str) -> str:
        name = os.path.basename(path)
        if not name:
            raise NbdRemoteError("Empty filename in URL")
        return name

    def _make_path(self, path: str) -> str:
        if not path:
            raise NbdRemoteError("Empty file path in URL")
        return path
