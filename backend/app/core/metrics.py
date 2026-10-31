"""Per-run performance counters carried in a ``ContextVar``.

A research run starts a ``RunMetrics`` and every task it spawns (``gather``,
semaphores) shares that object, so adapters can record timings and request
counts without being handed a collector. Only numbers and fixed labels are
recorded - never URLs, queries, headers or content.
"""

import time
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any


class RunMetrics:
    """Accumulated durations (ms) and counts for one run."""

    def __init__(self, request_budget: int = 0) -> None:
        self.durations_ms: Counter[str] = Counter()
        self.counts: Counter[str] = Counter()
        #: Max provider HTTP requests for the run (0 = unlimited). Retries count.
        self.request_budget = request_budget
        self.requests_sent = 0

    def snapshot(self) -> dict[str, Any]:
        """Plain-dict copy, split into durations, counts and provider requests."""
        counts = {k: v for k, v in self.counts.items() if not k.startswith("requests.")}
        requests = {
            k.removeprefix("requests."): v
            for k, v in self.counts.items()
            if k.startswith("requests.")
        }
        return {
            "durations_ms": dict(sorted(self.durations_ms.items())),
            "counts": dict(sorted(counts.items())),
            "provider_requests": dict(sorted(requests.items())),
        }


_current: ContextVar[RunMetrics | None] = ContextVar("aurora_run_metrics", default=None)


def start_run(request_budget: int = 0) -> RunMetrics:
    """Begin collecting metrics for the current task tree."""
    metrics = RunMetrics(request_budget)
    _current.set(metrics)
    return metrics


def current() -> RunMetrics | None:
    return _current.get()


def count(name: str, amount: int = 1) -> None:
    """Add to a counter; a no-op outside a run."""
    if (metrics := _current.get()) is not None:
        metrics.counts[name] += amount


def reserve_request(provider: str) -> bool:
    """Count one provider HTTP request; ``False`` if the run budget is spent."""
    metrics = _current.get()
    if metrics is None:
        return True
    if metrics.request_budget and metrics.requests_sent >= metrics.request_budget:
        metrics.counts["requests_blocked_by_budget"] += 1
        return False
    metrics.requests_sent += 1
    metrics.counts[f"requests.{provider.lower()}"] += 1
    return True


def add_ms(name: str, ms: float) -> None:
    if (metrics := _current.get()) is not None:
        metrics.durations_ms[name] += int(ms)


@contextmanager
def timed(name: str) -> Iterator[None]:
    """Add the elapsed wall time of the block to ``name``."""
    started = time.monotonic()
    try:
        yield
    finally:
        add_ms(name, (time.monotonic() - started) * 1000)
