"""Per-client rate limiting for the gateway.

A simple in-memory sliding-window limiter keyed by client IP. Good enough for
a single-process gateway; swap for Redis if the gateway is scaled
horizontally.
"""

import threading
import time
from collections import defaultdict, deque
from typing import Deque, Dict

from fastapi import Request

from .config import settings

_WINDOW_SECONDS = 60.0


def get_client_ip(request: Request) -> str:
    """Best-effort real client IP, checked in order of trust:

    1. ``X-Forwarded-For`` — the first hop is the original client when the
       gateway sits behind a reverse proxy/load balancer.
    2. ``X-Real-IP`` — set by some proxies (nginx) instead of the above.
    3. The direct socket peer — correct when there's no proxy in front.

    Note: forwarded headers are self-reported by whoever sent the request: a
    direct, unproxied caller can put anything in them. Only trust them when
    the gateway is deployed behind a proxy that overwrites these headers
    itself rather than passing a client-supplied value through.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip.strip()
    return request.client.host if request.client else "unknown"


class RateLimiter:
    def __init__(self, limit_per_minute: int):
        self.limit = limit_per_minute
        self._hits: Dict[str, Deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, identity: str) -> tuple[bool, int]:
        """Record a hit for ``identity``. Returns (allowed, retry_after_seconds).

        ``retry_after_seconds`` is 0 when allowed.
        """
        if self.limit <= 0:
            return True, 0
        now = time.monotonic()
        cutoff = now - _WINDOW_SECONDS
        with self._lock:
            q = self._hits[identity]
            while q and q[0] < cutoff:
                q.popleft()
            if len(q) >= self.limit:
                retry_after = max(1, int(q[0] + _WINDOW_SECONDS - now) + 1)
                return False, retry_after
            q.append(now)
            return True, 0


rate_limiter = RateLimiter(settings.RATE_LIMIT_PER_MINUTE)
