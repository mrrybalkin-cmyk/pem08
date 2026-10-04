"""Aggregate/comparison orchestration over persisted validated evidence only."""

from threading import Event
from time import perf_counter

from pydantic import ValidationError
from sqlalchemy.orm import Session

from backend.config import settings
from backend.models.analysis import (
    AggregateCoverage, CompetitorAnalysis, ComparisonResult, PreparedAggregateInput,
    PreparedComparisonInput, PreparedCompetitorProfile, PreparedSourceAnalysis,
)
from backend.models.api import AnalysisResponse
from backend.repositories import analyses, comparisons, competitors, sources
from backend.services.ai_service import (
    AIResponseError, ai_service, _require_complete_output, validate_comparison_participants,
)
from backend.services.ingestion_service import ResourceNotFound, _offload
from backend.services.prompt_service import AGGREGATE_PROMPT_VERSION


class AnalysisDataNotReady(ValueError):
    """Existing competitors have no usable current evidence/aggregate."""


def prepare_aggregate(db: Session, competitor_id: str) -> PreparedAggregateInput:
    try:
        competitor = competitors.get_competitor(db, competitor_id)
        if competitor is None:
            raise ResourceNotFound("Competitor not found")
        included, omitted = [], []
        all_sources = sources.list_sources(db, competitor_id)
        for source in all_sources:
            snapshot = sources.get_latest_snapshot(db, source.id)
            analysis = analyses.get_latest_snapshot_analysis(db, snapshot.id) if snapshot else None
            if analysis is None:
                omitted.append(source.id)
                continue
            included.append(PreparedSourceAnalysis(
                source_id=source.id, snapshot_id=snapshot.id, source_type=source.source_type,
                source_label=source.label, analysis_id=analysis.id, captured_at=snapshot.captured_at.isoformat(),
                result=CompetitorAnalysis.model_validate_json(analysis.result_json, strict=True),
            ))
        if not included:
            raise AnalysisDataNotReady("No current source analyses available")
        return PreparedAggregateInput(
            competitor_id=competitor.id, competitor_name=competitor.name, sources=tuple(included),
            coverage=AggregateCoverage(
                total_sources=len(all_sources), sources_with_current_analysis=len(included),
                sources_without_current_analysis=len(omitted),
                included_source_ids=tuple(item.source_id for item in included), omitted_source_ids=tuple(omitted),
            ),
        )
    finally:
        db.rollback()  # No SQLite read transaction across provider await.


def prepare_comparison(db: Session, competitor_ids: list[str]) -> PreparedComparisonInput:
    try:
        # Check all IDs before checking readiness so mixed unknown inputs always give 404.
        selected = [competitors.get_competitor(db, cid) for cid in competitor_ids]
        if any(item is None for item in selected):
            raise ResourceNotFound("Competitor not found")
        profiles = []
        for competitor in selected:
            analysis = analyses.get_latest_aggregate(db, competitor.id)
            if analysis is None:
                raise AnalysisDataNotReady("Create aggregate analyses before comparing")
            profiles.append(PreparedCompetitorProfile(
                competitor_id=competitor.id, competitor_name=competitor.name, analysis_id=analysis.id,
                created_at=analysis.created_at.isoformat(),
                result=CompetitorAnalysis.model_validate_json(analysis.result_json, strict=True),
            ))
        return PreparedComparisonInput(competitors=tuple(profiles))
    finally:
        db.rollback()


def validated_result(result, model):
    if not isinstance(result, model):
        raise AIResponseError("Missing structured response model")
    _require_complete_output(result)
    try:
        return model.model_validate_json(result.model_dump_json(), strict=True)
    except ValidationError as exc:
        raise AIResponseError("Invalid structured response") from exc


class AnalysisService:
    async def aggregate_competitor(self, db: Session, competitor_id: str) -> AnalysisResponse:
        prepared = await _offload(lambda: prepare_aggregate(db, competitor_id))
        model_id = settings.ai_model
        start = perf_counter()
        ai_service.reset_token_usage()
        result = validated_result(
            await ai_service.aggregate_competitor(prepared),
            CompetitorAnalysis,
        )
        input_tokens, output_tokens = ai_service.token_usage
        cancelled = Event()

        def persist():
            try:
                row = analyses.create_analysis(
                    db, competitor_id=competitor_id, snapshot_id=None, analysis_type="aggregate",
                    model_id=model_id,
                    prompt_version=AGGREGATE_PROMPT_VERSION,
                    result=result,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    duration_ms=round((perf_counter() - start) * 1000),
                )
                response = AnalysisResponse.model_validate(row)
                if cancelled.is_set():
                    db.rollback()
                    return
                db.commit()
                return response
            except BaseException:
                db.rollback()
                raise

        return await _offload(persist, on_cancel=cancelled.set)

    async def compare_competitors(self, db: Session, competitor_ids: list[str]) -> ComparisonResult:
        prepared = await _offload(lambda: prepare_comparison(db, competitor_ids))
        model_id = settings.ai_model
        result = validated_result(await ai_service.compare_competitors(prepared), ComparisonResult)
        result = validate_comparison_participants(result, prepared)
        cancelled = Event()

        def persist():
            try:
                comparisons.create_comparison(db, competitor_ids=competitor_ids, model_id=model_id, result=result)
                if cancelled.is_set():
                    db.rollback()
                    return
                db.commit()
            except BaseException:
                db.rollback()
                raise

        await _offload(persist, on_cancel=cancelled.set)
        return result


analysis_service = AnalysisService()
