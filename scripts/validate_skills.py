"""Validate the Claude Code skills installed under ``.claude/skills/``.

Skills are guidance text, not code. This script only *reads* them: it never
imports, executes or follows anything inside a skill folder. Checks:

- ``SKILL.md`` exists and has YAML frontmatter (parsed with ``yaml.safe_load``);
  ``name`` matches the directory and the Agent Skills naming rules; ``description``
  is present, bounded and free of XML tags.
- Provenance: external skills have ``ATTRIBUTION.md`` with a source repository
  and a 40-character commit hash, plus a license file or a declared license.
- Safety: no executables, symlinks or oversized files; relative Markdown links
  stay inside the skill and resolve; references to omitted scripts, MCP servers,
  runtime URL fetches and install commands are reported.
- AURORA rules: skills that must carry an "AURORA installation note" have one;
  ``neo4j-cypher`` keeps the Module 5 graph-safety rules.
- Tool dependencies a skill needs (for example Playwright) are present.
- ``docs/skills-catalog.md`` mentions every skill, and its module/workflow
  mapping section covers every skill.

Usage (repository root)::

    python scripts/validate_skills.py              # offline checks
    python scripts/validate_skills.py --json       # machine-readable report
    python scripts/validate_skills.py --verify-remote   # check commits on GitHub

Exit code 1 when any error is found; warnings and info never fail the run.
"""

from __future__ import annotations

import argparse
import importlib.util
import itertools
import json
import re
import shutil
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SKILLS_DIR = ROOT / ".claude" / "skills"
CATALOG = ROOT / "docs" / "skills-catalog.md"
FRONTEND = ROOT / "frontend"
MAPPING_HEADING = "## Module and workflow mapping"

MAX_FILE_BYTES = 256 * 1024
MAX_SKILL_LINES = 500  # skill-creator guidance for SKILL.md bodies
NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
COMMIT_RE = re.compile(r"\b[0-9a-f]{40}\b")
REPO_RE = re.compile(r"https://github\.com/([\w.-]+)/([\w.-]+?)(?:\.git)?(?=[/\s)`]|$)")
LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
SCRIPT_REF_RE = re.compile(r"\bscripts/[\w./-]+\.(?:py|mjs|js|sh|ts)\b")
EXECUTABLE_SUFFIXES = {
    ".py",
    ".pyc",
    ".sh",
    ".bash",
    ".ps1",
    ".bat",
    ".cmd",
    ".exe",
    ".dll",
    ".so",
    ".js",
    ".mjs",
    ".cjs",
    ".ts",
    ".jar",
    ".bin",
}
KNOWN_FRONTMATTER = {
    "name",
    "description",
    "license",
    "allowed-tools",
    "metadata",
    "compatibility",
    "version",
    "user-invocable",
    "argument-hint",
    "disable-model-invocation",
    "model",
    "inputs",
    "references",
    "last_verified",
    "author",
}

# Provenance sets. Local skills are authored in this repository; claude-seo skills
# were installed as text from https://github.com/AgriciDaniel/claude-seo and their
# provenance is recorded in docs/skills-catalog.md rather than per-skill files.
AURORA_PLANNING = {
    "web-research",
    "serp-analysis",
    "fact-verification",
    "firecrawl-extraction",
    "graphrag-research",
    "knowledge-graph",
    "seo-keyword-research",
    "seo-content-strategy",
    "seo-blog-writer",
    "seo-quality-audit",
    "wordpress-publishing",
}
LOCAL_REFERENCE = {"langgraph-orchestration", "serpapi-search-research"}
CLAUDE_SEO = {
    "seo",
    "seo-agentic",
    "seo-audit",
    "seo-backlinks",
    "seo-cluster",
    "seo-competitor-pages",
    "seo-content",
    "seo-content-brief",
    "seo-dataforseo",
    "seo-drift",
    "seo-ecommerce",
    "seo-flow",
    "seo-geo",
    "seo-google",
    "seo-hreflang",
    "seo-image-gen",
    "seo-images",
    "seo-local",
    "seo-maps",
    "seo-page",
    "seo-plan",
    "seo-programmatic",
    "seo-schema",
    "seo-sitemap",
    "seo-sxo",
    "seo-technical",
}

# Skills whose SKILL.md must start with an AURORA installation note, and phrases
# the note must contain (case-insensitive).
REQUIRED_NOTES: dict[str, tuple[str, ...]] = {
    "neo4j-cypher": (
        "graph_schema.Label",
        "parameters",
        "GraphSyncService",
        "user-supplied Cypher",
        "assertion=inferred",
        "non-destructive",
    ),
    "fastapi-patterns": ("AuroraException", "import"),
    "webapp-testing": ("not installed", "local"),
}
NOTE_PREFIXES = ("seo-toolkit-", "firecrawl-build")
NOTE_MARKER = "aurora installation note"

# Topics in the neo4j-cypher body that conflict with AURORA's graph rules; the
# AURORA note should address each one the body mentions.
NEO4J_CONFLICTS: dict[str, tuple[str, str]] = {
    "apoc": ("apoc", "body recommends APOC; AURORA avoids APOC"),
    "cypher 25": (
        "neo4j 5",
        "body defaults to CYPHER 25 (Neo4j 2025.x+); AURORA runs neo4j:5-community",
    ),
    "detach delete": (
        "delete",
        "body shows DETACH DELETE; AURORA graph writes are MERGE-only",
    ),
    "load csv": (
        "load csv",
        "body shows LOAD CSV bulk writes; only GraphSyncService writes",
    ),
    "$($label)": (
        "dynamic label",
        "body shows dynamic labels; AURORA labels come from enums",
    ),
}

# What a skill needs from the environment before its workflow can run.
REQUIREMENTS: dict[str, list[tuple[str, str]]] = {
    "webapp-testing": [("python", "playwright"), ("npm", "@playwright/test")],
    "seo-dataforseo": [("mcp", "dataforseo")],
    "seo-image-gen": [("mcp", "nanobanana")],
    "seo-gsc-diagnosis": [("mcp", "gsc")],
    "seo-google": [("credentials", "Google API / OAuth credentials")],
    "web-design-guidelines": [
        ("network", "raw.githubusercontent.com guidelines fetch")
    ],
}

RISK_PATTERNS: dict[str, re.Pattern[str]] = {
    "install_command": re.compile(
        r"\b(?:pip install|npm install|npx |playwright install|brew install)", re.I
    ),
    "env_write": re.compile(r"(?:>>?\s*\.env\b|write[^\n]{0,30}\.env\b)", re.I),
    "external_write": re.compile(
        r"\b(?:indexnow|indexing api|submit (?:the )?sitemap|disavow file"
        r"|publish(?:es|ing)? (?:to|the) )",
        re.I,
    ),
    "runtime_fetch": re.compile(
        r"\b(?:use webfetch|fetch (?:fresh|the latest) [\w ]{0,30}(?:from|before))",
        re.I,
    ),
    "mcp_dependency": re.compile(r"\bmcp\b", re.I),
}
NEGATION_RE = re.compile(r"\b(?:do not|don't|never|must not|not allowed|forbid)", re.I)


@dataclass
class Issue:
    level: str  # error | warning | info
    code: str
    message: str


@dataclass
class SkillReport:
    name: str
    provenance: str
    files: int = 0
    license: str | None = None
    commit: str | None = None
    repository: str | None = None
    issues: list[Issue] = field(default_factory=list)

    def add(self, level: str, code: str, message: str) -> None:
        self.issues.append(Issue(level, code, message))

    @property
    def status(self) -> str:
        levels = {i.level for i in self.issues}
        return (
            "error" if "error" in levels else "warning" if "warning" in levels else "ok"
        )


def provenance_of(name: str, has_attribution: bool) -> str:
    if name in AURORA_PLANNING:
        return "aurora-planning"
    if name in LOCAL_REFERENCE:
        return "aurora-local-reference"
    if name in CLAUDE_SEO:
        return "claude-seo"
    return "external" if has_attribution else "unclassified"


def split_frontmatter(text: str) -> tuple[str | None, str]:
    text = text.replace("\r\n", "\n")
    if not text.startswith("---\n"):
        return None, text
    end = text.find("\n---", 4)
    if end == -1:
        return None, text
    return text[4:end], text[end + 4 :]


def check_frontmatter(report: SkillReport, raw: str | None) -> dict[str, object]:
    if raw is None:
        report.add("error", "frontmatter_missing", "SKILL.md has no YAML frontmatter")
        return {}
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        report.add(
            "error",
            "frontmatter_invalid",
            f"YAML does not parse: {exc.__class__.__name__}",
        )
        return {}
    if not isinstance(data, dict):
        report.add("error", "frontmatter_invalid", "frontmatter is not a mapping")
        return {}
    name = data.get("name")
    if not isinstance(name, str) or not name:
        report.add("error", "name_missing", "frontmatter `name` is missing")
    else:
        if name != report.name:
            report.add(
                "error", "name_mismatch", f"`name: {name}` does not match the directory"
            )
        if len(name) > 64 or not NAME_RE.match(name):
            report.add(
                "error",
                "name_format",
                "name must be <=64 chars of lowercase letters, digits and hyphens",
            )
        if "anthropic" in name or "claude" in name:
            report.add(
                "warning",
                "name_reserved",
                "name contains a reserved word (anthropic/claude)",
            )
    description = data.get("description")
    if not isinstance(description, str) or not description.strip():
        report.add(
            "error", "description_missing", "frontmatter `description` is missing"
        )
    else:
        if len(description) > 1024:
            report.add(
                "error",
                "description_length",
                f"description is {len(description)} chars (max 1024)",
            )
        if re.search(r"<[A-Za-z/][^>]*>", description):
            report.add(
                "warning", "description_xml", "description contains XML/HTML tags"
            )
    unknown = sorted(set(data) - KNOWN_FRONTMATTER)
    if unknown:
        report.add(
            "info",
            "frontmatter_unknown_keys",
            f"unrecognised keys: {', '.join(unknown)}",
        )
    lic = data.get("license")
    if isinstance(lic, str) and lic.strip():
        report.license = lic.strip()
    return data


def check_files(report: SkillReport, skill_dir: Path) -> list[Path]:
    texts: list[Path] = []
    for path in sorted(skill_dir.rglob("*")):
        if path.is_symlink():
            report.add(
                "error",
                "symlink",
                f"symlink not allowed: {path.relative_to(skill_dir)}",
            )
            continue
        if not path.is_file():
            continue
        report.files += 1
        rel = path.relative_to(skill_dir).as_posix()
        if path.suffix.lower() in EXECUTABLE_SUFFIXES:
            report.add("error", "executable", f"executable file present: {rel}")
            continue
        size = path.stat().st_size
        head = path.read_bytes()[:2]
        if head == b"#!":
            report.add("error", "executable", f"file has a shebang: {rel}")
        if size > MAX_FILE_BYTES:
            report.add(
                "warning",
                "large_file",
                f"{rel} is {size // 1024} KB (limit {MAX_FILE_BYTES // 1024} KB)",
            )
        if path.suffix.lower() in {".md", ".txt", ".json", ".yml", ".yaml"}:
            texts.append(path)
    return texts


def check_links(report: SkillReport, skill_dir: Path, texts: list[Path]) -> None:
    root = skill_dir.resolve()
    missing_siblings: set[str] = set()
    _scan_links(report, skill_dir, texts, root, missing_siblings)
    if missing_siblings:
        report.add(
            "warning",
            "sibling_skill_missing",
            "links to skills that are not installed: "
            + ", ".join(sorted(missing_siblings)),
        )


def _scan_links(
    report: SkillReport,
    skill_dir: Path,
    texts: list[Path],
    root: Path,
    missing_siblings: set[str],
) -> None:
    for path in texts:
        if path.suffix.lower() != ".md":
            continue
        body = path.read_text(encoding="utf-8", errors="replace")
        body = re.sub(r"```.*?```", "", body, flags=re.S)  # ignore code samples
        for target in LINK_RE.findall(body):
            if re.match(r"^[a-z][a-z0-9+.-]*:", target, re.I) or target.startswith("#"):
                continue
            target = target.split("#", 1)[0]
            if not target or ("/" not in target and "." not in target):
                continue  # anchors and placeholders such as "(url)"
            resolved = (path.parent / target).resolve()
            rel = path.relative_to(skill_dir).as_posix()
            skills_root = SKILLS_DIR.resolve()
            if not resolved.is_relative_to(root):
                if (
                    resolved.is_relative_to(skills_root)
                    and len(resolved.relative_to(skills_root).parts) >= 2
                ):
                    sibling = resolved.relative_to(skills_root).parts[0]
                    if not (skills_root / sibling / "SKILL.md").exists():
                        missing_siblings.add(sibling)
                else:
                    report.add(
                        "warning",
                        "link_escapes",
                        f"{rel} links outside the skills directory: {target}",
                    )
            elif not resolved.exists():
                report.add(
                    "warning", "link_broken", f"{rel} links to a missing file: {target}"
                )


def _risky_lines(path: Path, pattern: re.Pattern[str]) -> int:
    """Lines matching ``pattern`` that are not themselves a prohibition."""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return sum(1 for ln in lines if pattern.search(ln) and not NEGATION_RE.search(ln))


def check_content(report: SkillReport, skill_dir: Path, texts: list[Path]) -> None:
    joined = "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in texts)
    missing_scripts = sorted(
        {ref for ref in SCRIPT_REF_RE.findall(joined) if not (skill_dir / ref).exists()}
    )
    if missing_scripts:
        shown = ", ".join(missing_scripts[:4]) + (
            " ..." if len(missing_scripts) > 4 else ""
        )
        report.add(
            "warning",
            "omitted_scripts",
            f"text refers to scripts that are not installed: {shown}",
        )
    for code, pattern in RISK_PATTERNS.items():
        hits = sum(1 for p in texts if _risky_lines(p, pattern))
        if hits:
            level = "warning" if code in {"env_write", "runtime_fetch"} else "info"
            report.add(
                level,
                f"mentions_{code}",
                f"{hits} file(s) mention {code.replace('_', ' ')} "
                "(guidance only; never executed)",
            )


def check_attribution(report: SkillReport, skill_dir: Path) -> None:
    attribution = skill_dir / "ATTRIBUTION.md"
    license_files = [
        p.name
        for p in skill_dir.iterdir()
        if p.is_file() and p.name.lower().startswith(("license", "licence"))
    ]
    if report.provenance in {"aurora-planning"}:
        return
    if not attribution.exists():
        if report.provenance == "claude-seo":
            report.add(
                "info",
                "attribution_catalog_only",
                "no ATTRIBUTION.md; provenance recorded in docs/skills-catalog.md "
                "(claude-seo set)",
            )
        elif report.provenance != "aurora-local-reference":
            report.add(
                "error", "attribution_missing", "external skill without ATTRIBUTION.md"
            )
    else:
        text = attribution.read_text(encoding="utf-8", errors="replace")
        repo = REPO_RE.search(text)
        report.repository = f"{repo.group(1)}/{repo.group(2)}" if repo else None
        commit = COMMIT_RE.search(text)
        report.commit = commit.group(0) if commit else None
        if report.provenance == "external":
            if not report.repository:
                report.add(
                    "error",
                    "attribution_no_repo",
                    "ATTRIBUTION.md names no GitHub source repository",
                )
            if not report.commit:
                report.add(
                    "error",
                    "attribution_no_commit",
                    "ATTRIBUTION.md has no 40-character commit hash",
                )
        if not report.license:
            lic = re.search(r"\*\*License:\*\*\s*([^\n(,;]+)", text)
            if lic:
                report.license = lic.group(1).strip()
    if license_files:
        report.license = report.license or license_files[0]
    elif report.provenance in {"external", "claude-seo"}:
        level = "info" if report.license else "error"
        report.add(
            level,
            "license_file_missing",
            "no license file; license is declared only in text"
            if report.license
            else "no license file and no declared license",
        )


def check_notes(report: SkillReport, body: str, full_text: str) -> None:
    needs_note = report.name in REQUIRED_NOTES or report.name.startswith(NOTE_PREFIXES)
    if not needs_note:
        return
    lowered = body.lower()
    start = lowered.find(NOTE_MARKER)
    if start == -1:
        report.add(
            "error",
            "aurora_note_missing",
            "required AURORA installation note is missing",
        )
        return
    # The note is the block-quoted paragraph that contains the marker.
    lines = body[start:].split("\n")
    note_lines = [
        lines[0],
        *itertools.takewhile(lambda ln: ln.startswith(">"), lines[1:]),
    ]
    note = "\n".join(note_lines).lower()
    for phrase in REQUIRED_NOTES.get(report.name, ()):
        if phrase.lower() not in note:
            report.add(
                "error",
                "aurora_rule_missing",
                f"AURORA note lacks required rule: '{phrase}'",
            )
    if report.name == "neo4j-cypher":
        text = full_text.lower()
        for trigger, (covered_by, message) in NEO4J_CONFLICTS.items():
            if trigger in text and covered_by not in note:
                report.add(
                    "warning", "aurora_rule_gap", f"note does not address: {message}"
                )


def check_requirements(report: SkillReport) -> None:
    for kind, what in REQUIREMENTS.get(report.name, []):
        if kind == "python":
            if importlib.util.find_spec(what) is None:
                report.add(
                    "warning",
                    "dependency_missing",
                    f"Python package '{what}' is not installed",
                )
        elif kind == "npm":
            if not (FRONTEND / "node_modules" / Path(*what.split("/"))).exists():
                report.add(
                    "warning",
                    "dependency_missing",
                    f"npm package '{what}' is not installed in frontend/",
                )
        elif kind == "mcp":
            report.add(
                "warning",
                "dependency_external",
                f"needs the '{what}' MCP server, which AURORA does not configure",
            )
        else:
            report.add("info", "dependency_external", f"needs {kind}: {what}")


def catalog_checks(reports: dict[str, SkillReport]) -> list[Issue]:
    issues: list[Issue] = []
    if not CATALOG.exists():
        return [
            Issue("error", "catalog_missing", f"{CATALOG.relative_to(ROOT)} not found")
        ]
    text = CATALOG.read_text(encoding="utf-8")
    mentioned = set(re.findall(r"`([a-z0-9-]+)`", text))
    for name, report in reports.items():
        if name not in mentioned:
            report.add(
                "warning",
                "catalog_missing_skill",
                "not mentioned in docs/skills-catalog.md",
            )
    if MAPPING_HEADING not in text:
        issues.append(
            Issue(
                "error",
                "catalog_no_mapping",
                f"catalog has no '{MAPPING_HEADING}' section",
            )
        )
    else:
        section = text.split(MAPPING_HEADING, 1)[1].split("\n## ", 1)[0]
        mapped = set(re.findall(r"`([a-z0-9-]+)`", section))
        for name, report in reports.items():
            if name not in mapped:
                report.add(
                    "warning",
                    "mapping_missing",
                    "no row in the catalog's module/workflow mapping",
                )
    return issues


def _fetch_commit_status(repository: str, commit: str, timeout: float) -> Issue:
    """One GitHub API lookup; the result is cached per (repository, commit)."""
    url = f"https://api.github.com/repos/{repository}/commits/{commit}"
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "aurora-skill-validator",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout):  # noqa: S310 - fixed https host
            return Issue(
                "info",
                "commit_verified",
                f"commit {commit[:10]} exists in {repository}",
            )
    except urllib.error.HTTPError as exc:
        if exc.code in (404, 422):
            return Issue(
                "error",
                "commit_not_found",
                f"commit {commit[:10]} not found in {repository} (HTTP {exc.code})",
            )
        reason = " (rate limited; retry later)" if exc.code in (403, 429) else ""
        return Issue(
            "warning",
            "commit_unverified",
            f"GitHub returned HTTP {exc.code}{reason}; commit not verified",
        )
    except (urllib.error.URLError, TimeoutError) as exc:
        return Issue(
            "warning",
            "commit_unverified",
            f"could not reach GitHub: {exc.__class__.__name__}",
        )


_REMOTE_CACHE: dict[tuple[str, str], Issue] = {}


def verify_remote(report: SkillReport, timeout: float = 15.0) -> None:
    if not (report.repository and report.commit):
        return
    key = (report.repository, report.commit)
    if key not in _REMOTE_CACHE:
        _REMOTE_CACHE[key] = _fetch_commit_status(*key, timeout)
    report.issues.append(_REMOTE_CACHE[key])


def validate(remote: bool) -> tuple[dict[str, SkillReport], list[Issue]]:
    reports: dict[str, SkillReport] = {}
    global_issues: list[Issue] = []
    if not SKILLS_DIR.is_dir():
        return reports, [
            Issue("error", "skills_dir_missing", f"{SKILLS_DIR} not found")
        ]
    for skill_dir in sorted(p for p in SKILLS_DIR.iterdir() if p.is_dir()):
        name = skill_dir.name
        report = SkillReport(
            name, provenance_of(name, (skill_dir / "ATTRIBUTION.md").exists())
        )
        reports[name] = report
        if report.provenance == "unclassified":
            report.add(
                "warning",
                "unclassified",
                "skill is not in a known provenance set and has no ATTRIBUTION.md",
            )
        if skill_dir.is_symlink():
            report.add("error", "symlink", "skill directory is a symlink")
            continue
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.is_file():
            report.add("error", "skill_md_missing", "SKILL.md not found")
            continue
        text = skill_md.read_text(encoding="utf-8", errors="replace")
        raw, body = split_frontmatter(text)
        check_frontmatter(report, raw)
        if body.count("\n") > MAX_SKILL_LINES:
            report.add(
                "info",
                "skill_md_long",
                f"SKILL.md body has {body.count(chr(10))} lines "
                f"(guidance: <= {MAX_SKILL_LINES})",
            )
        texts = check_files(report, skill_dir)
        check_links(report, skill_dir, texts)
        check_content(report, skill_dir, texts)
        check_attribution(report, skill_dir)
        full_text = "\n".join(
            p.read_text(encoding="utf-8", errors="replace") for p in texts
        )
        check_notes(report, body, full_text)
        check_requirements(report)
        if remote:
            verify_remote(report)
    global_issues.extend(catalog_checks(reports))
    names = [r.name for r in reports.values()]
    if len(names) != len(set(names)):
        global_issues.append(Issue("error", "duplicate_names", "duplicate skill names"))
    if shutil.which("node") is None:
        global_issues.append(Issue("info", "node_missing", "node is not on PATH"))
    return reports, global_issues


def print_text(
    reports: dict[str, SkillReport], global_issues: list[Issue], verbose: bool
) -> None:
    counts = {"ok": 0, "warning": 0, "error": 0}
    for report in reports.values():
        counts[report.status] += 1
    print(
        f"Skills: {len(reports)}  ok: {counts['ok']}  "
        f"with warnings: {counts['warning']}  with errors: {counts['error']}\n"
    )
    print(f"{'skill':34} {'status':8} {'provenance':24} {'license':14} files")
    for report in reports.values():
        lic = (report.license or "-")[:14]
        print(
            f"{report.name:34} {report.status:8} {report.provenance:24} "
            f"{lic:14} {report.files}"
        )
    print()
    for report in reports.values():
        shown = [i for i in report.issues if verbose or i.level != "info"]
        if shown:
            print(f"[{report.name}]")
            for issue in shown:
                print(f"  {issue.level:7} {issue.code}: {issue.message}")
    for issue in global_issues:
        print(f"[catalog] {issue.level:7} {issue.code}: {issue.message}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--json", action="store_true", help="print a JSON report")
    parser.add_argument(
        "--verbose", action="store_true", help="include info-level findings"
    )
    parser.add_argument(
        "--verify-remote",
        action="store_true",
        help="check ATTRIBUTION commits on GitHub (network)",
    )
    args = parser.parse_args()
    reports, global_issues = validate(args.verify_remote)
    if args.json:
        payload = {
            "skills": [
                {
                    "name": r.name,
                    "status": r.status,
                    "provenance": r.provenance,
                    "license": r.license,
                    "repository": r.repository,
                    "commit": r.commit,
                    "files": r.files,
                    "issues": [vars(i) for i in r.issues],
                }
                for r in reports.values()
            ],
            "catalog": [vars(i) for i in global_issues],
        }
        print(json.dumps(payload, indent=2))
    else:
        print_text(reports, global_issues, args.verbose)
    failed = any(r.status == "error" for r in reports.values()) or any(
        i.level == "error" for i in global_issues
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
