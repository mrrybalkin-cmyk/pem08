import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.database import Base
from backend.repositories.competitors import (
    create_competitor,
    delete_competitor,
    get_competitor,
    list_competitors,
    update_competitor,
)


@pytest.fixture
def repository_engine(tmp_path):
    db_path = tmp_path / "repository.db"

    engine = create_engine(
        f"sqlite:///{db_path.as_posix()}",
        echo=False,
    )

    Base.metadata.create_all(engine)

    try:
        yield engine
    finally:
        engine.dispose()


def test_competitor_repository_crud(repository_engine):
    with Session(repository_engine) as db:
        first = create_competitor(
            db,
            name="Alpha",
            website_url="https://alpha.example",
            niche="AI",
            notes="First competitor",
        )

        second = create_competitor(
            db,
            name="Beta",
        )

        db.commit()

        first_id = first.id
        second_id = second.id

    with Session(repository_engine) as db:
        loaded = get_competitor(db, first_id)

        assert loaded is not None
        assert loaded.name == "Alpha"
        assert loaded.website_url == "https://alpha.example"
        assert loaded.niche == "AI"
        assert loaded.notes == "First competitor"

        competitors = list_competitors(db)

        assert [item.id for item in competitors] == [
            first_id,
            second_id,
        ]

        updated = update_competitor(
            db,
            first_id,
            {
                "name": "Alpha Updated",
                "niche": "AI automation",
                "notes": None,
            },
        )

        assert updated is not None
        assert updated.name == "Alpha Updated"
        assert updated.niche == "AI automation"
        assert updated.notes is None

        db.commit()

    with Session(repository_engine) as db:
        persisted = get_competitor(db, first_id)

        assert persisted is not None
        assert persisted.name == "Alpha Updated"
        assert persisted.niche == "AI automation"
        assert persisted.notes is None

        assert delete_competitor(db, second_id) is True
        assert delete_competitor(db, "missing-id") is False

        db.commit()

    with Session(repository_engine) as db:
        assert get_competitor(db, second_id) is None
        assert [item.id for item in list_competitors(db)] == [first_id]


def test_repository_does_not_commit_implicitly(repository_engine):
    with Session(repository_engine) as db:
        competitor = create_competitor(
            db,
            name="Rollback Test",
        )

        competitor_id = competitor.id

        assert get_competitor(db, competitor_id) is not None

        db.rollback()

    with Session(repository_engine) as db:
        assert get_competitor(db, competitor_id) is None


def test_repository_rejects_unknown_update_fields(repository_engine):
    with Session(repository_engine) as db:
        competitor = create_competitor(
            db,
            name="Validation Test",
        )

        with pytest.raises(
            ValueError,
            match="Unsupported competitor fields",
        ):
            update_competitor(
                db,
                competitor.id,
                {"id": "replacement-id"},
            )
