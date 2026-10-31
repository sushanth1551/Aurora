"""Migration checks that need no database: generated SQL vs. the ORM models."""

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from app.database.models import Base

ROOT = Path(__file__).resolve().parents[2]
MODULE3_TABLES = {
    "research_websites",
    "mapped_pages",
    "processed_documents",
    "research_entities",
    "research_claims",
    "research_reports",
}


def alembic_sql(*args: str) -> str:
    env = {**os.environ, "PYTHONPATH": str(ROOT / "backend")}
    done = subprocess.run(
        [sys.executable, "-m", "alembic", *args, "--sql"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert done.returncode == 0, done.stderr[-500:]
    return done.stdout


@pytest.fixture(scope="module")
def full_upgrade() -> str:
    return alembic_sql("upgrade", "head")


def test_single_linear_head() -> None:
    env = {**os.environ, "PYTHONPATH": str(ROOT / "backend")}
    done = subprocess.run(
        [sys.executable, "-m", "alembic", "heads"],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=60,
    )  # fmt: skip
    assert done.stdout.strip().startswith("0002") and done.stdout.count("(head)") == 1


def test_upgrade_sql_matches_every_model_table_column_and_constraint(
    full_upgrade,
) -> None:
    problems: list[str] = []
    for table in Base.metadata.sorted_tables:
        if f"CREATE TABLE {table.name} " not in full_upgrade:
            problems.append(f"table {table.name}")
            continue
        ddl = str(CreateTable(table).compile(dialect=postgresql.dialect()))
        for column in re.findall(r"^\s+(\w+) ", ddl, re.M):
            if column in {"CREATE", "PRIMARY", "CONSTRAINT", "FOREIGN", "UNIQUE"}:
                continue
            block = full_upgrade.split(f"CREATE TABLE {table.name} ")[1].split(");")[0]
            if not re.search(rf"\b{column}\b", block) and table.name != "research_jobs":
                problems.append(f"{table.name}.{column}")
        for index in table.indexes:
            if index.name not in full_upgrade:
                problems.append(f"index {index.name}")
        for constraint in table.constraints:
            if constraint.name and constraint.name not in full_upgrade:
                problems.append(f"constraint {constraint.name}")
    assert problems == []
    # job_type is added by 0002, not by the Module 2 CREATE TABLE
    assert "ALTER TABLE research_jobs ADD COLUMN job_type" in full_upgrade


def test_module3_migration_is_additive_and_cascades(full_upgrade) -> None:
    step = alembic_sql("upgrade", "0001:0002")

    assert "DROP TABLE" not in step and "DROP COLUMN" not in step
    assert "DELETE FROM" not in step and "TRUNCATE" not in step
    assert "UPDATE research_jobs" not in step  # existing rows are never rewritten
    for table in MODULE3_TABLES:
        assert f"CREATE TABLE {table} " in step
    assert "ON DELETE CASCADE" in step and "ON DELETE SET NULL" in step
    assert "server_default" not in step and "DEFAULT 'research'" in step
    assert (
        full_upgrade.count("CREATE TABLE research_jobs") == 1
    )  # Module 2 table not recreated


def test_downgrade_removes_only_module3_objects() -> None:
    down = alembic_sql("downgrade", "0002:0001")

    dropped = set(re.findall(r"DROP TABLE (\w+)", down))
    assert dropped == MODULE3_TABLES
    assert "DROP COLUMN job_type" in down
    for module2 in ("research_jobs", "research_sources", "search_results"):
        assert module2 not in dropped
