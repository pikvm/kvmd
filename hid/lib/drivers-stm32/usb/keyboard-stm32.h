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
#include "keymap.h"
#include "tools.h"

namespace DRIVERS {

	const uint8_t reportDescriptionKeyboard[] = {
		HID_KEYBOARD_REPORT_DESCRIPTOR(),
	};

	class UsbKeyboard : public Keyboard {
		public:
			UsbKeyboard(HidWrapper& _hidWrapper) : Keyboard(USB_KEYBOARD),
				_hidWrapper(_hidWrapper), _keyboard(_hidWrapper.usbHid) {
				_hidWrapper.addReportDescriptor(reportDescriptionKeyboard, sizeof(reportDescriptionKeyboard));
			}

			void begin() override {
				_hidWrapper.begin();
				_keyboard.begin();
			}

			void clear() override {
				_keyboard.releaseAll();
			}

			void sendKey(uint8_t code, bool state) override {
				uint16_t usb_code = keymapUsb(code);
				if (usb_code != KEY_ERROR_UNDEFINED) {
					usb_code += KEY_HID_OFFSET;
					state ? _keyboard.press(usb_code) : _keyboard.release(usb_code);
				}
			}

			void periodic() override {
#if 0
				static unsigned long start_ts = 0;
				if (is_micros_timed_out(start_ts, 2000000)) {
					if (_hidWrapper.serial())
						_hidWrapper.serial()->println(_keyboard.getLEDs());
					for(int i = 0; i < 38; ++i) {
						sendKey(i, true);
						sendKey(i, false);
					}
					start_ts = micros();
				}
#endif
			}

			bool isOffline() override {
				return USBComposite == false;
			}

			KeyboardLedsState getLeds() override {
				uint8 leds = _keyboard.getLEDs();
				KeyboardLedsState result = {
					.caps = leds & 0b00000010,
					.scroll = false, //TODO how to implement this???
					.num = leds & 0b00000001,
				};
				return result;
			}

		private:
			HidWrapper& _hidWrapper;
			HIDKeyboard _keyboard;
			static constexpr uint8 KEY_ERROR_UNDEFINED = 3;
	};
}
