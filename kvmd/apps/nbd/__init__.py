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
import signal
import argparse

from ...logging import get_logger

from ... import aiotools

from ...nbd import NbdServer
from ...nbd.types import NbdStopEvent

from .._logging import init_logging


# =====
async def _async_main(device_path: str, url: str) -> None:
    server = NbdServer(device_path)

    async def poller() -> None:
        logger = get_logger(0)
        async for event in server.poll():
            logger.info("NBD-EVENT: %s", event)
            if isinstance(event, NbdStopEvent):
                break

    task = asyncio.create_task(poller())

    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGINT, server.unbind)
    loop.add_signal_handler(signal.SIGTERM, server.unbind)

    await server.bind(url)
    await task


def main() -> None:
    init_logging(True)

    parser = argparse.ArgumentParser()
    parser.add_argument("-d", "--device", default="/dev/nbd0")
    parser.add_argument("-u", "--url", required=True)
    opts = parser.parse_args()

    aiotools.run(_async_main(opts.device, opts.url))
