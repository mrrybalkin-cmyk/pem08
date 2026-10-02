from unittest.mock import Mock

import pytest
from pydantic import SecretStr


@pytest.mark.asyncio
async def test_missing_key_is_controlled(monkeypatch):
    from backend.services.openai_service import AIConfigurationError, OpenAIService
    constructor = Mock(side_effect=AssertionError("No client expected"))
    monkeypatch.setattr("backend.services.openai_service.OpenAI", constructor)
    service = OpenAIService()
    with pytest.raises(AIConfigurationError, match="PROXY_API_KEY"):
        await service.analyze_text("Offline test text")
    constructor.assert_not_called()
    service.close()


@pytest.mark.asyncio
async def test_lazy_client_reused_closed_and_no_secret_logged(monkeypatch, caplog):
    from backend.config import settings
    from backend.services.openai_service import OpenAIService
    marker = "test-only-secret-marker"
    monkeypatch.setattr(settings, "proxy_api_key", SecretStr(marker))
    client = Mock()
    client.chat.completions.create.return_value = Mock(
        choices=[Mock(message=Mock(content='{"summary":"mocked analysis"}'))], usage=None,
    )
    constructor = Mock(return_value=client)
    monkeypatch.setattr("backend.services.openai_service.OpenAI", constructor)
    service = OpenAIService()
    constructor.assert_not_called()
    with caplog.at_level("INFO"):
        assert (await service.analyze_text("Offline text")).summary == "mocked analysis"
        await service.analyze_image("test-only-image")
        constructor.assert_called_once_with(
            api_key=marker, base_url=settings.proxy_api_base_url, timeout=settings.ai_timeout_seconds,
        )
        service.close()
        service.close()
    client.close.assert_called_once_with()
    assert service._client is None
    assert marker not in caplog.text
