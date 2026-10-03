"""Lazy async v2 text/image analysis; no legacy parser or persistence dependencies."""

import base64

from openai import (
    APIError, APITimeoutError, AsyncOpenAI,
    ContentFilterFinishReasonError, LengthFinishReasonError,
)
from pydantic import BaseModel, ValidationError

from backend.config import settings
from backend.models.analysis import CompetitorAnalysis, PreparedAnalysisInput, SourceType
from backend.services.prompt_service import build_analysis_messages


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
        if prepared_input.source_type not in {SourceType.text, SourceType.image, SourceType.pdf}:
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
        options = {}
        if settings.ai_reasoning_effort.strip():
            options["reasoning_effort"] = settings.ai_reasoning_effort.strip()
        try:
            completion = await self.client.chat.completions.parse(
                model=settings.ai_model,
                messages=build_analysis_messages(prepared_input),
                response_format=CompetitorAnalysis,
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
        if not isinstance(parsed, CompetitorAnalysis):
            raise AIResponseError("Missing structured CompetitorAnalysis")
        _require_complete_output(parsed)
        try:
            # Revalidate even SDK model instances; never accept model_construct
            # or mutated objects as a validation bypass.
            return CompetitorAnalysis.model_validate_json(parsed.model_dump_json(), strict=True)
        except ValidationError as exc:
            raise AIResponseError("Invalid structured AI response") from exc

    async def close(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            await client.close()


ai_service = AIService()
