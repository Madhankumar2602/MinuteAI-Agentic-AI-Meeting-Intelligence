"""User model."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.db.models.meeting import Meeting


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"

    # Stored lower-cased (normalised in the service layer) so a plain UNIQUE
    # constraint is enough to stop Madhan@x.com and madhan@x.com registering
    # twice. Avoids needing the citext extension.
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)

    # Argon2id hash. Never the password itself, and never logged.
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)

    full_name: Mapped[str] = mapped_column(String(255), nullable=False)

    # Soft disable: lets an account be blocked without deleting its meetings.
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    meetings: Mapped[list[Meeting]] = relationship(
        back_populates="owner",
        cascade="all, delete-orphan",
        # lazy="raise" turns an accidental lazy load into an immediate, obvious
        # error instead of an async MissingGreenlet surprise. Relationships must
        # be loaded explicitly with selectinload(), which also prevents N+1.
        lazy="raise",
    )

    def __repr__(self) -> str:
        return f"<User {self.email}>"
