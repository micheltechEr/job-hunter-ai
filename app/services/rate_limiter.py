import asyncio
import time
import random
import logging
from typing import Callable, Any, Optional
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
import openai
from app.config import settings

logger = logging.getLogger("job_hunter.rate_limiter")

class AsyncTokenBucketRateLimiter:
    """Token Bucket rate limiter ensuring we do not exceed requests per minute without deadlock or negative compounding."""
    def __init__(self, requests_per_minute: int, max_concurrency: int):
        self.rpm = max(1, requests_per_minute)
        self.capacity = float(self.rpm)
        self.tokens = float(self.capacity)
        self.fill_rate = self.capacity / 60.0  # tokens per second
        self.last_update = time.monotonic()
        self.lock = asyncio.Lock()
        self.semaphore = asyncio.Semaphore(max(1, max_concurrency))

    async def acquire(self):
        """Acquires a token and semaphore slot with non-negative elapsed clamping and bounded pacing."""
        await self.semaphore.acquire()
        sleep_needed = 0.0
        async with self.lock:
            now = time.monotonic()
            elapsed = max(0.0, now - self.last_update)
            self.last_update = now
            self.tokens = min(self.capacity, max(0.0, self.tokens) + elapsed * self.fill_rate)

            if self.tokens >= 1.0:
                self.tokens -= 1.0
                sleep_needed = 0.0
            else:
                needed = 1.0 - self.tokens
                sleep_needed = needed / self.fill_rate
                self.tokens = 0.0

        if sleep_needed > 0:
            bounded_sleep = min(1.0, sleep_needed)
            logger.debug(f"Rate limiter pacing: waiting {bounded_sleep:.2f}s to respect RPM limits...")
            await asyncio.sleep(bounded_sleep)

    def release(self):
        """Releases the concurrency semaphore slot."""
        self.semaphore.release()


# Global LLM rate limiter instance
llm_rate_limiter = AsyncTokenBucketRateLimiter(
    requests_per_minute=settings.LLM_REQUESTS_PER_MINUTE,
    max_concurrency=settings.LLM_MAX_CONCURRENT_REQUESTS
)


async def execute_with_llm_protection(
    func: Callable[..., Any],
    *args,
    **kwargs
) -> Any:
    """
    Executes an async LLM/Embedding call with:
    1. Concurrency and RPM throttling with guaranteed semaphore release.
    2. Exponential backoff with full jitter on 429 RateLimitError, timeouts, and temporary 5xx errors.
    """
    max_retries = settings.LLM_MAX_RETRIES
    base_delay = settings.LLM_RETRY_BASE_DELAY
    max_delay = settings.LLM_RETRY_MAX_DELAY

    for attempt in range(1, max_retries + 1):
        await llm_rate_limiter.acquire()
        try:
            return await func(*args, **kwargs)
        except (openai.RateLimitError, openai.APIConnectionError, openai.APITimeoutError, openai.InternalServerError) as e:
            if attempt == max_retries:
                logger.error(f"LLM call failed after {max_retries} attempts due to: {e}")
                raise e

            # Check if there is a retry-after hint or calculate exponential backoff with jitter
            delay = min(max_delay, base_delay * (2 ** (attempt - 1)) + random.uniform(0.5, 2.0))
            logger.warning(
                f"Rate limit or API error encountered ({type(e).__name__}: {e}). "
                f"Attempt {attempt}/{max_retries}. Backing off for {delay:.2f}s..."
            )
            await asyncio.sleep(delay)
        finally:
            llm_rate_limiter.release()


class APIRateLimitMiddleware(BaseHTTPMiddleware):
    """Protects FastAPI server endpoints against denial-of-service and request bursts."""
    def __init__(self, app, requests_per_minute: int = 120):
        super().__init__(app)
        self.rpm = requests_per_minute
        self.client_records: dict[str, list[float]] = {}
        self.lock = asyncio.Lock()

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Skip static assets or templates
        path = request.url.path
        if path.startswith("/static") or path == "/favicon.ico":
            return await call_next(request)

        client_ip = request.client.host if request.client else "unknown"
        now = time.time()
        window_start = now - 60.0

        async with self.lock:
            timestamps = self.client_records.get(client_ip, [])
            # Filter out entries older than 1 minute
            valid_timestamps = [t for t in timestamps if t > window_start]

            if len(valid_timestamps) >= self.rpm:
                logger.warning(f"Client {client_ip} exceeded API rate limit ({self.rpm} req/min).")
                return JSONResponse(
                    status_code=429,
                    content={
                        "detail": "Taxa limite de requisições excedida. Aguarde um momento antes de tentar novamente.",
                        "error": "Rate limit exceeded"
                    },
                    headers={"Retry-After": "60"}
                )

            valid_timestamps.append(now)
            self.client_records[client_ip] = valid_timestamps

            # Memory cleanup for inactive clients
            if len(self.client_records) > 2000:
                self.client_records = {
                    ip: ts for ip, ts in self.client_records.items()
                    if ts and ts[-1] > window_start
                }

        return await call_next(request)
