"""Where insight results live.

Derived, small results go to the knowledge base
(``seo-intelligence/sites/<site>/insights/``): a dated snapshot per run plus a
``-latest`` copy, rank history, budget ledgers, recorded AI answers and the
video catalog. Bulky collected data (competitor sitemap URL lists, CSV exports)
goes to ``data/seo-intelligence/<site>/``, which is never committed.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from app.seo.intelligence.knowledge import SeoKnowledgeRepository, write_atomic
from app.seo.intelligence.models import validate_site_id

KINDS = (
    "opportunities",
    "alerts",
    "rank-tracking",
    "ai-visibility",
    "site-crawl",
    "competitor-watch",
    "video-seo",
    "keyword-data",
)


class InsightStore:
    def __init__(self, repo: SeoKnowledgeRepository, data_root: Path) -> None:
        self.repo = repo
        self.data_root = Path(data_root)

    def dir(self, site_id: str) -> Path:
        return self.repo.site_dir(site_id) / "insights"

    def data_dir(self, site_id: str) -> Path:
        path = self.data_root / "seo-intelligence" / validate_site_id(site_id)
        path.mkdir(parents=True, exist_ok=True)
        return path

    @staticmethod
    def _dump(data: Any) -> str:
        return json.dumps(data, indent=2, ensure_ascii=False, default=str) + "\n"

    def save_snapshot(self, site_id: str, kind: str, day: date, data: Any) -> Path:
        if kind not in KINDS:
            raise ValueError(f"unknown insight kind '{kind}'")
        folder = self.dir(site_id)
        folder.mkdir(parents=True, exist_ok=True)
        text = self._dump({"kind": kind, "date": day.isoformat(), "data": data})
        path = folder / f"{kind}-{day.isoformat()}.json"
        write_atomic(path, text)
        write_atomic(folder / f"{kind}-latest.json", text)
        return path

    def latest(self, site_id: str, kind: str) -> dict[str, Any] | None:
        path = self.dir(site_id) / f"{kind}-latest.json"
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def read_json(
        self, site_id: str, name: str, default: Any, *, data: bool = False
    ) -> Any:
        base = self.data_dir(site_id) if data else self.dir(site_id)
        path = base / name
        if not path.is_file():
            return default
        return json.loads(path.read_text(encoding="utf-8"))

    def write_json(
        self, site_id: str, name: str, value: Any, *, data: bool = False
    ) -> Path:
        base = self.data_dir(site_id) if data else self.dir(site_id)
        base.mkdir(parents=True, exist_ok=True)
        path = base / name
        write_atomic(path, self._dump(value))
        return path
