"""Repository tests against in-memory SQLite (schema from the ORM models).

PostgreSQL-specific behaviour (JSONB, migrations) is covered in
``test_research_integration.py``.
"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import event, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.exceptions import PersistenceException
from app.database.models import Base, ResearchSourceRecord, SearchResultRecord
from app.database.repositories.research_repository import (
    IdempotencyConflict,
    ResearchRepository,
)
from app.schemas.research import (
    ErrorDetail,
    ExtractionStatus,
    JobStatus,
    ResearchJob,
    ResearchRequest,
    ResearchSource,
    ResearchStage,
    SearchResult,
    StageStatus,
    StageSummary,
)


@pytest.fixture
async def session_factory():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _fk(dbapi_connection, _):  # enforce FKs like PostgreSQL does
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def new_job(request: ResearchRequest) -> ResearchJob:
    return ResearchJob(
        query=request.query,
        status=JobStatus.RUNNING,
        requested_result_count=request.max_results,
        started_at=datetime.now(UTC),
        request_id=request.request_id,
    )


def source(url: str, status=ExtractionStatus.SUCCEEDED, **kwargs) -> ResearchSource:
    return ResearchSource(
        original_url=url,
        normalized_url=url,
        domain="example.com",
        extraction_status=status,
        extracted_markdown="# md" if status == ExtractionStatus.SUCCEEDED else None,
        content_hash="a" * 64 if status == ExtractionStatus.SUCCEEDED else None,
        metadata={"position": 1},
        **kwargs,
    )


async def stored(session_factory, request=None, sources=None):
    request = request or ResearchRequest(query="ai tools", country="in", language="en")
    job = new_job(request)
    async with session_factory() as session:
        repo = ResearchRepository(session)
        await repo.create_job(job, request)
        job.status = JobStatus.COMPLETED
        job.actual_result_count = 2
        job.eligible_source_count = 1
        job.successful_source_count = 1
        job.completed_at = datetime.now(UTC)
        await repo.save_outcome(
            job,
            search_results=[
                SearchResult(
                    position=1,
                    title="A",
                    url="https://example.com/a",
                    snippet="s",
                    metadata={"search_id": "x"},
                ),
                SearchResult(position=2, title="B", url="https://example.org/b"),
            ],
            sources=sources or [source("https://example.com/a")],
            stages=[
                StageSummary(
                    stage=ResearchStage.SEARCH,
                    status=StageStatus.SUCCEEDED,
                    started_at=datetime.now(UTC),
                    duration_ms=5,
                    item_count=2,
                )
            ],
            errors=[
                ErrorDetail(stage=ResearchStage.EXTRACTION, category="c", message="m")
            ],
        )
    return job


async def test_roundtrip_links_sources_to_job(session_factory) -> None:
    job = await stored(session_factory)

    async with session_factory() as session:
        response = await ResearchRepository(session).get_response(job.job_id)

    assert response is not None and response.job_id == job.job_id
    assert response.status == JobStatus.COMPLETED and response.query == "ai tools"
    assert response.requested_result_count == 10 and response.actual_result_count == 2
    assert [r.position for r in response.search_results] == [1, 2]
    assert response.search_results[0].metadata == {"search_id": "x"}
    assert (
        len(response.sources) == 1 and response.sources[0].extracted_markdown == "# md"
    )
    assert response.stages[0].stage == ResearchStage.SEARCH
    assert response.errors[0].category == "c"
    assert response.completed_at is not None


async def test_include_content_false_omits_markdown(session_factory) -> None:
    job = await stored(session_factory)

    async with session_factory() as session:
        response = await ResearchRepository(session).get_response(
            job.job_id, include_content=False
        )

    assert response.sources[0].extracted_markdown is None
    assert response.sources[0].content_hash == "a" * 64


async def test_missing_job_returns_none(session_factory) -> None:
    async with session_factory() as session:
        assert await ResearchRepository(session).get_response(uuid4()) is None


async def test_failure_is_persisted_and_restored(session_factory) -> None:
    request = ResearchRequest(query="q")
    job = new_job(request)
    async with session_factory() as session:
        repo = ResearchRepository(session)
        await repo.create_job(job, request)
        job.status = JobStatus.FAILED
        job.failure = ErrorDetail(
            stage=ResearchStage.SEARCH, category="provider_unavailable", message="down"
        )
        await repo.save_outcome(
            job, search_results=[], sources=[], stages=[], errors=[job.failure]
        )
        response = await repo.get_response(job.job_id)

    assert response.status == JobStatus.FAILED
    assert response.failure.category == "provider_unavailable"


async def test_duplicate_normalized_url_in_same_job_is_rejected(
    session_factory,
) -> None:
    request = ResearchRequest(query="q")
    job = new_job(request)
    async with session_factory() as session:
        repo = ResearchRepository(session)
        await repo.create_job(job, request)
        with pytest.raises(PersistenceException):
            await repo.save_outcome(
                job,
                search_results=[],
                sources=[
                    source("https://example.com/a"),
                    source("https://example.com/a"),
                ],
                stages=[],
                errors=[],
            )
    async with (
        session_factory() as session
    ):  # the failed transaction left nothing behind
        rows = (await session.execute(select(ResearchSourceRecord))).scalars().all()
        assert rows == []


async def test_same_url_is_allowed_in_different_jobs(session_factory) -> None:
    await stored(session_factory)
    await stored(session_factory)

    async with session_factory() as session:
        rows = (await session.execute(select(ResearchSourceRecord))).scalars().all()
    assert len(rows) == 2


async def test_rejected_sources_without_normalized_url_can_repeat(
    session_factory,
) -> None:
    rejected = [
        ResearchSource(
            original_url="file:///a", extraction_status=ExtractionStatus.REJECTED
        ),
        ResearchSource(
            original_url="file:///b", extraction_status=ExtractionStatus.REJECTED
        ),
    ]
    job = await stored(session_factory, sources=rejected)

    async with session_factory() as session:
        response = await ResearchRepository(session).get_response(job.job_id)
    assert len(response.sources) == 2


async def test_source_cannot_exist_without_a_job(session_factory) -> None:
    async with session_factory() as session:
        session.add(
            ResearchSourceRecord(
                id=uuid4(),
                research_job_id=uuid4(),
                original_url="https://x.com/",
                extraction_status="failed",
                provider_metadata={},
                created_at=datetime.now(UTC),
            )
        )
        with pytest.raises(Exception, match="FOREIGN KEY|foreign key"):
            await session.commit()


async def test_deleting_a_job_cascades(session_factory) -> None:
    job = await stored(session_factory)

    async with session_factory() as session:
        from app.database.models import ResearchJobRecord

        record = await session.get(ResearchJobRecord, job.job_id)
        await session.delete(record)
        await session.commit()
        assert (
            await session.execute(select(ResearchSourceRecord))
        ).scalars().all() == []
        assert (await session.execute(select(SearchResultRecord))).scalars().all() == []


async def test_idempotency_key_is_unique(session_factory) -> None:
    request = ResearchRequest(query="q", request_id="idem-key-0001")
    await stored(session_factory, request=request)

    async with session_factory() as session:
        repo = ResearchRepository(session)
        assert await repo.find_job_id_by_request_id("idem-key-0001") is not None
        assert await repo.find_job_id_by_request_id("other-key-0001") is None
        with pytest.raises(IdempotencyConflict):
            await repo.create_job(new_job(request), request)


async def test_database_errors_become_safe_persistence_errors(session_factory) -> None:
    async with session_factory() as session:

        async def broken(*_a, **_k):
            raise OperationalError(
                "SELECT secret FROM t", {}, Exception("password=hunter2")
            )

        session.execute = broken  # type: ignore[method-assign]
        with pytest.raises(PersistenceException) as excinfo:
            await ResearchRepository(session).get_response(uuid4())

    message = str(excinfo.value)
    assert message == "The database is unavailable."
    assert "hunter2" not in message and "SELECT" not in message


async def test_unreachable_database_is_a_persistence_error() -> None:
    engine = create_async_engine("sqlite+aiosqlite:////nonexistent-dir/x/db.sqlite")
    async with AsyncSession(engine) as session:
        with pytest.raises(PersistenceException):
            await ResearchRepository(session).get_response(uuid4())
    await engine.dispose()


def test_non_async_database_url_is_a_configuration_error() -> None:
    from app.core.exceptions import ConfigurationException
    from app.database.session import create_engine

    with pytest.raises(ConfigurationException) as excinfo:
        create_engine("postgresql+psycopg2://user:secret-pw@localhost/db")

    assert "secret-pw" not in str(excinfo.value)
    assert "postgresql+asyncpg" in str(excinfo.value)
