# KVMD Plugin for Intellinet 19" Intelligent 8-Port PDU Model 163682
# Communication based on API created by CodingBot under MIT Licence
# Plugin Components based on KVMD ANELPWR-Plugin

### WARNING - WARNING - WARNING - WARNING - WARNING - WARNING - WARNING
#
#       from CodingRobot
#       I STRONGLY DISCOURAGE YOU FROM USING THIS PDU IN PRODUCTION.
#       IT'S SECURITY IS VIRTUALLY NON EXISTENT AND I FOUND MULTIPLE
#       EXPLOITABLE VULNERABILITIES JUST WHILE WRITING THIS API WRAPPER
#
### WARNING - WARNING - WARNING - WARNING - WARNING - WARNING - WARNING

import asyncio
import functools

from typing import Callable
from typing import Any

import aiohttp

from ...logging import get_logger

from ... import tools
from ... import aiotools
from ... import htclient

from ...yamlconf import Option

from ...validators.basic import valid_number
from ...validators.basic import valid_int_f0
from ...validators.net import valid_ip_or_host
from ...validators.basic import valid_stripped_string
from ...validators.basic import valid_float_f01

from . import BaseUserGpioDriver
from . import GpioDriverOfflineError

from urllib.parse import urlunsplit
from lxml import etree as et


# =====
class Plugin(BaseUserGpioDriver):  # pylint: disable=too-many-instance-attributes
    def __init__(
        self,
        instance_name: str,
        notifier: aiotools.AioNotifier,

        host: str,

        username: str,
        password: str,

        switch_delay: int,
        state_poll: float,
        timeout: float,

    ) -> None:

        super().__init__(instance_name, notifier)

        self.__host = host

        self.__username = username
        self.__password = password

        self.__switch_delay = switch_delay
        self.__state_poll = state_poll
        self.__timeout = timeout

        self.__initials: dict[int, (bool | None)] = {}
        self.__outlet_states: dict[int, (bool | None)] = {}
        self.__temp = None
        self.__current = None
        self.__humidity = None
        self.__update_notifier = aiotools.AioNotifier()
        self.__outlet_ondelay: dict[int, (int | None)] = {}
        self.__outlet_offdelay: dict[int, (int | None)] = {}

        self.__http_session: (aiohttp.ClientSession | None) = None

    @classmethod
    def get_plugin_options(cls) -> dict: 
        return {
            "host":             Option("",    type=valid_ip_or_host, if_empty=""),

            "username":         Option("admin",   type=valid_stripped_string),
            "password":         Option("admin",   type=valid_stripped_string),
            "switch_delay":     Option(1,   type=valid_int_f0),
            "state_poll":       Option(5.0, type=valid_float_f01),
            "timeout":          Option(5.0, type=valid_float_f01),
        }

    @classmethod
    def get_pin_validator(cls) -> Callable[[Any], Any]:
        return functools.partial(valid_number, min=0, max=7, name="IPDU outlet")

    def register_output(self, pin: str, initial: (bool | None)) -> None:
        self.__initials[int(pin)] = initial
        self.__outlet_states[int(pin)] = None

    def register_input(self, pin: str, debounce: float) -> None:
        _ = debounce
        self.__outlet_states[int(pin)] = False

    def prepare(self) -> None:
        async def inner_prepare() -> None:
            self.__create_ipdu()
            session = self.__ensure_http_session()
            endpoint = self.__endpoints["status"]
            url = urlunsplit([self.__schema, self.__host, endpoint, None, None])
            try:
                async with session.get(url, params=None) as resp:
                    htclient.raise_not_200(resp)
                    await resp.text()
            except Exception as err:
                get_logger().error("Can't connect to Intelligent PDU [%s] when getting status: %s", self.__host, tools.efmt(err))
            else:
                # save original values of on/off-delays
                await self.__save_config()
                ondelays: dict[int, (int | None)] = {}
                offdelays: dict[int, (int | None)] = {}
                for (outlet, state) in self.__initials.items():
                    # pre-set dicts to the specified switch-on/off-delay
                    ondelays[int(outlet)] = self.__switch_delay
                    offdelays[int(outlet)] = self.__switch_delay
                await self.__set_config(ondelays, offdelays)
                for (outlet, state) in self.__initials.items():
                    if state is not None:
                        await self.__control_outlet(outlet, state)
                await self.__ipdu_status()
        aiotools.run_sync(inner_prepare())

    async def run(self) -> None:
        prev_state: (dict | None) = None
        while True:
            await self.__ipdu_status()
            if self.__outlet_states != prev_state:
                self._notifier.notify()
                prev_state = self.__outlet_states
            await self.__update_notifier.wait(self.__state_poll)

    async def cleanup(self) -> None:
        if self.__http_session:
            # reset configuration of on/off-delays to original values
            await self.__set_config(self.__outlet_ondelay, self.__outlet_offdelay)
            await self.__http_session.close()
            self.__http_session = None


    async def read(self, pin: str) -> bool:
        # read status from ipdu
        await self.__ipdu_status()
        self.__update_notifier.notify()
        if self.__outlet_states[int(pin)] is None:
           raise GpioDriverOfflineError(self)
        return self.__outlet_states[int(pin)]

    async def write(self, pin: str, state: bool) -> None:
        assert 0 <= int(pin) <= 7
        get_logger().info("On IPDU {%s]: Controlling outlet %d: state %d", self.__host, int(pin), state)
        await self.__control_outlet(int(pin), state)
        await asyncio.sleep(self.__switch_delay)  # allow some time to complete execution on IPDU
        await self.__ipdu_status()
        self.__update_notifier.notify()
 
    # =====
    def __create_ipdu(self):
        self.__schema = "http"
        self.__charset = "gb2312"
        self.__endpoints = {
            # Information
            "status": "status.xml",
            "pdu": "info_PDU.htm",
            "system": "info_system.htm",
            # Control
            "outlet": "control_outlet.htm",
            # Config
            "config_pdu": "config_PDU.htm",
            "thresholds": "config_threshold.htm",
            "users": "config_user.htm",
            "network": "config_network.htm",
        }

    def __ensure_http_session(self) -> aiohttp.ClientSession:
        if not self.__http_session:
            kwargs: dict = {
                "headers": {
                    "User-Agent": htclient.make_user_agent("KVMD"),
                },
                "timeout": aiohttp.ClientTimeout(total=self.__timeout),
            }
            if self.__username:
                kwargs["auth"] = aiohttp.BasicAuth(self.__username, self.__password)
                kwargs["connector"] = aiohttp.TCPConnector(ssl=False)
            self.__http_session = aiohttp.ClientSession(**kwargs)
        return self.__http_session

    async def __control_outlet(self, outlet, state):
        session = self.__ensure_http_session()
        endpoint = self.__endpoints["outlet"]
        translation_table = {True: 0, False: 1}
        outlet_state = {"outlet{}".format(outlet): 1}
        outlet_state["op"] = translation_table[state]
        outlet_state["submit"] = "Anwenden"
        url = urlunsplit([self.__schema, self.__host, endpoint, None, None])
        try:
            async with session.get(url, params=outlet_state) as resp:
                htclient.raise_not_200(resp)
        except Exception as err:
            get_logger().error("Can't connect to Intelligent PDU [%s] for controlling outlet: %s", self.__host, tools.efmt(err))
            raise GpioDriverOfflineError(self)
        await self.__ipdu_status()
        self.__update_notifier.notify()

    async def __ipdu_status(self):
        session = self.__ensure_http_session()
        endpoint = self.__endpoints["status"]
        url = urlunsplit([self.__schema, self.__host, endpoint, None, None])
        try:
            async with session.get(url, params=None) as resp:
                htclient.raise_not_200(resp)
                decoded = await resp.text(encoding=self.__charset)
        except Exception as err:
            get_logger().error("Can't connect to Intelligent PDU [%s] when getting status: %s", self.__host, tools.efmt(err))
        else:
            self.__parse_resp(decoded)

    def __parse_resp(self, resp):
        assert resp
        # parse
        if "html" in resp.lower():
            parser = et.HTML
        else:
            parser = et.XML

        res = parser(resp)
        # save information
        self.__current = res.find("cur0").text
        self.__temp = res.find("tempBan").text
        self.__humidity = res.find("humBan").text
        translation_table = {"on": 1, "off": 0, "power_cycle_off_on": 2}
        for (outlet, state) in self.__outlet_states.items():
            statestr = res.find("outletStat{}".format(outlet)).text
            self.__outlet_states[int(outlet)] = translation_table[statestr]
        get_logger().info("IPDU device (%s) state: current: %s A; temp: %s C; humidity: %s", self.__host, self.__current, self.__temp, self.__humidity)

    async def __save_config(self):
        session = self.__ensure_http_session()
        endpoint = self.__endpoints["config_pdu"]
        url = urlunsplit([self.__schema, self.__host, endpoint, None, None])
        try:
            async with session.get(url, params=None) as resp:
                htclient.raise_not_200(resp)
                decoded = (await resp.text(encoding=self.__charset))
        except Exception as err:
            get_logger().error("Can't connect to Intellinet PDU [%s] for saving configuration: %s", self.__host, tools.efmt(err))
            raise GpioDriverOfflineError(self)
        if "html" in decoded.lower():
            parser = et.HTML
        else:
            parser = et.XML

        res = parser(decoded)
        xpath_input_field_values = ".//td/input/@value"
        xpath_input_fields = ".//tr[td/input/@value]"
        for idx, outlet in enumerate(res.xpath(xpath_input_fields)):
            values = outlet.xpath(xpath_input_field_values)
            self.__outlet_ondelay["outlet{}".format(idx)] = int(values[1])
            self.__outlet_offdelay["outlet{}".format(idx)] = int(values[2])

    async def __set_config(self, ondelay, offdelay):
        session = self.__ensure_http_session()
        endpoint = self.__endpoints["config_pdu"]
        setting = {}
        for (outlet, delay) in ondelay.items():
            setting["ondly" + str(outlet)] = delay
        for (outlet, delay) in offdelay.items():
            setting["ofdly" + str(outlet)] = delay
        url = urlunsplit([self.__schema, self.__host, endpoint, None, None])
        headers = {"Content-type": "application/x-www-form-urlencoded"}
        try:
            async with session.post(url, data=setting, headers=headers) as resp:
                htclient.raise_not_200(resp)
        except Exception as err:
            get_logger().error("Can't connect to Intellinet PDU [%s] for setting configuration: %s", self.__host, tools.efmt(err))
            raise GpioDriverOfflineError(self)

    # =====

    def __str__(self) -> str:
        return f"IPDU_163682({self._instance_name})"

    __repr__ = __str__

