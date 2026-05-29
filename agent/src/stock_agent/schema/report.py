"""LLM 输出报告的 Pydantic 数据模型（agent 自包含）"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


VALID_SEVERITIES = {"low", "medium", "high", "critical"}
VALID_HOLDING_ACTION_TYPES = {"buy", "reduce", "hold", "watch", "rebalance", "classify_required"}


class ReportSummary(BaseModel):
    model_config = ConfigDict(extra="ignore")

    health_score: int | None = Field(default=None, ge=0, le=100)
    status: str = "unknown"
    brief: str = ""


class DiagnosisItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = ""
    title: str = ""
    severity: str = "medium"
    explanation: str = ""
    evidence_refs: list[str] = Field(default_factory=list)


class HoldingAnalysis(BaseModel):
    model_config = ConfigDict(extra="ignore")

    target_code: str = ""
    target_name: str = ""
    action_type: str = "watch"
    title: str = ""
    reason: str = ""
    evidence_refs: list[str] = Field(default_factory=list)


class WatchCondition(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = ""
    target_code: str = ""
    metric: str = ""
    condition: str = ""
    reason: str = ""
    evidence_refs: list[str] = Field(default_factory=list)


class QuestionItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = ""
    question: str = ""
    reason: str = ""
    evidence_refs: list[str] = Field(default_factory=list)


class AgentReport(BaseModel):
    model_config = ConfigDict(extra="ignore")

    schema_version: int = 1
    summary: ReportSummary = Field(default_factory=ReportSummary)
    diagnosis: list[DiagnosisItem] = Field(default_factory=list)
    holding_analysis: list[HoldingAnalysis] = Field(default_factory=list)
    watch_conditions: list[WatchCondition] = Field(default_factory=list)
    questions: list[QuestionItem] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
