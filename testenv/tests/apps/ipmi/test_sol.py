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
import time
import queue
import threading

import serial

import pytest

from kvmd.apps.ipmi.server import _sol_pump


# =====
class _TtyProbe:
    # Wraps a real serial.Serial, timestamping every read that returns data
    # so the test can measure how long the reader was ever stalled.

    def __init__(self, ser: serial.Serial) -> None:
        self.__ser = ser
        self.read_times: list[float] = []
        self.total_read = 0

    def fileno(self) -> int:
        return self.__ser.fileno()

    def read_all(self) -> bytes:
        data = self.__ser.read_all()
        if data:
            self.read_times.append(time.monotonic())
            self.total_read += len(data)
        return data

    def write(self, data: bytes) -> int:
        return self.__ser.write(data)


class _BlockingConsole:
    # Stands in for pyghmi's ServerConsole: send_data() blocks for block_s seconds,
    # modelling Console._sendoutput waiting (stop-and-wait) for the client's SOL ACK.

    def __init__(self, block_s: float) -> None:
        self.__block_s = block_s
        self.calls = 0
        self.sent = 0

    def send_data(self, data: bytes) -> None:
        self.calls += 1
        self.sent += len(data)
        time.sleep(self.__block_s)


class _FakeUserQ:
    # Minimal AioMpQueue stand-in that never has pending user input.

    def __init__(self) -> None:
        (self.__r, self.__w) = os.pipe()

    def get_reader(self) -> int:
        return self.__r

    def qsize(self) -> int:
        return 0

    def get_nowait(self) -> bytes:
        raise queue.Empty

    def close(self) -> None:
        os.close(self.__r)
        os.close(self.__w)


# =====
@pytest.mark.skipif(not hasattr(os, "openpty"), reason="requires a POSIX pty")
def test_ok__sol_pump_does_not_block_reads_on_slow_send() -> None:
    # Regression test for pikvm/pikvm#899: pyghmi's SOL send_data() blocks until the
    # client ACKs each packet. _sol_pump must keep draining the serial port during that
    # blocking send instead of stalling (which caused bursty/stuttering console output).

    block_s = 0.5           # each send_data() blocks half a second
    select_timeout = 0.05
    feed_s = 1.5
    rate_bps = 11520        # 115200 baud, 8N1

    (master, slave) = os.openpty()
    ser = serial.Serial(os.ttyname(slave))
    tty = _TtyProbe(ser)
    console = _BlockingConsole(block_s)
    user_q = _FakeUserQ()
    stop = threading.Event()

    chunk = b"x" * 96
    interval = len(chunk) / rate_bps
    fed = 0

    def feeder() -> None:
        nonlocal fed
        deadline = time.monotonic()
        end = deadline + feed_s
        while time.monotonic() < end and not stop.is_set():
            os.write(master, chunk)
            fed += len(chunk)
            deadline += interval
            slp = deadline - time.monotonic()
            if slp > 0:
                time.sleep(slp)

    worker = threading.Thread(
        target=_sol_pump,
        args=(console, tty, user_q, select_timeout, stop.is_set),
        daemon=True,
    )
    feed = threading.Thread(target=feeder, daemon=True)
    worker.start()
    feed.start()
    feed.join()
    time.sleep(block_s + 0.3)   # let the pump drain the tail
    stop.set()
    worker.join(timeout=block_s + 2.0)

    times = tty.read_times
    gaps = [b - a for (a, b) in zip(times, times[1:])]
    max_gap = max(gaps) if gaps else 0.0

    try:
        # The sender must actually have been blocking (otherwise the test proves nothing).
        assert console.calls >= 1
        # The reader kept going during the blocked sends: the largest gap between reads is
        # on the order of select_timeout, nowhere near block_s. (Old inline code stalled for
        # a full block_s == 0.5s on every send.)
        assert max_gap < block_s / 2, f"reader stalled for {max_gap:.3f}s (block was {block_s}s)"
        # Nothing was dropped: everything fed was read off the serial port.
        assert tty.total_read == fed
        assert fed > 0
    finally:
        stop.set()
        os.close(master)
        ser.close()
        os.close(slave)
        user_q.close()
