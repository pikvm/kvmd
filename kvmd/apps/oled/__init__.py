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

from ...logging import get_logger

from ... import htclient

from ...clients.kvmd import KvmdClient

from .. import init

from .screen import Screen
from .sensors import Sensors


# =====
def _get_data_path(subdir: str, name: str) -> str:
    if not name.startswith("@"):
        return name  # Just a regular system path
    name = name[1:]
    module_path = sys.modules[__name__].__file__
    assert module_path is not None
    return os.path.join(os.path.dirname(module_path), subdir, name)


async def _run(options: argparse.Namespace) -> None:  # pylint: disable=too-many-branches,too-many-statements
    logger = get_logger(0)

    device = luma_cmdline.create_device(options)
    device.cleanup = (lambda _: None)
    screen = Screen(
        device=device,
        font=ImageFont.truetype(options.font, options.font_size),
        font_spacing=options.font_spacing,
        offset=(options.offset_x, options.offset_y),
    )

    if options.display not in luma_cmdline.get_display_types()["emulator"]:
        logger.info("Iface: %s", options.interface)
    logger.info("Display: %s", options.display)
    logger.info("Size: %dx%d", device.width, device.height)

    try:
        await screen.set_contrast(options.contrast)

        if options.image:
            await screen.draw_image(options.image)
            await asyncio.sleep(options.interval)

        elif options.text:
            await screen.draw_text(options.text.replace("\\n", "\n"))
            await asyncio.sleep(options.interval)

        elif options.pipe:
            text = ""
            for line in sys.stdin:
                text += line
                if "\0" in text:
                    await screen.draw_text(text.replace("\0", ""))
                    text = ""
            await asyncio.sleep(options.interval)

        elif options.fill:
            await screen.draw_white()

        else:
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
                kvmd=(
                    KvmdClient(
                        unix_path=options.kvmd_unix,
                        timeout=options.kvmd_timeout,
                        user_agent=htclient.make_user_agent("KVMD-OLED"),
                    ) if options.kvmd_unix else None
                ),
                fahrenheit=options.fahrenheit,
            ) as sensors:

                await screen.set_swimming(60, 3)

                async def draw_and_sleep(text: str) -> None:
                    await screen.set_contrast(options.low_contrast if sensors.has_clients() else options.contrast)
                    await screen.draw_text(sensors.render(text))
                    await asyncio.sleep(options.interval)

                if device.height >= 64:
                    while stop_reason is None:
                        text = "{fqdn}\n{ip}\niface: {iface}\ntemp: {temp}\ncpu: {cpu} mem: {mem}\n({hb} {clients}) {uptime}"
                        await draw_and_sleep(text)
                else:
                    summary = True
                    while stop_reason is None:
                        if summary:
                            text = "{fqdn}\n({hb} {clients}) {uptime}\ntemp: {temp}"
                        else:
                            text = "{ip}\n({hb}) iface: {iface}\ncpu: {cpu} mem: {mem}"
                        await draw_and_sleep(text)
                        summary = bool(time.monotonic() // 6 % 2)

            if stop_reason is not None:
                if len(stop_reason) > 0:
                    options.clear_on_exit = False
                    await screen.set_swimming(0, 0)
                    await screen.draw_text(stop_reason)
                while len(stop_reason) > 0:
                    await asyncio.sleep(0.1)

    except (SystemExit, KeyboardInterrupt):
        pass

    if options.clear_on_exit:
        await screen.draw_text("")


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
    parser.add_argument("--image", default="", type=(lambda arg: _get_data_path("pics", arg)), help="Display some image, wait a single interval and exit")
    parser.add_argument("--text", default="", help="Display some text, wait a single interval and exit")
    parser.add_argument("--pipe", action="store_true", help="Read and display lines from stdin until EOF, wait a single interval and exit")
    parser.add_argument("--fill", action="store_true", help="Fill the display with 0xFF")
    parser.add_argument("--clear-on-exit", action="store_true", help="Clear display on exit")
    # Compatibility options below
    parser.add_argument("--contrast", type=int, help="Set OLED contrast, values from 0 to 255")
    parser.add_argument("--low-contrast", type=int, help="Set OLED contrast when device is used")
    parser.add_argument("--fahrenheit", action="store_true", help="Display temperature in Fahrenheit instead of Celsius")
    # parser.add_argument("--kvmd-unix", help="Ask some info from KVMD like a clients count")
    # parser.add_argument("--kvmd-timeout", type=float, help="Timeout for KVMD requests")
    parser.set_defaults(
        width=ia.config.oled.width,
        height=ia.config.oled.height,
        rotate=ia.config.oled.rotate,
        fahrenheit=ia.config.oled.fahrenheit,
        low_contrast=ia.config.oled.contrast.low,
        contrast=ia.config.oled.contrast.normal,
        kvmd_unix=ia.config.oled.kvmd.unix,
        kvmd_timeout=ia.config.oled.kvmd.timeout,
    )
    options = parser.parse_args(ia.args)
    if options.config:
        options = parser.parse_args(
            luma_cmdline.load_config(options.config)
            + ia.args
        )
    options.contrast = min(max(options.contrast, 0), 255)
    options.low_contrast = min(max(options.low_contrast, 0), 255)

    asyncio.run(_run(options))
