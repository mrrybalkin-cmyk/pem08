from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from backend.models.api import (
    CompetitorCreate,
    CompetitorResponse,
    CompetitorUpdate,
)
from backend.models.db import Competitor


def test_competitor_create_validates_name_length():
    valid = CompetitorCreate(name="A")

    assert valid.name == "A"

    with pytest.raises(ValidationError):
        CompetitorCreate(name="")

    with pytest.raises(ValidationError):
        CompetitorCreate(name="A" * 121)


def test_competitor_update_preserves_partial_patch_semantics():
    update = CompetitorUpdate(
        niche="AI automation",
    )

    assert update.model_dump(exclude_unset=True) == {
        "niche": "AI automation",
    }

    clear_notes = CompetitorUpdate(
        notes=None,
    )

    assert clear_notes.model_dump(exclude_unset=True) == {
        "notes": None,
    }


def test_competitor_update_rejects_null_name():
    with pytest.raises(
        ValidationError,
        match="name cannot be null",
    ):
        CompetitorUpdate(name=None)


def test_competitor_response_supports_orm_objects():
    now = datetime.now(timezone.utc)

    competitor = Competitor(
        id="test-id",
        name="Acme AI",
        website_url="https://example.com",
        niche="AI",
        notes=None,
        created_at=now,
        updated_at=now,
    )

    response = CompetitorResponse.model_validate(competitor)

    assert response.id == "test-id"
    assert response.name == "Acme AI"
    assert response.website_url == "https://example.com"
    assert response.niche == "AI"
    assert response.notes is None
    assert response.created_at == now
    assert response.updated_at == now
