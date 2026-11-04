"""Package the SEO Intelligence agents and their skills as a Claude Code plugin.

Builds ``data/plugins/seo-intelligence/`` (build output, never committed) with
``.claude-plugin/plugin.json``, ``.claude-plugin/marketplace.json``, the agents
from ``.claude/agents/`` and every skill the SEO Intelligence registry uses
(copied as text, with each third-party skill's licence and attribution files).

Install it in Claude Code on any machine:

    /plugin marketplace add <path or git URL of the built folder>
    /plugin install seo-intelligence@aurora-seo

The agents run AURORA's scripts, so the machine also needs an AURORA checkout
(set its path in the agent files' "AURORA lives at" line) and its own
credentials: Search Console sign-in, SERPAPI_API_KEY, optional DataForSEO.
No secret, .env value or knowledge-base data is ever copied.

    python scripts/build_seo_plugin.py [--out DIR] [--version 1.0.0]
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / ".claude" / "skills"
AGENTS = ROOT / ".claude" / "agents"
REGISTRY = ROOT / "seo-intelligence" / "skills" / "skill-registry.json"
COPY_SUFFIXES = {".md", ".txt", ".json", ".yml", ".yaml", ".csv"}
NEVER = {".env", ".env.local", "credentials.json", "token.json"}
DESCRIPTION = (
    "AURORA SEO Intelligence: one agent for full SEO audits, free site crawls with source-code "
    "locations, quick wins and content decay from Search Console, per-market rank tracking, "
    "keyword demand, AI-search visibility, YMYL accuracy, video SEO, competitor change watch, "
    "weekly alerts and a 14-day monitor, with a finding ledger, history and approval gates."
)


def registry_skills() -> list[str]:
    data = json.loads(REGISTRY.read_text(encoding="utf-8"))
    names = {s["name"] for s in data["skills"]}
    for mode in data["modes"].values():
        names |= {step["skill"] for step in mode["steps"]}
    return sorted(n for n in names if (SKILLS / n / "SKILL.md").is_file())


def copy_skill(name: str, target: Path) -> int:
    count = 0
    for path in (SKILLS / name).rglob("*"):
        if (
            not path.is_file()
            or path.name in NEVER
            or path.suffix.lower() not in COPY_SUFFIXES
        ):
            continue
        dest = target / "skills" / name / path.relative_to(SKILLS / name)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)
        count += 1
    for extra in ("LICENSE", "LICENSE.txt", "ATTRIBUTION.md"):
        src = SKILLS / name / extra
        if src.is_file():
            dest = target / "skills" / name / extra
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
    return count


def build(out: Path, version: str) -> dict[str, object]:
    if out.exists():
        shutil.rmtree(out)
    (out / ".claude-plugin").mkdir(parents=True)
    author = {"name": "AURORA"}
    plugin = {
        "name": "seo-intelligence",
        "description": DESCRIPTION,
        "version": version,
        "author": author,
        "license": "UNLICENSED (private); third-party skills keep their own licences",
        "keywords": [
            "seo",
            "search-console",
            "rank-tracking",
            "ai-search",
            "geo",
            "ymyl",
            "audit",
        ],
    }
    marketplace = {
        "name": "aurora-seo",
        "owner": author,
        "metadata": {
            "description": "AURORA's private SEO Intelligence plugin.",
            "version": version,
        },
        "plugins": [
            {
                "name": "seo-intelligence",
                "source": "./",
                "description": DESCRIPTION,
                "version": version,
                "author": author,
                "category": "seo",
                "tags": plugin["keywords"],
            }
        ],
    }
    (out / ".claude-plugin" / "plugin.json").write_text(
        json.dumps(plugin, indent=2) + "\n", encoding="utf-8"
    )
    (out / ".claude-plugin" / "marketplace.json").write_text(
        json.dumps(marketplace, indent=2) + "\n", encoding="utf-8"
    )
    (out / "agents").mkdir()
    agents = []
    for agent in sorted(AGENTS.glob("*.md")):
        shutil.copy2(agent, out / "agents" / agent.name)
        agents.append(agent.stem)
    skills = registry_skills()
    files = sum(copy_skill(name, out) for name in skills)
    (out / "README.md").write_text(
        "# AURORA SEO Intelligence plugin\n\n"
        f"{DESCRIPTION}\n\n"
        "## Install\n\n"
        "```\n/plugin marketplace add <path or git URL of this folder>\n"
        "/plugin install seo-intelligence@aurora-seo\n```\n\n"
        "## Requirements\n\n"
        '- An AURORA checkout; the agents name its folder in their "AURORA lives at" line.\n'
        "- Your own credentials in AURORA's `.env` / Search Console sign-in (nothing is bundled).\n"
        '- Optional: `pip install -e ".[video]"` for playlist catalogs.\n\n'
        f"## Contents\n\nAgents: {', '.join(agents)}.\n\nSkills ({len(skills)}): {', '.join(skills)}.\n",
        encoding="utf-8",
    )
    return {"out": str(out), "agents": agents, "skills": len(skills), "files": files}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--out", type=Path, default=ROOT / "data" / "plugins" / "seo-intelligence"
    )
    parser.add_argument("--version", default="1.0.0")
    args = parser.parse_args(argv)
    print(json.dumps(build(args.out, args.version), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
