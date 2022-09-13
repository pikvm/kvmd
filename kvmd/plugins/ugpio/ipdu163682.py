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


import functools

from typing import Callable
from typing import Any

from ...logging import get_logger

from ... import tools
from ... import aiotools

from ...yamlconf import Option

from ...validators.basic import valid_number
from ...validators.basic import valid_float_f01
from ...validators.net import valid_ip_or_host
from ...validators.basic import valid_stripped_string

from . import BaseUserGpioDriver
from . import GpioDriverOfflineError, GpioOperationError

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

    #    timeout: float,
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

    @classmethod
    def get_plugin_options(cls) -> dict: 
        return {
            "host":        Option("",    type=valid_ip_or_host, if_empty=""),

            "username":     Option("admin",   type=valid_stripped_string),
            "password":     Option("admin",   type=valid_stripped_string),
        }

    @classmethod
    def get_pin_validator(cls) -> Callable[[Any], Any]:
        return functools.partial(valid_number, min=0, max=7, name="IPDU outlet")

    def register_output(self, outlet: str, initial: (bool | None)) -> None:
        self.__initials[int(outlet)] = initial
        self.__outlet_states[int(outlet)] = False

    def prepare(self) -> None:
        self.__ipdu = self.__create_ipdu()
        for (outlet, state) in self.__initials.items():
            if state is not None:
                self.__control_outlet(outlet, state)
                self.__get_outlet_states()

    async def run(self) -> None:
        
        await self.__ipdu_status()
        await self.__update_notifier.notify()

    async def cleanup(self) -> None:
        await self.__close_device()

    async def read(self, outlet: str) -> bool:
        return self.__outlet_states[int(outlet)]
    
    async def write(self, outlet: str, state: bool) -> None:
        
        # Intelligent PDU uses 1-based numbering
        channel = int(outlet) + 1
        assert 1 <= channel <= 8
        
        await self.__control_outlet(channel,state)
        await self.__ipdu_status()
        await self.__update_notifier.notify()
        

    # =====

    async def __close_device(self) -> None:
        self.__ipdu = None

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
        except requests.exceptions.ConnectionError as err:
            get_logger(0).error("Can't connect to Intellinet PDU [%s]: %s", self.__host, tools.efmt(err))
        #    raise GpioDriverOfflineError(self)
            pass
        except requests.exceptions.RequestException as err:
            get_logger(0).error("Error with Intellinet PDU [%s]: %s", self.__host, tools.efmt(err))
        #    raise GpioDriverOfflineError(self)
            pass
        # TODO exception for faild auth
    
    def __control_outlet(self, outlet, state):
        endpoint = self.__endpoints["outlet"]
        translation_table = {"on": 1, "off": 0, "power_cycle_off_on": 2}
        outlet_state = {"outlet{}".format(outlet): 1}
        outlet_state["op"] = translation_table[state]
        outlet_state["submit"] = "Anwenden"
        url = urlunsplit([self.__schema, self.__host, endpoint, None, None])
        try:
            resp = requests.get(url, auth=self.__auth, params=outlet_state)
            resp.raise_for_status()
        except requests.exceptions.ConnectionError as err:
            get_logger(0).error("Can't connect to Intelligent PDU [%s] for controlling outlet: %s", self.__host,tools.efmt(err))
         #   raise GpioDriverOfflineError
            pass
        except requests.exceptions.HTTPError as err:
            get_logger(0).error("error when controlling Outlet on to Intelligent PDU [%s]: %s", self.__host,tools.efmt(err))
        #    raise GpioOperationError
            pass
        except requests.exceptions.RequestException as err:
            get_logger(0).error("Error with Intellinet PDU [%s]: %s", self.__host, tools.efmt(err))
       #     raise GpioDriverOfflineError(self)
            pass
        else:
            self.__ipdu_status()

    def __ipdu_status(self):
        endpoint = self.__endpoints["status"]
        url = urlunsplit([self.__schema, self.__host, endpoint, None, None])
        try:
            resp = requests.get(url, auth=self.__auth, params=None)
            resp.raise_for_status()
        except requests.exceptions.ConnectionError as err:
            get_logger(0).error("Can't connect to Intelligent PDU [%s] for controlling outlet: %s", self.__host,tools.efmt(err))
      #      raise GpioDriverOfflineError
            pass
        except requests.exceptions.HTTPError as err:
            get_logger(0).error("Error when getting status from Intelligent PDU [%s]: %s", self.__host,tools.efmt(err))
    #        raise GpioOperationError
            pass
        except requests.exceptions.RequestException as err:
            get_logger(0).error("Error with Intellinet PDU [%s]: %s", self.__host, tools.efmt(err))
     #       raise GpioDriverOfflineError(self)
            pass
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
            statestr = res.find("outletState{}".format(outlet+1))
            state = translation_table[statestr]
    
    # =====

    def __str__(self) -> str:
        return f"IPDU_163682({self._instance_name})"

    __repr__ = __str__
    