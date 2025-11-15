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


import sys
import os
import copy
import dataclasses
import contextlib
import argparse

from typing import Generator
from typing import Any

from .. import tools

from ..plugins import UnknownPluginError

from ..yamlconf import ConfigError
from ..yamlconf import Section
from ..yamlconf import make_config
from ..yamlconf.loader import list_yaml_dir
from ..yamlconf.loader import load_yaml_file
from ..yamlconf.merger import yaml_merge
from ..yamlconf.dumper import dump_yaml
from ..yamlconf.dumper import override_yaml_file

from ..validators.os import valid_abs_path
from ..validators.os import valid_abs_file
from ..validators.os import valid_abs_dir

from ._logging import init_logging
from ._scheme import make_config_scheme
from ._scheme import patch_dynamic
from ._scheme import patch_raw


# =====
@dataclasses.dataclass(frozen=True)
class ConfigPaths:
    main:         str
    legacy_auth:  str
    override_dir: str
    override:     str


@dataclasses.dataclass(frozen=True)
class InitAttrs:
    parser: argparse.ArgumentParser
    args:   list[str]
    config: Section
    cps:    ConfigPaths


def init(
    prog: (str | None)=None,
    description: (str | None)=None,
    add_help: bool=True,
    check_run: bool=False,
    cli_logging: bool=False,
    test_args: (list[str] | None)=None,
    test_override: (dict | None)=None,
    **load: bool,
) -> InitAttrs:

    init_logging(cli_logging)

    prog = (prog or sys.argv[0])
    args = (test_args or sys.argv[1:])  # Remove app name from sys.argv

    parser = argparse.ArgumentParser(
        prog=prog,
        description=description,
        add_help=add_help,
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--main-config", default="/usr/lib/kvmd/main.yaml", type=valid_abs_file,
                        help="Set the main default config", metavar="<file>")
    parser.add_argument("--legacy-auth-config", default="/etc/kvmd/auth.yaml", type=valid_abs_path,
                        help="Set the auth config, which is applied before override (don't use it)", metavar="<file>")
    parser.add_argument("--override-dir", default="/etc/kvmd/override.d", type=valid_abs_dir,
                        help="Set the override.d directory", metavar="<dir>")
    parser.add_argument("--override-config", default="/etc/kvmd/override.yaml", type=valid_abs_file,
                        help="Set the override config", metavar="<file>")
    parser.add_argument("-m", "--dump-config", action="store_true",
                        help="View current configuration (include all overrides)")
    parser.add_argument("-M", "--dump-config-changes", action="store_true",
                        help="Similar to --dump-config, but shows only changed fields")
    if check_run:
        parser.add_argument("--run", dest="run", action="store_true",
                            help="Run the service")

    # Replace args for child parser
    (options, args) = parser.parse_known_args(list(args))
    cps = ConfigPaths(
        main=options.main_config,
        legacy_auth=options.legacy_auth_config,
        override_dir=options.override_dir,
        override=options.override_config,
    )

    dump_only = (options.dump_config or options.dump_config_changes)

    try:
        config = _init_config(
            cps=cps,
            test_override=test_override,
            load_all=dump_only,
            **load,
        )
    except ConfigError as ex:
        raise SystemExit(tools.efmt(ex))

    if dump_only:
        print(dump_yaml(
            data=config,
            only_changed=options.dump_config_changes,
            colored=sys.stdout.isatty(),
        ))
        raise SystemExit()

    if check_run and not options.run:
        raise SystemExit(
            "To prevent accidental startup, you must specify the --run option to start.\n"
            "Try the --help option to find out what this service does.\n"
            "Make sure you understand exactly what you are doing!"
        )

    return InitAttrs(
        parser=parser,
        args=list(args),
        config=config,
        cps=cps,
    )


@contextlib.contextmanager
def override_checked(cps: ConfigPaths) -> Generator[Any]:
    def validator(path: str) -> None:
        _init_config(
            cps=ConfigPaths(
                main=cps.main,
                legacy_auth=cps.legacy_auth,
                override_dir=cps.override_dir,
                override=path,
            ),
            test_override={},
            load_all=True,
        )

    try:
        with override_yaml_file(cps.override, validator) as doc:
            yield doc
    except ConfigError as ex:
        raise ConfigError(f"The resulting override turns invalid and will be discarded:\n{tools.efmt(ex)}")


# =====
def _checkload_yaml_file(path: str) -> dict:
    try:
        raw: dict = load_yaml_file(path)
    except Exception as ex:
        raise ConfigError(f"Can't read config file {path!r}:\n{tools.efmt(ex)}")
    if raw is None:
        return {}
    elif not isinstance(raw, dict):
        raise ConfigError(f"Top-level of the file {path!r} must be a dictionary")
    return raw


def _init_config(
    cps: ConfigPaths,
    test_override: (dict | None),
    **load: bool,  # Pass load_all=True to test full configuration
) -> Section:

    # Stage 1: Top-priority, considered as default
    main: dict = _checkload_yaml_file(cps.main)
    override: dict = {}

    # Stage 1.5: Legacy auth.yaml config, it shouln't be used anymore
    if os.path.isfile(cps.legacy_auth) or os.path.islink(cps.legacy_auth):
        yaml_merge(override, {"kvmd": {"auth": _checkload_yaml_file(cps.legacy_auth)}})

    # Stage 2: Directory for partial overrides
    for path in list_yaml_dir(cps.override_dir):
        yaml_merge(override, _checkload_yaml_file(path))

    # Stage 3: Manual overrides
    yaml_merge(override, _checkload_yaml_file(cps.override))

    # Stage 4: Test overrides
    if test_override is not None:
        yaml_merge(override, copy.deepcopy(test_override))

    patch_raw(main)
    patch_raw(override)

    scheme = make_config_scheme()
    try:
        config = make_config(main, override, scheme)
        if patch_dynamic(main, override, config, scheme, **load):
            config = make_config(main, override, scheme)
        return config
    except UnknownPluginError as ex:
        raise ConfigError(str(ex))  # We don't want to know too much about exception
