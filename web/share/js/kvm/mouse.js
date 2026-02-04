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


import {tools, $} from "../tools.js";
import {Keypad} from "../keypad.js";


export function Mouse(__getGeometry, __recordWsEvent) {
	var self = this;

	/************************************************************************/

	var __ws = null;
	var __online = true;
	var __abs = true;

	var __keypad = null;

	var __timer = null;

	var __touch_pos = null;

	var __abs_pos = null;
	var __rel_deltas = [];

	var __init__ = function() {
		__keypad = new Keypad($("mouse-buttons"), __sendButton);

		tools.storage.bindSimpleSlider($("hid-mouse-sens-slider"), "hid.mouse.sens", 0.1, 1.9, 0.1, 1.0, function (value) {
			$("hid-mouse-sens-value").innerText = value.toFixed(1);
		});

		tools.storage.bindSimpleSlider($("hid-mouse-scroll-slider"), "hid.mouse.scroll_rate", 1, 25, 1, 5, function (value) {
			$("hid-mouse-scroll-value").innerText = value;
		});

		tools.storage.bindSimpleSlider($("hid-mouse-rate-slider"), "hid.mouse.rate", 10, 100, 10, 10, function (value) {
			$("hid-mouse-rate-value").innerText = value + " ms";
			if (__timer) {
				clearInterval(__timer);
			}
			__timer = setInterval(__sendPlannedMove, value);
		});

		document.addEventListener("pointerlockchange", __relativeCapturedHandler); // Only for relative
		document.addEventListener("pointerlockerror", __relativeCapturedHandler);

		$("stream-box").addEventListener("contextmenu", (ev) => ev.preventDefault());
		$("stream-box").addEventListener("mouseenter", __updateOnlineLeds);
		$("stream-box").addEventListener("mouseleave", __updateOnlineLeds);
		$("stream-box").addEventListener("mousedown", (ev) => __streamButtonHandler(ev, true));
		$("stream-box").addEventListener("mouseup", (ev) => __streamButtonHandler(ev, false));
		$("stream-box").addEventListener("mousemove", __streamMoveHandler);
		$("stream-box").addEventListener("wheel", __streamScrollHandler);

		$("stream-box").addEventListener("touchstart", __streamTouchStartHandler);
		$("stream-box").addEventListener("touchmove", __streamTouchMoveHandler);
		$("stream-box").addEventListener("touchend", __streamTouchEndHandler);

		tools.storage.bindSimpleSwitch($("hid-mouse-squash-switch"), "hid.mouse.squash", true);
		tools.storage.bindSimpleSwitch($("hid-mouse-reverse-scrolling-y-switch"), "hid.mouse.reverse_scrolling", false);
		tools.storage.bindSimpleSwitch($("hid-mouse-reverse-scrolling-x-switch"), "hid.mouse.reverse_panning", false);
		let cumulative_scrolling = !(tools.browser.is_firefox && !tools.browser.is_mac);
		tools.storage.bindSimpleSwitch($("hid-mouse-cumulative-scrolling-switch"), "hid.mouse.cumulative_scrolling", cumulative_scrolling);
		tools.storage.bindSimpleSwitch($("hid-mouse-dot-switch"), "hid.mouse.dot", true, __updateOnlineLeds);

		__updateOnlineLeds();
	};

	/************************************************************************/

	self.setSocket = function(ws) {
		__ws = ws;
		if (!__abs && __isRelativeCaptured()) {
			document.exitPointerLock();
		}
		__updateOnlineLeds();
	};

	self.setState = function(online, abs, hid_online, hid_busy) {
		if (!hid_online) {
			__online = null;
		} else {
			__online = (online && !hid_busy);
		}
		if (!__abs && abs && __isRelativeCaptured()) {
			document.exitPointerLock();
		}
		if (__abs && !abs) {
			__touch_pos = null;
			__rel_deltas = [];
		}
		__abs = abs;
		__updateOnlineLeds();
	};

	self.releaseAll = function() {
		__keypad.releaseAll();
	};

	var __updateOnlineLeds = function() {
		let is_captured;
		if (__abs) {
			is_captured = (
				tools.browser.is_mobile
				|| $("stream-box").matches("#stream-box:hover")
			);
			let dot = $("hid-mouse-dot-switch").checked;
			$("stream-box").classList.toggle("stream-box-mouse-dot", (__ws && is_captured && dot));
			$("stream-box").classList.toggle("stream-box-mouse-none", (__ws && is_captured && !dot));
			$("stream-box").classList.toggle("stream-box-mouse-waitrel", false);
		} else {
			is_captured = __isRelativeCaptured();
			$("stream-box").classList.toggle("stream-box-mouse-dot", false);
			$("stream-box").classList.toggle("stream-box-mouse-none", false);
			$("stream-box").classList.toggle("stream-box-mouse-waitrel", (__ws && !is_captured));
		}

		let led = "led-gray";
		let title = "Mouse free";
		if (__ws) {
			if (__online === null) {
				led = "led-red";
				title = (is_captured ? "Mouse captured, HID offline" : "Mouse free, HID offline");
			} else if (__online) {
				if (is_captured) {
					led = "led-green";
					title = "Mouse captured";
				}
			} else {
				led = "led-yellow";
				title = (is_captured ? "Mouse captured, inactive/busy" : "Mouse free, inactive/busy");
			}
		} else {
			if (is_captured) {
				title = "Mouse captured, PiKVM offline";
			}
		}
		$("hid-mouse-led").className = led;
		$("hid-mouse-led").title = title;
	};

	var __isRelativeCaptured = function() {
		return (document.pointerLockElement === $("stream-box"));
	};

	var __relativeCapturedHandler = function() {
		tools.info("Relative mouse", (__isRelativeCaptured() ? "captured" : "released"), "by pointer lock");
		__updateOnlineLeds();
	};

	var __streamButtonHandler = function(ev, state) {
		// https://www.w3schools.com/jsref/event_button.asp
		ev.preventDefault();
		if (__abs || __isRelativeCaptured()) {
			switch (ev.button) {
				case 0: __keypad.emit("left", state); break;
				case 2: __keypad.emit("right", state); break;
				case 1: __keypad.emit("middle", state); break;
				case 3: __keypad.emit("up", state); break;
				case 4: __keypad.emit("down", state); break;
			}
		} else if (!__abs && !__isRelativeCaptured() && !state) {
			$("stream-box").requestPointerLock();
		}
	};

	var __streamTouchStartHandler = function(ev) {
		ev.preventDefault();
		let pos = __getTouchPosition(ev, 0);
		if (__abs && ev.touches.length === 1) {
			__abs_pos = pos;
			__sendPlannedMove();
		} else if (!__abs) {
			__touch_pos = pos;
			__abs_pos = null;
		}
	};

	var __streamTouchMoveHandler = function(ev) {
		ev.preventDefault();
		let pos = __getTouchPosition(ev, 0);
		if (ev.touches.length === 1) {
			if (__abs) {
				__abs_pos = pos;
			} else if (__touch_pos !== null) {
				__sendOrPlanRelativeMove({
					"x": (pos.x - __touch_pos.x),
					"y": (pos.y - __touch_pos.y),
				});
				__touch_pos = pos;
			}
		} else if (ev.touches.length >= 2) {
			if (__touch_pos === null) {
				__touch_pos = pos;
			} else {
				let dx = __touch_pos.x - pos.x;
				let dy = __touch_pos.y - pos.y;
				if (Math.abs(dx) < 15) {
					dx = 0;
				}
				if (Math.abs(dy) < 15) {
					dy = 0;
				}
				if (dx || dy) {
					__sendScroll({"x": dx, "y": dy});
					__touch_pos = null;
				}
			}
			__abs_pos = null;
		}
	};

	var __streamTouchEndHandler = function(ev) {
		ev.preventDefault();
		__sendPlannedMove();
		__touch_pos = null;
	};

	var __getTouchPosition = function(ev, index) {
		if (ev.touches[index].target && ev.touches[index].target.getBoundingClientRect) {
			let rect = ev.touches[index].target.getBoundingClientRect();
			return {
				"x": Math.round(ev.touches[index].clientX - rect.left),
				"y": Math.round(ev.touches[index].clientY - rect.top),
			};
		}
		return null;
	};

	var __streamMoveHandler = function(ev) {
		if (__abs) {
			let rect = ev.target.getBoundingClientRect();
			__abs_pos = {
				"x": Math.max(Math.round(ev.clientX - rect.left), 0),
				"y": Math.max(Math.round(ev.clientY - rect.top), 0),
			};
		} else if (__isRelativeCaptured()) {
			__sendOrPlanRelativeMove({
				"x": ev.movementX,
				"y": ev.movementY,
			});
		}
	};

	var __scroll_delta = {"x": 0, "y": 0};

	var __streamScrollHandler = function(ev) {
		// https://learn.javascript.ru/mousewheel
		// https://stackoverflow.com/a/24595588
		ev.preventDefault();
		if (!__abs && !__isRelativeCaptured()) {
			return;
		}
		let delta = {"x": 0, "y": 0};
		if ($("hid-mouse-cumulative-scrolling-switch").checked) {
			let fix = (tools.browser.is_mac ? 5 : 1);
			for (let [dir, cur] of [["x", ev.deltaX], ["y", ev.deltaY]]) {
				let prev = __scroll_delta[dir];
				if (prev && Math.sign(prev) !== Math.sign(cur)) {
					delta[dir] = prev;
					__scroll_delta[dir] = 0;
				} else {
					__scroll_delta[dir] += cur * fix;
					cur = __scroll_delta[dir];
					if (Math.abs(cur) >= 100) {
						delta[dir] = cur;
						__scroll_delta[dir] = 0;
					}
				}
			}
		} else {
			delta.x = ev.deltaX;
			delta.y = ev.deltaY;
		}
		__sendScroll(delta);
	};

	/************************************************************************/

	var __sendOrPlanRelativeMove = function(delta) {
		let sens = $("hid-mouse-sens-slider").valueAsNumber;
		delta = {
			"x": Math.min(Math.max(-127, Math.floor(delta.x * sens)), 127),
			"y": Math.min(Math.max(-127, Math.floor(delta.y * sens)), 127),
		};
		if (delta.x || delta.y) {
			if ($("hid-mouse-squash-switch").checked) {
				__rel_deltas.push(delta);
			} else {
				tools.debug("Mouse: relative:", delta);
				__sendEvent("mouse_relative", {"delta": delta});
			}
		}
	};

	var __sendPlannedMove = function() {
		if (__abs) {
			if (__abs_pos !== null) {
				let geo = __getGeometry();
				let to = {
					"x": tools.remap(__abs_pos.x - geo.x, 0, geo.width - 1, -32768, 32767),
					"y": tools.remap(__abs_pos.y - geo.y, 0, geo.height - 1, -32768, 32767),
				};
				tools.debug("Mouse: abs:", to);
				__sendEvent("mouse_move", {"to": to});
			}
		} else if (__rel_deltas.length) {
			tools.debug("Mouse: relative:", __rel_deltas);
			__sendEvent("mouse_relative", {"delta": __rel_deltas, "squash": true});
		}
		__abs_pos = null;
		__rel_deltas = [];
	};

	var __sendButton = function(button, state) {
		tools.debug("Mouse: button", (state ? "pressed:" : "released:"), button);
		__sendPlannedMove();
		__sendEvent("mouse_button", {"button": button, "state": state});
	};

	var __sendScroll = function(delta) {
		// Send a single scroll step defined by rate
		let rate = $("hid-mouse-scroll-slider").valueAsNumber;
		for (let dir of ["x", "y"]) {
			if (delta[dir]) {
				delta[dir] = Math.sign(delta[dir]) * (-rate);
				if ($(`hid-mouse-reverse-scrolling-${dir}-switch`).checked) {
					delta[dir] *= -1;
				}
			}
		}
		if (delta.x || delta.y) {
			tools.debug("Mouse: scrolled:", delta);
			__sendEvent("mouse_wheel", {"delta": delta});
		}
	};

	var __sendEvent = function(ev_type, ev) {
		ev = {"event_type": ev_type, "event": ev};
		if (__ws && !$("hid-mute-switch").checked) {
			__ws.sendHidEvent(ev);
		}
		__recordWsEvent(ev);
	};

	__init__();
}
