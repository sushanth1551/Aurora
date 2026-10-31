"""In-process sliding-window rate limits for the local API.

AURORA runs as a single local process, so limits live in memory per application
instance (``app.state.rate_limits``) and reset on restart. Keys are client
addresses. Memory is bounded: the oldest keys are evicted beyond ``max_keys``.
"""

import time
from collections import OrderedDict, deque
from collections.abc import Callable
from dataclasses import dataclass

from app.core.config import Settings

Clock = Callable[[], float]


class SlidingWindowLimiter:
    """Allow at most ``limit`` events per key within ``window_seconds``."""

    def __init__(
        self,
        limit: int,
        window_seconds: float,
        *,
        clock: Clock = time.monotonic,
        max_keys: int = 10_000,
    ) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self._clock = clock
        self._max_keys = max_keys
        self._events: OrderedDict[str, deque[float]] = OrderedDict()

    def _window(self, key: str, now: float) -> deque[float]:
        events = self._events.get(key)
        if events is None:
            events = deque()
            self._events[key] = events
            while len(self._events) > self._max_keys:
                self._events.popitem(last=False)
        else:
            self._events.move_to_end(key)
        cutoff = now - self.window_seconds
        while events and events[0] <= cutoff:
            events.popleft()
        return events

    def retry_after(self, key: str) -> float | None:
        """Seconds until the next event is allowed, or ``None`` if allowed now."""
        now = self._clock()
        events = self._window(key, now)
        if len(events) >= self.limit:
            return events[0] + self.window_seconds - now
        return None

    def record(self, key: str) -> None:
        """Count one event for ``key``."""
        now = self._clock()
        self._window(key, now).append(now)

    def acquire(self, key: str) -> float | None:
        """Record an event if allowed; otherwise return the retry delay."""
        delay = self.retry_after(key)
        if delay is None:
            self.record(key)
        return delay


@dataclass
class RateLimits:
    """The limiters one application instance uses."""

    writes: SlidingWindowLimiter
    public: SlidingWindowLimiter
    auth_failures: SlidingWindowLimiter

    @classmethod
    def from_settings(
        cls, settings: Settings, clock: Clock = time.monotonic
    ) -> "RateLimits":
        return cls(
            writes=SlidingWindowLimiter(
                settings.api_write_rate_limit_per_minute, 60, clock=clock
            ),
            public=SlidingWindowLimiter(
                settings.api_public_rate_limit_per_minute, 60, clock=clock
            ),
            auth_failures=SlidingWindowLimiter(
                settings.api_auth_max_failures,
                settings.api_auth_lockout_seconds,
                clock=clock,
            ),
        )
