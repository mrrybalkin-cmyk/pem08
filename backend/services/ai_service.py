"""Lazy async structured source, aggregate and comparison provider boundary."""

import base64

from openai import (
    APIError, APITimeoutError, AsyncOpenAI,
    ContentFilterFinishReasonError, LengthFinishReasonError,
)
from pydantic import BaseModel, ValidationError

from backend.config import settings
from backend.models.analysis import (
    CompetitorAnalysis, ComparisonResult, PreparedAnalysisInput, PreparedAggregateInput,
    PreparedComparisonInput, SourceType,
)
from backend.services.prompt_service import build_analysis_messages, build_aggregate_messages, build_comparison_messages


class AIConfigurationError(RuntimeError):
    """V2 analysis is not configured; no provider request was attempted."""


class AIProviderError(RuntimeError):
    """The configured provider request failed."""


class AIProviderTimeoutError(AIProviderError):
    """Provider timeout, distinguishable for a future HTTP 504 mapping."""


class AIResponseError(AIProviderError):
    """Provider output violates the full structured analysis contract."""


def _require_complete_output(value: BaseModel) -> None:
    # SDK validation honors Pydantic defaults. Strict wire output must explicitly
    # supply every field, including empty arrays and nullable score dimensions.
    if value.model_fields_set != set(type(value).model_fields):
        raise AIResponseError("Incomplete structured AI response")
    for field in type(value).model_fields:
        child = getattr(value, field)
        if isinstance(child, BaseModel):
            _require_complete_output(child)
        elif isinstance(child, list):
            for item in child:
                if isinstance(item, BaseModel):
                    _require_complete_output(item)


class AIService:
    def __init__(self):
        self._client: AsyncOpenAI | None = None

    @property
    def client(self) -> AsyncOpenAI:
        if self._client is None:
            key = settings.ai_api_key.get_secret_value().strip()
            if not key:
                raise AIConfigurationError("Set AI_API_KEY to use v2 analysis")
            self._client = AsyncOpenAI(
                api_key=key, base_url=settings.ai_base_url,
                timeout=settings.ai_timeout_seconds,
            )
        return self._client

    async def analyze_source(self, prepared_input: PreparedAnalysisInput) -> CompetitorAnalysis:
        if prepared_input.source_type not in {SourceType.text, SourceType.image, SourceType.pdf, SourceType.url}:
            raise ValueError("Only prepared text/image/PDF input is supported")
        if prepared_input.source_type == SourceType.text and prepared_input.image_inputs:
            raise ValueError("Prepared text input cannot contain images")
        if prepared_input.source_type == SourceType.image or prepared_input.image_inputs:
            if not prepared_input.image_inputs:
                raise ValueError("Prepared image input requires an image")
            for url in prepared_input.image_inputs:
                header, separator, encoded = url.partition(",")
                if not separator or header not in {
                    "data:image/jpeg;base64", "data:image/png;base64", "data:image/webp;base64",
                } or not base64.b64decode(encoded, validate=True):
                    raise ValueError("Prepared images must be supported base64 data URLs")
        return await self._structured_request(build_analysis_messages(prepared_input), CompetitorAnalysis)

    async def aggregate_competitor(self, payload: PreparedAggregateInput) -> CompetitorAnalysis:
        return await self._structured_request(build_aggregate_messages(payload), CompetitorAnalysis)

    async def compare_competitors(self, payload: PreparedComparisonInput) -> ComparisonResult:
        result = await self._structured_request(build_comparison_messages(payload), ComparisonResult)
        return validate_comparison_participants(result, payload)

    async def _structured_request(self, messages: list[dict], response_model: type[BaseModel]):
        options = {}
        if settings.ai_reasoning_effort.strip():
            options["reasoning_effort"] = settings.ai_reasoning_effort.strip()
        try:
            completion = await self.client.chat.completions.parse(
                model=settings.ai_model,
                messages=messages,
                response_format=response_model,
                **options,
            )
        except APITimeoutError as exc:
            raise AIProviderTimeoutError("AI provider timed out") from exc
        except APIError as exc:
            raise AIProviderError("AI provider request failed") from exc
        except (ValidationError, ValueError, LengthFinishReasonError, ContentFilterFinishReasonError) as exc:
            raise AIResponseError("Invalid or incomplete structured AI response") from exc

        if len(completion.choices) != 1:
            raise AIResponseError("Expected one structured AI response")
        choice = completion.choices[0]
        if choice.finish_reason != "stop" or choice.message.refusal:
            raise AIResponseError("AI response refused or did not finish normally")
        parsed = choice.message.parsed
        if not isinstance(parsed, response_model):
            raise AIResponseError("Missing structured response model")
        _require_complete_output(parsed)
        try:
            # Revalidate even SDK model instances; never accept model_construct
            # or mutated objects as a validation bypass.
            return response_model.model_validate_json(parsed.model_dump_json(), strict=True)
        except ValidationError as exc:
            raise AIResponseError("Invalid structured AI response") from exc

    async def close(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            await client.close()


def validate_comparison_participants(result: ComparisonResult, payload: PreparedComparisonInput) -> ComparisonResult:
    expected = {item.competitor_id: item.competitor_name for item in payload.competitors}
    rows = {item.competitor_id: item for item in result.competitors}
    if (len(result.competitors) != len(expected) or set(rows) != set(expected)
            or any(rows[key].competitor_name != name for key, name in expected.items())):
        raise AIResponseError("Comparison participants do not match prepared input")
    ordered = result.model_copy(update={"competitors": [rows[item.competitor_id] for item in payload.competitors]})
    return ComparisonResult.model_validate_json(ordered.model_dump_json(), strict=True)


ai_service = AIService()
