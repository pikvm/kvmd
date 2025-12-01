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


from typing import Callable
from typing import Any

from . import BaseUserGpioDriver


# =====
class Plugin(BaseUserGpioDriver):
    @classmethod
    def get_pin_validator(cls) -> Callable[[Any], Any]:
        return str

    async def read(self, pin: str) -> bool:
        _ = pin
        return False

    async def write(self, pin: str, state: bool) -> None:
        _ = pin
        _ = state

    def __str__(self) -> str:
        return f"NOOP({self._instance_name})"

    __repr__ = __str__
