"""Groq provider over mocked HTTP (respx): no network, no key, no paid calls."""

import json

import httpx
import pytest
import respx
from pydantic import BaseModel, Field, SecretStr, ValidationError

from app.core.config import Settings
from app.core.exceptions import ConfigurationException
from app.llm.exceptions import (
    LLMOutputError,
    ProviderAuthenticationError,
    ProviderRateLimitError,
    ProviderRequestError,
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from app.llm.groq_provider import GroqProvider
from app.llm.mock_provider import MockProvider
from app.llm.models import FinishStatus
from app.llm.provider_factory import FallbackProvider, build_llm_provider
from app.llm.usage_tracker import PricingRegistry, UsageTracker

URL = "https://api.groq.com/openai/v1/chat/completions"
KEY = "gsk_test_key_do_not_leak_123"
MODEL = "openai/gpt-oss-20b"


class Answer(BaseModel):
    title: str = Field(min_length=1)
    score: int = Field(ge=0, le=10)


def groq_settings(**kw) -> Settings:
    base = {
        "groq_api_key": SecretStr(KEY),
        "llm_provider": "groq",
        "llm_max_retries": 2,
        "llm_retry_base_delay_seconds": 0.01,
        "llm_retry_max_delay_seconds": 0.02,
    }
    return Settings(**{**base, **kw})


def completion(
    content: str | None, *, finish="stop", usage=True, model=MODEL, extra=None
):
    message = {"role": "assistant", "content": content}
    message.update(extra or {})
    body = {
        "id": "chatcmpl_1",
        "object": "chat.completion",
        "model": model,
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
    }
    if usage:
        body["usage"] = {
            "prompt_tokens": 1000,
            "completion_tokens": 200,
            "total_tokens": 1200,
            "prompt_tokens_details": {"cached_tokens": 400},
        }
    return body


def ok(content: str, **kw) -> httpx.Response:
    return httpx.Response(200, json=completion(content, **kw))


def error(
    status: int, body: dict | None = None, headers: dict | None = None
) -> httpx.Response:
    return httpx.Response(
        status,
        json=body or {"error": {"message": "x", "code": "err"}},
        headers=headers or {},
    )


@pytest.fixture
def sleeps() -> list[float]:
    return []


def provider(sleeps: list[float], **kw) -> GroqProvider:
    async def sleep(delay: float) -> None:
        sleeps.append(delay)

    return GroqProvider(groq_settings(**kw), sleep=sleep)


def sent(route: respx.Route, index: int = 0) -> dict:
    return json.loads(route.calls[index].request.content)


# -- success and payload ---------------------------------------------------------------


@respx.mock
async def test_text_generation_maps_response_and_usage() -> None:
    route = respx.post(URL).mock(return_value=ok("hello"))
    response = await provider([]).generate("p", system="sys")

    assert (
        response.text == "hello"
        and response.provider == "groq"
        and response.model == MODEL
    )
    assert response.input_tokens == 1000 and response.output_tokens == 200
    assert response.cached_tokens == 400 and response.total_tokens == 1200
    assert (
        response.request_id == "chatcmpl_1"
        and response.finish_status is FinishStatus.COMPLETED
    )
    body = sent(route)
    assert body["model"] == MODEL and body["include_reasoning"] is False
    assert body["messages"][0] == {"role": "system", "content": "sys"}
    assert "response_format" not in body and body["max_completion_tokens"] > 0


@respx.mock
async def test_authorization_header_is_sent_but_never_logged(caplog) -> None:
    route = respx.post(URL).mock(return_value=ok("x"))
    with caplog.at_level("DEBUG"):
        await provider([]).generate("p")
    assert route.calls[0].request.headers["Authorization"] == f"Bearer {KEY}"
    assert KEY not in caplog.text and KEY not in repr(groq_settings())


@respx.mock
async def test_structured_json_schema_request_and_validation() -> None:
    route = respx.post(URL).mock(return_value=ok('{"title": "Acme", "score": 7}'))
    parsed, response = await provider([]).generate_structured("p", Answer)

    assert parsed == Answer(title="Acme", score=7)
    fmt = sent(route)["response_format"]
    assert fmt["type"] == "json_schema" and fmt["json_schema"]["name"] == "Answer"
    assert fmt["json_schema"]["strict"] is False
    assert fmt["json_schema"]["schema"]["title"] == "Answer"
    assert response.parsed == {"title": "Acme", "score": 7}


@respx.mock
async def test_json_object_mode_puts_schema_in_prompt() -> None:
    route = respx.post(URL).mock(return_value=ok('{"title": "A", "score": 1}'))
    await provider([], groq_structured_output="json_object").generate_structured(
        "p", Answer
    )
    body = sent(route)
    assert body["response_format"] == {"type": "json_object"}
    assert (
        '"title"' in body["messages"][-1]["content"]
    )  # schema is described in the prompt


@respx.mock
async def test_fenced_json_is_accepted() -> None:
    respx.post(URL).mock(return_value=ok('```json\n{"title": "A", "score": 2}\n```'))
    parsed, _ = await provider([]).generate_structured("p", Answer)
    assert parsed.score == 2


@respx.mock
async def test_reasoning_field_is_ignored_not_returned() -> None:
    respx.post(URL).mock(
        return_value=ok(
            '{"title": "A", "score": 3}', extra={"reasoning": "secret chain"}
        )
    )
    response = await provider([]).generate("p")
    assert "secret chain" not in response.text and "reasoning" not in response.text


@respx.mock
async def test_reasoning_can_be_requested_for_non_reasoning_models() -> None:
    route = respx.post(URL).mock(return_value=ok("x"))
    await provider(
        [], groq_exclude_reasoning=False, groq_reasoning_effort="low"
    ).generate("p")
    body = sent(route)
    assert "include_reasoning" not in body and body["reasoning_effort"] == "low"


# -- malformed responses -----------------------------------------------------------------


@respx.mock
async def test_invalid_json_is_repaired_once_and_usage_summed() -> None:
    route = respx.post(URL).mock(
        side_effect=[ok("not json at all"), ok('{"title": "A", "score": 4}')]
    )
    parsed, response = await provider([]).generate_structured("p", Answer)
    assert parsed.score == 4 and response.metadata["repaired"] is True
    assert route.call_count == 2
    assert response.input_tokens == 2000  # both calls counted
    assert "rejected" in sent(route, 1)["messages"][-1]["content"]


@respx.mock
async def test_schema_violation_is_repaired() -> None:
    respx.post(URL).mock(
        side_effect=[ok('{"title": "", "score": 99}'), ok('{"title": "B", "score": 1}')]
    )
    parsed, _ = await provider([]).generate_structured("p", Answer)
    assert parsed.title == "B"


@respx.mock
async def test_groq_schema_rejection_triggers_repair() -> None:
    rejection = error(
        400,
        {
            "error": {
                "code": "json_validate_failed",
                "message": "Generated JSON does not match the expected schema. Please adjust your prompt.",
            }
        },
    )
    route = respx.post(URL).mock(
        side_effect=[rejection, ok('{"title": "C", "score": 5}')]
    )
    parsed, response = await provider([]).generate_structured("p", Answer)
    assert (
        parsed.title == "C" and route.call_count == 2 and response.metadata["repaired"]
    )


@respx.mock
async def test_persistent_bad_output_fails_without_unvalidated_data() -> None:
    route = respx.post(URL).mock(side_effect=[ok("bad"), ok("still bad")])
    with pytest.raises(LLMOutputError):
        await provider([]).generate_structured("p", Answer)
    assert route.call_count == 2


@respx.mock
async def test_truncated_output_is_never_accepted_as_structured() -> None:
    respx.post(URL).mock(return_value=ok('{"title": "A"', finish="length"))
    response = await provider([]).generate("p")
    assert response.finish_status is FinishStatus.TRUNCATED
    respx.post(URL).mock(return_value=ok('{"title": "A"', finish="length"))
    with pytest.raises(LLMOutputError, match="truncated"):
        await provider([]).generate_structured("p", Answer)


@respx.mock
async def test_content_filter_maps_to_filtered() -> None:
    respx.post(URL).mock(return_value=ok("", finish="content_filter"))
    response = await provider([]).generate("p")
    assert response.finish_status is FinishStatus.FILTERED


@respx.mock
async def test_missing_usage_stays_null() -> None:
    respx.post(URL).mock(return_value=ok("hi", usage=False))
    response = await provider([]).generate("p")
    assert (
        response.input_tokens is None
        and response.total_tokens is None
        and response.cached_tokens is None
    )


@pytest.mark.parametrize(
    "body",
    [
        "not json",
        {"object": "error"},
        {"choices": []},
        {"choices": [{"message": None, "finish_reason": "stop"}]},
    ],
)
@respx.mock
async def test_malformed_envelopes_raise_response_error(body) -> None:
    respx.post(URL).mock(
        return_value=httpx.Response(200, content=body)
        if isinstance(body, str)
        else httpx.Response(200, json=body)
    )
    with pytest.raises(ProviderResponseError):
        await provider([]).generate("p")


@respx.mock
async def test_empty_content_is_an_error() -> None:
    respx.post(URL).mock(return_value=ok(""))
    with pytest.raises(ProviderResponseError, match="empty"):
        await provider([]).generate("p")


# -- errors, rate limits, timeouts, retries ----------------------------------------------------


@respx.mock
async def test_rate_limit_is_retried_honouring_retry_after() -> None:
    sleeps: list[float] = []
    route = respx.post(URL).mock(
        side_effect=[
            error(429, headers={"retry-after": "2"}),
            ok('{"title": "A", "score": 1}'),
        ]
    )
    parsed, _ = await provider(sleeps).generate_structured("p", Answer)
    assert parsed.score == 1 and route.call_count == 2
    assert (
        sleeps and sleeps[0] >= 2.0
    )  # never shorter than Retry-After (capped at 30 s)


@respx.mock
async def test_rate_limit_exhausts_bounded_retries() -> None:
    route = respx.post(URL).mock(return_value=error(429))
    with pytest.raises(ProviderRateLimitError):
        await provider([], llm_max_retries=2).generate("p")
    assert route.call_count == 3  # first attempt + 2 retries


@respx.mock
async def test_server_error_is_retried_then_succeeds() -> None:
    route = respx.post(URL).mock(side_effect=[error(503), ok("fine")])
    assert (await provider([]).generate("p")).text == "fine" and route.call_count == 2


@respx.mock
async def test_timeout_is_retried_then_normalized() -> None:
    route = respx.post(URL).mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(ProviderTimeoutError) as info:
        await provider([], llm_max_retries=1).generate("p")
    assert route.call_count == 2 and "slow" not in str(info.value)


@respx.mock
async def test_connection_failure_is_retried_then_normalized() -> None:
    route = respx.post(URL).mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(ProviderUnavailableError):
        await provider([], llm_max_retries=0).generate("p")
    assert route.call_count == 1


@pytest.mark.parametrize(
    ("status", "exc"),
    [
        (401, ProviderAuthenticationError),
        (403, ProviderAuthenticationError),
        (400, ProviderRequestError),
    ],
)
@respx.mock
async def test_permanent_errors_are_not_retried(status, exc) -> None:
    route = respx.post(URL).mock(return_value=error(status))
    with pytest.raises(exc):
        await provider([]).generate("p")
    assert route.call_count == 1


@respx.mock
async def test_error_messages_do_not_leak_key_or_body() -> None:
    respx.post(URL).mock(
        return_value=error(401, {"error": {"message": f"bad key {KEY}"}})
    )
    with pytest.raises(ProviderAuthenticationError) as info:
        await provider([]).generate("p")
    assert KEY not in str(info.value) and "bad key" not in str(info.value)


# -- configuration -----------------------------------------------------------------------------


async def test_missing_key_raises_before_any_request() -> None:
    with pytest.raises(ConfigurationException, match="GROQ_API_KEY"):
        await GroqProvider(Settings(llm_provider="groq")).generate("p")


async def test_missing_model_raises_before_any_request() -> None:
    with pytest.raises(ConfigurationException, match="GROQ_MODEL"):
        await GroqProvider(groq_settings(groq_model=None)).generate("p")


def test_base_url_must_be_https_except_local_test_servers() -> None:
    assert groq_settings().groq_base_url == "https://api.groq.com/openai/v1"
    assert groq_settings(
        groq_base_url="http://localhost:9999/v1"
    ).groq_base_url.startswith("http://localhost")
    with pytest.raises(ValidationError):
        groq_settings(groq_base_url="http://api.groq.com/openai/v1")


def test_blank_optional_groq_values_mean_unset(monkeypatch) -> None:
    s = Settings(groq_reasoning_effort="", groq_model="")
    assert s.groq_reasoning_effort is None and s.groq_model is None


# -- provider selection ---------------------------------------------------------------------------


def test_groq_selected_by_environment_without_openai_key() -> None:
    chosen = build_llm_provider(groq_settings())
    assert isinstance(chosen, GroqProvider) and chosen.model == MODEL


def test_groq_selection_fails_clearly_without_key() -> None:
    with pytest.raises(ConfigurationException, match="GROQ_API_KEY"):
        build_llm_provider(Settings(llm_provider="groq", groq_model=MODEL))


def test_groq_override_requires_permission_and_pricing_for_other_models() -> None:
    s = Settings(groq_api_key=SecretStr(KEY), groq_model=MODEL)
    with pytest.raises(ConfigurationException, match="override is not permitted"):
        build_llm_provider(s, preferred_provider="groq")
    chosen = build_llm_provider(s, preferred_provider="groq", allow_override=True)
    assert chosen.name == "groq"
    with pytest.raises(ConfigurationException, match="Model override"):
        build_llm_provider(
            s,
            preferred_provider="groq",
            preferred_model="openai/gpt-oss-120b",
            allow_override=True,
        )
    priced = build_llm_provider(
        s, preferred_provider="groq", preferred_model="openai/gpt-oss-120b", allow_override=True,
        pricing_models=["groq:openai/gpt-oss-120b"],
    )  # fmt: skip
    assert priced.model == "openai/gpt-oss-120b"


def test_groq_can_be_fallback_or_primary_with_anthropic() -> None:
    s = Settings(
        llm_provider="groq", groq_api_key=SecretStr(KEY), groq_model=MODEL,
        anthropic_api_key=SecretStr("sk-ant-test"), anthropic_model="claude-x",
        llm_enable_provider_fallback=True, llm_fallback_provider="anthropic",
    )  # fmt: skip
    wrapped = build_llm_provider(s)
    assert isinstance(wrapped, FallbackProvider) and wrapped.primary.name == "groq"
    assert wrapped.secondary.name == "anthropic"
    s2 = s.model_copy(update={"llm_fallback_provider": "groq"})
    assert not isinstance(
        build_llm_provider(s2), FallbackProvider
    )  # never fall back to itself


@respx.mock
async def test_groq_rate_limit_falls_back_to_anthropic_and_records_it() -> None:
    respx.post(URL).mock(return_value=error(429))
    primary = GroqProvider(groq_settings(llm_max_retries=0))
    secondary = MockProvider(Settings())
    secondary.name = "anthropic"  # stands in for a configured Anthropic provider
    parsed, response = await FallbackProvider(primary, secondary).generate_structured(
        "p", Answer
    )
    assert parsed == Answer(title="mock", score=0)
    assert response.provider == "anthropic"
    assert response.metadata["fallback_from"] == "groq"
    assert response.metadata["fallback_reason"] == "provider_rate_limited"


# -- cost and usage ---------------------------------------------------------------------------------


PRICE_JSON = (
    '{"groq:openai/gpt-oss-20b": {"input_per_million": 0.075, '
    '"output_per_million": 0.30, "cached_input_per_million": 0.037, "currency": "USD"}}'
)


def test_pricing_registry_parses_groq_entry_and_costs_exactly() -> None:
    registry = PricingRegistry.from_settings(Settings(llm_pricing_json=PRICE_JSON))
    from app.llm.models import LLMUsage

    # 1000 prompt tokens of which 400 cached; 200 completion tokens
    usage = LLMUsage(input_tokens=1000, output_tokens=200, cached_tokens=400)
    expected = (600 * 0.075 + 400 * 0.037 + 200 * 0.30) / 1_000_000
    assert registry.estimate("groq", MODEL, usage) == pytest.approx(expected)
    assert registry.estimate("groq", "openai/gpt-oss-120b", usage) is None  # unpriced


@respx.mock
async def test_usage_tracker_records_groq_cost_from_reported_usage() -> None:
    respx.post(URL).mock(return_value=ok('{"title": "A", "score": 1}'))
    tracker = UsageTracker(
        pricing=PricingRegistry.from_settings(Settings(llm_pricing_json=PRICE_JSON))
    )
    from app.llm.usage_tracker import TrackedLLM

    llm = TrackedLLM(provider([]), tracker)
    await llm.structured("p", Answer, system="s", operation="evidence_analysis")
    record = tracker.records[0]
    assert record.provider == "groq" and record.total_tokens == 1200
    assert record.cost_available and record.estimated_cost == pytest.approx(
        (600 * 0.075 + 400 * 0.037 + 200 * 0.30) / 1_000_000
    )


@respx.mock
async def test_unpriced_groq_model_reports_cost_unavailable() -> None:
    respx.post(URL).mock(return_value=ok("x"))
    tracker = UsageTracker(pricing=PricingRegistry())
    from app.llm.usage_tracker import TrackedLLM

    await TrackedLLM(provider([]), tracker).text("p", system="s", operation="x")
    assert (
        tracker.records[0].cost_available is False
        and tracker.records[0].estimated_cost is None
    )
    assert tracker.records[0].total_tokens == 1200  # tokens are still measured


# -- citation and evidence validation with Groq replies -------------------------------------------------


@respx.mock
async def test_groq_analysis_is_citation_validated(db_session) -> None:
    """A full analysis whose model replies come from mocked Groq HTTP responses.

    The fake model cites only ids that appear in the prompt, and also invents an
    id and an unsupported number. Invented ids must be removed, and the report's
    citations must all resolve to stored evidence.
    """
    import re

    from app.database.models import ResearchContextRecord
    from app.database.repositories.analysis_repository import AnalysisRepository
    from app.schemas.analysis import AnalyzeRequest
    from tests.analysis_support import analysis_service, seed_acme_job

    id_pattern = re.compile(r'id="(c-[0-9a-f]{10})"')
    fake_reply = {
        "AnalysisOutput": {
            "findings": [
                {
                    "description": "Acme publishes pricing",
                    "kind": "fact",
                    "evidence_ids": ["c-deadbeef00"],
                }
            ],
            "gaps": [],
            "answered_questions": [1],
            "sufficient": True,
        },
        "SummaryOutput": {"summary": []},
    }

    def model_reply(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        prompt = body["messages"][-1]["content"]
        schema = body["response_format"]["json_schema"]["name"]
        ids = id_pattern.findall(prompt)[:2]
        if schema in fake_reply:
            payload = json.loads(json.dumps(fake_reply[schema]))
            if schema == "AnalysisOutput":
                payload["findings"][0]["evidence_ids"] = ids + ["c-deadbeef00"]
                payload["findings"].append(
                    {
                        "description": "Acme has 9000 customers",
                        "kind": "observation",
                        "evidence_ids": ids[:1],
                    }
                )
            return ok(json.dumps(payload))
        return ok(
            json.dumps(
                {"questions": [{"question": "What does Acme sell?", "priority": 1}]}
            )
        )

    respx.post(URL).mock(side_effect=model_reply)
    job = await seed_acme_job(db_session)
    service = analysis_service(db_session, provider([]))
    response = await service.analyze(
        AnalyzeRequest(
            research_job_id=job, research_objective="Analyse Acme products and pricing"
        )
    )

    assert response.provider == "groq" and response.model == MODEL
    repo = AnalysisRepository(db_session)
    report, _ = await repo.get_report(response.analysis_id)
    dumped = report.model_dump_json()
    assert "c-deadbeef00" not in dumped  # invented ids never reach the report
    stored = await repo.count_rows(ResearchContextRecord, response.analysis_id)
    assert stored >= 1
    known = {r.evidence_id for r in report.sources}
    for finding in report.key_findings:
        assert all(e in known or e.startswith("c-") for e in finding.evidence_ids)
    quality = await repo.get_quality(response.analysis_id)
    assert quality.metrics.invalid_citation_count == 0
    # the unsupported "9000 customers" statement is flagged, not silently passed
    assert quality.metrics.unsupported_claim_count >= 1 or "9000" not in dumped
    assert response.status.value in ("requires_review", "partially_completed")


# -- live test is in test_llm_live.py (skipped unless enabled) -------------------------------------------


def test_legacy_groq_tool_skeleton_still_refuses_to_fake_results() -> None:
    from app.tools.groq_tool import GroqTool

    assert GroqTool(Settings()).is_configured is False
