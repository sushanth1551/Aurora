"""Stable content hashing for deduplication and provenance."""

import hashlib


def content_hash(text: str) -> str:
    """SHA-256 hex digest of text with line endings and edge whitespace normalized."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
