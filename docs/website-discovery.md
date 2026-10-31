# Website discovery

`research/website_discovery.py` finds candidate websites for a research subject
using the Module 2 SerpApi adapter, validates every URL with `SourceManager`
(`UrlPolicy`), and ranks one candidate per registrable domain. It never invents
a website: every candidate came from a search result and keeps the query,
position and metadata that produced it.

## Query generation

`build_queries` creates at most 12 `(query, target name)` pairs:

| Request | Queries |
| --- | --- |
| `target_names=["Acme"]` (company) | `Acme official website`, `Acme products pricing` |
| `target_names` with product/service/technology | `<name> official website`, `<name> <subject type>` |
| company/product without names | the query, plus `... official website` / `... pricing features` |
| technology / service | the query, plus `... documentation` / `... service provider` |
| industry / competitor / category | the query, plus `top <query> companies` |

`country` and `language` are forwarded to SerpApi (`gl`, `hl`).

> **Industry queries:** "top 10 AI automation companies in India" returns web
> pages, not a clean list of companies. AURORA does not guess company names from
> prose; candidates are the sites that appear in results, all `unverified`. To
> research specific companies, pass `target_names` or `website_urls`. Extracting
> company lists from articles is future (LLM) work.

## Validation, deduplication and ranking

1. All results go through `SourceManager.prepare` (scheme, credentials, private
   IPs, DNS, ports). Rejected URLs are counted, never used.
2. Each candidate's **homepage** is derived from the result URL and validated
   again.
3. Results are grouped by registrable domain (`www.docs.acme.co.in` ->
   `acme.co.in`); one candidate per domain is kept.
4. Score (0-1) = position (max 0.3, decays with rank) + query-term overlap with
   the result text (up to 0.2) + official-evidence bonus (up to 0.4) + 0.05 per
   additional query that returned the domain (max 0.1). Third-party sites are
   capped at 0.2.
5. `selection_reasons` lists every contributing factor; `search_position`,
   `search_query`, positions seen and result URLs are preserved in `metadata`.

Directories, social, review, news and marketplace domains (G2, Crunchbase,
LinkedIn, Wikipedia, ...) are labelled `third_party` and excluded from the
website list (a note says how many). Their raw search results are still stored.

## Official-website assessment

Never "the first result". A website is `verified` only when **both**:

* its domain label equals the requested name, or the name plus a common affix
  (`getacme`, `acmehq`, `acme-ai` style: prefixes `get/try/use/go/...`, suffixes
  `hq/app/io/ai/labs/tech/...`), **and**
* the search result text (title/snippet/displayed link) mentions the name.

Look-alikes such as `acme-reviews.example` or `bestacme.example` are **not**
verified. If only the domain matches, the site stays `unverified` with a reason.
After the homepage is crawled, `confirm_with_homepage` can upgrade `unverified`
to `verified` if the homepage title/heading contains the name and the domain
relates to it. It never downgrades. User-supplied URLs (`website_urls`) are
always `unverified` until homepage confirmation, and without a target name they
stay unverified.

`WebsiteCandidate` fields: `candidate_id`, `website_name`, `domain`,
`homepage_url`, `title`, `description`, `search_query`, `search_position`,
`relevance_score`, `website_type`, `official_status`, `discovery_source`,
`confidence` (confidence in the *classification*, not in company facts),
`discovery_timestamp`, `selection_reasons`, `metadata`.

## Limits

Cross-references from credible third-party sources are only used as ranking
signal ("returned for N queries"); they cannot make a site `verified` on their
own. The third-party list is static and incomplete.

## SerpApi result shape (verified live)
SerpApi's `link` can be a relative Google redirect (`/goto?url=...`). When `link` is
not an absolute, non-Google http(s) URL, AURORA uses the host in `displayed_link`
and records `metadata.destination_source = "displayed_link"`; only the site root is
known in that case. `source` is a publisher name and is kept as `metadata.publisher`.
