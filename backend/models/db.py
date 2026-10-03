"""SQLAlchemy ORM models for Competitor Intelligence Assistant v2."""

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base
from backend.models.analysis import SourceType


def utc_now() -> datetime:
    """Return the current timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


class Competitor(Base):
    """Competitor workspace entity."""

    __tablename__ = "competitors"

    sources: Mapped[list["Source"]] = relationship(back_populates="competitor", passive_deletes="all")
    analyses: Mapped[list["Analysis"]] = relationship(back_populates="competitor", passive_deletes="all")

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )

    name: Mapped[str] = mapped_column(
        String(120),
        nullable=False,
    )

    website_url: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    niche: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    notes: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    competitor_id: Mapped[str] = mapped_column(ForeignKey("competitors.id", ondelete="CASCADE"), nullable=False)
    source_type: Mapped[SourceType] = mapped_column(
        Enum(SourceType, native_enum=False, create_constraint=True), nullable=False,
    )
    label: Mapped[str] = mapped_column(Text, nullable=False)
    original_filename: Mapped[str | None] = mapped_column(Text)
    mime_type: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text)
    storage_path: Mapped[str | None] = mapped_column(Text)
    sha256: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    competitor: Mapped[Competitor] = relationship(back_populates="sources")
    snapshots: Mapped[list["SourceSnapshot"]] = relationship(back_populates="source", passive_deletes="all")


class SourceSnapshot(Base):
    __tablename__ = "source_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    final_url: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)
    meta_description: Mapped[str | None] = mapped_column(Text)
    extracted_text: Mapped[str | None] = mapped_column(Text)
    screenshot_path: Mapped[str | None] = mapped_column(Text)
    secondary_screenshot_path: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False)

    source: Mapped[Source] = relationship(back_populates="snapshots")
    analyses: Mapped[list["Analysis"]] = relationship(back_populates="snapshot", passive_deletes="all")


class Analysis(Base):
    __tablename__ = "analyses"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    competitor_id: Mapped[str] = mapped_column(ForeignKey("competitors.id", ondelete="CASCADE"), nullable=False)
    snapshot_id: Mapped[str | None] = mapped_column(ForeignKey("source_snapshots.id", ondelete="CASCADE"))
    analysis_type: Mapped[str] = mapped_column(
        Enum("source", "aggregate", native_enum=False, create_constraint=True), nullable=False,
    )
    model_id: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_version: Mapped[str] = mapped_column(Text, nullable=False)
    result_json: Mapped[str] = mapped_column(Text, nullable=False)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    competitor: Mapped[Competitor] = relationship(back_populates="analyses")
    snapshot: Mapped[SourceSnapshot | None] = relationship(back_populates="analyses")


class Comparison(Base):
    """Historical comparison; JSON participant IDs intentionally have no FK."""
    __tablename__ = "comparisons"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    competitor_ids_json: Mapped[str] = mapped_column(Text, nullable=False)
    model_id: Mapped[str] = mapped_column(Text, nullable=False)
    result_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
