"""SQLAlchemy declarative base and shared column mixins."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, MetaData, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Deterministic constraint names.
#
# Without this, PostgreSQL invents names like `meetings_owner_id_fkey1` and
# Alembic cannot reliably generate a `downgrade()` that drops them. Fixing the
# convention now - before any migration exists - means every constraint the
# project ever creates is named predictably.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class UUIDPrimaryKeyMixin:
    """UUID surrogate key.

    UUIDs rather than auto-increment integers because meeting ids appear in
    URLs and S3 object keys: a sequential integer would let one user probe for
    another user's resources by counting. Authorization is still enforced
    server-side - this only removes the invitation to guess.
    """

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4, sort_order=-100)


class TimestampMixin:
    """created_at / updated_at maintained by the database itself.

    ``server_default``/``onupdate`` at the DB level means the values are
    correct even for rows written by a migration or by psql directly.
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, sort_order=100
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
        sort_order=101,
    )
