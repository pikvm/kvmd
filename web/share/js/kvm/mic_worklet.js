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


// The mono capturer for the direct stream mode. It collects the audio
// samples to the frames of the required size and sends them to the main
// thread as Float32Array.
class MicProcessor extends AudioWorkletProcessor {
	constructor(options) {
		super();
		this.__size = options.processorOptions.size;
		this.__buf = new Float32Array(this.__size);
		this.__used = 0;
	}

	process(inputs) {
		let chunk = ((inputs.length > 0 && inputs[0].length > 0) ? inputs[0][0] : null);
		if (chunk) {
			for (let index = 0; index < chunk.length; ++index) {
				this.__buf[this.__used] = chunk[index];
				this.__used += 1;
				if (this.__used >= this.__size) {
					let frame = this.__buf.slice(0);
					this.port.postMessage(frame, [frame.buffer]);
					this.__used = 0;
				}
			}
		}
		return true;
	}
}

registerProcessor("kvmd-mic", MicProcessor);
