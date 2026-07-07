import time
import asyncio
from fastapi import Request, HTTPException, status
from app.config import settings

class InMemoryRateLimiter:
    def __init__(self, max_requests: int, window_seconds: int):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.records: dict[str, list[float]] = {}
        self.lock = asyncio.Lock()

    async def check_rate_limit(self, identifier: str) -> None:
        async with self.lock:
            now = time.time()
            cutoff = now - self.window_seconds

            # Clean up old requests
            if identifier in self.records:
                self.records[identifier] = [
                    t for t in self.records[identifier] if t > cutoff
                ]
            else:
                self.records[identifier] = []

            # Check if limit is exceeded
            if len(self.records[identifier]) >= self.max_requests:
                oldest = self.records[identifier][0]
                retry_after = int(self.window_seconds - (now - oldest))
                if retry_after <= 0:
                    retry_after = 1
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail={
                        "error": "Rate limit exceeded",
                        "retry_after_seconds": retry_after,
                        "limit": self.max_requests,
                        "window_seconds": self.window_seconds,
                    },
                    headers={"Retry-After": str(retry_after)},
                )

            self.records[identifier].append(now)

rate_limiter = InMemoryRateLimiter(
    max_requests=settings.API_RATE_LIMIT_MAX_REQUESTS,
    window_seconds=settings.API_RATE_LIMIT_WINDOW_SECONDS,
)

async def rate_limit_dependency(request: Request):
    client_ip = request.client.host if request.client else "unknown"
    await rate_limiter.check_rate_limit(client_ip)
