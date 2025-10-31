/*****************************************************************************
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
*****************************************************************************/


"use strict";


import {tools} from "./tools.js";


export function Keypad(__el_keypad, __sendKey) {
	var self = this;

	/************************************************************************/

	var __keys = {};
	var __hold_timers = {};

	var __init__ = function() {
		__el_keypad.addEventListener("contextmenu", (ev) => ev.preventDefault());

		for (let el_key of [].slice.call(__el_keypad.getElementsByClassName("key"))) {
			if (el_key.hasAttribute("data-allow-autohold")) {
				el_key.title = "Long left click or short right click for hold, middle for lock";
			} else {
				el_key.title = "Right click for hold, middle for lock";
			}

			let code = el_key.getAttribute("data-code");

			tools.setDefault(__keys, code, []);
			__keys[code].push(el_key);

			tools.el.setOnDown(el_key, (ev) => __clickHandler(el_key, ev));
			tools.el.setOnUp(el_key, () => __clickHandler(el_key, null));
			el_key.onmouseout = function() {
				if (
					__isActive(el_key, "pressed")
					&& !__isActive(el_key, "holded")
					&& !__isActive(el_key, "locked")
				) {
					__clickHandler(el_key, null);
				}
			};
		}
	};

	/************************************************************************/

	self.isCodeActive = function(code) {
		let keys = __keys[code];
		return (keys !== undefined && keys.length > 0 && __isActive(keys[0]));
	};

	self.releaseAll = function() {
		for (let code in __keys) {
			if (self.isCodeActive(code)) {
				self.emit(code, false);
			}
		}
	};

	self.emit = function(code, state) {
		if (code in __keys) {
			let el_key = __keys[code][0];
			__stopHoldTimer(el_key);
			if (state && !__isActive(el_key)) {
				__deactivate(el_key);
				__activate(el_key, "pressed");
				__process(el_key, true);
			} else {
				__deactivate(el_key);
				__process(el_key, false);
			}
			__unholdAll();
		};
	};

	var __clickHandler = function(el_key, ev) {
		let state = false;
		let act = "pressed";
		if (ev) {
			state = (ev.type === "mousedown" || ev.type === "touchstart");
			if (ev.type === "mousedown") {
				if (ev.button === 1) {
					act = "locked";
				} else if (ev.button === 2) {
					act = "holded";
				}
			}
		}

		if (state && !__isActive(el_key)) {
			__stopHoldTimer(el_key);
			__deactivate(el_key);
			__activate(el_key, act);
			__process(el_key, true);
			__startHoldTimer(el_key);
		} else {
			let fixed = (__isActive(el_key, "holded") || __isActive(el_key, "locked"));
			if (!state && fixed && __stopHoldTimer(el_key)) {
				return; // Игнорировать первое отжатие сразу после нажатия
			}
			if (!state) {
				__stopHoldTimer(el_key);
				__deactivate(el_key);
				__process(el_key, false);
				if (!fixed) {
					__unholdAll();
				}
			}
		}
	};

	var __startHoldTimer = function(el_key) {
		__stopHoldTimer(el_key);
		let code = el_key.getAttribute("data-code");
		__hold_timers[code] = setTimeout(function() {
			// Помимо прямой функции, hold timer используется для детектирования факта
			// нажатия в рамках одной сессии press/release, чтобы не отпустить сразу же
			// зажатую или заблокированную клавишу. Поэтому таймер инициализируется всегда,
			// но основную функцию выполняет только если у него есть атрибут data-allow-autohold.
			if (el_key.hasAttribute("data-allow-autohold")) {
				__deactivate(el_key);
				__activate(el_key, "holded");
			}
		}, 500); // Check keypad.css for the animation
	};

	var __stopHoldTimer = function(el_key) {
		let code = el_key.getAttribute("data-code");
		if (!__hold_timers[code]) {
			return false;
		}
		clearTimeout(__hold_timers[code]);
		__hold_timers[code] = null;
		return true;
	};

	var __unholdAll = function() {
		for (let el_key of [].slice.call(__el_keypad.getElementsByClassName("key"))) {
			__stopHoldTimer(el_key);
			if (__isActive(el_key, "holded") && !__isActive(el_key, "locked")) { // Skip duplicating keys
				__deactivate(el_key);
				__process(el_key, false);
			}
		}
	};

	var __isActive = function(el_key, cls=null) {
		let el_keys = __resolveKeys(el_key);
		for (el_key of el_keys) {
			if (cls) {
				if (el_key.classList.contains(cls)) {
					return true;
				}
			} else if (
				el_key.classList.contains("pressed")
				|| el_key.classList.contains("holded")
				|| el_key.classList.contains("locked")
			) {
				return true;
			}
		}
		return false;
	};

	var __activate = function(el_key, cls) {
		let el_keys = __resolveKeys(el_key);
		for (el_key of el_keys) {
			el_key.classList.add(cls);
		}
	};

	var __deactivate = function(el_key) {
		let el_keys = __resolveKeys(el_key);
		for (el_key of el_keys) {
			el_key.classList.remove("pressed");
			el_key.classList.remove("holded");
			el_key.classList.remove("locked");
		}
	};

	var __resolveKeys = function(el_key) {
		let code = el_key.getAttribute("data-code");
		return __keys[code];
	};

	var __process = function(el_key, state) {
		let code = el_key.getAttribute("data-code");
		__sendKey(code, state);
	};

	__init__();
}
