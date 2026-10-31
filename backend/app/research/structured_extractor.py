"""Structured extraction: turn processed pages into typed entities and claims.

Deterministic extraction (headings, lists, tables and explicit phrases) is the
default and needs no credentials. Every fact becomes a ``ResearchClaim`` whose
``evidence_excerpt`` is copied verbatim from the page, so it can be re-checked
against the stored content. Nothing is inferred: headquarters, founding year,
company size and prices are extracted only from explicit statements, and
missing fields stay empty. An optional LLM extractor (pluggable; none is wired
yet because the Groq/Claude adapters are not implemented) must return JSON that
is schema-validated and whose excerpts are verified against the page text.
"""

import json
import logging
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.core.config import Settings, get_settings
from app.core.logging import log_event
from app.prompts.extraction_prompts import build_extraction_prompt
from app.research.text_utils import excerpt_in_text, trim_excerpt
from app.schemas.intelligence import (
    CompanyInfo,
    DocumentStatus,
    EntityType,
    ExtractedEntity,
    ExtractionMethod,
    FeatureInfo,
    OfficialStatus,
    PageType,
    PricingInfo,
    ProcessedDocument,
    ProductInfo,
    ResearchClaim,
    ServiceInfo,
    TargetCustomerInfo,
    TechnologyInfo,
)
from app.schemas.research import utc_now

logger = logging.getLogger(__name__)

GENERIC_NAMES = frozenset(
    {
        "product", "products", "solutions", "solution", "platform", "features",
        "feature", "pricing", "plans", "services", "service", "overview", "home",
        "welcome", "about", "about us", "contact", "contact us", "integrations",
        "faq", "documentation", "docs", "resources", "blog",
    }
)  # fmt: skip
_SKIP_FEATURE_HEADINGS = GENERIC_NAMES | {
    "get started", "learn more", "faqs", "testimonials", "customers", "book a demo",
    "request a demo", "start free trial", "ready to get started?", "sign up",
}  # fmt: skip
KNOWN_TECHNOLOGIES = (
    "Python", "Java", "JavaScript", "TypeScript", "Node.js", "React", "Kubernetes",
    "Docker", "AWS", "Azure", "Google Cloud", "TensorFlow", "PyTorch", "OpenAI",
    "LangChain", "Hugging Face", "PostgreSQL", "MongoDB", "Redis", "Kafka",
    "Snowflake", "Salesforce", "SAP", "GraphQL", "REST API", "Zapier", "Slack",
    "WhatsApp", "Microsoft Teams", "Zoho", "HubSpot", "Shopify", "Power BI",
    "Tableau", "BigQuery", "Terraform", "Elasticsearch", "Webhooks",
)  # fmt: skip
CUSTOMER_TERMS: dict[str, str | None] = {
    "enterprises": "enterprise",
    "enterprise": "enterprise",
    "startups": "startup",
    "small businesses": "small",
    "small and medium businesses": "small-medium",
    "smbs": "small-medium",
    "smes": "small-medium",
    "mid-market": "mid-market",
    "agencies": None,
    "developers": None,
    "marketers": None,
    "sales teams": None,
    "retailers": None,
    "manufacturers": None,
    "banks": None,
    "healthcare providers": None,
    "ecommerce brands": None,
}  # fmt: skip
_CUSTOMER_TRIGGERS = (
    "for ", "serving", "helps ", "help ", "trusted by", "used by", "built for", "designed for",
)  # fmt: skip

_HQ = re.compile(
    r"(?i:headquartered|head office|headquarters)\s*(?:(?i:is|are)\s+)?"
    r"(?:(?i:in|at)\b|:)?\s*([A-Z][A-Za-z.\-]+(?:\s[A-Z][A-Za-z.\-]+){0,2}"
    r"(?:,\s*[A-Z][A-Za-z.\-]+(?:\s[A-Z][A-Za-z.\-]+){0,2}){0,2})"
)
_FOUNDED = re.compile(
    r"(?i:\b(?:founded|established|incorporated)\b)[^.\n]{0,25}?\b(19\d{2}|20[0-3]\d)\b"
)
_SIZE = re.compile(
    r"(?i)\b(\d[\d,]*\+?)\s+(?:full[- ]time\s+)?(?:employees|team members)\b"
)
_INDUSTRY = re.compile(
    r"(?i)\b(?:is|are)\s+an?\s+([a-z][a-z0-9 \-/&]{2,50}?)\s+"
    r"(?:company|platform|provider|startup|firm|agency|vendor)\b"
)
_CUR = r"(?P<cur>US\$|\$|€|£|₹|Rs\.?|USD|EUR|GBP|INR)"
_AMT = r"(?P<amt>\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?)"
_PRICE = re.compile(
    rf"(?<![A-Za-z]){_CUR}\s?{_AMT}"
    r"(?:\s*(?:/|per|a)\s*(?P<per>month|mo|year|yr|user|seat))?"
    r"(?:\s*(?:/|per)\s*(?P<per2>month|mo|year|yr))?",
    re.IGNORECASE,
)
_PER_USER = re.compile(r"(?i)(?:\bper|/)\s*(?:user|seat)\b")
_CONTACT_SALES = re.compile(
    r"(?i)(contact (our )?sales|custom pricing|request (a )?quote|talk to sales|pricing on request)"
)
_FREE_TIER = re.compile(
    r"(?i)\b(free (plan|tier|forever)|forever free|free for up to)\b"
)
_TRIAL = re.compile(r"(?i)\b(\d+[- ]day (free )?trial|free trial)\b")
_CURRENCY = {"€": "EUR", "£": "GBP", "₹": "INR", "rs": "INR", "rs.": "INR"}
_INTERVAL = {"month": "month", "mo": "month", "year": "year", "yr": "year"}


@dataclass(frozen=True)
class WebsiteContext:
    """What extraction needs to know about the website being processed."""

    name: str
    domain: str
    homepage_url: str
    official_status: OfficialStatus = OfficialStatus.UNVERIFIED


@dataclass
class ExtractionResult:
    """Entities and claims extracted for one website."""

    entities: list[ExtractedEntity] = field(default_factory=list)
    claims: list[ResearchClaim] = field(default_factory=list)
    pricing_pages_checked: int = 0
    notes: list[str] = field(default_factory=list)


def _clean_inline(text: str) -> str:
    return re.sub(
        r"\s+", " ", re.sub(r"[*_`]+|\[([^\]]*)\]\([^)]*\)", r"\1", text)
    ).strip()


def _title_name(doc: ProcessedDocument) -> str | None:
    for section in doc.sections:
        if section.level == 1 and section.heading:
            name = _clean_inline(section.heading)
            break
    else:
        name = re.split(r"\s[|\-–—:·]\s", doc.title or "", maxsplit=1)[0].strip()
    if not name or name.casefold() in GENERIC_NAMES or len(name) > 80:
        return None
    return name


def _first_paragraph(text: str) -> str | None:
    for block in re.split(r"\n\s*\n", text):
        block = block.strip()
        if len(block) >= 40 and not block.startswith(
            ("-", "*", "|", "#", "!", ">", "```")
        ):
            if not re.match(r"^\[[^\]]+\]\([^)]*\)$", block):
                return block.split("\n")[0].strip() if len(block) > 400 else block
    return None


def _bullets(text: str) -> list[str]:
    items: list[str] = []
    for line in text.split("\n"):
        match = re.match(r"^\s*(?:[-*+]|\d+[.)])\s+(.+)$", line)
        if match and 2 <= len(_clean_inline(match.group(1))) <= 140:
            items.append(line.strip())
    return items


def _sentences(text: str) -> list[str]:
    parts: list[str] = []
    for line in text.split("\n"):
        line = line.strip()
        if line and not line.startswith(("#", "|", "```")):
            parts.extend(s for s in re.split(r"(?<=[.!?])\s+", line) if s)
    return parts


class _Facts:
    """Builds verbatim-evidenced claims for one document."""

    def __init__(
        self, ctx: WebsiteContext, doc: ProcessedDocument, out: ExtractionResult
    ):
        self.ctx, self.doc, self.out = ctx, doc, out
        self.seen: set[tuple[str, str, str]] = set()
        self.corpus = f"{doc.normalized_markdown}\n{doc.title or ''}\n{doc.metadata.get('description') or ''}"

    def add(
        self,
        subject: str,
        subject_type: EntityType,
        predicate: str,
        value: str,
        excerpt: str,
        confidence: float = 0.7,
    ) -> None:
        excerpt = trim_excerpt(excerpt)
        if not value or not excerpt_in_text(excerpt, self.corpus):
            return  # never record a claim we cannot point to in the source
        key = (subject, predicate, value)
        if key in self.seen:
            return
        self.seen.add(key)
        self.out.claims.append(
            ResearchClaim(
                subject_entity=subject,
                subject_type=subject_type,
                scope_domain=self.ctx.domain,
                predicate=predicate,
                object_value=value[:500],
                claim_text=f"{subject} {predicate.replace('_', ' ')}: {value[:200]}",
                source_url=self.doc.normalized_url,
                source_title=self.doc.title,
                source_domain=self.ctx.domain,
                source_content_hash=self.doc.content_hash,
                evidence_excerpt=excerpt,
                extraction_method=ExtractionMethod.DETERMINISTIC,
                confidence_score=confidence,
                publication_date=self.doc.published_at,
            )
        )


class DeterministicExtractor:
    """Rule-based extraction from processed documents."""

    def extract(
        self, ctx: WebsiteContext, documents: list[ProcessedDocument]
    ) -> ExtractionResult:
        """Extract entities and evidence-linked claims for one website."""
        docs = [d for d in documents if d.status == DocumentStatus.PROCESSED]
        out = ExtractionResult()
        company = CompanyInfo(name=ctx.name, official_website=ctx.homepage_url)
        company.source_urls = sorted({d.normalized_url for d in docs})
        for doc in docs:
            facts = _Facts(ctx, doc, out)
            if doc.page_type in (
                PageType.HOMEPAGE,
                PageType.ABOUT,
                PageType.COMPANY,
                PageType.CONTACT,
            ):
                self._company(ctx, doc, facts, company)
            if doc.page_type == PageType.PRODUCT:
                self._product(ctx, doc, facts, out, company)
            if doc.page_type == PageType.FEATURES:
                self._features(ctx, doc, facts, out, company)
            if doc.page_type == PageType.INTEGRATIONS:
                self._integrations(ctx, doc, facts, out, company)
            if doc.page_type == PageType.SERVICE:
                self._service(ctx, doc, facts, out, company)
            if doc.page_type == PageType.PRICING:
                out.pricing_pages_checked += 1
                self._pricing(ctx, doc, facts, out)
            self._customers(ctx, doc, facts, out)
            self._technologies(ctx, doc, facts, out, company)
        out.entities.insert(
            0,
            ExtractedEntity(
                entity_type=EntityType.COMPANY,
                name=ctx.name,
                data=company.model_dump(mode="json"),
                source_urls=company.source_urls,
                scope_domain=ctx.domain,
                confidence=0.8
                if ctx.official_status == OfficialStatus.VERIFIED
                else 0.5,
            ),
        )
        log_event(
            logger,
            "structured_extraction",
            domain=ctx.domain,
            entities=len(out.entities),
            claims=len(out.claims),
        )
        return out

    # -- company ---------------------------------------------------------------

    def _company(
        self,
        ctx: WebsiteContext,
        doc: ProcessedDocument,
        facts: _Facts,
        company: CompanyInfo,
    ) -> None:
        text = doc.normalized_markdown
        if company.description is None:
            paragraph = _first_paragraph(text)
            if paragraph:
                company.description = _clean_inline(paragraph)[:500]
                facts.add(
                    ctx.name,
                    EntityType.COMPANY,
                    "description",
                    company.description,
                    paragraph,
                    0.6,
                )
        for sentence in _sentences(text):
            if m := _HQ.search(sentence):
                value = m.group(1).strip(" .,")
                company.headquarters = company.headquarters or value
                facts.add(
                    ctx.name, EntityType.COMPANY, "headquarters", value, sentence, 0.7
                )
            if m := _FOUNDED.search(sentence):
                company.founded_year = company.founded_year or int(m.group(1))
                facts.add(
                    ctx.name,
                    EntityType.COMPANY,
                    "founded_year",
                    m.group(1),
                    sentence,
                    0.7,
                )
            if m := _SIZE.search(sentence):
                value = f"{m.group(1)} employees"
                company.company_size = company.company_size or value
                facts.add(
                    ctx.name, EntityType.COMPANY, "company_size", value, sentence, 0.6
                )
            if doc.page_type in (PageType.HOMEPAGE, PageType.ABOUT) and (
                m := _INDUSTRY.search(sentence)
            ):
                value = m.group(1).strip()
                company.industry = company.industry or value
                facts.add(
                    ctx.name, EntityType.COMPANY, "industry", value, sentence, 0.5
                )

    # -- products, features, integrations, services -----------------------------

    def _product(
        self, ctx, doc, facts: _Facts, out: ExtractionResult, company: CompanyInfo
    ) -> None:
        name = _title_name(doc)
        if name is None:
            return
        product = ProductInfo(
            name=name, product_url=doc.normalized_url, source_urls=[doc.normalized_url]
        )
        paragraph = _first_paragraph(doc.normalized_markdown)
        if paragraph:
            product.description = _clean_inline(paragraph)[:500]
            facts.add(
                name,
                EntityType.PRODUCT,
                "description",
                product.description,
                paragraph,
                0.6,
            )
        for section in doc.sections:
            heading = (section.heading or "").casefold()
            bullets = _bullets(section.text)
            if re.search(
                r"feature|capabilit|key|highlight|benefit|what you get", heading
            ):
                self._attach_features(ctx, doc, facts, out, name, bullets, product)
            elif "integrat" in heading:
                for line in bullets[:30]:
                    item = _clean_inline(re.sub(r"^\s*(?:[-*+]|\d+[.)])\s+", "", line))
                    product.integrations.append(item)
                    facts.add(name, EntityType.PRODUCT, "integration", item, line, 0.6)
            elif "use case" in heading:
                for line in bullets[:15]:
                    item = _clean_inline(re.sub(r"^\s*(?:[-*+]|\d+[.)])\s+", "", line))
                    product.use_cases.append(item)
                    facts.add(name, EntityType.PRODUCT, "use_case", item, line, 0.6)
        if name not in company.products:
            company.products.append(name)
            facts.add(ctx.name, EntityType.COMPANY, "offers_product", name, name, 0.6)
        out.entities.append(
            ExtractedEntity(
                entity_type=EntityType.PRODUCT,
                name=name,
                data=product.model_dump(mode="json"),
                source_urls=[doc.normalized_url],
                scope_domain=ctx.domain,
                confidence=0.6,
            )
        )

    def _attach_features(
        self,
        ctx,
        doc,
        facts: _Facts,
        out,
        product_name: str,
        bullets: list[str],
        product: ProductInfo | None = None,
    ) -> None:
        for line in bullets[:25]:
            item = _clean_inline(re.sub(r"^\s*(?:[-*+]|\d+[.)])\s+", "", line))
            if product is not None:
                product.features.append(item)
            out.entities.append(
                ExtractedEntity(
                    entity_type=EntityType.FEATURE,
                    name=item,
                    data=FeatureInfo(
                        name=item,
                        product=product_name,
                        source_urls=[doc.normalized_url],
                    ).model_dump(mode="json"),
                    source_urls=[doc.normalized_url],
                    scope_domain=ctx.domain,
                    confidence=0.6,
                )
            )
            facts.add(product_name, EntityType.PRODUCT, "feature", item, line, 0.6)

    def _features(self, ctx, doc, facts: _Facts, out, company: CompanyInfo) -> None:
        owner = _title_name(doc) or ctx.name
        count = 0
        for section in doc.sections:
            heading = _clean_inline(section.heading or "")
            if (
                not heading
                or heading.casefold() in _SKIP_FEATURE_HEADINGS
                or section.level == 1
            ):
                continue
            description = _first_paragraph(section.text)
            feature = FeatureInfo(
                name=heading,
                description=_clean_inline(description)[:300] if description else None,
                product=owner,
                source_urls=[doc.normalized_url],
            )
            out.entities.append(
                ExtractedEntity(
                    entity_type=EntityType.FEATURE,
                    name=heading,
                    data=feature.model_dump(mode="json"),
                    source_urls=[doc.normalized_url],
                    scope_domain=ctx.domain,
                    confidence=0.55,
                )
            )
            facts.add(owner, EntityType.PRODUCT, "feature", heading, heading, 0.55)
            count += 1
            if count >= 20:
                break

    def _integrations(self, ctx, doc, facts: _Facts, out, company: CompanyInfo) -> None:
        for section in doc.sections:
            for line in _bullets(section.text)[:40]:
                item = _clean_inline(re.sub(r"^\s*(?:[-*+]|\d+[.)])\s+", "", line))
                facts.add(ctx.name, EntityType.COMPANY, "integration", item, line, 0.55)

    def _service(self, ctx, doc, facts: _Facts, out, company: CompanyInfo) -> None:
        name = _title_name(doc)
        if name is None:
            return
        service = ServiceInfo(
            name=name, service_url=doc.normalized_url, source_urls=[doc.normalized_url]
        )
        paragraph = _first_paragraph(doc.normalized_markdown)
        if paragraph:
            service.description = _clean_inline(paragraph)[:500]
            facts.add(
                name,
                EntityType.SERVICE,
                "description",
                service.description,
                paragraph,
                0.6,
            )
        for section in doc.sections:
            if re.search(
                r"deliverable|what you get|includes|scope",
                (section.heading or "").casefold(),
            ):
                for line in _bullets(section.text)[:15]:
                    item = _clean_inline(re.sub(r"^\s*(?:[-*+]|\d+[.)])\s+", "", line))
                    service.deliverables.append(item)
                    facts.add(name, EntityType.SERVICE, "deliverable", item, line, 0.6)
        if name not in company.services:
            company.services.append(name)
            facts.add(ctx.name, EntityType.COMPANY, "offers_service", name, name, 0.6)
        out.entities.append(
            ExtractedEntity(
                entity_type=EntityType.SERVICE,
                name=name,
                data=service.model_dump(mode="json"),
                source_urls=[doc.normalized_url],
                scope_domain=ctx.domain,
                confidence=0.6,
            )
        )

    # -- pricing -----------------------------------------------------------------

    def _pricing(self, ctx, doc, facts: _Facts, out: ExtractionResult) -> None:
        owner = _title_name(doc) or ctx.name
        seen: set[tuple[str | None, str, str | None, str | None]] = set()
        for section in doc.sections:
            heading = _clean_inline(section.heading or "")
            plan_default = (
                None if heading.casefold() in GENERIC_NAMES else heading or None
            )
            for line in section.text.split("\n"):
                self._price_line(ctx, doc, facts, out, owner, plan_default, line, seen)
            self._pricing_flags(ctx, facts, out, owner, plan_default, section.text)

    def _price_line(
        self, ctx, doc, facts, out, owner, plan_default, line, seen
    ) -> None:
        plan = plan_default
        if line.strip().startswith("|"):
            cells = [_clean_inline(c) for c in line.strip().strip("|").split("|")]
            if (
                cells
                and cells[0]
                and not _PRICE.search(cells[0])
                and set(cells[0]) - set("-: ")
            ):
                plan = cells[0]
        elif (
            bold := re.match(r"^\s*(?:[-*]\s+)?\*\*([^*]{2,40})\*\*", line)
        ) and not _PRICE.match(bold.group(1)):
            plan = bold.group(1).strip()
        for match in _PRICE.finditer(line):
            try:
                amount = Decimal(match.group("amt").replace(",", ""))
            except InvalidOperation:
                continue
            raw_cur = match.group("cur")
            currency = _CURRENCY.get(
                raw_cur.casefold(), raw_cur.upper() if raw_cur.isalpha() else raw_cur
            )
            per = (match.group("per") or match.group("per2") or "").casefold()
            lowered = line.casefold()
            interval = _INTERVAL.get(per)
            model = (
                "per_user"
                if per in ("user", "seat") or _PER_USER.search(line)
                else None
            )
            if interval is None:
                if "annual" in lowered or "yearly" in lowered or "/year" in lowered:
                    interval = "year"
                elif (
                    "monthly" in lowered
                    or "/month" in lowered
                    or "per month" in lowered
                ):
                    interval = "month"
            key = (plan, str(amount), currency, interval)
            if key in seen:
                continue
            seen.add(key)
            info = PricingInfo(
                product_or_service=owner,
                plan_name=plan,
                price_amount=amount,
                currency=currency,
                billing_interval=interval,
                pricing_model=model or "subscription" if interval else model,
                source_url=doc.normalized_url,
                source_urls=[doc.normalized_url],
                last_verified_at=utc_now(),
                raw_text=trim_excerpt(line, 200),
            )
            label = f"{plan or owner}: {raw_cur}{match.group('amt')}" + (
                f" per {interval}" if interval else ""
            )
            out.entities.append(
                ExtractedEntity(
                    entity_type=EntityType.PRICING,
                    name=label,
                    data=info.model_dump(mode="json"),
                    source_urls=[doc.normalized_url],
                    scope_domain=ctx.domain,
                    confidence=0.7,
                )
            )
            facts.add(owner, EntityType.PRICING, "price", label, line, 0.75)
        if _CONTACT_SALES.search(line) and not _PRICE.search(line):
            facts.add(
                owner,
                EntityType.PRICING,
                "pricing_model",
                "custom / contact sales",
                line,
                0.6,
            )

    def _pricing_flags(self, ctx, facts, out, owner, plan, text) -> None:
        for sentence in _sentences(text) + [
            ln for ln in text.split("\n") if ln.strip().startswith("|")
        ]:
            if _FREE_TIER.search(sentence):
                facts.add(
                    owner, EntityType.PRICING, "free_tier", "available", sentence, 0.6
                )
            if _TRIAL.search(sentence):
                facts.add(
                    owner, EntityType.PRICING, "trial", "available", sentence, 0.6
                )

    # -- customers and technologies ---------------------------------------------

    def _customers(self, ctx, doc, facts: _Facts, out: ExtractionResult) -> None:
        if doc.page_type not in (
            PageType.HOMEPAGE,
            PageType.ABOUT,
            PageType.PRODUCT,
            PageType.SERVICE,
            PageType.COMPANY,
            PageType.FEATURES,
        ):
            return
        found = 0
        for sentence in _sentences(doc.normalized_markdown):
            lowered = sentence.casefold()
            if not any(t in lowered for t in _CUSTOMER_TRIGGERS):
                continue
            for term, size in CUSTOMER_TERMS.items():
                if re.search(rf"\b{re.escape(term)}\b", lowered):
                    info = TargetCustomerInfo(
                        segment=term,
                        business_size=size,
                        source_urls=[doc.normalized_url],
                    )
                    out.entities.append(
                        ExtractedEntity(
                            entity_type=EntityType.TARGET_CUSTOMER,
                            name=term,
                            data=info.model_dump(mode="json"),
                            source_urls=[doc.normalized_url],
                            scope_domain=ctx.domain,
                            confidence=0.5,
                        )
                    )
                    facts.add(
                        ctx.name,
                        EntityType.COMPANY,
                        "target_customer",
                        term,
                        sentence,
                        0.5,
                    )
                    found += 1
            if found >= 6:
                break

    def _technologies(
        self, ctx, doc, facts: _Facts, out: ExtractionResult, company: CompanyInfo
    ) -> None:
        text = doc.normalized_markdown
        for tech in KNOWN_TECHNOLOGIES:
            if tech in company.technologies or len(company.technologies) >= 25:
                continue
            pattern = re.compile(rf"(?<![A-Za-z0-9]){re.escape(tech)}(?![A-Za-z0-9])")
            match = pattern.search(text)
            if not match:
                continue
            sentence = next((s for s in _sentences(text) if pattern.search(s)), None)
            if sentence is None:
                continue
            company.technologies.append(tech)
            info = TechnologyInfo(
                name=tech,
                documentation_urls=[doc.normalized_url]
                if doc.page_type in (PageType.DOCUMENTATION, PageType.API_DOCUMENTATION)
                else [],
                source_urls=[doc.normalized_url],
            )
            out.entities.append(
                ExtractedEntity(
                    entity_type=EntityType.TECHNOLOGY,
                    name=tech,
                    data=info.model_dump(mode="json"),
                    source_urls=[doc.normalized_url],
                    scope_domain=ctx.domain,
                    confidence=0.4,
                )
            )
            # "mentions" is deliberately weaker than "uses": we only know it is named.
            facts.add(
                ctx.name, EntityType.COMPANY, "mentions_technology", tech, sentence, 0.4
            )


# -- optional LLM extraction ---------------------------------------------------


class LLMClient(Protocol):
    """Minimal contract a future Groq/Claude wrapper must satisfy."""

    async def complete_json(self, system: str, user: str) -> str: ...


class LLMClaimModel(BaseModel):
    """One claim as returned by an LLM; validated before it is trusted."""

    model_config = ConfigDict(extra="forbid")

    entity_type: EntityType
    entity_name: str = Field(min_length=1, max_length=200)
    predicate: str = Field(pattern=r"^[a-z][a-z_]{1,39}$")
    value: str = Field(min_length=1, max_length=500)
    evidence_excerpt: str = Field(min_length=10, max_length=500)


class LLMClaimBatch(BaseModel):
    """Top-level LLM response."""

    model_config = ConfigDict(extra="forbid")

    claims: list[LLMClaimModel] = Field(max_length=50)


class LLMStructuredExtractor:
    """Schema-validated, evidence-checked extraction through an injected LLM."""

    def __init__(self, client: LLMClient) -> None:
        self._client = client

    async def extract(
        self, ctx: WebsiteContext, documents: list[ProcessedDocument]
    ) -> ExtractionResult:
        """Return claims whose excerpts verifiably occur in the page text."""
        out = ExtractionResult()
        rejected = 0
        for doc in (d for d in documents if d.status == DocumentStatus.PROCESSED):
            system, user = build_extraction_prompt(
                doc.normalized_url, doc.normalized_markdown
            )
            try:
                raw = await self._client.complete_json(system, user)
                batch = LLMClaimBatch.model_validate(json.loads(raw))
            except (ValueError, ValidationError):
                out.notes.append(
                    f"LLM output for {doc.normalized_url} was invalid and ignored"
                )
                continue
            for item in batch.claims:
                corpus = f"{doc.normalized_markdown}\n{doc.title or ''}"
                if not excerpt_in_text(item.evidence_excerpt, corpus):
                    rejected += 1
                    continue
                out.claims.append(
                    ResearchClaim(
                        subject_entity=item.entity_name,
                        subject_type=item.entity_type,
                        scope_domain=ctx.domain,
                        predicate=item.predicate,
                        object_value=item.value,
                        claim_text=f"{item.entity_name} {item.predicate.replace('_', ' ')}: {item.value[:200]}",
                        source_url=doc.normalized_url,
                        source_title=doc.title,
                        source_domain=ctx.domain,
                        source_content_hash=doc.content_hash,
                        evidence_excerpt=item.evidence_excerpt,
                        extraction_method=ExtractionMethod.LLM,
                        confidence_score=0.4,
                        publication_date=doc.published_at,
                    )
                )
        if rejected:
            out.notes.append(
                f"{rejected} LLM claim(s) dropped: excerpt not found in source"
            )
        return out


class StructuredExtractor:
    """Facade: deterministic always, LLM only when enabled *and* a client is injected."""

    def __init__(
        self,
        llm: LLMStructuredExtractor | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._deterministic = DeterministicExtractor()
        self._llm = llm

    async def extract(
        self, ctx: WebsiteContext, documents: list[ProcessedDocument]
    ) -> ExtractionResult:
        """Extract entities and claims for one website."""
        result = self._deterministic.extract(ctx, documents)
        if self._settings.website_research_enable_llm_extraction:
            if self._llm is None:
                result.notes.append(
                    "LLM extraction is enabled but no LLM provider is configured; "
                    "deterministic extraction was used."
                )
            else:
                llm = await self._llm.extract(ctx, documents)
                known = {
                    (c.subject_entity, c.predicate, c.object_value, c.source_url)
                    for c in result.claims
                }
                result.claims += [
                    c
                    for c in llm.claims
                    if (c.subject_entity, c.predicate, c.object_value, c.source_url)
                    not in known
                ]
                result.notes += llm.notes
        return result
