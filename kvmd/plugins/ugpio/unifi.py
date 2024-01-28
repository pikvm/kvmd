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
import functools
import json

from typing import Callable
from typing import Any

from multidict import CIMultiDict, CIMultiDictProxy

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

def set_poe_mode(pin: str, state: bool, port_override: dict[str, Any]) -> dict[str, Any]:
    port_idx = str(port_override["port_idx"])
    if port_idx == pin:
        port_override["poe_mode"] = "auto" if state else "off"
    return port_override


# pylint: disable=too-many-instance-attributes disable=too-many-arguments
class Plugin(BaseUserGpioDriver):
    def __init__(
        self,
        instance_name: str,
        notifier: aiotools.AioNotifier,

        url: str,
        verify: bool,
        user: str,
        passwd: str,
        mac: str,

        timeout: float,
        switch_delay: float,
        state_poll: float,
    ) -> None:

        super().__init__(instance_name, notifier)

        self.__url = url
        self.__user = user
        self.__passwd = passwd
        self.__mac = mac
        self.__state_poll = state_poll

        self.__switch_delay = switch_delay

        self.__initial: dict[str, (bool | None)] = {}

        self.__state: dict[str, (bool | None)] = {}

        self.__port_table: dict[str, dict[str, Any]] = {}
        self.__port_overrides: dict[str, dict[str, Any]] = {}

        self.__update_notifier = aiotools.AioNotifier()

        self.__http_session: aiohttp.ClientSession = self.__get_client_session(timeout, verify)

        self.__csrf_token: (str | None) = None
        self.__id: (str | None) = None
        self.__api_url: str = f"{self.__url}/proxy/network/api/s/default"

    @classmethod
    def get_plugin_options(cls) -> dict[str, Option]:
        return {
            "url":        Option("",   type=valid_stripped_string_not_empty),
            "verify":     Option(True, type=valid_bool),
            "user":       Option(""),
            "passwd":     Option(""),
            "mac":        Option("",   type=valid_stripped_string_not_empty),
            "state_poll": Option(5.0,  type=valid_float_f01),
            "timeout":    Option(5.0,  type=valid_float_f01),
            "switch_delay": Option(1.0,  type=valid_float_f01),
        }

    @classmethod
    def get_pin_validator(cls) -> Callable[[Any], Any]:
        return valid_stripped_string_not_empty

    def register_input(self, pin: str, debounce: float) -> None:
        _ = debounce
        self.__state[pin] = None

    def register_output(self, pin: str, initial: (bool | None)) -> None:
        if pin.isnumeric():
            self.__initial[pin] = initial
            self.__state[pin] = None

    def prepare(self) -> None:
        for (pin, state) in self.__initial.items():
            if pin.isnumeric():
                if state is not None:
                    self.__state[pin] = state

    async def run(self) -> None:
        prev_state: (dict | None) = None
        while True:
            try:
                if self.__csrf_token is None:
                    get_logger().info(
                        "Logging into UNIFI"
                    )
                    await self.login()
                async with self.__http_session.get(
                    url=f"{self.__api_url}/stat/device/{self.__mac}",
                    headers=self.__get_headers()
                ) as response:
                    htclient.raise_not_200(response)
                    self.__handle_headers(response.headers)

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

            except aiohttp.ClientResponseError as err:
                await self.__handle_client_response_error(err)
            except Exception as err:
                get_logger().error("Failed UNIFI bulk GET request: %s",
                                   tools.efmt(err))
                self.__state = dict.fromkeys(self.__state, None)

            if self.__state != prev_state:
                self._notifier.notify()
                prev_state = self.__state

            await self.__update_notifier.wait(self.__state_poll)

    async def cleanup(self) -> None:
        if self.__http_session:
            await self.__http_session.close()
            self.__http_session = aiohttp.ClientSession()
            self.__csrf_token = None

    async def read(self, pin: str) -> bool:
        if pin.isnumeric() is False:
            return False
        try:
            if self.__csrf_token is None:
                get_logger().info(
                    "Logging into UNIFI"
                )
                await self.login()
            return await self.__inner_read(pin)
        except aiohttp.ClientResponseError as err:
            await self.__handle_client_response_error(err)
        except Exception:
            raise GpioDriverOfflineError(self)
        return False

    async def write(self, pin: str, state: bool) -> None:
        try:
            if self.__csrf_token is None:
                get_logger().info(
                    "Logging into UNIFI"
                )
                await self.login()
            if pin.endswith(":cycle"):
                await self.__cycle_device(pin, state)
            else:
                await self.__inner_write(pin, state)
        except aiohttp.ClientResponseError as err:
            await self.__handle_client_response_error(err)
        except Exception as err:
            get_logger().error(
                "Failed UNIFI PUT request | pin : %s | Error: %s",
                pin,
                tools.efmt(err)
            )
        await asyncio.sleep(self.__switch_delay)  # Slowdown
        self.__update_notifier.notify()

    # =====

    def __get_client_session(self, timeout: float, verify: bool) -> aiohttp.ClientSession:
        kwargs: dict = {
            "headers": {
                "Accept": "application/json",
                "User-Agent": htclient.make_user_agent("KVMD"),
            },
            "timeout": aiohttp.ClientTimeout(total=timeout),
            "connector": aiohttp.TCPConnector(verify_ssl=verify),
            "cookie_jar": aiohttp.CookieJar(),
        }

        return aiohttp.ClientSession(**kwargs)

    async def __inner_read(self, pin: str) -> bool:
        return self.__state[pin] is not None and bool(self.__state[pin])

    async def __inner_write(self, pin: str, state: bool) -> None:
        await self.__set_device(pin, state)

    async def __cycle_device(self, pin: str, state: bool) -> None:
        if state is False:
            return
        get_logger().info("Cycling device %s: port: %s", self.__mac, pin)
        async with self.__http_session.post(
            url=f"{self.__api_url}/cmd/devmgr",
            json={
                "cmd": "power-cycle",
                "mac": self.__mac,
                "port_idx": pin.split(":")[0],
            },
            headers=self.__get_headers()
        ) as response:
            self.__handle_headers(response.headers)
            htclient.raise_not_200(response)

    async def __set_device(self, pin: str, state: bool) -> None:
        get_logger().info("Setting device %s: port: %s, state: %s", self.__mac, pin, state)

        port_overrides = map(
            functools.partial(set_poe_mode, pin, state),
            self.__port_overrides.values()
        )

        data = {
            "port_overrides": list(port_overrides)
        }

        get_logger().info("Posting content %s:\n%s\n", pin, json.dumps(data).encode("utf-8"))

        async with self.__http_session.put(
            url=f"{self.__api_url}/rest/device/{self.__id}",
            json=data,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json;charset=UTF-8",
                "X-CSRF-TOKEN": self.__csrf_token,
                "User-Agent": htclient.make_user_agent("KVMD"),
            }
        ) as response:
            htclient.raise_not_200(response)

            for header in response.headers:
                if header.upper() == "X-CSRF-TOKEN":
                    self.__csrf_token = response.headers[header]

            if response.cookies:
                self.__http_session.cookie_jar.update_cookies(
                    response.cookies
                )

        result = await asyncio.sleep(5, result=state)

        self.__port_table[pin]["poe_enable"] = result
        self.__port_table[pin]["poe_mode"] = "auto" if result else "off"
        self.__state[pin] = result

    def __get_headers(self,
                      extra: (dict | None) = None
                      ) -> CIMultiDictProxy[str]:
        kwargs: dict = {
            "Accept": "application/json",
            "X-CSRF-TOKEN": self.__csrf_token,
        }
        headers: CIMultiDict[str] = CIMultiDict(**kwargs)
        if extra is not None:
            headers.update(**extra)
        return CIMultiDictProxy(headers)

    def __handle_headers(self, response_headers: CIMultiDictProxy[str]) -> None:
        for header in response_headers:
            if header.upper() == "X-CSRF-TOKEN":
                self.__csrf_token = response_headers.get(header)

    async def __handle_client_response_error(self,
                                             err: aiohttp.ClientResponseError
                                             ) -> None:
        if err.status == 401:
            get_logger().info(
                "UNIFI API request unauthorized. Attempting to refresh session"
            )
            try:
                await self.login()
            except Exception as login_err:
                get_logger().error("Failed UNIFI login request: %s",
                                   tools.efmt(login_err))

    async def login(self) -> None:
        try:
            response = await self.__http_session.post(
                url=f"{self.__url}/api/auth/login",
                json={
                    "username": self.__user,
                    "password": self.__passwd
                },
                headers={
                    "Accept": "application/json",
                    "User-Agent": htclient.make_user_agent("KVMD"),
                    "Content-Type": "application/json;charset=UTF-8",
                }
            )

            htclient.raise_not_200(response)

            for header in response.headers:
                if header.upper() == "X-CSRF-TOKEN":
                    self.__csrf_token = response.headers[header]

            if response.cookies:
                self.__http_session.cookie_jar.update_cookies(
                    response.cookies
                )
        except Exception as err:
            get_logger().error(
                "Failed UNIFI login request: %s",
                tools.efmt(err)
            )

    def __str__(self) -> str:
        return f"UNIFI({self._instance_name})"

    __repr__ = __str__
