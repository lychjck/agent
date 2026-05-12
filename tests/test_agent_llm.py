import datetime as dt
import json
import unittest
from copy import deepcopy
from unittest.mock import patch

from stock_assistant import DEFAULTS, Bar, CandidateAction, Holding, InstrumentClassification, RiskFlag
from stock_assistant.agent_llm import (
    build_agent_llm_context,
    fallback_agent_report,
    llm_structured_kwargs,
    parse_agent_report,
    strip_json_markdown,
)
from stock_assistant.portfolio import generate_portfolio_observations, summarize_portfolio


class TestAgentLlm(unittest.TestCase):
    def setUp(self):
        self.config = deepcopy(DEFAULTS)
        self.config["ledger"]["cookie"] = "COOKIE_SHOULD_NOT_LEAK"
        self.config["llm"]["api_key"] = "KEY_SHOULD_NOT_LEAK"
        self.holding = Holding(
            code="510300",
            name="沪深300ETF",
            quantity=1000,
            cost_price=4.0,
            market_value=4200,
            profit_pct=5.0,
            day_profit=12.3,
            source_row={"Cookie": "ROW_SECRET"},
            asset_type="etf",
        )
        self.classification = InstrumentClassification(
            code="510300",
            name="沪深300ETF",
            asset_class="broad_index",
            sector="multi_sector",
            theme="csi300",
            region="china_a",
            strategy="passive_index",
            tracked_index="沪深300",
            issuer="华泰柏瑞",
            confidence=0.98,
            source="eastmoney_llm_verified",
            evidence=(
                {
                    "title": "沪深300ETF 产品资料",
                    "url": "https://example.com/fund",
                    "snippet": "跟踪沪深300指数",
                    "raw_content": "RAW_CONTENT_SHOULD_NOT_LEAK",
                    "source_tier": "tier2",
                },
            ),
            reviewed_by_user=True,
        )
        self.latest = Bar(
            date=dt.date(2026, 5, 11),
            open=4.1,
            close=4.2,
            high=4.25,
            low=4.05,
            volume=100000,
            amount=420000,
            pct_change=1.2,
        )
        self.technical_results = [
            {
                "holding": self.holding,
                "latest": self.latest,
                "ok": True,
                "ma20": 4.05,
                "ma60": 3.9,
                "ma120": 3.7,
                "ret5": 2.1,
                "ret20": 6.4,
                "rsi14": 63.2,
                "drawdown": -3.0,
                "vol20": 1.4,
                "vol_ratio": 1.1,
                "profit_pct": 5.0,
                "current_value": 4200,
                "weight": 100.0,
                "action": "持有观察",
                "reason": "价格站上 MA20 且 MA20 高于 MA60",
            }
        ]
        self.summary = summarize_portfolio(
            [self.holding],
            {self.holding.code: self.classification},
            self.config,
        )
        self.observations = generate_portfolio_observations(self.summary)
        self.risk_flags = [
            RiskFlag(
                id="risk:concentration:position:510300",
                code="510300",
                label="单只持仓集中",
                severity="medium",
                evidence=("510300 weight=100.00%",),
            )
        ]
        self.candidate_action = CandidateAction(
            id="action:hold:trend_ok:510300",
            type="hold",
            target_code="510300",
            target_name="沪深300ETF",
            priority="medium",
            reason="趋势未破坏，继续观察",
            evidence=("价格站上 MA20",),
            reason_code="trend_ok",
        )

    def context(self):
        return build_agent_llm_context(
            holdings=[self.holding],
            classifications={self.holding.code: self.classification},
            technical_results=self.technical_results,
            portfolio_summary=self.summary,
            observations=self.observations,
            risk_flags=self.risk_flags,
            candidate_actions=[self.candidate_action],
            history_diff={"is_first_run": False},
            ledger_summary={"total_asset": 4200, "account_id": "ACCOUNT_SHOULD_NOT_LEAK"},
            config=self.config,
        )

    def test_build_agent_llm_context_contains_contract_without_secrets(self):
        context = self.context()

        self.assertEqual(context["schema_version"], 1)
        self.assertEqual(context["portfolio"]["position_count"], 1)
        self.assertEqual(context["holdings"][0]["code"], "510300")
        self.assertIn("classification", context["holdings"][0])
        self.assertIn("technical", context["holdings"][0])
        self.assertIn("holding:510300:technical", context["evidence_index"])
        self.assertIn("risk:concentration:position:510300", context["evidence_index"])
        self.assertIn("action:hold:trend_ok:510300", context["evidence_index"])

        serialized = json.dumps(context, ensure_ascii=False)
        self.assertNotIn("COOKIE_SHOULD_NOT_LEAK", serialized)
        self.assertNotIn("KEY_SHOULD_NOT_LEAK", serialized)
        self.assertNotIn("ROW_SECRET", serialized)
        self.assertNotIn("RAW_CONTENT_SHOULD_NOT_LEAK", serialized)
        self.assertNotIn("ACCOUNT_SHOULD_NOT_LEAK", serialized)

    def test_strip_json_markdown(self):
        self.assertEqual(strip_json_markdown("```json\n{\"a\": 1}\n```"), "{\"a\": 1}")
        self.assertEqual(strip_json_markdown("```\n{\"a\": 1}\n```"), "{\"a\": 1}")

    def test_parse_agent_report_valid_json_adds_legacy_fields(self):
        context = self.context()
        payload = {
            "summary": {"health_score": 82, "status": "review", "brief": "组合趋势尚可，但集中度高。"},
            "diagnosis": [
                {
                    "id": "diag:concentration",
                    "title": "单只集中度偏高",
                    "severity": "high",
                    "explanation": "组合全部集中在一只宽基 ETF。",
                    "evidence_refs": ["risk:concentration:position:510300"],
                }
            ],
            "action_reviews": [
                {
                    "candidate_action_id": "action:hold:trend_ok:510300",
                    "stance": "support",
                    "reason": "规则动作与趋势证据一致。",
                    "evidence_refs": ["action:hold:trend_ok:510300"],
                }
            ],
            "watch_conditions": [],
            "questions": [],
            "limitations": [],
        }

        report = parse_agent_report(
            json.dumps(payload, ensure_ascii=False),
            [self.candidate_action],
            context["evidence_index"],
            self.config,
        )

        self.assertEqual(report["summary"]["health_score"], 82)
        self.assertEqual(report["diagnosis"][0]["severity"], "high")
        self.assertEqual(report["action_reviews"][0]["candidate_action_id"], self.candidate_action.id)
        self.assertIn("单只集中度偏高", report["risk_tags"])
        self.assertIn("detailed_analysis", report)

    def test_parse_agent_report_adds_per_holding_advice_when_llm_omits_it(self):
        context = self.context()
        payload = {
            "summary": {"brief": "组合需要继续观察。"},
            "diagnosis": [],
            "action_reviews": [],
        }

        report = parse_agent_report(
            json.dumps(payload, ensure_ascii=False),
            [],
            context["evidence_index"],
            self.config,
            holdings=context["holdings"],
        )

        self.assertEqual(len(report["holding_analysis"]), 1)
        self.assertEqual(report["holding_analysis"][0]["target_code"], "510300")
        self.assertEqual(report["holding_analysis"][0]["action_type"], "hold")
        self.assertEqual(report["action_items"][0]["target"], "沪深300ETF")

    def test_parse_agent_report_keeps_llm_per_holding_advice(self):
        context = self.context()
        payload = {
            "summary": {"brief": "组合需要继续观察。"},
            "diagnosis": [],
            "holding_analysis": [
                {
                    "target_code": "510300",
                    "target_name": "沪深300ETF",
                    "action_type": "watch",
                    "title": "等待趋势确认",
                    "reason": "RSI 未过热，但单只仓位过高，需要观察。",
                    "evidence_refs": ["holding:510300:technical"],
                }
            ],
            "action_reviews": [],
        }

        report = parse_agent_report(
            json.dumps(payload, ensure_ascii=False),
            [],
            context["evidence_index"],
            self.config,
            holdings=context["holdings"],
        )

        self.assertEqual(report["holding_analysis"][0]["title"], "等待趋势确认")
        self.assertEqual(report["action_items"][0]["type"], "watch")
        self.assertEqual(report["action_items"][0]["target"], "沪深300ETF")

    def test_parse_agent_report_preserves_rule_reduce_when_llm_softens_to_watch(self):
        context = self.context()
        context["holdings"][0]["technical"]["rule_action"] = "减仓/暂停加仓"
        context["holdings"][0]["technical"]["rule_reason"] = "MA20 低于 MA60；距 120 日高点回撤 -15.00%"
        payload = {
            "summary": {"brief": "组合需要控制回撤。"},
            "diagnosis": [],
            "holding_analysis": [
                {
                    "target_code": "510300",
                    "target_name": "沪深300ETF",
                    "action_type": "watch",
                    "title": "LLM 软化成观察",
                    "reason": "虽然规则提示减仓，但模型写成观察。",
                    "evidence_refs": ["holding:510300:technical"],
                }
            ],
        }

        report = parse_agent_report(
            json.dumps(payload, ensure_ascii=False),
            [],
            context["evidence_index"],
            self.config,
            holdings=context["holdings"],
        )

        self.assertEqual(report["holding_analysis"][0]["action_type"], "reduce")
        self.assertEqual(report["action_items"][0]["type"], "reduce")

    def test_parse_agent_report_preserves_rule_buy_when_llm_softens_to_watch(self):
        context = self.context()
        context["holdings"][0]["technical"]["rule_action"] = "可分批加仓"
        context["holdings"][0]["technical"]["rule_reason"] = "价格站上 MA20 且 MA20 高于 MA60"
        payload = {
            "summary": {"brief": "趋势改善。"},
            "diagnosis": [],
            "holding_analysis": [
                {
                    "target_code": "510300",
                    "target_name": "沪深300ETF",
                    "action_type": "watch",
                    "title": "LLM 软化成观察",
                    "reason": "虽然规则提示分批加仓，但模型写成观察。",
                    "evidence_refs": ["holding:510300:technical"],
                }
            ],
        }

        report = parse_agent_report(
            json.dumps(payload, ensure_ascii=False),
            [],
            context["evidence_index"],
            self.config,
            holdings=context["holdings"],
        )

        self.assertEqual(report["holding_analysis"][0]["action_type"], "buy")
        self.assertEqual(report["action_items"][0]["type"], "buy")

    def test_parse_agent_report_removes_unknown_action_and_bad_evidence(self):
        context = self.context()
        payload = {
            "summary": {"brief": "需要确认策略。"},
            "diagnosis": [
                {
                    "title": "无法引用的诊断",
                    "severity": "bad",
                    "explanation": "这条证据不存在。",
                    "evidence_refs": ["missing:evidence"],
                }
            ],
            "action_reviews": [
                {
                    "candidate_action_id": "action:buy:unknown:999999",
                    "stance": "support",
                    "reason": "未知动作应被降级。",
                    "evidence_refs": ["missing:evidence"],
                }
            ],
        }

        report = parse_agent_report(
            json.dumps(payload, ensure_ascii=False),
            [self.candidate_action],
            context["evidence_index"],
            self.config,
        )

        self.assertEqual(report["diagnosis"][0]["severity"], "medium")
        self.assertEqual(report["diagnosis"][0]["evidence_refs"], [])
        self.assertEqual(report["action_reviews"], [])
        self.assertTrue(any("未知候选动作" in item["question"] for item in report["questions"]))

    @patch("stock_assistant.agent_llm.call_llm")
    def test_parse_agent_report_repairs_invalid_json(self, call_llm):
        context = self.context()
        call_llm.return_value = json.dumps({
            "summary": {"brief": "修复成功"},
            "diagnosis": [],
            "action_reviews": [],
        }, ensure_ascii=False)

        report = parse_agent_report(
            "{\"summary\":",
            [self.candidate_action],
            context["evidence_index"],
            self.config,
        )

        self.assertEqual(report["summary"]["brief"], "修复成功")
        call_llm.assert_called_once()

    @patch("stock_assistant.agent_llm.call_llm")
    def test_parse_agent_report_falls_back_when_repair_fails(self, call_llm):
        context = self.context()
        call_llm.return_value = "{not json"

        report = parse_agent_report(
            "{\"summary\":",
            [self.candidate_action],
            context["evidence_index"],
            self.config,
        )

        self.assertEqual(report["summary"]["status"], "fallback")
        self.assertEqual(report["action_items"][0]["id"], self.candidate_action.id)

    def test_fallback_agent_report_uses_rule_actions(self):
        context = self.context()
        report = fallback_agent_report([self.candidate_action], self.observations, "LLM 关闭", context["holdings"])

        self.assertEqual(report["summary"]["status"], "fallback")
        self.assertEqual(report["action_items"][0]["id"], self.candidate_action.id)
        self.assertTrue(any(item["target"] == "沪深300ETF" for item in report["action_items"]))
        self.assertTrue(report["watch_conditions"])

    def test_llm_structured_kwargs_respects_provider_capability(self):
        config = deepcopy(self.config)
        config["llm"]["structured_output"] = "auto"
        config["llm"]["supports_response_format"] = False
        self.assertEqual(llm_structured_kwargs(config), {})

        config["llm"]["supports_response_format"] = True
        self.assertEqual(llm_structured_kwargs(config), {"response_format": {"type": "json_object"}})

        config["llm"]["structured_output"] = "json_object"
        config["llm"]["supports_response_format"] = False
        self.assertEqual(llm_structured_kwargs(config), {"response_format": {"type": "json_object"}})


if __name__ == "__main__":
    unittest.main()
