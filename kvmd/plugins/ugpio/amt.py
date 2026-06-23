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
import functools

from typing import Final
from typing import Callable
from typing import Any

from ...logging import get_logger

from ... import tools
from ... import aiotools
from ... import aioproc

from ...yamlconf import Section
from ...yamlconf import Option

from ...validators import check_string_in_list
from ...validators.basic import valid_float_f01
from ...validators.basic import valid_bool
from ...validators.net import valid_ip_or_host
from ...validators.os import valid_command

from . import GpioDriverOfflineError
from . import BaseUserGpioDriver


# =====
_OUTPUTS = {
    "1": "poweron",
    "2": "poweroff",
    "3": "powercycle",
    "4": "reset",
    "5": "sleep",
    "6": "hibernate",
}


# =====
class Plugin(BaseUserGpioDriver):  # pylint: disable=too-many-instance-attributes
    def __init__(  # pylint: disable=super-init-not-called
        self,
        instance_name: str,
        notifier: aiotools.AioNotifier,
        c: Section,
    ) -> None:

        super().__init__(instance_name, notifier, c)

        self.__host:   Final[str] = c.host
        self.__user:   Final[str] = c.user
        self.__passwd: Final[str] = c.passwd
        self.__tls:    Final[bool] = c.tls

        self.__passwd_env: Final[str]       = c.passwd_env
        self.__cmd:        Final[list[str]] = c.cmd

        self.__state_poll: Final[float] = c.state_poll

        self.__online = False
        self.__power = False

    @classmethod
    def get_plugin_options(cls) -> dict:
        return {
            "host":   Option("",  type=valid_ip_or_host),
            "user":   Option(""),
            "passwd": Option(""),
            "tls":    Option(True, type=valid_bool),

            "passwd_env": Option("AMT_PASSWORD"),
            "cmd": Option([
                "/usr/local/bin/meshcmd",
                "amtpower",
                "{action_opt}",
                "--host", "{host}",
                "--user", "{user}",
                "--pass", "{passwd}",
                "{tls_opt}",
            ], type=valid_command),

            "state_poll": Option(1.0, type=valid_float_f01),
        }

    @classmethod
    def get_pin_validator(cls) -> Callable[[Any], Any]:
        actions = ["0", *_OUTPUTS, "status", *_OUTPUTS.values()]
        return (lambda arg: check_string_in_list(arg, "Current power state", actions))

    def register_input(self, pin: str, debounce: float) -> None:
        _ = debounce
        if pin not in ["0", "status"]:
            raise RuntimeError(f"Unsupported mode 'input' for pin={pin} on {self}")

    def register_output(self, pin: str, initial: (bool | None)) -> None:
        _ = initial
        if pin not in [*_OUTPUTS, *_OUTPUTS.values()]:
            raise RuntimeError(f"Unsupported mode 'output' for pin={pin} on {self}")

    async def prepare(self) -> None:
        get_logger(0).info("Probing driver %s on %s ...", self, self.__host)

    async def run(self) -> None:
        prev = (False, False)
        while True:
            await self.__update_power()
            new = (self.__online, self.__power)
            if new != prev:
                self._notifier.notify()
                prev = new
            await asyncio.sleep(self.__state_poll)

    async def read(self, pin: str) -> bool:
        if not self.__online:
            raise GpioDriverOfflineError(self)
        if pin == "0":
            return self.__power
        return False

    async def write(self, pin: str, state: bool) -> None:
        if not self.__online:
            raise GpioDriverOfflineError(self)
        if not state:
            return
        action = (_OUTPUTS[pin] if pin.isdigit() else pin)
        try:
            proc = await aioproc.log_process(**self.__make_meshcmd_kwargs(action), logger=get_logger(0), prefix=str(self))
            if proc.returncode != 0:
                raise RuntimeError(f"meshcmd error: retcode={proc.returncode}")
        except Exception as ex:
            get_logger(0).error("Can't send AMT power-%s request to %s: %s",
                                action, self.__host, tools.efmt(ex))
            raise GpioDriverOfflineError(self)

    # =====

    async def __update_power(self) -> None:
        try:
            (proc, text) = await aioproc.read_process(**self.__make_meshcmd_kwargs("status"))
            if proc.returncode != 0:
                raise RuntimeError(f"meshcmd error: retcode={proc.returncode}")
            stripped = text.strip()
            if stripped.startswith("Current power state: "):
                self.__power = (stripped == "Current power state: Power on")
                self.__online = True
                return
            if stripped.startswith("Error"):
                raise RuntimeError(f"Error meshcmd response: {text}")
            raise RuntimeError(f"Invalid meshcmd response: {text}")
        except Exception as ex:
            get_logger(0).error("Can't fetch AMT power status from %s: %s",
                                self.__host, tools.efmt(ex))
            self.__power = False
            self.__online = False

    @functools.lru_cache()
    def __make_meshcmd_kwargs(self, action: str) -> dict:
        if action == "status":
            action_opt = ""
        else:
            action_opt = f"--{action}"
        if self.__tls:
            tls_opt = "--tls"
        else:
            tls_opt = ""
        return {
            "cmd": [
                part.format(
                    host=self.__host,
                    user=self.__user,
                    passwd=self.__passwd,
                    action_opt=action_opt,
                    tls_opt=tls_opt,
                )
                for part in self.__cmd
            ],
            "env": (
                {self.__passwd_env: self.__passwd}
                if self.__passwd and self.__passwd_env
                else None
            ),
        }

    def __str__(self) -> str:
        return f"AMT({self._instance_name})"

    __repr__ = __str__
