"""AI-search visibility: crawler access, llms.txt, AI Overview citations, prompt panel.

- **Crawler access**: robots.txt evaluated for each known AI user agent, grouped
  by purpose. Blocking *search/answer* crawlers removes the site from AI answers;
  blocking *training* crawlers does not, so the two are judged separately and
  against the owner's training policy.
- **llms.txt**: present and well formed (it has no confirmed ranking effect; it
  is reported as low priority).
- **AI Overviews**: from SERP spot checks, whether an AI Overview appears and
  cites the site, and which domains it cites instead.
- **Prompt panel**: answers observed in ChatGPT, Perplexity, Gemini or AI Mode
  for the owner's prompts are recorded (engine, date, cited domains, brand
  mention) and turned into share of voice. AURORA does not call those engines;
  an answer is recorded only after someone (or the browser agent) observed it.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any

from app.seo.intelligence.insights.common import evidence, finding, slug
from app.seo.intelligence.insights.market_ranks import SerpCheck
from app.seo.intelligence.models import AiVisibilitySettings, Finding
from app.seo.technical.site_files import parse_robots

SKILL = "seo-ai-visibility"

# (user agent token, operator, purpose). Purpose: search = indexes for AI answers
# with citations; user = fetches a page when a user asks; training = model training.
AI_CRAWLERS: tuple[tuple[str, str, str], ...] = (
    ("OAI-SearchBot", "OpenAI", "search"),
    ("ChatGPT-User", "OpenAI", "user"),
    ("GPTBot", "OpenAI", "training"),
    ("Claude-SearchBot", "Anthropic", "search"),
    ("Claude-User", "Anthropic", "user"),
    ("ClaudeBot", "Anthropic", "training"),
    ("PerplexityBot", "Perplexity", "search"),
    ("Perplexity-User", "Perplexity", "user"),
    ("Googlebot", "Google (Search, AI Overviews, AI Mode)", "search"),
    ("Google-Extended", "Google (Gemini training/grounding opt-out)", "training"),
    ("Bingbot", "Microsoft (Bing, Copilot)", "search"),
    ("Applebot", "Apple", "search"),
    ("Applebot-Extended", "Apple", "training"),
    ("DuckAssistBot", "DuckDuckGo", "user"),
    ("MistralAI-User", "Mistral", "user"),
    ("meta-externalagent", "Meta", "training"),
    ("Amazonbot", "Amazon", "search"),
    ("CCBot", "Common Crawl", "training"),
    ("Bytespider", "ByteDance", "training"),
)


@dataclass
class CrawlerAccess:
    agent: str
    operator: str
    purpose: str
    allowed_paths: int
    blocked_paths: list[str] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return bool(self.blocked_paths)


def crawler_access(
    robots_txt: str | None, base_url: str, paths: list[str]
) -> list[CrawlerAccess]:
    """Evaluate robots.txt (RFC 9309: wildcards, longest match) for every AI agent
    on ``paths``. No robots.txt (None) means everything is allowed."""
    parser = parse_robots(robots_txt or "")
    out = []
    for agent, operator, purpose in AI_CRAWLERS:
        blocked = [
            p for p in paths if not parser.can_fetch(agent, base_url.rstrip("/") + p)
        ]
        out.append(
            CrawlerAccess(
                agent=agent,
                operator=operator,
                purpose=purpose,
                allowed_paths=len(paths) - len(blocked),
                blocked_paths=blocked,
            )
        )
    return out


def explicit_ai_agents(robots_txt: str | None) -> list[str]:
    """AI user agents that robots.txt names in its own group (an explicit policy)."""
    tokens = {
        agent.lower(): agent for agent, _, _ in AI_CRAWLERS if agent != "Googlebot"
    }
    named = {
        a.lower().strip()
        for g in parse_robots(robots_txt or "").groups
        for a in g.agents
    }
    return sorted(tokens[t] for t in named if t in tokens)


@dataclass
class LlmsTxt:
    status: int | None
    has_title: bool = False
    link_count: int = 0
    sections: int = 0

    @property
    def valid(self) -> bool:
        return self.status == 200 and self.has_title and self.link_count > 0


def check_llms_txt(status: int | None, text: str | None) -> LlmsTxt:
    if status != 200 or not text:
        return LlmsTxt(status=status)
    lines = text.splitlines()
    return LlmsTxt(
        status=status,
        has_title=any(ln.startswith("# ") for ln in lines[:5]),
        link_count=len(re.findall(r"\]\((https?://[^)]+)\)", text)),
        sections=sum(1 for ln in lines if ln.startswith("## ")),
    )


# --- Prompt panel -------------------------------------------------------------------------

ENGINES = ("chatgpt", "perplexity", "gemini", "ai-mode", "copilot", "claude")


@dataclass
class AiAnswer:
    prompt: str
    engine: str
    observed_on: str
    cited_domains: list[str]
    mentions_brand: bool
    excerpt: str = ""
    observed_by: str = ""

    def __post_init__(self) -> None:
        if self.engine not in ENGINES:
            raise ValueError(f"engine must be one of {', '.join(ENGINES)}")
        date.fromisoformat(self.observed_on)
        self.cited_domains = [
            d.lower().removeprefix("www.") for d in self.cited_domains
        ]
        self.excerpt = self.excerpt[:500]


def _is_site(domain: str, site_domain: str) -> bool:
    return domain == site_domain or domain.endswith("." + site_domain)


def share_of_voice(answers: list[AiAnswer], site_domain: str) -> dict[str, Any]:
    """Per engine: answers recorded, answers citing or mentioning the site, and the
    domains cited most often (latest answer per prompt and engine only)."""
    latest: dict[tuple[str, str], AiAnswer] = {}
    for a in sorted(answers, key=lambda a: a.observed_on):
        latest[(a.prompt, a.engine)] = a
    per_engine: dict[str, dict[str, Any]] = {}
    domain_counts: dict[str, int] = defaultdict(int)
    for a in latest.values():
        e = per_engine.setdefault(a.engine, {"answers": 0, "cited": 0, "mentioned": 0})
        e["answers"] += 1
        if any(_is_site(d, site_domain) for d in a.cited_domains):
            e["cited"] += 1
        if a.mentions_brand:
            e["mentioned"] += 1
        for d in set(a.cited_domains):
            domain_counts[d] += 1
    for e in per_engine.values():
        e["share_cited_pct"] = round(100 * e["cited"] / e["answers"], 1)
    return {
        "engines": per_engine,
        "top_cited_domains": sorted(domain_counts.items(), key=lambda kv: -kv[1])[:15],
        "answers_considered": len(latest),
    }


# --- Findings -----------------------------------------------------------------------------


def access_findings(
    access: list[CrawlerAccess],
    settings: AiVisibilitySettings,
    base_url: str,
    explicit: list[str] | None = None,
) -> list[Finding]:
    out = []
    if explicit is not None and not explicit:
        out.append(
            finding(
                key="robots-ai-crawlers:site",
                issue="robots.txt has no explicit policy for AI crawlers",
                category="technical/robots",
                priority="low",
                evidence_items=[
                    evidence(
                        "robots.txt (groups)",
                        "No group names an AI user agent; they fall back to '*'.",
                        base_url.rstrip("/") + "/robots.txt",
                    )
                ],
                seo_impact="AI crawlers follow the generic rules, so training and answer use are not a deliberate choice.",
                action="Decide per AI crawler (answer/search agents vs training agents) and write the policy in robots.txt.",
                outcome="robots.txt states an explicit AI policy.",
                verification="Re-run /seo-ai-visibility.",
                skill=SKILL,
            )
        )
    blocked_answers = [
        a for a in access if a.blocked and a.purpose in ("search", "user")
    ]
    if blocked_answers:
        out.append(
            finding(
                key="ai-answer-crawlers-blocked:site",
                issue="robots.txt blocks AI search/answer crawlers: "
                + ", ".join(a.agent for a in blocked_answers),
                category="ai-search/crawlers",
                priority="high",
                evidence_items=[
                    evidence(
                        "robots.txt (evaluated per user agent)",
                        "; ".join(
                            f"{a.agent} ({a.operator}) blocked on {', '.join(a.blocked_paths[:3])}"
                            for a in blocked_answers
                        ),
                        base_url.rstrip("/") + "/robots.txt",
                    )
                ],
                seo_impact="Those engines cannot fetch the pages, so they cannot cite them in answers.",
                action="Allow these user agents in robots.txt (owner decision); keep training "
                "crawlers on whatever policy the owner chose.",
                outcome="Answer engines can read and cite the site.",
                verification="Re-run /seo-ai-visibility; the agents show as allowed.",
                skill=SKILL,
            )
        )
    training = [a for a in access if a.purpose == "training"]
    if settings.allow_training_crawlers is not None:
        mismatched = [
            a for a in training if a.blocked == settings.allow_training_crawlers
        ]
        if mismatched:
            want = "allowed" if settings.allow_training_crawlers else "blocked"
            out.append(
                finding(
                    key="ai-training-crawler-policy:site",
                    issue=f"Training crawlers do not match the owner's policy ({want}): "
                    + ", ".join(a.agent for a in mismatched),
                    category="ai-search/crawlers",
                    priority="low",
                    evidence_items=[
                        evidence(
                            "robots.txt (evaluated per user agent)",
                            ", ".join(
                                f"{a.agent}: {'blocked' if a.blocked else 'allowed'}"
                                for a in mismatched
                            ),
                        )
                    ],
                    seo_impact="Content use for model training differs from the owner's choice.",
                    action=f"Update robots.txt so these agents are {want}.",
                    outcome="robots.txt matches the policy.",
                    verification="Re-run /seo-ai-visibility.",
                    skill=SKILL,
                )
            )
    return out


def llms_findings(result: LlmsTxt, base_url: str) -> list[Finding]:
    if result.valid:
        return []
    return [
        finding(
            key="llms-txt:site",
            issue="llms.txt is missing or not well formed"
            + (f" (HTTP {result.status})" if result.status else ""),
            category="ai-search/llms-txt",
            priority="low",
            evidence_items=[
                evidence(
                    "HTTP GET /llms.txt",
                    f"status {result.status}, title {result.has_title}, links {result.link_count}",
                    base_url.rstrip("/") + "/llms.txt",
                )
            ],
            seo_impact="No confirmed ranking or citation effect; a curated map can help AI tools find key pages.",
            action="Publish /llms.txt: an H1 with the site name, a one-line summary, and sections "
            "of links to key pages with short descriptions.",
            outcome="A valid llms.txt is served.",
            verification="Re-run /seo-ai-visibility.",
            skill=SKILL,
        )
    ]


def overview_findings(checks: list[SerpCheck]) -> list[Finding]:
    out = []
    for c in checks:
        if c.ai_overview != "present" or c.ai_overview_cites_site:
            continue
        out.append(
            finding(
                key=f"ai-overview-not-cited:{slug(c.keyword)}:{c.market.lower()}",
                issue=f"Google shows an AI Overview for '{c.keyword}' ({c.market}) without citing the site",
                category="ai-search/citations",
                priority="medium" if c.our_position and c.our_position <= 10 else "low",
                evidence_items=[
                    evidence(
                        "SerpApi (Google AI Overview references)",
                        f"{c.device}, {c.checked_on}; cited: {', '.join(c.ai_overview_domains) or 'none listed'}; "
                        f"site organic position {c.our_position or 'not in top 10'}",
                        c.our_url,
                    )
                ],
                seo_impact="The AI Overview answers above the results and sends clicks to the cited sources.",
                action="Give the page a direct, self-contained answer passage (definition, numbers with "
                "their official source, a short list), clear headings that match the question, "
                "and up-to-date dates; compare with the cited pages.",
                outcome="The site is cited in the AI Overview.",
                verification="Next SERP check lists the site among the AI Overview references.",
                skill=SKILL,
                urls=[c.our_url] if c.our_url else [],
            )
        )
    return out


def panel_findings(
    answers: list[AiAnswer], site_domain: str, brand: str
) -> list[Finding]:
    latest: dict[tuple[str, str], AiAnswer] = {}
    for a in sorted(answers, key=lambda a: a.observed_on):
        latest[(a.prompt, a.engine)] = a
    missing: dict[str, list[AiAnswer]] = defaultdict(list)
    for a in latest.values():
        if not a.mentions_brand and not any(
            _is_site(d, site_domain) for d in a.cited_domains
        ):
            missing[a.prompt].append(a)
    out = []
    for prompt, items in missing.items():
        others = sorted({d for a in items for d in a.cited_domains})[:8]
        out.append(
            finding(
                key=f"ai-prompt-gap:{slug(prompt)}",
                issue=f"AI answers to '{prompt}' neither cite nor mention {brand or site_domain}",
                category="ai-search/citations",
                priority="medium",
                evidence_items=[
                    evidence(
                        "Recorded AI answers (prompt panel)",
                        "; ".join(f"{a.engine} {a.observed_on}" for a in items)
                        + f"; cited instead: {', '.join(others) or 'none'}",
                    )
                ],
                seo_impact="People asking AI assistants this question do not hear about the site.",
                action="Publish or improve the page that best answers this prompt, make the answer "
                "quotable, and earn mentions on the domains the engines cite.",
                outcome="The site is cited or mentioned for this prompt.",
                verification="Record a new answer for the same prompt and engine.",
                skill=SKILL,
            )
        )
    return out


def snapshot(
    access: list[CrawlerAccess],
    llms: LlmsTxt,
    checks: list[SerpCheck],
    sov: dict[str, Any],
) -> dict[str, Any]:
    return {
        "crawler_access": [asdict(a) | {"blocked": a.blocked} for a in access],
        "llms_txt": asdict(llms) | {"valid": llms.valid},
        "ai_overviews": [
            {
                "keyword": c.keyword,
                "market": c.market,
                "status": c.ai_overview,
                "cites_site": c.ai_overview_cites_site,
                "domains": c.ai_overview_domains,
            }
            for c in checks
        ],
        "share_of_voice": sov,
    }
