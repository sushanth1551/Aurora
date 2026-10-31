"""Knowledge graph node and relationship vocabulary."""

NODE_LABELS: tuple[str, ...] = ("Company", "Person", "Product", "Source", "Claim")
RELATIONSHIP_TYPES: tuple[str, ...] = (
    "MENTIONS",
    "PRODUCES",
    "WORKS_FOR",
    "SUPPORTED_BY",
)
