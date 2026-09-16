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
import json

import pytest

from . import get_configured_auth_service


# =====
@pytest.mark.asyncio
async def test_ok__onetime_service(tmpdir) -> None:  # type: ignore
    path = os.path.abspath(str(tmpdir.join("otpasswd")))
    async with get_configured_auth_service("onetime", file=path) as service:
        with open(path) as file:
            data = json.load(file)
            user = data["user"]
            passwd = data["passwd"]
        assert user == "onetime"
        assert len(passwd) == 8
        assert not (await service.authorize("onetime", "foo"))
        assert (await service.authorize("onetime", passwd))
        assert not (await service.authorize("", passwd))
        assert not (await service.authorize(" ", passwd))
        assert not (await service.authorize("onetime ", passwd))
        assert not (await service.authorize("onetime", passwd + " "))
        assert not (await service.authorize("onetime", ""))
        assert not (await service.authorize("onetime", "foo"))
        assert not (await service.authorize("admin", "foo"))
        assert not (await service.authorize("user", "pass"))
        assert not (await service.authorize("admin", "pass"))
        assert not (await service.authorize("admin", "admin"))
        assert not (await service.authorize("admin", ""))
        assert not (await service.authorize("admin", ""))
        assert not (await service.authorize(" ", " "))
        assert not (await service.authorize("", ""))
        assert (await service.authorize("onetime", passwd))
        assert not (await service.authorize("onetime", "foo"))


@pytest.mark.asyncio
async def test_ok__ontime_changing_service(tmpdir) -> None:  # type: ignore
    path = os.path.abspath(str(tmpdir.join("otpasswd")))
    async with get_configured_auth_service("onetime", file=path, change_after_login=True) as service:
        with open(path) as file:
            d1 = json.load(file)
            u1 = d1["user"]
            p1 = d1["passwd"]
        assert u1 == "onetime"
        assert len(p1) == 8

        assert not (await service.authorize("onetime", "foo"))
        assert (await service.authorize("onetime", p1))
        assert not (await service.authorize("onetime", p1))

        del d1
        del u1

        with open(path) as file:
            d2 = json.load(file)
            u2 = d2["user"]
            p2 = d2["passwd"]
        assert u2 == "onetime"
        assert len(p2) == 8

        assert p1 != p2
        del p1

        assert not (await service.authorize("onetime", "foo"))
        assert (await service.authorize("onetime", p2))
        assert not (await service.authorize("onetime", p2))
