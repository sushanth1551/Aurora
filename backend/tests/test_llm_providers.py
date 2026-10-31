"""LLM providers over fake SDK clients: no network, no keys, no paid calls.

The OpenAI tests use a fake client because the ``openai`` package is not
installed in the development environment; they verify AURORA's request and
response mapping, not the live SDK.
"""

import json
from types import SimpleNamespace

import pytest
from pydantic import BaseModel, Field, SecretStr

from app.core.config import Settings
from app.core.exceptions import ConfigurationException
from app.llm.anthropic_provider import AnthropicProvider
from app.llm.base import classify_sdk_error, extract_json
from app.llm.exceptions import (
    LLMOutputError,
    ProviderAuthenticationError,
    ProviderRateLimitError,
    ProviderRequestError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from app.llm.mock_provider import MockProvider, minimal_instance
from app.llm.models import FinishStatus
from app.llm.openai_provider import OpenAIProvider
from app.llm.provider_factory import FallbackProvider, build_llm_provider


class Answer(BaseModel):
    title: str = Field(min_length=1)
    score: int = Field(ge=0, le=10)
    tags: list[str] = Field(default_factory=list)


def settings(**kw) -> Settings:
    return Settings(
        **{"llm_max_retries": 2, "llm_retry_base_delay_seconds": 0.01, **kw}
    )


async def no_sleep(_: float) -> None:
    return None


# -- fake SDK errors (classified by class name + status code, like the real SDKs) --


class RateLimitError(Exception):
    status_code = 429

    def __init__(self, retry_after: str | None = None):
        super().__init__("secret-key-in-message sk-123")
        self.response = SimpleNamespace(
            headers={"retry-after": retry_after} if retry_after else {}
        )


class AuthenticationError(Exception):
    status_code = 401


class BadRequestError(Exception):
    status_code = 400


class InternalServerError(Exception):
    status_code = 503


class APITimeoutError(Exception):
    pass


# -- fake clients -------------------------------------------------------------------


class FakeOpenAI:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls: list[dict] = []
        self.responses = SimpleNamespace(create=self.create)

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def openai_reply(text: str, *, status="completed", reason=None, usage=True):
    return SimpleNamespace(
        id="resp_1",
        model="gpt-test",
        status=status,
        output_text=text,
        incomplete_details=SimpleNamespace(reason=reason) if reason else None,
        usage=SimpleNamespace(
            input_tokens=120,
            output_tokens=30,
            input_tokens_details=SimpleNamespace(cached_tokens=20),
        )
        if usage
        else None,
    )


class FakeAnthropic:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls: list[dict] = []
        self.messages = SimpleNamespace(create=self.create)

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def anthropic_reply(*, tool_input=None, text=None, stop="end_turn", usage=True):
    content = []
    if text is not None:
        content.append(SimpleNamespace(type="text", text=text))
    if tool_input is not None:
        content.append(
            SimpleNamespace(type="tool_use", name="record_result", input=tool_input)
        )
    return SimpleNamespace(
        id="msg_1",
        model="claude-test",
        stop_reason=stop,
        content=content,
        usage=SimpleNamespace(
            input_tokens=200, output_tokens=40, cache_read_input_tokens=0
        )
        if usage
        else None,
    )


def openai(replies, **kw):
    client = FakeOpenAI(replies)
    return OpenAIProvider(
        settings(**kw), client=client, model="gpt-test", sleep=no_sleep
    ), client


def anthropic(replies, **kw):
    client = FakeAnthropic(replies)
    return AnthropicProvider(
        settings(**kw), client=client, model="claude-test", sleep=no_sleep
    ), client


# -- OpenAI ---------------------------------------------------------------------------


async def test_openai_structured_output_and_usage() -> None:
    provider, client = openai([openai_reply('{"title": "Acme", "score": 7}')])
    parsed, response = await provider.generate_structured("p", Answer, system="sys")
    assert parsed == Answer(title="Acme", score=7)
    call = client.calls[0]
    assert call["text"]["format"]["type"] == "json_schema"
    assert call["text"]["format"]["name"] == "Answer" and call["instructions"] == "sys"
    assert call["store"] is False and call["timeout"] == 60.0
    assert response.provider == "openai" and response.model == "gpt-test"
    assert (response.input_tokens, response.output_tokens, response.cached_tokens) == (
        120,
        30,
        20,
    )
    assert response.total_tokens == 150 and response.request_id == "resp_1"
    assert response.parsed == {"title": "Acme", "score": 7, "tags": []}


async def test_openai_text_generation() -> None:
    provider, client = openai([openai_reply("hello")])
    response = await provider.generate("p")
    assert response.text == "hello" and "text" not in client.calls[0]


async def test_openai_retries_rate_limit_then_succeeds_honouring_retry_after() -> None:
    delays: list[float] = []

    async def record(d: float) -> None:
        delays.append(d)

    client = FakeOpenAI(
        [RateLimitError("2"), openai_reply('{"title": "x", "score": 1}')]
    )
    provider = OpenAIProvider(settings(), client=client, model="gpt-test", sleep=record)
    parsed, _ = await provider.generate_structured("p", Answer)
    assert parsed.score == 1 and len(client.calls) == 2 and delays[0] >= 2.0


@pytest.mark.parametrize(
    ("error", "expected", "calls"),
    [
        (AuthenticationError(), ProviderAuthenticationError, 1),  # never retried
        (BadRequestError(), ProviderRequestError, 1),  # never retried
        (APITimeoutError(), ProviderTimeoutError, 3),  # 1 + 2 retries
        (InternalServerError(), ProviderUnavailableError, 3),
        (RateLimitError(), ProviderRateLimitError, 3),
    ],
)
async def test_openai_error_normalization_and_bounded_retries(
    error, expected, calls
) -> None:
    provider, client = openai([error, error, error, error])
    with pytest.raises(expected) as info:
        await provider.generate("p")
    assert len(client.calls) == calls
    assert "sk-123" not in str(info.value)  # SDK messages are never propagated


async def test_openai_invalid_json_is_repaired_once_then_fails() -> None:
    provider, client = openai([openai_reply("not json"), openai_reply("{still bad")])
    with pytest.raises(LLMOutputError):
        await provider.generate_structured("p", Answer)
    assert len(client.calls) == 2 and "rejected" in client.calls[1]["input"]


async def test_openai_schema_violation_repaired() -> None:
    provider, _ = openai(
        [
            openai_reply('{"title": "", "score": 99}'),
            openai_reply('{"title": "ok", "score": 3}'),
        ]
    )
    parsed, response = await provider.generate_structured("p", Answer)
    assert parsed.score == 3 and response.metadata["repaired"] is True
    assert response.input_tokens == 240  # both calls are counted


async def test_openai_truncated_output_rejected_and_missing_usage_is_none() -> None:
    provider, _ = openai(
        [
            openai_reply(
                '{"title": "x"', status="incomplete", reason="max_output_tokens"
            ),
            openai_reply('{"title": "x", "score": 2}', usage=False),
        ]
    )
    _, response = await provider.generate_structured("p", Answer)
    # the truncated first call's measured tokens are kept; the second reported none
    assert response.input_tokens == 120 and response.metadata["repaired"] is True
    provider, _ = openai([openai_reply("hi", usage=False)])
    response = await provider.generate("p")
    assert (
        response.input_tokens is None and response.total_tokens is None
    )  # never invented


async def test_openai_requires_key_and_model() -> None:
    with pytest.raises(ConfigurationException, match="OPENAI_API_KEY"):
        await OpenAIProvider(Settings(), model="m").generate("p")
    with pytest.raises(ConfigurationException, match="OPENAI_MODEL"):
        await OpenAIProvider(Settings(openai_api_key=SecretStr("k"))).generate("p")


# -- Anthropic -------------------------------------------------------------------------


async def test_anthropic_structured_output_via_forced_tool() -> None:
    provider, client = anthropic(
        [anthropic_reply(tool_input={"title": "Acme", "score": 5})]
    )
    parsed, response = await provider.generate_structured("p", Answer, system="sys")
    assert parsed.title == "Acme"
    call = client.calls[0]
    assert call["tool_choice"] == {"type": "tool", "name": "record_result"}
    assert call["tools"][0]["input_schema"]["title"] == "Answer"
    assert call["system"] == "sys" and call["max_tokens"] == 4096
    assert (response.input_tokens, response.output_tokens) == (200, 40)
    assert response.provider == "anthropic" and response.request_id == "msg_1"


async def test_anthropic_text_and_truncation() -> None:
    provider, _ = anthropic([anthropic_reply(text="hello")])
    assert (await provider.generate("p")).text == "hello"
    provider, _ = anthropic([anthropic_reply(text="{", stop="max_tokens")] * 2)
    with pytest.raises(LLMOutputError, match="truncated"):
        await provider.generate_structured("p", Answer)


async def test_anthropic_errors_and_retries() -> None:
    provider, client = anthropic([InternalServerError(), anthropic_reply(text="ok")])
    assert (await provider.generate("p")).text == "ok" and len(client.calls) == 2
    provider, client = anthropic([AuthenticationError()])
    with pytest.raises(ProviderAuthenticationError):
        await provider.generate("p")
    assert len(client.calls) == 1


async def test_anthropic_missing_usage_and_config() -> None:
    provider, _ = anthropic([anthropic_reply(text="x", usage=False)])
    response = await provider.generate("p")
    assert response.usage.input_tokens is None and response.total_tokens is None
    with pytest.raises(ConfigurationException, match="ANTHROPIC_API_KEY"):
        await AnthropicProvider(Settings(), model="m").generate("p")


# -- mock -------------------------------------------------------------------------------


async def test_mock_is_deterministic_and_schema_valid() -> None:
    mock = MockProvider(settings())
    parsed, response = await mock.generate_structured("p", Answer)
    assert parsed == Answer(title="mock", score=0)
    assert (response.input_tokens, response.output_tokens) == (100, 50)
    again, _ = await mock.generate_structured("p", Answer)
    assert again == parsed and (await mock.generate("x")).text == "Mock response."


async def test_mock_scripted_failures_and_replies() -> None:
    mock = MockProvider(
        settings(llm_max_retries=0),
        responses={"Answer": {"title": "Scripted", "score": 9}},
        script=[
            ProviderRateLimitError("mock", "limited"),
            ProviderAuthenticationError("mock", "bad key"),
            ProviderTimeoutError("mock", "slow"),
            "malformed",
            "malformed",
        ],
        usage=(10, 5, 2),
    )
    for expected in (
        ProviderRateLimitError,
        ProviderAuthenticationError,
        ProviderTimeoutError,
    ):
        with pytest.raises(expected):
            await mock.generate("p")
    with pytest.raises(LLMOutputError):
        await mock.generate_structured("p", Answer)
    parsed, response = await mock.generate_structured("p", Answer)
    assert parsed.title == "Scripted" and response.cached_tokens == 2


def test_minimal_instance_handles_refs_enums_and_optionals() -> None:
    from app.intelligence.llm_schemas import AnalysisOutput, ComparisonOutput

    for schema in (AnalysisOutput, ComparisonOutput):
        schema.model_validate(minimal_instance(schema.model_json_schema()))


def test_extract_json_and_error_classification() -> None:
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('Here: {"a": 2} done') == {"a": 2}
    with pytest.raises(ValueError):
        extract_json("[1, 2]")
    err = classify_sdk_error("openai", RateLimitError("5"))
    assert isinstance(err, ProviderRateLimitError) and err.retry_after == 5.0
    assert "sk-123" not in str(err)


# -- fallback and factory ------------------------------------------------------------------


async def test_fallback_on_transient_error_records_provider_and_reason() -> None:
    primary = MockProvider(
        settings(llm_max_retries=0),
        script=[ProviderUnavailableError("mock", "down")],
        model="primary-model",
    )
    secondary = MockProvider(settings(), model="secondary-model")
    secondary.name = "anthropic"
    fallback = FallbackProvider(primary, secondary)
    parsed, response = await fallback.generate_structured("p", Answer)
    assert parsed == Answer(title="mock", score=0)  # validated identically
    assert response.provider == "anthropic" and response.model == "secondary-model"
    assert response.metadata == {
        **response.metadata,
        "fallback_from": "mock",
        "fallback_reason": "provider_unavailable",
    }


async def test_no_fallback_for_authentication_or_invalid_output() -> None:
    for event in (ProviderAuthenticationError("mock", "bad"), "malformed"):
        script = [event, event]
        primary = MockProvider(settings(llm_max_retries=0), script=script)
        secondary = MockProvider(settings())
        with pytest.raises((ProviderAuthenticationError, LLMOutputError)):
            await FallbackProvider(primary, secondary).generate_structured("p", Answer)
        assert secondary.calls == []


async def test_both_providers_failing_raises_and_never_fabricates() -> None:
    primary = MockProvider(
        settings(llm_max_retries=0), script=[ProviderUnavailableError("mock", "down")]
    )
    secondary = MockProvider(
        settings(llm_max_retries=0), script=[ProviderUnavailableError("mock", "down")]
    )
    with pytest.raises(ProviderUnavailableError):
        await FallbackProvider(primary, secondary).generate("p")


def test_factory_selection_and_override_rules() -> None:
    assert build_llm_provider(Settings()).name == "mock"
    keyed = Settings(
        llm_provider="openai", openai_api_key=SecretStr("k"), openai_model="gpt-a",
        anthropic_api_key=SecretStr("k2"), anthropic_model="claude-a",
    )  # fmt: skip
    assert build_llm_provider(keyed).model == "gpt-a"
    with pytest.raises(ConfigurationException, match="override is not permitted"):
        build_llm_provider(keyed, preferred_provider="anthropic")
    chosen = build_llm_provider(
        keyed, preferred_provider="anthropic", allow_override=True
    )
    assert chosen.name == "anthropic" and chosen.model == "claude-a"
    with pytest.raises(ConfigurationException, match="Model override"):
        build_llm_provider(keyed, preferred_model="gpt-expensive", allow_override=True)
    priced = build_llm_provider(
        keyed,
        preferred_model="gpt-b",
        allow_override=True,
        pricing_models=["openai:gpt-b"],
    )
    assert priced.model == "gpt-b"
    with pytest.raises(ConfigurationException, match="not configured"):
        build_llm_provider(Settings(llm_provider="anthropic"))


def test_factory_wraps_fallback_only_when_enabled_and_configured() -> None:
    base = dict(
        llm_provider="openai", openai_api_key=SecretStr("k"), openai_model="gpt-a",
        anthropic_api_key=SecretStr("k2"), anthropic_model="claude-a",
    )  # fmt: skip
    assert not isinstance(build_llm_provider(Settings(**base)), FallbackProvider)
    wrapped = build_llm_provider(Settings(**base, llm_enable_provider_fallback=True))
    assert (
        isinstance(wrapped, FallbackProvider) and wrapped.secondary.name == "anthropic"
    )
    alone = Settings(
        llm_provider="openai",
        openai_api_key=SecretStr("k"),
        openai_model="g",
        llm_enable_provider_fallback=True,
    )
    assert not isinstance(build_llm_provider(alone), FallbackProvider)


def test_finish_status_values() -> None:
    assert FinishStatus.TRUNCATED.value == "truncated"
    assert json.loads(Answer(title="a", score=1).model_dump_json())["score"] == 1
