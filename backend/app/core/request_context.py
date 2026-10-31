"""Per-request correlation identifier shared by middleware and logging."""

import re
from contextvars import ContextVar
from uuid import uuid4

REQUEST_ID_HEADER = "X-Request-ID"
_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")


def new_request_id(candidate: str | None = None) -> str:
    """Return ``candidate`` if it is a safe identifier, else a fresh UUID.

    Client-supplied ids are restricted so they cannot inject log lines.
    """
    if candidate and _SAFE_ID.fullmatch(candidate):
        return candidate
    return uuid4().hex
