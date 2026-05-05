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


import asyncio

from typing import Final
from typing import Callable
from typing import Any

import aiohttp

from ...logging import get_logger

from ... import tools
from ... import aiotools
from ... import htclient

from ...yamlconf import Section
from ...yamlconf import Option

from ...validators.basic import valid_stripped_string_not_empty
from ...validators.basic import valid_bool
from ...validators.basic import valid_number
from ...validators.basic import valid_float_f01

from . import BaseUserGpioDriver
from . import GpioDriverOfflineError


# =====
class Plugin(BaseUserGpioDriver):  # pylint: disable=too-many-instance-attributes
    def __init__(
        self,
        instance_name: str,
        notifier: aiotools.AioNotifier,
        c: Section,
    ) -> None:

        super().__init__(instance_name, notifier, c)

        self.__url:        Final[str]   = c.url
        self.__verify:     Final[bool]  = c.verify
        self.__user:       Final[str]   = c.user
        self.__passwd:     Final[str]   = c.passwd
        self.__state_poll: Final[float] = c.state_poll
        self.__timeout:    Final[float] = c.timeout

        self.__initial: dict[str, (bool | None)] = {}

        self.__state: dict[str, (bool | None)] = {}
        self.__update_nr = aiotools.AioNotifier()

        self.__session: (aiohttp.ClientSession | None) = None

    @classmethod
    def get_plugin_options(cls) -> dict[str, Option]:
        return {
            "url":        Option("",   type=valid_stripped_string_not_empty),
            "verify":     Option(True, type=valid_bool),
            "user":       Option(""),
            "passwd":     Option(""),
            "state_poll": Option(5.0,  type=valid_float_f01),
            "timeout":    Option(5.0,  type=valid_float_f01),
        }

    @classmethod
    def get_pin_validator(cls) -> Callable[[Any], Any]:
        return valid_number.mk(min=0, max=7, name="ANELPWR channel")

    def register_input(self, pin: str, debounce: float) -> None:
        _ = debounce
        self.__state[pin] = None

    def register_output(self, pin: str, initial: (bool | None)) -> None:
        self.__initial[pin] = initial
        self.__state[pin] = None

    async def prepare(self) -> None:
        await asyncio.gather(*[
            self.write(pin, state)
            for (pin, state) in self.__initial.items()
            if state is not None
        ], return_exceptions=True)

    async def run(self) -> None:
        prev_state: (dict | None) = None
        while True:
            session = self.__ensure_session()
            try:
                async with session.get(f"{self.__url}/strg.cfg") as resp:
                    htclient.raise_not_200(resp)
                    parts = (await resp.text()).split(";")
                    for pin in self.__state:
                        self.__state[pin] = (parts[1 + int(pin) * 5] == "1")
            except Exception as ex:
                get_logger().error("Failed ANELPWR bulk GET request: %s", tools.efmt(ex))
                self.__state = dict.fromkeys(self.__state, None)
            if self.__state != prev_state:
                self._notifier.notify()
                prev_state = self.__state
            await self.__update_nr.wait(self.__state_poll)

    async def cleanup(self) -> None:
        if self.__session:
            try:
                await self.__session.close()
            finally:
                self.__session = None

    async def read(self, pin: str) -> bool:
        if self.__state[pin] is None:
            raise GpioDriverOfflineError(self)
        return self.__state[pin]  # type: ignore

    async def write(self, pin: str, state: bool) -> None:
        session = self.__ensure_session()
        try:
            async with session.post(
                url=f"{self.__url}/ctrl.htm",
                data=f"F{pin}={int(state)}",
                headers={aiohttp.hdrs.CONTENT_TYPE: "text/plain"},
            ) as resp:
                htclient.raise_not_200(resp)
        except Exception as ex:
            get_logger().error("Failed ANELPWR POST request to pin %s: %s", pin, tools.efmt(ex))
            raise GpioDriverOfflineError(self)
        self.__update_nr.notify()

    def __ensure_session(self) -> aiohttp.ClientSession:
        if not self.__session:
            self.__session = aiohttp.ClientSession(
                headers={aiohttp.hdrs.USER_AGENT: htclient.make_user_agent("KVMD")},
                connector=aiohttp.TCPConnector(ssl=self.__verify),
                auth=(aiohttp.BasicAuth(self.__user, self.__passwd) if self.__user else None),
                timeout=aiohttp.ClientTimeout(total=self.__timeout),
            )
        return self.__session

    def __str__(self) -> str:
        return f"ANELPWR({self._instance_name})"

    __repr__ = __str__
