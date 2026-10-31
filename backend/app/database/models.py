"""SQLAlchemy model declarations.

Only the declarative base exists so far; tables, migrations, and repositories
are future work (see docs/database-design.md).
"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Declarative base class for all ORM models."""


# Backwards-compatible name used by the earlier scaffold.
BaseModel = Base

# TODO: add ResearchRequest, Source, and Document models plus migrations.
