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
import subprocess
import dataclasses

from aiohttp.web import Request
from aiohttp.web import Response
from aiohttp.web import WebSocketResponse

from ...logging import get_logger

from ... import tools
from ... import aiotools
from ... import aioproc

from ...htserver import exposed_http
from ...htserver import exposed_ws
from ...htserver import make_json_response
from ...htserver import WsSession
from ...htserver import HttpServer

from ...nbd import NbdController


# =====
class NbdServer(HttpServer):
    __EV_REMOTES = "remotes"
    __EV_NBD = "nbd"

    def __init__(
        self,
        device_path: str,
        disconnect_cmd: list[str],
    ) -> None:

        super().__init__()

        self.__device_path = device_path
        self.__disconnect_cmd = disconnect_cmd

        self.__ctl = NbdController(device_path)

    # ===== HTTP

    @exposed_http("GET", "/state")
    async def __state_handler(self, _: Request) -> Response:
        return make_json_response(dataclasses.asdict(self.__ctl.get_state()))

    @exposed_http("GET", "/remotes")
    async def __remotes_handler(self, _: Request) -> Response:
        return make_json_response(self.__ctl.get_remotes())

    @exposed_http("POST", "/bind")
    async def __bind_handler(self, req: Request) -> Response:
        await self.__ctl.bind(**dict(req.query))
        return make_json_response({})

    @exposed_http("POST", "/unbind")
    async def __unbind_handler(self, _: Request) -> Response:
        self.__ctl.unbind()
        return make_json_response({})

    # ===== WEBSOCKET

    @exposed_http("GET", "/ws")
    async def __ws_handler(self, req: Request) -> WebSocketResponse:
        async with self._ws_session(req) as ws:
            await ws.send_event("loop", {})
            await ws.send_event(self.__EV_REMOTES, self.__ctl.get_remotes())
            await ws.send_event(self.__EV_NBD, dataclasses.asdict(self.__ctl.get_state()))
            return (await self._ws_loop(ws))

    @exposed_ws("ping")
    async def __ws_ping_handler(self, ws: WsSession, _: dict) -> None:
        await ws.send_event("pong", {})

    # ===== SYSTEM STUFF

    async def _init_app(self) -> None:
        await self.__force_disconnect()
        aiotools.create_deadly_task("Controller", self.__controller())
        self._add_exposed(self)

    async def _on_shutdown(self) -> None:
        logger = get_logger(0)
        logger.info("Stopping system tasks ...")
        await aiotools.stop_all_deadly_tasks()
        logger.info("On-Shutdown complete")

    async def _on_cleanup(self) -> None:
        logger = get_logger(0)
        await self.__force_disconnect()
        logger.info("On-Cleanup complete")

    # ===== SYSTEM TASKS

    async def __controller(self) -> None:
        logger = get_logger(0)
        async for (event, state) in self.__ctl.poll_state():
            logger.info("NBD-EVENT: %s", event)
            await self._broadcast_ws_event(self.__EV_NBD, dataclasses.asdict(state))

    async def __force_disconnect(self) -> bool:
        logger = get_logger()
        cmd = [
            part.format(device=os.path.realpath(self.__device_path))
            for part in self.__disconnect_cmd
        ]
        logger.info("Forced disconnecting NBD %s: %s", self.__device_path, tools.cmdfmt(cmd))
        try:
            proc = await aioproc.log_process(cmd, logger)
            if proc.returncode != 0:
                assert proc.returncode is not None
                raise subprocess.CalledProcessError(proc.returncode, cmd)
        except Exception as ex:
            logger.error("Can't forcibly disconnect NBD: %s", tools.efmt(ex))
            return False
        return True
