"""Search Console reporting service boundary."""


class SearchConsoleMonitor:
    """Retrieve authorized Search Console metrics through an injected adapter."""

    async def report(self, property_id: str) -> list[dict[str, object]]:
        """Return performance rows for the property."""
        # TODO: validate the property, bound the reporting window, and call
        # SearchConsoleTool.
        raise NotImplementedError("Search Console monitoring is not implemented.")
