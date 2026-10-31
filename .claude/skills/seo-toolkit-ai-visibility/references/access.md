# Access evidence

Read for crawler audits. Profiles below were checked against operator documentation
on 2026-09-05. They describe audit subjects, not runtime dependencies. Recheck the
linked documentation before recommending policy changes; if unavailable, disclose
that the profile is dated. Do not hard-code IP ranges.

| Token | Purpose | Source |
| --- | --- | --- |
| OAI-SearchBot | Search discovery | [Crawler documentation](https://developers.openai.com/api/docs/bots) |
| GPTBot | Model training | [Crawler documentation](https://developers.openai.com/api/docs/bots) |
| ChatGPT-User | User-triggered retrieval; robots rules may not apply | [Crawler documentation](https://developers.openai.com/api/docs/bots) |
| Claude-SearchBot | Search discovery | [Crawler documentation](https://support.claude.com/en/articles/8896518-does-anthropic-crawl-data-from-the-web-and-how-can-site-owners-block-the-crawler) |
| ClaudeBot | Model development | [Crawler documentation](https://support.claude.com/en/articles/8896518-does-anthropic-crawl-data-from-the-web-and-how-can-site-owners-block-the-crawler) |
| Claude-User | User-triggered retrieval | [Crawler documentation](https://support.claude.com/en/articles/8896518-does-anthropic-crawl-data-from-the-web-and-how-can-site-owners-block-the-crawler) |
| PerplexityBot | Search discovery, not foundation-model training | [Crawler documentation](https://docs.perplexity.ai/docs/resources/perplexity-crawlers) |
| Perplexity-User | User-triggered retrieval; generally ignores robots rules | [Crawler documentation](https://docs.perplexity.ai/docs/resources/perplexity-crawlers) |
| Googlebot | Search, including AI answer surfaces | [Search documentation](https://developers.google.com/search/docs/appearance/ai-features) |

Search eligibility does not require training permission. Google-Extended is not the
control for AI features in Search. Special AI markup or llms.txt is not required for
those features. Avoid transferring one operator's rules to another.

## Collect and interpret

Use the bundled robots script for group extraction, http-trace for status/redirects,
and page-signals for source HTML. The robots script is a parser, not a URL permission
evaluator. Its `selected` array includes wildcard groups alongside exact groups;
use the original `groups` and raw file to determine applicability, not the union.

Apply [REP rules](https://www.rfc-editor.org/rfc/rfc9309.html): combine groups matching
the product token case-insensitively; use wildcard groups only if no explicit group
matches. Blank lines do not terminate a group in REP; verify raw input if the parser
splits it. Match URL paths case-sensitively, handling percent encoding, wildcards and
end anchors; the longest applicable rule wins, with Allow winning equivalent ties.
For ambiguous or unsupported syntax, leave the verdict unresolved rather than guess.
Record the exact rule and path behind any inferred permission.

Check the response status before interpreting fetched text as robots.txt. A redirect,
error page, HTML challenge or missing file is not an empty valid policy. Explain
status-specific handling from REP; tool failure is not evidence of allowed access.
Files require an explicit origin association before applying rules to a URL.

Report declared permission as `inferred` from observed rules. Actual crawler access
requires matching server/CDN logs with operator-verified identity and timestamps.
A spoofed user-agent string or a successful toolkit fetch proves neither identity
nor crawler access. Preserve 403/429, WAF, rendering, noindex and snippet restrictions
as separate findings. Do not bypass protections or modify access policies during an audit.
