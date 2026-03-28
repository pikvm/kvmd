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


import dataclasses


# =====
@dataclasses.dataclass(frozen=True)
class NbdImage:
    url:  str
    size: int
    rw:   bool


# =====
class BaseNbdEvent:
    pass


@dataclasses.dataclass(frozen=True)
class NbdSetupEvent(BaseNbdEvent):
    image: NbdImage


@dataclasses.dataclass(frozen=True)
class NbdStartEvent(BaseNbdEvent):
    pass


@dataclasses.dataclass(frozen=True)
class NbdStatusEvent(BaseNbdEvent):
    online: bool
    msg:    str


@dataclasses.dataclass(frozen=True)
class NbdStopEvent(BaseNbdEvent):
    src: str
    msg: str
    ok:  bool


# =====
@dataclasses.dataclass(frozen=True)
class NbdStopped:
    image:  NbdImage
    result: NbdStopEvent


@dataclasses.dataclass(frozen=True)
class NbdState:
    image:   (NbdImage | None) = dataclasses.field(default=None)
    bound:   str = dataclasses.field(default="")
    changed: (NbdStatusEvent | None) = dataclasses.field(default=None)
    stopped: (NbdStopped | None) = dataclasses.field(default=None)
