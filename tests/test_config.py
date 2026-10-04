import pytest
from pydantic import ValidationError


def test_config_defaults_csv_and_paths(monkeypatch):
    from backend.config import PROJECT_ROOT, Settings
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:8000, http://127.0.0.1:8000 ,")
    settings = Settings(_env_file=None, upload_dir="data/test-uploads")
    assert settings.app_host == "127.0.0.1"
    assert settings.allowed_origins == ["http://localhost:8000", "http://127.0.0.1:8000"]
    assert settings.upload_dir == PROJECT_ROOT / "data/test-uploads"
    assert not settings.ai_configured
    assert settings.ai_base_url == "https://api.proxyapi.ru/v1"


@pytest.mark.parametrize("field", ["max_image_mb", "max_pdf_mb", "max_text_chars", "browser_timeout_ms", "ai_timeout_seconds"])
def test_invalid_limits(field):
    from backend.config import Settings
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: 0})


def test_secret_representation_and_legacy_alias(monkeypatch):
    from backend.config import Settings
    monkeypatch.setenv("API_PORT", "8080")
    settings = Settings(_env_file=None, ai_api_key="test-only-secret-marker")
    assert settings.api_port == 8080
    assert settings.ai_configured
    assert "test-only-secret-marker" not in repr(settings)
    monkeypatch.setenv("APP_PORT", "9000")
    assert Settings(_env_file=None).api_port == 9000
