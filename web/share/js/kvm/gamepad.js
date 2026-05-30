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


export function Gamepad(__recordWsEvent) {
	var self = this;

	/************************************************************************/

	var __ws = null;
	var __enabled = false; // HID online and a gamepad device present on the target
	var __index = null;    // Index of the active browser gamepad
	var __raf = null;
	var __last = null;     // Last sent snapshot (JSON) for change detection

	var __NEUTRAL = {"buttons": 0, "lx": 128, "ly": 128, "rx": 128, "ry": 128, "lt": 0, "rt": 0, "hat": 8};

	// Browser "standard gamepad" button index -> our HID report button bit.
	// Triggers (6, 7) become analog axes; the D-pad (12..15) becomes the hat.
	var __BUTTON_MAP = [
		[0, 0], [1, 1], [2, 2], [3, 3], // A B X Y
		[4, 4], [5, 5],                 // LB RB
		[8, 6], [9, 7],                 // Back Start
		[10, 8], [11, 9],               // L3 R3
		[16, 10],                       // Guide
	];

	var __init__ = function() {
		window.addEventListener("gamepadconnected", __connectHandler);
		window.addEventListener("gamepaddisconnected", __disconnectHandler);
	};

	/************************************************************************/

	self.setSocket = function(ws) {
		__ws = ws;
		if (!__ws) {
			__last = null;
		}
		__updateLoop();
	};

	self.setState = function(online, hid_online, hid_busy) {
		__enabled = !!(hid_online && online && !hid_busy);
		__updateLoop();
	};

	self.releaseAll = function() {
		if (__ws) {
			__last = null;
			__send(__NEUTRAL);
		}
	};

	/************************************************************************/

	var __connectHandler = function(ev) {
		if (__index === null) {
			__index = ev.gamepad.index;
			tools.info("Gamepad: connected:", ev.gamepad.id);
			__updateLoop();
		}
	};

	var __disconnectHandler = function(ev) {
		if (ev.gamepad.index === __index) {
			tools.info("Gamepad: disconnected:", ev.gamepad.id);
			__index = null;
			self.releaseAll();
			__updateLoop();
		}
	};

	var __updateLoop = function() {
		if (__enabled && __ws && __index !== null) {
			if (__raf === null) {
				__raf = window.requestAnimationFrame(__poll);
			}
		} else if (__raf !== null) {
			window.cancelAnimationFrame(__raf);
			__raf = null;
		}
	};

	var __poll = function() {
		__raf = null;
		if (__enabled && __ws && __index !== null) {
			let pads = navigator.getGamepads();
			let gp = (pads ? pads[__index] : null);
			if (gp) {
				__send(__readPad(gp));
			}
			__raf = window.requestAnimationFrame(__poll);
		}
	};

	/************************************************************************/

	var __readPad = function(gp) {
		let buttons = 0;
		for (let [bi, bit] of __BUTTON_MAP) {
			if (gp.buttons[bi] && gp.buttons[bi].pressed) {
				buttons |= (1 << bit);
			}
		}
		return {
			"buttons": buttons,
			"lx": __axis(gp.axes[0]), "ly": __axis(gp.axes[1]),
			"rx": __axis(gp.axes[2]), "ry": __axis(gp.axes[3]),
			"lt": __trigger(gp.buttons[6]), "rt": __trigger(gp.buttons[7]),
			"hat": __hat(__down(gp, 12), __down(gp, 13), __down(gp, 14), __down(gp, 15)),
		};
	};

	var __down = function(gp, index) {
		return !!(gp.buttons[index] && gp.buttons[index].pressed);
	};

	var __axis = function(value) {
		// -1.0..1.0 -> 0..255 (center 128)
		return Math.min(255, Math.max(0, Math.round(((value || 0) + 1) * 127.5)));
	};

	var __trigger = function(button) {
		// 0.0..1.0 -> 0..255
		let value = (button ? button.value : 0);
		return Math.min(255, Math.max(0, Math.round(value * 255)));
	};

	var __hat = function(up, down, left, right) {
		if (up && right) { return 1; }
		if (down && right) { return 3; }
		if (down && left) { return 5; }
		if (up && left) { return 7; }
		if (up) { return 0; }
		if (right) { return 2; }
		if (down) { return 4; }
		if (left) { return 6; }
		return 8;
	};

	/************************************************************************/

	var __send = function(state) {
		let snapshot = JSON.stringify(state);
		if (snapshot === __last) {
			return; // Only send when something actually changed
		}
		__last = snapshot;
		let ev = {"event_type": "gamepad", "event": state};
		if (__ws && !$("hid-mute-switch").checked) {
			__ws.sendHidEvent(ev);
		}
		__recordWsEvent(ev);
	};

	__init__();
}
