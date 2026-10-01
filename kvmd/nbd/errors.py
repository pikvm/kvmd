# ========================================================================== #
#                                                                            #
#    KVMD - The main PiKVM daemon.                                           #
#                                                                            #
#    Copyright (C) 2020  Maxim Devaev <mdevaev@gmail.com>                    #
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


from .. import tools

from ..errors import OperationError
from ..errors import IsBusyError


# =====
class NbdError(Exception):
    _DEFAULT_MSG = ""

    def __init__(self, msg: str="", ex: (Exception | None)=None) -> None:
        if not msg:
            msg = self._DEFAULT_MSG
        if ex:
            if msg:
                msg += ": "
            msg += tools.efmt(ex)
        super().__init__(msg)


class NbdOperationError(NbdError, OperationError):
    pass


class NbdIsBusyError(NbdError, IsBusyError):
    _DEFAULT_MSG = "Performing another NBD operation, please try again later"


# =====
class NbdControllerError(NbdOperationError):
    pass


class NbdBoundError(NbdControllerError):
    _DEFAULT_MSG = "NBD is already bound"


class NbdBindError(NbdControllerError):
    pass


class NbdProbeError(NbdControllerError):
    pass


# =====
class NbdDeviceError(NbdOperationError):
    pass


# =====
class NbdIoError(NbdOperationError):
    pass


class NbdIoConnectionError(NbdIoError):
    pass


class NbdIoProtocolError(NbdIoError):
    pass


# =====
class NbdRemoteError(NbdOperationError):
    pass
