"""Research request API placeholder."""

from fastapi import APIRouter

from app.schemas.research import ResearchRequest, ResearchResponse, ResearchStatus

router = APIRouter()

PLACEHOLDER_MESSAGE = (
    "Request validated but not processed. The research workflow will be "
    "implemented in a later module; no research was started."
)


@router.post(
    "",
    response_model=ResearchResponse,
    summary="Submit a research request (placeholder)",
)
async def submit_research(request: ResearchRequest) -> ResearchResponse:
    """Validate a request and state clearly that no research is performed."""
    return ResearchResponse(
        topic=request.topic,
        status=ResearchStatus.NOT_IMPLEMENTED,
        message=PLACEHOLDER_MESSAGE,
        is_placeholder=True,
    )
