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

#include <stdint.h>
#include "driver.h"

namespace DRIVERS {

	typedef struct {
		bool caps;
		bool scroll;
		bool num;
	} KeyboardLedsState;


	struct Keyboard : public Driver {
		using Driver::Driver;
		
		virtual void begin() {}
	
		/**
		* Release all keys
		*/
		virtual void clear() {}
	
		/**
		* Sends key
		* @param code ???
		* @param state true pressed, false released
		*/
		virtual void sendKey(uint8_t code, bool state) {}
	
		virtual void periodic() {}
	
		/**
		* False if online or unknown. Otherwise true.
		*/
		virtual bool isOffline() { return false; }
	
		virtual KeyboardLedsState getLeds() {
			KeyboardLedsState result = {};
			return result;
		}

	};
}
