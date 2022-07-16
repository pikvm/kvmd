/*****************************************************************************
#                                                                            #
#    KVMD - The main PiKVM daemon.                                           #
#                                                                            #
#    Copyright (C) 2018-2022  Maxim Devaev <mdevaev@gmail.com>               #
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

#pragma once

#include "keyboard.h"
#include "hid-wrapper-stm32.h"
#include <USBComposite.h>

namespace DRIVERS {

	const uint8_t reportDescriptionKeyboard[] = {
		HID_BOOT_KEYBOARD_REPORT_DESCRIPTOR(),
	};

	class UsbKeyboard : public Keyboard {
		public:
			UsbKeyboard(HidWrapper& _hidWrapper) : Keyboard(USB_KEYBOARD),
				_hidWrapper(_hidWrapper), _bootKeyboard(_hidWrapper.usbHid, 0) {
				_hidWrapper.addReportDescriptor(reportDescriptionKeyboard, sizeof(reportDescriptionKeyboard));
			}

			void begin() override {
				_hidWrapper.begin();
				_bootKeyboard.begin();
			}

			void clear() override {
				_bootKeyboard.releaseAll();
			}

			bool isOffline() override {
				return USBComposite == false;
			}

			KeyboardLedsState getLeds() override {
				uint8 leds = _bootKeyboard.getLEDs();
				KeyboardLedsState result = {
					.caps = leds & 0b00000010,
					.scroll = false, //TODO how to implement this???
					.num = leds & 0b00000001,
				};
				return result;
			}

		private:
			HidWrapper& _hidWrapper;
			HIDKeyboard _bootKeyboard;
	};
}
