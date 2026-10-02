"""Pydantic API schemas for Competitor Intelligence Assistant v2."""

from datetime import datetime

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    field_validator,
)


class CompetitorCreate(BaseModel):
    """Payload for creating a competitor."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(
        min_length=1,
        max_length=120,
    )
    website_url: HttpUrl | None = None
    niche: str | None = Field(default=None, max_length=160)
    notes: str | None = Field(default=None, max_length=2000)


class CompetitorUpdate(BaseModel):
    """Payload for partial competitor updates."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=120,
    )
    website_url: HttpUrl | None = None
    niche: str | None = Field(default=None, max_length=160)
    notes: str | None = Field(default=None, max_length=2000)

    @field_validator("name")
    @classmethod
    def name_cannot_be_null(cls, value: str | None) -> str:
        """Allow name to be omitted, but never explicitly set to null."""
        if value is None:
            raise ValueError("name cannot be null")

        return value


class CompetitorResponse(BaseModel):
    """API representation of a competitor."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    website_url: str | None
    niche: str | None
    notes: str | None
    created_at: datetime
    updated_at: datetime
