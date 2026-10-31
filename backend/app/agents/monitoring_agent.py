"""MonitoringAgent: monitors search performance.

Input: a Search Console property id. Output (future): performance summaries
from authorized, read-only data.
"""

from app.agents.base import BaseAgent


class MonitoringAgent(BaseAgent):
    """Collect and summarize search performance data."""

    name = "monitoring_agent"
    responsibility = "Monitor search performance."
    uses_llm = False

    async def monitor(self, property_id: str) -> list[dict[str, object]]:
        """Return performance rows for the property.

        Errors: ``ConfigurationException`` if Google credentials are missing.
        """
        # TODO: delegate to SearchConsoleMonitor; summaries are deterministic.
        raise self._not_implemented("monitor")
