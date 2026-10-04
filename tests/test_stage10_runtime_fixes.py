import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from backend.services.ai_service import AIService


ROOT = Path(__file__).resolve().parents[1]


class DummyStructuredResult(BaseModel):
    value: str


class FakeCompletions:
    def __init__(self, response, delay=0):
        self.response = response
        self.delay = delay

    async def parse(self, **_kwargs):
        if self.delay:
            await asyncio.sleep(self.delay)

        return self.response


class FakeClient:
    def __init__(self, response, delay=0):
        self.chat = SimpleNamespace(
            completions=FakeCompletions(response, delay)
        )


def completion(prompt_tokens, completion_tokens):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason="stop",
                message=SimpleNamespace(
                    refusal=None,
                    parsed=DummyStructuredResult(value="ok"),
                ),
            )
        ],
        usage=SimpleNamespace(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        ),
    )


@pytest.mark.asyncio
async def test_structured_request_captures_token_usage():
    service = AIService()
    service._client = FakeClient(
        completion(123, 45)
    )

    result = await service._structured_request(
        [{"role": "user", "content": "test"}],
        DummyStructuredResult,
    )

    assert result == DummyStructuredResult(value="ok")
    assert service.token_usage == (123, 45)


@pytest.mark.asyncio
async def test_token_usage_is_task_local():
    async def run(
        prompt_tokens,
        completion_tokens,
        delay,
    ):
        service = AIService()
        service._client = FakeClient(
            completion(
                prompt_tokens,
                completion_tokens,
            ),
            delay=delay,
        )

        await service._structured_request(
            [{"role": "user", "content": "test"}],
            DummyStructuredResult,
        )

        return service.token_usage

    first, second = await asyncio.gather(
        run(101, 11, 0.02),
        run(202, 22, 0.0),
    )

    assert first == (101, 11)
    assert second == (202, 22)


def test_source_and_aggregate_persist_usage():
    ingestion = (
        ROOT / "backend/services/ingestion_service.py"
    ).read_text(encoding="utf-8")

    aggregate = (
        ROOT / "backend/services/analysis_service.py"
    ).read_text(encoding="utf-8")

    assert "input_tokens=input_tokens" in ingestion
    assert "output_tokens=output_tokens" in ingestion

    assert "input_tokens=input_tokens" in aggregate
    assert "output_tokens=output_tokens" in aggregate


def test_windows_startup_disables_reload():
    text = (
        ROOT / "run.py"
    ).read_text(encoding="utf-8")

    assert 'sys.platform != "win32"' in text
    assert "reload=reload_enabled" in text


def test_env_example_uses_direct_openai():
    text = (
        ROOT / ".env.example"
    ).read_text(encoding="utf-8")

    assert "AI_PROVIDER=openai" in text
    assert (
        "AI_BASE_URL=https://api.openai.com/v1"
        in text
    )
    assert "AI_MODEL=gpt-6-luna" in text
    assert "proxyapi.ru" not in text

    key_lines = [
        line
        for line in text.splitlines()
        if line.startswith("AI_API_KEY=")
    ]

    assert key_lines == ["AI_API_KEY="]
