"""Confidence with separate, explicit components (no single opaque score).

Rubric
------
* ``extraction``        mean extraction confidence of the cited claims.
* ``source_credibility`` mean credibility score of the cited sources.
* ``evidence_support``  mean directness of the cited evidence: supported 1.0,
  partially supported 0.7, conflicting 0.5, unverified 0.4, unsupported 0.1,
  bare source excerpts 0.4.
* ``analytical``        the LLM's stated level, *capped* by what the evidence
  allows: HIGH needs >=3 items from >=2 domains and support >= 0.7; MEDIUM needs
  >=2 items; otherwise LOW. A model can lower but never raise the cap.
* ``overall``           the weakest of the numeric components (>=0.7 high,
  >=0.45 medium, else low) and the analytical level.
"""

from collections.abc import Iterable

from app.intelligence.models import Confidence, EvidenceItem, EvidenceKind, Level
from app.schemas.intelligence import VerificationStatus

_SUPPORT = {
    VerificationStatus.SUPPORTED: 1.0,
    VerificationStatus.PARTIALLY_SUPPORTED: 0.7,
    VerificationStatus.CONFLICTING: 0.5,
    VerificationStatus.UNVERIFIED: 0.4,
    VerificationStatus.UNSUPPORTED: 0.1,
}
_ORDER = {Level.LOW: 0, Level.MEDIUM: 1, Level.HIGH: 2}
_BY_ORDER = {v: k for k, v in _ORDER.items()}


def _level(value: float) -> Level:
    return Level.HIGH if value >= 0.7 else Level.MEDIUM if value >= 0.45 else Level.LOW


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 3) if values else None


def compute_confidence(
    cited: Iterable[EvidenceItem], stated: Level = Level.LOW
) -> Confidence:
    """Confidence breakdown for a statement citing ``cited`` evidence."""
    items = list(cited)
    if not items:
        return Confidence(rubric="no cited evidence: analytical confidence is low")
    extraction = _mean(
        [i.extraction_confidence for i in items if i.extraction_confidence is not None]
    )
    credibility = _mean([i.credibility.score for i in items])
    support = _mean(
        [
            _SUPPORT[i.verification_status] if i.kind is EvidenceKind.CLAIM else 0.4
            for i in items
        ]
    )
    domains = {i.source_domain for i in items}
    if len(items) >= 3 and len(domains) >= 2 and (support or 0) >= 0.7:
        cap = Level.HIGH
    elif len(items) >= 2:
        cap = Level.MEDIUM
    else:
        cap = Level.LOW
    analytical = _BY_ORDER[min(_ORDER[stated], _ORDER[cap])]
    parts = [_ORDER[analytical]] + [
        _ORDER[_level(v)] for v in (extraction, credibility, support) if v is not None
    ]
    return Confidence(
        extraction=extraction,
        source_credibility=credibility,
        evidence_support=support,
        analytical=analytical,
        overall=_BY_ORDER[min(parts)],
        rubric=f"{len(items)} evidence item(s) from {len(domains)} domain(s); "
        f"model stated {stated.value}, evidence cap {cap.value}",
    )
