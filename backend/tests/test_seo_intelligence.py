"""SEO Intelligence: knowledge repository, history comparison, executions,
skill registry, controlled skill improvement, audit import, API and CLI.

Everything runs on temporary directories; no network, database or credentials.
"""

from __future__ import annotations

import json
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.config import get_settings
from app.core.exceptions import (
    ConflictException,
    ResearchException,
    ResourceNotFoundException,
)
from app.main import create_app
from app.seo.intelligence.adapters import import_technical_audit, priority_for
from app.seo.intelligence.knowledge import SeoKnowledgeRepository
from app.seo.intelligence.models import (
    CheckedScope,
    Evidence,
    Execution,
    ExecutionPayload,
    Finding,
    SkillUse,
    ToolUse,
    WebsiteConfig,
)
from app.seo.intelligence.service import SeoAgentService
from app.seo.skills.improvement import SkillChange, SkillImprovementService
from app.seo.skills.registry import SkillRegistry, content_hash
from tests.auth_support import AUTH_HEADERS

ROOT = Path(__file__).resolve().parents[2]
KNOWLEDGE = ROOT / "seo-intelligence"
SKILLS = ROOT / ".claude" / "skills"
BASE = "/api/v1/seo/intelligence"


def finding(key: str, category: str = "on-page/metadata", **kw: object) -> Finding:
    data: dict[str, object] = {
        "key": key,
        "issue": f"Issue {key}",
        "category": category,
        "priority": "medium",
        "evidence": [Evidence(source="test", observation="seen")],
        "seo_impact": "Snippet truncated.",
        "recommended_action": "Shorten it.",
        "expected_outcome": "The full text can show.",
        "verification_method": "Re-run audit_page.",
    }
    data.update(kw)
    return Finding.model_validate(data)


def write_registry(root: Path, skills_dir: Path) -> None:
    """A tiny registry: one AURORA skill, one third-party skill, one MCP-backed skill."""
    for name in ("own-skill", "third-party", "mcp-skill"):
        folder = skills_dir / name
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: test\nmetadata:\n  version: '1.0'\n---\n\n"
            f"# {name}\n\nBody text.\n",
            encoding="utf-8",
        )
    registry = {
        "version": 1,
        "updated": "2026-11-03",
        "skills": [
            {
                "name": "own-skill",
                "role": "r",
                "origin": "aurora-seo-intelligence",
                "modes": ["audit"],
            },
            {
                "name": "third-party",
                "role": "r",
                "origin": "seo-toolkit",
                "modes": ["audit"],
                "requires": [{"kind": "setting", "name": "serpapi_api_key"}],
            },
            {
                "name": "mcp-skill",
                "role": "r",
                "origin": "external",
                "modes": ["audit"],
                "requires": [{"kind": "mcp", "name": "seo-data"}],
            },
        ],
        "modes": {
            "audit": {
                "entry_skill": "own-skill",
                "description": "d",
                "steps": [
                    {"key": "a", "skill": "own-skill", "purpose": "p"},
                    {"key": "b", "skill": "third-party", "purpose": "p"},
                    {"key": "c", "skill": "mcp-skill", "purpose": "p"},
                    {
                        "key": "d",
                        "skill": "not-installed",
                        "purpose": "p",
                        "optional": True,
                    },
                ],
            }
        },
    }
    path = root / "skills" / "skill-registry.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(registry), encoding="utf-8")


@pytest.fixture
def env(tmp_path: Path) -> SimpleNamespace:
    root, skills = tmp_path / "kb", tmp_path / "skills"
    write_registry(root, skills)
    repo = SeoKnowledgeRepository(root)
    repo.save_config(
        WebsiteConfig(
            id="example",
            name="Example",
            domain="example.com",
            base_url="https://example.com",
        )
    )
    registry = SkillRegistry(root / "skills" / "skill-registry.json", skills)
    return SimpleNamespace(
        root=root,
        skills=skills,
        repo=repo,
        registry=registry,
        service=SeoAgentService(repo, registry),
    )


def run_once(
    service: SeoAgentService,
    findings: list[Finding],
    checked: CheckedScope | None = None,
) -> Execution:
    execution = service.start("example", "audit")
    service.record(
        "example",
        execution.id,
        ExecutionPayload(
            findings=findings,
            checked=checked or CheckedScope(),
            skills_used=[SkillUse(name="own-skill", reason="test")],
            tools_used=[
                ToolUse(name="seo-data audit_page", kind="mcp", status="success")
            ],
        ),
    )
    finished, _ = service.finish("example", execution.id, "completed")
    return finished


# --- models -------------------------------------------------------------------------


def test_finding_rejects_guaranteed_outcomes_and_bad_keys() -> None:
    with pytest.raises(ValidationError):
        finding("title:/a", expected_outcome="Guaranteed first page rankings.")
    with pytest.raises(ValidationError):
        finding("Has Spaces")
    with pytest.raises(ValidationError):
        finding("x:/a", evidence=[])
    assert finding("Title:/A").key == "title:/a"


def test_site_ids_cannot_escape_the_knowledge_root(env: SimpleNamespace) -> None:
    with pytest.raises(ValueError):
        env.repo.site_dir("../outside")
    with pytest.raises(ResourceNotFoundException):
        env.repo.execution_path("example", "../../x")
    with pytest.raises(ValidationError):
        WebsiteConfig(id="Bad_ID", name="x", domain="x.com", base_url="https://x.com")


# --- history comparison ---------------------------------------------------------------


def test_comparison_new_recurring_resolved_and_not_rechecked(
    env: SimpleNamespace,
) -> None:
    first = run_once(
        env.service,
        [finding("a:/1"), finding("b:/2"), finding("c:/3", category="content/thin")],
    )
    assert first.comparison is not None
    assert first.comparison.new == ["a:/1", "b:/2", "c:/3"]

    # Second run re-checks metadata only: b disappears (resolved); c was not
    # rechecked, so it must stay open rather than be called fixed.
    second = run_once(
        env.service,
        [finding("a:/1"), finding("d:/4")],
        CheckedScope(categories=["on-page/metadata"]),
    )
    assert second.comparison is not None
    assert second.comparison.new == ["d:/4"]
    assert second.comparison.recurring == ["a:/1"]
    assert second.comparison.resolved == ["b:/2"]
    assert second.comparison.not_rechecked == ["c:/3"]
    ledger = {f.key: f for f in env.repo.load_findings("example")}
    assert ledger["b:/2"].status == "resolved"
    assert ledger["c:/3"].status == "open"
    assert ledger["a:/1"].seen_in == [first.id, second.id]

    third = run_once(env.service, [finding("b:/2")])
    assert third.comparison is not None and third.comparison.reopened == ["b:/2"]
    assert env.repo.load_findings("example")[1].status == "open"


def test_implemented_findings_are_verified_or_flagged(env: SimpleNamespace) -> None:
    run_once(env.service, [finding("a:/1"), finding("b:/2")])
    env.service.update_finding(
        "example", "a:/1", "implemented", "commit abc", reference="abc"
    )
    env.service.update_finding("example", "b:/2", "implemented", "commit def")
    run_once(
        env.service, [finding("b:/2")], CheckedScope(categories=["on-page/metadata"])
    )
    ledger = {f.key: f for f in env.repo.load_findings("example")}
    assert ledger["a:/1"].status == "verified"
    assert ledger["b:/2"].status == "implemented"
    assert "still observed" in ledger["b:/2"].history[-1].note
    report = env.repo.load_report("example", env.service.executions("example")[0].id)
    assert "Marked implemented but still observed" in report


def test_rejected_decisions_survive_later_executions(env: SimpleNamespace) -> None:
    run_once(env.service, [finding("fees:/cost")])
    env.service.reject("example", "fees:/cost", "Owner wants dollars only", "Owner")
    later = run_once(env.service, [finding("fees:/cost")])
    assert later.comparison is not None and later.comparison.recurring == ["fees:/cost"]
    assert env.repo.load_findings("example")[0].status == "rejected"
    assert env.repo.load_rejected("example")[0].reason == "Owner wants dollars only"
    with pytest.raises(ResearchException):
        env.service.reject("example", "fees:/cost", " ", "Owner")


# --- execution lifecycle ----------------------------------------------------------------


def test_lifecycle_rules_and_report(env: SimpleNamespace) -> None:
    execution = env.service.start("example", "audit")
    with pytest.raises(ConflictException):
        env.service.start("example", "audit")
    env.service.record(
        "example",
        execution.id,
        ExecutionPayload(
            findings=[finding("a:/1", priority="critical")],
            blockers=["PageSpeed quota exhausted"],
            skills_used=[SkillUse(name="own-skill", reason="plan step")],
            tools_used=[
                ToolUse(
                    name="pagespeed", kind="mcp", status="unavailable", note="quota"
                )
            ],
        ),
    )
    with pytest.raises(ResearchException):
        env.service.finish("example", execution.id, "completed")
    done, report = env.service.finish("example", execution.id, "partial")
    assert done.status == "partial"
    assert done.skills_used[0].content_hash == content_hash(
        env.skills / "own-skill" / "SKILL.md"
    )
    for heading in (
        "## Execution summary",
        "## Skills used",
        "## Tools and integrations used",
        "## Changes performed",
        "### Critical improvements",
        "## Learning summary",
        "Unavailable or failed: pagespeed",
        "does not predict rankings",
    ):
        assert heading in report
    assert (env.root / "sites" / "example" / "reports" / "latest-report.md").exists()
    assert env.service.executions("example")[0].status == "partial"
    with pytest.raises(ConflictException):
        env.service.record("example", execution.id, ExecutionPayload())


def test_unknown_website_and_invalid_files(env: SimpleNamespace) -> None:
    with pytest.raises(ResourceNotFoundException):
        env.service.context("missing")
    (env.root / "sites" / "example").mkdir(parents=True, exist_ok=True)
    (env.root / "sites" / "example" / "findings.json").write_text(
        "[{}]", encoding="utf-8"
    )
    with pytest.raises(Exception, match="invalid"):
        env.service.findings("example")


# --- skill registry and improvement ----------------------------------------------------


def test_registry_plan_reports_availability(env: SimpleNamespace) -> None:
    steps = {s.key: s for s in env.registry.plan("audit")}
    assert steps["a"].availability == "available"
    assert steps["b"].availability == "missing_setting"
    assert "SERPAPI_API_KEY" in steps["b"].reasons[0]
    assert steps["c"].availability == "runtime_check"
    assert steps["d"].availability == "missing_skill"
    keyed = SkillRegistry(env.registry.registry_path, env.skills, {"serpapi_api_key"})
    assert {s.key: s for s in keyed.plan("audit")}["b"].availability == "available"
    assert env.registry.check() == ["mode 'audit' step 'd' is unregistered"]
    with pytest.raises(ResourceNotFoundException):
        env.registry.plan("nope")


def test_content_hash_ignores_line_endings(tmp_path: Path) -> None:
    a, b = tmp_path / "a.md", tmp_path / "b.md"
    a.write_bytes(b"x\ny\n")
    b.write_bytes(b"x\r\ny\r\n")
    assert content_hash(a) == content_hash(b)


def test_skill_changes_need_approval_and_fresh_base(env: SimpleNamespace) -> None:
    improvement = SkillImprovementService(env.registry, env.root)
    change = SkillChange(
        kind="append_section", section_title="Refinement", text="New rule."
    )
    with pytest.raises(ResearchException):
        improvement.propose(
            "third-party", change, "Seen twice in audits.", ["k"], ["e1"]
        )

    proposal = improvement.propose(
        "own-skill", change, "Seen in one audit.", ["k"], ["e1"]
    )
    assert proposal.single_execution
    with pytest.raises(ConflictException):
        improvement.propose("own-skill", change, "Seen in one audit.", ["k"], ["e1"])
    with pytest.raises(ConflictException):
        improvement.apply(proposal.id)
    with pytest.raises(ResearchException):
        improvement.decide(proposal.id, approve=True, decided_by="Owner")
    improvement.decide(
        proposal.id, approve=True, decided_by="Owner", accept_single_execution=True
    )
    applied = improvement.apply(proposal.id)
    skill_text = (env.skills / "own-skill" / "SKILL.md").read_text(encoding="utf-8")
    assert applied.status == "applied" and "## Refinement\n\nNew rule." in skill_text
    assert "Approved by: Owner" in (env.root / "skills" / "CHANGELOG.md").read_text(
        encoding="utf-8"
    )

    stale = improvement.propose(
        "own-skill",
        SkillChange(kind="replace_text", old_text="Body text.", text="Changed."),
        "Seen in two audits.",
        ["k"],
        ["e1", "e2"],
    )
    assert not stale.single_execution
    improvement.decide(stale.id, approve=True, decided_by="Owner")
    (env.skills / "own-skill" / "SKILL.md").write_text(
        "edited meanwhile", encoding="utf-8"
    )
    with pytest.raises(ConflictException):
        improvement.apply(stale.id)
    assert improvement.get(stale.id).status == "stale"


# --- adapter: import an AURORA technical audit -----------------------------------------


class FakeAudits:
    def __init__(self, status: str, host: str = "www.example.com") -> None:
        self.audit_id = uuid4()
        self.finding_id = uuid4()
        self.audit = SimpleNamespace(
            id=self.audit_id,
            host=host,
            status=status,
            coverage={"pages_audited": 12},
            completed_at=datetime(2026, 11, 3, tzinfo=UTC),
        )

    async def get(self, audit_id):
        return self.audit if audit_id == self.audit_id else None

    async def findings(self, audit_id):
        return [
            SimpleNamespace(
                id=self.finding_id,
                check_id="title_missing",
                category="metadata",
                severity="high",
                title="Pages without a title",
                detail="2 pages",
                affected_urls=["https://example.com/a"],
                evidence_state="observed",
                evidence=[
                    {
                        "url": "https://example.com/a",
                        "source": "firecrawl_raw_html",
                        "observed": {"title": None},
                    }
                ],
                skill_refs=["seo-toolkit-page-audit"],
            )
        ]

    async def recommendations(self, audit_id):
        return [
            SimpleNamespace(
                finding_id=self.finding_id,
                tier="blocker",
                action="Add titles",
                rationale="Titles are required",
                verification="Re-run the audit",
            )
        ]


async def test_import_technical_audit(env: SimpleNamespace) -> None:
    audits = FakeAudits("completed")
    execution = await import_technical_audit(
        env.service, audits, "example", audits.audit_id
    )
    assert execution.status == "completed"
    assert execution.findings[0].key == "tech:title_missing"
    assert execution.findings[0].priority == "critical"
    assert execution.findings[0].evidence[0].url == "https://example.com/a"

    partial = FakeAudits("partially_completed")
    second = await import_technical_audit(
        env.service, partial, "example", partial.audit_id
    )
    assert second.status == "partial" and second.blockers

    for bad in (FakeAudits("running"), FakeAudits("completed", host="other.com")):
        with pytest.raises(ConflictException):
            await import_technical_audit(env.service, bad, "example", bad.audit_id)


def test_priority_mapping() -> None:
    assert priority_for("high", "blocker") == "critical"
    assert priority_for("high", "high_leverage") == "high"
    assert priority_for("medium", None) == "medium"
    assert priority_for("info", None) == "low"


# --- API ------------------------------------------------------------------------------


def test_api_reads_knowledge_base(
    env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SEO_INTELLIGENCE_ROOT", str(env.root))
    monkeypatch.setenv("SEO_SKILLS_DIR", str(env.skills))
    get_settings.cache_clear()
    executed = run_once(env.service, [finding("a:/1", priority="high")])
    client = TestClient(create_app(), headers=AUTH_HEADERS)

    sites = client.get(f"{BASE}/websites").json()
    assert sites["total"] == 1 and sites["items"][0]["id"] == "example"
    plan = client.get(f"{BASE}/websites/example/plan").json()
    assert [s["availability"] for s in plan["steps"]][0] == "available"
    found = client.get(f"{BASE}/websites/example/findings", params={"priority": "high"})
    assert found.json()["total"] == 1
    history = client.get(f"{BASE}/websites/example/executions").json()
    assert history["items"][0]["id"] == executed.id
    report = client.get(f"{BASE}/websites/example/executions/{executed.id}/report")
    assert report.status_code == 200 and "## Skills used" in report.text
    assert client.get(f"{BASE}/websites/missing").status_code == 404
    assert client.get(f"{BASE}/websites/Bad..id").status_code == 422
    skills = client.get(f"{BASE}/skills").json()
    assert skills["total"] == 3 and skills["problems"]
    assert client.get(f"{BASE}/skills/proposals").json()["total"] == 0
    assert TestClient(create_app()).get(f"{BASE}/websites").status_code == 401


# --- committed knowledge base and CLI ------------------------------------------------


def test_committed_registry_matches_installed_skills() -> None:
    registry = SkillRegistry(KNOWLEDGE / "skills" / "skill-registry.json", SKILLS)
    assert registry.check() == []
    modes = registry.load().modes
    assert set(modes) >= {
        "audit",
        "competitor-analysis",
        "content-optimization",
        "performance-review",
        "update-skills",
    }
    for mode, definition in modes.items():
        assert (SKILLS / definition.entry_skill / "SKILL.md").exists(), mode


def test_committed_knowledge_base_is_valid() -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    import seo_intelligence as cli

    service = SeoAgentService(
        SeoKnowledgeRepository(KNOWLEDGE),
        SkillRegistry(KNOWLEDGE / "skills" / "skill-registry.json", SKILLS),
    )
    assert cli.validate_all(service) == []
    assert "nextsteps" in service.repository.list_site_ids()


def test_cli_full_cycle(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    import seo_intelligence as cli

    root = tmp_path / "kb"
    shutil.copytree(
        KNOWLEDGE / "skills",
        root / "skills",
        ignore=shutil.ignore_patterns("proposals", "CHANGELOG.md"),
    )
    SeoKnowledgeRepository(root).save_config(
        WebsiteConfig(
            id="demo", name="Demo", domain="demo.com", base_url="https://demo.com"
        )
    )

    def call(*args: str) -> tuple[int, str]:
        code = cli.main(["--root", str(root), *args])
        return code, capsys.readouterr().out

    code, out = call("start", "demo", "--mode", "audit")
    assert code == 0
    execution_id = json.loads(out)["execution_id"]
    payload = tmp_path / "payload.json"
    payload.write_text(
        ExecutionPayload(findings=[finding("a:/1")]).model_dump_json(), encoding="utf-8"
    )
    assert call("record", "demo", execution_id, "--file", str(payload))[0] == 0
    code, out = call("finish", "demo", execution_id, "--status", "completed")
    assert code == 0 and json.loads(out)["comparison"]["new"] == ["a:/1"]
    code, out = call("report", "demo")
    assert code == 0 and "SEO execution report: Demo" in out
    payload.write_text('{"findings": [{"key": "x"}]}', encoding="utf-8")
    code, out = call("start", "demo", "--mode", "audit")
    second = json.loads(out)["execution_id"]
    code, out = call("record", "demo", second, "--file", str(payload))
    assert code == 2 and "ValidationError" in out
    code, out = call("context", "missing")
    assert code == 2 and "not configured" in out


def test_api_insights_dashboard(
    env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from datetime import date

    from app.seo.intelligence.insights.store import InsightStore
    from app.seo.intelligence.knowledge import SeoKnowledgeRepository

    monkeypatch.setenv("SEO_INTELLIGENCE_ROOT", str(env.root))
    monkeypatch.setenv("SEO_SKILLS_DIR", str(env.skills))
    monkeypatch.setenv("SEO_INTELLIGENCE_DATA_ROOT", str(tmp_path / "data"))
    get_settings.cache_clear()
    client = TestClient(create_app(), headers=AUTH_HEADERS)
    assert client.get(f"{BASE}/websites/example/insights/alerts").status_code == 404
    store = InsightStore(SeoKnowledgeRepository(env.root), tmp_path / "data")
    store.save_snapshot("example", "alerts", date(2026, 11, 4), {"alerts": []})
    board = client.get(f"{BASE}/websites/example/insights").json()
    assert board["site_id"] == "example" and board["updated"]["alerts"] == "2026-11-04"
    one = client.get(f"{BASE}/websites/example/insights/alerts").json()
    assert one["kind"] == "alerts"
    assert client.get(f"{BASE}/websites/example/insights/bogus").status_code == 422
    assert client.get(f"{BASE}/websites/missing/insights").status_code == 404
