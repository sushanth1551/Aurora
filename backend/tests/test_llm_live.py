"""Opt-in live LLM checks. They spend real credits, so they are skipped unless
``AURORA_RUN_LIVE_LLM_TESTS=true`` and the provider key and model are set in ``.env``.
Each test makes one small structured request."""

import os

import pytest
from pydantic import BaseModel, Field

from app.core.config import ENV_FILE, Settings
from app.llm.anthropic_provider import AnthropicProvider
from app.llm.groq_provider import GroqProvider
from app.llm.openai_provider import OpenAIProvider
from app.llm.provider_factory import provider_is_configured

LIVE = os.environ.get("AURORA_RUN_LIVE_LLM_TESTS", "false").lower() == "true"
pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not LIVE, reason="AURORA_RUN_LIVE_LLM_TESTS is not 'true'"),
]


class Capital(BaseModel):
    country: str = Field(max_length=60)
    capital: str = Field(max_length=60)


PROMPT = "Return the country 'France' and its capital city."


def live_settings() -> Settings:
    return Settings(_env_file=ENV_FILE, llm_max_retries=1, llm_max_output_tokens=200)  # type: ignore[call-arg]


@pytest.mark.parametrize(
    ("name", "cls"),
    [
        ("openai", OpenAIProvider),
        ("anthropic", AnthropicProvider),
        ("groq", GroqProvider),
    ],
)
async def test_live_structured_output(name, cls) -> None:
    settings = live_settings()
    if not provider_is_configured(name, settings):
        pytest.skip(f"{name.upper()}_API_KEY / {name.upper()}_MODEL not set")
    if name == "openai":
        pytest.importorskip("openai")
    if name == "groq" and settings.groq_model is None:
        pytest.skip("GROQ_MODEL is not set")
    parsed, response = await cls(settings).generate_structured(PROMPT, Capital)
    assert parsed.capital.lower() == "paris"
    assert (
        response.provider == name and response.input_tokens and response.output_tokens
    )
