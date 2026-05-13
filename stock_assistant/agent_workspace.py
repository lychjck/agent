from typing import Any

from .agent import classify_for_agent, fund_analysis_result, holding_from_result, holdings_from_results
from .agent_llm import build_agent_llm_context
from .analysis import analyze_one
from .market import fetch_bars
from .memory import build_agent_snapshot, diff_agent_snapshots, load_latest_agent_snapshot
from .models import Holding, InstrumentClassification, analysis_result_to_dict, holding_to_dict
from .portfolio import generate_portfolio_observations, summarize_portfolio
from .tzzb import fetch_tzzb_holdings
from .utils import log


class AgentWorkspace:
    def __init__(
        self,
        config: dict[str, Any],
        *,
        holdings: list[Holding] | None = None,
        cached_results: list[dict[str, Any]] | None = None,
    ) -> None:
        self.config = config
        self._holdings = holdings
        self.cached_results = cached_results
        self.source = "provided" if holdings is not None else "unknown"
        self.ledger_summary: dict[str, Any] = {}
        self._technical_by_code: dict[str, dict[str, Any]] = {}
        self._classifications: dict[str, InstrumentClassification] | None = None
        self._portfolio_summary: dict[str, Any] | None = None
        self._observations: list[dict[str, Any]] | None = None
        self._previous_snapshot: dict[str, Any] | None | bool = False
        if cached_results is not None:
            self._holdings = holdings_from_results(cached_results)
            self.source = "cached_results"
            for item in cached_results:
                holding = holding_from_result(item)
                if holding is not None:
                    self._technical_by_code[holding.code] = item

    @property
    def holdings(self) -> list[Holding]:
        return self.ensure_holdings()

    @property
    def technical_results(self) -> list[dict[str, Any]]:
        return list(self._technical_by_code.values())

    @property
    def classifications(self) -> dict[str, InstrumentClassification]:
        return self.ensure_classifications()

    @property
    def observations(self) -> list[dict[str, Any]]:
        self.ensure_portfolio_profile()
        return self._observations or []

    def ensure_holdings(self) -> list[Holding]:
        if self._holdings is not None:
            return self._holdings
        if str(self.config.get("ledger", {}).get("mode", "")).strip().lower() != "tzzb_api":
            raise RuntimeError("tool_agent 模式当前需要 ledger.mode=tzzb_api、cached_results 或显式 holdings")
        holdings, source_path, ledger_summary = fetch_tzzb_holdings(self.config)
        self._holdings = holdings
        self.source = str(source_path)
        self.ledger_summary = ledger_summary
        return holdings

    def total_value(self) -> float | None:
        total = sum(item.market_value or 0 for item in self.ensure_holdings())
        return total or None

    def ensure_technical(self, codes: list[str] | None = None) -> list[dict[str, Any]]:
        holdings = self.ensure_holdings()
        requested = set(codes or [item.code for item in holdings])
        total_value = self.total_value()
        for holding in holdings:
            if holding.code not in requested or holding.code in self._technical_by_code:
                continue
            if holding.asset_type == "fund":
                self._technical_by_code[holding.code] = fund_analysis_result(holding, total_value)
                continue
            try:
                bars = fetch_bars(holding.code, self.config)
                self._technical_by_code[holding.code] = analysis_result_to_dict(
                    analyze_one(holding, bars, self.config, total_value)
                )
            except Exception as exc:  # noqa: BLE001
                log(f"tool_agent 分析 {holding.code} 失败: {exc}", level="WARN", name="agent_workspace")
                self._technical_by_code[holding.code] = {
                    "holding": holding_to_dict(holding),
                    "ok": False,
                    "action": "行情失败",
                    "reason": str(exc),
                }
        return [self._technical_by_code[code] for code in requested if code in self._technical_by_code]

    def ensure_classifications(self) -> dict[str, InstrumentClassification]:
        if self._classifications is not None:
            return self._classifications
        self._classifications = {
            holding.code: classify_for_agent(holding, self.config)
            for holding in self.ensure_holdings()
        }
        return self._classifications

    def ensure_portfolio_profile(self) -> dict[str, Any]:
        if self._portfolio_summary is not None:
            return self._portfolio_summary
        self._portfolio_summary = summarize_portfolio(
            self.ensure_holdings(),
            self.ensure_classifications(),
            self.config,
        )
        self._observations = generate_portfolio_observations(self._portfolio_summary)
        return self._portfolio_summary

    def previous_snapshot(self) -> dict[str, Any] | None:
        if self._previous_snapshot is False:
            self._previous_snapshot = load_latest_agent_snapshot(self.config)
        return self._previous_snapshot if isinstance(self._previous_snapshot, dict) else None

    def ensure_history_diff(self) -> dict[str, Any]:
        current_facts = {
            "ledger_summary": self.ledger_summary,
            "portfolio": self.ensure_portfolio_profile(),
            "classifications": self.ensure_classifications(),
            "technical_results": self.technical_results,
            "risk_flags": [],
            "candidate_actions": [],
        }
        return diff_agent_snapshots(self.previous_snapshot(), current_facts)

    def build_llm_context(self) -> dict[str, Any]:
        return build_agent_llm_context(
            holdings=self.ensure_holdings(),
            classifications=self.ensure_classifications(),
            technical_results=self.technical_results,
            portfolio_summary=self.ensure_portfolio_profile(),
            observations=self.observations,
            risk_flags=[],
            candidate_actions=[],
            history_diff=self.ensure_history_diff(),
            ledger_summary=self.ledger_summary,
            config=self.config,
        )

    def build_snapshot(self, agent_report: dict[str, Any], model: str | None) -> dict[str, Any]:
        return build_agent_snapshot(
            source=self.source,
            ledger_summary=self.ledger_summary,
            holdings=self.ensure_holdings(),
            classifications=self.ensure_classifications(),
            technical_results=self.technical_results,
            summary=self.ensure_portfolio_profile(),
            observations=self.observations,
            risk_flags=[],
            candidate_actions=[],
            agent_report=agent_report,
            model=model,
        )
