# ========================================================================== #
#                                                                            #
#    KVMD - The main PiKVM daemon.                                           #
#                                                                            #
#    Copyright (C) 2018-2021  Maxim Devaev <mdevaev@gmail.com>               #
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

from typing import Tuple
from typing import List
from typing import Dict
from typing import Optional

import setproctitle

from .logging import get_logger


# =====
async def run_process(
    cmd: List[str],
    err_to_null: bool=False,
    use_shell: bool=False,
    env: Optional[Dict[str, str]]=None,
) -> asyncio.subprocess.Process:  # pylint: disable=no-member
    if use_shell:
        return (await asyncio.create_subprocess_shell(
            ' '.join(cmd),
            stdout=asyncio.subprocess.PIPE,
            stderr=(asyncio.subprocess.DEVNULL if err_to_null else asyncio.subprocess.STDOUT),
            preexec_fn=os.setpgrp,
            env=env,
        ))
    else:
        return (await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=(asyncio.subprocess.DEVNULL if err_to_null else asyncio.subprocess.STDOUT),
            preexec_fn=os.setpgrp,
            env=env,
        ))


async def read_process(
    cmd: List[str],
    err_to_null: bool=False,
    use_shell: bool=False,
    env: Optional[Dict[str, str]]=None,
) -> Tuple[asyncio.subprocess.Process, str]:  # pylint: disable=no-member

    proc = await run_process(cmd, err_to_null, use_shell, env)
    (stdout, _) = await proc.communicate()
    return (proc, stdout.decode(errors="ignore").strip())


async def log_process(
    cmd: List[str],
    logger: logging.Logger,
    env: Optional[Dict[str, str]]=None,
) -> asyncio.subprocess.Process:  # pylint: disable=no-member

    (proc, stdout) = await read_process(cmd, env=env)
    if stdout:
        log = (logger.info if proc.returncode == 0 else logger.error)
        for line in stdout.split("\n"):
            log("=> %s", line)
    return proc


async def log_stdout_infinite(proc: asyncio.subprocess.Process, logger: logging.Logger) -> None:  # pylint: disable=no-member
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


async def kill_process(proc: asyncio.subprocess.Process, wait: float, logger: logging.Logger) -> None:  # pylint: disable=no-member
    if proc.returncode is None:
        try:
            #We use killpg to kill any processes spawned by the top-level process;
            #This is needed for processes created with use_shell/create_subprocess_shell
            pgid = os.getpgid(proc.pid)
            os.killpg(pgid,signal.SIGTERM)
            await asyncio.sleep(wait)
            process_exited_by_sigterm = proc.returncode is not None
            try:
                os.killpg(pgid,signal.SIGKILL)
            except Exception:
                if not process_exited_by_sigterm and proc.returncode is not None:
                    raise
            await proc.wait()
            logger.info("Process killed: pid=%d; retcode=%d", proc.pid, proc.returncode)
        except asyncio.CancelledError:
            pass
        except Exception:
            if proc.returncode is None:
                logger.exception("Can't kill process pid=%d", proc.pid)
            else:
                logger.info("Process killed: pid=%d; retcode=%d", proc.pid, proc.returncode)


def rename_process(suffix: str, prefix: str="kvmd") -> None:
    setproctitle.setproctitle(f"{prefix}/{suffix}: {setproctitle.getproctitle()}")


def settle(name: str, suffix: str, prefix: str="kvmd") -> logging.Logger:
    logger = get_logger(1)
    logger.info("Started %s pid=%d", name, os.getpid())
    os.setpgrp()
    rename_process(suffix, prefix)
    return logger
