from unittest.mock import Mock

from fastapi.testclient import TestClient
import pytest


def test_repeated_lifecycle_does_not_create_clients(app, monkeypatch):
    from backend.config import settings
    from backend.services.ai_service import ai_service
    constructor = Mock(side_effect=AssertionError("Client created at startup"))
    monkeypatch.setattr("backend.services.ai_service.AsyncOpenAI", constructor)
    monkeypatch.setattr("backend.services.browser_service.browser_service.factory", constructor)
    for _ in range(2):
        with TestClient(app):
            assert app.state.started
            assert settings.upload_dir.is_dir() and settings.screenshot_dir.is_dir()
            assert ai_service._client is None
        assert not app.state.started
    constructor.assert_not_called()


def test_browser_closed_even_if_ai_cleanup_fails(app, monkeypatch):
    from unittest.mock import AsyncMock
    from backend.services.ai_service import ai_service
    from backend.services.browser_service import browser_service
    close = AsyncMock()
    monkeypatch.setattr(browser_service, "close", close)
    monkeypatch.setattr(ai_service, "close", AsyncMock(side_effect=RuntimeError("AI cleanup failed")))
    with pytest.raises(RuntimeError):
        with TestClient(app):
            pass
    close.assert_awaited_once()


def test_cleanup_on_startup_failure(app, monkeypatch, tmp_path):
    from unittest.mock import AsyncMock
    from backend.config import settings
    from backend.services.ai_service import ai_service
    from backend.services.browser_service import browser_service
    client = Mock(close=AsyncMock())
    monkeypatch.setattr(ai_service, "_client", client)
    close = AsyncMock()
    monkeypatch.setattr(browser_service, "close", close)
    occupied = tmp_path / "occupied"
    occupied.write_text("not a directory", encoding="utf-8")
    monkeypatch.setattr(settings, "screenshot_dir", occupied)
    with pytest.raises(FileExistsError):
        with TestClient(app):
            pytest.fail("Startup should fail")
    client.close.assert_awaited_once()
    close.assert_awaited_once()
    assert not app.state.started


def test_lifespan_initializes_competitor_table(app):
    from sqlalchemy import inspect

    from backend.database import engine

    with TestClient(app):
        inspector = inspect(engine)
        tables = inspector.get_table_names()

        assert app.state.database_ready is True
        assert "competitors" in tables
        assert {"sources", "source_snapshots", "analyses", "comparisons"} <= set(tables)

    assert app.state.database_ready is False


def test_v2_client_closed_on_repeated_lifecycle(app, monkeypatch):
    from unittest.mock import AsyncMock
    from backend.services.ai_service import ai_service
    for _ in range(2):
        client = Mock(close=AsyncMock())
        with TestClient(app):
            monkeypatch.setattr(ai_service, "_client", client)
        client.close.assert_awaited_once()
        assert ai_service._client is None


def test_database_disposed_even_if_v2_cleanup_fails(app, monkeypatch):
    from unittest.mock import AsyncMock
    from backend.services.ai_service import ai_service
    from backend.database import engine
    client = Mock(close=AsyncMock(side_effect=RuntimeError("v2 cleanup failure")))
    monkeypatch.setattr(ai_service, "_client", client)
    dispose = Mock(wraps=engine.dispose)
    monkeypatch.setattr(engine, "dispose", dispose)
    with pytest.raises(RuntimeError, match="v2 cleanup failure"):
        with TestClient(app):
            pass
    assert ai_service._client is None
    dispose.assert_called_once()
