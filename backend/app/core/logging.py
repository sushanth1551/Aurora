"""Central logging configuration.

Never log credentials, authorization headers, or retrieved source content.
Settings secrets are ``SecretStr`` values whose repr is masked, but avoid
passing them to loggers at all.
"""

import logging
import re

from app.core.request_context import request_id_var

LOG_FORMAT = "%(asctime)s %(levelname)s [%(request_id)s] %(name)s %(message)s"
_HANDLER_MARKER = "_aurora_handler"

# These libraries log full request URLs (which can carry API keys in query
# strings) at INFO level, so they are capped at WARNING.
_QUIET_LOGGERS = ("httpx", "httpcore")


_SECRET_PARAM = re.compile(r"(?i)\b(api_key|apikey|access_token|token)=[^&\s\"']+")


class RedactSecretsFilter(logging.Filter):
    """Mask secret-looking query parameters (e.g. ``api_key=...``) in messages.

    ``httpx`` logs full request URLs, and SerpApi takes its key as a query
    parameter, so this guards against leaks regardless of logging setup.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        redacted = _SECRET_PARAM.sub(r"\1=***", message)
        if redacted != message:
            record.msg, record.args = redacted, None
        return True


def install_secret_redaction() -> None:
    """Attach the redaction filter to HTTP client loggers (idempotent)."""
    for name in _QUIET_LOGGERS:
        target = logging.getLogger(name)
        if not any(isinstance(f, RedactSecretsFilter) for f in target.filters):
            target.addFilter(RedactSecretsFilter())


class RequestIdFilter(logging.Filter):
    """Attach the current request id to every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


def configure_logging(level: str = "INFO") -> None:
    """Configure the root logger once; repeated calls only update the level."""
    resolved = logging.getLevelNamesMapping().get(level.upper(), logging.INFO)
    install_secret_redaction()
    root = logging.getLogger()
    root.setLevel(resolved)
    for name in _QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
    if any(getattr(h, _HANDLER_MARKER, False) for h in root.handlers):
        return
    handler = logging.StreamHandler()
    handler.addFilter(RequestIdFilter())
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    setattr(handler, _HANDLER_MARKER, True)
    root.addHandler(handler)


def log_event(
    logger: logging.Logger, event: str, *, level: int = logging.INFO, **fields: object
) -> None:
    """Emit ``event key=value ...``; callers must not pass secrets or content."""
    rendered = " ".join(f"{key}={value}" for key, value in fields.items())
    logger.log(level, "%s %s", event, rendered)
