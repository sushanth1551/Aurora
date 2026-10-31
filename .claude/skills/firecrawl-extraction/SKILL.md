---
name: firecrawl-extraction
description: "Specify safe extraction of web content through Firecrawl. Planning guidance only; AURORA provider integrations are not implemented yet."
---

# firecrawl-extraction

**Description:** Specify safe extraction of web content through Firecrawl.

## Purpose
Define a reliable adapter workflow for crawling and normalizing web pages. The
Firecrawl integration is a planned boundary, not an operational feature yet.

## When to use it
Use when a configured Firecrawl client is available and the requested URLs are
within the permitted research scope.

## Required inputs
- URL or bounded URL set.
- Extraction fields and crawl limits.
- Valid provider configuration supplied through environment secrets.

## Execution workflow
1. Validate URL scheme, destination, and scope before dispatch.
2. Apply provider limits, timeouts, and access policies.
3. Extract only requested content and preserve URL and retrieval metadata.
4. Normalize output while retaining the original source reference.
5. Return extraction status and any warnings.

## Expected outputs
Normalized page content, source URL, retrieval timestamp, and extraction
warnings suitable for downstream indexing.

## Error handling
Surface invalid URLs, provider failures, rate limits, and partial results.
Never silently substitute scraped or cached content for failed extraction.

## Quality requirements
Protect against unsafe URL targets, respect robots and applicable terms, avoid
secret logging, and retain provenance for every extracted document.
