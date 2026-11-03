"""Command-line bookkeeping for the SEO Intelligence agent (no network access).

The ``/seo-*`` Claude Code skills do the analysis; this CLI records it
deterministically in the knowledge base (``seo-intelligence/``) through the same
services the API uses. Output is JSON on stdout (reports are Markdown).

Usage (repository root)::

    python scripts/seo_intelligence.py sites
    python scripts/seo_intelligence.py context nextsteps
    python scripts/seo_intelligence.py plan nextsteps --mode audit
    python scripts/seo_intelligence.py start nextsteps --mode audit
    python scripts/seo_intelligence.py record nextsteps <execution_id> --file p.json
    python scripts/seo_intelligence.py finish nextsteps <execution_id> --status partial
    python scripts/seo_intelligence.py finding nextsteps <key> --status <s> --note ...
    python scripts/seo_intelligence.py reject nextsteps <key> --reason "..." --by "Name"
    python scripts/seo_intelligence.py metrics nextsteps --file metrics.json
    python scripts/seo_intelligence.py research nextsteps --file research.json
    python scripts/seo_intelligence.py profile nextsteps --file profile.json
    python scripts/seo_intelligence.py history nextsteps
    python scripts/seo_intelligence.py report nextsteps [<execution_id>]
    python scripts/seo_intelligence.py skills [--check]
    python scripts/seo_intelligence.py propose --file proposal.json
    python scripts/seo_intelligence.py proposals [--status pending]
    python scripts/seo_intelligence.py decide <proposal_id> --approve --by "Name"
    python scripts/seo_intelligence.py apply <proposal_id>
    python scripts/seo_intelligence.py validate

Exit codes: 0 success, 1 validation problems, 2 a refused or invalid operation.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from pydantic import TypeAdapter, ValidationError  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.exceptions import AuroraException  # noqa: E402
from app.seo.intelligence.factory import (  # noqa: E402
    build_seo_agent_service,
    build_skill_improvement,
)
from app.seo.intelligence.models import (  # noqa: E402
    ExecutionPayload,
    MetricObservation,
    ResearchRecord,
)
from app.seo.intelligence.service import SeoAgentService  # noqa: E402
from app.seo.skills.improvement import SkillChange  # noqa: E402

MODES = [
    "audit",
    "competitor-analysis",
    "content-optimization",
    "performance-review",
    "update-skills",
    "baseline-import",
]


def _emit(data: Any) -> None:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    if isinstance(data, str):
        print(data)
        return
    print(json.dumps(data, indent=2, ensure_ascii=False, default=str))


def _json(path: str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _dump(model: Any) -> Any:
    return model.model_dump(mode="json") if hasattr(model, "model_dump") else model


def validate_all(service: SeoAgentService) -> list[str]:
    problems = [f"registry: {p}" for p in service.registry.check()]
    for site_id in service.repository.list_site_ids():
        try:
            service.website(site_id)
            service.repository.load_findings(site_id)
            service.repository.load_rejected(site_id)
            service.repository.load_changes(site_id)
            service.repository.load_metrics(site_id)
            service.repository.list_research(site_id)
            for entry in service.repository.load_index(site_id):
                service.repository.load_execution(site_id, entry.id)
        except AuroraException as exc:
            problems.append(f"{site_id}: {exc.message}")
    return problems


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--root", help="knowledge root (default: settings)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("sites")
    for name in ("context", "history"):
        sub.add_parser(name).add_argument("site")
    p = sub.add_parser("plan")
    p.add_argument("site")
    p.add_argument("--mode", choices=MODES, default="audit")
    p = sub.add_parser("start")
    p.add_argument("site")
    p.add_argument("--mode", choices=MODES, required=True)
    p = sub.add_parser("record")
    p.add_argument("site")
    p.add_argument("execution")
    p.add_argument("--file", required=True)
    p = sub.add_parser("finish")
    p.add_argument("site")
    p.add_argument("execution")
    p.add_argument(
        "--status", choices=["completed", "partial", "blocked", "failed"], required=True
    )
    p.add_argument("--summary")
    p = sub.add_parser("finding")
    p.add_argument("site")
    p.add_argument("key")
    p.add_argument(
        "--status",
        choices=[
            "open",
            "in_progress",
            "implemented",
            "verified",
            "resolved",
            "deferred",
        ],
        required=True,
    )
    p.add_argument("--note", required=True)
    p.add_argument("--reference")
    p.add_argument("--execution")
    p = sub.add_parser("reject")
    p.add_argument("site")
    p.add_argument("key")
    p.add_argument("--reason", required=True)
    p.add_argument("--by", required=True)
    for name in ("metrics", "research", "profile"):
        p = sub.add_parser(name)
        p.add_argument("site")
        p.add_argument("--file", required=True)
    p = sub.add_parser("report")
    p.add_argument("site")
    p.add_argument("execution", nargs="?")
    sub.add_parser("skills").add_argument("--check", action="store_true")
    sub.add_parser("propose").add_argument("--file", required=True)
    sub.add_parser("proposals").add_argument("--status")
    p = sub.add_parser("decide")
    p.add_argument("proposal")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--approve", action="store_true")
    group.add_argument("--reject", action="store_true")
    p.add_argument("--by", required=True)
    p.add_argument("--note", default="")
    p.add_argument("--accept-single-execution", action="store_true")
    sub.add_parser("apply").add_argument("proposal")
    sub.add_parser("validate")
    return parser


def run(args: argparse.Namespace) -> int:
    settings = get_settings()
    if args.root:
        settings = settings.model_copy(
            update={"seo_intelligence_root": Path(args.root)}
        )
    service = build_seo_agent_service(settings)
    improvement = build_skill_improvement(settings)
    cmd = args.command

    if cmd == "sites":
        _emit(
            [
                {"id": w.id, "name": w.name, "domain": w.domain}
                for w in service.websites()
            ]
        )
    elif cmd == "context":
        _emit(service.context(args.site))
    elif cmd == "history":
        _emit([_dump(e) for e in service.executions(args.site)])
    elif cmd == "plan":
        _emit([_dump(s) for s in service.plan(args.site, args.mode)])
    elif cmd == "start":
        execution = service.start(args.site, args.mode)
        _emit({"execution_id": execution.id, "status": execution.status})
    elif cmd == "record":
        payload = ExecutionPayload.model_validate(_json(args.file))
        execution = service.record(args.site, args.execution, payload)
        _emit(
            {
                "execution_id": execution.id,
                "findings": len(execution.findings),
                "skills": [s.name for s in execution.skills_used],
                "tools": len(execution.tools_used),
            }
        )
    elif cmd == "finish":
        execution, _ = service.finish(
            args.site, args.execution, args.status, args.summary
        )
        site_dir = service.repository.site_dir(args.site)
        _emit(
            {
                "execution_id": execution.id,
                "status": execution.status,
                "comparison": _dump(execution.comparison),
                "report": str(site_dir / "reports" / f"{execution.id}.md"),
            }
        )
    elif cmd == "finding":
        finding = service.update_finding(
            args.site,
            args.key,
            args.status,
            args.note,
            reference=args.reference,
            execution_id=args.execution,
        )
        _emit({"key": finding.key, "status": finding.status})
    elif cmd == "reject":
        _emit(_dump(service.reject(args.site, args.key, args.reason, args.by)))
    elif cmd == "metrics":
        items = TypeAdapter(list[MetricObservation]).validate_python(_json(args.file))
        _emit({"recorded": service.record_metrics(args.site, items)})
    elif cmd == "research":
        record = ResearchRecord.model_validate(_json(args.file))
        service.website(args.site)
        _emit({"saved": str(service.repository.save_research(args.site, record))})
    elif cmd == "profile":
        service.website(args.site)
        service.repository.save_profile(args.site, _json(args.file))
        _emit({"saved": "website-profile.json"})
    elif cmd == "report":
        execution_id = args.execution or next(
            (e.id for e in service.executions(args.site) if e.status != "running"), None
        )
        if execution_id is None:
            _emit({"error": "no finished execution yet"})
            return 2
        _emit(service.report(args.site, execution_id))
    elif cmd == "skills":
        if args.check:
            problems = service.registry.check()
            _emit({"problems": problems})
            return 1 if problems else 0
        _emit([_dump(s) for s in service.registry.skills()])
    elif cmd == "propose":
        data = _json(args.file)
        proposal = improvement.propose(
            data["skill"],
            SkillChange.model_validate(data["change"]),
            data["rationale"],
            data.get("evidence", []),
            data.get("evidence_executions", []),
        )
        _emit(_dump(proposal))
    elif cmd == "proposals":
        _emit([_dump(p) for p in improvement.list(args.status)])
    elif cmd == "decide":
        proposal = improvement.decide(
            args.proposal,
            approve=args.approve,
            decided_by=args.by,
            note=args.note,
            accept_single_execution=args.accept_single_execution,
        )
        _emit({"id": proposal.id, "status": proposal.status})
    elif cmd == "apply":
        proposal = improvement.apply(args.proposal)
        _emit({"id": proposal.id, "status": proposal.status, "skill": proposal.skill})
    elif cmd == "validate":
        problems = validate_all(service)
        _emit({"problems": problems, "sites": service.repository.list_site_ids()})
        return 1 if problems else 0
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return run(args)
    except (AuroraException, ValidationError, KeyError, OSError) as exc:
        message = exc.message if isinstance(exc, AuroraException) else str(exc)
        _emit({"error": type(exc).__name__, "message": message[:2000]})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
