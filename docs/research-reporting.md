# Research reporting

`research/research_report.py` builds structured, evidence-linked reports
(`ResearchReportData`) and renders Markdown from that data. The renderer is a pure
function; **no LLM writes or edits any field**, and fields without evidence are
shown as "not publicly found".

## Report types (`ReportType`)

| Type | Content |
| --- | --- |
| `company_profile` | One section per researched website/company |
| `product_profile` | One row per product: company, category, features, use cases, integrations, official product URL, sources |
| `service_profile` | One row per service: description, deliverables, service URL |
| `technology_profile` | Technologies named on the websites, with documentation URLs and sources |
| `competitor_profile` | Comparison table + company sections for several websites |
| `industry_report` | Same as competitor profile, for industry/category subjects |

The default type follows the request: company -> company profile (competitor
profile when several companies were researched), product -> product profile, etc.
`report_type` can be set explicitly.

## Company section

Overview, official website (+ `verified`/`unverified`), industry, headquarters,
founded year, company size, products, services, main features, technologies *named
on the website*, pricing (each line with amount, currency, interval, plan, source
URL and verification date), target customers, use cases, integrations, strengths,
limitations, competitor relationships, evidence coverage, and source URLs.

Rules enforced by the builder and tests:

* A value is shown only if a claim in `supported`, `partially_supported` or
  `conflicting` status backs it. Conflicts are shown as "conflicting evidence: a |
  b".
* **Strengths and limitations are empty** unless evidence-backed observations
  exist (none are generated today) - they print as "not publicly found". Market
  share, revenue, customer counts and rankings are never produced.
* **Competitor relationships** are not inferred. In competitor/industry reports
  the other researched companies are listed only as "Researched alongside ... (no
  competitive relationship is asserted from evidence)".
* Pricing: `found`, or "not publicly found (checked N pricing page(s))", or "not
  publicly found (no pricing page was crawled)".
* Report-level sections: sources (with official/third-party kind), unverified
  information (unverified ownership, unverified/unsupported claims), conflicting
  information, evidence coverage and notes (including "not found means not found in
  the crawled pages").

## Outputs

* `GET /api/v1/research/{job_id}/report` -> `GeneratedReport` JSON (`data`
  structured report + `markdown`).
* `...?format=markdown` -> `text/markdown`.
* Reports are stored per job and type in `research_reports` (a new report of the
  same type replaces the old one). In the staged flow, the report is regenerated
  from all stored entities/claims after each crawl.

Set `WEBSITE_RESEARCH_ENABLE_REPORTS=false` to skip report generation.
