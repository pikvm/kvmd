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


import dataclasses
import contextlib

from typing import Callable
from typing import AsyncGenerator

import aiohttp

from ... import htclient


# =====
@dataclasses.dataclass(frozen=True)
class DownloadingFile:
    name: str
    size: int
    read: Callable[[int], AsyncGenerator[bytes]]


@contextlib.asynccontextmanager
async def download(
    url: str,
    verify: bool,
    timeout: float,
    read_timeout: float,
    user_agent: str="",
) -> AsyncGenerator[DownloadingFile]:

    async with aiohttp.ClientSession(
        headers={aiohttp.hdrs.USER_AGENT: htclient.make_user_agent(user_agent)},
        timeout=aiohttp.ClientTimeout(
            connect=timeout,
            sock_connect=timeout,
            sock_read=read_timeout,
        ),
    ) as session:

        async with session.get(url, verify_ssl=verify) as resp:  # type: ignore
            htclient.raise_not_200(resp)

            name = htclient.get_filename(resp)

            size = resp.content_length
            if size is None or size < 0:
                raise aiohttp.ClientError("No Content-Length found")

            # Make it unified for the future API
            async def read(chunk_size: int) -> AsyncGenerator[bytes]:
                async for chunk in resp.content.iter_chunked(chunk_size):
                    yield chunk

            yield DownloadingFile(name, size, read)
