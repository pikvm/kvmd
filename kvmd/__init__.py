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


__version__ = "4.188"


# FIXME: Migrate to some other start method
import multiprocessing


multiprocessing.set_start_method("fork")


# FIXME: Do something with bcrypt bug
#   - https://github.com/pyca/bcrypt/pull/1000/changes
#   - https://gitlab.archlinux.org/archlinux/packaging/packages/python-passlib/-/work_items/2
#   - https://gitlab.archlinux.org/archlinux/packaging/packages/python-passlib/-/work_items/3
try:
    import bcrypt  # noqa E402  # pylint: disable=wrong-import-position
except ModuleNotFoundError:
    pass
else:
    bcrypt_hashpw_orig = bcrypt.hashpw

    def bcrypt_hashpw_fixed(password, salt):  # type: ignore
        return bcrypt_hashpw_orig(password[:72], salt)

    bcrypt.hashpw = bcrypt_hashpw_fixed
