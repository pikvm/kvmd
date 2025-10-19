# ========================================================================== #
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
# ========================================================================== #


import os
import argparse

import mako.template

from ... import network

from .. import init


# =====
def main() -> None:
    ia = init(add_help=False)
    parser = argparse.ArgumentParser(
        prog="kvmd-nginx-mkconf",
        description="Generate KVMD-Nginx config",
        parents=[ia.parser],
    )
    parser.add_argument("-p", "--print", action="store_true", help="Print the result to stdout besides the output file")
    parser.add_argument("input", help="Input Mako template")
    parser.add_argument("output", help="Output Nginx config")
    options = parser.parse_args(ia.args)

    with open(options.input, "r") as in_file:
        template = in_file.read()

    rendered = mako.template.Template(template).render(
        http_ipv4=ia.config.nginx.http.ipv4,
        http_ipv6=ia.config.nginx.http.ipv6,
        http_port=ia.config.nginx.http.port,
        https_enabled=ia.config.nginx.https.enabled,
        https_ipv4=ia.config.nginx.https.ipv4,
        https_ipv6=ia.config.nginx.https.ipv6,
        https_port=ia.config.nginx.https.port,
        ipv6_enabled=network.is_ipv6_enabled(),
    )

    if options.print:
        print(rendered)

    try:
        os.remove(options.output)
    except FileNotFoundError:
        pass

    with open(options.output, "w") as out_file:
        out_file.write(rendered)
