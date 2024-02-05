"""
Enable non-bot user login. ( against TOS yeah yeah yeah )

use the UserClient class instead of discord.Client class when initializing for a user.
"""

import logging
from typing import *

from urllib.parse import quote as _uriquote

import discord
from discord.http import *
from discord.user import ClientUser
from discord.client import _loop


_log = logging.getLogger(__name__)


class NonBotHTTPClient(discord.http.HTTPClient):
    """Copied the entire method, just to remove the 'bot' string from the token"""
    # learned something new : _className__privateVariable , then can access private variable !

    # 
    async def request(
        self,
        route,
        *,
        files: Optional[Sequence[File]] = None,
        form: Optional[Iterable[Dict[str, Any]]] = None,
        **kwargs: Any,
    ) -> Any:
        method = route.method
        url = route.url
        route_key = route.key

        bucket_hash = None
        try:
            bucket_hash = self._bucket_hashes[route_key]
        except KeyError:
            key = f'{route_key}:{route.major_parameters}'
        else:
            key = f'{bucket_hash}:{route.major_parameters}'

        ratelimit = self.get_ratelimit(key)

        # header creation
        headers: Dict[str, str] = {
            'User-Agent': self.user_agent,
        }

        if self.token is not None:
            headers['Authorization'] = self.token

        # some checking if it's a JSON request
        if 'json' in kwargs:
            headers['Content-Type'] = 'application/json'
            kwargs['data'] = utils._to_json(kwargs.pop('json'))

        try:
            reason = kwargs.pop('reason')
        except KeyError:
            pass
        else:
            if reason:
                headers['X-Audit-Log-Reason'] = _uriquote(reason, safe='/ ')

        kwargs['headers'] = headers

        # Proxy support
        if self.proxy is not None:
            kwargs['proxy'] = self.proxy
        if self.proxy_auth is not None:
            kwargs['proxy_auth'] = self.proxy_auth

        if not self._global_over.is_set():
            # wait until the global lock is complete
            await self._global_over.wait()

        response: Optional[aiohttp.ClientResponse] = None
        data: Optional[Union[Dict[str, Any], str]] = None
        async with ratelimit:
            for tries in range(5):
                if files:
                    for f in files:
                        f.reset(seek=tries)

                if form:
                    # with quote_fields=True '[' and ']' in file field names are escaped, which discord does not support
                    form_data = aiohttp.FormData(quote_fields=False)
                    for params in form:
                        form_data.add_field(**params)
                    kwargs['data'] = form_data

                try:
                    async with self._HTTPClient__session.request(method, url, **kwargs) as response: #type: ignore

                        if response is None:
                            raise RuntimeError("No response impossible wtf ?!!!!")
                        
                        _log.debug('%s %s with %s has returned %s', method, url, kwargs.get('data'), response.status)

                        # even errors have text involved in them so this is safe to call
                        data = await json_or_text(response)

                        # Update and use rate limit information if the bucket header is present
                        discord_hash = response.headers.get('X-Ratelimit-Bucket')
                        # I am unsure if X-Ratelimit-Bucket is always available
                        # However, X-Ratelimit-Remaining has been a consistent cornerstone that worked
                        has_ratelimit_headers = 'X-Ratelimit-Remaining' in response.headers
                        if discord_hash is not None:
                            # If the hash Discord has provided is somehow different from our current hash something changed
                            if bucket_hash != discord_hash:
                                if bucket_hash is not None:
                                    # If the previous hash was an actual Discord hash then this means the
                                    # hash has changed sporadically.
                                    # This can be due to two reasons
                                    # 1. It's a sub-ratelimit which is hard to handle
                                    # 2. The rate limit information genuinely changed
                                    # There is no good way to discern these, Discord doesn't provide a way to do so.
                                    # At best, there will be some form of logging to help catch it.
                                    # Alternating sub-ratelimits means that the requests oscillate between
                                    # different underlying rate limits -- this can lead to unexpected 429s
                                    # It is unavoidable.
                                    fmt = 'A route (%s) has changed hashes: %s -> %s.'
                                    _log.debug(fmt, route_key, bucket_hash, discord_hash)

                                    self._bucket_hashes[route_key] = discord_hash
                                    recalculated_key = discord_hash + route.major_parameters
                                    self._buckets[recalculated_key] = ratelimit
                                    self._buckets.pop(key, None)
                                elif route_key not in self._bucket_hashes:
                                    fmt = '%s has found its initial rate limit bucket hash (%s).'
                                    _log.debug(fmt, route_key, discord_hash)
                                    self._bucket_hashes[route_key] = discord_hash
                                    self._buckets[discord_hash + route.major_parameters] = ratelimit

                        if has_ratelimit_headers:
                            if response.status != 429:
                                ratelimit.update(response, use_clock=self.use_clock)
                                if ratelimit.remaining == 0:
                                    _log.debug(
                                        'A rate limit bucket (%s) has been exhausted. Pre-emptively rate limiting...',
                                        discord_hash or route_key,
                                    )

                        # the request was successful so just return the text/json
                        if 300 > response.status >= 200:
                            _log.debug('%s %s has received %s', method, url, data)
                            return data

                        # we are being rate limited
                        if response.status == 429:
                            if not response.headers.get('Via') or isinstance(data, str):
                                # Banned by Cloudflare more than likely.
                                raise HTTPException(response, data)

                            if ratelimit.remaining > 0:
                                # According to night
                                # https://github.com/discord/discord-api-docs/issues/2190#issuecomment-816363129
                                # Remaining > 0 and 429 means that a sub ratelimit was hit.
                                # It is unclear what should happen in these cases other than just using the retry_after
                                # value in the body.
                                _log.debug(
                                    '%s %s received a 429 despite having %s remaining requests. This is a sub-ratelimit.',
                                    method,
                                    url,
                                    ratelimit.remaining,
                                )

                            retry_after: float = data['retry_after']
                            if self.max_ratelimit_timeout and retry_after > self.max_ratelimit_timeout:
                                _log.warning(
                                    'We are being rate limited. %s %s responded with 429. Timeout of %.2f was too long, erroring instead.',
                                    method,
                                    url,
                                    retry_after,
                                )
                                raise RateLimited(retry_after)

                            fmt = 'We are being rate limited. %s %s responded with 429. Retrying in %.2f seconds.'
                            _log.warning(fmt, method, url, retry_after)

                            _log.debug(
                                'Rate limit is being handled by bucket hash %s with %r major parameters',
                                bucket_hash,
                                route.major_parameters,
                            )

                            # check if it's a global rate limit
                            is_global = data.get('global', False)
                            if is_global:
                                _log.warning('Global rate limit has been hit. Retrying in %.2f seconds.', retry_after)
                                self._global_over.clear()

                            await asyncio.sleep(retry_after)
                            _log.debug('Done sleeping for the rate limit. Retrying...')

                            # release the global lock now that the
                            # global rate limit has passed
                            if is_global:
                                self._global_over.set()
                                _log.debug('Global rate limit is now over.')

                            continue

                        # we've received a 500, 502, 504, or 524, unconditional retry
                        if response.status in {500, 502, 504, 524}:
                            await asyncio.sleep(1 + tries * 2)
                            continue

                        # the usual error cases
                        if response.status == 403:
                            raise Forbidden(response, data)
                        elif response.status == 404:
                            raise NotFound(response, data)
                        elif response.status >= 500:
                            raise DiscordServerError(response, data)
                        else:
                            raise HTTPException(response, data)

                # This is handling exceptions from the request
                except OSError as e:
                    # Connection reset by peer
                    if tries < 4 and e.errno in (54, 10054):
                        await asyncio.sleep(1 + tries * 2)
                        continue
                    raise

            if response is not None:
                # We've run out of retries, raise.
                if response.status >= 500:
                    raise DiscordServerError(response, data)

                raise HTTPException(response, data)

            raise RuntimeError('Unreachable code in HTTP handling')

class UserClient(discord.Client):
    """Just gotta remove the getapplication info part and not make it error"""

    async def login(self, token: str, bot = False) -> None:
        
        _log.info('logging in using static token')

        if self.loop is _loop:
            await self._async_setup_hook()

        if not isinstance(token, str):
            raise TypeError(f'expected token to be a str, received {token.__class__.__name__} instead')
        token = token.strip()

        data = await self.http.static_login(token)
        self._connection.user = ClientUser(state=self._connection, data=data)

        if bot: # here
            self._application = await self.application_info()
            if self._connection.application_id is None:
                self._connection.application_id = self._application.id

            if not self._connection.application_flags:
                self._connection.application_flags = self._application.flags

        await self.setup_hook()

def apply():
    discord.http.HTTPClient.request= HTTPClient.request # type: ignore
