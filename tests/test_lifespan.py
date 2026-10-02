from unittest.mock import Mock

from fastapi.testclient import TestClient
import pytest


def test_repeated_lifecycle_does_not_create_clients(app, monkeypatch):
    from backend.config import settings
    from backend.services.openai_service import openai_service
    from backend.services.parser_service import parser_service
    constructor = Mock(side_effect=AssertionError("Client created at startup"))
    history_existed = settings.history_file.exists()
    history_before = settings.history_file.read_bytes() if history_existed else None
    monkeypatch.setattr("backend.services.openai_service.OpenAI", constructor)
    for _ in range(2):
        with TestClient(app):
            assert app.state.started
            assert settings.upload_dir.is_dir()
            assert settings.screenshot_dir.is_dir()
            assert openai_service._client is None
            assert parser_service._executor is None
        assert not app.state.started
    constructor.assert_not_called()
    assert settings.history_file.exists() is history_existed
    if history_existed:
        assert settings.history_file.read_bytes() == history_before


@pytest.mark.asyncio
async def test_lazy_parser_executor_can_restart(monkeypatch):
    from backend.services.parser_service import ParserService
    parser = ParserService()
    monkeypatch.setattr(parser, "_parse_sync", lambda url: (url, None, None, None, None))
    for _ in range(2):
        assert (await parser.parse_url("https://example.invalid"))[0] == "https://example.invalid"
        executor = parser._executor
        await parser.close()
        assert parser._executor is None
        with pytest.raises(RuntimeError):
            executor.submit(lambda: None)


def test_cleanup_on_startup_failure(app, monkeypatch, tmp_path):
    from backend.config import settings
    from backend.services.openai_service import openai_service
    from backend.services.parser_service import parser_service
    client = Mock()
    executor = Mock()
    monkeypatch.setattr(openai_service, "_client", client)
    monkeypatch.setattr(parser_service, "_executor", executor)
    occupied = tmp_path / "occupied"
    occupied.write_text("not a directory", encoding="utf-8")
    monkeypatch.setattr(settings, "screenshot_dir", occupied)
    with pytest.raises(FileExistsError):
        with TestClient(app):
            pytest.fail("Startup should fail")
    client.close.assert_called_once_with()
    executor.shutdown.assert_called_once_with(wait=True)
    assert not app.state.started


def test_ai_cleanup_even_if_parser_cleanup_fails(app, monkeypatch):
    from unittest.mock import AsyncMock
    from backend.services.openai_service import openai_service
    from backend.services.parser_service import parser_service
    client = Mock()
    monkeypatch.setattr(openai_service, "_client", client)
    monkeypatch.setattr(parser_service, "close", AsyncMock(side_effect=RuntimeError("cleanup failed")))
    with pytest.raises(RuntimeError, match="cleanup failed"):
        with TestClient(app):
            pass
    client.close.assert_called_once_with()



def test_lifespan_initializes_competitor_table(app):
    from sqlalchemy import inspect

    from backend.database import engine

    with TestClient(app):
        inspector = inspect(engine)
        tables = inspector.get_table_names()

        assert app.state.database_ready is True
        assert "competitors" in tables

    assert app.state.database_ready is False
