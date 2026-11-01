"""Errors of the Search Console integration (all safe to show to the user)."""

from app.core.exceptions import AuroraException


class GscNotConnected(AuroraException):
    """No refresh token is stored or configured: the integration is disconnected."""

    status_code = 409
    code = "gsc_not_connected"

    def __init__(self, message: str | None = None) -> None:
        super().__init__(
            message
            or "Google Search Console is not connected. Authorize it from the "
            "Search Console tab."
        )


class GscReauthorizationRequired(AuroraException):
    """Google rejected the stored refresh token (revoked, expired or changed)."""

    status_code = 409
    code = "gsc_reauthorization_required"

    def __init__(self, message: str | None = None) -> None:
        super().__init__(
            message
            or "Google no longer accepts the stored authorization. "
            "Connect Search Console again."
        )


class GscOAuthError(AuroraException):
    """The OAuth authorization could not be completed."""

    status_code = 400
    code = "gsc_oauth_error"


class GscStorageUnavailable(AuroraException):
    """No secure place exists to store a refresh token on this machine."""

    status_code = 503
    code = "gsc_storage_unavailable"
