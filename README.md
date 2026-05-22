# ETF 持仓日报工具

每天下午五点拉一次投资账本持仓和 ETF 日 K，生成一份 Markdown 持仓报告。

报告里的"可分批加仓 / 持有观察 / 减仓或暂停加仓"是基于均线、RSI、回撤、仓位占比和持仓收益的规则化提示，不是投资建议或收益承诺。

> 想理清整个项目结构、哪些文件可以跳过，先看 [`docs/architecture.md`](docs/architecture.md)。

## 快速开始

接入真实投资账本：

```bash
cd /Users/liyanran/github/stock
cp config.example.toml config.toml
uv run python -m stock_assistant.cli --config config.toml run
```

把投资账本网页的请求 `Copy as cURL` 保存到 `data/.tzzb-curl`，脚本会读 cURL 里的 Cookie，
依次请求 `account_list`、`stock_position`、`fund_position`，把返回归档为
`data/holdings/YYYYMMDD-HHMMSS-tzzb-api.json`，再进入 K 线分析和报告流程。

```toml
[ledger]
mode = "tzzb_api"
curl_file = "data/.tzzb-curl"
```

> 当前只支持 `tzzb_api` 这一种 ledger 模式。早期文档里提到的 `manual` / `playwright`
> 模式在代码里**没有实现**，配置项写了也不会生效。

如果启用 AI 解读，把 key 放到本地 `.env`，不要写进 `config.toml`：

```bash
cp .env.example .env
# 然后把 .env 里的 MODELSCOPE_API_KEY / EASYROUTER_API_KEY 改成你的 key
```

`config.toml` 默认优先使用 ModelScope：

```toml
[llm]
enabled = true
client = "openai"
base_url = "https://api-inference.modelscope.cn/v1"
model = "deepseek-ai/DeepSeek-V4-Pro"
api_key_env = "MODELSCOPE_API_KEY"
max_tokens = 65536
stream = true
```

要切回 EasyRouter：

```toml
[llm]
client = "openai"
base_url = "https://easyrouter.io/v1"
model = "deepseek-v4-pro"
api_key_env = "EASYROUTER_API_KEY"
stream = false
```

## MCP Server

本项目可以把内部只读 Agent 工具暴露为 MCP server，方便 Qclaw/OpenClaw 等 MCP client 调用外部模型分析。

本地调试用 stdio：

```bash
cd /Users/liyanran/github/stock
uv run python -m stock_assistant.mcp_server --config config.toml
```

如果项目已安装为命令行入口，也可以直接：

```bash
stock-assistant-mcp --config /Users/liyanran/github/stock/config.toml
```

远程接入 Qclaw/OpenClaw 时部署 HTTP 模式，并通过反向代理提供 HTTPS：

```bash
cd /Users/liyanran/github/stock
export STOCK_MCP_TOKEN="换成一段长随机 token"
uv run python -m stock_assistant.mcp_server \
  --transport http \
  --host 127.0.0.1 \
  --port 8766 \
  --config /Users/liyanran/github/stock/config.toml
```

反向代理把公网 HTTPS 的 `/mcp` 转发到 `http://127.0.0.1:8766/mcp`。Qclaw/OpenClaw 配置示例：

```json
{
  "mcpServers": {
    "stock-assistant": {
      "url": "https://你的域名/mcp",
      "transport": "streamable-http",
      "headers": {
        "Authorization": "Bearer 换成同一个 token"
      }
    }
  }
}
```

如果 MCP client 只支持本地命令模式，继续用 stdio 配置：

```json
{
  "mcpServers": {
    "stock-assistant": {
      "command": "uv",
      "args": [
        "run",
        "python",
        "-m",
        "stock_assistant.mcp_server",
        "--config",
        "/Users/liyanran/github/stock/config.toml"
      ]
    }
  }
}
```

第一批工具都带 `stock_` 前缀，且只暴露只读能力，例如 `stock_get_current_holdings`、`stock_get_portfolio_profile`、`stock_get_holding_technical`。工具调用复用后端现有参数校验、只读限制、敏感字段过滤和结果截断。

## Agent Skills

项目支持安装本地 Agent skill。skill 是一个包含 `SKILL.md` 的目录，默认安装到：

```text
/Users/liyanran/github/stock/data/skills/
```

从互联网上安装 raw `SKILL.md` 或 GitHub blob URL：

```bash
uv run python -m stock_assistant.cli --config config.toml skills install https://example.com/SKILL.md
uv run python -m stock_assistant.cli --config config.toml skills list
uv run python -m stock_assistant.cli --config config.toml skills show skill-name
```

启用后，tool-agent 会额外获得 `list_skills` 和 `read_skill` 两个只读工具。Agent 可以先发现已安装 skill，再读取对应 `SKILL.md`，按你的自定义流程完成分析；它不会通过 skill 工具直接联网、写文件或执行命令。

## 输出位置

```text
reports/YYYY-MM-DD-etf-report.md          # Markdown 报告
reports/ai-report-YYYYMMDD-HHMMSS.json    # AI 诊断 JSON
data/holdings/YYYYMMDD-HHMMSS-tzzb-api.json   # 持仓快照归档
data/research/<code>.json                  # 分类 + 搜索证据缓存
data/state/snapshots/                       # Agent 状态快照
data/state/agent_traces/                    # Agent 执行 trace
```

## 每天下午五点运行

macOS 用 `launchd`。先确认 `launchd/com.local.etf-position-assistant.plist` 里的路径，然后安装：

```bash
mkdir -p ~/Library/LaunchAgents
cp /Users/liyanran/github/stock/launchd/com.local.etf-position-assistant.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.local.etf-position-assistant.plist
```

日志默认写到：

```text
/Users/liyanran/github/stock/logs/stdout.log
/Users/liyanran/github/stock/logs/stderr.log
```

## 持仓表头

默认识别这些列：

- 代码：`证券代码, 基金代码, 代码, 产品代码, symbol, code`
- 名称：`证券名称, 基金名称, 名称, 产品名称, name`
- 数量：`持仓数量, 可用份额, 持有份额, 数量, 份额`
- 成本价：`成本价, 持仓成本价, 买入均价, 成本`
- 市值：`持仓市值, 市值, 最新市值`
- 收益率：`收益率, 持仓收益率, 盈亏比例`

如果你的投资账本字段名不同，直接改 `config.toml` 的 `[columns]`。
