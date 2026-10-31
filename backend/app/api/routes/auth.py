"""Token check for the dashboard (mounted behind ``require_api_token``)."""

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class SessionResponse(BaseModel):
    """Returned only when the presented API token is valid."""

    authenticated: bool


@router.get(
    "/session",
    response_model=SessionResponse,
    summary="Verify the API token",
    responses={401: {"description": "Missing or invalid token"}},
)
async def session() -> SessionResponse:
    """Lets the dashboard confirm a token before storing it; contacts nothing."""
    return SessionResponse(authenticated=True)
