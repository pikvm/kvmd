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
import datetime

from typing import AsyncGenerator

from ....logging import get_logger

from .... import env
from .... import aiotools

from .base import BaseInfoSubmanager


# =====
class UptimeInfoSubmanager(BaseInfoSubmanager):
    __RESOLUTION = 5  # Seconds

    def __init__(self) -> None:
        self.__notifier = aiotools.AioNotifier()

    async def get_state(self) -> dict:
        total = await self.__read_uptime_file()
        uptime = datetime.timedelta(seconds=total)
        days = uptime.days
        (hours, rem) = divmod(uptime.seconds, 3600)
        (mins, secs) = divmod(rem, 60)
        return {
            "total": total,
            "parts": {
                "days": days,
                "hours": hours,
                "minutes": mins,
                "seconds": secs,
            },
        }

    async def trigger_state(self) -> None:
        self.__notifier.notify()

    async def poll_state(self) -> AsyncGenerator[(dict | None), None]:
        while True:
            await self.__notifier.wait(timeout=self.__RESOLUTION)
            yield (await self.get_state())

    # =====

    async def __read_uptime_file(self) -> int:
        path = os.path.join(f"{env.PROCFS_PREFIX}/proc/uptime")
        try:
            return int(float((await aiotools.read_file(path)).split()[0]))
        except Exception as ex:
            get_logger(0).error("Can't read system uptime: %s", ex)
        return 0
