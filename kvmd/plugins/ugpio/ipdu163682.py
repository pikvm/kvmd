# KVMD Plugin for INtellinet 19" Intelligent 8-Port PDU Model 163682
# Communication based on API created by CodingBot under MIT Licence
# Plugin Components based on KMUD Tesmart-Plugin

### WARNING - WARNING - WARNING - WARNING - WARNING - WARNING - WARNING
#
#       from CodingRobot
#       I STRONGLY DISCOURAGE YOU FROM USING THIS PDU IN PRODUCTION.
#       IT'S SECURITY IS VIRTUALLY NON EXISTENT AND I FOUND MULTIPLE
#       EXPLOITABLE VULNERABILITIES JUST WHILE WRITING THIS API WRAPPER
# 
# ### WARNING - WARNING - WARNING - WARNING - WARNING - WARNING - WARNING

import asyncio
import functools

from typing import Callable
from typing import Any

from ...logging import get_logger

from ... import tools
from ... import aiotools

from ...yamlconf import Option

from ...validators.basic import valid_number
from ...validators.basic import valid_int_f0
from ...validators.net import valid_ip_or_host
from ...validators.basic import valid_stripped_string

from . import BaseUserGpioDriver
from . import GpioDriverOfflineError

import requests
from urllib.parse import urlunsplit
from lxml import etree as et

# =====
class Plugin(BaseUserGpioDriver): # pylint: disable=too-many-instance-attributes
    def __init__(
        self,
        instance_name: str,
        notifier: aiotools.AioNotifier,

        host: str,

        username: str,
        password: str,

        switch_delay: int,

    )  -> None:

        super().__init__(instance_name, notifier)

        self.__host = host

        self.__username = username
        self.__password = password

        self.__initials: dict[int, (bool | None)] = {}
        self.__outlet_states: dict[int, (bool | None)] = {}
        self.__temp = None
        self.__current = None
        self.__humidity = None
        self.__update_notifier = aiotools.AioNotifier()
        self.__outlet_ondelay: dict[int, (int | None)] = {}
        self.__outlet_offdelay: dict[int, (int | None)] = {}

    @classmethod
    def get_plugin_options(cls) -> dict: 
        return {
            "host":             Option("",    type=valid_ip_or_host, if_empty=""),

            "username":         Option("admin",   type=valid_stripped_string),
            "password":         Option("admin",   type=valid_stripped_string),
            "switch_delay":     Option(1,   type=valid_int_f0),
        }

    @classmethod
    def get_pin_validator(cls) -> Callable[[Any], Any]:
        return functools.partial(valid_number, min=0, max=7, name="IPDU outlet")

    def register_output(self, outlet: str, initial: (bool | None)) -> None:
        self.__initials[int(outlet)] = initial

    def register_input(self, outlet: str, debounce: float) -> None:
        self.__outlet_states[int(outlet)] = False

    def prepare(self) -> None:
        self.__create_ipdu()
        # save original values of on/off-delays
        self.__save_config()
        ondelays: dict[ int, (int | None)] ={}
        offdelays: dict[ int, (int | None)] ={}
        for (outlet, state) in self.__initials.items():
            # pre-set dicts to the specified switch-on/off-delay
            ondelays[int(outlet)] = self.__switch_delay
            offdelays[int(outlet)] = self.__switch_delay
        self.__set_config(ondelays, offdelays)
        for (outlet, state) in self.__initials.items():
            if state is not None:
                self.__control_outlet(outlet, state)
        self.__ipdu_status()

    async def run(self) -> None:
        # try to get status from ipdu, ignore exceptions to be able to continue
        try:
            await self.__ipdu_status()
        except Exception:
            pass
        await self.__update_notifier.notify()

    async def cleanup(self) -> None:
        # reset configuration of on/off-delays to original values
        self.__set_config(self.__outlet_ondelay, self.__outlet_offdelay)

    async def read(self, outlet: str) -> bool:
        # read status from ipdu
        try:
            self.__ipdu_status()
        except Exception as err:
            get_logger(0).error("Can't connect to Intellinet PDU [%s] to get status: %s", self.__host, tools.efmt(err))
            raise GpioDriverOfflineError(self)
        await self.__update_notifier.notify()
        return self.__outlet_states[int(outlet)]
    
    async def write(self, outlet: str, state: bool) -> None:
              
        assert 0 <= int(outlet) <= 7
        get_logger(0).info("On IPDU {%s]: Controlling outlet %d: state %d", self.__host, int(outlet), state)
        await self.__control_outlet(int(outlet),state)
        await asyncio.sleep(self.__switch_delay + 1) #allow some time to complete execution on IPDU
        await self.__ipdu_status()
        await self.__update_notifier.notify()
        

    # =====

    def __create_ipdu(self):
        self.__schema = "http"
        self.__charset = "gb2312"
        self.__auth = self.__auth((self.__username,self.__password))
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
        
    def __auth(self, creds):
        # from codingrobot:
        #    Don't even bother... The PDU only requests a http auth on the / page.
        #    All other pages/endpoints (including settings updates und file uploads)
        #    are unprotected.
        try:
            return requests.auth.HTTPBasicAuth(*creds)
        except Exception as err:
            get_logger(0).error("Can't connect to Intellinet PDU [%s] for auth: %s", self.__host, tools.efmt(err))
            raise GpioDriverOfflineError(self)
    
    def __control_outlet(self, outlet, state):
        endpoint = self.__endpoints["outlet"]
        translation_table = {True: 1, False: 0, "power_cycle_off_on": 2}
        outlet_state = {"outlet{}".format(outlet): 1}
        outlet_state["op"] = translation_table[state]
        outlet_state["submit"] = "Anwenden"
        url = urlunsplit([self.__schema, self.__host, endpoint, None, None])
        try:
            resp = requests.get(url, auth=self.__auth, params=outlet_state)
            resp.raise_for_status()
        except Exception as err:
            get_logger(0).error("Can't connect to Intelligent PDU [%s] for controlling outlet: %s", self.__host,tools.efmt(err))
            raise GpioDriverOfflineError(self)
        else:
            self.__ipdu_status()

    def __ipdu_status(self):
        endpoint = self.__endpoints["status"]
        url = urlunsplit([self.__schema, self.__host, endpoint, None, None])
        try:
            resp = requests.get(url, auth=self.__auth, params=None)
        except Exception as err:
            get_logger(0).error("Can't connect to Intelligent PDU [%s] when getting status: %s", self.__host,tools.efmt(err))
            raise GpioDriverOfflineError
        else:
            self.__decodeparse_resp(resp)

    def __decodeparse_resp(self, resp):
        # decode
        assert resp
        decoded = resp.content.decode(self.__charset)
        # parse
        if "html" in decoded.lower():
            parser = et.HTML
        else:
            parser = et.XML

        res = parser(decoded)
        # save information
        self.__current = res.find("cur0").text
        self.__temp = res.find("tempBan").text
        self.__humidity = res.find("humBan").text
        translation_table = {"on": 1, "off": 0, "power_cycle_off_on": 2}
        for (outlet, state) in self.__outlet_states.items():
            statestr = res.find("outletState{}".format(outlet)).text
            self.__outlet_states[int(outlet)] = translation_table[statestr]
        get_logger(0).info("IPDU device (%s) state: current: %s A; temp: %s C; humidity: %s", self.__host, self.__current, self.__temp, self.__humidity)
    
    def __save_config(self):
        endpoint = self.__endpoints["config_pdu"]
        url = urlunsplit([self.__schema, self.__host, endpoint, None, None])
        try:
            resp = requests.get(url, auth=self.__auth, params=None)
        except Exception as err:
            get_logger(0).error("Can't connect to Intellinet PDU [%s] for saving configuration: %s", self.__host, tools.efmt(err))
            raise GpioDriverOfflineError(self)
        else:
            decoded = resp.content.decode(self.__charset)
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
    
    def __set_config(self, ondelay, offdelay):
        endpoint = self.__endpoints["config_pdu"]
        setting = {}
        for (outlet, delay) in ondelay.items():
            setting["ondly"+str(outlet)] = delay
        for (outlet, delay) in offdelay.items():
            setting["ofdly"+str(outlet)] = delay
        url = urlunsplit([self.__schema, self.__host, endpoint, None, None])
        headers = {"Content-type": "application/x-www-form-urlencoded"}
        try:
            requests.post(url, auth=self.__auth,data=setting, headers=headers)
        except Exception as err:
            get_logger(0).error("Can't connect to Intellinet PDU [%s] for setting configuration: %s", self.__host, tools.efmt(err))
            raise GpioDriverOfflineError(self)

    # =====

    def __str__(self) -> str:
        return f"IPDU_163682({self._instance_name})"

    __repr__ = __str__
    