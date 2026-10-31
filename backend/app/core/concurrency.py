"""Process-wide concurrency gates shared by every request on one event loop.

Adapters are built per API request, so a semaphore owned by an adapter instance
only limits one job. ``shared_gate`` returns the same semaphore for every caller
on the same loop and name, giving a true global cap on simultaneous requests to
a provider. The cap is a local application limit, not a provider plan limit.
"""

import asyncio
import weakref

_gates: weakref.WeakKeyDictionary[
    asyncio.AbstractEventLoop, dict[tuple[str, int], asyncio.Semaphore]
] = weakref.WeakKeyDictionary()


def shared_gate(name: str, limit: int) -> asyncio.Semaphore:
    """The shared semaphore for ``name`` (limit changes create a fresh gate)."""
    loop = asyncio.get_running_loop()
    gates = _gates.setdefault(loop, {})
    key = (name, max(1, limit))
    if key not in gates:
        gates[key] = asyncio.Semaphore(key[1])
    return gates[key]
