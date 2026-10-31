"""JSON-LD extraction and validation.

Follows ``seo-toolkit-schema``: three separate questions, never conflated and
never a promise of a rich result.

1. **Syntax**: does the block parse as JSON?
2. **Vocabulary**: is there a schema.org ``@context`` and an ``@type``?
3. **Feature requirements**: AURORA's small, documented rule set for common
   Google Search features (``FEATURE_RULES``). Missing "required" properties are
   warnings; missing "recommended" ones are info. They are a checklist to confirm
   in Google's Rich Results Test, not a verdict on eligibility.

Types whose Google rich result was retired (per
``.claude/skills/seo-schema/references/deprecated-types-2024-2026.md``, last
verified 2026-09-23) are reported as info, never as errors. Markup is only read;
nothing is generated or filled in.
"""

import json
from typing import Any

from pydantic import BaseModel

RULESET_VERSION = "aurora-sd-1 (2026-11-01)"
MAX_BLOCK_CHARS = 200_000
MAX_ITEMS = 50

# type -> (required, recommended, one_of_required)
FEATURE_RULES: dict[str, tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]] = {
    "Product": (
        ("name",),
        ("image", "description", "brand"),
        ("offers", "review", "aggregateRating"),
    ),
    "BreadcrumbList": (("itemListElement",), (), ()),
    "LocalBusiness": (
        ("name", "address"),
        ("telephone", "openingHoursSpecification", "url"),
        (),
    ),
    "Event": (
        ("name", "startDate", "location"),
        ("endDate", "eventStatus", "image"),
        (),
    ),
    "VideoObject": (
        ("name", "thumbnailUrl", "uploadDate"),
        ("description", "contentUrl", "duration"),
        (),
    ),
    "Recipe": (("name", "image"), ("author", "datePublished", "recipeIngredient"), ()),
    "JobPosting": (
        ("title", "description", "datePosted", "hiringOrganization"),
        ("validThrough", "employmentType"),
        ("jobLocation", "applicantLocationRequirements"),
    ),
    "Article": ((), ("headline", "image", "datePublished", "author"), ()),
    "NewsArticle": ((), ("headline", "image", "datePublished", "author"), ()),
    "BlogPosting": ((), ("headline", "image", "datePublished", "author"), ()),
    "Organization": ((), ("name", "url", "logo"), ()),
    "WebSite": ((), ("name", "url"), ()),
    "SoftwareApplication": (
        ("name",),
        ("offers", "aggregateRating", "applicationCategory"),
        (),
    ),
    "Person": (("name",), (), ()),
}
SUBTYPES = {
    "Restaurant": "LocalBusiness",
    "Store": "LocalBusiness",
    "MedicalBusiness": "LocalBusiness",
    "ProfessionalService": "LocalBusiness",
    "MobileApplication": "SoftwareApplication",
    "WebApplication": "SoftwareApplication",
    "Corporation": "Organization",
    "TechArticle": "Article",
    "ProductGroup": "Product",
}
RETIRED = {
    "FAQPage": "FAQ rich results were fully retired on 2026-05-07; keep only if accurate for other consumers.",
    "HowTo": "HowTo rich results were removed in September 2023.",
    "ClaimReview": "The fact-check rich result was retired in June 2025.",
    "VehicleListing": "Vehicle listing rich results were retired in June 2025.",
    "SpecialAnnouncement": "Special Announcement was deprecated in July 2025.",
    "EstimatedSalary": "Estimated salary rich results were retired in June 2025.",
}


class SchemaIssue(BaseModel):
    level: str  # error | warning | info
    question: str  # syntax | vocabulary | feature | deprecated | agreement
    message: str
    type: str | None = None
    property: str | None = None


class SchemaItem(BaseModel):
    types: list[str]
    properties: list[str]
    name: str | None = None
    issues: list[SchemaIssue] = []


class StructuredDataReport(BaseModel):
    blocks: int = 0
    parse_errors: int = 0
    items: list[SchemaItem] = []
    issues: list[SchemaIssue] = []  # block-level (syntax)
    microdata_present: bool = False
    rdfa_present: bool = False
    ruleset: str = RULESET_VERSION


def _types(item: dict[str, Any]) -> list[str]:
    raw = item.get("@type")
    values = raw if isinstance(raw, list) else [raw]
    out = []
    for value in values:
        if isinstance(value, str) and value.strip():
            out.append(value.strip().rsplit("/", 1)[-1].rsplit(":", 1)[-1])
    return out


def _present(item: dict[str, Any], prop: str) -> bool:
    value = item.get(prop)
    if value is None:
        return False
    if isinstance(value, str | list | dict):
        return bool(value)
    return True


def _schema_context(context: Any) -> bool:
    if isinstance(context, str):
        return "schema.org" in context.lower()
    if isinstance(context, list):
        return any(_schema_context(c) for c in context)
    if isinstance(context, dict):
        return "@vocab" in context and _schema_context(context["@vocab"])
    return False


def _flatten(node: Any, context: Any, out: list[tuple[dict[str, Any], Any]]) -> None:
    if len(out) >= MAX_ITEMS:
        return
    if isinstance(node, list):
        for child in node:
            _flatten(child, context, out)
        return
    if not isinstance(node, dict):
        return
    context = node.get("@context", context)
    if "@graph" in node:
        _flatten(node["@graph"], context, out)
        if "@type" not in node:
            return
    out.append((node, context))


def _check_breadcrumbs(item: dict[str, Any], issues: list[SchemaIssue]) -> None:
    elements = item.get("itemListElement")
    if not isinstance(elements, list):
        return
    for index, element in enumerate(elements[:50], start=1):
        if not isinstance(element, dict):
            continue
        if "position" not in element:
            issues.append(
                SchemaIssue(
                    level="warning",
                    question="feature",
                    type="ListItem",
                    property="position",
                    message=f"Breadcrumb item {index} has no position.",
                )
            )
        if not (_present(element, "name") or isinstance(element.get("item"), dict)):
            issues.append(
                SchemaIssue(
                    level="warning",
                    question="feature",
                    type="ListItem",
                    property="name",
                    message=f"Breadcrumb item {index} has no name.",
                )
            )


def _validate(item: dict[str, Any], context: Any) -> SchemaItem:
    types = _types(item)
    issues: list[SchemaIssue] = []
    if not types:
        issues.append(
            SchemaIssue(
                level="warning", question="vocabulary", message="Item has no @type."
            )
        )
    if not _schema_context(context):
        issues.append(
            SchemaIssue(
                level="warning",
                question="vocabulary",
                message="No schema.org @context applies to this item.",
            )
        )
    for type_name in types:
        if type_name in RETIRED:
            issues.append(
                SchemaIssue(
                    level="info",
                    question="deprecated",
                    type=type_name,
                    message=RETIRED[type_name],
                )
            )
        rule_type = SUBTYPES.get(type_name, type_name)
        rule = FEATURE_RULES.get(rule_type)
        if rule is None:
            continue
        required, recommended, one_of = rule
        for prop in required:
            if not _present(item, prop):
                issues.append(
                    SchemaIssue(
                        level="warning",
                        question="feature",
                        type=type_name,
                        property=prop,
                        message=f"{type_name} is missing '{prop}', required by the {rule_type} feature rules.",
                    )
                )
        if one_of and not any(_present(item, p) for p in one_of):
            issues.append(
                SchemaIssue(
                    level="warning",
                    question="feature",
                    type=type_name,
                    property="|".join(one_of),
                    message=f"{type_name} needs at least one of: {', '.join(one_of)}.",
                )
            )
        for prop in recommended:
            if not _present(item, prop):
                issues.append(
                    SchemaIssue(
                        level="info",
                        question="feature",
                        type=type_name,
                        property=prop,
                        message=f"{type_name} has no recommended '{prop}'.",
                    )
                )
        if rule_type == "BreadcrumbList":
            _check_breadcrumbs(item, issues)
    potential = item.get("potentialAction")
    actions = potential if isinstance(potential, list) else [potential]
    if any(isinstance(a, dict) and "SearchAction" in _types(a) for a in actions):
        issues.append(
            SchemaIssue(
                level="info",
                question="deprecated",
                type="SearchAction",
                message="The sitelinks search box (SearchAction) was retired in November 2024.",
            )
        )
    name = (
        item.get("name") if isinstance(item.get("name"), str) else item.get("headline")
    )
    return SchemaItem(
        types=types,
        properties=sorted(k for k in item if not k.startswith("@"))[:60],
        name=name[:300] if isinstance(name, str) else None,
        issues=issues,
    )


def analyze_json_ld(
    blocks: list[str], *, microdata: bool = False, rdfa: bool = False
) -> StructuredDataReport:
    report = StructuredDataReport(
        blocks=len(blocks), microdata_present=microdata, rdfa_present=rdfa
    )
    for number, block in enumerate(blocks, start=1):
        text = block.strip()
        if len(text) > MAX_BLOCK_CHARS:
            report.parse_errors += 1
            report.issues.append(
                SchemaIssue(
                    level="warning",
                    question="syntax",
                    message=f"JSON-LD block {number} exceeds {MAX_BLOCK_CHARS} characters; not parsed.",
                )
            )
            continue
        try:
            data = json.loads(text)
        except ValueError as exc:
            report.parse_errors += 1
            position = (
                f" (line {exc.lineno}, column {exc.colno})"
                if isinstance(exc, json.JSONDecodeError)
                else ""
            )
            report.issues.append(
                SchemaIssue(
                    level="warning",
                    question="syntax",
                    message=f"JSON-LD block {number} is not valid JSON{position}.",
                )
            )
            continue
        flat: list[tuple[dict[str, Any], Any]] = []
        _flatten(data, None, flat)
        for item, context in flat:
            if len(report.items) < MAX_ITEMS:
                report.items.append(_validate(item, context))
    return report


def agreement_issues(
    report: StructuredDataReport, titles: list[str], h1s: list[str]
) -> list[SchemaIssue]:
    """Marked-up headline/name of an article that matches neither the title nor an H1."""
    visible = " ".join(titles + h1s).lower()
    issues = []
    for item in report.items:
        if not item.name or not visible:
            continue
        if (
            any(t in ("Article", "NewsArticle", "BlogPosting") for t in item.types)
            and item.name.lower() not in visible
        ):
            issues.append(
                SchemaIssue(
                    level="info",
                    question="agreement",
                    type=item.types[0],
                    property="headline",
                    message="The marked-up headline does not appear in the page title or H1.",
                )
            )
    return issues
