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

	var __connect = function() {
		// Connect to the gamer-mode streamer's signaling WebSocket.
		// In production this would go through kvmd's /ws, but for the
		// standalone spike it connects directly to port 8765.
		let proto = (location.protocol === "https:" ? "wss:" : "ws:");
		let url = proto + "//" + location.hostname + ":8765/ws";

		__ws = new WebSocket(url);

		__ws.onopen = function() {
			__setInfo(false, false, "Gamer mode: waiting for offer...");
		};

		__ws.onmessage = async function(ev) {
			let msg = JSON.parse(ev.data);

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
					if (event.candidate && __ws) {
						__ws.send(JSON.stringify({
							type: "ice",
							candidate: event.candidate.candidate,
							sdpMLineIndex: event.candidate.sdpMLineIndex,
						}));
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
				__ws.send(JSON.stringify({type: "answer", sdp: answer.sdp}));
			}

			else if (msg.type === "ice") {
				if (__pc && msg.candidate) {
					await __pc.addIceCandidate({
						candidate: msg.candidate,
						sdpMLineIndex: msg.sdpMLineIndex,
					});
				}
			}
		};

		__ws.onclose = function() {
			__setInactive();
			__scheduleRetry();
		};

		__ws.onerror = function() {
			if (__ws) __ws.close();
		};
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
