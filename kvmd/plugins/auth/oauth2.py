import urllib
from urllib.parse import urlencode
import secrets
import time

import aiohttp
from aiohttp import ClientSession
from yarl import URL

from ...validators.os import valid_stripped_string_not_empty
from ...yamlconf import Option
from . import OAuthService

from ...logging import get_logger


class Plugin(OAuthService):
    def __init__(
            self,
            client_id: str,
            client_secret: str,
            access_token_url: str,
            authorize_url: str,
            base_url: str,
            user_info_url: str,
            short_name: str,
            long_name: str,
            scope: str,
            username_attribute: str,
            **params
    ) -> None:
        super().__init__(short_name, long_name)
        self.__client_id = client_id
        self.__client_secret = client_secret
        self.__access_token_url: URL = URL(access_token_url)
        self.__authorize_url: URL = URL(authorize_url)
        self.__base_url: URL = URL(base_url)
        self.__user_info_url: URL = URL(user_info_url)
        self.__scope = scope
        self.__username_attribute = username_attribute
        self.__params = params
        self.__states: list[OAuthState] = []

    @classmethod
    def get_plugin_options(cls) -> dict:
        return {
            "client_id": Option(""),
            "client_secret": Option(""),
            "access_token_url": Option(""),
            "authorize_url": Option(""),
            "base_url": Option(""),
            "user_info_url": Option(""),
            "short_name": Option(""),
            "long_name": Option(""),
            "scope": Option(""),
            "username_attribute": Option(""),
        }

    def is_redirect_from_provider(self, request_query) -> bool:
        return "code" in request_query    # TODO

    def get_authorize_url(self, redirect_url: URL, session, **kwargs) -> str:
        """
        Generates the Authorization-Code-Request (Step 2)
        """
        params: dict[str, str] = {}
        for param in self.__params:
            params.update({param: self.__params[param]})
        for param in kwargs:
            params.update({param: kwargs[param]})
        params.update(
            {
                "client_id": self.__client_id,
                "response_type": "code",
                "scope": self.__scope,
                "access_type": "offline",
                "state": session['state'],
                "redirect_uri": redirect_url.human_repr(),
            }
        )
        ret = f"{self.__authorize_url}?{urllib.parse.urlencode(params)}"
        return ret

    def is_valid_session(self, oauth_session) -> bool:
        if 'state' not in oauth_session:
            return False
        for stored_state in self.__states:
            if oauth_session['state'] == stored_state.get_value():
                if not stored_state.is_valid():
                    self.__states.pop(oauth_session['state'])
                    return False
        return True

    async def get_user_info(
            self,
            oauth_session,
            request_query,
            redirect_url
    ):
        if not self.is_valid_session(oauth_session):
            raise OAuthService.OAuthException(message="unknown or invalid state")

        payload = {
            "grant_type": "authorization_code",
            "client_id": self.__client_id,
            "client_secret": self.__client_secret,
            "code": request_query['code'],
            "redirect_uri": str(redirect_url),
            "state": oauth_session['state']
        }
        get_logger().warning("PAYLOAD: "+str(payload))
        headers = {"content-type": "application/x-www-form-urlencoded"}
        async with ClientSession() as session:
            try:
                async with session.post(self.__access_token_url, data=payload, headers=headers) as resp:
                    token_data = await resp.json()
                    if 'access_token' not in token_data:
                        get_logger().exception(str(token_data))
                        raise OAuthService.OAuthException(message=f"could not get access-token{str(token_data)}")
                    access_token = token_data.get("access_token")
            except aiohttp.ClientConnectorError as e:
                raise OAuthService.OAuthException(message="could not connect to provider! error message: %s" % str(e))

            headers = {
                "Cache-Control": "no-cache",
            }
            payload = {
                "access_token": access_token
            }
            try:
                async with session.get(self.__user_info_url.with_query(payload), headers=headers) as response:
                    user_info = await response.json()
                    return user_info[self.__username_attribute]
            except aiohttp.ClientConnectorError as e:
                raise OAuthService.OAuthException(message="could not connect to provider! error message: %s" % str(e))

    def register_new_session(self):
        state = OAuthState()
        self.__states.append(state)
        return {'state': state.get_value()}


class OAuthState:
    _TTL = 3600.0  # valid for one hour

    def __init__(self):
        self.state = secrets.token_urlsafe(16)
        self.__created = time.time()

    def __eq__(self, other):
        if isinstance(other, OAuthState):
            return self.state == other.state
        return False

    def __getitem__(self, item):
        if item == self.state:
            return self

    def is_valid(self):
        return (self.__created + self._TTL) > time.time()

    def get_value(self):
        return self.state
