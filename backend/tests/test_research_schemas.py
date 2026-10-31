"""Tests for research schemas and workflow state."""

import pytest
from pydantic import ValidationError

from app.research.state import ResearchState
from app.schemas.research import (
    ExtractedPage,
    ExtractionStatus,
    JobStatus,
    ResearchRequest,
    ResearchSource,
)


def test_request_defaults() -> None:
    request = ResearchRequest(query="  ai tools  ")

    assert request.query == "ai tools"
    assert request.max_results == 10
    assert request.scrape_pages is True
    assert request.country is None and request.allowed_domains is None


def test_request_normalizes_optional_fields() -> None:
    request = ResearchRequest(
        query="x",
        country="IN",
        language="EN",
        allowed_domains=["Example.com", "example.com.", "docs.python.org"],
        request_id="job-key-0001",
    )

    assert request.country == "in" and request.language == "en"
    assert request.allowed_domains == ["docs.python.org", "example.com"]


@pytest.mark.parametrize(
    "payload",
    [
        {"query": ""},
        {"query": "   "},
        {"query": "x" * 501},
        {"query": "x", "max_results": 0},
        {"query": "x", "max_results": 101},
        {"query": "x", "country": "india"},
        {"query": "x", "language": "e"},
        {"query": "x", "allowed_domains": ["not a domain"]},
        {"query": "x", "allowed_domains": ["localhost"]},
        {"query": "x", "request_id": "short"},
        {"query": "x", "request_id": "bad id with spaces!"},
        {"query": "x", "unknown_field": 1},
    ],
)
def test_request_rejects_invalid_input(payload: dict) -> None:
    with pytest.raises(ValidationError):
        ResearchRequest(**payload)


def test_enums_cover_required_statuses() -> None:
    assert {s.value for s in JobStatus} == {
        "pending",
        "running",
        "completed",
        "partially_completed",
        "failed",
    }
    assert {s.value for s in ExtractionStatus} >= {"succeeded", "failed", "rejected"}


def test_models_default_to_safe_values() -> None:
    page = ExtractedPage(original_url="https://example.com/")
    source = ResearchSource(original_url="https://example.com/")

    assert page.status == ExtractionStatus.PENDING and page.final_url is None
    assert source.extraction_status == ExtractionStatus.PENDING
    assert source.id is not None and source.created_at.tzinfo is not None


def test_state_is_a_typed_dict_with_reducers() -> None:
    state: ResearchState = {"query": "x", "errors": [], "stages": []}

    assert state["query"] == "x"
    hints = ResearchState.__annotations__
    assert "search_results" in hints and "extracted_pages" in hints
    assert not any("key" in name for name in hints)  # no credentials in state
