import base64
import json
from typing import Any

from cryptography import fernet
from cryptography.fernet import InvalidToken
from yarl import URL

from kvmd.logging import get_logger
from kvmd.plugins.auth import OAuthService, get_oauth_service_class


class OAuthManager:
    def __init__(
        self,
        oauth_providers: dict,
    ) -> None:
        self.__session_storage = OAuthSessionStorage(fernet.Fernet.generate_key())
        self.__providers: dict[str, OAuthService] = {}
        get_logger().warning("HALLO" + str(oauth_providers))

        for provider, data in oauth_providers.items():
            get_logger().warning("type: "+data['type'])
            get_logger().warning("type: "+data['type'])
            service_type = data.pop("type")
            self.__providers.update(
                {
                    provider: get_oauth_service_class(service_type)(**data)
                }
            )

    def valid_provider(self, provider: str) -> bool:
        return provider in self.__providers

    def get_providers(self) -> dict[str, str]:  # short_name: long_name
        ret = {}
        for provider in self.__providers:
            ret[provider] = self.__providers[provider].get_long_name()
        return ret

    def is_redirect_from_provider(self, provider: str, request_query) -> bool:
        return self.__providers[provider].is_redirect_from_provider(request_query)

    async def get_authorize_url(self, provider: str, redirect_url: URL, session: str, **kwargs) -> str:
        params = kwargs
        session_decrypted = await self.__session_storage.get_session_data(session)
        return self.__providers[provider].get_authorize_url(
            redirect_url=redirect_url,
            session=session_decrypted,
            **params
        )

    async def get_user_info(
            self,
            provider: str,
            oauth_session,
            request_query,
            redirect_url
    ):
        session = await self.__session_storage.get_session_data(oauth_session)
        return await self.__providers[provider].get_user_info(
            oauth_session=session,
            request_query=request_query,
            redirect_url=redirect_url
        )

    async def get_session_data(self, cookie: str):
        return await self.__session_storage.get_session_data(cookie)

    async def register_new_session(self, provider):
        provider_session_data = self.__providers[provider].register_new_session()
        session = await self.__session_storage.set_session_data(provider_session_data)
        return session

    async def is_valid_session(self, provider, cookie):
        if cookie == "":
            return False
        try:
            session = await self.__session_storage.get_session_data(cookie)
        except InvalidToken:
            return False
        return self.__providers[provider].is_valid_session(session)


class OAuthSessionStorage:
    def __init__(self, secure_key):
        __secure_key = secure_key
        self.__cipher = fernet.Fernet(secure_key)

    async def __encrypt_data(self, data):
        encrypted_data = self.__cipher.encrypt(data.encode())
        return base64.urlsafe_b64encode(encrypted_data).decode()

    async def __decrypt_data(self, encrypted_data):
        decrypted_data = self.__cipher.decrypt(base64.urlsafe_b64decode(encrypted_data)).decode()
        return decrypted_data

    async def set_session_data(self, data):
        encrypted_data = await self.__encrypt_data(json.dumps(data))
        return encrypted_data

    async def get_session_data(self, oauth_cookie: str):
        if oauth_cookie:
            decrypted_data = await self.__decrypt_data(oauth_cookie)
            return json.loads(decrypted_data)
        else:
            return {}

    async def logout(self, token: str):
        pass


class User:
    def __init__(self, user_name: str, provider: OAuthService):
        self.__provider: OAuthService = provider
        self.__provider_data: Any
        self.__user_name: str = user_name

    def __eq__(self, other):
        if isinstance(other, User):
            return self.__user_name == other.__user_name
        return False

    def __getitem__(self, item):
        if item == self.__user_name:
            return self
