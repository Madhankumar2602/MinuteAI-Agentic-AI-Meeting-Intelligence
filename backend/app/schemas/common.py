"""Shared response shapes."""

from __future__ import annotations

from typing import ClassVar, Self

from pydantic import BaseModel, Field, model_validator


class Page[T](BaseModel):
    """Envelope for paginated list endpoints.

    Returning `total` lets the UI render "showing 1-20 of 137" and decide
    whether a next-page control should exist.
    """

    items: list[T]
    total: int = Field(description="Total rows matching the filter, ignoring pagination.")
    page: int = Field(ge=1)
    size: int = Field(ge=1)

    @property
    def pages(self) -> int:
        return max(1, -(-self.total // self.size))


class PartialUpdate(BaseModel):
    """Base for PATCH bodies.

    Optional fields let a client omit what it is not changing. That also admits
    an explicit ``null``, which for a NOT NULL column would reach the database
    and fail as a 500. Subclasses list those columns in ``NON_NULLABLE`` so an
    explicit null is rejected as a 422 at the boundary instead.
    """

    NON_NULLABLE: ClassVar[frozenset[str]] = frozenset()

    @model_validator(mode="after")
    def _reject_explicit_nulls(self) -> Self:
        bad = sorted(
            name
            for name in self.model_fields_set & self.NON_NULLABLE
            if getattr(self, name) is None
        )
        if bad:
            raise ValueError(f"these fields cannot be null: {', '.join(bad)}")
        return self
