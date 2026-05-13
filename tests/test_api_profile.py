import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import api.main as api


class TestApiProfile(unittest.TestCase):
    def test_profile_endpoint_uses_cached_classification_by_default(self):
        expected = {
            "source": "snapshot.json",
            "ledger_summary": {},
            "summary": {"total_value": 1000, "position_count": 1},
            "observations": [],
            "classifications": {},
        }
        with patch("api.main.build_portfolio_profile", return_value=expected) as build_profile:
            response = TestClient(api.app).get("/api/profile")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), expected)
        build_profile.assert_called_once_with(
            api.config,
            refresh_classification=False,
        )

    def test_profile_endpoint_can_refresh_classification(self):
        with patch("api.main.build_portfolio_profile", return_value={"summary": {}}) as build_profile:
            response = TestClient(api.app).get("/api/profile?refresh_classification=true")

        self.assertEqual(response.status_code, 200)
        build_profile.assert_called_once_with(
            api.config,
            refresh_classification=True,
        )


if __name__ == "__main__":
    unittest.main()
