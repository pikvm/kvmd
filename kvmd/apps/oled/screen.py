# ========================================================================== #
#                                                                            #
#    KVMD-OLED - A small OLED daemon for PiKVM.                              #
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
# ========================================================================== #


import time

import async_lru

from luma.core.device import device as luma_device
from luma.core.render import canvas as luma_canvas

from PIL import Image
from PIL import ImageFont

from ... import aiotools


# =====
class Screen:  # pylint: disable=too-many-instance-attributes
    def __init__(
        self,
        device: luma_device,
        font: ImageFont.FreeTypeFont,
        font_spacing: int,
        offset: tuple[int, int],
    ) -> None:

        self.__device = device
        self.__font = font
        self.__font_spacing = font_spacing
        self.__offset = offset

        self.__swim_interval = 0.0
        self.__swim_offset_x = 0
        self.__swim_after_ts = time.monotonic() + self.__swim_interval
        self.__swim_state = True

    async def set_swimming(self, interval: float, offset_x: int) -> None:
        self.__swim_interval = interval
        self.__swim_offset_x = offset_x

    @async_lru.alru_cache(maxsize=1)
    async def set_contrast(self, contrast: int) -> None:
        await aiotools.run_async(self.__device.contrast, contrast)

    async def draw_text(self, text: str) -> None:
        await aiotools.run_async(self.__inner_draw_text, text)

    async def draw_image(self, image_path: str) -> None:
        await aiotools.run_async(self.__inner_draw_image, image_path)

    async def draw_white(self) -> None:
        await aiotools.run_async(self.__inner_draw_white)

    def __inner_draw_text(self, text: str) -> None:
        with luma_canvas(self.__device) as draw:
            draw.multiline_text(self.__get_offset(), text, font=self.__font, spacing=self.__font_spacing, fill="white")

    def __inner_draw_image(self, image_path: str) -> None:
        with luma_canvas(self.__device) as draw:
            draw.bitmap(self.__get_offset(), Image.open(image_path).convert("1"), fill="white")

    def __inner_draw_white(self) -> None:
        with luma_canvas(self.__device) as draw:
            draw.rectangle((0, 0, self.__device.width, self.__device.height), fill="white")

    def __get_offset(self) -> tuple[int, int]:
        if self.__swim_interval >= 0:
            now_ts = time.monotonic()
            if now_ts >= self.__swim_after_ts:
                self.__swim_state = (not self.__swim_state)
                self.__swim_after_ts = now_ts + self.__swim_interval
            return (self.__offset[0] + (self.__swim_state * self.__swim_offset_x), self.__offset[1])
        return self.__offset
