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


import collections
import math

import pytest

from kvmd.plugins.atx.gpio import _LedHistory


# Use large, unaligned nanosecond timestamps to expose any loss of integer precision.
_START = 1_000_000_000_000_000_037
_SECOND = 1_000_000_000
_INTERVAL = 100_000_000


def test_ok__uninitialized() -> None:
    history = _LedHistory()
    assert history.make_report(_START, True, None) == {
        "interval_ms": 100,
        "complete": False,
        "power": [None] * 30,
        "transitions": [],
    }


@pytest.mark.parametrize("value", [False, True])
def test_fail__missing_initial_record(value: bool, caplog: pytest.LogCaptureFixture) -> None:
    history = _LedHistory()
    assert history.make_report(_START, value, 0) == {
        "interval_ms": 100,
        "complete": False,
        "power": [None] * 29 + [int(value)],
        "transitions": [],
    }
    assert history.make_report(_START + 3 * _SECOND - 1, value, 0)["complete"] is False
    report = history.make_report(_START + 3 * _SECOND, value, 0)
    assert report["complete"] is True
    assert report["power"] == [int(value)] * 30
    assert len(caplog.records) == 1
    assert "history has a gap" in caplog.text
    assert caplog.records[0].exc_info is None

    # A delayed initial callback must discard the recovered interval and start again.
    restart = _START + 4 * _SECOND
    history.record(value, 0, restart)
    report = history.make_report(restart, value, 0)
    assert report["power"] == [None] * 29 + [int(value)]
    assert report["transitions"] == []
    assert report["complete"] is False
    assert history.make_report(restart + 3 * _SECOND - 1, value, 0)["complete"] is False
    assert history.make_report(restart + 3 * _SECOND, value, 0)["complete"] is True


@pytest.mark.parametrize("elapsed,unknown,complete", [
    (0, 29, False),
    (1, 29, False),
    (_INTERVAL - 1, 29, False),
    (_INTERVAL, 28, False),
    (1_234_000_000, 17, False),
    (2_900_000_000 - 1, 1, False),
    (2_900_000_000, 0, False),
    (2_950_000_000, 0, False),
    (3 * _SECOND - 1, 0, False),
    (3 * _SECOND, 0, True),
])
def test_ok__startup(elapsed: int, unknown: int, complete: bool) -> None:
    history = _LedHistory()
    history.record(True, 0, _START)
    assert history.make_report(_START + elapsed, True, 0) == {
        "interval_ms": 100,
        "complete": complete,
        "power": [None] * unknown + [1] * (30 - unknown),
        "transitions": [],
    }


@pytest.mark.parametrize("value", [False, True])
def test_ok__steady(value: bool) -> None:
    history = _LedHistory()
    history.record(value, 0, _START)
    for elapsed in [3 * _SECOND, 30 * _SECOND]:
        report = history.make_report(_START + elapsed, value, 0)
        assert report == {
            "interval_ms": 100,
            "complete": True,
            "power": [int(value)] * 30,
            "transitions": [],
        }
        assert all(isinstance(sample, int) and not isinstance(sample, bool) for sample in report["power"])


def test_ok__sleep_example() -> None:
    history = _LedHistory()
    now = _START + 3 * _SECOND
    history.record(False, 0, _START)
    for (changes, age_ms) in enumerate([2731, 2216, 1701, 1186, 671, 156], start=1):
        history.record(bool(changes % 2), changes, now - age_ms * 1_000_000)
    assert history.make_report(now, False, 6) == {
        "interval_ms": 100,
        "complete": True,
        "power": [
            0, 0, 1, 1, 1, 1, 1, 0, 0, 0,
            0, 0, 1, 1, 1, 1, 1, 1, 0, 0,
            0, 0, 0, 1, 1, 1, 1, 1, 0, 0,
        ],
        "transitions": [
            [-2.731, True], [-2.216, False], [-1.701, True],
            [-1.186, False], [-0.671, True], [-0.156, False],
        ],
    }


def test_ok__sample_boundaries_and_rounding() -> None:
    now = _START + 3 * _SECOND
    for index in range(30):
        for offset in [-1, 0, 1, 999_999]:
            transition = _START + (index + 1) * _INTERVAL + offset
            if transition > now:
                continue
            history = _LedHistory()
            history.record(False, 0, _START)
            history.record(True, 1, transition)
            report = history.make_report(now, True, 1)
            relative = report["transitions"][0][0]
            for sample in range(30):
                included = transition <= _START + (sample + 1) * _INTERVAL
                assert report["power"][sample] == int(included)
                assert included == (relative <= (-2900 + sample * 100) / 1000)
            assert report["transitions"][0][1] is True
            if relative == 0:
                assert math.copysign(1, relative) == 1  # No negative zero.


def test_ok__prune_when_reading_and_recording() -> None:
    history = _LedHistory()
    history.record(False, 0, _START)
    history.record(True, 1, _START + _SECOND)
    report = history.make_report(_START + 11 * _SECOND, True, 1)
    assert report["power"] == [1] * 30
    assert report["transitions"] == []
    assert report["complete"] is True

    history.record(False, 2, _START + 12 * _SECOND)
    report = history.make_report(_START + 12 * _SECOND, False, 2)
    assert report["power"] == [1] * 29 + [0]
    assert report["transitions"] == [[0.0, False]]
    assert report["complete"] is True
    # A transition exactly on the left boundary becomes the base, rather than an output event.
    report = history.make_report(_START + 15 * _SECOND, False, 2)
    assert report["power"] == [0] * 30
    assert report["transitions"] == []
    assert report["complete"] is True

    other = _LedHistory()
    other.record(False, 0, _START)
    other.record(True, 1, _START + _SECOND)
    other.record(False, 2, _START + 12 * _SECOND)
    assert other.make_report(_START + 12 * _SECOND, False, 2) == {
        "interval_ms": 100,
        "complete": True,
        "power": [1] * 29 + [0],
        "transitions": [[0.0, False]],
    }


def test_ok__overflow_and_recovery() -> None:
    history = _LedHistory()
    history.record(False, 0, _START)
    for changes in range(1, 81):
        now = _START + 4 * _SECOND + changes * 10_000_000
        history.record(bool(changes % 2), changes, now)
        report = history.make_report(now, bool(changes % 2), changes)
        assert len(report["transitions"]) == min(changes, 64)
        assert report["complete"] is (changes <= 64)

    # After 80 changes, transition 16 is the base. Recovery is earlier than overflow + 3s.
    base_time = _START + 4 * _SECOND + 16 * 10_000_000
    report = history.make_report(base_time + 3 * _SECOND - 1, False, 80)
    assert report["power"].count(None) == 0
    assert report["complete"] is False
    report = history.make_report(base_time + 3 * _SECOND, False, 80)
    assert report["complete"] is True
    assert len(report["transitions"]) == 64


@pytest.mark.parametrize("elapsed", [0, 234_000_000, 1_234_000_000, 2_950_000_000])
def test_ok__conservative_retry_delay(elapsed: int) -> None:
    history = _LedHistory()
    history.record(True, 0, _START)
    report = history.make_report(_START + elapsed, True, 0)
    assert report["complete"] is False
    unknown = report["power"].count(None)
    if elapsed == 2_950_000_000:
        assert unknown == 0
    retry_at = _START + elapsed + (unknown + 1) * _INTERVAL
    assert history.make_report(retry_at, True, 0)["complete"] is True


@pytest.mark.parametrize("current,changes", [(False, 2), (True, 0)])
def test_fail__self_check(current: bool, changes: int) -> None:
    history = _LedHistory()
    history.record(False, 0, _START)
    assert history.make_report(_START + 3 * _SECOND, False, 0)["complete"] is True
    restart = _START + 4 * _SECOND
    report = history.make_report(restart, current, changes)
    assert report["complete"] is False
    assert report["power"] == [None] * 29 + [int(current)]
    assert report["transitions"] == []
    assert history.make_report(restart + 3 * _SECOND - 1, current, changes)["complete"] is False
    assert history.make_report(restart + 3 * _SECOND, current, changes)["complete"] is True


@pytest.mark.parametrize("changes", [0, 1, 2])
def test_fail__duplicate_value(changes: int) -> None:
    history = _LedHistory()
    history.record(True, 0, _START)
    restart = _START + 4 * _SECOND
    history.record(True, changes, restart)
    report = history.make_report(restart, True, changes)
    assert report["transitions"] == []
    assert report["power"] == [None] * 29 + [1]
    assert report["complete"] is False
    assert history.make_report(restart + 3 * _SECOND, True, changes)["complete"] is True


@pytest.mark.parametrize("scenario", ["returned", "another", "no_followup", "partial_failure"])
def test_fail__missing_changes(scenario: str, monkeypatch: pytest.MonkeyPatch) -> None:
    history = _LedHistory()
    history.record(False, 0, _START)
    assert history.make_report(_START + 3 * _SECOND, False, 0)["complete"] is True
    missed = _START + 4 * _SECOND
    if scenario == "partial_failure":
        class BrokenDeque(collections.deque[tuple[int, bool]]):
            failed = False

            def append(self, item: tuple[int, bool]) -> None:
                super().append(item)
                if not self.failed:
                    self.failed = True
                    raise RuntimeError("record failed after modifying transitions")

        monkeypatch.setattr(history, "_LedHistory__transitions", BrokenDeque())
        with pytest.raises(RuntimeError, match="record failed"):
            history.record(True, 1, missed)
        assert getattr(history, "_LedHistory__seen") == 0
        assert list(getattr(history, "_LedHistory__transitions")) == [(missed, True)]
    # In the other scenarios, notification 1 (True) was never delivered to record().
    restart = missed + _SECOND
    if scenario == "no_followup":
        current = True
        changes = 1
    else:
        current = False
        changes = 2
        history.record(current, changes, restart)
        if scenario == "another":
            history.record(True, 3, restart + _INTERVAL)
            current = True
            changes = 3
    report = history.make_report(restart + _INTERVAL, current, changes)
    # With no follow-up, it is the read that detects the gap and sets the new base.
    if scenario == "no_followup":
        restart += _INTERVAL
    assert report["complete"] is False
    assert report["power"][-1] == int(current)
    assert history.make_report(restart + 3 * _SECOND - 1, current, changes)["complete"] is False
    assert history.make_report(restart + 3 * _SECOND, current, changes)["complete"] is True
    assert getattr(history, "_LedHistory__started") == _START


def test_fail__partial_record_detected_on_read(monkeypatch: pytest.MonkeyPatch) -> None:
    history = _LedHistory()
    history.record(False, 0, _START)

    def broken_prune(_now: int) -> None:
        raise RuntimeError("prune failed")

    now = _START + 4 * _SECOND
    with monkeypatch.context() as patch:
        patch.setattr(history, "_LedHistory__prune", broken_prune)
        with pytest.raises(RuntimeError, match="prune failed"):
            history.record(True, 1, now)
    report = history.make_report(now, True, 1)
    assert report["power"] == [None] * 29 + [1]
    assert report["complete"] is False
    assert history.make_report(now + 3 * _SECOND, True, 1)["complete"] is True


def test_fail__self_check_log_rate(caplog: pytest.LogCaptureFixture) -> None:
    history = _LedHistory()
    history.record(False, 0, _START)
    steps = [
        (0, 1),
        (1, 1),
        (60 * _SECOND - 1, 1),
        (60 * _SECOND, 2),
        (60 * _SECOND, 2),
        (120 * _SECOND - 1, 2),
        (120 * _SECOND, 3),
    ]
    for (changes, (elapsed, logs)) in enumerate(steps, start=1):
        report = history.make_report(_START + elapsed, bool(changes % 2), changes)
        assert report["complete"] is False
        assert len(caplog.records) == logs
    assert all(record.exc_info is None for record in caplog.records)
    assert [record.levelname for record in caplog.records] == ["ERROR"] * 3
    assert "4 gaps" in caplog.records[1].getMessage()
    assert "7 gaps" in caplog.records[2].getMessage()
