# Stock MCP Server (Standalone)

完全剥离、无状态、确定性的数据供给与物理执行层 MCP 服务。

## 启动方式

### 1. 安装依赖

本项目采用 `uv` 管理虚拟环境与依赖：

```bash
cd mcp
uv sync
```

### 2. 本地开发 / Stdio 模式 (供本地 Agent 直接接入)

```bash
# 自动寻找当前目录下的 config.toml 并以 stdio 管道运行
uv run python -m stock_mcp --config config.example.toml --transport stdio
```

### 3. HTTP 模式运行 (供远程 Agent 接入)

```bash
export STOCK_MCP_TOKEN="your-secure-token"

uv run python -m stock_mcp --config config.example.toml \
  --transport http --host 127.0.0.1 --port 8766
```

## 功能清单

* **持仓画像分类** (`get_current_holdings`, `get_portfolio_profile`, `get_classification`)
* **一键大礼包** (`get_current_account_bundle`) - **单次物理请求聚合器**，合并持仓获取并在内存计算分类画像，防同花顺高频风控。
* **个股技术与统计分析** (`get_holding_technical`) - MA20/60/120、RSI14、120日最大回撤及 z-score，**并发局域缓存**。
* **ETF持股穿透** (`get_etf_constituents`) - 东财天天基金主轨，**带指数退避重试与1小时本地物理缓存**。
* **资产诊断历史快照** (`load_snapshot_summary`, `save_snapshot`, `compare_snapshots`) - 无状态对比，支持**单日覆盖去重**与**180天滚动过期清理**。
* **网络与工具** (`web_search`, `web_read`, `web_fetch`, `opencli_command`) - 网页搜索、Markdown 提取与网页爬取。
* **流水/盈亏占位符** - 针对需签名 `s` 的功能暴露友好占位，返回 `capability_unavailable` 并阻止 LLM 重试。
