"""Shared base for external provider adapters.

Adapters receive ``Settings`` by injection, never hold hardcoded credentials,
and must not create network clients at import or construction time. Missing
credentials are reported by name only; values are never logged or exposed.
"""

from pydantic import SecretStr

from app.core.config import Settings, get_settings
from app.core.exceptions import ConfigurationException


class ExternalTool:
    """Base class providing credential checks for provider adapters."""

    #: Human-readable provider name used in error messages.
    provider: str = "external provider"
    #: Names of ``Settings`` attributes that must be set for this adapter.
    required_settings: tuple[str, ...] = ()

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    @staticmethod
    def _has_value(value: object) -> bool:
        if isinstance(value, SecretStr):
            value = value.get_secret_value()
        return bool(value)

    @property
    def missing_settings(self) -> list[str]:
        """Names (upper-case env vars) of required settings that are unset."""
        return [
            name.upper()
            for name in self.required_settings
            if not self._has_value(getattr(self._settings, name))
        ]

    @property
    def is_configured(self) -> bool:
        """Whether every required credential is present."""
        return not self.missing_settings

    def ensure_configured(self) -> None:
        """Raise ``ConfigurationException`` naming any missing credentials."""
        missing = self.missing_settings
        if missing:
            raise ConfigurationException(
                f"{self.provider} is not configured; set {', '.join(missing)}."
            )

    def _not_implemented(self, operation: str) -> NotImplementedError:
        return NotImplementedError(
            f"{self.provider}: {operation} is not implemented yet."
        )
