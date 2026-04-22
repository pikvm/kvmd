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


import asyncio
import sys
import os
import signal
import argparse
import time

from luma.core import cmdline as luma_cmdline

from PIL import ImageFont

from .. import InitAttrs
from .. import init

from .screen import Screen
from .sensors import Sensors


# =====
async def _run_mode_image(screen: Screen, interval: float, image_path: str) -> None:
    await screen.draw_image(image_path)
    await asyncio.sleep(interval)


async def _run_mode_text(screen: Screen, interval: float, text: str) -> None:
    await screen.draw_text(text.replace("\\n", "\n"))
    await asyncio.sleep(interval)


async def _run_mode_pipe(screen: Screen, interval: float) -> None:
    text = ""
    for line in sys.stdin:
        text += line
        if "\0" in text:
            await screen.draw_text(text.replace("\0", ""))
            text = ""
    await asyncio.sleep(interval)


async def _run_mode_default(screen: Screen, interval: float, fahrenheit: bool, ia: InitAttrs) -> bool:
    stop_reason: (str | None) = None

    def sigusr_handler(signum: int, _) -> None:  # type: ignore
        nonlocal stop_reason
        if signum in (signal.SIGINT, signal.SIGTERM):
            stop_reason = ""
        elif signum == signal.SIGUSR1:
            stop_reason = "Rebooting...\nPlease wait"
        elif signum == signal.SIGUSR2:
            stop_reason = "Halted"

    for signum in [signal.SIGTERM, signal.SIGINT, signal.SIGUSR1, signal.SIGUSR2]:
        signal.signal(signum, sigusr_handler)

    async with Sensors(
        kvmd=ia.make_kvmd_client("-OLED"),
        fahrenheit=fahrenheit,
    ) as sensors:

        await screen.set_swimming(60, 3)

        async def draw_and_sleep(text: str) -> None:
            await screen.set_contrast(not sensors.has_clients())
            await screen.draw_text(sensors.render(text))
            await asyncio.sleep(interval)

        if screen.get_height() >= 64:
            while stop_reason is None:
                text = (
                    "{fqdn}\n"
                    "{ip}\n"
                    "iface: {iface}\n"
                    "temp: {temp}\n"
                    "cpu: {cpu} mem: {mem}\n"
                    "({hb} {clients}) {uptime}"
                )
                await draw_and_sleep(text)
        else:
            summary = True
            while stop_reason is None:
                if summary:
                    text = (
                        "{fqdn}\n"
                        "({hb} {clients}) {uptime}\n"
                        "temp: {temp}"
                    )
                else:
                    text = (
                        "{ip}\n"
                        "({hb}) iface: {iface}\n"
                        "cpu: {cpu} mem: {mem}"
                    )
                await draw_and_sleep(text)
                summary = bool(time.monotonic() // 6 % 2)

    force_keep_content = False
    if stop_reason is not None:
        if len(stop_reason) > 0:
            force_keep_content = True
            await screen.set_swimming(0, 0)
            await screen.draw_text(stop_reason)
        while len(stop_reason) > 0:
            await asyncio.sleep(0.1)
    return force_keep_content


async def _run(ia: InitAttrs, options: argparse.Namespace) -> None:
    device = luma_cmdline.create_device(options)
    device.cleanup = (lambda _: None)
    screen = Screen(
        device=device,
        font=ImageFont.truetype(options.font, options.font_size),
        font_spacing=options.font_spacing,
        offset=(options.offset_x, options.offset_y),
        contrast_normal=options.contrast,
        contrast_low=options.low_contrast,
    )

    force_keep_content = False
    try:
        await screen.set_contrast(True)
        if options.image:
            await _run_mode_image(screen, options.interval, options.image)
        elif options.text:
            await _run_mode_text(screen, options.interval, options.text)
        elif options.pipe:
            await _run_mode_pipe(screen, options.interval)
        elif options.fill:
            await screen.draw_white()
        else:
            force_keep_content = await _run_mode_default(screen, options.interval, options.fahrenheit, ia)
    except KeyboardInterrupt:
        raise SystemExit("Interrupted by Ctrl+C")

    if options.clear_on_exit and not force_keep_content:
        await screen.draw_text("")


def _get_data_path(subdir: str, name: str) -> str:
    if not name.startswith("@"):
        return name  # Just a regular system path
    name = name[1:]
    module_path = sys.modules[__name__].__file__
    assert module_path is not None
    return os.path.join(os.path.dirname(module_path), subdir, name)


# =====
def main() -> None:
    ia = init(add_help=False)
    parser = argparse.ArgumentParser(
        prog="kvmd-oled",
        description="Display some info on PiKVM OLED display",
        parents=[ia.parser],
    )
    luma_cmdline.create_parser("", parser=parser)

    parser.add_argument("--font", default="@ProggySquare.ttf", type=(lambda arg: _get_data_path("fonts", arg)), help="Font path")
    parser.add_argument("--font-size", default=16, type=int, help="Font size")
    parser.add_argument("--font-spacing", default=2, type=int, help="Font line spacing")
    parser.add_argument("--offset-x", default=0, type=int, help="Horizontal offset")
    parser.add_argument("--offset-y", default=0, type=int, help="Vertical offset")
    parser.add_argument("--interval", default=0.5, type=float, help="Screens interval")

    # Display modes
    parser.add_argument("--image", default="", type=(lambda arg: _get_data_path("pics", arg)),
                        help="Display some image, wait a single interval and exit")
    parser.add_argument("--text", default="",
                        help="Display some text, wait a single interval and exit")
    parser.add_argument("--pipe", action="store_true",
                        help="Read and display lines from stdin until EOF, wait a single interval and exit")
    parser.add_argument("--fill", action="store_true",
                        help="Fill the display with 0xFF")
    parser.add_argument("--clear-on-exit", action="store_true", help="Clear display on exit")

    # Compatibility
    parser.add_argument("--contrast", type=int,
                        help="Set OLED contrast, values from 0 to 255")
    parser.add_argument("--low-contrast", type=int,
                        help="Set OLED contrast when device is used")
    parser.add_argument("--fahrenheit", action="store_true",
                        help="Display temperature in Fahrenheit instead of Celsius")

    parser.set_defaults(
        # Device-specific from the config
        width=ia.config.oled.width,
        height=ia.config.oled.height,
        rotate=ia.config.oled.rotate,
        # Compatibility
        fahrenheit=ia.config.oled.fahrenheit,
        low_contrast=ia.config.oled.contrast.low,
        contrast=ia.config.oled.contrast.normal,
    )

    options = parser.parse_args(ia.args)
    if options.config:  # Luma config, not KVMD
        options = parser.parse_args(
            luma_cmdline.load_config(options.config)
            + ia.args
        )

    options.contrast = min(max(options.contrast, 0), 255)
    options.low_contrast = min(max(options.low_contrast, 0), 255)

    asyncio.run(_run(ia, options))
