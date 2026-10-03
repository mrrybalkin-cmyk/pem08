import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import SecretStr

from backend.models.analysis import CompetitorAnalysis, PreparedAnalysisInput, SourceType
from backend.services.ai_service import (
    AIConfigurationError as V2ConfigurationError, AIProviderError,
    AIProviderTimeoutError, AIResponseError, AIService,
)


@pytest.fixture
def prepared_input():
    return PreparedAnalysisInput(
        competitor_name="Alpha", source_type="text", source_label="Описание",
        text_context="Автоматизация для команд",
        origin_metadata={"source_id": "text-1", "origin": "manual"},
    )


def completion(parsed, *, finish_reason="stop", refusal=None):
    return SimpleNamespace(choices=[SimpleNamespace(
        finish_reason=finish_reason,
        message=SimpleNamespace(parsed=parsed, refusal=refusal),
    )])


@pytest.fixture
def v2_boundary(monkeypatch, analysis_payload):
    from backend.config import settings
    monkeypatch.setattr(settings, "ai_api_key", SecretStr("test-only-v2-key"))
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(
            parse=AsyncMock(return_value=completion(CompetitorAnalysis.model_validate(analysis_payload))),
        )),
        close=AsyncMock(),
    )
    constructor = Mock(return_value=client)
    monkeypatch.setattr("backend.services.ai_service.AsyncOpenAI", constructor)
    return client, constructor


@pytest.mark.asyncio
async def test_v2_missing_credentials_does_not_construct_client(monkeypatch, prepared_input):
    from backend.config import settings
    monkeypatch.setattr(settings, "ai_api_key", SecretStr(""))
    constructor = Mock(side_effect=AssertionError("Unexpected client"))
    monkeypatch.setattr("backend.services.ai_service.AsyncOpenAI", constructor)
    service = AIService()
    await service.close()
    with pytest.raises(V2ConfigurationError, match="AI_API_KEY"):
        await service.analyze_source(prepared_input)
    constructor.assert_not_called()


@pytest.mark.asyncio
async def test_v2_lazy_config_prompt_validation_and_cleanup(v2_boundary, prepared_input, analysis_payload, monkeypatch, caplog):
    from backend.config import settings
    from backend.services.prompt_service import ANALYSIS_PROMPT_VERSION
    monkeypatch.setattr(settings, "ai_model", "configured-test-model")
    monkeypatch.setattr(settings, "ai_base_url", "https://provider.invalid/v1")
    monkeypatch.setattr(settings, "ai_timeout_seconds", 12.5)
    monkeypatch.setattr(settings, "ai_reasoning_effort", "low")
    client, constructor = v2_boundary
    service = AIService()
    constructor.assert_not_called()
    result = await service.analyze_source(prepared_input)
    assert isinstance(result, CompetitorAnalysis)
    assert result.model_dump(mode="json") == analysis_payload
    constructor.assert_called_once_with(
        api_key="test-only-v2-key", base_url="https://provider.invalid/v1", timeout=12.5,
    )
    kwargs = client.chat.completions.parse.await_args.kwargs
    assert kwargs["model"] == "configured-test-model"
    assert kwargs["reasoning_effort"] == "low"
    assert kwargs["response_format"] is CompetitorAnalysis
    assert json.loads(kwargs["messages"][1]["content"]) == prepared_input.model_dump(mode="json")
    policy = kwargs["messages"][0]["content"]
    for requirement in ("русском", "evidence", "confidence", "limitations", "rationale", "0–10", "данные источника"):
        assert requirement in policy
    assert ANALYSIS_PROMPT_VERSION == "competitor-analysis-v2.0"
    await service.analyze_source(prepared_input)
    constructor.assert_called_once()
    await service.close()
    await service.close()
    client.close.assert_awaited_once()
    assert service._client is None
    await service.analyze_source(prepared_input)
    assert constructor.call_count == 2
    await service.close()
    assert "test-only-v2-key" not in caplog.text


@pytest.mark.asyncio
async def test_v2_empty_reasoning_effort_is_omitted(v2_boundary, prepared_input, monkeypatch):
    from backend.config import settings
    monkeypatch.setattr(settings, "ai_reasoning_effort", " ")
    service = AIService()
    await service.analyze_source(prepared_input)
    assert "reasoning_effort" not in v2_boundary[0].chat.completions.parse.await_args.kwargs
    await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["missing", "refusal", "length", "filter", "empty", "partial", "nested_partial", "mutated"])
async def test_v2_rejects_invalid_output(v2_boundary, prepared_input, analysis_payload, invalid):
    parsed = CompetitorAnalysis.model_validate(analysis_payload)
    output = completion(parsed)
    if invalid == "missing":
        output.choices[0].message.parsed = None
    elif invalid == "refusal":
        output.choices[0].message.refusal = "Cannot comply"
    elif invalid in {"length", "filter"}:
        output.choices[0].finish_reason = "length" if invalid == "length" else "content_filter"
    elif invalid == "empty":
        output.choices = []
    elif invalid == "partial":
        del analysis_payload["limitations"]
        output = completion(CompetitorAnalysis.model_validate(analysis_payload))
    elif invalid == "nested_partial":
        del analysis_payload["scorecard"]["visual_consistency"]
        output = completion(CompetitorAnalysis.model_validate(analysis_payload))
    else:
        parsed.scorecard.trust.score = 11
    v2_boundary[0].chat.completions.parse.return_value = output
    service = AIService()
    with pytest.raises(AIResponseError):
        await service.analyze_source(prepared_input)
    await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("timeout", [False, True])
async def test_v2_provider_failures(v2_boundary, prepared_input, timeout):
    from openai import APIError, APITimeoutError
    import httpx2
    request = httpx2.Request("POST", "https://provider.invalid/v1/chat/completions")
    error = APITimeoutError(request=request) if timeout else APIError("Injected", request, body=None)
    v2_boundary[0].chat.completions.parse.side_effect = error
    service = AIService()
    with pytest.raises(AIProviderTimeoutError if timeout else AIProviderError) as raised:
        await service.analyze_source(prepared_input)
    assert raised.value.__cause__ is error
    await service.close()


@pytest.mark.asyncio
async def test_v2_request_does_not_block_event_loop(v2_boundary, prepared_input):
    entered, release = asyncio.Event(), asyncio.Event()
    output = v2_boundary[0].chat.completions.parse.return_value

    async def waiting_provider(**kwargs):
        entered.set()
        await release.wait()
        return output

    v2_boundary[0].chat.completions.parse.side_effect = waiting_provider
    service = AIService()
    task = asyncio.create_task(service.analyze_source(prepared_input))
    await asyncio.wait_for(entered.wait(), timeout=1)
    assert not task.done()
    await asyncio.sleep(0)
    release.set()
    assert isinstance(await task, CompetitorAnalysis)
    await service.close()


@pytest.mark.asyncio
async def test_v2_text_cannot_contain_images(v2_boundary, prepared_input):
    prepared_input.image_inputs = ["data:image/png;base64,test-only"]
    with pytest.raises(ValueError, match="text input cannot contain images"):
        await AIService().analyze_source(prepared_input)
    v2_boundary[1].assert_not_called()


@pytest.mark.asyncio
async def test_v2_image_multimodal_structured_result(v2_boundary, prepared_input, analysis_payload):
    import base64
    from io import BytesIO
    from PIL import Image
    image = BytesIO()
    Image.new("RGB", (3, 3)).save(image, format="PNG")
    data_url = "data:image/png;base64," + base64.b64encode(image.getvalue()).decode("ascii")
    prepared_input.source_type = SourceType.image
    prepared_input.image_inputs = [data_url]
    for field in ("visual_consistency", "ux_clarity"):
        analysis_payload["scorecard"][field] = {"score": 7, "rationale": "Видимые элементы согласованы"}
    client, _ = v2_boundary
    client.chat.completions.parse.return_value = completion(CompetitorAnalysis.model_validate(analysis_payload))
    service = AIService()
    result = await service.analyze_source(prepared_input)
    assert result.model_dump(mode="json") == analysis_payload
    parts = client.chat.completions.parse.await_args.kwargs["messages"][1]["content"]
    assert parts[0]["type"] == "text"
    assert json.loads(parts[0]["text"])["source_type"] == "image"
    assert data_url not in parts[0]["text"]
    assert parts[1] == {"type": "image_url", "image_url": {"url": data_url}}
    assert client.chat.completions.parse.await_args.kwargs["response_format"] is CompetitorAnalysis
    await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["no_image", "remote", "bad_base64", "pdf", "url"])
async def test_v2_rejects_unsupported_image_inputs(v2_boundary, prepared_input, kind):
    prepared_input.source_type = SourceType.image
    prepared_input.image_inputs = [] if kind == "no_image" else ["https://example.invalid/a.png" if kind == "remote" else "data:image/png;base64,!!!"]
    if kind in {"pdf", "url"}:
        prepared_input.source_type = SourceType(kind)
    with pytest.raises(ValueError):
        await AIService().analyze_source(prepared_input)
    v2_boundary[1].assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("visual", [False, True])
async def test_v2_pdf_structured_payload(v2_boundary, prepared_input, analysis_payload, visual):
    from backend.services.document_service import prepare_pdf
    from test_documents import pdf_bytes
    prepared = prepare_pdf(pdf_bytes(2, "scanned" if visual else "text"),
                           "application/pdf", 100000, 8, 30000)
    prepared_input.source_type = SourceType.pdf
    prepared_input.text_context = prepared.extracted_text
    prepared_input.origin_metadata = prepared.metadata
    prepared_input.image_inputs = prepared.image_inputs if visual else []
    client, constructor = v2_boundary
    service = AIService()
    constructor.assert_not_called()
    result = await service.analyze_source(prepared_input)
    assert result.model_dump(mode="json") == analysis_payload
    kwargs = client.chat.completions.parse.await_args.kwargs
    assert kwargs["response_format"] is CompetitorAnalysis
    content = kwargs["messages"][1]["content"]
    context = json.loads(content[0]["text"] if visual else content)
    assert context["source_type"] == "pdf"
    assert context["origin_metadata"]["selected_pages"] == [1, 2]
    assert "only provided pages" in kwargs["messages"][0]["content"]
    if visual:
        assert len(content) == 3
        assert content[1]["image_url"]["url"] == prepared.image_inputs[0]
        assert "base64" not in content[0]["text"]
    await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["invalid", "timeout"])
async def test_v2_pdf_provider_failure(v2_boundary, prepared_input, failure):
    from openai import APITimeoutError
    import httpx2
    prepared_input.source_type = SourceType.pdf
    client, _ = v2_boundary
    if failure == "timeout":
        client.chat.completions.parse.side_effect = APITimeoutError(request=httpx2.Request("POST", "https://test.invalid"))
    else:
        client.chat.completions.parse.return_value = completion(None)
    service = AIService()
    with pytest.raises(AIProviderTimeoutError if failure == "timeout" else AIResponseError):
        await service.analyze_source(prepared_input)
    await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["timeout", "invalid"])
async def test_v2_image_errors_remain_controlled(v2_boundary, prepared_input, failure):
    from openai import APITimeoutError
    import httpx2
    prepared_input.source_type = SourceType.image
    prepared_input.image_inputs = ["data:image/png;base64,dGVzdA=="]
    client, _ = v2_boundary
    if failure == "timeout":
        client.chat.completions.parse.side_effect = APITimeoutError(request=httpx2.Request("POST", "https://test.invalid"))
    else:
        client.chat.completions.parse.return_value = completion(None)
    service = AIService()
    with pytest.raises(AIProviderTimeoutError if failure == "timeout" else AIResponseError):
        await service.analyze_source(prepared_input)
    await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("content_kind", ["valid", "malformed", "out_of_range", "missing"])
async def test_actual_sdk_strict_wire_and_parser(monkeypatch, prepared_input, analysis_payload, content_kind):
    # Exercise the installed SDK's real schema conversion/parser at _post,
    # with no HTTP transport, network or paid request.
    from openai import AsyncOpenAI
    from openai.types.chat import ChatCompletion
    from backend.config import settings
    client = AsyncOpenAI(api_key="test-only-wire-key")
    if content_kind == "out_of_range":
        analysis_payload["scorecard"]["trust"]["score"] = 11
    if content_kind == "missing":
        del analysis_payload["limitations"]
    content = "not JSON" if content_kind == "malformed" else json.dumps(analysis_payload)
    raw = ChatCompletion.model_validate({
        "id": "test", "object": "chat.completion", "created": 0, "model": "test",
        "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": content}}],
    })

    async def fake_post(path, **kwargs):
        assert path == "/chat/completions"
        wire = kwargs["body"]["response_format"]
        assert wire["type"] == "json_schema"
        assert wire["json_schema"]["strict"] is True
        schema = wire["json_schema"]["schema"]
        for node in [schema, *schema["$defs"].values()]:
            if node.get("type") == "object":
                assert node["additionalProperties"] is False
                assert set(node["required"]) == set(node["properties"])
        return kwargs["options"]["post_parser"](raw)

    monkeypatch.setattr(client.chat.completions, "_post", fake_post)
    monkeypatch.setattr(settings, "ai_api_key", SecretStr("test-only-wire-key"))
    monkeypatch.setattr("backend.services.ai_service.AsyncOpenAI", Mock(return_value=client))
    service = AIService()
    try:
        if content_kind == "valid":
            assert (await service.analyze_source(prepared_input)).model_dump(mode="json") == analysis_payload
        else:
            with pytest.raises(AIResponseError):
                await service.analyze_source(prepared_input)
    finally:
        await service.close()


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
