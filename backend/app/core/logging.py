"""Central logging configuration.

Never log credentials or retrieved source content. Settings secrets are
``SecretStr`` values whose repr is masked, but avoid passing them to loggers.
"""

import logging

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"
_HANDLER_MARKER = "_aurora_handler"


def configure_logging(level: str = "INFO") -> None:
    """Configure the root logger once; repeated calls only update the level."""
    resolved = logging.getLevelNamesMapping().get(level.upper(), logging.INFO)
    root = logging.getLogger()
    root.setLevel(resolved)
    if any(getattr(h, _HANDLER_MARKER, False) for h in root.handlers):
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    setattr(handler, _HANDLER_MARKER, True)
    root.addHandler(handler)
