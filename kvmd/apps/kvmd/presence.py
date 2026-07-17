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


"""
User presence awareness registry for PiKVM.

Design:
    This module uses module-level state to track which users are connected
    (via WebSocket) and which users are actively sending HID input events.
    This is safe because kvmd runs as a single-threaded asyncio application;
    all calls happen on the same event loop with no concurrent mutations.

Rate-limiting:
    record_input() is called from HID handlers on every key/mouse event,
    which can reach ~1kHz during mouse drags. To avoid unnecessary overhead,
    each call is rate-limited per user: the time.monotonic() syscall and
    dict write are skipped if the last recorded timestamp for that user is
    less than 0.25 seconds old. On the common (throttled) path, the function
    performs only a dict lookup, a subtraction, and a comparison.

Memory bounds:
    _last_input entries are auto-pruned when older than 1 hour. Pruning
    runs inside get_controllers() and get_active(), which are called every
    0.5 seconds by the presence broadcast loop. The _users dict is bounded
    by the number of concurrent WebSocket connections.
"""


import time

from ...logging import get_logger


# =====
# token -> username
_users: dict[str, str] = {}

# username -> last input monotonic timestamp
_last_input: dict[str, float] = {}

# username -> last record_input monotonic timestamp (for rate-limiting)
_last_record_ts: dict[str, float] = {}

_RATE_LIMIT_INTERVAL = 0.25  # seconds
_PRUNE_AGE = 3600.0  # 1 hour


# =====
def set_user(token: str, user: str) -> None:
    if user:
        _users[token] = user
        get_logger(0).info("Presence: user %r connected (token=%s...)", user, token[:8])


def unset_user(token: str) -> None:
    user = _users.pop(token, None)
    if user:
        get_logger(0).info("Presence: user %r disconnected (token=%s...)", user, token[:8])
        # Clean up input tracking if no other sessions for this user
        if user not in _users.values():
            _last_input.pop(user, None)
            _last_record_ts.pop(user, None)


def record_input(token: str) -> None:
    user = _users.get(token)
    if not user:
        return
    now = time.monotonic()
    prev = _last_record_ts.get(user, 0.0)
    if (now - prev) < _RATE_LIMIT_INTERVAL:
        return
    _last_record_ts[user] = now
    _last_input[user] = now


def get_controllers(window: float=10.0) -> list[str]:
    _prune()
    now = time.monotonic()
    return sorted(
        user for (user, ts) in _last_input.items()
        if (now - ts) <= window
    )


def get_active(idle: float=300.0) -> list[str]:
    _prune()
    now = time.monotonic()
    return sorted(
        user for (user, ts) in _last_input.items()
        if (now - ts) <= idle
    )


def get_connected_users() -> list[str]:
    return sorted(set(_users.values()))


def _prune() -> None:
    now = time.monotonic()
    stale = [
        user for (user, ts) in _last_input.items()
        if (now - ts) > _PRUNE_AGE
    ]
    for user in stale:
        _last_input.pop(user, None)
        _last_record_ts.pop(user, None)
