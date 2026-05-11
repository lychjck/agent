# ETF 持仓日报工具

这个目录是一个本地自动化工具：每天下午五点打开投资账本，拿到持仓导出文件，拉取 ETF 日 K，生成一份 Markdown 持仓报告。

报告里的“可分批加仓 / 持有观察 / 减仓或暂停加仓”是基于均线、RSI、回撤、仓位占比和持仓收益的规则化提示，不是投资建议或收益承诺。

## 快速开始

先跑样例，确认程序会输出报告：

```bash
cd /Users/liyanran/github/stock
python3 stock_assistant.py analyze tests/fixtures/holdings.csv
```

如果你用 `uv`，建议显式传子命令：

```bash
uv run python stock_assistant.py analyze tests/fixtures/holdings.csv
```

接入真实投资账本：

```bash
cd /Users/liyanran/github/stock
cp config.example.toml config.toml
python3 stock_assistant.py --config config.toml run
```

如果已经从 Chrome Network 里把投资账本请求 `Copy as cURL` 保存为 `.tzzb-curl`，可以直接走投资账本 API，不再下载 CSV/XLSX：

```toml
[ledger]
mode = "tzzb_api"
curl_file = ".tzzb-curl"
```

运行：

```bash
python3 stock_assistant.py --config config.toml run
```

脚本会依次请求 `account_list`、`stock_position`、`fund_position`，把返回数据归档为 `data/holdings/YYYYMMDD-HHMMSS-tzzb-api.json`，再进入原有 K 线分析和报告流程。

如果启用 AI 解读，把 key 放到本地 `.env`，不要写进 `config.toml`：

```bash
cp .env.example .env
# 然后把 .env 里的 MODELSCOPE_API_KEY / EASYROUTER_API_KEY 改成你的 key
```

当前 `config.toml` 默认优先使用 ModelScope：

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

如果要切回 EasyRouter，保留同一套 OpenAI-compatible 调用方式，改回：

```toml
[llm]
client = "openai"
base_url = "https://easyrouter.io/v1"
model = "deepseek-v4-pro"
api_key_env = "EASYROUTER_API_KEY"
stream = false
```

首次运行建议保留 `ledger.mode = "manual"`：程序会打开浏览器，你登录投资账本并导出持仓文件，脚本会在 `~/Downloads` 和 `./downloads` 里等待新的 `csv/xlsx` 文件。

如果已经有持仓文件，可以直接分析：

```bash
python3 stock_assistant.py analyze /path/to/holdings.csv
```

输出报告在：

```text
/Users/liyanran/github/stock/reports/YYYY-MM-DD-etf-report.md
```

持仓原文件会归档到：

```text
/Users/liyanran/github/stock/data/holdings/
```

## 自动下载

如果投资账本网页的导出按钮稳定，可以改用 Playwright：

```bash
python3 -m pip install playwright
python3 -m playwright install chromium
```

然后在 `config.toml` 中设置：

```toml
[ledger]
url = "你的投资账本地址"
mode = "playwright"
download_selectors = "text=导出,text=下载持仓"
```

复杂登录建议继续用 `manual`，让浏览器保留登录态，脚本只负责打开页面和等待导出文件。

## 每天下午五点运行

macOS 可以用 `launchd`。先把 `launchd/com.local.etf-position-assistant.plist` 里的路径确认一遍，然后安装：

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

如果你的投资账本导出的字段名不同，直接改 `config.toml` 的 `[columns]`。
