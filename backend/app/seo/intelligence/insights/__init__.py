"""Deterministic SEO insights computed from measured data (no LLM, no estimates).

Each module turns observed inputs (Search Console rows, fetched HTML, SERP
snapshots, sitemaps, an imported conversions file) into ``Finding`` records for
the knowledge base plus a small JSON snapshot. Inputs are injected, so every
module is testable offline.
"""
