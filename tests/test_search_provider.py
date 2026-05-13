import unittest
import os
from unittest.mock import patch, MagicMock

from stock_assistant import (
    build_search_provider,
    TavilySearchProvider,
    BraveSearchProvider,
    score_classification_evidence
)
from stock_assistant.integrations.search import search_freshness_for_provider, search_result_evidence
from stock_assistant.integrations.search import extract_json_object, classification_llm_config

class TestSearchProvider(unittest.TestCase):
    def test_build_tavily_provider(self):
        config = {
            "search": {
                "enabled": True,
                "provider": "tavily",
                "freshness": "year",
                "include_raw_content": "text",
                "providers": {
                    "tavily": {
                        "api_key_env": "TEST_TAVILY_API_KEY",
                        "search_depth": "advanced",
                        "topic": "finance"
                    }
                }
            }
        }
        with patch.dict(os.environ, {"TEST_TAVILY_API_KEY": "test_key"}):
            provider = build_search_provider(config)
            self.assertIsInstance(provider, TavilySearchProvider)
            self.assertEqual(provider.api_key, "test_key")
            self.assertEqual(provider.search_depth, "advanced")
            self.assertEqual(provider.topic, "finance")
            self.assertEqual(provider.freshness, "year")
            self.assertEqual(provider.include_raw_content, "text")

    def test_build_tavily_provider_missing_key(self):
        config = {
            "search": {
                "enabled": True,
                "provider": "tavily",
                "providers": {
                    "tavily": {
                        "api_key_env": "MISSING_KEY"
                    }
                }
            }
        }
        with patch.dict(os.environ, clear=True):
            with self.assertRaises(RuntimeError):
                build_search_provider(config)

    def test_build_brave_provider(self):
        config = {
            "search": {
                "enabled": True,
                "provider": "brave",
                "freshness": "week",
                "providers": {
                    "brave": {
                        "api_key_env": "TEST_BRAVE_API_KEY"
                    }
                }
            }
        }
        with patch.dict(os.environ, {"TEST_BRAVE_API_KEY": "brave_key"}):
            provider = build_search_provider(config)
            self.assertIsInstance(provider, BraveSearchProvider)
            self.assertEqual(provider.api_key, "brave_key")
            self.assertEqual(provider.freshness, "pw")

    def test_search_freshness_mapping(self):
        self.assertEqual(search_freshness_for_provider("tavily", {"search": {"freshness": "py"}}), "year")
        self.assertEqual(search_freshness_for_provider("brave", {"search": {"freshness": "month"}}), "pm")
        self.assertEqual(search_freshness_for_provider("tavily", {"search": {"freshness": "none"}}), "")

    def test_search_result_evidence_keeps_content(self):
        result = {
            "title": "证券ETF 产品页",
            "url": "https://www.sse.com.cn/test",
            "snippet": "跟踪证券公司指数",
            "content": "这只 ETF 跟踪中证全指证券公司指数。",
            "raw_content": "完整正文",
            "published_date": "2026-05-01",
            "retrieved_at": "2026-05-12T00:00:00+00:00",
            "source": "tavily",
        }
        config = {
            "search": {
                "max_stored_content_chars": 8,
                "source_tiers": {"tier1": "sse.com.cn", "tier2": ""}
            }
        }

        evidence = search_result_evidence(result, config)

        self.assertEqual(evidence["source_tier"], "1")
        self.assertEqual(evidence["published_date"], "2026-05-01")
        self.assertIn("跟踪证券", evidence["snippet"])
        self.assertTrue(evidence["content"].endswith("[truncated]"))

    def test_extract_json_object_from_markdown(self):
        payload = extract_json_object("""```json
{"asset_class": "active_equity", "confidence": 0.8}
```""")
        self.assertEqual(payload["asset_class"], "active_equity")

    def test_classification_llm_config_defaults_to_local_no_think_model(self):
        llm_config = classification_llm_config({"classification": {"llm": {"enabled": True}}})
        llm = llm_config["llm"]
        self.assertEqual(llm["client"], "urllib")
        self.assertEqual(llm["base_url"], "http://10.33.207.193:1234/v1")
        self.assertEqual(llm["model"], "google/gemma-4-31b")
        self.assertTrue(llm["disable_thinking"])
        self.assertFalse(llm["stream"])

    def test_classification_llm_config_can_carry_log_context(self):
        llm_config = classification_llm_config({
            "classification": {
                "llm": {
                    "enabled": True,
                    "log_context": "classification code=000001"
                }
            }
        })
        self.assertEqual(llm_config["llm"]["log_context"], "classification code=000001")

    def test_score_classification_evidence(self):
        config = {
            "search": {
                "source_tiers": {
                    "tier1": "sse.com.cn, szse.cn",
                    "tier2": "eastmoney.com"
                }
            }
        }
        
        # Test Tier 1 source
        evidence_tier1 = [
            {"url": "https://www.sse.com.cn/test"},
            {"url": "https://example.com/test"}
        ]
        # Base score starts at 0.
        # Tier 1 (+0.5)
        # 2 sources (+0.2)
        # Matches name rule (+0.1)
        self.assertAlmostEqual(score_classification_evidence(evidence_tier1, config), 0.8)
        
        # Test Only Tier 3 sources
        evidence_tier3 = [
            {"url": "https://example.com/test"},
        ]
        # Base: 0
        # Matches name rule (+0.1)
        # Only tier 3 (-0.3) -> 0.1 - 0.3 = -0.2 (min 0)
        self.assertEqual(score_classification_evidence(evidence_tier3, config), 0.0)

        # Empty evidence
        self.assertEqual(score_classification_evidence([], config), 0.0)

if __name__ == '__main__':
    unittest.main()
