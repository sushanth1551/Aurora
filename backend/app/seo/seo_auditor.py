"""On-page SEO auditor (deterministic checks)."""

from app.schemas.seo import SeoAnalysisResponse


class SeoAuditor:
    """Audit content or pages against SEO rules."""

    async def audit(self, content: str) -> SeoAnalysisResponse:
        """Return audit findings for ``content``."""
        # TODO: title/meta length, headings, links, readability checks.
        raise NotImplementedError("SEO auditing is not implemented.")
