"""Shared response shapes."""

from __future__ import annotations

from pydantic import BaseModel, Field


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
