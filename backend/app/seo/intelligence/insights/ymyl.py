"""Accuracy checks for health and medical-education content (YMYL).

Deterministic, on fetched HTML:

- **Unsourced numbers**: fees, percentages and pass rates in a paragraph, list
  item or table cell with no link to an authoritative domain in the same block
  or its section.
- **Stale years**: a past year in a title or heading, or next to exam/fee words
  in body text, on a page that presents current information.
- **Claim terms**: superlatives and promises from the site's editorial list
  ("#1", "guaranteed", "100%", "success rate" ...).
- **Facts registry**: facts the owner verified at the official source (with a
  regex for each) are compared with every page; contradictions and facts past
  their review date are reported. AURORA never fills the registry itself.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from urllib.parse import urlparse

from bs4 import BeautifulSoup, Tag

from app.seo.intelligence.insights.common import evidence, finding, page_path
from app.seo.intelligence.models import Finding, YmylSettings

SKILL = "seo-ymyl-accuracy"
MONEY_RE = re.compile(
    r"(?:US\$|\$|USD\s?|₹|INR\s?|Rs\.?\s?)\s?\d[\d,]*(?:\.\d+)?", re.I
)
PERCENT_RE = re.compile(r"\b\d{1,3}(?:\.\d+)?\s?%")
PASS_RE = re.compile(r"pass(?:ing)?\s+rates?", re.I)
YEAR_RE = re.compile(r"\b(20[12]\d)\b")
TIME_WORDS = re.compile(
    r"\b(fee|fees|cost|exam|deadline|dates?|updated|latest|current|new|changes?|match|cycle|eligib\w*)\b",
    re.I,
)
BLOCK_TAGS = ("p", "li", "td", "th", "blockquote")


@dataclass
class PageAccuracy:
    url: str
    unsourced: list[str] = field(default_factory=list)
    stale: list[str] = field(default_factory=list)
    claims: dict[str, list[str]] = field(default_factory=dict)
    fact_mismatches: list[tuple[str, str]] = field(
        default_factory=list
    )  # (fact id, found)
    page_has_official_link: bool = False


def _authoritative(href: str, domains: list[str]) -> bool:
    host = (urlparse(href).hostname or "").lower().removeprefix("www.")
    return any(host == d or host.endswith("." + d) for d in domains)


def _section_links(block: Tag) -> list[str]:
    """Links in the block plus the siblings up to the previous heading."""
    hrefs = [a.get("href") or "" for a in block.find_all("a")]
    sibling = block.find_previous_sibling()
    steps = 0
    while (
        sibling is not None
        and steps < 12
        and sibling.name not in ("h1", "h2", "h3", "h4")
    ):
        hrefs += (
            [a.get("href") or "" for a in sibling.find_all("a")]
            if isinstance(sibling, Tag)
            else []
        )
        sibling = sibling.find_previous_sibling()
        steps += 1
    nxt = block.find_next_sibling()
    if isinstance(nxt, Tag) and nxt.name not in ("h1", "h2", "h3", "h4"):
        hrefs += [a.get("href") or "" for a in nxt.find_all("a")]
    return [str(h) for h in hrefs]


NEGATION_RE = re.compile(
    r"\b(no|not|never|cannot|can't|don't|doesn't|isn't|without|nor|neither)\b[^.]{0,25}$",
    re.I,
)
LISTING_PATHS = ("/", "/blog")


def _in_teaser(block: Tag) -> bool:
    """A card or teaser: the block sits inside a link, or one link covers most of it."""
    if block.find_parent("a") is not None:
        return True
    text = block.get_text(" ").strip()
    link_text = sum(len(a.get_text(" ").strip()) for a in block.find_all("a"))
    return bool(text) and link_text / len(text) > 0.6


def check_page(
    url: str, html: str, settings: YmylSettings, today: date
) -> PageAccuracy:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "nav", "footer", "header"]):
        tag.decompose()
    result = PageAccuracy(url=url)
    result.page_has_official_link = any(
        _authoritative(a.get("href") or "", settings.authoritative_domains)
        for a in soup.find_all("a")
    )
    listing = page_path(url) in LISTING_PATHS
    current = today.year
    for block in soup.find_all(BLOCK_TAGS):
        text = " ".join(block.get_text(" ").split())
        if len(text) < 20 or _in_teaser(block):
            continue
        numbers = MONEY_RE.findall(text) + PERCENT_RE.findall(text)
        if numbers or PASS_RE.search(text):
            if not any(
                _authoritative(h, settings.authoritative_domains)
                for h in _section_links(block)
            ):
                result.unsourced.append(text[:180])
        for year in YEAR_RE.findall(text):
            if (
                int(year) < current
                and TIME_WORDS.search(text)
                and not re.search(
                    rf"(since|in|from|until|before)\s+{year}\b.*\b(to|until|through|-)\b",
                    text,
                    re.I,
                )
            ):
                if re.search(
                    r"\b(as of|in|for|updated|latest|current|new)\s+" + year, text, re.I
                ):
                    result.stale.append(text[:180])
                    break
    if not listing:  # listing pages show other posts' titles, not their own claims
        for heading in soup.find_all(["title", "h1", "h2", "h3"]):
            if heading.find_parent("a") is not None:
                continue
            text = " ".join(heading.get_text(" ").split())
            years = [int(y) for y in YEAR_RE.findall(text)]
            if years and max(years) < current:
                result.stale.append(f"{heading.name}: {text[:160]}")
    visible = " ".join(soup.get_text(" ").split())
    lowered = visible.lower()
    for term in settings.claim_terms:
        t = term.lower()
        start = 0
        while (idx := lowered.find(t, start)) != -1:
            start = idx + len(t)
            if NEGATION_RE.search(lowered[max(0, idx - 40) : idx]):
                continue  # "no score guarantees a match" is not a claim
            result.claims.setdefault(term, []).append(
                visible[max(0, idx - 60) : idx + 80]
            )
            break
    for fact in settings.facts:
        for m in re.finditer(fact.pattern, visible, re.I):
            found = (m.group(1) or "").strip()
            if found and _norm(found) != _norm(fact.expected):
                result.fact_mismatches.append((fact.id, found))
    return result


def _norm(value: str) -> str:
    return re.sub(r"[\s,]", "", value.lower().replace("us$", "$").replace("usd", "$"))


def due_facts(settings: YmylSettings, today: date) -> list[str]:
    return [
        f.id
        for f in settings.facts
        if f.verified_on + timedelta(days=f.review_after_days) < today
    ]


def accuracy_findings(
    pages: list[PageAccuracy], settings: YmylSettings, today: date
) -> list[Finding]:
    out: list[Finding] = []
    official = ", ".join(settings.authoritative_domains[:5])
    for p in pages:
        path = page_path(p.url)
        if p.unsourced:
            out.append(
                finding(
                    key=f"unsourced-numbers:{path}",
                    issue=f"{path} states {len(p.unsourced)} fees/percentages/pass rates without an official source nearby"
                    + (
                        ""
                        if p.page_has_official_link
                        else " (no official source linked anywhere on the page)"
                    ),
                    category="content/ymyl",
                    priority="high"
                    if len(p.unsourced) >= 3 and not p.page_has_official_link
                    else "medium"
                    if not p.page_has_official_link
                    else "low",
                    evidence_items=[
                        evidence(
                            "AURORA crawl (visible text)",
                            " | ".join(p.unsourced[:4]),
                            p.url,
                        )
                    ],
                    seo_impact="Unsourced health/education numbers weaken trust (E-E-A-T) and AI citability, "
                    "and go stale silently.",
                    action=f"Link each number to its official source and year ({official}), or remove it.",
                    outcome="Every fee, rate and pass figure links to its source.",
                    verification="Re-run /seo-ymyl-accuracy.",
                    skill=SKILL,
                    urls=[p.url],
                )
            )
        if p.stale:
            out.append(
                finding(
                    key=f"stale-year:{path}",
                    issue=f"{path} presents past years as current",
                    category="content/freshness",
                    priority="medium",
                    evidence_items=[
                        evidence(
                            "AURORA crawl (titles, headings, text)",
                            " | ".join(p.stale[:4]),
                            p.url,
                        )
                    ],
                    seo_impact="Outdated exam facts mislead readers and lose clicks to fresher pages.",
                    action="Check whether newer official data or rules exist: update the page and its "
                    "dateModified if so; if the year is the latest available, say so ('latest "
                    "available data'); label dated news as an archived update linking to current rules.",
                    outcome="No stale year presented as current.",
                    verification="Re-run /seo-ymyl-accuracy.",
                    skill=SKILL,
                    urls=[p.url],
                )
            )
        if p.claims:
            strong = {"#1", "guaranteed", "guarantee", "100%", "number one"}
            out.append(
                finding(
                    key=f"unsupported-claim:{path}",
                    issue=f"{path} uses claim terms: " + ", ".join(sorted(p.claims)),
                    category="content/claims",
                    priority="high"
                    if any(t.lower() in strong for t in p.claims) and path == "/"
                    else "medium",
                    evidence_items=[
                        evidence(
                            "AURORA crawl (visible text)",
                            " | ".join(
                                f"'{t}': …{v[0]}…"
                                for t, v in list(p.claims.items())[:4]
                            ),
                            p.url,
                        )
                    ],
                    seo_impact="Unproven superlatives and promises reduce trust and can breach advertising rules.",
                    action="Back each claim with published, dated evidence (method, cohort, period) or rephrase it.",
                    outcome="Every claim is substantiated or removed.",
                    verification="Re-run /seo-ymyl-accuracy.",
                    skill=SKILL,
                    urls=[p.url],
                )
            )
        for fact_id, found in p.fact_mismatches:
            fact = next(f for f in settings.facts if f.id == fact_id)
            out.append(
                finding(
                    key=f"fact-mismatch:{fact_id}:{path}",
                    issue=f"{path} says '{found}' but the verified fact is '{fact.expected}' ({fact.statement})",
                    category="content/ymyl",
                    priority="high",
                    evidence_items=[
                        evidence(
                            "AURORA crawl (visible text)", f"found '{found}'", p.url
                        ),
                        evidence(
                            "Facts registry (owner-verified)",
                            f"{fact.expected}, verified {fact.verified_on}",
                            fact.source_url,
                        ),
                    ],
                    seo_impact="A wrong fact on a medical-education site damages trust and can mislead students.",
                    action=f"Correct it to '{fact.expected}' and cite {fact.source_url}.",
                    outcome="The page matches the verified fact.",
                    verification="Re-run /seo-ymyl-accuracy.",
                    skill=SKILL,
                    urls=[p.url],
                )
            )
    for fact_id in due_facts(settings, today):
        fact = next(f for f in settings.facts if f.id == fact_id)
        out.append(
            finding(
                key=f"fact-review:{fact_id}",
                issue=f"Fact '{fact.statement}' is due for re-verification (verified {fact.verified_on})",
                category="content/ymyl",
                priority="low",
                evidence_items=[
                    evidence(
                        "Facts registry (owner-verified)",
                        f"review every {fact.review_after_days} days",
                        fact.source_url,
                    )
                ],
                seo_impact="Official fees and rules change; an old verification may no longer hold.",
                action="Re-check the official source and update the registry entry (owner).",
                outcome="The fact is re-verified.",
                verification="verified_on is updated in the site config.",
                skill=SKILL,
            )
        )
    return out


def checkable_keys(pages: list[PageAccuracy], settings: YmylSettings) -> list[str]:
    keys = []
    for p in pages:
        path = page_path(p.url)
        keys += [
            f"unsourced-numbers:{path}",
            f"stale-year:{path}",
            f"unsupported-claim:{path}",
        ]
        keys += [f"fact-mismatch:{f.id}:{path}" for f in settings.facts]
    return keys + [f"fact-review:{f.id}" for f in settings.facts]
