"""Tests that skeleton modules import safely and fail honestly when unimplemented."""

import asyncio
import importlib
import pkgutil

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app
from app.core.exceptions import (
    AuroraException,
    ConfigurationException,
    IntegrationException,
    PersistenceException,
    ResearchException,
    ResourceNotFoundException,
    register_exception_handlers,
)
from app.tools.claude_tool import ClaudeTool
from app.tools.groq_tool import GroqTool
from app.tools.search_console_tool import SearchConsoleTool
from app.tools.wordpress_tool import WordPressTool
from app.workflows.research_workflow import build_research_workflow
from app.workflows.seo_content_workflow import build_seo_content_workflow


def test_every_module_imports() -> None:
    """All backend modules import without side effects or missing credentials."""
    names = [m.name for m in pkgutil.walk_packages(app.__path__, "app.")]
    assert len(names) > 50
    for name in names:
        importlib.import_module(name)


@pytest.mark.parametrize(
    ("tool_cls", "args"),
    [
        (GroqTool, ("complete", "prompt")),
        (ClaudeTool, ("complete", "prompt")),
        (WordPressTool, ("create_draft", "title", "body")),
        (SearchConsoleTool, ("query_performance", "p", "2026-01-01", "2026-01-31")),
    ],
)
def test_tools_require_credentials_and_never_fake_results(tool_cls, args) -> None:
    """Unconfigured tools raise ConfigurationException; no fake data is returned."""
    tool = tool_cls()
    assert tool.is_configured is False
    with pytest.raises(ConfigurationException) as excinfo:
        asyncio.run(getattr(tool, args[0])(*args[1:]))
    assert tool.missing_settings[0] in str(excinfo.value)


def test_configured_tool_still_raises_not_implemented(monkeypatch) -> None:
    """With credentials present, unimplemented operations still refuse to fake."""
    monkeypatch.setenv("GROQ_API_KEY", "test-value")
    from app.core.config import Settings

    tool = GroqTool(Settings())

    assert tool.is_configured is True
    with pytest.raises(NotImplementedError):
        asyncio.run(tool.complete("hi"))


@pytest.mark.parametrize(
    "builder", [build_research_workflow, build_seo_content_workflow]
)
def test_workflows_are_not_implemented(builder) -> None:
    """Workflow builders do not pretend to execute."""
    with pytest.raises(NotImplementedError):
        builder()


@pytest.mark.parametrize(
    ("exc", "status"),
    [
        (ConfigurationException("c"), 503),
        (IntegrationException("i"), 502),
        (ResearchException("r"), 422),
        (PersistenceException("p"), 503),
        (ResourceNotFoundException("n"), 404),
        (AuroraException("a"), 500),
    ],
)
def test_exception_handlers_return_safe_json(exc, status) -> None:
    """Application exceptions map to their status with a stable body."""
    application = FastAPI()
    register_exception_handlers(application)

    @application.get("/boom")
    async def boom() -> None:
        raise exc

    response = TestClient(application).get("/boom")

    assert response.status_code == status
    assert response.json()["error"]["message"] == str(exc)


def test_unexpected_errors_do_not_leak_details() -> None:
    """Unhandled errors return a generic message without internals."""
    application = FastAPI()
    register_exception_handlers(application)

    @application.get("/boom")
    async def boom() -> None:
        raise RuntimeError("secret internal detail")

    response = TestClient(application, raise_server_exceptions=False).get("/boom")

    assert response.status_code == 500
    assert "secret internal detail" not in response.text
