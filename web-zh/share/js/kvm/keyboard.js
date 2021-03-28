/*****************************************************************************
#                                                                            #
#    KVMD - The main Pi-KVM daemon.                                          #
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
*****************************************************************************/


import {tools, $, $$$} from "../tools.js";
import {Keypad} from "../keypad.js";


export function Keyboard(record_callback) {
	var self = this;

	/************************************************************************/

	var __record_callback = record_callback;

	var __ws = null;
	var __online = true;

	var __keypad = null;
	var __fix_mac_cmd = false;

	var __init__ = function() {
		__keypad = new Keypad("div#keyboard-window", __sendKey);

		$("hid-keyboard-led").title = "Keyboard free";

		$("keyboard-window").onkeydown = (event) => __keyboardHandler(event, true);
		$("keyboard-window").onkeyup = (event) => __keyboardHandler(event, false);
		$("keyboard-window").onfocus = __updateOnlineLeds;
		$("keyboard-window").onblur = __updateOnlineLeds;

		$("stream-window").onkeydown = (event) => __keyboardHandler(event, true);
		$("stream-window").onkeyup = (event) => __keyboardHandler(event, false);
		$("stream-window").onfocus = __updateOnlineLeds;
		$("stream-window").onblur = __updateOnlineLeds;

		window.addEventListener("focusin", __updateOnlineLeds);
		window.addEventListener("focusout", __updateOnlineLeds);

		if (tools.browser.is_mac) {
			// https://bugs.chromium.org/p/chromium/issues/detail?id=28089
			// https://bugzilla.mozilla.org/show_bug.cgi?id=1299553
			tools.info("Keyboard: enabled Fix-Mac-CMD");
			__fix_mac_cmd = true;
		}
	};

	/************************************************************************/

	self.setSocket = function(ws) {
		if (ws !== __ws) {
			self.releaseAll();
			__ws = ws;
		}
		__updateOnlineLeds();
	};

	self.setState = function(state, hid_online, hid_busy) {
		if (!hid_online) {
			__online = null;
		} else {
			__online = (state.online && !hid_busy);
		}
		__updateOnlineLeds();

		for (let led of ["caps", "scroll", "num"]) {
			for (let el of $$$(`.hid-keyboard-${led}-led`)) {
				if (state.leds[led]) {
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
		__keyboardHandler({code: code}, state);
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
				title = "Keyboard captured, Pi-KVM offline";
			}
		}
		$("hid-keyboard-led").className = led;
		$("hid-keyboard-led").title = title;
	};

	var __keyboardHandler = function(event, state) {
		if (event.preventDefault) {
			event.preventDefault();
		}
		if (!event.repeat) {
			// https://bugs.chromium.org/p/chromium/issues/detail?id=28089
			// https://bugzilla.mozilla.org/show_bug.cgi?id=1299553
			__keypad.emit(event.code, state, __fix_mac_cmd);
		}
	};

	var __sendKey = function(code, state) {
		tools.debug("Keyboard: key", (state ? "pressed:" : "released:"), code);
		let event = {
			"event_type": "key",
			"event": {"key": code, "state": state},
		};
		if (__ws) {
			__ws.send(JSON.stringify(event));
		}
		__record_callback(event);
	};

	__init__();
}
