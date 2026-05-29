"""Agent 自包含的报告 Schema 与校验逻辑（不依赖主项目）"""

from stock_agent.schema.report import (
    AgentReport,
    DiagnosisItem,
    HoldingAnalysis,
    QuestionItem,
    ReportSummary,
    WatchCondition,
    VALID_HOLDING_ACTION_TYPES,
    VALID_SEVERITIES,
)
from stock_agent.schema.validate import (
    agent_report_schema_hint,
    fallback_holding_analysis_from_context,
    load_agent_report_json,
    strip_json_markdown,
    validate_agent_report,
)

__all__ = [
    "AgentReport",
    "DiagnosisItem",
    "HoldingAnalysis",
    "QuestionItem",
    "ReportSummary",
    "WatchCondition",
    "VALID_HOLDING_ACTION_TYPES",
    "VALID_SEVERITIES",
    "agent_report_schema_hint",
    "fallback_holding_analysis_from_context",
    "load_agent_report_json",
    "strip_json_markdown",
    "validate_agent_report",
]
