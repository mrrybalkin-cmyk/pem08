"""V2 analysis contracts from PEM08_V2_SPEC.md, independent of legacy v1."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, JsonValue


class SourceType(StrEnum):
    text = "text"
    image = "image"
    pdf = "pdf"
    url = "url"


class ConfidenceLevel(StrEnum):
    low = "low"
    medium = "medium"
    high = "high"


class AnalysisModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ScoreItem(AnalysisModel):
    score: int = Field(ge=0, le=10, strict=True)
    rationale: str = Field(min_length=3, max_length=700)


class AnalysisScorecard(AnalysisModel):
    positioning_clarity: ScoreItem
    value_proposition: ScoreItem
    trust: ScoreItem
    cta_strength: ScoreItem
    visual_consistency: ScoreItem | None = None
    ux_clarity: ScoreItem | None = None


class EvidenceItem(AnalysisModel):
    category: str = Field(min_length=2, max_length=80)
    finding: str = Field(min_length=3, max_length=700)
    evidence: str = Field(min_length=1, max_length=1200)
    source_hint: str = Field(min_length=1, max_length=300)
    confidence: ConfidenceLevel


class CompetitorAnalysis(AnalysisModel):
    executive_summary: str = Field(min_length=10, max_length=2500)
    positioning: str = Field(min_length=3, max_length=1500)
    target_audience: list[str] = Field(default_factory=list, max_length=8)
    value_propositions: list[str] = Field(default_factory=list, max_length=8)
    differentiators: list[str] = Field(default_factory=list, max_length=8)
    strengths: list[str] = Field(default_factory=list, max_length=8)
    gaps: list[str] = Field(default_factory=list, max_length=8)
    marketing_messages: list[str] = Field(default_factory=list, max_length=8)
    scorecard: AnalysisScorecard
    evidence: list[EvidenceItem] = Field(default_factory=list, max_length=20)
    opportunities: list[str] = Field(default_factory=list, max_length=8)
    recommended_actions: list[str] = Field(default_factory=list, max_length=8)
    limitations: list[str] = Field(default_factory=list, max_length=8)


class PreparedAnalysisInput(AnalysisModel):
    """Prepared context boundary (§12); images use validated data URLs."""

    competitor_name: str
    source_type: SourceType
    source_label: str
    text_context: str
    image_inputs: list[str] = Field(default_factory=list)
    origin_metadata: dict[str, JsonValue] = Field(default_factory=dict)


class CompetitorComparisonRow(AnalysisModel):
    competitor_id: str
    competitor_name: str
    positioning: str
    strengths: list[str]
    gaps: list[str]
    positioning_clarity: int = Field(ge=0, le=10, strict=True)
    value_proposition: int = Field(ge=0, le=10, strict=True)
    trust: int = Field(ge=0, le=10, strict=True)
    cta_strength: int = Field(ge=0, le=10, strict=True)


class ComparisonResult(AnalysisModel):
    executive_summary: str
    competitors: list[CompetitorComparisonRow]
    shared_patterns: list[str]
    meaningful_differences: list[str]
    market_gaps: list[str]
    opportunities: list[str]
    limitations: list[str]


class PreparedSourceAnalysis(AnalysisModel):
    source_id: str
    snapshot_id: str
    source_type: SourceType
    source_label: str
    analysis_id: str
    captured_at: str
    result: CompetitorAnalysis


class AggregateCoverage(AnalysisModel):
    total_sources: int
    sources_with_current_analysis: int
    sources_without_current_analysis: int
    included_source_ids: tuple[str, ...]
    omitted_source_ids: tuple[str, ...]


class PreparedAggregateInput(AnalysisModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    competitor_id: str
    competitor_name: str
    sources: tuple[PreparedSourceAnalysis, ...]
    coverage: AggregateCoverage


class PreparedCompetitorProfile(AnalysisModel):
    competitor_id: str
    competitor_name: str
    analysis_id: str
    created_at: str
    result: CompetitorAnalysis


class PreparedComparisonInput(AnalysisModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    competitors: tuple[PreparedCompetitorProfile, ...]
