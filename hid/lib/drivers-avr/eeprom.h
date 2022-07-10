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

#include "storage.h"
#ifdef HID_DYNAMIC
#include <avr/eeprom.h>
#endif

namespace DRIVERS {

	struct Eeprom : public Storage {
		using Storage::Storage;

		void read_block (void *_dst, const void *_src, size_t _n) override {
			eeprom_read_block(_dst, _src, _n);
		}

		void update_block (const void *_src, void *_dst, size_t _n) override {
			eeprom_update_block(_src, _dst, _n);
		}
	};
}
