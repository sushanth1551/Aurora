---
name: seo-video
description: "SEO Intelligence video SEO for a configured website: a catalog of the owner's YouTube playlists (title, upload date, duration, thumbnail, description; metadata only, no downloads), pages that embed videos without VideoObject markup with ready JSON-LD proposals, and existing videos (for example student testimonials) matched to blog posts that have no video. Use for /seo-video, 'video SEO', 'use my YouTube videos on the website', 'VideoObject schema'."
user-invocable: true
argument-hint: "[site-id] [--playlist URL]"
metadata:
  author: AURORA
  version: "1.0.0"
  category: seo-intelligence
---

# /seo-video

## When to activate

"Video SEO", "add my YouTube videos to the site", "VideoObject", "testimonial videos on the
website", or when a page embeds videos.

## Required inputs

Website id; playlists in `video.playlists` or `--playlist`; the optional `video` extra
(`.venv\Scripts\pip install -e ".[video]"`, yt-dlp) to read playlists.

## Tools and integrations

1. `scripts/seo_insights.py videos <site> [--playlist URL]` refreshes
   `insights/videos-catalog.json` (YouTube metadata through yt-dlp, no media download).
2. `scripts/seo_insights.py crawl <site>` checks embeds and markup and writes
   `insights/videos-videoobject-proposals.json` and matches.

## Execution steps

1. Refresh the catalog when playlists changed, then run the crawl.
2. Report pages missing VideoObject (with the proposal), and the best video-to-page matches.
3. Embedding a video or adding markup is a website change: `seo-content-optimization`, branch,
   owner approval.

## Analysis methodology

- Embeds: YouTube iframes, links and lite-youtube elements; markup: a `VideoObject` in JSON-LD.
- Matches: overlap of meaningful title words between the video and the page title/slug
  (>= 0.3 Jaccard); a suggestion, not a judgement of relevance; the owner decides.
- Quotes from testimonials are used only verbatim from captions, with the video link.

## Output format

Keys `videoobject-missing:<path>|blog`, `video-embed-opportunities:site`.

## Quality checks

uploadDate and duration only from YouTube metadata, never guessed.

## Error handling

yt-dlp missing: the catalog step is blocked with the install command; crawl checks still run.

## Website-specific configuration

`video.playlists`.

## How findings are stored

Ledger findings through the crawl; catalog and proposals under `insights/`.

## Improving this skill

Through proposals (`seo-learning-and-memory`) and `/seo-update-skills`.
