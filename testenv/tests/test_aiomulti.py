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


import signal
import time

import pytest

from kvmd.aiomulti import AioMpProcess


# =====
def _target(a: int, b: str) -> None:
    assert a == 1
    assert b == "foo"
    while True:
        time.sleep(1)


# =====
@pytest.mark.asyncio
async def test_ok__sigterm_join() -> None:
    proc = AioMpProcess("test", _target, (1, "foo"))
    assert not proc.is_alive()
    proc.start()
    assert proc.is_alive()
    assert (await proc.async_join(0.1))
    assert (await proc.async_join(1))
    proc.send_sigterm()
    assert not (await proc.async_join(30))
    assert not (await proc.async_join(1))
    assert not (await proc.async_join())
    assert proc.exitcode == -int(signal.SIGTERM)


@pytest.mark.asyncio
async def test_ok__sigkill_join() -> None:
    proc = AioMpProcess("test", _target, (1, "foo"))
    assert not proc.is_alive()
    proc.start()
    assert proc.is_alive()
    assert (await proc.async_join(0.1))
    assert (await proc.async_join(1))
    proc.sendpg_sigkill()
    assert not (await proc.async_join(30))
    assert not (await proc.async_join(1))
    assert not (await proc.async_join())
    assert proc.exitcode == -int(signal.SIGKILL)
