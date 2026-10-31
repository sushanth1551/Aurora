"""Development commands for the knowledge graph (never run automatically).

Usage (repository root, ``PYTHONPATH=backend``)::

    python scripts/graph_admin.py health
    python scripts/graph_admin.py init-schema          # idempotent constraints + indexes
    python scripts/graph_admin.py verify-schema
    python scripts/graph_admin.py inspect-schema       # list constraint/index statements
    python scripts/graph_admin.py sync --job-id <uuid> [--dry-run]
    python scripts/graph_admin.py sync-all [--dry-run] [--limit N]     # full sync
    python scripts/graph_admin.py sync-incremental [--limit N]         # pending outbox events
    python scripts/graph_admin.py retry-failed [--limit N]
    python scripts/graph_admin.py reconcile [--apply]                   # default: dry run
    python scripts/graph_admin.py cleanup-test --ids-file ids.txt --confirm   # test-only

There is deliberately no command that drops or resets the whole graph.
``cleanup-test`` deletes only the node ids listed in a file, and only with
``--confirm``. Credentials come from settings and are never printed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.core.config import get_settings  # noqa: E402
from app.graph.graph_health import check_health  # noqa: E402
from app.graph.graph_repository import Neo4jGraphRepository  # noqa: E402
from app.graph.graph_schema import CONSTRAINTS, INDEXES, SCHEMA_VERSION  # noqa: E402
from app.graph.neo4j_client import Neo4jClient  # noqa: E402


def _print(data) -> None:
    print(json.dumps(data, indent=2, default=str))


async def _with_sync(fn):
    from app.database.repositories.graph_sync_repository import GraphSyncRepository
    from app.database.repositories.intelligence_repository import IntelligenceRepository
    from app.database.repositories.research_repository import ResearchRepository
    from app.database.session import create_session_factory, dispose_engine
    from app.graph.graph_sync_service import GraphSyncService

    settings = get_settings()
    client = Neo4jClient(settings)
    try:
        async with create_session_factory()() as session:
            service = GraphSyncService(
                research_repo=ResearchRepository(session),
                intelligence_repo=IntelligenceRepository(session),
                sync_repo=GraphSyncRepository(session),
                graph=Neo4jGraphRepository(client) if client.is_configured else None,
                settings=settings,
            )
            return await fn(service)
    finally:
        await client.close()
        await dispose_engine()


async def main(args: argparse.Namespace) -> int:
    settings = get_settings()
    if args.command == "inspect-schema":
        _print(
            {
                "version": SCHEMA_VERSION,
                "constraints": dict(CONSTRAINTS),
                "indexes": dict(INDEXES),
            }
        )
        return 0
    if args.command in ("health", "init-schema", "verify-schema", "cleanup-test"):
        client = Neo4jClient(settings)
        try:
            if args.command == "health":
                _print((await check_health(client)).model_dump())
                return 0
            repo = Neo4jGraphRepository(client)
            if args.command == "init-schema":
                _print(await repo.initialize_schema())
            elif args.command == "verify-schema":
                result = await repo.verify_schema()
                _print(result)
                return (
                    1
                    if result["missing_constraints"] or result["missing_indexes"]
                    else 0
                )
            else:
                if not args.confirm or not args.ids_file:
                    print(
                        "cleanup-test needs --ids-file and --confirm; nothing deleted."
                    )
                    return 2
                ids = [
                    line.strip()
                    for line in Path(args.ids_file).read_text().splitlines()
                    if line.strip()
                ]
                _print({"deleted": await repo.delete_nodes(ids)})
            return 0
        finally:
            await client.close()

    async def run(service):
        if args.command == "sync":
            return [await service.sync_job(UUID(args.job_id), dry_run=args.dry_run)]
        if args.command == "sync-all":
            return await service.full_sync(dry_run=args.dry_run, limit=args.limit)
        if args.command == "sync-incremental":
            return await service.process_due(limit=args.limit)
        if args.command == "retry-failed":
            return await service.process_due(limit=args.limit, include_pending=False)
        return await service.reconcile(dry_run=not args.apply)

    result = await _with_sync(run)
    if isinstance(result, list):
        _print(
            [r.model_dump(mode="json", exclude={"rejected", "review"}) for r in result]
        )
        return 0 if all(r.status in ("completed", "dry_run") for r in result) else 1
    _print(result.model_dump(mode="json"))
    return 0


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = p.add_subparsers(dest="command", required=True)
    for name in ("health", "init-schema", "verify-schema", "inspect-schema"):
        sub.add_parser(name)
    s = sub.add_parser("sync")
    s.add_argument("--job-id", required=True)
    s.add_argument("--dry-run", action="store_true")
    s = sub.add_parser("sync-all")
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--limit", type=int, default=1000)
    for name in ("sync-incremental", "retry-failed"):
        sub.add_parser(name).add_argument("--limit", type=int, default=20)
    sub.add_parser("reconcile").add_argument(
        "--apply", action="store_true", help="enqueue drifted jobs"
    )
    c = sub.add_parser("cleanup-test")
    c.add_argument("--ids-file")
    c.add_argument("--confirm", action="store_true")
    return p


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(parser().parse_args())))
