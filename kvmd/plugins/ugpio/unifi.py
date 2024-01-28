# ========================================================================== #
#                                                                            #
#    KVMD - The main PiKVM daemon.                                           #
#                                                                            #
#    Copyright (C) 2018-2023  Maxim Devaev <mdevaev@gmail.com>               #
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

from typing import Callable
from typing import Any

import aiohttp

from ...logging import get_logger

from ... import tools
from ... import aiotools
from ... import htclient

from ...yamlconf import Option

from ...validators.basic import valid_stripped_string_not_empty
from ...validators.basic import valid_bool
from ...validators.basic import valid_float_f01

from . import BaseUserGpioDriver
from . import GpioDriverOfflineError


# =====
class Plugin(BaseUserGpioDriver):  # pylint: disable=too-many-instance-attributes
    def __init__(  # pylint: disable=too-many-arguments
        self,
        instance_name: str,
        notifier: aiotools.AioNotifier,

        url: str,
        verify: bool,
        user: str,
        passwd: str,
        mac: str,
        switch_delay: float,
        timeout: float,
        state_poll: float,
    ) -> None:

        super().__init__(instance_name, notifier)

        self.__url = url
        self.__verify = verify
        self.__user = user
        self.__passwd = passwd
        self.__mac = mac
        self.__switch_delay = switch_delay
        self.__timeout = timeout
        self.__state_poll = state_poll

        self.__state: dict[str, (bool | None)] = {}

        self.__port_table: dict[str, dict[str, Any]] = {}
        self.__port_overrides: dict[str, dict[str, Any]] = {}

        self.__update_notifier = aiotools.AioNotifier()

        self.__http_session: (aiohttp.ClientSession | None) = None

        self.__csrf_token: (str | None) = None
        self.__id: (str | None) = None
        self.__api_url: str = f"{self.__url}/proxy/network/api/s/default"

    @classmethod
    def get_plugin_options(cls) -> dict[str, Option]:
        return {
            "url":          Option("",   type=valid_stripped_string_not_empty),
            "verify":       Option(True, type=valid_bool),
            "user":         Option(""),
            "passwd":       Option(""),
            "mac":          Option("",   type=valid_stripped_string_not_empty),
            "switch_delay": Option(1.0,  type=valid_float_f01),
            "state_poll":   Option(5.0,  type=valid_float_f01),
            "timeout":      Option(5.0,  type=valid_float_f01),
        }

    @classmethod
    def get_pin_validator(cls) -> Callable[[Any], Any]:
        return valid_stripped_string_not_empty

    def register_input(self, pin: str, debounce: float) -> None:
        _ = debounce
        self.__state[pin] = None

    def register_output(self, pin: str, initial: (bool | None)) -> None:
        _ = initial
        if pin.isnumeric():
            self.__state[pin] = None

    async def run(self) -> None:
        prev_state: (dict | None) = None
        while True:
            if (await self.__ensure_login()):
                try:
                    async with self.__ensure_http_session().get(
                        url=f"{self.__api_url}/stat/device/{self.__mac}",
                        headers=self.__make_headers(token=True, post_json=False),
                    ) as response:

                        self.__handle_response(response)

                        status = (await response.json())["data"][0]
                        if self.__id is None or self.__id != status["_id"]:
                            self.__id = status["_id"]

                        port_overrides = dict(map(
                            lambda port: (str(port["port_idx"]), port),
                            status["port_overrides"]))

                        for port_key, port in port_overrides.items():
                            self.__port_overrides[port_key] = port

                        port_table = dict(
                            map(lambda port: (str(port["port_idx"]), port),
                                list(filter(lambda p: p["port_poe"] is True,
                                            status["port_table"]))))

                        for port_key, port in port_table.items():
                            self.__port_table[port_key] = port

                        for pin in self.__state:
                            if pin is not None:
                                port = self.__port_table[pin]
                                self.__state[pin] = port["poe_mode"] == "auto"

                except Exception as err:
                    get_logger().error("Failed UNIFI bulk GET request: %s", tools.efmt(err))
                    self.__state = dict.fromkeys(self.__state, None)

                if self.__state != prev_state:
                    self._notifier.notify()
                    prev_state = self.__state

            await self.__update_notifier.wait(self.__state_poll)

    async def cleanup(self) -> None:
        if self.__http_session:
            await self.__http_session.close()
            self.__http_session = None
            self.__csrf_token = None

    async def read(self, pin: str) -> bool:
        if not pin.isnumeric():
            return False
        if not (await self.__ensure_login()):
            raise GpioDriverOfflineError(self)
        return self.__state[pin] is not None and bool(self.__state[pin])

    async def write(self, pin: str, state: bool) -> None:
        if not (await self.__ensure_login()):
            raise GpioDriverOfflineError(self)
        try:
            if pin.endswith(":cycle"):
                await self.__cycle_device(pin, state)
            else:
                await self.__set_device(pin, state)
        except Exception as err:
            get_logger().error("Failed UNIFI PUT request | pin: %s | Error: %s", pin, tools.efmt(err))
        await asyncio.sleep(self.__switch_delay)  # Slowdown
        self.__update_notifier.notify()

    # =====

    async def __cycle_device(self, pin: str, state: bool) -> None:
        if not state:
            return
        get_logger().info("Cycling device %s: port: %s", self.__mac, pin)
        async with self.__ensure_http_session().post(
            url=f"{self.__api_url}/cmd/devmgr",
            json={
                "cmd":      "power-cycle",
                "mac":      self.__mac,
                "port_idx": pin.split(":")[0],
            },
            headers=self.__make_headers(token=True, post_json=True),
        ) as response:
            self.__handle_response(response)

    async def __set_device(self, pin: str, state: bool) -> None:
        get_logger().info("Setting device %s: port: %s, state: %s", self.__mac, pin, state)

        port_overrides: list[dict[str, Any]] = []
        for po in self.__port_overrides.values():
            if str(po["port_idx"]) == pin:
                # Also modifies value in self.__port_overrides
                po["poe_mode"] = ("auto" if state else "off")
            port_overrides.append(po)

        async with self.__ensure_http_session().put(
            url=f"{self.__api_url}/rest/device/{self.__id}",
            json={"port_overrides": port_overrides},
            headers=self.__make_headers(token=True, post_json=True),
        ) as response:

            self.__handle_response(response)

        await asyncio.sleep(5)

        self.__port_table[pin]["poe_enable"] = state
        self.__port_table[pin]["poe_mode"] = ("auto" if state else "off")
        self.__state[pin] = state

    async def __ensure_login(self) -> bool:
        if self.__csrf_token is None:
            get_logger().info("Logging into Unifi")
            try:
                async with self.__ensure_http_session().post(
                    url=f"{self.__url}/api/auth/login",
                    json={
                        "username": self.__user,
                        "password": self.__passwd,
                    },
                    headers=self.__make_headers(token=False, post_json=True),
                ) as response:
                    self.__handle_response(response)
            except Exception as err:
                get_logger().error("Failed Unifi login request: %s", tools.efmt(err))
                return False
        return True

    def __make_headers(self, token: bool, post_json: bool) -> dict[str, str]:
        headers: dict[str, str] = {}
        if token:
            assert self.__csrf_token is not None
            headers["X-CSRF-TOKEN"] = self.__csrf_token
        if post_json:
            headers["Content-Type"] = "application/json;charset=UTF-8"
        return headers

    def __handle_response(self, response: aiohttp.ClientResponse) -> None:
        assert self.__http_session is not None
        if response.status == 401:
            get_logger().info("Unifi API request unauthorized, we will retry a login")
            self.__csrf_token = None
            self.__http_session.cookie_jar.clear()
        htclient.raise_not_200(response)
        if "X-CSRF-TOKEN" in response.headers:
            self.__csrf_token = response.headers["X-CSRF-TOKEN"]
        if response.cookies:
            self.__http_session.cookie_jar.update_cookies(response.cookies)

    def __ensure_http_session(self) -> aiohttp.ClientSession:
        if not self.__http_session:
            kwargs: dict = {
                "headers": {
                    "Accept":     "application/json",
                    "User-Agent": htclient.make_user_agent("KVMD"),
                },
                "cookie_jar": aiohttp.CookieJar(),
                "timeout":    aiohttp.ClientTimeout(total=self.__timeout),
            }
            if not self.__verify:
                kwargs["connector"] = aiohttp.TCPConnector(ssl=False)
            self.__http_session = aiohttp.ClientSession(**kwargs)
        return self.__http_session

    def __str__(self) -> str:
        return f"Unifi({self._instance_name})"

    __repr__ = __str__
