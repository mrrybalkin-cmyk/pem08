from datetime import datetime
from uuid import UUID

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.database import Base
from backend.models.db import Competitor


def test_competitor_table_contract():
    table = Competitor.__table__

    assert table.name == "competitors"
    assert list(table.columns.keys()) == [
        "id",
        "name",
        "website_url",
        "niche",
        "notes",
        "created_at",
        "updated_at",
    ]

    assert table.c.id.primary_key
    assert table.c.name.nullable is False
    assert table.c.name.type.length == 120

    assert table.c.website_url.nullable is True
    assert table.c.niche.nullable is True
    assert table.c.notes.nullable is True

    assert table.c.created_at.nullable is False
    assert table.c.updated_at.nullable is False


def test_competitor_can_be_persisted_in_sqlite():
    test_engine = create_engine("sqlite:///:memory:")

    try:
        Base.metadata.create_all(test_engine)

        with Session(test_engine) as session:
            competitor = Competitor(
                name="Acme AI",
                website_url="https://example.com",
                niche="AI automation",
                notes="Test competitor",
            )

            session.add(competitor)
            session.commit()
            session.refresh(competitor)

            UUID(competitor.id)

            assert competitor.name == "Acme AI"
            assert competitor.website_url == "https://example.com"
            assert competitor.niche == "AI automation"
            assert competitor.notes == "Test competitor"

            assert isinstance(competitor.created_at, datetime)
            assert isinstance(competitor.updated_at, datetime)

            loaded = session.get(Competitor, competitor.id)

            assert loaded is not None
            assert loaded.id == competitor.id
            assert loaded.name == "Acme AI"
    finally:
        test_engine.dispose()
