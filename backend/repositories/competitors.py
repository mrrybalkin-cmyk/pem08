"""Repository operations for competitors."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models.db import Competitor


_UPDATABLE_FIELDS = {
    "name",
    "website_url",
    "niche",
    "notes",
}


def create_competitor(
    db: Session,
    *,
    name: str,
    website_url: str | None = None,
    niche: str | None = None,
    notes: str | None = None,
) -> Competitor:
    """Create a competitor inside the current transaction."""
    competitor = Competitor(
        name=name,
        website_url=website_url,
        niche=niche,
        notes=notes,
    )

    db.add(competitor)
    db.flush()
    db.refresh(competitor)

    return competitor


def get_competitor(
    db: Session,
    competitor_id: str,
) -> Competitor | None:
    """Return one competitor by ID."""
    return db.get(Competitor, competitor_id)


def list_competitors(db: Session) -> list[Competitor]:
    """Return competitors in stable creation order."""
    statement = select(Competitor).order_by(
        Competitor.created_at.asc(),
        Competitor.id.asc(),
    )

    return list(db.scalars(statement).all())


def update_competitor(
    db: Session,
    competitor_id: str,
    changes: dict[str, Any],
) -> Competitor | None:
    """Update allowed competitor fields inside the current transaction."""
    unknown_fields = set(changes) - _UPDATABLE_FIELDS

    if unknown_fields:
        fields = ", ".join(sorted(unknown_fields))
        raise ValueError(f"Unsupported competitor fields: {fields}")

    competitor = get_competitor(db, competitor_id)

    if competitor is None:
        return None

    for field, value in changes.items():
        setattr(competitor, field, value)

    db.flush()
    db.refresh(competitor)

    return competitor


def delete_competitor(
    db: Session,
    competitor_id: str,
) -> bool:
    """Delete one competitor inside the current transaction."""
    competitor = get_competitor(db, competitor_id)

    if competitor is None:
        return False

    db.delete(competitor)
    db.flush()

    return True
