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
	var __enabled = false;
	var __connected = {};  // {browser_index: true} for all connected gamepads
	var __interval = null;
	var __last = {};       // {pad_index: "json"} for change detection
	var __pollMs = 4;      // 4ms = 250Hz (browser minimum reliable interval)

	var __NEUTRAL = {"buttons": 0, "lx": 128, "ly": 128, "rx": 128, "ry": 128, "lt": 0, "rt": 0, "hat": 8};

	// Browser "standard gamepad" button index -> our HID report button bit.
	// Triggers (6, 7) become analog axes; the D-pad (12..15) becomes the hat.
	var __BUTTON_MAP = [
		[0, 0], [1, 1], [2, 2], [3, 3], // A B X Y
		[4, 4], [5, 5],                 // LB RB
		[8, 6], [9, 7],                 // Back Start
		[10, 8], [11, 9],               // L3 R3
		[16, 10],                       // Guide
		[17, 11],                       // Capture (Switch) / Mute (DualSense)
	];

	// Empirical layout for a Switch Pro Controller exposed with a
	// non-standard mapping (Linux hid-nintendo through Chrome):
	// 0=Y 1=B 2=A 3=X 4=L 5=R 6=ZL 7=ZR 8=minus 9=plus 10=L3 11=R3
	// 12=home 13=capture; the d-pad is the hat axis pair.
	var __NINTENDO_RAW_MAP = [
		[1, 0], [2, 1], [0, 2], [3, 3], // bottom right left top
		[4, 4], [5, 5],
		[8, 6], [9, 7],
		[10, 8], [11, 9],
		[12, 10],                       // Home
		[13, 11],                       // Capture
	];

	var __mapFor = function(gp) {
		if (gp.mapping !== "standard" && /pro controller|joy-con|057e/i.test(gp.id)) {
			return __NINTENDO_RAW_MAP;
		}
		return __BUTTON_MAP;
	};

	// UI throttle: update visuals at ~30fps, not 250Hz
	var __uiInterval = null;
	var __uiMs = 33;  // ~30fps
	var __lastUiState = null;  // last state rendered to SVG (for the primary pad)

	// Stick center positions (match SVG layout)
	var __LSTICK_CX = 155;
	var __LSTICK_CY = 105;
	var __RSTICK_CX = 235;
	var __RSTICK_CY = 120;
	var __STICK_RANGE = 14;  // max pixel deflection from center

	var __init__ = function() {
		window.addEventListener("gamepadconnected", __connectHandler);
		window.addEventListener("gamepaddisconnected", __disconnectHandler);
	};

	/************************************************************************/

	var __FACE_LABELS = {
		"switchpro": {"a": "B", "b": "A", "x": "Y", "y": "X", "back": "−", "start": "+"},
		"dualsense": {"a": "✕", "b": "○", "x": "□", "y": "△", "back": "Share", "start": "Opt"},
		"": {"a": "A", "b": "B", "x": "X", "y": "Y", "back": "Back", "start": "Start"},
	};

	self.setMode = function(mode) {
		// The SVG buttons are positional; relabel them for the emulated pad
		let labels = (__FACE_LABELS[mode] || __FACE_LABELS[""]);
		for (let key of ["a", "b", "x", "y", "back", "start"]) {
			let el = $("gp-label-" + key);
			if (el) {
				el.textContent = labels[key];
			}
		}
	};

	self.setSocket = function(ws) {
		__ws = ws;
		if (!__ws) {
			__last = {};
		}
		__updateLoop();
	};

	self.setState = function(online, hid_online, hid_busy) {
		__enabled = !!(hid_online && online && !hid_busy);
		__updateLoop();
		__updateLed(online, hid_online, hid_busy);
	};

	self.releaseAll = function() {
		if (__ws) {
			__last = {};
			for (let idx in __connected) {
				__send(parseInt(idx), __NEUTRAL);
			}
		}
	};

	/************************************************************************/

	var __updateLed = function(online, hid_online, hid_busy) {
		let el = $("hid-gamepad-led");
		if (!el) return;
		if (online && hid_online && !hid_busy) {
			el.className = (Object.keys(__connected).length > 0 ? "led-green" : "led-yellow");
			el.title = (Object.keys(__connected).length > 0 ? "Gamepad connected" : "Gamepad ready, no controller");
		} else if (online) {
			el.className = "led-yellow";
			el.title = "Gamepad inactive/busy";
		} else {
			el.className = "led-gray";
			el.title = "Gamepad offline";
		}
	};

	var __connectHandler = function(ev) {
		__connected[ev.gamepad.index] = true;
		tools.info("Gamepad: connected [" + ev.gamepad.index + "]:", ev.gamepad.id);
		__updateStatusText(ev.gamepad.index, ev.gamepad.id, ev.gamepad.mapping);
		__updateLoop();
	};

	var __disconnectHandler = function(ev) {
		let idx = ev.gamepad.index;
		if (__connected[idx]) {
			tools.info("Gamepad: disconnected [" + idx + "]:", ev.gamepad.id);
			delete __connected[idx];
			if (__ws) {
				__send(idx, __NEUTRAL);
			}
			delete __last[idx];
			__updateStatusDisconnected();
			__updateLoop();
		}
	};

	var __updateLoop = function() {
		let active = __enabled && __ws && Object.keys(__connected).length > 0;
		if (active && __interval === null) {
			__interval = setInterval(__poll, __pollMs);
			__uiInterval = setInterval(__updateUI, __uiMs);
		} else if (!active && __interval !== null) {
			clearInterval(__interval);
			__interval = null;
			if (__uiInterval !== null) {
				clearInterval(__uiInterval);
				__uiInterval = null;
			}
			__resetUI();
		}
	};

	var __poll = function() {
		if (!__enabled || !__ws) return;
		let pads = navigator.getGamepads();
		if (!pads) return;
		for (let idx in __connected) {
			let i = parseInt(idx);
			let gp = pads[i];
			if (gp && i < 4) {
				__send(i, __readPad(gp));
			}
		}
	};

	/************************************************************************/

	var __readPad = function(gp) {
		let buttons = 0;
		for (let [bi, bit] of __mapFor(gp)) {
			if (gp.buttons[bi] && gp.buttons[bi].pressed) {
				buttons |= (1 << bit);
			}
		}
		let up = false, down = false, left = false, right = false;
		if (gp.mapping === "standard") {
			up = __down(gp, 12); down = __down(gp, 13); left = __down(gp, 14); right = __down(gp, 15);
		} else if (gp.axes.length >= 6) {
			// Non-standard mappings put other buttons at indexes 12-15 (e.g.
			// home, capture) and expose the d-pad as a hat axis pair
			// (ABS_HAT0X/Y), typically the last two axes.
			let hx = gp.axes[gp.axes.length - 2] || 0;
			let hy = gp.axes[gp.axes.length - 1] || 0;
			up = (hy < -0.5);
			down = (hy > 0.5);
			left = (hx < -0.5);
			right = (hx > 0.5);
		}
		return {
			"buttons": buttons,
			"lx": __axis(gp.axes[0]), "ly": __axis(gp.axes[1]),
			"rx": __axis(gp.axes[2]), "ry": __axis(gp.axes[3]),
			"lt": __trigger(gp.buttons[6]), "rt": __trigger(gp.buttons[7]),
			"hat": __hat(up, down, left, right),
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

	var __send = function(padIndex, state) {
		let snapshot = JSON.stringify(state);
		if (snapshot === __last[padIndex]) {
			return;
		}
		__last[padIndex] = snapshot;
		// Use 10-byte binary format: index(1) + buttons(2) + lx,ly,rx,ry,lt,rt,hat(7)
		if (__ws && !$("hid-mute-switch").checked) {
			let buf = new ArrayBuffer(10);
			let dv = new DataView(buf);
			dv.setUint8(0, padIndex & 0x03);
			dv.setUint16(1, state.buttons, false); // big-endian
			dv.setUint8(3, state.lx);
			dv.setUint8(4, state.ly);
			dv.setUint8(5, state.rx);
			dv.setUint8(6, state.ry);
			dv.setUint8(7, state.lt);
			dv.setUint8(8, state.rt);
			dv.setUint8(9, state.hat);
			__ws.sendHidBin(6, new Uint8Array(buf));
		}
		let ev = {"event_type": "gamepad", "event": Object.assign({"index": padIndex}, state)};
		__recordWsEvent(ev);
	};

	/************************************************************************/
	// UI update functions (throttled to ~30fps via __uiInterval)
	/************************************************************************/

	var __updateStatusText = function(index, name, mapping) {
		let el_status = $("hid-gamepad-status");
		if (el_status) {
			el_status.innerHTML = "<b>" + __escHtml(__shortName(name)) + "</b> (Pad " + index + ")";
		}
		let el_disc = $("gamepad-info-disconnected");
		let el_conn = $("gamepad-info-connected");
		let el_name = $("gamepad-info-name");
		let el_slot = $("gamepad-info-slot");
		if (el_disc && el_conn) {
			el_disc.classList.add("hidden");
			el_conn.classList.remove("hidden");
			if (el_name) el_name.textContent = __shortName(name);
			if (el_slot) {
				el_slot.textContent = ("(Pad " + index + ")"
					+ (mapping === "standard" ? "" : " ⚠ non-standard browser mapping: buttons may be scrambled"));
			}
		}
	};

	var __updateStatusDisconnected = function() {
		// If other pads still connected, show the first one
		let keys = Object.keys(__connected);
		if (keys.length > 0) {
			let idx = parseInt(keys[0]);
			let pads = navigator.getGamepads();
			if (pads && pads[idx]) {
				__updateStatusText(idx, pads[idx].id, pads[idx].mapping);
				return;
			}
		}
		let el_status = $("hid-gamepad-status");
		if (el_status) {
			el_status.innerHTML = "<i>No controller</i>";
		}
		let el_disc = $("gamepad-info-disconnected");
		let el_conn = $("gamepad-info-connected");
		if (el_disc && el_conn) {
			el_disc.classList.remove("hidden");
			el_conn.classList.add("hidden");
		}
	};

	var __shortName = function(name) {
		// Trim long gamepad IDs to something readable
		if (!name) return "Unknown";
		if (name.length > 40) {
			return name.substring(0, 37) + "...";
		}
		return name;
	};

	var __escHtml = function(str) {
		let div = document.createElement("div");
		div.appendChild(document.createTextNode(str));
		return div.innerHTML;
	};

	var __updateUI = function() {
		// Read the primary connected pad and update SVG elements
		let pads = navigator.getGamepads();
		if (!pads) return;

		let keys = Object.keys(__connected);
		if (keys.length === 0) return;

		let idx = parseInt(keys[0]);
		let gp = pads[idx];
		if (!gp) return;

		let state = __readPad(gp);

		let el_raw = $("gamepad-raw");
		if (el_raw) {
			let parts = [];
			for (let i = 0; i < gp.buttons.length; i++) {
				if (gp.buttons[i] && gp.buttons[i].pressed) {
					parts.push("btn" + i);
				}
			}
			for (let i = 0; i < gp.axes.length; i++) {
				if (Math.abs(gp.axes[i] || 0) > 0.5) {
					parts.push("ax" + i + "=" + gp.axes[i].toFixed(1));
				}
			}
			el_raw.textContent = (parts.length ? ("Raw browser input: " + parts.join(" ")) : "");
		}

		// Quick check: skip if nothing changed
		let snap = JSON.stringify(state);
		if (snap === __lastUiState) return;
		__lastUiState = snap;

		// Button highlights
		// Bit mapping: 0=A, 1=B, 2=X, 3=Y, 4=LB, 5=RB, 6=Back, 7=Start, 8=L3, 9=R3, 10=Guide
		__setBtnActive("gp-btn-a", state.buttons & (1 << 0));
		__setBtnActive("gp-btn-b", state.buttons & (1 << 1));
		__setBtnActive("gp-btn-x", state.buttons & (1 << 2));
		__setBtnActive("gp-btn-y", state.buttons & (1 << 3));
		__setBtnActive("gp-btn-lb", state.buttons & (1 << 4));
		__setBtnActive("gp-btn-rb", state.buttons & (1 << 5));
		__setBtnActive("gp-btn-back", state.buttons & (1 << 6));
		__setBtnActive("gp-btn-start", state.buttons & (1 << 7));
		__setBtnActive("gp-btn-l3", state.buttons & (1 << 8));
		__setBtnActive("gp-btn-r3", state.buttons & (1 << 9));
		__setBtnActive("gp-btn-guide", state.buttons & (1 << 10));
		__setBtnActive("gp-btn-capture", state.buttons & (1 << 11));

		// Analog sticks: lx/ly/rx/ry are 0-255, center 128
		let lx_norm = (state.lx - 128) / 128;  // -1..1
		let ly_norm = (state.ly - 128) / 128;
		let rx_norm = (state.rx - 128) / 128;
		let ry_norm = (state.ry - 128) / 128;

		__setStickPos("gp-lstick-dot", __LSTICK_CX, __LSTICK_CY, lx_norm, ly_norm);
		__setStickPos("gp-rstick-dot", __RSTICK_CX, __RSTICK_CY, rx_norm, ry_norm);

		// Triggers: 0-255 -> bar width 0-50
		let lt_width = Math.round((state.lt / 255) * 50);
		let rt_width = Math.round((state.rt / 255) * 50);
		__setTriggerWidth("gp-lt-fill", lt_width);
		__setTriggerWidth("gp-rt-fill", rt_width);

		// D-pad via hat value
		__updateDpad(state.hat);
	};

	var __setBtnActive = function(id, pressed) {
		let el = $(id);
		if (el) {
			if (pressed) {
				el.classList.add("gp-active");
			} else {
				el.classList.remove("gp-active");
			}
		}
	};

	var __setStickPos = function(id, cx, cy, nx, ny) {
		let el = $(id);
		if (el) {
			el.setAttribute("cx", cx + nx * __STICK_RANGE);
			el.setAttribute("cy", cy + ny * __STICK_RANGE);
		}
	};

	var __setTriggerWidth = function(id, width) {
		let el = $(id);
		if (el) {
			el.setAttribute("width", width);
		}
	};

	var __updateDpad = function(hat) {
		// hat: 0=N, 1=NE, 2=E, 3=SE, 4=S, 5=SW, 6=W, 7=NW, 8=neutral
		let up = (hat === 0 || hat === 1 || hat === 7);
		let right = (hat === 1 || hat === 2 || hat === 3);
		let down = (hat === 3 || hat === 4 || hat === 5);
		let left = (hat === 5 || hat === 6 || hat === 7);

		__setDpadDir("gp-dpad-up", up);
		__setDpadDir("gp-dpad-down", down);
		__setDpadDir("gp-dpad-left", left);
		__setDpadDir("gp-dpad-right", right);
	};

	var __setDpadDir = function(id, active) {
		let el = $(id);
		if (el) {
			if (active) {
				el.classList.add("gp-dpad-active");
				el.setAttribute("opacity", "1");
			} else {
				el.classList.remove("gp-dpad-active");
				el.setAttribute("opacity", "0");
			}
		}
	};

	var __resetUI = function() {
		// Reset all visual elements to neutral
		__lastUiState = null;
		let btnIds = [
			"gp-btn-a", "gp-btn-b", "gp-btn-x", "gp-btn-y",
			"gp-btn-lb", "gp-btn-rb", "gp-btn-back", "gp-btn-start",
			"gp-btn-l3", "gp-btn-r3", "gp-btn-guide", "gp-btn-capture",
		];
		for (let id of btnIds) {
			__setBtnActive(id, false);
		}
		__setStickPos("gp-lstick-dot", __LSTICK_CX, __LSTICK_CY, 0, 0);
		__setStickPos("gp-rstick-dot", __RSTICK_CX, __RSTICK_CY, 0, 0);
		__setTriggerWidth("gp-lt-fill", 0);
		__setTriggerWidth("gp-rt-fill", 0);
		__updateDpad(8);
	};

	__init__();
}
