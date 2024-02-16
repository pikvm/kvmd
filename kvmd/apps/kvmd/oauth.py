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

from cryptography import fernet
from cryptography.fernet import InvalidToken
from yarl import URL

from kvmd.plugins.auth import OAuthService, get_oauth_service_class


class OAuthManager:
    def __init__(
        self,
        oauth_providers: dict,
    ) -> None:
        """
        Initializes the OAuthManager.
        1. creates OAuthSessionStorage with random key
        2. iterates over the providers and initializes them
        @param oauth_providers: the configured providers
        """
        self.__session_storage = OAuthSessionStorage(fernet.Fernet.generate_key())
        self.__providers: dict[str, OAuthService] = {}

        for provider, data in oauth_providers.items():
            service_type = data.pop("type")
            self.__providers.update(
                {
                    provider: get_oauth_service_class(service_type)(**data)
                }
            )

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
            session = await self.__session_storage.get_session_data(session)
        except InvalidToken:
            return False
        return self.__providers[provider].is_valid_session(session)


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
        decrypts a given session to a string
        @param oauth_cookie: the encrypted session
        @return: the decrypted session
        """
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

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, User):  # because of this, we will be able "access a protected member"
            return self.__user_name == other.__user_name  # pylint: disable=W0212
        return False

    def __getitem__(self, item: Any):
        if item == self.__user_name:
            return self
        return None
