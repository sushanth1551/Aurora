"""File-backed SEO knowledge repository with per-website isolation.

Layout under the knowledge root (``Settings.seo_intelligence_root``)::

    config/sites/<site_id>.json            website configuration (WebsiteConfig)
    sites/<site_id>/website-profile.json   discovered profile (free-form, dated)
    sites/<site_id>/findings.json          finding ledger: every issue ever seen
    sites/<site_id>/rejected-recommendations.json
    sites/<site_id>/implemented-changes.json
    sites/<site_id>/metrics/performance-history.json
    sites/<site_id>/research/<kind>/<id>.json   keyword/competitor/content snapshots
    sites/<site_id>/executions/execution-history.json   index, newest last
    sites/<site_id>/executions/<execution_id>.json      full execution record
    sites/<site_id>/reports/<execution_id>.md and latest-report.md

JSON files are the system of record for this phase; the repository interface
is small so a PostgreSQL implementation can replace it later (see
``docs/seo-intelligence.md``). Writes are atomic (temp file + replace). Raw
page HTML or provider payloads are never stored here; they belong in ``data/``.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, TypeAdapter, ValidationError

from app.core.exceptions import (
    ConfigurationException,
    PersistenceException,
    ResourceNotFoundException,
)
from app.seo.intelligence.models import (
    ChangeRecord,
    Execution,
    ExecutionIndexEntry,
    Finding,
    MetricObservation,
    RejectedRecommendation,
    ResearchRecord,
    WebsiteConfig,
    validate_site_id,
)

T = TypeVar("T")
EXECUTION_ID_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789-")


def _dump(value: Any) -> str:
    if isinstance(value, BaseModel):
        data = value.model_dump(mode="json")
    elif isinstance(value, list):
        data = [
            v.model_dump(mode="json") if isinstance(v, BaseModel) else v for v in value
        ]
    else:
        data = value
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def write_atomic(path: Path, text: str) -> None:
    """Write ``text`` so readers never see a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.replace(tmp, path)
    except OSError as exc:
        Path(tmp).unlink(missing_ok=True)
        raise PersistenceException("Could not write SEO knowledge file.") from exc


def validate_execution_id(value: str) -> str:
    if not value or len(value) > 80 or not set(value) <= EXECUTION_ID_CHARS:
        raise ResourceNotFoundException("Execution not found.")
    return value


class SeoKnowledgeRepository:
    """Reads and writes one knowledge root; every path is derived from validated ids."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    # -- paths -----------------------------------------------------------------

    def config_path(self, site_id: str) -> Path:
        return self.root / "config" / "sites" / f"{validate_site_id(site_id)}.json"

    def site_dir(self, site_id: str) -> Path:
        return self.root / "sites" / validate_site_id(site_id)

    def _read(self, path: Path, adapter: TypeAdapter[T], default: T) -> T:
        if not path.exists():
            return default
        try:
            return adapter.validate_json(path.read_bytes())
        except ValidationError as exc:
            raise ConfigurationException(
                f"Knowledge file {path.relative_to(self.root).as_posix()} is invalid "
                f"({exc.error_count()} validation errors)."
            ) from exc
        except OSError as exc:
            raise PersistenceException("Could not read SEO knowledge file.") from exc

    # -- website configuration -------------------------------------------------

    def list_site_ids(self) -> list[str]:
        folder = self.root / "config" / "sites"
        if not folder.is_dir():
            return []
        return sorted(p.stem for p in folder.glob("*.json"))

    def load_config(self, site_id: str) -> WebsiteConfig:
        path = self.config_path(site_id)
        if not path.exists():
            raise ResourceNotFoundException(f"Website '{site_id}' is not configured.")
        config = self._read(path, TypeAdapter(WebsiteConfig), None)
        assert config is not None
        if config.id != site_id:
            raise ConfigurationException(
                f"Website config {path.name} declares id '{config.id}'."
            )
        return config

    def save_config(self, config: WebsiteConfig) -> None:
        write_atomic(self.config_path(config.id), _dump(config))

    # -- profile -----------------------------------------------------------------

    def load_profile(self, site_id: str) -> dict[str, Any]:
        path = self.site_dir(site_id) / "website-profile.json"
        return self._read(path, TypeAdapter(dict[str, Any]), {})

    def save_profile(self, site_id: str, profile: dict[str, Any]) -> None:
        write_atomic(self.site_dir(site_id) / "website-profile.json", _dump(profile))

    # -- findings ledger ---------------------------------------------------------

    def load_findings(self, site_id: str) -> list[Finding]:
        path = self.site_dir(site_id) / "findings.json"
        return self._read(path, TypeAdapter(list[Finding]), [])

    def save_findings(self, site_id: str, findings: list[Finding]) -> None:
        ordered = sorted(findings, key=lambda f: f.key)
        write_atomic(self.site_dir(site_id) / "findings.json", _dump(ordered))

    # -- decisions, changes, metrics --------------------------------------------

    def load_rejected(self, site_id: str) -> list[RejectedRecommendation]:
        path = self.site_dir(site_id) / "rejected-recommendations.json"
        return self._read(path, TypeAdapter(list[RejectedRecommendation]), [])

    def save_rejected(self, site_id: str, items: list[RejectedRecommendation]) -> None:
        path = self.site_dir(site_id) / "rejected-recommendations.json"
        write_atomic(path, _dump(items))

    def load_changes(self, site_id: str) -> list[ChangeRecord]:
        path = self.site_dir(site_id) / "implemented-changes.json"
        return self._read(path, TypeAdapter(list[ChangeRecord]), [])

    def append_changes(self, site_id: str, changes: list[ChangeRecord]) -> None:
        if not changes:
            return
        existing = self.load_changes(site_id)
        path = self.site_dir(site_id) / "implemented-changes.json"
        write_atomic(path, _dump(existing + changes))

    def load_metrics(self, site_id: str) -> list[MetricObservation]:
        path = self.site_dir(site_id) / "metrics" / "performance-history.json"
        return self._read(path, TypeAdapter(list[MetricObservation]), [])

    def append_metrics(self, site_id: str, metrics: list[MetricObservation]) -> None:
        if not metrics:
            return
        path = self.site_dir(site_id) / "metrics" / "performance-history.json"
        write_atomic(path, _dump(self.load_metrics(site_id) + metrics))

    # -- research snapshots ------------------------------------------------------

    def save_research(self, site_id: str, record: ResearchRecord) -> Path:
        validate_execution_id(record.id)
        path = self.site_dir(site_id) / "research" / record.kind / f"{record.id}.json"
        write_atomic(path, _dump(record))
        return path

    def list_research(
        self, site_id: str, kind: str | None = None
    ) -> list[ResearchRecord]:
        base = self.site_dir(site_id) / "research"
        pattern = f"{kind}/*.json" if kind else "*/*.json"
        adapter = TypeAdapter(ResearchRecord)
        records = [
            r
            for p in sorted(base.glob(pattern))
            if (r := self._read(p, adapter, None)) is not None
        ]
        return sorted(records, key=lambda r: r.created_at)

    # -- executions --------------------------------------------------------------

    def execution_path(self, site_id: str, execution_id: str) -> Path:
        validate_execution_id(execution_id)
        return self.site_dir(site_id) / "executions" / f"{execution_id}.json"

    def save_execution(self, execution: Execution) -> None:
        write_atomic(
            self.execution_path(execution.site_id, execution.id), _dump(execution)
        )

    def load_execution(self, site_id: str, execution_id: str) -> Execution:
        path = self.execution_path(site_id, execution_id)
        if not path.exists():
            raise ResourceNotFoundException("Execution not found.")
        execution = self._read(path, TypeAdapter(Execution), None)
        assert execution is not None
        return execution

    def load_index(self, site_id: str) -> list[ExecutionIndexEntry]:
        path = self.site_dir(site_id) / "executions" / "execution-history.json"
        return self._read(path, TypeAdapter(list[ExecutionIndexEntry]), [])

    def upsert_index(self, site_id: str, entry: ExecutionIndexEntry) -> None:
        entries = [e for e in self.load_index(site_id) if e.id != entry.id]
        entries.append(entry)
        entries.sort(key=lambda e: e.started_at)
        path = self.site_dir(site_id) / "executions" / "execution-history.json"
        write_atomic(path, _dump(entries))

    # -- reports -----------------------------------------------------------------

    def save_report(self, site_id: str, execution_id: str, markdown: str) -> Path:
        validate_execution_id(execution_id)
        reports = self.site_dir(site_id) / "reports"
        path = reports / f"{execution_id}.md"
        write_atomic(path, markdown)
        write_atomic(reports / "latest-report.md", markdown)
        return path

    def load_report(self, site_id: str, execution_id: str) -> str:
        validate_execution_id(execution_id)
        path = self.site_dir(site_id) / "reports" / f"{execution_id}.md"
        if not path.exists():
            raise ResourceNotFoundException("Report not found.")
        return path.read_text(encoding="utf-8")

    # -- generic per-site lists (monitoring state) -------------------------------

    def load_list(self, site_id: str, relative: str, model: type[T]) -> list[T]:
        """Load ``sites/<site>/<relative>`` as a list of ``model`` (fixed names only)."""
        path = self.site_dir(site_id) / relative
        return self._read(path, TypeAdapter(list[model]), [])  # type: ignore[valid-type]

    def save_list(self, site_id: str, relative: str, items: list[Any]) -> None:
        write_atomic(self.site_dir(site_id) / relative, _dump(items))
