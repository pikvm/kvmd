# ========================================================================== #
#                                                                            #
#    KVMD - The main PiKVM daemon.                                           #
#                                                                            #
#    Copyright (C) 2018-2024  Maxim Devaev <mdevaev@gmail.com>               #
#                  2024-2024  Markus Beckschulte (SLA/RWTH Aachen)           #
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
import json
from typing import Any
from typing import Self

from aiohttp.web import Request
from aiohttp.web import Response
from aiohttp.web import HTTPFound
from aiohttp.web import HTTPNotFound
from aiohttp.web import HTTPUnauthorized

from cryptography import fernet
from cryptography.fernet import InvalidToken
from yarl import URL

from ...htserver import ForbiddenError
from ...htserver import exposed_http
from ...htserver import make_json_response

from ...validators.auth import valid_user

from ...apps.kvmd.router import HttpRouter
from ...apps.kvmd.auth import AuthManager
from ...apps.kvmd.api.auth import _COOKIE_AUTH_TOKEN

from ..oauth import BaseOAuthProvider, get_oauth_provider_class
from . import BaseAuthFlowService


_COOKIE_OAUTH_SESSION = "oauth_session"


class OAuthApi:
    def __init__(self, plugin: "Plugin"):
        self.__plugin: "Plugin" = plugin

    @exposed_http("GET", "/auth/flow/oauth/providers", auth_required=False, allow_usc=False)
    async def __oauth_providers(self, _: Request) -> Response:
        """
        Return a json containing the available Providers with short_name and long_name and if oauth is enabled
        @param request:
        @return: json with provider infos
        """
        response: dict[str, (bool | dict)] = {}
        if self.__plugin is None:
            response.update({"enabled": False})
        else:
            response.update({"enabled": True, "providers": self.__plugin.get_providers()})
        return make_json_response(response)

    @exposed_http("GET", "/auth/flow/oauth/login/{provider}", auth_required=False, allow_usc=False)
    async def __oauth(self, request: Request) -> None:
        """
        Creates the redirect to the Provider specified in the URL. Checks if the provider is valid.
        Also sets a cookie containing session information.
        @param request:
        @return: redirect to provider
        """
        provider = format(request.match_info["provider"])
        if not self.__plugin.valid_provider(provider):
            raise HTTPNotFound(reason="Unknown provider %s" % provider)

        redirect_url = request.url.with_path(f"/api/auth/flow/oauth/callback/{provider}").with_scheme("https")
        oauth_cookie = request.cookies.get(_COOKIE_OAUTH_SESSION, "")

        is_valid_session = await self.__plugin.is_valid_session(provider, oauth_cookie)
        if not is_valid_session:
            session = await self.__plugin.register_new_session(provider)
        else:
            session = oauth_cookie

        response = HTTPFound(
            await self.__plugin.get_authorize_url(
                provider=provider, redirect_url=redirect_url, session=session,
            )
        )
        response.set_cookie(name=_COOKIE_OAUTH_SESSION, value=session, secure=True, httponly=True, samesite="Lax")

        # 302 redirect to provider:
        raise response

    @exposed_http("GET", "/auth/flow/oauth/callback/{provider}", auth_required=False, allow_usc=False)
    async def __callback(self, request: Request) -> Response:
        """
        After successful login on the side of the provider, the user gets redirected here. If everything is correct,
        the user gets logged in with the username provided by the Provider.
        @param request:
        @return:
        """
        if not request.match_info["provider"]:
            raise HTTPUnauthorized(reason="Provider is missing")
        provider = format(request.match_info["provider"])
        if not self.__plugin.valid_provider(provider):
            raise HTTPNotFound(reason="Unknown provider %s" % provider)

        if _COOKIE_OAUTH_SESSION not in request.cookies.keys():
            raise HTTPUnauthorized(reason="Cookie is missing")
        oauth_session = request.cookies[_COOKIE_OAUTH_SESSION]

        if not self.__plugin.is_redirect_from_provider(provider=provider, request_query=dict(request.query)):
            raise HTTPUnauthorized(reason="Authorization Code is missing")

        redirect_url = request.url.with_path(f"/api/auth/oauth/callback/{provider}").with_scheme("https")
        user = await self.__plugin.get_user_info(
            provider=provider,
            oauth_session=oauth_session,
            request_query=dict(request.query),
            redirect_url=redirect_url
        )
        if not user:
            raise ForbiddenError()

        # pylint: disable=protected-access
        token = await self.__plugin._manager.login_external(
            user=valid_user(user)
        )
        if not token:
            raise ForbiddenError()

        response = HTTPFound(
            request.url.with_path("").with_scheme("https")
        )
        response.set_cookie(name=_COOKIE_AUTH_TOKEN, value=token, samesite="Lax", httponly=True)
        return response


class Plugin(BaseAuthFlowService):
    # pylint: disable=super-init-not-called
    def __init__(self, *, manager: AuthManager, providers: dict) -> None:
        """
        Initializes the OAuth authentication flow plugin.
        1. Creates OAuthSessionStorage with a random key
        2. Iterates over the providers and initializes them
        @param manager: the AuthManager instance
        @param providers: the configured providers
        """
        self._manager: AuthManager = manager
        self.__api: OAuthApi = OAuthApi(self)
        self.__router: HttpRouter = HttpRouter(manager)
        self.__session_storage: OAuthSessionStorage = OAuthSessionStorage(fernet.Fernet.generate_key())
        self.__providers: dict[str, BaseOAuthProvider] = {}

        self.__router.add_exposed(self.__api)

        for provider, data in providers.items():
            # TODO: this is ad-hoc schema validation, refactor this and extract schema
            service_type = data.pop("type")
            self.__providers.update({
                provider: get_oauth_provider_class(service_type)(**data)
            })

    def api(self) -> object:
        return self.__api

    async def dispatch(self, req: Request, subpath: str | None = None) -> Response:
        return await self.__router.dispatch(req, subpath)

    def valid_provider(self, provider: str) -> bool:
        """
        checks if the given provider exists.
        @param provider: provider to be checked
        @return: True: provider exists
        """
        return provider in self.__providers

    def get_providers(self) -> dict[str, str]:  # short_name: long_name
        """
        Returns a dict mapping the short_name of a provider to the long_name
        @return: dict short_name: long_name
        """
        ret = {}
        for short_name, provider in self.__providers.items():
            ret[short_name] = provider.get_long_name()
        return ret

    def is_redirect_from_provider(self, provider: str, request_query: dict) -> bool:
        """
        Delegates the request_query to the appropriate provider and returns its response.
        @param provider: provider this is meant for
        @param request_query: the request_query
        @return: True: is a redirect from provider
        """
        return self.__providers[provider].is_redirect_from_provider(request_query)

    async def get_authorize_url(self, provider: str, redirect_url: URL, session: str) -> str:
        """
        Lets the appropriate provider form an authorize URL the user should be redirected to
        @param provider: provider this is meant for
        @param redirect_url: the URL the user should be redirected to after successful login
        @param session: the oauth_session cookie
        @return: authorization URL, or authorization code request
        """
        session_decrypted = await self.__session_storage.get_session_data(session)
        return self.__providers[provider].get_authorize_url(
            redirect_url=redirect_url,
            session=session_decrypted,
        )

    async def get_user_info(
            self,
            provider: str,
            oauth_session: str,
            request_query: dict,
            redirect_url: URL
    ) -> str:
        """
        Returns the Username it gets by the provider
        @param provider: provider this is meant for
        @param oauth_session: the encrypted oauth_session cookie
        @param request_query: the query of the request
        @param redirect_url: the redirect URL the used with get_authorize_url
        @return:
        """
        session = await self.__session_storage.get_session_data(oauth_session)
        return await self.__providers[provider].get_user_info(
            oauth_session=session,
            request_query=request_query,
            redirect_url=redirect_url
        )

    async def register_new_session(self, provider: str) -> str:
        """
        Registers a new session and returns it.
        @param provider: provider this is meant for
        @return: the new encrypted session
        """
        provider_session_data = self.__providers[provider].register_new_session()
        session = await self.__session_storage.set_session_data(provider_session_data)
        return session

    async def is_valid_session(self, provider: str, session: str) -> bool:
        """
        Checks if the provided session is valid.
        @param provider: provider this is meant for
        @param session: the encrypted oauth_session
        @return: True: is valid session
        """
        if session == "":
            return False
        try:
            session_data = await self.__session_storage.get_session_data(session)
        except InvalidToken:
            return False
        return self.__providers[provider].is_valid_session(session_data)


class OAuthSessionStorage:
    def __init__(self, secure_key: bytes):
        """
        Initiates a new OAuthSessionStorage and stores the cipher.
        @param secure_key: bytes to generate the cipher
        """
        self.__cipher = fernet.Fernet(secure_key)

    async def __encrypt_data(self, data: str) -> str:
        """
        encrypts the given session
        @param data: the session represented by a string
        @return: encrypted session
        """
        encrypted_data = self.__cipher.encrypt(data.encode())
        return base64.urlsafe_b64encode(encrypted_data).decode()

    async def __decrypt_data(self, encrypted_data: str) -> str:
        """
        Decrypts a session to a string representation
        @param encrypted_data: the encrypted session
        @return: decrypted session as string
        """
        decrypted_data = self.__cipher.decrypt(base64.urlsafe_b64decode(encrypted_data)).decode()
        return decrypted_data

    async def set_session_data(self, data: dict) -> str:
        """
        Encrypts a dict and returns its representation as string
        @param data: the session to encrypt
        @return: encrypted session
        """
        encrypted_data = await self.__encrypt_data(json.dumps(data))
        return encrypted_data

    async def get_session_data(self, oauth_cookie: str) -> dict:
        """
        decrypts a given session to a dict
        @param oauth_cookie: the encrypted session
        @return: the decrypted session
        """
        if oauth_cookie:
            decrypted_data = await self.__decrypt_data(oauth_cookie)
            return json.loads(decrypted_data)
        else:
            return {}

    async def logout(self, token: str) -> None:
        pass


class User:
    def __init__(self, user_name: str, provider: BaseOAuthProvider):
        self.__provider: BaseOAuthProvider = provider
        self.__provider_data: Any
        self.__user_name: str = user_name

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, User):  # because of this, we will be able "access a protected member"
            return self.__user_name == other.__user_name  # pylint: disable=W0212
        return False

    def __getitem__(self, item: Any) -> Self | None:
        if item == self.__user_name:
            return self
        return None
