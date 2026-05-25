import pytest

@pytest.fixture
def base_config():
    return {
        "mcp": {
            "expose_legacy_tzzb_placeholders": True,
            "log_level": "INFO",
        },
        "server": {
            "default_transport": "stdio",
            "http_host": "127.0.0.1",
            "http_port": 8766,
            "http_path": "/mcp",
            "auth_token_env": "STOCK_MCP_TOKEN",
        },
        "ledger": {
            "tzzb": {
                "mode": "tzzb_api",
                "curl_file": ".tzzb-curl",
                "cookie_file": "",
                "api_timeout_seconds": 30,
            }
        },
        "market": {
            "provider": "sina",
            "history_days": 260,
            "timeout_seconds": 15,
        },
        "paths": {
            "snapshots_dir": "./data/snapshots",
            "classification_cache_dir": "./data/research",
        },
        "search": {
            "enabled": True,
            "provider": "opencli",
            "timeout_seconds": 20,
            "max_results": 5,
            "auto_read_top_result": True,
        }
    }

