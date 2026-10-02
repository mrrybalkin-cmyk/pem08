import pytest
from pydantic import ValidationError

from backend.models.analysis import (
    AnalysisScorecard, CompetitorAnalysis, EvidenceItem, PreparedAnalysisInput,
    ScoreItem, SourceType,
)


def test_full_analysis_and_json_round_trip(analysis_payload):
    model = CompetitorAnalysis.model_validate(analysis_payload)
    assert isinstance(model.scorecard, AnalysisScorecard)
    assert isinstance(model.scorecard.trust, ScoreItem)
    assert isinstance(model.evidence[0], EvidenceItem)
    assert model.evidence[0].confidence.value == "high"
    assert CompetitorAnalysis.model_validate_json(model.model_dump_json(), strict=True) == model
    assert model.model_dump(mode="json") == analysis_payload


@pytest.mark.parametrize("field", ["executive_summary", "positioning", "scorecard"])
def test_required_analysis_fields(analysis_payload, field):
    del analysis_payload[field]
    with pytest.raises(ValidationError):
        CompetitorAnalysis.model_validate(analysis_payload)


@pytest.mark.parametrize("score", [0, 10])
def test_score_boundaries(score):
    assert ScoreItem(score=score, rationale="Supported").score == score


@pytest.mark.parametrize("score", [-1, 11, "5", 5.1, True, None])
def test_invalid_score(score):
    with pytest.raises(ValidationError):
        ScoreItem(score=score, rationale="Supported")


@pytest.mark.parametrize("field,minimum,maximum", [
    ("executive_summary", 10, 2500), ("positioning", 3, 1500),
])
def test_analysis_text_bounds(analysis_payload, field, minimum, maximum):
    for value in ("A" * (minimum - 1), "A" * (maximum + 1), 123):
        analysis_payload[field] = value
        with pytest.raises(ValidationError):
            CompetitorAnalysis.model_validate(analysis_payload)
    for length in (minimum, maximum):
        analysis_payload[field] = "A" * length
        CompetitorAnalysis.model_validate(analysis_payload)


@pytest.mark.parametrize("field", [
    "target_audience", "value_propositions", "differentiators", "strengths", "gaps",
    "marketing_messages", "opportunities", "recommended_actions", "limitations",
])
def test_analysis_list_bounds_and_types(analysis_payload, field):
    for value in (["A"] * 9, [123], "not a list"):
        analysis_payload[field] = value
        with pytest.raises(ValidationError):
            CompetitorAnalysis.model_validate(analysis_payload)
    analysis_payload[field] = ["A"] * 8
    CompetitorAnalysis.model_validate(analysis_payload)


@pytest.mark.parametrize("field,minimum,maximum", [
    ("category", 2, 80), ("finding", 3, 700), ("evidence", 1, 1200),
    ("source_hint", 1, 300),
])
def test_evidence_bounds(analysis_payload, field, minimum, maximum):
    evidence = analysis_payload["evidence"][0]
    for length in (minimum - 1, maximum + 1):
        evidence[field] = "A" * length
        with pytest.raises(ValidationError):
            EvidenceItem.model_validate(evidence)
    for length in (minimum, maximum):
        evidence[field] = "A" * length
        EvidenceItem.model_validate(evidence)


def test_nested_constraints_and_unknown_fields(analysis_payload):
    for rationale in ("ab", "A" * 701, 123):
        with pytest.raises(ValidationError):
            ScoreItem(score=5, rationale=rationale)
    evidence = analysis_payload["evidence"][0]
    with pytest.raises(ValidationError):
        EvidenceItem.model_validate({**evidence, "confidence": "certain"})
    for field in evidence:
        with pytest.raises(ValidationError):
            EvidenceItem.model_validate({key: value for key, value in evidence.items() if key != field})
    analysis_payload["evidence"] = [evidence] * 21
    with pytest.raises(ValidationError):
        CompetitorAnalysis.model_validate(analysis_payload)
    for model, payload in (
        (ScoreItem, {"score": 5, "rationale": "Supported"}),
        (EvidenceItem, evidence), (AnalysisScorecard, analysis_payload["scorecard"]),
    ):
        with pytest.raises(ValidationError):
            model.model_validate({**payload, "unknown": True})
    analysis_payload["evidence"] = [evidence]
    with pytest.raises(ValidationError):
        CompetitorAnalysis.model_validate({**analysis_payload, "unknown": True})
    del analysis_payload["scorecard"]["trust"]
    with pytest.raises(ValidationError):
        CompetitorAnalysis.model_validate(analysis_payload)


def test_prepared_input_contract():
    prepared = PreparedAnalysisInput(
        competitor_name="Alpha", source_type="text", source_label="Описание",
        text_context="Автоматизация для команд", origin_metadata={"source_id": "text-1"},
    )
    assert prepared.source_type == SourceType.text
    assert prepared.image_inputs == []
    assert PreparedAnalysisInput.model_validate_json(prepared.model_dump_json()) == prepared
