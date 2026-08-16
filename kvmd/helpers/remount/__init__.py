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
import stat
import pwd
import grp
import shutil
import subprocess

from ...fstab import Partition
from ...fstab import find_msd
from ...fstab import find_pst


# =====
def _log(msg: str) -> None:
    print(msg, file=sys.stderr)


def _remount(path: str, rw: bool) -> None:
    mode = ("rw" if rw else "ro")
    _log(f"Remounting {path} to {mode.upper()}-mode ...")
    try:
        subprocess.check_call(["/bin/mount", "--options", f"remount,{mode}", path])
    except subprocess.CalledProcessError as ex:
        raise SystemExit(f"Can't remount: {ex}")


def _mkdir(path: str) -> None:
    if not os.path.exists(path):
        _log(f"MKDIR --- {path}")
        try:
            os.mkdir(path)
        except Exception as ex:
            raise SystemExit(f"Can't create directory: {ex}")


def _chown(path: str, user: str) -> None:
    if pwd.getpwuid(os.stat(path).st_uid).pw_name != user:
        _log(f"CHOWN --- {user} - {path}")
        try:
            shutil.chown(path, user=user)
        except Exception as ex:
            raise SystemExit(f"Can't change ownership: {ex}")


def _chgrp(path: str, group: str) -> None:
    if grp.getgrgid(os.stat(path).st_gid).gr_name != group:
        _log(f"CHGRP --- {group} - {path}")
        try:
            shutil.chown(path, group=group)
        except Exception as ex:
            raise SystemExit(f"Can't change group: {ex}")


def _chmod(path: str, mode: int) -> None:
    if stat.S_IMODE(os.stat(path).st_mode) != mode:
        _log(f"CHMOD --- 0o{mode:o} - {path}")
        try:
            os.chmod(path, mode)
        except Exception as ex:
            raise SystemExit(f"Can't change permissions: {ex}")


# =====
def _fix_msd(part: Partition) -> None:
    if part.user:
        _chown(part.root_path, part.user)
    if part.group:
        _chgrp(part.root_path, part.group)


def _fix_pst(part: Partition) -> None:
    path = os.path.join(part.root_path, "data")
    _mkdir(path)
    if part.user:
        _chown(part.root_path, part.user)
        _chown(path, part.user)
    if part.group:
        _chgrp(part.root_path, part.group)
        _chgrp(path, part.group)
    if part.user and part.group:
        _chmod(part.root_path, 0o1775)


# =====
def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in ["ro", "rw"]:
        raise SystemExit(f"Usage: {sys.argv[0]} [ro|rw]")

    finder = None
    fix = None
    app = os.path.basename(sys.argv[0])
    if app == "kvmd-helper-otgmsd-remount":
        finder = find_msd
        fix = _fix_msd
    elif app == "kvmd-helper-pst-remount":
        finder = find_pst
        fix = _fix_pst
    else:
        raise SystemExit("Unknown application target")

    rw = (sys.argv[1] == "rw")

    assert finder is not None
    part = finder()
    _remount(part.mount_path, rw)
    if rw and part.root_path:
        fix(part)
    _log(f"Storage in the {'RW' if rw else 'RO'}-mode now")
