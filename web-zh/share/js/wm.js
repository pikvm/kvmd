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


import {tools, $, $$, $$$} from "./tools.js";


export var wm;

export function initWindowManager() {
	wm = new __WindowManager();
}

function __WindowManager() {
	var self = this;

	/************************************************************************/

	var __top_z_index = 0;
	var __windows = [];
	var __menu_buttons = [];

	var __init__ = function() {
		for (let el_button of $$$("button")) {
			// XXX: Workaround for iOS Safari:
			// https://stackoverflow.com/questions/3885018/active-pseudo-class-doesnt-work-in-mobile-safari
			el_button.ontouchstart = function() {};
		}

		for (let el_button of $$("menu-button")) {
			el_button.parentElement.querySelector(".menu").setAttribute("tabindex", "-1");
			tools.setOnDown(el_button, () => __toggleMenu(el_button));
			__menu_buttons.push(el_button);
		}

		for (let el_window of $$("window")) {
			el_window.setAttribute("tabindex", "-1");
			__makeWindowMovable(el_window);
			__windows.push(el_window);

			let el_button = el_window.querySelector(".window-header .window-button-close");
			if (el_button) {
				tools.setOnClick(el_button, function() {
					let close_window = function() {
						__closeWindow(el_window);
						__activateLastWindow(el_window);
					};
					let confirm_msg = el_window.getAttribute("data-close-confirm");
					if (confirm_msg) {
						self.confirm(confirm_msg).then(function(ok) {
							if (ok) {
								close_window();
							}
						});
					} else {
						close_window();
					}
				});
			}
		}

		window.onmouseup = __globalMouseButtonHandler;
		window.ontouchend = __globalMouseButtonHandler;

		window.addEventListener("focusin", __focusIn);
		window.addEventListener("focusout", __focusOut);

		window.addEventListener("resize", __organizeWindowsOnResize);
		window.addEventListener("orientationchange", __organizeWindowsOnResize);
	};

	/************************************************************************/

	self.error = (...args) => __modalDialog("Error", args.join(" "), true, false);
	self.confirm = (...args) => __modalDialog("Question", args.join(" "), true, true);

	var __modalDialog = function(header, text, ok, cancel) {
		let el_active_menu = (document.activeElement && document.activeElement.closest(".menu"));

		let el_modal = document.createElement("div");
		el_modal.className = "modal";
		el_modal.style.visibility = "visible";

		let el_window = document.createElement("div");
		el_window.className = "modal-window";
		el_window.setAttribute("tabindex", "-1");
		el_modal.appendChild(el_window);

		let el_header = document.createElement("div");
		el_header.className = "modal-header";
		el_header.innerHTML = header;
		el_window.appendChild(el_header);

		let el_content = document.createElement("div");
		el_content.className = "modal-content";
		el_content.innerHTML = text;
		el_window.appendChild(el_content);

		let promise = null;
		if (ok || cancel) {
			promise = new Promise(function(resolve) {
				let el_buttons = document.createElement("div");
				el_buttons.className = "modal-buttons";
				el_window.appendChild(el_buttons);

				function close(retval) {
					__closeWindow(el_window);
					el_modal.outerHTML = "";
					let index = __windows.indexOf(el_modal);
					if (index !== -1) {
						__windows.splice(index, 1);
					}
					if (el_active_menu && el_active_menu.style.visibility === "visible") {
						el_active_menu.focus();
					} else {
						__activateLastWindow(el_modal);
					}
					resolve(retval);
				}

				if (cancel) {
					var el_cancel_button = document.createElement("button");
					el_cancel_button.innerHTML = "Cancel";
					tools.setOnClick(el_cancel_button, () => close(false));
					el_buttons.appendChild(el_cancel_button);
				}
				if (ok) {
					var el_ok_button = document.createElement("button");
					el_ok_button.innerHTML = "OK";
					tools.setOnClick(el_ok_button, () => close(true));
					el_buttons.appendChild(el_ok_button);
				}
				if (ok && cancel) {
					el_ok_button.className = "row50";
					el_cancel_button.className = "row50";
				}

				el_window.onkeyup = function(event) {
					event.preventDefault();
					if (ok && event.code === "Enter") {
						el_ok_button.click();
					} else if (cancel && event.code === "Escape") {
						el_cancel_button.click();
					}
				};
			});
		}

		__windows.push(el_modal);
		document.body.appendChild(el_modal);
		__activateWindow(el_modal);

		return promise;
	};

	self.setElementEnabled = function(el, enabled) {
		if (!enabled && document.activeElement === el) {
			let el_to_focus = (
				el.closest(".modal-window")
				|| el.closest(".window")
				|| el.closest(".menu")
			);
			if (el_to_focus) {
				el_to_focus.focus();
			}
		}
		el.disabled = !enabled;
	};

	self.setRadioEnabled = function(name, enabled) {
		for (let el of $$$(`input[type="radio"][name="${name}"]`)) {
			self.setElementEnabled(el, enabled);
		}
	};

	self.showWindow = function(el_window, activate=true, center=false) {
		let showed = false;
		if (el_window.style.visibility === "hidden") {
			center = true;
			showed = true;
		}
		__organizeWindow(el_window, center);
		el_window.style.visibility = "visible";
		if (activate) {
			__activateWindow(el_window);
		}
		if (Object.prototype.hasOwnProperty.call(el_window, "show_hook")) {
			if (showed) {
				el_window.show_hook();
			}
		}
	};

	self.isWindowVisible = function(el_window) {
		return (window.getComputedStyle(el_window, null).visibility !== "hidden");
	};

	self.getViewGeometry = function() {
		let el_navbar = $("navbar");
		return {
			top: (el_navbar ? el_navbar.clientHeight : 0), // Navbar height
			bottom: Math.max(document.documentElement.clientHeight, window.innerHeight || 0),
			left: 0,
			right: Math.max(document.documentElement.clientWidth, window.innerWidth || 0),
		};
	};

	var __closeWindow = function(el_window) {
		el_window.focus();
		el_window.blur();
		el_window.style.visibility = "hidden";
	};

	var __toggleMenu = function(el_a) {
		let all_hidden = true;

		for (let el_button of __menu_buttons) {
			let el_menu = el_button.parentElement.querySelector(".menu");
			if (el_button === el_a && window.getComputedStyle(el_menu, null).visibility === "hidden") {
				let rect = el_menu.getBoundingClientRect();
				let offset = self.getViewGeometry().right - (rect.x + el_menu.clientWidth + 2); // + 2 is ugly hack
				if (offset < 0) {
					el_menu.style.right = "0px";
				} else {
					el_menu.style.removeProperty("right");
				}

				el_button.classList.add("menu-button-pressed");
				el_menu.style.visibility = "visible";
				el_menu.focus();
				all_hidden &= false;
			} else {
				el_button.classList.remove("menu-button-pressed");
				el_menu.style.visibility = "hidden";
				el_menu.style.removeProperty("right");
			}
		}

		if (all_hidden) {
			document.onkeyup = null;
			__activateLastWindow();
		} else {
			document.onkeyup = function(event) {
				if (event.code === "Escape") {
					event.preventDefault();
					__closeAllMenues();
					__activateLastWindow();
				}
			};
		}
	};

	var __closeAllMenues = function() {
		document.onkeyup = null;
		for (let el_button of __menu_buttons) {
			let el_menu = el_button.parentElement.querySelector(".menu");
			el_button.classList.remove("menu-button-pressed");
			el_menu.style.visibility = "hidden";
			el_menu.style.removeProperty("right");
		}
	};

	var __focusIn = function(event) {
		let el_parent;
		if ((el_parent = event.target.closest(".modal-window")) !== null) {
			el_parent.classList.add("window-active");
		} else if ((el_parent = event.target.closest(".window")) !== null) {
			el_parent.classList.add("window-active");
		} else if ((el_parent = event.target.closest(".menu")) !== null) {
			el_parent.classList.add("menu-active");
		}
		tools.debug("UI: Focus in:", el_parent);
	};

	var __focusOut = function(event) {
		let el_parent;
		if ((el_parent = event.target.closest(".modal-window")) !== null) {
			el_parent.classList.remove("window-active");
		} else if ((el_parent = event.target.closest(".window")) !== null) {
			el_parent.classList.remove("window-active");
		} else if ((el_parent = event.target.closest(".menu")) !== null) {
			el_parent.classList.remove("menu-active");
		}
		tools.debug("UI: Focus out:", el_parent);
	};

	var __globalMouseButtonHandler = function(event) {
		if (!event.target.matches(".menu-button") && !event.target.closest(".modal")) {
			for (let el_item = event.target; el_item && el_item !== document; el_item = el_item.parentNode) {
				if (el_item.hasAttribute("data-force-hide-menu")) {
					break;
				} else if (el_item.hasAttribute("data-dont-hide-menu")) {
					return;
				}
			}
			__closeAllMenues();
			__activateLastWindow();
		}
	};

	var __organizeWindowsOnResize = function() {
		for (let el_window of $$("window")) {
			if (el_window.style.visibility === "visible") {
				__organizeWindow(el_window);
			}
		}
	};

	var __organizeWindow = function(el_window, center=false) {
		let view = self.getViewGeometry();
		let rect = el_window.getBoundingClientRect();

		if (el_window.hasAttribute("data-centered") || center) {
			el_window.style.top = Math.max(view.top, Math.round((view.bottom - rect.height) / 2)) + "px";
			el_window.style.left = Math.round((view.right - rect.width) / 2) + "px";
			el_window.setAttribute("data-centered", "");
		} else {
			if (rect.top <= view.top) {
				el_window.style.top = view.top + "px";
			} else if (rect.bottom > view.bottom) {
				el_window.style.top = view.bottom - rect.height + "px";
			}

			if (rect.left <= view.left) {
				el_window.style.left = view.left + "px";
			} else if (rect.right > view.right) {
				el_window.style.left = view.right - rect.width + "px";
			}
		}
	};

	var __activateLastWindow = function(el_except_window=null) {
		let el_last_window = null;

		if (document.activeElement) {
			el_last_window = (document.activeElement.closest(".modal-window") || document.activeElement.closest(".window"));
			if (el_last_window && window.getComputedStyle(el_last_window, null).visibility === "hidden") {
				el_last_window = null;
			}
		}

		if (!el_last_window || el_last_window === el_except_window) {
			let max_z_index = 0;

			for (let el_window of __windows) {
				let z_index = parseInt(window.getComputedStyle(el_window, null).zIndex) || 0;
				let visibility = window.getComputedStyle(el_window, null).visibility;

				if (max_z_index < z_index && visibility !== "hidden" && el_window !== el_except_window) {
					el_last_window = el_window;
					max_z_index = z_index;
				}
			}
		}

		if (el_last_window) {
			tools.debug("UI: Activating last window:", el_last_window);
			__activateWindow(el_last_window);
		} else {
			tools.debug("UI: No last window to activation");
		}
	};

	var __activateWindow = function(el_window) {
		if (window.getComputedStyle(el_window, null).visibility !== "hidden") {
			let el_to_focus;
			let el_window_contains_focus;

			if (el_window.className === "modal") {
				el_to_focus = el_window.querySelector(".modal-window");
				el_window_contains_focus = (document.activeElement && document.activeElement.closest(".modal-window"));
			} else { // .window
				el_to_focus = el_window;
				el_window_contains_focus = (document.activeElement && document.activeElement.closest(".window"));
			}

			if (el_window.className !== "modal" && parseInt(el_window.style.zIndex) !== __top_z_index) {
				__top_z_index += 1;
				el_window.style.zIndex = __top_z_index;
				tools.debug("UI: Activated window:", el_window);
			}

			if (el_window !== el_window_contains_focus) {
				el_to_focus.focus();
				tools.debug("UI: Focused window:", el_window);
			}
		}
	};

	var __makeWindowMovable = function(el_window) {
		let el_header = el_window.querySelector(".window-header");
		let el_grab = el_window.querySelector(".window-header .window-grab");

		let prev_pos = {x: 0, y: 0};

		function startMoving(event) {
			__closeAllMenues();
			__activateWindow(el_window);
			event = (event || window.event);
			event.preventDefault();

			if (!event.touches || event.touches.length === 1) {
				el_header.classList.add("window-header-grabbed");

				prev_pos = getEventPosition(event);

				document.onmousemove = doMoving;
				document.onmouseup = stopMoving;

				document.ontouchmove = doMoving;
				document.ontouchend = stopMoving;
			}
		}

		function doMoving(event) {
			el_window.removeAttribute("data-centered");

			event = (event || window.event);
			event.preventDefault();

			let event_pos = getEventPosition(event);
			let x = prev_pos.x - event_pos.x;
			let y = prev_pos.y - event_pos.y;

			el_window.style.top = (el_window.offsetTop - y) + "px";
			el_window.style.left = (el_window.offsetLeft - x) + "px";

			prev_pos = event_pos;
		}

		function stopMoving() {
			el_header.classList.remove("window-header-grabbed");

			document.onmousemove = null;
			document.onmouseup = null;

			document.ontouchmove = null;
			document.ontouchend = null;
		}

		function getEventPosition(event) {
			if (event.touches) {
				return {x: event.touches[0].clientX, y: event.touches[0].clientY};
			} else {
				return {x: event.clientX, y: event.clientY};
			}
		}

		el_window.setAttribute("data-centered", "");
		el_window.onmousedown = el_window.ontouchstart = () => __activateWindow(el_window);

		el_grab.onmousedown = startMoving;
		el_grab.ontouchstart = startMoving;
	};

	__init__();
}
