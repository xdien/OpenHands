"""
Usage:

Call setup_rate_limit_handler on your FastAPI app to add the exception handler

Create a rate limiter like:
    `rate_limiter = create_redis_rate_limiter("10/second; 100/minute")`

Call hit() with some key and allow the RateLimitException to propagate:
    `rate_limiter.hit('some action', user_id)`
"""

import time
from dataclasses import dataclass

import limits
from fastapi.responses import JSONResponse
from starlette.applications import Request, Response, Starlette
from starlette.exceptions import HTTPException
from storage.redis import get_redis_authed_url

from openhands.core.logger import openhands_logger as logger


def setup_rate_limit_handler(app: Starlette):
    """
    Add exception handler that
    """
    app.add_exception_handler(RateLimitException, _rate_limit_exceeded_handler)


@dataclass
class RateLimitResult:
    """Result of a rate limit check, times in seconds"""

    description: str
    remaining: int
    reset_time: int
    retry_after: int | None = None

    def add_headers(self, response: Response) -> None:
        """Add rate limit headers to a response"""
        response.headers['X-RateLimit-Limit'] = self.description
        response.headers['X-RateLimit-Remaining'] = str(self.remaining)
        response.headers['X-RateLimit-Reset'] = str(self.reset_time)
        if self.retry_after is not None:
            response.headers['Retry-After'] = str(self.retry_after)


class RateLimiter:
    strategy: limits.aio.strategies.RateLimiter
    limit_items: list[limits.RateLimitItem]

    def __init__(self, strategy: limits.aio.strategies.RateLimiter, windows: str):
        self.strategy = strategy
        self.limit_items = limits.parse_many(windows)

    async def hit(self, namespace: str, key: str):
        """
        Raises RateLimitException when limit is hit.
        Logs and swallows exceptions and logs if lookup fails.
        """
        logger.debug(f'Rate limit check: namespace={namespace}, key={key}')
        for lim in self.limit_items:
            allowed = True
            try:
                allowed = await self.strategy.hit(lim, namespace, key)
            except Exception as e:
                # Log detailed error information for debugging
                import socket

                try:
                    redis_host = socket.gethostbyname('127.0.0.1')
                    logger.error(
                        f'Rate limit check failed. DNS resolution test: 127.0.0.1 -> {redis_host}. '
                        f'Error type: {type(e).__name__}, Error: {e}'
                    )
                except socket.gaierror as dns_err:
                    logger.error(
                        f'Rate limit check failed AND DNS resolution failed for 127.0.0.1: {dns_err}. '
                        f'Original error type: {type(e).__name__}, Error: {e}'
                    )
                logger.exception('Rate limit check could not complete, redis issue?')
            if not allowed:
                logger.info(f'Rate limit hit for {namespace}:{key}')
                try:
                    result = await self._get_stats_as_result(lim, namespace, key)
                except Exception:
                    logger.exception(
                        'Rate limit exceeded but window lookup failed, swallowing'
                    )
                else:
                    raise RateLimitException(result)

    async def _get_stats_as_result(
        self, lim: limits.RateLimitItem, namespace: str, key: str
    ) -> RateLimitResult:
        """
        Lookup rate limit window stats and return a RateLimitResult with the data needed for response headers.
        """
        stats: limits.WindowStats = await self.strategy.get_window_stats(
            lim, namespace, key
        )
        return RateLimitResult(
            description=str(lim),
            remaining=stats.remaining,
            reset_time=int(stats.reset_time),
            retry_after=int(stats.reset_time - time.time())
            if stats.remaining == 0
            else None,
        )


def create_redis_rate_limiter(windows: str) -> RateLimiter:
    """
    Create a RateLimiter with the Redis backend and "Fixed Window" strategy.
    windows arg example: "10/second; 100/minute"
    """
    # Get Redis URL and log it (with password masked for security)
    redis_url = get_redis_authed_url()

    # Parse and log the URL components for debugging
    try:
        from urllib.parse import urlparse

        parsed = urlparse(redis_url)
        logger.info(
            f'Creating Redis rate limiter: host={parsed.hostname}, port={parsed.port}, '
            f'db={parsed.path}, password_set={bool(parsed.password)}, '
            f'scheme={parsed.scheme}'
        )
    except Exception as e:
        logger.warning(f'Could not parse Redis URL for logging: {e}')

    # URL-encode the password to handle special characters
    # The get_redis_authed_url() returns: redis://:password@host:port/db
    # We need to ensure special characters in password are properly encoded
    try:
        from urllib.parse import quote, urlparse, urlunparse

        parsed = urlparse(redis_url)
        if parsed.password:
            # Reconstruct URL with encoded password
            encoded_password = quote(parsed.password, safe='')
            # Build netloc with encoded password
            if parsed.port:
                netloc = f':{encoded_password}@{parsed.hostname}:{parsed.port}'
            else:
                netloc = f':{encoded_password}@{parsed.hostname}'
            redis_url = urlunparse(
                (
                    parsed.scheme,
                    netloc,
                    parsed.path,
                    parsed.params,
                    parsed.query,
                    parsed.fragment,
                )
            )
            logger.info('Redis password URL-encoded for special characters')
    except Exception as e:
        logger.warning(f'Could not URL-encode Redis password: {e}')

    full_url = f'async+{redis_url}'
    logger.info('Connecting to Redis with async+redis:// scheme')

    try:
        backend = limits.aio.storage.RedisStorage(full_url)
        strategy = limits.aio.strategies.FixedWindowRateLimiter(backend)
        logger.info('Redis rate limiter created successfully')
        return RateLimiter(strategy, windows)
    except Exception as e:
        logger.error(f'Failed to create Redis rate limiter: {type(e).__name__}: {e}')
        raise


class RateLimitException(HTTPException):
    """
    exception raised when a rate limit is hit.
    """

    result: RateLimitResult

    def __init__(self, result: RateLimitResult) -> None:
        self.result = result
        super(RateLimitException, self).__init__(
            status_code=429, detail=result.description
        )


def _rate_limit_exceeded_handler(request: Request, exc: Exception) -> Response:
    """
    Build a simple JSON response that includes the details of the rate limit that was hit.
    """
    logger.info(exc.__class__.__name__)
    if isinstance(exc, RateLimitException):
        response = JSONResponse(
            {'error': f'Rate limit exceeded: { exc.detail}'}, status_code=429
        )
        if exc.result:
            exc.result.add_headers(response)
    else:
        # Shouldn't happen, this handler is only bound to RateLimitException
        response = JSONResponse({'error': 'Rate limit exceeded'}, status_code=429)
    return response
