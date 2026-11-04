"""Where in the website's source a page's title, description or slug is defined.

Read-only: lists tracked files with ``git ls-files`` and searches their text for
the exact strings observed on the live page (title, meta description, URL slug),
then adds ``file:line`` evidence to the matching findings so a fix can point to
the line to change. It also reports how the site sets metadata (framework and
head-management library), read from ``package.json`` and source patterns.
"""

from __future__ import annotations

import html as html_lib
import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from app.seo.intelligence.insights.common import evidence, page_path
from app.seo.intelligence.models import Finding

TEXT_SUFFIXES = {
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".json",
    ".md",
    ".mdx",
    ".html",
    ".htm",
    ".yml",
    ".yaml",
    ".astro",
    ".vue",
    ".svelte",
    ".php",
    ".py",
    ".txt",
    ".xml",
}
CODE_SUFFIXES = {
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".mjs",
    ".astro",
    ".vue",
    ".svelte",
    ".html",
    ".php",
}
SKIP_PARTS = {"node_modules", "dist", "build", ".next", "coverage", "public/assets"}
MAX_FILE_BYTES = 1_500_000
MAX_FILES = 6000
HEAD_PATTERNS = {
    "react-helmet-async / react-helmet": ("<Helmet", "react-helmet"),
    "Next.js metadata API": ("export const metadata", "generateMetadata"),
    "document.title at runtime": ("document.title",),
    "templated <title> in components": ("<title>{",),
}


@dataclass
class SourceIndex:
    root: Path
    files: dict[str, list[str]] = field(default_factory=dict)  # relative path -> lines
    framework: list[str] = field(default_factory=list)


def build_index(repo: Path) -> SourceIndex:
    listed = subprocess.run(
        ["git", "-C", str(repo), "ls-files"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=True,
    ).stdout.splitlines()
    index = SourceIndex(root=repo)
    for rel in listed[: MAX_FILES * 3]:
        if len(index.files) >= MAX_FILES:
            break
        path = repo / rel
        if path.suffix.lower() not in TEXT_SUFFIXES or any(
            part in rel for part in SKIP_PARTS
        ):
            continue
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                continue
            index.files[rel] = path.read_text(
                encoding="utf-8", errors="replace"
            ).splitlines()
        except OSError:
            continue
    index.framework = detect_framework(index)
    return index


def detect_framework(index: SourceIndex) -> list[str]:
    notes = []
    for rel, lines in index.files.items():
        if rel.endswith("package.json") and "node_modules" not in rel:
            try:
                data = json.loads("\n".join(lines))
            except ValueError:
                continue
            deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
            for name in (
                "next",
                "react",
                "vite",
                "astro",
                "gatsby",
                "nuxt",
                "react-helmet-async",
                "react-helmet",
                "react-snap",
                "vite-plugin-ssr",
                "vite-ssg",
            ):
                if name in deps:
                    notes.append(f"{name} {deps[name]} ({rel})")
    code = {
        rel: lines
        for rel, lines in index.files.items()
        if Path(rel).suffix.lower() in CODE_SUFFIXES
    }
    for label, needles in HEAD_PATTERNS.items():
        hit = next(
            (
                rel
                for rel, lines in code.items()
                if any(n in ln for ln in lines for n in needles)
            ),
            None,
        )
        if hit:
            notes.append(f"{label}: e.g. {hit}")
    return notes


def locate(index: SourceIndex, text: str, limit: int = 3) -> list[tuple[str, int, str]]:
    """``(file, line number, line excerpt)`` for exact matches of ``text`` (and its
    HTML-escaped form); case-insensitive only when nothing matches exactly."""
    text = text.strip()
    if len(text) < 8:
        return []
    variants = {text, html_lib.escape(text, quote=False), text.replace("'", "\\'")}
    hits: list[tuple[str, int, str]] = []
    for insensitive in (False, True):
        for rel, lines in index.files.items():
            for i, line in enumerate(lines, 1):
                hay = line.lower() if insensitive else line
                if any((v.lower() if insensitive else v) in hay for v in variants):
                    hits.append((rel, i, line.strip()[:160]))
                    if len(hits) >= limit:
                        return hits
        if hits:
            return hits
    return hits


def page_strings(
    signals_by_url: dict[str, tuple[str | None, str | None]],
) -> dict[str, dict[str, str]]:
    """URL -> {'title': ..., 'description': ..., 'slug': ...} from crawl signals."""
    out = {}
    for url, (title, desc) in signals_by_url.items():
        entry = {}
        if title:
            entry["title"] = title
        if desc:
            entry["description"] = desc
        slug = page_path(url).rsplit("/", 1)[-1]
        if len(slug) >= 8:
            entry["slug"] = slug
        out[url] = entry
    return out


def attach(
    findings: list[Finding],
    index: SourceIndex,
    strings: dict[str, dict[str, str]],
    *,
    per_finding: int = 8,
) -> int:
    """Add repository locations as evidence to findings that name affected URLs.
    Returns how many locations were attached."""
    by_path = {page_path(u): v for u, v in strings.items()}
    attached = 0
    for f in findings:
        notes = []
        for url in f.affected_urls[:per_finding]:
            values = by_path.get(page_path(url))
            if not values:
                continue
            for label in ("title", "description", "slug"):
                if label not in values:
                    continue
                hits = locate(index, values[label], limit=1)
                if hits:
                    rel, line, _ = hits[0]
                    notes.append(f"{page_path(url)} {label}: {rel}:{line}")
                    break
        if notes:
            f.evidence.append(
                evidence("Website repository (read-only text search)", "; ".join(notes))
            )
            attached += len(notes)
    return attached
