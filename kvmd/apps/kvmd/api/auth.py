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


import base64

from aiohttp.web import Request
from aiohttp.web import Response
from aiohttp.web import HTTPFound

from ....htserver import UnauthorizedError
from ....htserver import ForbiddenError
from ....htserver import HttpExposed
from ....htserver import exposed_http
from ....htserver import make_json_response
from ....htserver import set_request_auth_info
from ....htserver import get_request_unix_credentials

from ....validators.auth import valid_user
from ....validators.auth import valid_passwd
from ....validators.auth import valid_expire
from ....validators.auth import valid_auth_token
from ....validators.auth import valid_login_redirect

from ..auth import AuthManager


# =====
_COOKIE_AUTH_TOKEN = "auth_token"


async def _check_xhdr(auth: AuthManager, _: HttpExposed, req: Request) -> bool:
    user = req.headers.get("X-KVMD-User", "")
    if user:
        user = valid_user(user)
        passwd = req.headers.get("X-KVMD-Passwd", "")
        set_request_auth_info(req, f"{user} (xhdr)")
        if (await auth.authorize(user, valid_passwd(passwd))):
            return True
        raise ForbiddenError()
    return False


async def _check_token(auth: AuthManager, _: HttpExposed, req: Request) -> bool:
    token = req.cookies.get(_COOKIE_AUTH_TOKEN, "")
    if token:
        user = auth.check(valid_auth_token(token))
        if user:
            set_request_auth_info(req, f"{user} (token)", token=token)
            return True
        set_request_auth_info(req, "- (token)")
        raise ForbiddenError()
    return False


async def _check_basic(auth: AuthManager, _: HttpExposed, req: Request) -> bool:
    basic_auth = req.headers.get("Authorization", "")
    if basic_auth and basic_auth[:6].lower() == "basic ":
        try:
            (user, passwd) = base64.b64decode(basic_auth[6:]).decode("utf-8").split(":")
        except Exception:
            raise UnauthorizedError()
        user = valid_user(user)
        set_request_auth_info(req, f"{user} (basic)")
        if (await auth.authorize(user, valid_passwd(passwd))):
            return True
        raise ForbiddenError()
    return False


async def _check_usc(auth: AuthManager, exposed: HttpExposed, req: Request) -> bool:
    if exposed.allow_usc:
        creds = get_request_unix_credentials(req)
        if creds is not None:
            user = auth.check_unix_credentials(creds)
            if user:
                set_request_auth_info(req, f"{user}[{creds.uid}] (unix)")
                return True
        raise UnauthorizedError()
    return False


async def check_request_auth(auth: AuthManager, exposed: HttpExposed, req: Request) -> None:
    if not auth.is_auth_required(exposed):
        return
    for checker in [_check_xhdr, _check_token, _check_basic, _check_usc]:
        if (await checker(auth, exposed, req)):
            return
    raise UnauthorizedError()


class AuthApi:
    def __init__(self, auth: AuthManager) -> None:
        self.__auth = auth

    # =====

    @exposed_http("POST", "/auth/login", auth_required=False, allow_usc=False)
    async def __login_handler(self, req: Request) -> Response:
        if self.__auth.is_auth_enabled():
            params = await req.post()
            redirect = valid_login_redirect(params.get("redirect", ""))
            token = await self.__auth.login(
                user=valid_user(params.get("user", "")),
                passwd=valid_passwd(params.get("passwd", "")),
                expire=valid_expire(params.get("expire", "0")),
            )
            if token:
                if redirect:
                    ex = HTTPFound(location=redirect)
                    ex.set_cookie(_COOKIE_AUTH_TOKEN, token)
                    raise ex
                return make_json_response(set_cookies={_COOKIE_AUTH_TOKEN: token})
            raise ForbiddenError()
        return make_json_response()

    @exposed_http("POST", "/auth/logout", allow_usc=False)
    async def __logout_handler(self, req: Request) -> Response:
        if self.__auth.is_auth_enabled():
            token = valid_auth_token(req.cookies.get(_COOKIE_AUTH_TOKEN, ""))
            self.__auth.logout(token)
        return make_json_response()

    # XXX: This handle is used for access control so it should NEVER allow access by socket credentials
    @exposed_http("GET", "/auth/check", allow_usc=False)
    async def __check_handler(self, _: Request) -> Response:
        return make_json_response()
