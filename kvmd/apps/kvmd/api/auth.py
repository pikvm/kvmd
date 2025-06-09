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
from aiohttp.web_exceptions import HTTPNotFound, HTTPFound, HTTPUnauthorized

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

from ..auth import AuthManager


# =====
_COOKIE_AUTH_TOKEN = "auth_token"
_COOKIE_OAUTH_SESSION = "oauth-session"


async def _check_xhdr(auth_manager: AuthManager, _: HttpExposed, req: Request) -> bool:
    user = req.headers.get("X-KVMD-User", "")
    if user:
        user = valid_user(user)
        passwd = req.headers.get("X-KVMD-Passwd", "")
        set_request_auth_info(req, f"{user} (xhdr)")
        if (await auth_manager.authorize(user, valid_passwd(passwd))):
            return True
        raise ForbiddenError()
    return False


async def _check_token(auth_manager: AuthManager, _: HttpExposed, req: Request) -> bool:
    token = req.cookies.get(_COOKIE_AUTH_TOKEN, "")
    if token:
        user = auth_manager.check(valid_auth_token(token))
        if user:
            set_request_auth_info(req, f"{user} (token)")
            return True
        set_request_auth_info(req, "- (token)")
        raise ForbiddenError()
    return False


async def _check_basic(auth_manager: AuthManager, _: HttpExposed, req: Request) -> bool:
    basic_auth = req.headers.get("Authorization", "")
    if basic_auth and basic_auth[:6].lower() == "basic ":
        try:
            (user, passwd) = base64.b64decode(basic_auth[6:]).decode("utf-8").split(":")
        except Exception:
            raise UnauthorizedError()
        user = valid_user(user)
        set_request_auth_info(req, f"{user} (basic)")
        if (await auth_manager.authorize(user, valid_passwd(passwd))):
            return True
        raise ForbiddenError()
    return False


async def _check_usc(auth_manager: AuthManager, exposed: HttpExposed, req: Request) -> bool:
    if exposed.allow_usc:
        creds = get_request_unix_credentials(req)
        if creds is not None:
            user = auth_manager.check_unix_credentials(creds)
            if user:
                set_request_auth_info(req, f"{user}[{creds.uid}] (unix)")
                return True
        raise UnauthorizedError()
    return False


async def check_request_auth(auth_manager: AuthManager, exposed: HttpExposed, req: Request) -> None:
    if not auth_manager.is_auth_required(exposed):
        return
    for checker in [_check_xhdr, _check_token, _check_basic, _check_usc]:
        if (await checker(auth_manager, exposed, req)):
            return
    raise UnauthorizedError()


class AuthApi:
    def __init__(self, auth_manager: AuthManager) -> None:
        self.__auth_manager = auth_manager

    # =====

    @exposed_http("POST", "/auth/login", auth_required=False, allow_usc=False)
    async def __login_handler(self, req: Request) -> Response:
        if self.__auth_manager.is_auth_enabled():
            credentials = await req.post()
            token = await self.__auth_manager.login(
                user=valid_user(credentials.get("user", "")),
                passwd=valid_passwd(credentials.get("passwd", "")),
                expire=valid_expire(credentials.get("expire", "0")),
            )
            if token:
                return make_json_response(set_cookies={_COOKIE_AUTH_TOKEN: token})
            raise ForbiddenError()
        return make_json_response()

    @exposed_http("POST", "/auth/logout", allow_usc=False)
    async def __logout_handler(self, req: Request) -> Response:
        if self.__auth_manager.is_auth_enabled():
            token = valid_auth_token(req.cookies.get(_COOKIE_AUTH_TOKEN, ""))
            self.__auth_manager.logout(token)
        return make_json_response()

    # XXX: This handle is used for access control so it should NEVER allow access by socket credentials
    @exposed_http("GET", "/auth/check", allow_usc=False)
    async def __check_handler(self, _: Request) -> Response:
        return make_json_response()

    @exposed_http("GET", "/auth/oauth/providers", auth_required=False)
    async def __oauth_providers(self, request: Request) -> Response:
        """
        Return a json containing the available Providers with short_name and long_name and if oauth is enabled
        @param request:
        @return: json with provider infos
        """
        response: dict[str, (bool | dict)] = {}
        if self.__auth_manager.oauth_manager is None:
            response.update({'enabled': False})
        else:
            response.update({'enabled': True, 'providers': self.__auth_manager.oauth_manager.get_providers()})
        return make_json_response(response)

    @exposed_http("GET", "/auth/oauth/login/{provider}", auth_required=False)
    async def __oauth(self, request: Request) -> None:
        """
        Creates the redirect to the Provider specified in the URL. Checks if the provider is valid.
        Also sets a cookie containing session information.
        @param request:
        @return: redirect to provider
        """
        if self.__auth_manager.oauth_manager is None:
            return
        provider = format(request.match_info['provider'])
        if not self.__auth_manager.oauth_manager.valid_provider(provider):
            raise HTTPNotFound(reason="Unknown provider %s" % provider)

        redirect_url = request.url.with_path(f"/api/auth/oauth/callback/{provider}").with_scheme('https')
        oauth_cookie = request.cookies.get(_COOKIE_OAUTH_SESSION, "")

        is_valid_session = await self.__auth_manager.oauth_manager.is_valid_session(provider, oauth_cookie)
        if not is_valid_session:
            session = await self.__auth_manager.oauth_manager.register_new_session(provider)
        else:
            session = oauth_cookie

        response = HTTPFound(
            await self.__auth_manager.oauth_manager.get_authorize_url(
                provider=provider, redirect_url=redirect_url, session=session,
            )
        )
        response.set_cookie(name=_COOKIE_OAUTH_SESSION, value=session, secure=True, httponly=True, samesite="Lax")

        # 302 redirect to provider:
        raise response

    @exposed_http("GET", "/auth/oauth/callback/{provider}", auth_required=False)
    async def __callback(self, request: Request) -> Response:
        """
        After successful login on the side of the provider, the user gets redirected here. If everything is correct,
        the user gets logged in with the username provided by the Provider.
        @param request:
        @return:
        """
        if self.__auth_manager.oauth_manager is None:
            return make_json_response()

        if not request.match_info['provider']:
            raise HTTPUnauthorized(reason="Provider is missing")
        provider = format(request.match_info['provider'])
        if not self.__auth_manager.oauth_manager.valid_provider(provider):
            raise HTTPNotFound(reason="Unknown provider %s" % provider)

        if _COOKIE_OAUTH_SESSION not in request.cookies.keys():
            raise HTTPUnauthorized(reason="Cookie is missing")
        oauth_session = request.cookies[_COOKIE_OAUTH_SESSION]

        if not self.__auth_manager.oauth_manager.is_redirect_from_provider(provider=provider, request_query=dict(request.query)):
            raise HTTPUnauthorized(reason="Authorization Code is missing")

        redirect_url = request.url.with_query("").with_path(f"/api/auth/oauth/callback/{provider}").with_scheme('https')
        user = await self.__auth_manager.oauth_manager.get_user_info(
            provider=provider,
            oauth_session=oauth_session,
            request_query=dict(request.query),
            redirect_url=redirect_url
        )

        if self.__auth_manager.is_auth_enabled():
            token = await self.__auth_manager.login_oauth(
                user=valid_user(user)
            )
            if token:
                return make_json_response(set_cookies={_COOKIE_AUTH_TOKEN: token})
            raise ForbiddenError()
        return make_json_response()
