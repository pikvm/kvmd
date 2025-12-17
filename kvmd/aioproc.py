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
import logging

import setproctitle

from .logging import get_logger


# =====
async def run_process(
    cmd: list[str],
    err_to_null: bool=False,
    env: (dict[str, str] | None)=None,
) -> asyncio.subprocess.Process:  # pylint: disable=no-member

    return (await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=(asyncio.subprocess.DEVNULL if err_to_null else asyncio.subprocess.STDOUT),
        preexec_fn=os.setpgrp,
        env=env,
    ))


async def read_process(
    cmd: list[str],
    err_to_null: bool=False,
    env: (dict[str, str] | None)=None,
) -> tuple[asyncio.subprocess.Process, str]:  # pylint: disable=no-member

    proc = await run_process(cmd, err_to_null, env)
    (stdout, _) = await proc.communicate()
    return (proc, stdout.decode(errors="ignore").strip())


async def log_process(
    cmd: list[str],
    logger: logging.Logger,
    env: (dict[str, str] | None)=None,
    prefix: str="",
) -> asyncio.subprocess.Process:  # pylint: disable=no-member

    (proc, stdout) = await read_process(cmd, env=env)
    if stdout:
        log = (logger.info if proc.returncode == 0 else logger.error)
        if prefix:
            prefix += " "
        for line in stdout.split("\n"):
            log("%s=> %s", prefix, line)
    return proc


async def log_stdout_infinite(
    proc: asyncio.subprocess.Process,  # pylint: disable=no-member
    logger: logging.Logger,
) -> None:

    empty = 0
    async for line_bytes in proc.stdout:  # type: ignore
        line = line_bytes.decode(errors="ignore").strip()
        if line:
            logger.info("=> %s", line)
            empty = 0
        else:
            empty += 1
            if empty == 100:  # asyncio bug
                raise RuntimeError("Asyncio process: too many empty lines")


async def kill_process(
    proc: asyncio.subprocess.Process,  # pylint: disable=no-member
    wait: float,
    logger: logging.Logger,
) -> None:

    if proc.returncode is None:
        try:
            try:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=wait)
                except TimeoutError:
                    pass
            finally:
                if proc.returncode is None:
                    try:
                        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                        await asyncio.wait_for(proc.wait(), timeout=wait)
                    except Exception:
                        if proc.returncode is not None:
                            raise
        except Exception:
            if proc.returncode is None:
                logger.exception("Can't kill process pid=%s", proc.pid)
        finally:
            if proc.returncode is not None:
                logger.info("Process killed: retcode=%s", proc.returncode)


def rename_process(suffix: str, prefix: str="kvmd") -> None:
    setproctitle.setproctitle(f"{prefix}/{suffix}: {setproctitle.getproctitle()}")


def settle(name: str, suffix: str, prefix: str="kvmd") -> logging.Logger:
    logger = get_logger(1)
    logger.info("Started %s pid=%s", name, os.getpid())
    os.setpgrp()
    rename_process(suffix, prefix)
    return logger
