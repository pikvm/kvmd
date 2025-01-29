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
import {checkBrowser} from "../bb.js";
import {wm, initWindowManager} from "../wm.js";


export function main() {
	if (checkBrowser(null, null)) {
		initWindowManager();

		__loadProviders();

		tools.el.setOnClick($("login-button"), __login);
		$("user-input").onkeyup = $("passwd-input").onkeyup = $("code-input").onkeyup = function(event) {
			if (event.code === "Enter") {
				event.preventDefault();
				$("login-button").click();
			}
		};

		$("user-input").focus();
	}
}

function __loadProviders () {
	let http = tools.makeRequest("GET", "/api/auth/oauth/providers", function() {
		if (http.readyState === 4) {
			if (http.status === 200) {
				let oauthInfo = JSON.parse(http.responseText).result;
				if (!oauthInfo.enabled) {
					return;
				}
				let buttons = `<tr>
                                          <td colspan="2">
                                            <hr>
                                          </td>
                                        </tr>
					<tr><td>&nbsp;</tr></td>`
                                for (const [short_name, long_name] of Object.entries(oauthInfo.providers)) {
					buttons += __makeProvider(short_name, long_name);
				}
                                $("oauth-tbody").innerHTML = buttons
			}
		}
	})
}

function __makeProvider(shortName, longName) {
	return `<tr>
              <td colspan="2">
                <button class="key" onclick="window.location.href='/api/auth/oauth/login/${shortName}';">Login with ${longName}</button>
              </td>
            </tr>`;
}


function __login() {
	let user = $("user-input").value;
	if (user.length === 0) {
		$("user-input").focus();
	} else {
		let passwd = $("passwd-input").value + $("code-input").value;
		let body = `user=${encodeURIComponent(user)}&passwd=${encodeURIComponent(passwd)}`;
		tools.httpPost("/api/auth/login", null, function(http) {
			if (http.status === 200) {
				document.location.href = "/";
			} else if (http.status === 403) {
				wm.error("Invalid credentials").then(__tryAgain);
			} else {
				let error = "";
				if (http.status === 400) {
					try { error = JSON.parse(http.responseText)["result"]["error"]; } catch { /* Nah */ }
				}
				if (error === "ValidatorError") {
					wm.error("Invalid characters in credentials").then(__tryAgain);
				} else {
					wm.error("Login error", http.responseText).then(__tryAgain);
				}
			}
		}, body, "application/x-www-form-urlencoded");
		__setEnabled(false);
	}
}

function __setEnabled(enabled) {
	tools.el.setEnabled($("user-input"), enabled);
	tools.el.setEnabled($("passwd-input"), enabled);
	tools.el.setEnabled($("code-input"), enabled);
	tools.el.setEnabled($("login-button"), enabled);
}

function __tryAgain() {
	__setEnabled(true);
	let el = ($("code-input").value.length ? $("code-input") : $("passwd-input"));
	el.focus();
	el.select();
}
