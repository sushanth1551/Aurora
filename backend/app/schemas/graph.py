"""API contracts for the knowledge graph endpoints (Module 5)."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.graph.models import SyncRun


class SyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    research_job_id: UUID
    dry_run: bool = Field(
        default=False, description="Plan only: report what would be written"
    )


class ReconcileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dry_run: bool = Field(default=True, description="Report drift without enqueueing")


class ProcessRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    limit: int = Field(default=20, ge=1, le=200)


class SyncRunsResponse(BaseModel):
    runs: list[SyncRun]


class OutboxView(BaseModel):
    status: str
    attempt_count: int
    next_retry_at: datetime | None = None
    last_error: str | None = None
    processed_at: datetime | None = None
    updated_at: datetime


class SyncStatusResponse(BaseModel):
    research_job_id: UUID
    synchronized: bool = Field(
        description="True only after Neo4j confirmed the last sync"
    )
    outbox: OutboxView | None = None
    recent_runs: list[dict[str, Any]] = Field(default_factory=list)


class ItemsResponse(BaseModel):
    items: list[dict[str, Any]]
    count: int
    limit: int | None = None
    offset: int = 0
    note: str | None = None


GraphDocument = dict[str, Any]
