import logging
from pathlib import Path
from typing import Any
import toml

from stock_mcp.core.logging import logger

def load_config(config_path: Path | None = None) -> dict[str, Any]:
    """强行加载并校验 TOML 配置文件。文件不存在或参数缺失则直接抛错。"""
    if not config_path:
        config_path = Path("config.toml")
        
    if not config_path.exists():
        raise FileNotFoundError(
            f"未找到配置文件 '{config_path}'！请复制 config.example.toml 并重命名为 config.toml 进行配置。"
        )
        
    try:
        config = toml.load(config_path)
        logger.info(f"Loaded config from {config_path}")
    except Exception as e:
        raise ValueError(f"解析 TOML 配置文件 '{config_path}' 失败: {e}")
        
    # --- 强行参数与类型校验 ---
    required_specs = [
        # (section, key, expected_type)
        ("mcp", "expose_legacy_tzzb_placeholders", bool),
        ("mcp", "log_level", str),
        ("server", "default_transport", str),
        ("server", "http_host", str),
        ("server", "http_port", int),
        ("paths", "snapshots_dir", str),
        ("paths", "classification_cache_dir", str),
        ("search", "enabled", bool),
    ]
    
    missing_errors = []
    for section, key, expected_type in required_specs:
        if section not in config or not isinstance(config[section], dict):
            missing_errors.append(f"缺失配置小节: [{section}]")
            continue
            
        val = config[section].get(key)
        if val is None:
            missing_errors.append(f"缺失必要配置项: [{section}] {key}")
        elif not isinstance(val, expected_type) and not (expected_type is float and isinstance(val, int)):
            # 允许 int 作为 float 的兼容，其他类型严格匹配
            missing_errors.append(
                f"配置类型错误: [{section}] {key} (期望: {expected_type.__name__}, 实际: {type(val).__name__})"
            )
            
    if missing_errors:
        raise ValueError("配置文件检测失败，存在以下缺失或错误参数：\n" + "\n".join(f" - {e}" for e in missing_errors))
        
    # 动态设定日志等级
    mcp_level = config.get("mcp", {}).get("log_level", "INFO")
    logger.setLevel(getattr(logging, mcp_level.upper(), logging.INFO))
    
    return config

