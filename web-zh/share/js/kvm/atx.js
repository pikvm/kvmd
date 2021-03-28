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


"use strict";


import {tools, $} from "../tools.js";
import {wm} from "../wm.js";


export function Atx() {
	var self = this;

	/************************************************************************/

	var __init__ = function() {
		$("atx-power-led").title = "Power Led";
		$("atx-hdd-led").title = "Disk Activity Led";

		for (let args of [
			["atx-power-button", "power", "Are you sure you want to press the power button?"],
			["atx-power-button-long", "power_long", `
				Are you sure you want to long press the power button?<br>
				Warning! This could cause data loss on the server.
			`],
			["atx-reset-button", "reset", `
				Are you sure you want to press the reset button?<br>
				Warning! This could case data loss on the server.
			`],
		]) {
			tools.setOnClick($(args[0]), () => __clickButton(args[1], args[2]));
		}
	};

	/************************************************************************/

	self.setState = function(state) {
		let buttons_enabled = false;
		if (state) {
			tools.featureSetEnabled($("atx-dropdown"), state.enabled);
			$("atx-power-led").className = (state.busy ? "led-yellow" : (state.leds.power ? "led-green" : "led-gray"));
			$("atx-hdd-led").className = (state.leds.hdd ? "led-red" : "led-gray");
			buttons_enabled = !state.busy;
		} else {
			$("atx-power-led").className = "led-gray";
			$("atx-hdd-led").className = "led-gray";
		}
		for (let id of ["atx-power-button", "atx-power-button-long", "atx-reset-button"]) {
			wm.setElementEnabled($(id), buttons_enabled);
		}
	};

	var __clickButton = function(button, confirm_msg) {
		wm.confirm(confirm_msg).then(function(ok) {
			if (ok) {
				let http = tools.makeRequest("POST", `/api/atx/click?button=${button}`, function() {
					if (http.readyState === 4) {
						if (http.status === 409) {
							wm.error("Performing another ATX operation for other client.<br>Please try again later");
						} else if (http.status !== 200) {
							wm.error("Click error:<br>", http.responseText);
						}
					}
				});
			}
		});
	};

	__init__();
}
