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


export function WebrtcStreamer(__setActive, __setInactive, __setInfo, __organizeHook, __orient) {
	var self = this;

	/************************************************************************/

	var __pc = null;
	var __ws = null;
	var __retry_timeout = null;
	var __ensuring = false;

	/************************************************************************/

	self.getOrientation = () => __orient;
	self.isAudioAllowed = () => false;
	self.isMicAllowed = () => false;
	self.isCamAllowed = () => false;

	self.getName = function() {
		return "WebRTC Direct";
	};

	self.getMode = function() {
		return "webrtc";
	};

	self.ensureStream = function(state) {
		if (__ensuring) return;
		__ensuring = true;
		__connect();
	};

	self.stopStream = function() {
		__ensuring = false;
		__cleanup();
	};

	/************************************************************************/

	// Signaling rides on kvmd's existing /ws WebSocket (event_type "webrtc_signal").
	// kvmd transparently bridges these frames to the GamerStreamer subprocess.

	var __connect = function() {
		let proto = (location.protocol === "https:" ? "wss:" : "ws:");
		let url = proto + "//" + location.host + "/api/ws?stream=0";
		__ws = new WebSocket(url);
		__ws.onopen = function() {
			__setInfo(false, false, "Gamer mode: requesting offer...");
			// Poke the server to start the pipeline; the message body is unused
			// on the very first call (it just triggers ensure_start in the handler).
			__ws.send(JSON.stringify({event_type: "webrtc_signal", event: {type: "hello"}}));
		};
		__ws.onmessage = (ev) => __onSignal(JSON.parse(ev.data));
		__ws.onclose = function() {
			__setInactive();
			__scheduleRetry();
		};
		__ws.onerror = function() {
			if (__ws) __ws.close();
		};
	};

	var __sendSignal = function(payload) {
		if (__ws && __ws.readyState === WebSocket.OPEN) {
			__ws.send(JSON.stringify({event_type: "webrtc_signal", event: payload}));
		}
	};

	var __onSignal = async function(frame) {
		// kvmd wraps events as {event_type: "webrtc_signal", event: {...}}
		if (frame.event_type !== "webrtc_signal") return;
		let msg = frame.event;

		if (msg.type === "offer") {
			__setInfo(false, false, "Got offer, creating answer...");
			__pc = new RTCPeerConnection({
				iceServers: [{urls: "stun:stun.l.google.com:19302"}],
			});

			__pc.ontrack = function(event) {
				__setActive();
				__setInfo(false, false, "Gamer mode: streaming!");
				$("stream-video").srcObject = event.streams[0];
			};

			__pc.onicecandidate = function(event) {
				if (event.candidate) {
					__sendSignal({
						type: "ice",
						candidate: event.candidate.candidate,
						sdpMLineIndex: event.candidate.sdpMLineIndex,
					});
				}
			};

			__pc.oniceconnectionstatechange = function() {
				let state = __pc.iceConnectionState;
				if (state === "failed" || state === "disconnected" || state === "closed") {
					__setInactive();
					__setInfo(false, false, "Gamer mode: ICE " + state);
					__scheduleRetry();
				}
			};

			await __pc.setRemoteDescription({type: "offer", sdp: msg.sdp});
			let answer = await __pc.createAnswer();
			await __pc.setLocalDescription(answer);
			__sendSignal({type: "answer", sdp: answer.sdp});
		}
		else if (msg.type === "ice") {
			if (__pc && msg.candidate) {
				await __pc.addIceCandidate({
					candidate: msg.candidate,
					sdpMLineIndex: msg.sdpMLineIndex,
				});
			}
		}
		else if (msg.type === "signal_lost") {
			__setInfo(false, false, "Gamer mode: capture signal lost (port switch?)");
		}
		else if (msg.type === "signal_restored") {
			__setInfo(false, false, "Gamer mode: streaming!");
		}
	};

	var __cleanup = function() {
		if (__retry_timeout) {
			clearTimeout(__retry_timeout);
			__retry_timeout = null;
		}
		if (__pc) {
			__pc.close();
			__pc = null;
		}
		if (__ws) {
			__ws.close();
			__ws = null;
		}
		__setInactive();
	};

	var __scheduleRetry = function() {
		if (!__ensuring) return;
		__cleanup();
		__ensuring = true;  // restore after cleanup cleared it
		__retry_timeout = setTimeout(__connect, 2000);
	};
}
