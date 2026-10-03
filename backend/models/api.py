"""Pydantic API schemas for Competitor Intelligence Assistant v2."""

from datetime import datetime
import json
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    JsonValue,
    field_validator,
)

from backend.config import settings
from backend.models.analysis import CompetitorAnalysis, SourceType


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


class TextSourceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = Field(default="Текст", max_length=120)
    text: str = Field(min_length=10, max_length=30000)

    @field_validator("text")
    @classmethod
    def validate_content(cls, value: str) -> str:
        if len(value) > settings.max_text_chars or not value.strip():
            raise ValueError("Text must contain content within MAX_TEXT_CHARS")
        return value


class UrlSourceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = Field(default="Сайт", max_length=120)
    url: HttpUrl


class SourceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    competitor_id: str
    source_type: SourceType
    label: str
    original_filename: str | None
    mime_type: str | None
    url: str | None
    storage_path: str | None
    sha256: str | None
    created_at: datetime


class SnapshotResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    source_id: str
    captured_at: datetime
    final_url: str | None
    title: str | None
    meta_description: str | None
    extracted_text: str | None
    screenshot_path: str | None
    secondary_screenshot_path: str | None
    metadata_json: dict[str, JsonValue]

    @field_validator("metadata_json", mode="before")
    @classmethod
    def decode_metadata(cls, value):
        return json.loads(value) if isinstance(value, str) else value


class AnalysisResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    competitor_id: str
    snapshot_id: str | None
    analysis_type: Literal["source", "aggregate"]
    model_id: str
    prompt_version: str
    result_json: CompetitorAnalysis
    input_tokens: int | None
    output_tokens: int | None
    duration_ms: int | None
    created_at: datetime

    @field_validator("result_json", mode="before")
    @classmethod
    def decode_result(cls, value):
        return CompetitorAnalysis.model_validate_json(value, strict=True) if isinstance(value, str) else value


class SourceDetailResponse(BaseModel):
    source: SourceResponse
    snapshots: list[SnapshotResponse]
    analyses: list[AnalysisResponse]


class CompetitorDetailResponse(CompetitorResponse):
    sources: list[SourceResponse]
    latest_analysis: AnalysisResponse | None


class SourceErrorDetail(BaseModel):
    code: str
    message: str
    details: JsonValue = None


class SourceErrorResponse(BaseModel):
    error: SourceErrorDetail
