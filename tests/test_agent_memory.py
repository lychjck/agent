import unittest
from stock_assistant.memory import (
    agent_snapshot_fingerprint,
    agent_snapshots_have_same_facts,
    diff_agent_snapshots,
)

class TestAgentMemory(unittest.TestCase):
    def test_diff_agent_snapshots_first_run(self):
        current = {
            "portfolio": {
                "total_value": 1000,
                "positions": [{"code": "512880", "name": "证券ETF", "asset_class": "sector_equity"}]
            },
            "risk_flags": [],
            "candidate_actions": []
        }
        diff = diff_agent_snapshots(None, current)
        self.assertTrue(diff["is_first_run"])
        
    def test_diff_agent_snapshots_changes(self):
        previous = {
            "portfolio": {
                "total_value": 1000,
                "positions": [{"code": "512880", "name": "证券ETF", "asset_class": "sector_equity"}]
            },
            "risk_flags": [
                {"id": "risk1", "severity": "medium"}
            ],
            "candidate_actions": []
        }
        
        current = {
            "portfolio": {
                "total_value": 1200,
                "positions": [
                    {"code": "512880", "name": "证券ETF", "asset_class": "sector_equity"},
                    {"code": "510300", "name": "300ETF", "asset_class": "unknown"}
                ]
            },
            "risk_flags": [
                {"id": "risk1", "severity": "high"},
                {"id": "risk2", "severity": "low"}
            ],
            "candidate_actions": []
        }
        
        diff = diff_agent_snapshots(previous, current)
        self.assertFalse(diff["is_first_run"])
        self.assertEqual(diff["portfolio_changes"]["total_value_delta"], 200)
        self.assertIn("510300", diff["portfolio_changes"]["new_positions"])
        self.assertEqual(len(diff["risk_changes"]["new"]), 1)
        self.assertEqual(diff["risk_changes"]["new"][0]["id"], "risk2")
        self.assertEqual(len(diff["risk_changes"]["worsened"]), 1)
        self.assertEqual(diff["risk_changes"]["worsened"][0]["id"], "risk1")

    def test_snapshot_fingerprint_ignores_report_and_generated_at(self):
        base = {
            "generated_at": "2026-05-12T15:10:00",
            "portfolio": {"total_value": 1000, "positions": [{"code": "510300", "weight": 100.0}]},
            "risk_flags": [],
            "candidate_actions": [],
            "agent_report": {"summary": {"brief": "第一次"}},
        }
        later = {
            **base,
            "generated_at": "2026-05-12T16:20:00",
            "agent_report": {"summary": {"brief": "第二次"}},
        }

        self.assertEqual(agent_snapshot_fingerprint(base), agent_snapshot_fingerprint(later))
        self.assertTrue(agent_snapshots_have_same_facts(base, later))

    def test_diff_agent_snapshots_marks_duplicate_without_continued_noise(self):
        previous = {
            "portfolio": {
                "total_value": 1000,
                "positions": [{"code": "510300", "name": "300ETF", "asset_class": "broad_index"}],
            },
            "risk_flags": [{"id": "risk1", "severity": "medium"}],
            "candidate_actions": [{"id": "action1"}],
            "agent_report": {"summary": {"brief": "旧报告"}},
        }
        current = {
            "portfolio": {
                "total_value": 1000,
                "positions": [{"code": "510300", "name": "300ETF", "asset_class": "broad_index"}],
            },
            "risk_flags": [{"id": "risk1", "severity": "medium"}],
            "candidate_actions": [{"id": "action1"}],
        }

        diff = diff_agent_snapshots(previous, current)

        self.assertTrue(diff["duplicate_of_latest"])
        self.assertEqual(diff["portfolio_changes"], {"unchanged": True})
        self.assertEqual(diff["risk_changes"]["continued"], [])
        self.assertEqual(diff["action_changes"]["continued"], [])

if __name__ == '__main__':
    unittest.main()
