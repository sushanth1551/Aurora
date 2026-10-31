---
name: wordpress-publishing
description: "Prepare an approved content item for controlled WordPress publication. Planning guidance only; AURORA provider integrations are not implemented yet."
---

# wordpress-publishing

**Description:** Prepare an approved content item for controlled WordPress
publication.

## Purpose
Define a human-gated, auditable publishing workflow. The WordPress adapter is
not implemented in the current scaffold.

## When to use it
Use only after an authorized user explicitly approves the exact final content
and a configured WordPress integration is available.

## Required inputs
- Final reviewed title, body, metadata, and destination site.
- Explicit publication approval and requested status (draft/publish).
- Securely configured credentials with minimum required scope.

## Execution workflow
1. Verify user approval, destination, and content version.
2. Validate fields and confirm intended post status.
3. Submit through an authenticated, least-privilege adapter.
4. Verify provider response and record an auditable result.
5. Return the resulting status and URL, if supplied by WordPress.

## Expected outputs
Publication result, remote identifier, status, and audit metadata without
exposing credentials.

## Error handling
Stop on missing approval, ambiguous destination, authentication errors, or
uncertain provider outcomes. Do not blindly retry non-idempotent writes.

## Quality requirements
Never publish autonomously without explicit approval; protect credentials,
avoid duplicate posts, and preserve a record of the approved content version.
