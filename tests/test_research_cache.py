import unittest
import tempfile
import json
import datetime as dt
from pathlib import Path
from unittest.mock import patch

from stock_assistant import (
    Holding,
    InstrumentClassification,
    classification_from_config,
    load_cached_classification,
    save_classification_cache,
    classification_cache_is_fresh,
    classification_cache_status,
    build_search_provider,
    suggest_classification_with_search,
    classify_holding
)

class TestResearchCache(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.cache_dir = Path(self.temp_dir.name)
        self.config = {
            "search": {
                "enabled": True,
                "provider": "manual_json",
                "cache_dir": str(self.cache_dir),
                "manual_results_file": str(self.cache_dir / "manual.json")
            },
            "classification": {
                "cache_ttl_days": 90,
                "require_user_review_below_confidence": 0.75,
                "llm": {
                    "enabled": False
                }
            },
            "classifications": {
                "510300": {
                    "asset_class": "broad_index",
                    "sector": "",
                    "theme": "csi300",
                    "region": "china_a",
                    "strategy": "passive_index",
                    "reviewed_by_user": True,
                }
            }
        }

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_classification_from_config(self):
        h = Holding(code="510300", name="300ETF")
        cls = classification_from_config(h, self.config)
        self.assertIsNotNone(cls)
        self.assertEqual(cls.asset_class, "broad_index")
        self.assertTrue(cls.reviewed_by_user)
        self.assertEqual(cls.confidence, 1.0)
        
        h2 = Holding(code="159915", name="创业板ETF")
        self.assertIsNone(classification_from_config(h2, self.config))

    def test_classification_cache_is_fresh(self):
        now = dt.datetime.now(dt.timezone.utc)
        fresh_record = {"retrieved_at": (now - dt.timedelta(days=10)).isoformat()}
        stale_record = {"retrieved_at": (now - dt.timedelta(days=100)).isoformat()}
        
        self.assertTrue(classification_cache_is_fresh(fresh_record, 90))
        self.assertFalse(classification_cache_is_fresh(stale_record, 90))
        self.assertFalse(classification_cache_is_fresh({}, 90))

    def test_save_and_load_cache(self):
        h = Holding(code="512880", name="证券ETF")
        cls = InstrumentClassification(
            code="512880", name="证券ETF", asset_class="sector_equity", sector="financials"
        )
        save_classification_cache(cls, self.config)
        
        loaded = load_cached_classification(h, self.config)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.asset_class, "sector_equity")
        self.assertEqual(loaded.sector, "financials")
        
        # Test stale cache
        cache_path = self.cache_dir / "512880.json"
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        data["retrieved_at"] = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=100)).isoformat()
        cache_path.write_text(json.dumps(data))
        
        self.assertIsNone(load_cached_classification(h, self.config))

        # Test reviewed user cache loads even if stale
        data["reviewed_by_user"] = True
        cache_path.write_text(json.dumps(data))
        loaded_reviewed = load_cached_classification(h, self.config)
        self.assertIsNotNone(loaded_reviewed)

    def test_search_cache_without_content_is_ignored(self):
        h = Holding(code="512880", name="证券ETF")
        cls = InstrumentClassification(
            code="512880",
            name="证券ETF",
            asset_class="sector_equity",
            sector="financials",
            confidence=0.9,
            source="search_rule",
            evidence=({"title": "证券ETF 产品页", "url": "https://www.sse.com.cn/test"},),
        )
        save_classification_cache(cls, self.config)

        self.assertIsNone(load_cached_classification(h, self.config))

        cache_path = self.cache_dir / "512880.json"
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        data["evidence"][0]["snippet"] = "跟踪证券公司指数"
        cache_path.write_text(json.dumps(data), encoding="utf-8")

        loaded = load_cached_classification(h, self.config)
        self.assertIsNotNone(loaded)

    def test_classification_cache_status(self):
        h = Holding(code="512880", name="证券ETF")
        self.assertEqual(classification_cache_status(h, self.config)[0], "miss")

        cls = InstrumentClassification(
            code="512880",
            name="证券ETF",
            asset_class="sector_equity",
            sector="financials",
            confidence=0.5,
            source="search_llm",
            evidence=({"snippet": "跟踪证券公司指数"},),
        )
        save_classification_cache(cls, self.config)
        status, detail = classification_cache_status(h, self.config)
        self.assertEqual(status, "low_confidence")
        self.assertIn("0.5000", detail)

        cache_path = self.cache_dir / "512880.json"
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        data["confidence"] = 0.9
        data["evidence"] = [{"title": "证券ETF 产品页", "url": "https://www.sse.com.cn/test"}]
        cache_path.write_text(json.dumps(data), encoding="utf-8")
        status, _ = classification_cache_status(h, self.config)
        self.assertEqual(status, "missing_evidence_content")

        data["retrieved_at"] = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=100)).isoformat()
        data["evidence"][0]["snippet"] = "跟踪证券公司指数"
        cache_path.write_text(json.dumps(data), encoding="utf-8")
        status, _ = classification_cache_status(h, self.config)
        self.assertEqual(status, "stale")

    def test_low_confidence_unreviewed_cache_status_is_low_confidence(self):
        """低置信度未审核缓存：cache_status 报告 low_confidence，但 load 仍返回缓存供 UI 展示。"""
        h = Holding(code="512880", name="证券ETF")
        cls = InstrumentClassification(
            code="512880",
            name="证券ETF",
            asset_class="sector_equity",
            sector="financials",
            confidence=0.5,
            source="search_llm",
            evidence=({"title": "证券ETF 产品页", "url": "https://www.sse.com.cn/test", "snippet": "跟踪证券公司指数"},),
        )
        save_classification_cache(cls, self.config)

        cfg = dict(self.config)
        cfg["classification"] = dict(cfg["classification"])
        cfg["classification"]["require_user_review_below_confidence"] = 0.75

        # cache_status 应报告 low_confidence
        status, detail = classification_cache_status(h, cfg)
        self.assertEqual(status, "low_confidence")
        self.assertIn("0.5000", detail)

        # load_cached_classification 仍返回缓存（上层逻辑决定如何处理）
        loaded = load_cached_classification(h, cfg)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.confidence, 0.5)

    def test_disabled_search(self):
        h = Holding(code="512880", name="证券ETF")
        cfg = {"search": {"enabled": False}}
        provider = build_search_provider(cfg)
        self.assertEqual(provider.search("query", 5), [])

    @patch("stock_assistant.search.eastmoney_fetch_and_classify", return_value=None)
    def test_manual_json_search(self, mock_em):
        """天天基金 API 不可用时，fallback 到 manual_json 搜索。"""
        manual_path = self.cache_dir / "manual.json"
        manual_path.write_text(json.dumps({
            "512880 证券ETF ETF 跟踪指数 行业 基金公司": [
                {
                    "title": "证券ETF 产品页",
                    "url": "https://www.sse.com.cn/test",
                    "snippet": "跟踪证券公司指数",
                    "content": "产品资料显示，该 ETF 跟踪证券公司指数。",
                    "published_date": "2026-05-01"
                }
            ]
        }), encoding="utf-8")
        
        h = Holding(code="512880", name="证券ETF")
        cls = suggest_classification_with_search(h, self.config)
        self.assertIsNotNone(cls)
        self.assertEqual(cls.sector, "financials")
        self.assertEqual(cls.evidence[0]["snippet"], "跟踪证券公司指数")
        self.assertIn("证券公司指数", cls.evidence[0]["content"])
        self.assertEqual(cls.evidence[0]["published_date"], "2026-05-01")

        cache_data = json.loads((self.cache_dir / "512880.json").read_text(encoding="utf-8"))
        self.assertEqual(cache_data["evidence"][0]["snippet"], "跟踪证券公司指数")
        self.assertIn("证券公司指数", cache_data["evidence"][0]["content"])
        self.assertEqual(cache_data["source"], "search_rule_fallback")

    @patch("stock_assistant.search.eastmoney_fetch_and_classify", return_value=None)
    def test_manual_json_search_with_llm_classifier(self, mock_em):
        """天天基金 API 不可用时，fallback 到搜索引擎 + LLM 分类。"""
        cfg = dict(self.config)
        cfg["classification"] = dict(cfg["classification"])
        cfg["classification"]["llm"] = {
            "enabled": True,
            "client": "urllib",
            "base_url": "http://10.33.207.193:1234/v1",
            "model": "google/gemma-4-31b",
            "disable_thinking": True,
        }
        manual_path = self.cache_dir / "manual.json"
        manual_path.write_text(json.dumps({
            "000259 农银区间收益混合 ETF 跟踪指数 行业 基金公司": [
                {
                    "title": "农银区间收益混合产品资料",
                    "url": "https://fund.eastmoney.com/000259.html",
                    "snippet": "农银区间收益混合是一只混合型基金。",
                    "content": "该基金为灵活配置混合型证券投资基金。",
                    "published_date": "2026-05-01"
                }
            ]
        }), encoding="utf-8")
        h = Holding(code="000259", name="农银区间收益混合")

        with patch("stock_assistant.search.call_llm") as call_llm:
            call_llm.return_value = json.dumps({
                "asset_class": "mixed_allocation",
                "sector": "multi_sector",
                "theme": "",
                "region": "china_a",
                "strategy": "mixed_allocation",
                "tracked_index": "",
                "issuer": "农银汇理基金",
                "confidence": 0.86,
                "evidence_urls": ["https://fund.eastmoney.com/000259.html"]
            })
            cls = suggest_classification_with_search(h, cfg)

        self.assertIsNotNone(cls)
        self.assertEqual(cls.asset_class, "mixed_allocation")
        self.assertEqual(cls.strategy, "mixed_allocation")
        self.assertEqual(cls.source, "search_llm")
        self.assertTrue(call_llm.called)

        cache_data = json.loads((self.cache_dir / "000259.json").read_text(encoding="utf-8"))
        self.assertEqual(cache_data["source"], "search_llm")
        self.assertIn("混合型基金", cache_data["evidence"][0]["snippet"])

    def test_classify_holding_priority(self):
        # Config priority
        h1 = Holding(code="510300", name="300ETF")
        cls1 = classify_holding(h1, self.config)
        self.assertEqual(cls1.source, "config")

        # Fallback priority (mock 掉天天基金 API)
        h2 = Holding(code="512880", name="证券ETF")
        fallback_cfg = dict(self.config)
        fallback_cfg["search"] = dict(fallback_cfg["search"])
        fallback_cfg["search"]["enabled"] = False
        with patch("stock_assistant.search.eastmoney_fetch_and_classify", return_value=None):
            cls2 = classify_holding(h2, fallback_cfg)
        self.assertEqual(cls2.source, "local_heuristic")

        # Unknown (mock 掉天天基金 API)
        h3 = Holding(code="000001", name="Unknown")
        with patch("stock_assistant.search.eastmoney_fetch_and_classify", return_value=None):
            cls3 = classify_holding(h3, fallback_cfg)
        self.assertEqual(cls3.source, "unknown")

    def test_eastmoney_direct_api_classification(self):
        """测试天天基金直接 API 路径 (mock HTTP 调用)。"""
        h = Holding(code="011068", name="华宝资源优选混合C")
        mock_rule_result = {
            "asset_class": "mixed_allocation",
            "sector": "materials",
            "strategy": "mixed_allocation",
            "theme": "resources",
            "region": "china_a",
            "issuer": "华宝基金",
            "confidence": 0.9,
            "classification_reasons": ["test"],
            "structured_evidence": {
                "fund_code": "011068",
                "fund_name": "华宝资源优选混合C",
                "top_holdings": [{"code": "601899", "name": "紫金矿业"}],
            },
        }
        with patch("stock_assistant.search.eastmoney_fetch_and_classify", return_value=mock_rule_result):
            cls = suggest_classification_with_search(h, self.config)

        self.assertIsNotNone(cls)
        self.assertEqual(cls.asset_class, "mixed_allocation")
        self.assertEqual(cls.sector, "materials")
        self.assertEqual(cls.source, "eastmoney_rule")
        self.assertAlmostEqual(cls.confidence, 0.9)

        cache_data = json.loads((self.cache_dir / "011068.json").read_text(encoding="utf-8"))
        self.assertEqual(cache_data["source"], "eastmoney_rule")
        self.assertEqual(cache_data["asset_class"], "mixed_allocation")

if __name__ == '__main__':
    unittest.main()
