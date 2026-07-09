import time
import asyncio
from fastapi import Request, HTTPException, status
from app.config import settings

# Global in-memory storage and lock
RECORDS: dict[str, list[float]] = {}
LOCK = asyncio.Lock()

async def rate_limit_dependency(request: Request):
    client_ip = request.headers.get("x-forwarded-for")
    if client_ip:
        client_ip = client_ip.split(",")[0].strip()
    else:
        client_ip = request.headers.get("x-real-ip")
        
    if not client_ip:
        client_ip = request.client.host if request.client else "unknown"

    
    async with LOCK:
        now = time.time()
        cutoff = now - settings.API_RATE_LIMIT_WINDOW_SECONDS
        
        # Clean up old requests
        RECORDS[client_ip] = [t for t in RECORDS.get(client_ip, []) if t > cutoff]

        # Check if limit is exceeded
        if len(RECORDS[client_ip]) >= settings.API_RATE_LIMIT_MAX_REQUESTS:
            oldest = RECORDS[client_ip][0]
            retry_after = int(settings.API_RATE_LIMIT_WINDOW_SECONDS - (now - oldest))
            if retry_after <= 0:
                retry_after = 1
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={
                    "error": "Rate limit exceeded",
                    "retry_after_seconds": retry_after,
                    "limit": settings.API_RATE_LIMIT_MAX_REQUESTS,
                    "window_seconds": settings.API_RATE_LIMIT_WINDOW_SECONDS,
                },
                headers={"Retry-After": str(retry_after)},
            )

        RECORDS[client_ip].append(now)
