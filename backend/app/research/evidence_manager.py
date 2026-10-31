"""Evidence management: verify claims against retrieved content and find conflicts.

A claim is only ``supported`` when its excerpt is found in the stored content of
the page it cites. Search snippets or text we never retrieved give ``unverified``;
an excerpt missing from the page gives ``unsupported``. Marketing-style claims,
third-party sources, unverified ownership, bare mentions and LLM-extracted
claims are capped at ``partially_supported``. Conflicting values are all kept.
Nothing here is influenced by how confident an extractor was.
"""

import logging
import re
from collections import defaultdict

from app.core.logging import log_event
from app.research.domains import registrable_domain
from app.research.text_utils import excerpt_in_text, squash
from app.schemas.intelligence import (
    ClaimConflict,
    EntityType,
    EvidenceCoverage,
    ExtractionMethod,
    OfficialStatus,
    PricingInfo,
    ProcessedDocument,
    ResearchClaim,
    ResolvedEntity,
    SourceKind,
    VerificationStatus,
)

logger = logging.getLogger(__name__)

#: Predicates with one true value per entity; different values conflict.
SINGLE_VALUED = frozenset({"headquarters", "founded_year", "company_size"})
#: Subjective or promotional statements: existence of the statement, not truth.
MARKETING_PREDICATES = frozenset(
    {"description", "target_customer", "industry", "use_case", "mentions_technology"}
)
_PLAN_SPLIT = re.compile(r"^(.*?):\s")


def _corpus(doc: ProcessedDocument) -> str:
    return (
        f"{doc.normalized_markdown}\n{doc.title or ''}\n"
        f"{doc.metadata.get('description') or ''}"
    )


class EvidenceManager:
    """Validate claims and compute evidence coverage."""

    def validate(
        self,
        claims: list[ResearchClaim],
        documents: list[ProcessedDocument],
        official_status: dict[str, OfficialStatus],
    ) -> tuple[list[ResearchClaim], list[ClaimConflict]]:
        """Return verified copies of ``claims`` plus the conflicts found.

        ``official_status`` maps registrable domains to the website's official
        status as established during discovery.
        """
        corpora = {d.normalized_url: _corpus(d) for d in documents}
        verified = [self._verify_one(c, corpora, official_status) for c in claims]
        conflicts = self._conflicts(verified)
        log_event(
            logger,
            "evidence_validated",
            claims=len(verified),
            conflicts=len(conflicts),
            supported=sum(
                c.verification_status == VerificationStatus.SUPPORTED for c in verified
            ),
            unsupported=sum(
                c.verification_status == VerificationStatus.UNSUPPORTED
                for c in verified
            ),
        )
        return verified, conflicts

    # -- single claim -----------------------------------------------------------

    def _verify_one(
        self,
        claim: ResearchClaim,
        corpora: dict[str, str],
        official_status: dict[str, OfficialStatus],
    ) -> ResearchClaim:
        notes = list(claim.notes)
        kind = self._source_kind(claim, official_status)
        text = corpora.get(claim.source_url)
        if text is None:
            return claim.model_copy(
                update={
                    "verification_status": VerificationStatus.UNVERIFIED,
                    "source_kind": kind,
                    "notes": notes + ["source page content was not retrieved"],
                }
            )
        if not excerpt_in_text(claim.evidence_excerpt, text):
            return claim.model_copy(
                update={
                    "verification_status": VerificationStatus.UNSUPPORTED,
                    "source_kind": kind,
                    "notes": notes
                    + ["evidence excerpt not found in the retrieved page"],
                }
            )
        status = VerificationStatus.SUPPORTED
        if kind != SourceKind.OFFICIAL:
            status = VerificationStatus.PARTIALLY_SUPPORTED
            notes.append(
                "source is third-party"
                if kind == SourceKind.THIRD_PARTY
                else "official ownership of the source is not verified"
            )
        if claim.predicate in MARKETING_PREDICATES:
            status = VerificationStatus.PARTIALLY_SUPPORTED
            notes.append(
                "vendor-stated; the statement is recorded, not independently verified"
            )
        if claim.extraction_method == ExtractionMethod.LLM:
            status = VerificationStatus.PARTIALLY_SUPPORTED
            notes.append(
                "LLM-extracted; excerpt verified against the page, claim not independently verified"
            )
        return claim.model_copy(
            update={"verification_status": status, "source_kind": kind, "notes": notes}
        )

    @staticmethod
    def _source_kind(
        claim: ResearchClaim, official_status: dict[str, OfficialStatus]
    ) -> SourceKind:
        status = official_status.get(registrable_domain(claim.source_domain))
        if status == OfficialStatus.VERIFIED:
            return SourceKind.OFFICIAL
        if status == OfficialStatus.THIRD_PARTY:
            return SourceKind.THIRD_PARTY
        return SourceKind.UNKNOWN

    # -- conflicts ----------------------------------------------------------------

    def _conflicts(self, claims: list[ResearchClaim]) -> list[ClaimConflict]:
        groups: dict[tuple[str, str], list[ResearchClaim]] = defaultdict(list)
        for claim in claims:
            if claim.verification_status in (
                VerificationStatus.UNSUPPORTED,
                VerificationStatus.UNVERIFIED,
            ):
                continue
            key = self.conflict_key(claim)
            if key is not None:
                groups[key].append(claim)
        conflicts: list[ClaimConflict] = []
        for (_, predicate), group in groups.items():
            values = {self._comparable(c) for c in group}
            sources = {c.source_url for c in group}
            if len(values) < 2:
                continue
            if predicate.startswith("price:") and len(sources) < 2:
                continue  # one page showing several tiers/intervals is not a conflict
            ids = []
            for claim in group:
                claim.verification_status = VerificationStatus.CONFLICTING
                claim.notes.append(
                    "conflicts with other evidence; all values are preserved"
                )
                ids.append(claim.claim_id)
            official = [c for c in group if c.source_kind == SourceKind.OFFICIAL]
            if official:
                for c in official:
                    c.notes.append("official source; preferred for company-owned facts")
            conflicts.append(
                ClaimConflict(
                    subject_entity=group[0].subject_entity,
                    predicate=predicate,
                    values=sorted({c.object_value for c in group}),
                    claim_ids=ids,
                )
            )
        return conflicts

    @staticmethod
    def conflict_key(claim: ResearchClaim) -> tuple[str, str] | None:
        subject = f"{claim.scope_domain or ''}|{squash(claim.subject_entity)}"
        if claim.predicate in SINGLE_VALUED:
            return subject, claim.predicate
        if claim.predicate == "price":
            match = _PLAN_SPLIT.match(claim.object_value)
            plan = squash(match.group(1)) if match else ""
            return subject, f"price:{plan}"
        return None

    @staticmethod
    def _comparable(claim: ResearchClaim) -> str:
        return squash(claim.object_value)

    # -- coverage -----------------------------------------------------------------

    @staticmethod
    def coverage(
        claims: list[ResearchClaim], entities: list[ResolvedEntity]
    ) -> EvidenceCoverage:
        """Share of claims backed by verified evidence, plus entity coverage."""
        count = {s: 0 for s in VerificationStatus}
        for claim in claims:
            count[claim.verification_status] += 1
        total = len(claims)
        backed = (
            count[VerificationStatus.SUPPORTED]
            + count[VerificationStatus.PARTIALLY_SUPPORTED]
        )
        with_support = {
            c.entity_id
            for c in claims
            if c.entity_id
            and c.verification_status
            in (VerificationStatus.SUPPORTED, VerificationStatus.PARTIALLY_SUPPORTED)
        }
        return EvidenceCoverage(
            total_claims=total,
            supported=count[VerificationStatus.SUPPORTED],
            partially_supported=count[VerificationStatus.PARTIALLY_SUPPORTED],
            conflicting=count[VerificationStatus.CONFLICTING],
            unsupported=count[VerificationStatus.UNSUPPORTED],
            unverified=count[VerificationStatus.UNVERIFIED],
            coverage_ratio=round(backed / total, 3) if total else 0.0,
            entities_total=len(entities),
            entities_with_supported_claim=len(
                {e.entity_id for e in entities} & with_support
            ),
            pricing_found=any(
                c.predicate == "price"
                for c in claims
                if c.verification_status
                in (
                    VerificationStatus.SUPPORTED,
                    VerificationStatus.PARTIALLY_SUPPORTED,
                    VerificationStatus.CONFLICTING,
                )
            ),
        )

    @staticmethod
    def pricing_gaps(entities: list[ResolvedEntity]) -> list[str]:
        """Describe stated prices that lack currency, billing period or plan."""
        gaps: list[str] = []
        for entity in entities:
            if entity.entity_type != EntityType.PRICING:
                continue
            data = entity.attributes
            try:
                info = PricingInfo.model_validate(
                    {**data, "source_url": data.get("source_url", "")}
                )
            except ValueError:
                continue
            missing = [
                label
                for label, value in (
                    ("currency", info.currency),
                    ("billing period", info.billing_interval),
                    ("plan name", info.plan_name),
                )
                if not value
            ]
            if missing:
                gaps.append(f"{entity.canonical_name}: missing {', '.join(missing)}")
        return gaps
