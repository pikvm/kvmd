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


import {tools, $, $$$} from "../tools.js";
import {Keypad} from "../keypad.js";


export function Keyboard(__recordWsEvent) {
	var self = this;

	/************************************************************************/

	var __ws = null;
	var __online = true;

	var __keypad = null;

	var __init__ = function() {
		__keypad = new Keypad($("keyboard-window"), __sendKey, true);

		$("hid-keyboard-led").title = "Keyboard free";

		$("keyboard-window").onkeydown = (ev) => __keyboardHandler(ev, true);
		$("keyboard-window").onkeyup = (ev) => __keyboardHandler(ev, false);
		$("keyboard-window").onfocus = __updateOnlineLeds;
		$("keyboard-window").onblur = __updateOnlineLeds;

		$("stream-window").onkeydown = (ev) => __keyboardHandler(ev, true);
		$("stream-window").onkeyup = (ev) => __keyboardHandler(ev, false);
		$("stream-window").onfocus = __updateOnlineLeds;
		$("stream-window").onblur = __updateOnlineLeds;

		window.addEventListener("focusin", __updateOnlineLeds);
		window.addEventListener("focusout", __updateOnlineLeds);

		tools.storage.bindSimpleSwitch($("hid-keyboard-bad-link-switch"), "hid.keyboard.bad_link", false);
		tools.storage.bindSimpleSwitch($("hid-keyboard-swap-cc-switch"), "hid.keyboard.swap_cc", false);
	};

	/************************************************************************/

	self.setSocket = function(ws) {
		if (ws !== __ws) {
			self.releaseAll();
			__ws = ws;
		}
		__updateOnlineLeds();
	};

	self.setState = function(online, leds, hid_online, hid_busy) {
		if (!hid_online) {
			__online = null;
		} else {
			__online = (online && !hid_busy);
		}
		__updateOnlineLeds();

		for (let led of ["caps", "scroll", "num"]) {
			for (let el of $$$(`.hid-keyboard-${led}-led`)) {
				if (leds[led]) {
					el.classList.add("led-green");
					el.classList.remove("led-gray");
				} else {
					el.classList.add("led-gray");
					el.classList.remove("led-green");
				}
			}
		}
	};

	self.releaseAll = function() {
		__keypad.releaseAll();
	};

	self.emit = function(code, state) {
		__keypad.emitByCode(code, state);
	};

	var __updateOnlineLeds = function() {
		let is_captured = (
			$("stream-window").classList.contains("window-active")
			|| $("keyboard-window").classList.contains("window-active")
		);
		let led = "led-gray";
		let title = "Keyboard free";

		if (__ws) {
			if (__online === null) {
				led = "led-red";
				title = (is_captured ? "Keyboard captured, HID offline" : "Keyboard free, HID offline");
			} else if (__online) {
				if (is_captured) {
					led = "led-green";
					title = "Keyboard captured";
				}
			} else {
				led = "led-yellow";
				title = (is_captured ? "Keyboard captured, inactive/busy" : "Keyboard free, inactive/busy");
			}
		} else {
			if (is_captured) {
				title = "Keyboard captured, PiKVM offline";
			}
		}
		$("hid-keyboard-led").className = led;
		$("hid-keyboard-led").title = title;
	};

	var __sendKey = function(code, state) {
		tools.debug("Keyboard: key", (state ? "pressed:" : "released:"), code);
		if ($("hid-keyboard-swap-cc-switch").checked) {
			if (code === "ControlLeft") {
				code = "CapsLock";
			} else if (code === "CapsLock") {
				code = "ControlLeft";
			}
		}
		let ev = {
			"event_type": "key",
			"event": {
				"key": code,
				"state": state,
				"finish": $("hid-keyboard-bad-link-switch").checked,
			},
		};
		if (__ws && !$("hid-mute-switch").checked) {
			__ws.sendHidEvent(ev);
		}
		delete ev.event.finish;
		__recordWsEvent(ev);
	};

	var __magic_key = null; // TODO
	var __magic_pressed = false;
	var __magic_pressed_ts = 0;
	var __magic_started = false;
	var __magic_fired_once = false;
	var __magic_mods = [];
	var __all_mods = {
		"ControlLeft": "Ctrl L",
		"ControlRight": "Ctrl R",
		"AltLeft": (tools.browser.is_mac ? "Option L" : "Alt L"),
		"AltRight": (tools.browser.is_mac ? "Option R" : "Alt R"),
		"ShiftLeft": "Shift L",
		"ShiftRight": "Shift R",
		"MetaLeft": (tools.browser.is_mac ? "Cmd L" : "Meta L"),
		"MetaRight": (tools.browser.is_mac ? "Cmd R" : "Meta R"),
	};

	var __isModifier = function(code) {
		return (code in __all_mods);
	};

	var __startMagic = function() {
		__magic_started = true;
		__drawMagicOverStream();
	};

	var __addNewMagicModifier = function(code) {
		if (!__magic_mods.includes(code)) {
			__magic_mods.push(code);
			__drawMagicOverStream();
			return true;
		}
		return false;
	};

	var __drawMagicOverStream = function(code=null) {
		let html = "";
		if (__magic_started) {
			html += "<span>Shortcut &rarr;</span>";
		}
		for (let mod of __magic_mods) {
			html += `<span>${__all_mods[mod]}</span>`;
		}
		if (code) {
			html += `<span>${code}</span>`;
		}
		$("stream-keyboard-magic").innerHTML = html;
	};

	var __releaseMagicModifiers = function() {
		while (__magic_mods.length > 0) {
			let code = __magic_mods.pop();
			__keypad.emitByCode(code, false, false);
		}
		__magic_started = false;
		__magic_fired_once = false;
		__magic_mods = [];
		setTimeout(function() {
			if (!__magic_started) {
				__drawMagicOverStream();
			}
		}, 100);
	};

	var __keyboardHandler = function(ev, state) {
		ev.preventDefault();
		if (__magic_key !== null && ev.code === __magic_key) {
			let now_ts = new Date().getTime();
			__magic_pressed = state;
			if (state) {
				if (__magic_started) {
					if (now_ts - __magic_pressed_ts < 250) {
						__releaseMagicModifiers();
					}
				} else {
					__startMagic();
				}
			} else if (__magic_fired_once) {
				__releaseMagicModifiers();
			}
			__magic_pressed_ts = now_ts;
		} else {
			if (__magic_started) {
				if (__isModifier(ev.code)) {
					if (state && __addNewMagicModifier(ev.code)) {
						__keypad.emitByKeyEvent(ev, state);
					}
				} else {
					__drawMagicOverStream(state ? ev.code : null);
					__keypad.emitByKeyEvent(ev, state);
					__magic_fired_once = true;
					if (!__magic_pressed) {
						__releaseMagicModifiers();
					}
				}
			} else {
				__keypad.emitByKeyEvent(ev, state);
			}
		}
	};

	__init__();
}
