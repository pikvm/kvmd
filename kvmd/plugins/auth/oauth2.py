from typing import Any
from urllib.parse import urlencode
import secrets
import time

import aiohttp
from aiohttp import ClientSession
from yarl import URL

from ...yamlconf import Option
from . import OAuthService, OAuthException


class Plugin(OAuthService):  # pylint: disable=too-many-instance-attributes
    def __init__(  # pylint: disable=too-many-arguments
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
            username_attribute: str
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

    def is_redirect_from_provider(self, request_query: dict) -> bool:
        return "code" in request_query    # TODO

    def get_authorize_url(self, redirect_url: URL, session: dict) -> str:
        """
        Generates the Authorization-Code-Request (Step 2)
        """
        params: dict[str, str] = {}
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
        ret = f"{self.__authorize_url}?{urlencode(params)}"
        return ret

    def is_valid_session(self, oauth_session: dict) -> bool:
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
            oauth_session: dict,
            request_query: dict,
            redirect_url: URL
    ) -> str:
        if not self.is_valid_session(oauth_session):
            raise OAuthException(message="unknown or invalid state")

        payload = {
            "grant_type": "authorization_code",
            "client_id": self.__client_id,
            "client_secret": self.__client_secret,
            "code": request_query['code'],
            "redirect_uri": str(redirect_url),
            "state": oauth_session['state']
        }
        headers = {"content-type": "application/x-www-form-urlencoded"}
        async with ClientSession() as session:
            try:
                async with session.post(self.__access_token_url, data=payload, headers=headers) as resp:
                    token_data = await resp.json()
                    if 'access_token' not in token_data:
                        raise OAuthException(message=f"could not get access-token{str(token_data)}")
                    access_token = token_data.get("access_token")
            except aiohttp.ClientConnectorError as error:
                raise OAuthException(message="could not connect to provider! error message: %s" % str(error))

            headers = {
                "Cache-Control": "no-cache",
                "Authorization": f"Bearer {access_token}"
            }
            try:
                async with session.get(self.__user_info_url, headers=headers) as response:
                    user_info = await response.json()
                    return user_info[self.__username_attribute]
            except aiohttp.ClientConnectorError as error:
                raise OAuthException(message="could not connect to provider! error message: %s" % str(error))

    def register_new_session(self) -> dict:
        state = OAuthState()
        self.__states.append(state)
        return {'state': state.get_value()}


class OAuthState:
    _TTL = 3600.0  # valid for one hour

    def __init__(self) -> None:
        self.state = secrets.token_urlsafe(16)
        self.__created = time.time()

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, OAuthState):
            return self.state == other.state
        return False

    def __getitem__(self, item: Any):
        if item == self.state:
            return self
        return None

    def is_valid(self) -> bool:
        return (self.__created + self._TTL) > time.time()

    def get_value(self) -> str:
        return self.state
