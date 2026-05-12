import unittest
from stock_assistant.memory import diff_agent_snapshots

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

if __name__ == '__main__':
    unittest.main()
