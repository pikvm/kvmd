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


var _Janus = null;


export function JanusStreamer(__setActive, __setInactive, __setInfo, __organizeHook, __orient, __allow_audio, __allow_mic) {
	var self = this;

	/************************************************************************/

	__allow_mic = (__allow_audio && __allow_mic); // XXX: Mic only with audio

	var __stop = false;
	var __ensuring = false;

	var __janus = null;
	var __handle = null;

	var __retry_ensure_timeout = null;
	var __retry_emsg_timeout = null;
	var __info_interval = null;

	var __state = null;
	var __frames = 0;
	var __res = {"width": -1, "height": -1};
	var __resize_listener_installed = false;

	var __ice = null;

	/************************************************************************/

	self.getOrientation = () => __orient;
	self.isAudioAllowed = () => __allow_audio;
	self.isMicAllowed = () => __allow_mic;

	self.getName = function() {
		let name = "WebRTC H.264";
		if (__allow_audio) {
			name += " + Audio";
			if (__allow_mic) {
				name += " + Mic";
			}
		}
		return name;
	};
	self.getMode = () => "janus";

	self.getResolution = function() {
		let el = $("stream-video");
		return {
			// Разрешение видео или элемента
			"real_width": (el.videoWidth || el.offsetWidth),
			"real_height": (el.videoHeight || el.offsetHeight),
			"view_width": el.offsetWidth,
			"view_height": el.offsetHeight,
		};
	};

	self.ensureStream = function(state) {
		__state = state;
		__stop = false;
		__ensureJanus(false);
		if (!__resize_listener_installed) {
			$("stream-video").addEventListener("resize", __videoResizeHandler);
			__resize_listener_installed = true;
		}
	};

	self.stopStream = function() {
		__stop = true;
		__destroyJanus();
		if (__resize_listener_installed) {
			$("stream-video").removeEventListener("resize", __videoResizeHandler);
			__resize_listener_installed = false;
		}
	};

	var __videoResizeHandler = function(ev) {
		let el = ev.target;
		if (__res.width !== el.videoWidth || __res.height !== el.videoHeight) {
			__res.width = el.videoWidth;
			__res.height = el.videoHeight;
			__organizeHook();
		}
	};

	var __ensureJanus = function(internal) {
		if (__janus === null && !__stop && (!__ensuring || internal)) {
			__ensuring = true;
			__setInactive();
			__setInfo(false, false, "");
			__logInfo("Starting Janus ...");
			__janus = new _Janus({
				"server": tools.makeWsUrl("janus/ws"),
				"ipv6": true,
				"destroyOnUnload": false,
				"iceServers": () => __getIceServers(),
				"success": __attachJanus,
				"error": function(error) {
					__logError(error);
					__setInfo(false, false, error);
					__finishJanus();
				},
			});
		}
	};

	var __getIceServers = function() {
		if (__ice !== null && __ice.url) {
			__logInfo("Using the custom ICE Server got from uStreamer:", __ice);
			return [{"urls": __ice.url}];
		} else {
			return [];
		}
	};

	var __finishJanus = function() {
		if (__stop) {
			if (__retry_ensure_timeout !== null) {
				clearTimeout(__retry_ensure_timeout);
				__retry_ensure_timeout = null;
			}
			__ensuring = false;
		} else {
			if (__retry_ensure_timeout === null) {
				__retry_ensure_timeout = setTimeout(function() {
					__retry_ensure_timeout = null;
					__ensureJanus(true);
				}, 5000);
			}
		}
		__stopRetryEmsgInterval();
		__stopInfoInterval();
		if (__handle) {
			__logInfo("uStreamer detaching ...:", __handle.getPlugin(), __handle.getId());
			__handle.detach();
			__handle = null;
		}
		__janus = null;
		__setInactive();
		if (__stop) {
			__setInfo(false, false, "");
		}
	};

	var __destroyJanus = function() {
		if (__janus !== null) {
			__janus.destroy();
		}
		__finishJanus();
		let stream = $("stream-video").srcObject;
		if (stream) {
			for (let track of stream.getTracks()) {
				__removeTrack(track);
			}
		}
	};

	var __addTrack = function(track) {
		let el = $("stream-video");
		if (el.srcObject) {
			for (let tr of el.srcObject.getTracks()) {
				if (tr.kind === track.kind && tr.id !== track.id) {
					__removeTrack(tr);
				}
			}
		}
		if (!el.srcObject) {
			el.srcObject = new MediaStream();
		}
		el.srcObject.addTrack(track);
		// FIXME: Задержка уменьшается, но начинаются заикания на кейфреймах.
		// XXX: Этот пример переехал из януса 0.x, перед использованием адаптировать к 1.x.
		//   - https://github.com/Glimesh/janus-ftl-plugin/issues/101
		/*if (__handle && __handle.webrtcStuff && __handle.webrtcStuff.pc) {
			for (let receiver of __handle.webrtcStuff.pc.getReceivers()) {
				if (receiver.track && receiver.track.kind === "video" && receiver.playoutDelayHint !== undefined) {
					receiver.playoutDelayHint = 0;
				}
			}
		}*/
	};

	var __removeTrack = function(track) {
		let el = $("stream-video");
		if (!el.srcObject) {
			return;
		}
		track.stop();
		el.srcObject.removeTrack(track);
		if (el.srcObject.getTracks().length === 0) {
			// MediaStream should be destroyed to prevent old picture freezing
			// on Janus reconnecting.
			el.srcObject = null;
		}
	};

	var __attachJanus = function() {
		if (__janus === null) {
			return;
		}
		__janus.attach({
			"plugin": "janus.plugin.ustreamer",
			"opaqueId": "oid-" + _Janus.randomString(12),

			"success": function(handle) {
				__handle = handle;
				__logInfo("uStreamer attached:", handle.getPlugin(), handle.getId());
				__logInfo("Sending FEATURES ...");
				__handle.send({"message": {"request": "features"}});
			},

			"error": function(error) {
				__logError("Can't attach uStreamer: ", error);
				__setInfo(false, false, error);
				__destroyJanus();
			},

			"connectionState": function(state) {
				__logInfo("Peer connection state changed to", state);
				if (state === "failed") {
					__destroyJanus();
				}
			},

			"iceState": function(state) {
				__logInfo("ICE state changed to", state);
			},

			"webrtcState": function(up) {
				__logInfo("Janus says our WebRTC PeerConnection is", (up ? "up" : "down"), "now");
				if (up) {
					__sendKeyRequired();
				}
			},

			"onmessage": function(msg, jsep) {
				__stopRetryEmsgInterval();

				if (msg.result) {
					__logInfo("Got uStreamer result message:", msg.result); // starting, started, stopped
					if (msg.result.status === "started") {
						__setActive();
						__setInfo(false, false, "");
					} else if (msg.result.status === "stopped") {
						__setInactive();
						__setInfo(false, false, "");
					} else if (msg.result.status === "features") {
						tools.feature.setEnabled($("stream-audio"), msg.result.features.audio);
						tools.feature.setEnabled($("stream-mic"), msg.result.features.mic);
						__ice = msg.result.features.ice;
						__sendWatch();
					}
				} else if (msg.error_code || msg.error) {
					__logError("Got uStreamer error message:", msg.error_code, "-", msg.error);
					__setInfo(false, false, msg.error);
					if (__retry_emsg_timeout === null) {
						__retry_emsg_timeout = setTimeout(function() {
							if (!__stop) {
								__sendStop();
								__sendWatch();
							}
							__retry_emsg_timeout = null;
						}, 2000);
					}
					return;
				} else {
					__logInfo("Got uStreamer other message:", msg);
				}

				if (jsep) {
					__logInfo("Handling SDP:", jsep);
					let tracks = [{"type": "video", "capture": false, "recv": true, "add": true}];
					if (__allow_audio) {
						tracks.push({"type": "audio", "capture": __allow_mic, "recv": true, "add": true});
					}
					__handle.createAnswer({
						"jsep": jsep,

						"tracks": tracks,

						// Chrome is playing OPUS as mono without this hack
						//   - https://issues.webrtc.org/issues/41481053 - IT'S NOT FIXED!
						//   - https://github.com/ossrs/srs/pull/2683/files
						"customizeSdp": function(jsep) {
							jsep.sdp = jsep.sdp.replace("useinbandfec=1", "useinbandfec=1;stereo=1");
						},

						"success": function(jsep) {
							__logInfo("Got SDP:", jsep);
							__sendStart(jsep);
						},

						"error": function(error) {
							__logInfo("Error on SDP handling:", error);
							__setInfo(false, false, error);
							//__destroyJanus();
						},
					});
				}
			},

			// Janus 1.x
			"onremotetrack": function(track, id, added, meta) {
				// Chrome sends `muted` notifiation for tracks in `disconnected` ICE state
				// and Janus.js just removes muted track from list of available tracks.
				// But track still exists actually so it's safe to just ignore
				// reason === "mute" and "unmute".
				let reason = (meta || {}).reason;
				__logInfo("Got onremotetrack:", id, added, reason, track, meta);
				if (added && reason === "created") {
					__addTrack(track);
					if (track.kind === "video") {
						__sendKeyRequired();
						__startInfoInterval();
					}
				} else if (!added && reason === "ended") {
					__removeTrack(track);
				}
			},

			"oncleanup": function() {
				__logInfo("Got a cleanup notification");
				__stopInfoInterval();
			},
		});
	};

	var __startInfoInterval = function() {
		__stopInfoInterval();
		__setActive();
		__updateInfo();
		__info_interval = setInterval(__updateInfo, 1000);
	};

	var __stopInfoInterval = function() {
		if (__info_interval !== null) {
			clearInterval(__info_interval);
		}
		__info_interval = null;
	};

	var __stopRetryEmsgInterval = function() {
		if (__retry_emsg_timeout !== null) {
			clearTimeout(__retry_emsg_timeout);
			__retry_emsg_timeout = null;
		}
	};

	var __updateInfo = function() {
		if (__handle !== null) {
			let info = "";
			if (__handle !== null) {
				// https://wiki.whatwg.org/wiki/Video_Metrics
				let frames = null;
				let el = $("stream-video");
				if (el.webkitDecodedFrameCount !== undefined) {
					frames = el.webkitDecodedFrameCount;
				} else if (el.mozPaintedFrames !== undefined) {
					frames = el.mozPaintedFrames;
				}
				info = `${__handle.getBitrate()}`.replace("kbits/sec", "kbps");
				if (frames !== null) {
					info += ` / ${Math.max(0, frames - __frames)} fps dynamic`;
					__frames = frames;
				}
			}
			__setInfo(true, __isOnline(), info);
		}
	};

	var __isOnline = function() {
		return !!(__state && __state.source.online);
	};

	var __sendWatch = function() {
		if (__handle) {
			__logInfo(`Sending WATCH(orient=${__orient}, audio=${__allow_audio}, mic=${__allow_mic}) ...`);
			__handle.send({"message": {"request": "watch", "params": {
				"orientation": __orient,
				"audio": __allow_audio,
				"mic": __allow_mic,
			}}});
		}
	};

	var __sendStart = function(jsep) {
		if (__handle) {
			__logInfo("Sending START ...");
			__handle.send({"message": {"request": "start"}, "jsep": jsep});
		}
	};

	var __sendKeyRequired = function() {
		/*if (__handle) {
			// На этом шаге мы говорим что стрим пошел и надо запросить кейфрейм
			__logInfo("Sending KEY_REQUIRED ...");
			__handle.send({message: {request: "key_required"}});
		}*/
	};

	var __sendStop = function() {
		__stopInfoInterval();
		if (__handle) {
			__logInfo("Sending STOP ...");
			__handle.send({"message": {"request": "stop"}});
			__handle.hangup();
		}
	};

	var __logInfo = (...args) => tools.info("Stream [Janus]:", ...args);
	var __logError = (...args) => tools.error("Stream [Janus]:", ...args);
}

JanusStreamer.ensure_janus = function(callback) {
	if (_Janus === null) {
		import("./janus.js").then((module) => {
			module.Janus.init({
				"debug": "all",
				"callback": function() {
					_Janus = module.Janus;
					callback(true);
				},
			});
		}).catch((ex) => {
			tools.error("Stream: Can't import Janus module:", ex);
			callback(false);
		});
	} else {
		callback(true);
	}
};

JanusStreamer.is_webrtc_available = function() {
	return !!window.RTCPeerConnection;
};
