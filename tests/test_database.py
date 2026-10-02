from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from concurrent.futures import ThreadPoolExecutor

import backend.database as database


def test_sqlite_connection_can_cross_dependency_threads():
    # Exercise the application's actual engine configuration, not a test-only engine.
    with database.engine.connect() as connection:
        with ThreadPoolExecutor(max_workers=1) as executor:
            result = executor.submit(
                lambda: connection.execute(text("SELECT 1")).scalar_one()
            ).result(timeout=5)
        assert result == 1


def test_database_session_with_temporary_sqlite(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"

    test_engine = create_engine(
        f"sqlite:///{db_path.as_posix()}",
        echo=False,
    )

    test_session_local = sessionmaker(
        bind=test_engine,
        class_=Session,
        autoflush=False,
        expire_on_commit=False,
    )

    monkeypatch.setattr(database, "SessionLocal", test_session_local)

    db_generator = database.get_db()
    db = next(db_generator)

    try:
        result = db.execute(text("SELECT 1")).scalar_one()

        assert result == 1
        assert db_path.exists()
    finally:
        try:
            next(db_generator)
        except StopIteration:
            pass

        test_engine.dispose()
