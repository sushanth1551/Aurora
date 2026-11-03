# SEO execution report: Next Steps USMLE (audit)

> This report does not predict rankings or traffic. Expected outcomes describe what each change is meant to improve; verify them with the stated method.

## Execution summary

- **Website:** Next Steps USMLE (https://nextstepsusmle.com)
- **Execution:** `20261103-084638-audit` · started 2026-11-03 08:46 UTC · finished 2026-11-03 08:48 UTC
- **Status:** Partially completed: some steps ran without all their data (see blockers).
- **Pages analysed:** 7
- **Reports processed:** 0

First live /seo-audit after the baseline import: free seo-data checks of robots, sitemap, key pages, two retired slugs and a soft-404 probe. Nothing from the 2026-11-02 branch is live yet; both retired slugs serve the homepage. PageSpeed quota exhausted (Core Web Vitals not measured).

Pages analysed:
- https://nextstepsusmle.com/
- https://nextstepsusmle.com/mentorship
- https://nextstepsusmle.com/blog
- https://nextstepsusmle.com/success-stories
- https://nextstepsusmle.com/this-page-does-not-exist-aurora-check
- https://nextstepsusmle.com/blog/usmle-step-2-ck-passing-score-step-by-step-guidance
- https://nextstepsusmle.com/blog/2024-match-pathways-application-reopened-for-imgs

### Main findings

- **High**: Unknown URLs return HTTP 200 with the homepage canonical (soft 404)
- **High**: Ten SEO commits from 2026-11-02 are still not live
- **High**: Homepage title claims '#1 USMLE Preparation Platform' without support
- **Medium**: 30 of 40 images on /mentorship have no alt text
- **Medium**: 47 of 69 blog titles were longer than 60 characters
- **Medium**: Mentorship meta description is 178 characters (limit 160)
- **Medium**: Old ECFMG pathways slug serves the homepage instead of a 301

### Compared with previous executions

| Change | Count | Finding keys |
| --- | --- | --- |
| New | 1 | broken-link:facebook-profile |
| Recurring | 13 | blog-index-metadata:/blog, images-missing-alt:/mentorship, long-titles:blog, meta-description-length:/mentorship, missing-redirect:2024-match-pathways-application-reopened-for-imgs, missing-redirect:usmle-step-2-ck-passing-score-step-by-step-guidance, robots-ai-crawlers:site, soft-404:site, thin-content:/success-stories, title-length:/mentorship, undeployed-seo-changes:update-blog-titles, unsupported-claim:/, unsupported-claim:/mentorship |
| Reopened | 0 | – |
| Resolved | 0 | – |
| Not rechecked (still open) | 21 | blogposting-citation-missing:site, brand-name-inconsistent:site, cannibalization:step-2-ck-score, content-gap:free-120-and-free-qbanks, content-gap:nbme-self-assessments, content-gap:step-2-ck-passing-score, content-gap:usmle-vs-neet-pg, cover-images-old-titles:blog, eeat-first-hand-outcomes:site, fee-intent-owner:india, hidden-faq:blog, image-dimensions-missing:/, images-missing-alt:/step-1-2-ck, knowsabout-narrow:site, low-ctr:/blog/what-is-oet-exam-how-much-does-it-costs, orphan-posts:blog, rank-outside-top10:priority-keywords, reindex-pending:blog, reviewer-schema-missing-sameas:site, security-headers-missing:site, thin-content:blog-two-posts |

Marked implemented but still observed (check deployment): `long-titles:blog`

## Skills used

| Skill | Why selected | What it analysed | Actions | Output | Version |
| --- | --- | --- | --- | --- | --- |
| `seo-learning-and-memory` | Plan step load-knowledge / store-and-report | 35 baseline findings, 1 rejected recommendation, 1 previous execution | loaded context and plan; reused finding keys; recorded this run | execution record and report | 24faa3e9da63 |
| `website-seo-discovery` | Plan step discovery | robots.txt, sitemap (82 URLs), key pages, repository branch state | robots and sitemap checks; deployment check of a retitled post; link check of the homepage | deployment and link findings | 0f58d3185141 |
| `seo-toolkit-technical-audit` | Plan step technical | soft-404 probe, two retired slugs, robots | status/canonical checks | soft-404 and redirect findings re-observed | 4d2fb633a222 |
| `seo-toolkit-page-audit` | Plan step page-audit | /mentorship, /blog, /success-stories, homepage | metadata, headings, images, schema | on-page findings re-observed | 07ad64f0b62a |
| `seo-quality-audit` | Plan step content-quality | claims on / and /mentorship; /success-stories depth | claims and thin-content checks | claims and thin-content findings | 44030955307d |
| `seo-toolkit-prioritization` | Plan step prioritize | 15 observed findings | Critical/High/Medium/Low ordering | priorities | bf12c28418d8 |

## Tools and integrations used

| Tool / source | Kind | Status | Calls | Note |
| --- | --- | --- | --- | --- |
| seo-data check_robots_txt | mcp | success | 1 | – |
| seo-data analyze_sitemap | mcp | success | 1 | 82 URLs, lastmod 2024-11-18..2026-11-01 |
| seo-data audit_page | mcp | success | 6 | – |
| seo-data check_links | mcp | success | 1 | 25 links checked |
| seo-data pagespeed | mcp | unavailable | 1 | HTTP 429: daily PageSpeed quota exceeded |
| git (read-only) on nextsteps-usmle | git | success | 1 | – |
| AURORA GSC API | aurora | skipped | 0 | no new Search Console pull in this run |
| SerpApi / Firecrawl | api | skipped | 0 | no credit budget requested |

Unavailable or failed: seo-data pagespeed (unavailable: HTTP 429: daily PageSpeed quota exceeded)

## Changes performed

**Files created**

None.

**Files modified**

None.

**SEO changes implemented**

None.

**Knowledge records created**

- `seo-intelligence/sites/nextsteps/`: Execution record, finding ledger update and report

**Skill files updated**

None.

## Recommendations

### Critical improvements

None in this execution.

### High-priority improvements

#### Unknown URLs return HTTP 200 with the homepage canonical (soft 404)

- **Key:** `soft-404:site` · **Category:** technical/status-codes · **Evidence state:** observed
- **Evidence:** Downloads/nextstepsusmle-SEO-Audit.pdf (claude-seo seo-audit, 2026-11-01): /this-page-does-not-exist-xyz returned 200 with homepage canonical; nextsteps-usmle repository (read-only check, 2026-11-03): frontend/nginx-spa.conf: location / falls back to /index.html; the /blog/ =404 block was 'still not deployed' per the tracker (2026-09-11); seo-data audit_page (2026-11-03): /this-page-does-not-exist-aurora-check returns HTTP 200 with the homepage title and canonical https://nextstepsusmle.com/ (https://nextstepsusmle.com/this-page-does-not-exist-aurora-check)
- **Affected URL(s):** https://nextstepsusmle.com/this-page-does-not-exist-aurora-check
- **SEO impact:** Junk and retired URLs are served as homepage duplicates; Google reports them as soft 404s.
- **Priority:** High
- **Recommended action:** Serve a real 404 for unknown paths (nginx try_files ... =404 plus a noindex 404 page) and deploy the /blog/ =404 block.
- **Implementation status:** open
- **Expected outcome:** Unknown URLs return 404.
- **Verification method:** audit_page on a random URL returns status 404.

#### Ten SEO commits from 2026-11-02 are still not live

- **Key:** `undeployed-seo-changes:update-blog-titles` · **Category:** process/deployment · **Evidence state:** observed
- **Evidence:** nextsteps-usmle repository (read-only check, 2026-11-03): git rev-list main..update-blog-titles = 10 commits (3c54aa5..13f1cb3); origin/main is a70b3f7 (2026-10-23); seo-data audit_page (2026-11-03): /blog lists 'How to Apply for the USMLE from India (2026): Registration, Fees & Test Centres'; branch update-blog-titles (3c54aa5) has 'How to Apply for USMLE from India: 2027 Registration Steps' (https://nextstepsusmle.com/blog); git (read-only): main..update-blog-titles = 10 commits, not pushed
- **Affected URL(s):** https://nextstepsusmle.com/blog
- **SEO impact:** Title, on-page, word-count and FAQ fixes have no effect until deployed.
- **Priority:** High
- **Recommended action:** Owner reviews, pushes and merges the branch, then requests reindexing of the changed URLs.
- **Implementation status:** open
- **Expected outcome:** The changed titles and sections are live and crawlable.
- **Verification method:** audit_page on 3 retitled posts after deploy.

#### Homepage title claims '#1 USMLE Preparation Platform' without support

- **Key:** `unsupported-claim:/` · **Category:** content/claims · **Evidence state:** observed
- **Evidence:** Downloads/nextstepsusmle-SEO-Audit.pdf (claude-seo seo-audit, 2026-11-01): '#1' in homepage title flagged as unsupported; nextsteps-usmle repository (read-only check, 2026-11-03): frontend/src/pages/Index.tsx title '#1 USMLE Preparation Platform | Next Steps USMLE'; description says 'trusted by 700+ students'; seo-data audit_page (2026-11-03): title '#1 USMLE Preparation Platform | Next Steps USMLE'; description 'trusted by 700+ students' (https://nextstepsusmle.com/)
- **Affected URL(s):** https://nextstepsusmle.com/
- **SEO impact:** Unsupported superlatives weaken trust on a medical-education site and can breach ad policies.
- **Priority:** High
- **Recommended action:** Use a brand-first, factual title; keep '700+' only with a substantiation note.
- **Implementation status:** open
- **Expected outcome:** A trustworthy title that still contains the main query.
- **Verification method:** Inspect the deployed title; watch homepage CTR for 28 days.

### Medium-priority improvements

#### 30 of 40 images on /mentorship have no alt text

- **Key:** `images-missing-alt:/mentorship` · **Category:** on-page/images · **Evidence state:** observed
- **Evidence:** SEOWORK/SEO_Competitor_Gap_Analysis.xlsx (seo-data MCP + WebSearch, 2026-11-01): audit_page: 30/40 images missing alt; seo-data audit_page (2026-11-03): 30 of 40 images missing alt (student chat thumbnails, residency logos, YouTube thumbnails) (https://nextstepsusmle.com/mentorship)
- **Affected URL(s):** https://nextstepsusmle.com/mentorship
- **SEO impact:** Proof images (score reports, residency logos) carry no context for search or screen readers.
- **Priority:** Medium
- **Recommended action:** Alt text naming the student/exam/score or hospital; alt="" for decorative images.
- **Implementation status:** open
- **Expected outcome:** Images have meaningful alt text.
- **Verification method:** audit_page missing_alt = 0 (decorative excepted).

#### 47 of 69 blog titles were longer than 60 characters

- **Key:** `long-titles:blog` · **Category:** on-page/metadata · **Evidence state:** observed
- **Evidence:** Downloads/nextstepsusmle-SEO-Blog-Report-2026-11-02.pdf (69-post crawl + SerpApi US): 47 of 69 titles over 60 characters; nextsteps-usmle repository (read-only check, 2026-11-03): fixed on branch update-blog-titles in 3c54aa5 (+1b24309, c705eb1); not deployed; seo-data audit_page (2026-11-03): /blog still shows pre-fix titles (for example the 79-character 'How to Apply for the USMLE from India (2026): ...') (https://nextstepsusmle.com/blog)
- **Affected URL(s):** site-wide
- **SEO impact:** Truncated titles hide the query and the year.
- **Priority:** Medium
- **Recommended action:** Deploy the branch; recheck strong pages after 2-3 weeks and revert any that drop.
- **Implementation status:** implemented
- **Expected outcome:** Titles display in full.
- **Verification method:** audit_page on 5 retitled posts after deploy; GSC CTR by page.

#### Mentorship meta description is 178 characters (limit 160)

- **Key:** `meta-description-length:/mentorship` · **Category:** on-page/metadata · **Evidence state:** observed
- **Evidence:** SEOWORK/SEO_Competitor_Gap_Analysis.xlsx (seo-data MCP + WebSearch, 2026-11-01): audit_page: meta description 178 chars; Downloads/nextstepsusmle-SEO-Audit.pdf (claude-seo seo-audit, 2026-11-01): /mentorship description 178; seo-data audit_page (2026-11-03): meta_description_length 178 (https://nextstepsusmle.com/mentorship)
- **Affected URL(s):** https://nextstepsusmle.com/mentorship
- **SEO impact:** Google truncates the snippet on the main conversion page.
- **Priority:** Medium
- **Recommended action:** Rewrite to 150-160 characters with the main query first and without the 95% claim.
- **Implementation status:** open
- **Expected outcome:** The whole description can show in results.
- **Verification method:** audit_page reports <= 160 characters.

#### Old ECFMG pathways slug serves the homepage instead of a 301

- **Key:** `missing-redirect:2024-match-pathways-application-reopened-for-imgs` · **Category:** technical/redirects · **Evidence state:** observed
- **Evidence:** nextsteps-usmle repository (read-only check, 2026-11-03): post renamed to ecfmg-2027-pathways-for-imgs (9231df8/a386145); no rule for the old slug in blog-redirects.conf; tracker: 'needs 301 redirect'; live server state not checked; seo-data audit_page (2026-11-03): returns HTTP 200 with the homepage title and canonical https://nextstepsusmle.com/; no redirect (https://nextstepsusmle.com/blog/2024-match-pathways-application-reopened-for-imgs)
- **Affected URL(s):** https://nextstepsusmle.com/blog/2024-match-pathways-application-reopened-for-imgs
- **SEO impact:** Links to the renamed post land on a homepage duplicate.
- **Priority:** Medium
- **Recommended action:** Add a 301 to /blog/ecfmg-2027-pathways-for-imgs.
- **Implementation status:** open
- **Expected outcome:** One-hop 301.
- **Verification method:** audit_page shows a 301 redirect.

#### Retired Step 2 CK passing-score slug serves the homepage instead of a 301

- **Key:** `missing-redirect:usmle-step-2-ck-passing-score-step-by-step-guidance` · **Category:** technical/redirects · **Evidence state:** observed
- **Evidence:** nextsteps-usmle repository (read-only check, 2026-11-03): no rule for this slug in blog-redirects.conf; blog-reindex-tracker.md (retired 2026-09-11) says the 301 to /blog/how-to-score-280-on-usmle-step-2-ck 'still needs to be applied'; live server state not checked; seo-data audit_page (2026-11-03): returns HTTP 200 with the homepage title and canonical https://nextstepsusmle.com/; no redirect (https://nextstepsusmle.com/blog/usmle-step-2-ck-passing-score-step-by-step-guidance)
- **Affected URL(s):** https://nextstepsusmle.com/blog/usmle-step-2-ck-passing-score-step-by-step-guidance
- **SEO impact:** The retired URL's links and history are lost and it shows as a soft 404.
- **Priority:** Medium
- **Recommended action:** Add the 301 to /blog/how-to-score-280-on-usmle-step-2-ck on the server.
- **Implementation status:** open
- **Expected outcome:** One-hop 301 to the merged post.
- **Verification method:** audit_page shows a 301 redirect.

#### Success Stories page has 128 words and no H2 sections

- **Key:** `thin-content:/success-stories` · **Category:** content/thin · **Evidence state:** observed
- **Evidence:** Downloads/nextstepsusmle-SEO-Audit.pdf (claude-seo seo-audit, 2026-11-01): /success-stories ~300 words; description 122 chars; seo-data audit_page (2026-11-03): word_count 128; h2 none; banner image without alt; description says stories include AMC exams (https://nextstepsusmle.com/success-stories)
- **Affected URL(s):** https://nextstepsusmle.com/success-stories
- **SEO impact:** The site's proof page is nearly empty; misses an E-E-A-T opportunity.
- **Priority:** Medium
- **Recommended action:** Add real, consented outcomes (exam, score band, match result, year); fix the description to match the USMLE focus.
- **Implementation status:** open
- **Expected outcome:** A substantive proof page.
- **Verification method:** audit_page word count and headings.

#### Mentorship page uses '95% success rate' and '700+ students' claims

- **Key:** `unsupported-claim:/mentorship` · **Category:** content/claims · **Evidence state:** observed
- **Evidence:** Downloads/nextstepsusmle-SEO-Audit.pdf (claude-seo seo-audit, 2026-11-01): '95% success rate' and '700+ students' flagged as unsupported; nextsteps-usmle repository (read-only check, 2026-11-03): Mentorship.tsx meta description and soft/Closing.tsx stats; an asterisk footnote exists for the 95% claim (added for Google Ads policy); seo-data audit_page (2026-11-03): meta description ends 'a proven 95% success rate. Book your slot today.' (https://nextstepsusmle.com/mentorship)
- **Affected URL(s):** https://nextstepsusmle.com/mentorship
- **SEO impact:** A claim in the snippet cannot carry its footnote.
- **Priority:** Medium
- **Recommended action:** Keep the 95% claim on the page with its footnote; remove it from the meta description.
- **Implementation status:** open
- **Expected outcome:** Claims are substantiated where they appear.
- **Verification method:** audit_page meta description.

### Future opportunities (low priority)

#### Blog index has a 23-character title, a 127-character description and no JSON-LD

- **Key:** `blog-index-metadata:/blog` · **Category:** on-page/metadata · **Evidence state:** observed
- **Evidence:** Downloads/nextstepsusmle-SEO-Audit.pdf (claude-seo seo-audit, 2026-11-01): /blog title 23 chars, description 127; SEOWORK/SEO_Competitor_Gap_Analysis.xlsx (seo-data MCP + WebSearch, 2026-11-01): audit_page: /blog has no JSON-LD; seo-data audit_page (2026-11-03): title 'Blog — Next Steps USMLE' (23); description 127; schema_types [] (https://nextstepsusmle.com/blog)
- **Affected URL(s):** https://nextstepsusmle.com/blog
- **SEO impact:** The blog hub is weakly described.
- **Priority:** Low
- **Recommended action:** Descriptive title/description; Blog + BreadcrumbList JSON-LD.
- **Implementation status:** open
- **Expected outcome:** A clearer hub page.
- **Verification method:** audit_page on /blog.

#### Footer Facebook link answers HTTP 400 to the link checker

- **Key:** `broken-link:facebook-profile` · **Category:** links/broken · **Evidence state:** inferred
- **Evidence:** seo-data check_links (2026-11-03): https://www.facebook.com/nextstepsUSMLE/ status 400 (1 of 25 links) (https://nextstepsusmle.com/)
- **Affected URL(s):** https://nextstepsusmle.com/
- **SEO impact:** Probably Facebook blocking automated checks rather than a dead page.
- **Priority:** Low
- **Recommended action:** Open the link in a browser; fix the URL only if it is really broken.
- **Implementation status:** open
- **Expected outcome:** Social profile link works for visitors.
- **Verification method:** Manual browser check.

#### robots.txt has no explicit policy for AI crawlers

- **Key:** `robots-ai-crawlers:site` · **Category:** technical/robots · **Evidence state:** observed
- **Evidence:** nextstepsusmle.com-audit/FULL-AUDIT-REPORT.md (single-page audit, 2026-09-21): open item [Low]: no AI-bot rules; nextsteps-usmle repository (read-only check, 2026-11-03): frontend/public/robots.txt: Allow / with payment/auth/api disallows only; seo-data check_robots_txt (2026-11-03): User-agent: * Allow: / with payment/auth/api disallows; sitemap declared; no AI-crawler groups
- **Affected URL(s):** site-wide
- **SEO impact:** Default allow is fine; an explicit policy documents the owner's choice.
- **Priority:** Low
- **Recommended action:** Decide per AI crawler and write it in robots.txt.
- **Implementation status:** open
- **Expected outcome:** A deliberate AI-crawler policy.
- **Verification method:** check_robots_txt with each AI user agent.

#### Mentorship title is 63 characters (limit 60)

- **Key:** `title-length:/mentorship` · **Category:** on-page/metadata · **Evidence state:** observed
- **Evidence:** Downloads/nextstepsusmle-SEO-Audit.pdf (claude-seo seo-audit, 2026-11-01): /mentorship title 67 characters; seo-data audit_page (2026-11-03): title_length 63 ('Next Steps — Crack USMLE Step 1 & Step 2 CK with 1:1 Mentorship') (https://nextstepsusmle.com/mentorship)
- **Affected URL(s):** https://nextstepsusmle.com/mentorship
- **SEO impact:** The end of the title is cut in results.
- **Priority:** Low
- **Recommended action:** Shorten to <= 60 characters, for example drop 'Next Steps — '.
- **Implementation status:** open
- **Expected outcome:** The full title shows in results.
- **Verification method:** audit_page reports <= 60 characters.

## Learning summary

**What this execution learned**

- Retired blog slugs serve the homepage with HTTP 200, so the /blog/ =404 nginx block is still not deployed.
- /success-stories has shrunk to 128 words (the 2026-11-01 report saw about 300).

**Historical information reused**

- 35 baseline findings and their keys
- owner decision: fees in dollars only
- editorial rules from the site config

**Patterns identified**

- Fixes stall between code and production: the deployment finding recurs in both executions.

**Proposed skill improvements** (not applied; see `/seo-update-skills`)

- website-seo-discovery: probe every retired slug listed in the site's reindex tracker (proposal filed, pending)

**Requires human approval**

- Push/merge/deploy update-blog-titles
- Server: real 404s and the two 301s
- Skill proposal for website-seo-discovery

## Blockers and notes

- PageSpeed quota exhausted: Core Web Vitals not measured (image-dimensions-missing:/ not rechecked)
- Skipped plan steps: report-analysis (no new reports), full-site-sweep (not requested), schema (types only), internal-linking, cannibalization and keyword-fit (no fresh GSC or crawl data in this run), ai-search.
- Findings not rechecked stay open; nothing was marked resolved without re-observation.
