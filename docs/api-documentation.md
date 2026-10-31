# API documentation

Run the API locally and open `/docs` (Swagger UI) or `/redoc` for generated
OpenAPI documentation.

## Current endpoints

### `GET /`
Returns application metadata:

```json
{
  "application": "AURORA",
  "version": "0.1.0",
  "description": "Autonomous Web Research and SEO Intelligence Platform",
  "status": "running"
}
```

### `GET /api/v1/health`
Returns `200` with `{"status":"healthy","application":"AURORA","version":"0.1.0"}`
when the API process is responsive. It does not check databases or providers.

### `POST /api/v1/research` (placeholder)
Accepts JSON:

| Field | Type | Notes |
| --- | --- | --- |
| `topic` | string, required | 2–500 characters after trimming |
| `description` | string, optional | up to 2000 characters |
| `research_depth` | `quick` \| `standard` \| `deep`, optional | default `standard` |
| `max_sources` | integer, optional | 1–50, default 10 |

Unknown fields are rejected with `422`. A valid request returns `200`:

```json
{
  "request_id": "…uuid…",
  "topic": "Example research topic",
  "status": "not_implemented",
  "message": "Request validated but not processed. …",
  "is_placeholder": true,
  "created_at": "2026-10-31T00:00:00Z"
}
```

This endpoint starts **no** research, calls no LLM, crawls nothing, and returns
no findings. The workflow arrives in a later module.

## Errors
Application errors use `{"error": {"code": "...", "message": "..."}}`.
Unexpected errors return a generic 500 message with no internals. Request
validation errors use FastAPI's standard `422` format.

## Planned API areas
Future versioned routes may cover research job status/results, source
management, SEO analysis, content briefs/drafts, quality audits, approval and
publishing, and Search Console metrics. Add authentication, authorization,
pagination, rate limiting, idempotency, and stable error contracts before
exposing user data or side effects.
