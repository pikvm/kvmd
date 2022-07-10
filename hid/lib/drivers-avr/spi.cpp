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


#include "spi.h"

#include <Arduino.h>
#include <SPI.h>


static volatile uint8_t _spi_in[8] = {0};
static volatile uint8_t _spi_in_index = 0;

static volatile uint8_t _spi_out[8] = {0};
static volatile uint8_t _spi_out_index = 0;


void spiBegin() {
	pinMode(MISO, OUTPUT);
	SPCR = (1 << SPE) | (1 << SPIE); // Slave, SPI En, IRQ En
}

bool spiReady() {
	return (!_spi_out[0] && _spi_in_index == 8);
}

const uint8_t *spiGet() {
	return (const uint8_t *)_spi_in;
}

void spiWrite(const uint8_t *data) {
	// Меджик в нулевом байте разрешает начать ответ
	for (int index = 7; index >= 0; --index) {
		_spi_out[index] = data[index];
	}
}


ISR(SPI_STC_vect) {
	uint8_t in = SPDR;
	if (_spi_out[0] && _spi_out_index < 8) {
		SPDR = _spi_out[_spi_out_index];
		if (!(SPSR & (1 << WCOL))) {
			++_spi_out_index;
			if (_spi_out_index == 8) {
				_spi_out_index = 0;
				_spi_in_index = 0;
				_spi_out[0] = 0;
			}
		}
	} else {
		static bool receiving = false;
		if (!receiving && in != 0) {
			receiving = true;
		}
		if (receiving && _spi_in_index < 8) {
			_spi_in[_spi_in_index] = in;
			++_spi_in_index;
		}
		if (_spi_in_index == 8) {
			receiving = false;
		}
		SPDR = 0;
	}
}
