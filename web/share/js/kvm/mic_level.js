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


// The microphone level meter. The mic itself belongs to Janus, so the meter
// just listens to the track that is being sent to the host.
export function MicLevel() {
	var self = this;

	/************************************************************************/

	var __FLOOR = -60; // The bottom of the scale, dBFS
	var __RELEASE = 2; // The meter falls this many dB per frame, 100dB/s
	var __CLIP = 0.99; // Everything above is a clipped sample
	var __CLIP_HOLD = 40; // ... and the warning stays for 0.8s
	var __PERIOD = 20; // The meter is updated 50 times per second

	var __sum = 0;
	var __count = 0;
	var __peak = 0;

	var __level_db = __FLOOR;
	var __clipped = 0;

	var __ctx = null;
	var __resuming = false;
	var __src = null;
	var __analyser = null;
	var __buf = null;
	var __timer = null;

	/************************************************************************/

	self.attach = function(track) {
		self.detach();
		try {
			// The bar jumps between the updates by default, this blends the steps
			// without making the meter lag behind the sound
			$("stream-mic-level-progress").querySelector(".progress-value")
				.style.transition = `width ${__PERIOD}ms linear`;
			__ctx = new AudioContext();
			// The source must be kept referenced, otherwise it can be garbage collected
			__src = __ctx.createMediaStreamSource(new MediaStream([track]));
			__analyser = __ctx.createAnalyser();
			// 1024 samples is about 21ms at 48kHz: the same window the direct mode
			// measures, so both modes react to the sound in the same way
			__analyser.fftSize = 1024;
			__buf = new Float32Array(__analyser.fftSize);
			__src.connect(__analyser); // Not connected to the destination: nothing is played
			__timer = setInterval(__poll, __PERIOD);
			__poll();
		} catch (err) {
			tools.error("MicLevel: Can't attach to the track:", err);
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
				tools.error("MicLevel: Can't detach from the track:", err);
			}
		}
		__resuming = false;
		__src = null;
		__analyser = null;
		__buf = null;
		__ctx = null;
		self.reset();
	};

	self.reset = function() {
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
					tools.error("MicLevel: Can't resume the context:", err);
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
		for (let index = 0; index < samples.length; ++index) {
			let value = Math.abs(samples[index]);
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

	var __percent = function(db) {
		return Math.max(0, Math.min(100, (db - __FLOOR) / -__FLOOR * 100));
	};

	var __draw = function() {
		let percent = __percent(__level_db);
		let label = "";
		if (__clipped > 0) {
			label = "Clipping!";
		} else if (percent > 0) {
			label = `${Math.round(__level_db)} dB`;
		}
		tools.progress.setValue($("stream-mic-level-progress"), label, percent);
	};
}
