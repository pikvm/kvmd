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
import {wm} from "../wm.js";
import {MicLevel} from "./mic_level.js";


export function MediaStreamer(__setActive, __setInactive, __setInfo, __watchHook, __organizeHook) {
	var self = this;

	/************************************************************************/

	var __stop = false;
	var __ensuring = false;

	var __ws = null;
	var __ping_timer = null;
	var __missed_heartbeats = 0;

	var __codec = "";
	var __decoder = null;
	var __frame = null;
	var __canvas = $("stream-canvas");
	var __ctx = __canvas.getContext("2d");

	var __state = null;
	var __fps_accum = 0;
	var __bytes_accum = 0;

	var __orient = 0;

	var __audio_info = null; // Audio params from the server
	var __audio_volume = 0; // Requested volume
	var __audio = null; // Active player
	var __audio_starting = false;
	var __audio_failed = false; // Don't retry the broken audio in a loop
	var __audio_error = ""; // The last error reported by the server
	var __audio_grace_timer = null;

	var __mic_level = new MicLevel();

	var __mic_info = null; // Mic params from the server
	var __mic_req = null; // Requested mic device ID
	var __mic_raw = false; // Requested capture without the browser's noise processing
	var __mic = null; // Active capturer
	var __mic_starting = false;
	var __mic_failed = false; // Don't retry the broken mic in a loop
	var __mic_retries = 0;

	var __init__ = function() {
		tools.feature.setEnabled($("stream-orient"), true);
		tools.feature.setEnabled($("stream-multimedia"), false);
		tools.feature.setEnabled($("stream-audio"), false);
		tools.feature.setEnabled($("stream-mic"), false);
		tools.feature.setEnabled($("stream-mic-raw"), false);
		tools.feature.setEnabled($("stream-mic-level"), false);
		tools.feature.setEnabled($("stream-camera"), false);
	};

	/************************************************************************/

	self.setOrientation = function(orient) { __orient = orient; };
	self.setCameraDevice = function(camera) {}; // eslint-disable-line no-unused-vars

	self.setAudioVolume = function(volume) {
		let prev = __audio_volume;
		__audio_volume = volume;
		if (!prev !== !volume) {
			__audio_failed = false; // The user has changed their mind
		}
		if (__audio !== null) {
			__audio.gain.gain.value = volume / 100;
		}
		__ensureAudio();
	};

	self.setMicDevice = function(mic) {
		if (__mic_req !== mic) {
			__mic_req = mic;
			__mic_failed = false;
			__ensureMic();
		}
	};

	self.setMicRaw = function(raw) {
		if (__mic_raw !== !!raw) {
			__mic_raw = !!raw;
			__mic_failed = false;
			__ensureMic();
		}
	};

	self.getName = function() {
		let extra = [];
		if (__audio !== null) {
			extra.push("Audio");
		}
		if (__mic !== null) {
			extra.push("Mic");
		}
		return ["Direct H.264", ...extra].join(" + ");
	};
	self.getMode = () => "media";

	self.getResolution = function() {
		return {
			// Разрешение видео или элемента
			"real_width": (__canvas.width || __canvas.offsetWidth),
			"real_height": (__canvas.height || __canvas.offsetHeight),
			"view_width": __canvas.offsetWidth,
			"view_height": __canvas.offsetHeight,
		};
	};

	self.ensureStream = function(state) {
		__state = state;
		__stop = false;
		__ensureMedia(false);
	};

	self.stopStream = function() {
		__stop = true;
		__ensuring = false;
		__wsForceClose();
		__setInfo(false, false, "");
	};

	var __updateMultimedia = function() {
		tools.feature.setEnabled($("stream-multimedia"), (__audio_info !== null || __mic_info !== null));
	};

	var __isWsReady = () => (__ws !== null && __ws.readyState === WebSocket.OPEN);

	/************************************************************************/
	/*                                 AUDIO                                */
	/************************************************************************/

	var __AUDIO_LAG = 0.06; // Buffer 60ms of the audio to smooth the network jitter
	var __AUDIO_MIN_LAG = 0.01; // Below this we have nothing to play: resync
	var __AUDIO_MAX_LAG = __AUDIO_LAG + 0.1; // Above this the lag is just a delay: catch up
	var __AUDIO_FADE = 0.005; // Ramp the first frame after a discontinuity to avoid a click
	var __AUDIO_GRACE = 15000; // The server retries the capture, so don't alarm the user at once

	var __MIC_RETRIES = 5; // The playback device can be busy for a while after a mode switch
	var __MIC_RETRY_DELAY = 3000;

	var __setupAudio = function(info) {
		__audio_info = ((info && window.AudioContext) ? info : null);
		__audio_failed = false; // The new session deserves the new try
		tools.feature.setEnabled($("stream-audio"), (__audio_info !== null));
		__updateMultimedia();
		__ensureAudio();
	};

	var __ensureAudio = function() {
		if (__audio_volume > 0 && !__audio_failed && __audio_info !== null && !__stop && __isWsReady()) {
			if (__audio === null && !__audio_starting) {
				__audio_starting = true;
				__startAudio().catch(function(err) {
					__logInfo("Can't start the audio:", err);
					__failAudio("Can't play the audio.<br>Check the browser permissions for it.", err);
				}).finally(function() {
					__audio_starting = false;
					__ensureAudio(); // Something might have changed while we were starting
				});
			}
		} else {
			__stopAudio(true);
		}
	};

	var __startAudio = async () => {
		let audio = {
			"ctx": null, "gain": null, "decoder": null, "format": "pcm",
			"ts": 0, "time": 0, "faded": false, "resyncs": 0, "drops": 0,
		};
		try {
			audio.format = await __chooseAudioFormat();

			audio.ctx = new AudioContext({"sampleRate": __audio_info.hz, "latencyHint": "interactive"});
			audio.gain = audio.ctx.createGain();
			audio.gain.gain.value = __audio_volume / 100;
			audio.gain.connect(audio.ctx.destination);

			if (audio.format === "opus") {
				audio.decoder = new AudioDecoder({
					"output": (frame) => __playAudioFrame(audio, frame),
					"error": (err) => __logInfo("Audio decoder error:", err),
				});
				audio.decoder.configure(__makeAudioDecoderConfig());
			}

			if (audio.ctx.state === "suspended") {
				await audio.ctx.resume();
			}

			if (__audio_volume <= 0 || !__isWsReady()) { // Something has changed while we were starting
				__closeAudio(audio);
				__logInfo("Audio start cancelled");
				return;
			}
			__ws.send(JSON.stringify({
				"event_type": "audio_start",
				"event": {"format": audio.format},
			}));
			__audio = audio;
			__logInfo(`Audio started (${audio.format})`);
		} catch (err) {
			__closeAudio(audio);
			throw err;
		}
	};

	var __makeAudioDecoderConfig = function() {
		return {
			"codec": "opus",
			"sampleRate": __audio_info.hz,
			"numberOfChannels": __audio_info.channels,
		};
	};

	var __chooseAudioFormat = async () => {
		if (__audio_info.formats.includes("opus") && window.AudioDecoder) {
			try {
				let sup = await AudioDecoder.isConfigSupported(__makeAudioDecoderConfig());
				if (sup.supported) {
					return "opus";
				}
			} catch (err) {
				__logInfo("Can't check the Opus decoder:", err);
			}
		}
		if (!__audio_info.formats.includes("pcm")) {
			throw new Error("This browser can't decode the audio");
		}
		return "pcm";
	};

	var __recvAudioData = function(gap, raw) {
		let audio = __audio;
		if (audio === null) {
			return;
		}
		if (__audio_grace_timer !== null) {
			__clearAudioGrace(); // The server has recovered the capture
		}
		if (gap) {
			// The server dropped something: the sound will jump, so ramp it in
			audio.faded = false;
		}
		if (audio.decoder === null) {
			__playAudioPcm(audio, raw);
		} else if (audio.decoder.state === "configured") {
			audio.decoder.decode(new EncodedAudioChunk({
				"type": "key", // Opus frames are always the key frames
				"timestamp": audio.ts,
				"data": raw,
			}));
			audio.ts += __audio_info.frame_ms * 1000;
		}
	};

	var __playAudioFrame = function(audio, frame) {
		try {
			if (__audio !== audio) {
				return;
			}
			let buf = audio.ctx.createBuffer(frame.numberOfChannels, frame.numberOfFrames, frame.sampleRate);
			for (let ch = 0; ch < frame.numberOfChannels; ++ch) {
				frame.copyTo(buf.getChannelData(ch), {"planeIndex": ch, "format": "f32-planar"});
			}
			__playAudioBuffer(audio, buf);
		} catch (err) {
			__logInfo("Can't play the audio frame:", err);
		} finally {
			frame.close();
		}
	};

	var __playAudioPcm = function(audio, raw) {
		let channels = __audio_info.channels;
		let pcm = new Int16Array(raw);
		let count = Math.floor(pcm.length / channels);
		if (count === 0) {
			return;
		}
		let buf = audio.ctx.createBuffer(channels, count, __audio_info.hz);
		for (let ch = 0; ch < channels; ++ch) {
			let plane = buf.getChannelData(ch);
			for (let index = 0; index < count; ++index) {
				plane[index] = pcm[index * channels + ch] / 32768;
			}
		}
		__playAudioBuffer(audio, buf);
	};

	var __playAudioBuffer = function(audio, buf) {
		let now = audio.ctx.currentTime;
		if (audio.time < now + __AUDIO_MIN_LAG) {
			// The first frame or we have fallen behind: resync the playback
			audio.time = now + __AUDIO_LAG;
			audio.resyncs += 1;
			audio.faded = false;
		} else if (audio.time > now + __AUDIO_MAX_LAG) {
			// The lag has grown (a burst after a stall), drop the frame to catch up
			audio.drops += 1;
			audio.faded = false;
			return;
		}
		if (!audio.faded) {
			__fadeInAudio(buf); // Don't splice the sound at a random amplitude
			audio.faded = true;
		}
		let src = audio.ctx.createBufferSource();
		src.buffer = buf;
		src.connect(audio.gain);
		src.start(audio.time);
		audio.time += buf.duration;
	};

	var __fadeInAudio = function(buf) {
		let count = Math.min(Math.round(__AUDIO_FADE * buf.sampleRate), buf.length);
		for (let ch = 0; ch < buf.numberOfChannels; ++ch) {
			let plane = buf.getChannelData(ch);
			for (let index = 0; index < count; ++index) {
				plane[index] *= (1 - Math.cos(Math.PI * index / count)) / 2;
			}
		}
	};

	var __clearAudioGrace = function() {
		if (__audio_grace_timer !== null) {
			clearTimeout(__audio_grace_timer);
			__audio_grace_timer = null;
		}
		__audio_error = "";
	};

	var __failAudioLate = function() {
		// The server hasn't recovered the capture in time, now it's a real error
		__audio_grace_timer = null;
		__failAudio("The audio is not available on PiKVM.", __audio_error);
	};

	var __failAudio = function(html, err) {
		__audio_failed = true;
		__stopAudio(true);
		if (__audio_volume > 0) { // Not a cancellation by the user
			wm.error(html, err);
		}
	};

	var __stopAudio = function(notify) {
		__clearAudioGrace();
		let audio = __audio;
		__audio = null;
		if (audio !== null) {
			__closeAudio(audio);
			if (notify && __isWsReady()) {
				try {
					__ws.send(JSON.stringify({"event_type": "audio_stop", "event": {}}));
				} catch {}
			}
			__logInfo(`Audio stopped (resyncs=${audio.resyncs}, drops=${audio.drops})`);
		}
	};

	var __closeAudio = function(audio) {
		for (let close of [
			() => { if (audio.decoder !== null && audio.decoder.state !== "closed") { audio.decoder.close(); } },
			() => { if (audio.gain !== null) { audio.gain.disconnect(); } },
			() => { if (audio.ctx !== null) { audio.ctx.close(); } },
		]) {
			try {
				close();
			} catch (err) {
				__logInfo("Can't close the audio:", err);
			}
		}
	};

	/************************************************************************/
	/*                              MICROPHONE                              */
	/************************************************************************/

	var __setupMic = function(info) {
		// The worklet-based capturer is mono-only, as well as the server side
		let sup = (window.AudioWorkletNode && navigator.mediaDevices);
		__mic_info = ((info && info.channels === 1 && sup) ? info : null);
		__mic_failed = false; // The new session deserves the new try
		tools.feature.setEnabled($("stream-mic"), (__mic_info !== null));
		tools.feature.setEnabled($("stream-mic-raw"), (__mic_info !== null));
		tools.feature.setEnabled($("stream-mic-level"), (__mic_info !== null));
		__updateMultimedia();
		__ensureMic();
	};

	var __ensureMic = function() {
		if (__mic_req && !__mic_failed && __mic_info !== null && !__stop && __isWsReady()) {
			if (__mic !== null && (__mic.id !== __mic_req || __mic.raw !== __mic_raw)) {
				__stopMic(true); // The device or the capture mode was changed by the user
			}
			if (__mic === null && !__mic_starting) {
				__mic_starting = true;
				__startMic().catch(function(err) {
					__logInfo("Can't start the mic:", err);
					__failMic("Can't start the microphone.<br>Check the browser permissions for it.", err);
				}).finally(function() {
					__mic_starting = false;
					__ensureMic(); // Something might have changed while we were starting
				});
			}
		} else {
			__stopMic(true);
		}
	};

	var __startMic = async () => {
		let mic = {
			"id": __mic_req, "raw": __mic_raw, "stream": null, "ctx": null, "src": null,
			"node": null, "encoder": null, "format": "pcm", "ts": 0,
		};
		try {
			let want = __mic_req; // The request we've started for
			let id = want;
			try {
				mic.stream = await __getMicStream(id, mic.raw);
			} catch (err) {
				if (id === ".__default__") {
					throw err;
				}
				// The stored device is gone, so we're falling back to the default one
				__logInfo("Can't use the selected mic device:", err);
				id = ".__default__";
				if (__mic_req === want) { // The user might have turned the mic off meanwhile
					__mic_req = id;
				}
				mic.id = id;
				mic.stream = await __getMicStream(id, mic.raw);
			}
			mic.format = await __chooseMicFormat();
			if (mic.format === "opus") {
				mic.encoder = new AudioEncoder({
					"output": (chunk) => __sendMicChunk(mic, chunk),
					"error": (err) => __logInfo("Mic encoder error:", err),
				});
				mic.encoder.configure(__makeMicEncoderConfig());
			}

			mic.ctx = new AudioContext({"sampleRate": __mic_info.hz});
			await mic.ctx.audioWorklet.addModule(new URL("mic_worklet.js", import.meta.url));
			mic.node = new AudioWorkletNode(mic.ctx, "kvmd-mic", {
				"numberOfInputs": 1,
				"numberOfOutputs": 0,
				"channelCount": 1,
				"channelCountMode": "explicit",
				"processorOptions": {"size": Math.round(__mic_info.hz * __mic_info.frame_ms / 1000)},
			});
			mic.node.port.onmessage = (ev) => __sendMicFrame(mic, ev.data);
			// The source must be kept referenced: the graph is not connected
			// to the context destination, so it can be garbage collected
			mic.src = mic.ctx.createMediaStreamSource(mic.stream);
			mic.src.connect(mic.node);
			if (mic.ctx.state === "suspended") {
				await mic.ctx.resume();
			}

			if (__mic_req !== mic.id || !__isWsReady()) { // Something has changed meanwhile
				__closeMic(mic);
				__logInfo("Mic start cancelled");
				return;
			}
			await __refillMicDevices(id); // Only now it's our device for sure
			__ws.send(JSON.stringify({
				"event_type": "mic_start",
				"event": {"format": mic.format},
			}));
			__mic = mic;
			__logInfo(`Mic started (${mic.format})`);
		} catch (err) {
			__closeMic(mic);
			throw err;
		}
	};

	var __getMicStream = function(id, raw) {
		let audio = (id === ".__default__" ? {} : {"deviceId": {"exact": id}});
		if (raw) {
			// The echo cancellation stays on: the host audio is played by the same page,
			// so without it the host would hear itself back through the microphone
			audio["noiseSuppression"] = false;
			audio["autoGainControl"] = false;
		}
		return navigator.mediaDevices.getUserMedia({"audio": audio});
	};

	var __refillMicDevices = async (id) => {
		// The device labels are available only after the permission is granted
		let el = $("stream-mic-selector");
		let devices = await navigator.mediaDevices.enumerateDevices();
		el.options.length = 1;
		let found = false;
		for (let dev of devices) {
			if (dev.kind === "audioinput") {
				tools.selector.addOption(el, dev.label, dev.deviceId);
				if (dev.deviceId === id) {
					found = true;
				}
			}
		}
		el.value = (found ? id : ".__default__");
		tools.storage.set("stream.mic.device.id", el.value);
		let name = "\u2500 Unknown yet \u2500";
		try {
			name = el.options[el.selectedIndex].innerText;
		} catch {}
		tools.storage.set("stream.mic.device.name", name);
	};

	var __makeMicEncoderConfig = function() {
		return {
			"codec": "opus",
			"sampleRate": __mic_info.hz,
			"numberOfChannels": __mic_info.channels,
			"bitrate": 48000,
			"opus": {"frameDuration": __mic_info.frame_ms * 1000},
		};
	};

	var __chooseMicFormat = async () => {
		if (__mic_info.formats.includes("opus") && window.AudioEncoder) {
			try {
				let sup = await AudioEncoder.isConfigSupported(__makeMicEncoderConfig());
				if (sup.supported) {
					return "opus";
				}
			} catch (err) {
				__logInfo("Can't check the Opus encoder:", err);
			}
		}
		if (!__mic_info.formats.includes("pcm")) {
			throw new Error("This browser can't encode the mic audio");
		}
		return "pcm";
	};

	var __sendMicFrame = function(mic, samples) {
		if (__mic !== mic) {
			return;
		}
		__mic_level.feed(samples);
		if (mic.encoder === null) {
			__sendMicData(mic, __makeMicPcm(samples));
		} else if (mic.encoder.state === "configured") {
			let data = new AudioData({
				"format": "f32-planar",
				"sampleRate": __mic_info.hz,
				"numberOfFrames": samples.length,
				"numberOfChannels": 1,
				"timestamp": mic.ts,
				"data": samples,
			});
			mic.ts += Math.round(samples.length * 1000000 / __mic_info.hz);
			try {
				mic.encoder.encode(data);
			} finally {
				data.close();
			}
		}
	};

	var __sendMicChunk = function(mic, chunk) {
		let data = new Uint8Array(chunk.byteLength);
		chunk.copyTo(data);
		__sendMicData(mic, data);
	};

	var __makeMicPcm = function(samples) {
		let pcm = new Int16Array(samples.length);
		for (let index = 0; index < samples.length; ++index) {
			let value = Math.max(-1, Math.min(1, samples[index]));
			pcm[index] = Math.round(value * 32767);
		}
		return new Uint8Array(pcm.buffer);
	};

	var __MIC_MAX_BUFFERED = 262144; // Drop the mic frames if the uplink can't keep up

	var __sendMicData = function(mic, data) {
		if (__mic !== mic || !__isWsReady() || __ws.bufferedAmount > __MIC_MAX_BUFFERED) {
			return;
		}
		let msg = new Uint8Array(data.length + 1);
		msg[0] = 2; // Mic frame
		msg.set(data, 1);
		try {
			__ws.send(msg);
		} catch (err) {
			__logInfo("Can't send the mic frame:", err);
		}
	};

	var __retryMic = function() {
		if (__mic !== null && __mic_req && __isWsReady()) {
			__logInfo(`Retrying the mic (${__mic_retries}/${__MIC_RETRIES}) ...`);
			__ws.send(JSON.stringify({
				"event_type": "mic_start",
				"event": {"format": __mic.format},
			}));
		}
	};

	var __failMic = function(html, err) {
		__mic_failed = true;
		__stopMic(true);
		if (__mic_req) { // Not a cancellation by the user
			wm.error(html, err);
		}
	};

	var __stopMic = function(notify) {
		__mic_retries = 0;
		let mic = __mic;
		__mic = null;
		if (mic !== null) {
			__closeMic(mic);
			if (notify && __isWsReady()) {
				try {
					__ws.send(JSON.stringify({"event_type": "mic_stop", "event": {}}));
				} catch {}
			}
			__mic_level.reset();
			__logInfo("Mic stopped");
		}
	};

	var __closeMic = function(mic) {
		for (let close of [
			() => { if (mic.src !== null) { mic.src.disconnect(); } },
			() => { if (mic.node !== null) { mic.node.port.onmessage = null; mic.node.disconnect(); } },
			() => { if (mic.encoder !== null && mic.encoder.state !== "closed") { mic.encoder.close(); } },
			() => { if (mic.ctx !== null) { mic.ctx.close(); } },
			() => { if (mic.stream !== null) { mic.stream.getTracks().forEach((track) => track.stop()); } },
		]) {
			try {
				close();
			} catch (err) {
				__logInfo("Can't close the mic:", err);
			}
		}
	};

	var __ensureMedia = function(internal) {
		if (__ws === null && !__stop && (!__ensuring || internal)) {
			__ensuring = true;
			__setInactive();
			__setInfo(false, false, "");
			__logInfo("Starting Media ...");
			__ws = new WebSocket(tools.makeWsUrl("api/media/ws"));
			__ws.binaryType = "arraybuffer";
			__ws.onopen = __wsOpenHandler;
			__ws.onerror = __wsErrorHandler;
			__ws.onclose = __wsCloseHandler;
			__ws.onmessage = async (ev) => {
				try {
					if (typeof ev.data === "string") {
						ev = JSON.parse(ev.data);
						__wsJsonHandler(ev.event_type, ev.event);
					} else { // Binary
						await __wsBinHandler(ev.data);
					}
				} catch (ex) {
					__wsErrorHandler(ex);
				}
			};
		}
	};

	var __wsOpenHandler = function(ev) {
		__logInfo("Socket opened:", ev);
		__missed_heartbeats = 0;
		__ping_timer = setInterval(__ping, 1000);
	};

	var __ping = function() {
		try {
			__missed_heartbeats += 1;
			if (__missed_heartbeats >= 5) {
				throw new Error("Too many missed heartbeats");
			}
			__ws.send(new Uint8Array([0]));

			if (__decoder && __decoder.state === "configured") {
				let online = !!(__state && __state.source.online);
				// Everything the socket delivers: the video and the host audio
				let kbps = Math.round(__bytes_accum * 8 / 1000);
				__setInfo(true, online, `${kbps} kbps / ${__fps_accum} dyn.fps`);
			}
			__fps_accum = 0;
			__bytes_accum = 0;
		} catch (ex) {
			__wsErrorHandler(ex.message);
		}
	};

	var __wsForceClose = function() {
		if (__ws) {
			__ws.onclose = null;
			__ws.close();
		}
		__wsCloseHandler(null);
		__setInactive();
	};

	var __wsErrorHandler = function(ev) {
		__logInfo("Socket error:", ev);
		__setInfo(false, false, ev);
		__wsForceClose();
	};

	var __wsCloseHandler = function(ev) {
		__logInfo("Socket closed:", ev);
		__stopAudio(false);
		__stopMic(false);
		if (__ping_timer) {
			clearInterval(__ping_timer);
			__ping_timer = null;
		}
		__closeDecoder();
		__missed_heartbeats = 0;
		__fps_accum = 0;
		__bytes_accum = 0;
		__ws = null;
		if (!__stop) {
			setTimeout(() => __ensureMedia(true), 1000);
		}
	};

	var __wsJsonHandler = function(ev_type, ev) {
		if (ev_type === "media") {
			__setupCodec(ev.video);
			__setupAudio(ev.audio);
			__setupMic(ev.mic);
			if (__audio_info !== null || __mic_info !== null) {
				// Ask the user if the multimedia settings of the previous session
				// should be restored: the browser needs a gesture to play the audio
				__watchHook();
			}
		} else if (ev_type === "audio_state") {
			if (ev.error) {
				__logInfo("Audio error on the server:", ev.error);
				__audio_error = ev.error;
				if (__audio !== null && __audio_grace_timer === null) {
					// The capture device is busy for a while after switching from WebRTC,
					// and the server retries it, so we just wait for the frames to arrive
					__audio_grace_timer = setTimeout(__failAudioLate, __AUDIO_GRACE);
				} else if (__audio === null && !__audio_starting) {
					// While we're starting, the error belongs to the previous session
					__failAudio("The audio is not available on PiKVM.", ev.error);
				}
			} else if (ev.started) {
				__clearAudioGrace();
			}
		} else if (ev_type === "mic_state") {
			if (ev.error) {
				__logInfo("Mic error on the server:", ev.error);
				if (__mic !== null && __mic_req && __mic_retries < __MIC_RETRIES) {
					// The playback device is busy for a while after switching from WebRTC
					__mic_retries += 1;
					setTimeout(__retryMic, __MIC_RETRY_DELAY);
				} else if (__mic !== null || !__mic_starting) {
					// While we're starting, the error belongs to the previous session
					__mic_failed = true;
					__stopMic(false);
					if (__mic_req) { // Not a cancellation by the user
						wm.error("The microphone is not available on PiKVM.", ev.error);
					}
				}
			} else if (ev.started) {
				__mic_retries = 0;
			}
		}
	};

	var __setupCodec = function(formats) {
		__closeDecoder();
		if (formats.h264 === undefined) {
			let msg = "No H.264 stream available on PiKVM";
			__setInfo(false, false, msg);
			__logInfo(msg);
			return;
		}
		if (!window.VideoDecoder) {
			let msg = "This browser can't handle direct H.264 stream";
			if (!tools.is_https) {
				msg = "Direct H.264 requires HTTPS";
			}
			__setInfo(false, false, msg);
			__logInfo(msg);
			return;
		}
		__codec = `avc1.${formats.h264.profile_level_id}`;
		__ws.send(JSON.stringify({
			"event_type": "start",
			"event": {"type": "video", "format": "h264"},
		}));
	};

	var __wsBinHandler = async (data) => {
		__bytes_accum += data.byteLength;
		let header = new Uint8Array(data.slice(0, 2));
		if (header[0] === 255) { // Pong
			__missed_heartbeats = 0;
		} else if (header[0] === 1) { // Video frame
			let key = !!header[1];
			if (await __ensureDecoder(key)) {
				await __processFrame(key, data.slice(2));
			}
		} else if (header[0] === 3) { // Audio frame
			try {
				__recvAudioData(!!(header[1] & 1), data.slice(2));
			} catch (err) { // The video must survive the broken audio
				__logInfo("Can't play the audio frame:", err);
			}
		}
	};

	var __ensureDecoder = async (key) => {
		if (__codec === "") {
			return false;
		}
		if (__decoder === null || __decoder.state === "closed") {
			let started = (__codec !== "");
			let codec = __codec;
			__closeDecoder();
			__codec = codec;
			__decoder = new VideoDecoder({
				"output": __renderFrame,
				"error": (err) => __logInfo(err.message),
			});
			if (started) {
				__ws.send(new Uint8Array([0]));
			}
		}
		if (__decoder.state !== "configured") {
			if (!key) {
				return false;
			}
			await __decoder.configure({"codec": __codec, "optimizeForLatency": true});
		}
		if (__decoder.state === "configured") {
			__setActive();
			return true;
		}
		return false;
	};

	var __processFrame = async (key, raw) => {
		let chunk = new EncodedVideoChunk({
			"timestamp": (performance.now() + performance.timeOrigin) * 1000,
			"type": (key ? "key" : "delta"),
			"data": raw,
		});
		await __decoder.decode(chunk);
	};

	var __closeDecoder = function() {
		if (__decoder !== null) {
			try {
				__decoder.close();
			} finally {
				__codec = "";
				__decoder = null;
				if (__frame !== null) {
					try {
						__closeFrame(__frame);
					} finally {
						__frame = null;
					}
				}
			}
		}
	};

	var __renderFrame = function(frame) {
		if (__frame === null) {
			__frame = frame;
			window.requestAnimationFrame(__drawPendingFrame, __canvas);
		} else {
			__closeFrame(frame);
		}
	};

	var __drawPendingFrame = function() {
		if (__frame === null) {
			return;
		}
		try {
			let width = __frame.displayWidth;
			let height = __frame.displayHeight;
			switch (__orient) {
				case 90:
				case 270:
					width = __frame.displayHeight;
					height = __frame.displayWidth;
			}

			if (__canvas.width !== width || __canvas.height !== height) {
				__canvas.width = width;
				__canvas.height = height;
				__organizeHook();
			}

			if (__orient === 0) {
				__ctx.drawImage(__frame, 0, 0);
			} else {
				__ctx.save();
				try {
					switch(__orient) {
						case 90: __ctx.translate(0, height); __ctx.rotate(-Math.PI / 2); break;
						case 180: __ctx.translate(width, height); __ctx.rotate(-Math.PI); break;
						case 270: __ctx.translate(width, 0); __ctx.rotate(Math.PI / 2); break;
					}
					__ctx.drawImage(__frame, 0, 0);
				} finally {
					__ctx.restore();
				}
			}
			__fps_accum += 1;
		} finally {
			__closeFrame(__frame);
			__frame = null;
		}
	};

	var __closeFrame = function(frame) {
		// FIXME: On Firefox, image is flickering when we're closing the frame for some reason.
		// So we're just not performing the close() and it seems there is no problems here
		// because Firefox is implementing some auto-closing logic. With auto-close,
		// no flickering observed.
		//  - https://github.com/mozilla/gecko-dev/blob/82333a9/dom/media/webcodecs/VideoFrame.cpp
		//
		// Note at 2025.05.13:
		//  - The problem is not observed on nightly Firefox 140.
		//  - It's also not observed with hardware accelleration on 138.
		//
		// Update at 2025.11.06:
		//  - It seems it causes memory leak in Firefox.
		//  - But flickering is fixed on upstream so I commented this workaround for now.

		/*if (tools.browser.is_firefox) {
			return;
		}*/
		frame.close();
	};

	var __logInfo = (...args) => tools.info("Stream [Media]:", ...args);

	__init__();
}

MediaStreamer.is_videodecoder_available = function() {
	return !!window.VideoDecoder;
};
