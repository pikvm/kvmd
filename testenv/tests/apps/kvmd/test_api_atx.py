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
import json
import threading

from collections.abc import Awaitable
from collections.abc import Callable
from types import SimpleNamespace
from unittest.mock import AsyncMock
from unittest.mock import Mock
from unittest.mock import call

import gpiod
import pytest

from aiohttp.test_utils import make_mocked_request
from aiohttp.web import Application
from aiohttp.web import Request
from aiohttp.web import Response

from kvmd.apps.kvmd.api.atx import AtxApi
from kvmd.htserver import HttpServer
from kvmd.htserver import make_json_response
from kvmd.plugins.atx import BaseAtx
from kvmd.plugins.atx import gpio
from kvmd.plugins.atx.disabled import Plugin as DisabledPlugin
from kvmd.validators import ValidatorError
from kvmd.yamlconf import make_config

from ...gpio_fakes import _patch_request
from ...plugins.atx.test_gpio import _change_state
from ...plugins.atx.test_gpio import _make_plugin
from ...plugins.atx.test_gpio import _run_plugin
from ...plugins.atx.test_gpio import _wait_until


def _state_handler(atx: BaseAtx) -> Callable[[Request], Awaitable[Response]]:
    api = AtxApi(atx)
    handlers = [
        handler
        for handler in [getattr(api, name) for name in dir(api)]
        if getattr(handler, "_http_exposed", False)
        and getattr(handler, "_http_method", None) == "GET"
        and getattr(handler, "_http_path", None) == "/atx"
    ]
    assert len(handlers) == 1
    return handlers[0]


def _make_atx() -> Mock:
    atx = Mock(spec=BaseAtx)
    atx.get_state = AsyncMock(return_value={
        "enabled": True,
        "busy": True,
        "acts": {"power": True, "reset": False},
        "leds": {"power": True, "hdd": False},
    })
    atx.get_history = AsyncMock(return_value={
        "interval_ms": 100,
        "complete": True,
        "power": [1] * 30,
        "transitions": [],
    })
    return atx


@pytest.mark.asyncio
@pytest.mark.parametrize("history", [None, "0", "false", "no"])
async def test_ok__state_without_history(history: (str | None)) -> None:
    atx = _make_atx()
    path = "/atx" if history is None else f"/atx?history={history}"
    response = await _state_handler(atx)(make_mocked_request("GET", path))
    state = atx.get_state.return_value
    assert response.status == 200
    assert response.text == make_json_response(state).text
    assert json.loads(response.text or "")["result"] == state
    assert "history" not in state
    atx.get_state.assert_awaited_once_with()
    atx.get_history.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("history", ["1", "true", "yes"])
async def test_ok__state_with_history(history: str) -> None:
    atx = _make_atx()
    response = await _state_handler(atx)(make_mocked_request("GET", f"/atx?history={history}"))
    assert response.status == 200
    assert json.loads(response.text or "") == {
        "ok": True,
        "result": {**atx.get_state.return_value, "history": atx.get_history.return_value},
    }
    assert "history" not in atx.get_state.return_value
    atx.get_state.assert_awaited_once_with()
    atx.get_history.assert_awaited_once_with()
    assert atx.mock_calls == [call.get_state(), call.get_history()]


@pytest.mark.asyncio
async def test_fail__invalid_history(monkeypatch: pytest.MonkeyPatch) -> None:
    atx = _make_atx()
    request = make_mocked_request("GET", "/atx?history=abc")
    with pytest.raises(ValidatorError):
        await _state_handler(atx)(request)

    # Register the real HttpServer wrapper, including its exception-to-400 conversion.
    server = HttpServer()
    app = Application()
    monkeypatch.setattr(server, "_HttpServer__app", app, raising=False)
    getattr(server, "_add_exposed")(AtxApi(atx))
    match = await app.router.resolve(request)
    response = await match.handler(request)
    assert isinstance(response, Response)
    assert response.status == 400
    payload = json.loads(response.text or "")
    assert payload["ok"] is False
    assert payload["result"]["error"] == "ValidatorError"
    atx.get_state.assert_not_awaited()
    atx.get_history.assert_not_awaited()


@pytest.mark.asyncio
async def test_ok__disabled_history() -> None:
    atx = DisabledPlugin(make_config({}, {}, DisabledPlugin.get_plugin_options()))
    state = await atx.get_state()
    response = await _state_handler(atx)(make_mocked_request("GET", "/atx?history=1"))
    assert response.status == 200
    assert json.loads(response.text or "") == {"ok": True, "result": {**state, "history": {}}}


@pytest.mark.asyncio
@pytest.mark.parametrize("inverted", [False, True])
async def test_ok__gpio_history_matches_state(monkeypatch: pytest.MonkeyPatch, inverted: bool) -> None:
    line_req = _patch_request(monkeypatch, {24: False, 22: False})
    now = 1_000_000_000_000_000_037
    monkeypatch.setattr(gpio, "time", SimpleNamespace(monotonic_ns=lambda: now))
    atx = _make_plugin(inverted)
    handler = _state_handler(atx)
    states = atx.poll_state()
    async with _run_plugin(atx, line_req):
        try:
            await _wait_until(lambda: getattr(atx, "_Plugin__reader").get_changes(24) == 0)
            state = await asyncio.wait_for(anext(states), timeout=1)
            for (index, raw) in enumerate([False, True, False, True, False]):
                if index:
                    now += 500_000_000
                    state = await _change_state(states, line_req, 24, raw)
                response = await handler(make_mocked_request("GET", "/atx?history=1"))
                assert response.status == 200
                payload = json.loads(response.text or "")
                result = payload["result"]
                assert payload["ok"] is True
                assert result == {**state, "history": result["history"]}
                assert result["leds"]["power"] is (raw ^ inverted)
                assert len(result["history"]["power"]) == 30
                assert result["history"]["power"][29] == result["leds"]["power"]
        finally:
            await states.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("inverted", [False, True])
async def test_ok__gpio_history_during_startup(monkeypatch: pytest.MonkeyPatch, inverted: bool) -> None:
    line_req = _patch_request(monkeypatch, {24: True, 22: False})
    monkeypatch.setattr(gpio, "time", SimpleNamespace(monotonic_ns=lambda: 1_000_000_000_000_000_037))
    atx = _make_plugin(inverted)
    reader = getattr(atx, "_Plugin__reader")
    allow_initial_read = threading.Event()
    get_values = line_req.get_values
    get = reader.get

    def gated_get_values(pins: list[int]) -> list[gpiod.line.Value]:
        assert allow_initial_read.wait(2)
        return get_values(pins)

    def read(pin: int) -> bool:
        value = get(pin)
        if pin == 24 and not allow_initial_read.is_set():
            # Finish GPIO initialization while the request is still building its response.
            allow_initial_read.set()
            line_req.flush_blocking()
        return value

    monkeypatch.setattr(line_req, "get_values", gated_get_values)
    monkeypatch.setattr(reader, "get", read)
    handler = _state_handler(atx)
    request = make_mocked_request("GET", "/atx?history=1")
    async with _run_plugin(atx, line_req):
        await asyncio.sleep(0)
        response = await handler(request)
        assert response.status == 200
        result = json.loads(response.text or "")["result"]
        assert result["leds"]["power"] is inverted
        history = result["history"]
        assert history["power"] == [None] * 30
        assert history["transitions"] == []
        assert history["complete"] is False

        await _wait_until(lambda: reader.get_changes(24) == 0)
        response = await handler(request)
        assert response.status == 200
        result = json.loads(response.text or "")["result"]
        assert result["leds"]["power"] is (not inverted)
        history = result["history"]
        assert history["power"] == [None] * 29 + [int(not inverted)]
        assert history["transitions"] == []
        assert history["complete"] is False


@pytest.mark.asyncio
async def test_ok__gpio_history_with_pending_edge(monkeypatch: pytest.MonkeyPatch) -> None:
    line_req = _patch_request(monkeypatch, {24: False, 22: False})
    now = 1_000_000_000_000_000_037
    monkeypatch.setattr(gpio, "time", SimpleNamespace(monotonic_ns=lambda: now))
    atx = _make_plugin()
    reader = getattr(atx, "_Plugin__reader")
    handler = _state_handler(atx)
    request = make_mocked_request("GET", "/atx?history=1")
    get = reader.get
    queued = False

    def read(pin: int) -> bool:
        nonlocal queued
        value = get(pin)
        if pin == 24 and not queued:
            queued = True
            line_req.push((pin, True))
            # Wait for the GPIO thread to queue the edge while the event loop is in the handler.
            line_req.flush_blocking()
        return value

    async with _run_plugin(atx, line_req):
        await line_req.flush()
        assert reader.get_changes(24) == 0
        now += 3_000_000_000
        monkeypatch.setattr(reader, "get", read)

        response = await handler(request)
        assert response.status == 200
        result = json.loads(response.text or "")["result"]
        assert result["leds"]["power"] is False
        assert result["history"]["power"] == [0] * 30
        assert result["history"]["transitions"] == []
        assert result["history"]["complete"] is True

        await _wait_until(lambda: reader.get_changes(24) == 1)
        response = await handler(request)
        assert response.status == 200
        result = json.loads(response.text or "")["result"]
        assert result["leds"]["power"] is True
        assert result["history"]["power"] == [0] * 29 + [1]
        assert result["history"]["transitions"] == [[0.0, True]]
        assert result["history"]["complete"] is True
