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


import {tools} from "../tools.js";


export function VuMeter(el_progress) {
	var self = this;

	/************************************************************************/

	var __FLOOR = -60; // The bottom of the scale, dBFS
	var __RELEASE = 10; // The meter falls this many dB per frame, 500dB/s
	var __CLIP = 0.99; // Everything above is a clipped sample
	var __CLIP_HOLD = 40; // ... and the warning stays for 0.8s
	var __PERIOD = 100; // The meter is updated 100 times per second

	var __ctx = null;
	var __resuming = false;
	var __src = null;
	var __analyser = null;
	var __buf = null;
	var __timer = null;

	var __sum = 0;
	var __count = 0;
	var __peak = 0;
	var __level_db = __FLOOR;
	var __clipped = 0;

	/************************************************************************/

	self.attach = function(track) {
		self.detach(); // Also draws some initial value on the progress bar
		try {
			// The bar jumps between the updates by default, this blends the steps
			// without making the meter lag behind the sound
			el_progress.querySelector(".progress-value").style.transition = `width ${__PERIOD}ms linear`;

			__ctx = new AudioContext();

			// The source must be kept referenced, otherwise it can be garbage collected
			__src = __ctx.createMediaStreamSource(new MediaStream([track]));

			// 1024 samples is about 21ms at 48kHz: the same window the direct mode
			// measures, so both modes react to the sound in the same way
			__analyser = __ctx.createAnalyser();
			__analyser.fftSize = 1024;

			__buf = new Float32Array(__analyser.fftSize);
			__src.connect(__analyser); // Not connected to the destination: nothing is played

			__timer = setInterval(__poll, __PERIOD);
			__poll();
		} catch (err) {
			tools.error("VuMeter: Can't attach to the track:", err);
			self.detach();
		}
	};

	self.detach = function() {
		if (__timer !== null) {
			clearInterval(__timer);
			__timer = null;
		}

		for (let close of [
			() => { if (__src !== null) { __src.disconnect(); } },
			() => { if (__analyser !== null) { __analyser.disconnect(); } },
			() => { if (__ctx !== null) { __ctx.close(); } },
		]) {
			try {
				close();
			} catch (err) {
				tools.error("VuMeter: Can't detach from the track:", err);
			}
		}
		__resuming = false;
		__src = null;
		__analyser = null;
		__buf = null;
		__ctx = null;

		__sum = 0;
		__count = 0;
		__peak = 0;
		__level_db = __FLOOR;
		__clipped = 0;
		__draw();
	};

	/************************************************************************/

	var __poll = function() {
		if (__ctx === null) {
			return;
		}
		if (__ctx.state !== "running") {
			if (!__resuming) { // One pending resume is enough
				__resuming = true;
				__ctx.resume().catch(function(err) {
					tools.error("VuMeter: Can't resume the context:", err);
				}).finally(function() {
					__resuming = false;
				});
			}
			return;
		}
		__analyser.getFloatTimeDomainData(__buf);
		__accumulate(__buf);
		__update();
	};

	var __accumulate = function(samples) {
		for (let i = 0; i < samples.length; ++i) {
			let value = Math.abs(samples[i]);
			__sum += value * value;
			if (value > __peak) {
				__peak = value;
			}
		}
		__count += samples.length;
	};

	var __update = function() {
		if (__count === 0) {
			return;
		}
		let rms = Math.sqrt(__sum / __count);
		let db = (rms > 0 ? 20 * Math.log10(rms) : __FLOOR);
		// The level jumps up instantly and falls smoothly, like any audio meter
		__level_db = Math.max(db, __level_db - __RELEASE);
		if (__peak >= __CLIP) {
			__clipped = __CLIP_HOLD;
		} else if (__clipped > 0) {
			__clipped -= 1;
		}
		__sum = 0;
		__count = 0;
		__peak = 0;
		__draw();
	};

	var __draw = function() {
		let label = "Clip!";
		let percent = 100;
		if (__clipped <= 0) { // Not clipped
			if (__level_db <= __FLOOR) { // Don't calculate
				label = `${__FLOOR} dB`;
				percent = 0;
			} else {
				label = `${Math.round(__level_db)} dB`;
				percent = Math.max(0, Math.min(100, (__level_db - __FLOOR) / -__FLOOR * 100));
			}
		}
		tools.progress.setValue(el_progress, label, percent);
	};
}
