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
import signal
import asyncio
import asyncio.subprocess
import argparse
import contextlib
import copy
import dataclasses
import termios

from typing import Final

import aiohttp

from ...logging import get_logger

from ... import aiotools
from ... import aioproc
from ... import htclient
from ... import htserver

from .. import init


# =====
if not hasattr(termios, "_POSIX_VDISABLE"):
    raise RuntimeError("termios._POSIX_VDISABLE is not available")


# =====
@dataclasses.dataclass
class _Termios:
    """
    Fields of struct termios, in the order of the pseudo-tuple returned by termios.tcgetattr()
    """

    iflag:  int  # noqa: vulture-ignore
    oflag:  int  # noqa: vulture-ignore
    cflag:  int  # noqa: vulture-ignore
    lflag:  int  # noqa: vulture-ignore
    ispeed: int  # noqa: vulture-ignore
    ospeed: int  # noqa: vulture-ignore
    cc:     list[int]

    def fields(self) -> list[int | list[int]]:
        # shallow equivalent of dataclasses.astuple()
        return [
            getattr(self, field.name)
            for field in dataclasses.fields(self)
        ]


# =====
_KBD_SIGNALS: Final[tuple[int, ...]] = (
    signal.SIGINT,   # ^C
    signal.SIGQUIT,  # ^\
)
_TTY_SIGNALS: Final[tuple[int, ...]] = (
    signal.SIGTTOU,
    signal.SIGTTIN,
)


_g_ctty_fd = -1
_g_is_interactive = None
_g_termios: _Termios | None = None


def _close_ctty_fd() -> None:
    global _g_ctty_fd  # pylint: disable=global-statement
    if _g_ctty_fd >= 0:
        os.close(_g_ctty_fd)
        _g_ctty_fd = -1


async def _run_process(cmd: list[str], data_path: str) -> asyncio.subprocess.Process:  # pylint: disable=no-member
    """
    Starts a potentially interactive process, performing minimal job control
    if we are launched in an interactive context.
    """
    # Assorted references:
    # https://stackoverflow.com/questions/58918188/why-is-stdin-not-propagated-to-child-process-of-different-process-group

    global _g_ctty_fd, _g_is_interactive, _g_termios  # pylint: disable=global-statement

    # locate our controlling terminal
    try:
        _g_ctty_fd = os.open("/dev/tty", os.O_RDWR | os.O_NOCTTY)
        # only perform job control if we are in the foreground group
        _g_is_interactive = (os.tcgetpgrp(_g_ctty_fd) == os.getpgid(0))
    except OSError:
        _close_ctty_fd()
        _g_is_interactive = False

    # ignore keyboard signals such that we stay alive through setup and teardown
    if _g_ctty_fd >= 0:
        for sig in _KBD_SIGNALS:
            signal.signal(sig, signal.SIG_IGN)

    if _g_is_interactive:
        assert _g_ctty_fd >= 0
        # ignore terminal signals such that our own tcset*() on cleanup
        # will not signal us when we are not in a foreground group anymore
        for sig in _TTY_SIGNALS:
            signal.signal(sig, signal.SIG_IGN)

        # configure terminal for the child
        # (suppress ^Z as we are not doing full, proper job control)
        _g_termios = _Termios(*termios.tcgetattr(_g_ctty_fd))
        ti = copy.deepcopy(_g_termios)
        ti.cc[termios.VSUSP] = termios._POSIX_VDISABLE  # type: ignore[attr-defined]  # pylint: disable=protected-access
        termios.tcsetattr(_g_ctty_fd, termios.TCSANOW, ti.fields())

    def _preexec() -> None:
        if _g_is_interactive:
            assert _g_ctty_fd >= 0
            # place ourselves into a new process group and become foreground
            me = os.getpid()
            os.setpgid(me, me)
            os.tcsetpgrp(_g_ctty_fd, me)
        # reset signal disposition for the child
        # "The dispositions of any signals that are being caught are reset to the default",
        # which notably does not include SIG_IGN
        for sig in _KBD_SIGNALS + _TTY_SIGNALS:
            signal.signal(sig, signal.SIG_DFL)

    subprocess = (await asyncio.create_subprocess_exec(
        *cmd,
        preexec_fn=_preexec,
        env={
            **os.environ,
            "KVMD_PST_DATA": data_path,
        },
    ))

    child = subprocess.pid
    if _g_is_interactive:
        assert _g_ctty_fd >= 0
        # race avoidance: place the child into a new process group and make it foreground
        with contextlib.suppress(PermissionError):
            # setpgid() returns EACCES if we lost the race and the child had exec'd already
            os.setpgid(child, child)
        os.tcsetpgrp(_g_ctty_fd, child)

    return subprocess


def _cleanup_process() -> None:
    if _g_is_interactive:
        assert _g_ctty_fd >= 0
        assert _g_termios is not None
        os.tcsetpgrp(_g_ctty_fd, os.getpgrp())
        termios.tcsetattr(_g_ctty_fd, termios.TCSANOW, _g_termios.fields())


async def _run_cmd_ws(cmd: list[str], ws: aiohttp.ClientWebSocketResponse) -> int:  # pylint: disable=too-many-branches
    logger = get_logger(0)
    recv_task: (asyncio.Task | None) = None
    proc_task: (asyncio.Task | None) = None
    proc: (asyncio.subprocess.Process | None) = None  # pylint: disable=no-member

    try:  # pylint: disable=too-many-nested-blocks
        while True:
            if recv_task is None:
                recv_task = asyncio.create_task(ws.receive())
            if proc_task is None and proc is not None:
                proc_task = asyncio.create_task(proc.wait())

            tasks = list(filter(None, [recv_task, proc_task]))
            done = (await aiotools.wait_first(*tasks))[0]

            if recv_task in done:
                msg = recv_task.result()
                if msg.type == aiohttp.WSMsgType.TEXT:
                    (event_type, event) = htserver.parse_ws_event(msg.data)
                    if event_type == "storage":
                        if event["data"]["write_allowed"] and proc is None:
                            logger.info("PST write is allowed: %s", event["data"]["path"])
                            logger.info("Running the process ...")
                            proc = await _run_process(cmd, event["data"]["path"])
                        elif not event["data"]["write_allowed"]:
                            logger.error("PST write is not allowed")
                            break
                elif msg.type == aiohttp.WSMsgType.CLOSED:
                    logger.error("PST connection closed")
                    break
                else:
                    logger.error("Unknown PST message type: %r", msg)
                    break
                recv_task = None

            if proc_task in done:
                break
    except Exception:
        logger.exception("Unhandled exception")

    if recv_task is not None:
        recv_task.cancel()
    if proc_task is not None:
        proc_task.cancel()
    if proc is not None:
        try:
            _cleanup_process()
        finally:
            await aioproc.kill_process(proc, 1, logger)
            assert proc.returncode is not None
            logger.info("Process finished: returncode=%d", proc.returncode)
        return proc.returncode
    return 1


async def _run_cmd(cmd: list[str], unix_path: str) -> None:
    get_logger(0).info("Opening PST session ...")
    async with aiohttp.ClientSession(
        headers={"User-Agent": htclient.make_user_agent("KVMD-PSTRun")},
        connector=aiohttp.UnixConnector(path=unix_path),
        timeout=aiohttp.ClientTimeout(total=5),
    ) as session:

        async with session.ws_connect("http://localhost:0/ws") as ws:
            try:
                retval = await _run_cmd_ws(cmd, ws)
            finally:
                _close_ctty_fd()
            raise SystemExit(retval)


# =====
def main() -> None:
    ia = init(
        add_help=False,
        cli_logging=True,
    )
    parser = argparse.ArgumentParser(
        prog="kvmd-pstrun",
        description="Request the access to KVMD persistent storage and run the script",
        parents=[ia.parser],
    )
    parser.add_argument("cmd", nargs="+", help="Script with arguments to run")
    options = parser.parse_args(ia.args)
    aiotools.run(_run_cmd(options.cmd, ia.config.pst.server.unix))
