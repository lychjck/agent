# 我的持仓分析 Agent 详细实现计划

> **给后续执行者:** 实现本计划时，请按任务逐项执行、逐项验证。不要跳过测试，不要一次性大重构。

**目标:** 把当前 ETF/基金持仓日报工具升级成一个本地个人持仓分析 agent：它能同步持仓、拉取行情、补全标的信息、做行业和资产分类、生成组合画像、记住历史，并在用户确认策略规则后生成风险诊断和候选动作。第一版先做事实画像和解释，不自动交易，不把未确认规则当成投资建议。

**架构:** 第一版继续保持本地单体，不急着拆成多个服务。`stock_assistant.py` 先承载核心 agent 编排、数据处理、规则分析、分类、记忆和 LLM 调用；`api.py` 对外提供 FastAPI 接口；`frontend/src/App.tsx` 逐步升级成 agent 驾驶舱。Agent 不能只靠 LLM，应该是“确定性工具 + 搜索取证 + 规则引擎 + 记忆 + LLM 解释层”的组合。

**技术栈:** Python 3.12、FastAPI、Pydantic、`unittest`、OpenAI-compatible LLM API、TOML 配置、本地 JSON/JSONL 快照、React/Vite/TypeScript/Recharts。

---

## 1. 当前项目状态

当前主要文件：

- CLI 入口：`/Users/liyanran/github/stock/stock_assistant.py`
- 核心包：`/Users/liyanran/github/stock/stock_assistant/`
- FastAPI 后端：`/Users/liyanran/github/stock/api.py`
- React 前端：`/Users/liyanran/github/stock/frontend/src/App.tsx`
- 配置样例：`/Users/liyanran/github/stock/config.example.toml`
- 后端测试：`/Users/liyanran/github/stock/tests/`
- 持仓快照归档：`/Users/liyanran/github/stock/data/holdings`
- 报告输出：`/Users/liyanran/github/stock/reports`

当前已经具备的能力：

- 从 CSV/XLSX 解析持仓。
- 从投资账本 `tzzb_api` 同步持仓。
- 拉取 ETF 日 K。
- 计算 MA20/MA60/MA120、RSI、回撤、波动率、量比。
- 用规则输出“可分批加仓 / 持有观察 / 减仓/暂停加仓”。
- 调用 LLM 生成结构化 JSON 诊断。
- 支持分类配置、分类缓存、搜索 provider、本地 LLM 搜索证据分类。
- 前端展示总资产、盈亏、资产分布、K 线、AI 诊断。

当前验证结果：

```bash
uv run python -m unittest discover -s tests
```

结果：当前通过 26 个后端测试。

已修复的早期问题：

- `fetch_tzzb_holdings(config)` 返回 3 个值时，CLI `run()` 已按 `(holdings, archived, _summary)` 接收。
- 前端 Recharts tooltip formatter 类型问题已作为 Task 0 的修稳项处理。

当前 git 状态里有已有改动：

- `api.py`
- `frontend/src/App.tsx`
- `stock_assistant.py`
- `stock_assistant/`
- `tests/test_research_cache.py`
- `tests/test_search_provider.py`
- `data/research/`
- `data/state/`

实现时不要回滚这些改动。每次改文件前先看 diff，确认只改和当前任务相关的部分。

### 1.1 当前实现进度快照（2026-05-12）

已完成基础版：

- Task 0：修稳当前工程。
- Task 1：统一序列化契约。
- Task 2：加入 agent/profile/policy/classification/search 配置基础结构。
- Task 4：分类缓存、手动配置优先级、搜索工具接口、搜索缓存读取。
- Task 5：`manual_json` / Tavily / Brave 搜索 provider、搜索 freshness、搜索内容写入 JSON、本地 LLM 搜索证据分类。

已验证：

```bash
uv run python -m unittest discover -s tests
```

当前通过 26 个后端测试。

未完成或待实测：

- Tavily / Brave 真实外部 API 端到端联调。
- `http://10.33.207.193:1234/v1` 的 `google/gemma-4-31b` 本地模型端到端联调。
- Task 6：组合画像和观察项。
- Task 8：历史快照和 stable id diff。
- Task 10：agent SSE 编排接口。
- Task 9：LLM 报告结构化校验和 repair retry。
- Task 7：候选动作，等用户确认 `[policy]` 后再做。

---

## 2. 关键产品判断

### 2.1 这个 Agent 不是只用 LLM

LLM 不是 agent 的全部。LLM 适合做解释、归纳、排序、生成用户可读报告，但不适合承担全部事实获取、分类、计算、风控和状态管理。

这个 agent 至少需要这些“工具”：

| 工具类别 | MVP 是否需要 | 作用 | 实现方式 |
| --- | --- | --- | --- |
| 持仓读取工具 | 需要 | 从 TZZB/API/CSV/XLSX 读取持仓 | 确定性 Python 函数 |
| 行情工具 | 需要 | 拉取 ETF/股票/基金行情，缓存 K 线 | 确定性 Python 函数 |
| 策略约束工具 | 后置 | 读取个人风险偏好、仓位上限、配置目标；用户确认 policy 后启用 | 确定性 Python 函数 |
| 分类工具 | 需要 | 判断宽基、行业、主题、债券、海外、现金等 | 规则 + 缓存 + 搜索 + LLM |
| 组合分析工具 | 需要 | 第一版计算分类占比、Top 暴露、未知占比等组合画像；后续再做风险标签 | 确定性 Python 函数 |
| 历史记忆工具 | 需要 | 保存每日快照，对比昨天和今天变化 | 本地 JSON/JSONL |
| 候选动作工具 | 后置 | 用户确认 policy 后，根据规则生成可审阅动作 | 确定性 Python 函数 |
| LLM 报告工具 | 需要 | 解释证据、解释组合画像、必要时排序候选动作、生成报告 | LLM |
| 搜索工具 | 已完成基础版 | 查询 ETF 跟踪指数、基金公司、行业归属、主题暴露 | Tavily / Brave / 手工 JSON |
| 告警工具 | 第三阶段加入 | 风险触发后生成提醒 | 本地报告，后续可接飞书/邮件 |
| 交易工具 | 不做 | 自动下单、提交交易 | 第一版禁止 |

这里的“工具”不一定是 OpenAI tool calling、MCP server 或插件。第一版直接实现为稳定的 Python 函数，每个函数有明确输入输出，由后端 agent orchestrator 显式调用。等本地逻辑稳定后，再决定是否把部分函数暴露为 LLM function calling tools 或 MCP tools。

### 2.2 工具实现形态：先 Python Tools，不做 MCP

已确认决策：

```text
第一版直接写 Python tools，不做 MCP。
MCP 只作为未来把部分安全工具暴露给外部 agent/客户端的适配层。
```

原因：

- 当前只有一个本地 FastAPI 后端和一个本地前端，不需要跨客户端工具协议。
- 持仓、Cookie、TZZB 原始响应都属于敏感数据，不适合一开始暴露成 MCP。
- 投资分析需要可测试、可回放、可解释，显式 Python 编排比“模型自主调工具”更稳。
- MCP 会增加额外 server、schema、权限、部署和调试复杂度。
- 现在真正需要的是把工具边界设计清楚，而不是先上协议。

第一版工具形态：

```python
def fetch_holdings(config: dict[str, Any]) -> list[Holding]:
    ...

def fetch_market_data(code: str, config: dict[str, Any]) -> list[Bar]:
    ...

def classify_holding(holding: Holding, config: dict[str, Any]) -> InstrumentClassification:
    ...

def search_instrument_metadata(code: str, name: str, config: dict[str, Any]) -> list[dict[str, str]]:
    ...

def summarize_portfolio(
    holdings: list[Holding],
    classifications: dict[str, InstrumentClassification],
    config: dict[str, Any],
) -> dict[str, Any]:
    ...

def evaluate_policy(summary: dict[str, Any], config: dict[str, Any]) -> list[RiskFlag]:
    ...

def save_agent_snapshot(snapshot: dict[str, Any], config: dict[str, Any]) -> Path:
    ...

def generate_agent_report(context: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    ...
```

第二版再抽象统一工具接口，但仍然由后端显式调度：

```python
@dataclasses.dataclass(frozen=True)
class ToolResult:
    ok: bool
    data: dict[str, Any]
    error: str = ""
    evidence: tuple[dict[str, str], ...] = ()


class AgentTool:
    name: str
    description: str

    def run(self, input: dict[str, Any], context: dict[str, Any]) -> ToolResult:
        raise NotImplementedError
```

后续是否接 function calling：

- 可以先给 `search_instrument_metadata`、`classify_instrument` 这类低敏工具做 OpenAI-compatible tool schema。
- 不要让 LLM 直接调用 `fetch_tzzb_holdings`、读取 cookie、读取原始账户快照、写入敏感文件。
- 即使接 function calling，也要由后端做 allowlist、参数校验、结果审计和敏感字段过滤。

什么时候才需要 MCP：

1. 你希望 Codex、Claude Desktop、ChatGPT 或其他外部 agent 直接调用这个项目里的工具。
2. 你希望“查询组合风险 / 查询历史快照 / 搜索标的信息”成为跨项目复用能力。
3. 你有多个 agent 客户端，不止当前 FastAPI 后端。
4. 工具输入输出已经稳定，并且权限边界已经清楚。

第一版不要做成 MCP 的工具：

- `fetch_tzzb_holdings`
- `cookie_from_ledger_config`
- `tzzb_post`
- 读取 `.tzzb-curl` / `.env`
- 读取原始账户数据
- 写入敏感快照

未来可以考虑暴露为 MCP 的低敏工具：

- `search_instrument_metadata`
- `classify_instrument`
- `get_portfolio_summary`
- `get_latest_agent_snapshot`
- `explain_risk_flags`

MCP 设计原则：

- MCP 只是适配层，不是核心业务逻辑所在地。
- MCP server 只调用已经存在的 Python tools，不重新实现一套逻辑。
- MCP 输出必须过滤敏感字段。
- MCP 工具默认只读，写操作需要额外确认。
- MCP 不暴露 Cookie、API key、原始 TZZB 响应和精确账户信息。

### 2.3 为什么搜索工具是必要的

你的判断是对的：如果要做真正的持仓分析 agent，尤其涉及行业分类、ETF 跟踪指数、基金主题、发行方、跨市场资产类型，搜索工具一定会有用。

搜索工具主要用于：

- 查询未知 ETF/基金的跟踪指数。
- 确认某个 ETF 属于宽基、行业、主题、债券、海外、商品还是货币。
- 查询基金公司或产品页。
- 查证某个名称模糊的标的到底对应什么资产。
- 定期刷新已缓存的标的元数据。
- 在用户允许时补充外部市场背景。

搜索工具不应该用于：

- 每天默认搜索所有持仓。
- 把你的精确持仓金额、盈亏、仓位比例发给搜索引擎。
- 让 LLM 凭搜索结果编新闻故事。
- 替代本地行情和规则分析。

搜索查询应该只包含公开标的信息，例如：

```text
510300 沪深300ETF 跟踪指数 基金公司
512880 证券ETF 跟踪指数 行业
159915 创业板ETF 跟踪指数 基金公司
513500 标普500ETF 跟踪指数
518880 黄金ETF 跟踪标的
```

不应该搜索：

```text
我持仓 512880 多少钱 要不要减仓
我的账户 今天亏损多少 怎么操作
```

搜索返回的结果必须缓存，并保存：

- 查询词。
- 标的代码。
- 标的名称。
- 结果标题。
- URL。
- 摘要。
- 获取时间。
- 使用这个结果得出的分类。
- 分类置信度。
- 是否经过用户确认。

### 2.4 行业分类谁来做

建议不要完全交给 LLM，也不要完全靠你手工做。最合理的是混合方案：

1. 你在 `config.toml` 里手动覆盖的分类优先级最高。
2. 已经缓存且经过你确认的分类第二优先。
3. 经过搜索证据验证且仍在有效期内的缓存第三优先。
4. 搜索工具查询公开资料。
5. LLM 只基于搜索证据做结构化归类建议。
6. 极少数本地启发式规则只能做 fallback 或搜索 hint，不能阻止搜索。
7. 置信度低的分类必须进入“待确认”状态。

分类优先级：

```text
用户手动配置 > 用户确认过的缓存 > 已验证搜索缓存 > 搜索证据 + LLM 归类 > 本地启发式 fallback > unknown
```

也就是说：

- 你不需要手动维护所有分类。
- LLM 可以帮你分类。
- 但 LLM 必须基于搜索证据或明确规则。
- 影响风控的分类，低置信度不能直接当真。

例如 `512880 证券ETF`：

- 本地启发式看到“证券”两个字，只能生成低置信度 hint，例如 `sector_equity / financials / brokerage`。
- 搜索工具查产品页，确认它跟踪证券公司指数。
- LLM 把证据归入 taxonomy。
- 如果置信度大于 `0.75`，可以自动使用；否则前端提示你确认。

### 2.5 为什么不能让 LLM 直接决定买卖

LLM 可以解释和组织信息，但不要让它直接凭空决定买卖。正确做法：

1. 本地规则先生成候选动作。
2. 候选动作必须带证据。
3. LLM 只能解释、排序、补充观察条件。
4. 如果 LLM 想新增动作，必须标记为“问题/观察”，不能直接变成操作建议。
5. 所有动作都需要用户确认。

候选动作示例：

```json
{
  "id": "rebalance-512880-single-overweight",
  "type": "rebalance",
  "target_code": "512880",
  "target_name": "证券ETF",
  "priority": "medium",
  "reason": "单只持仓超过策略上限",
  "evidence": [
    "weight=23.20% > limit 20.00%",
    "sector=financials actual 42.30% > limit 35.00%"
  ],
  "requires_user_confirmation": true
}
```

LLM 可以把它解释成自然语言，但不能私自变成“明天卖出 50%”。

---

## 3. 目标 Agent 工作流

每天运行时，agent 应该按下面流程走：

1. 读取配置。
2. 同步持仓。
3. 标准化持仓字段。
4. 读取上一份 agent 快照。
5. 对每个标的做分类。
6. 对未知或过期分类决定是否调用搜索工具。
7. 拉取或读取缓存行情。
8. 计算技术指标。
9. 计算组合层面的资产分类、行业暴露、主题暴露、单标的集中度。
10. 第一版生成组合画像和事实观察项，不做策略检查。
11. 如果用户后续确认 `[policy]`，再按策略配置检查风险。
12. 如果存在已确认策略规则，再生成确定性的候选动作。
13. 把持仓、指标、分类、历史变化、组合画像和观察项交给 LLM。
14. 如果已有风险标签和候选动作，也一并交给 LLM 解释。
15. LLM 输出结构化 JSON。
16. 后端校验 JSON schema。
17. 如果 LLM 输出不合法，先触发一次 JSON 修复重试；仍失败才回退到确定性结果。
18. 保存快照。
19. 生成报告。
20. 触发告警。
21. 前端展示 agent 结果，等待你确认。

API 运行时必须用 SSE 流式推送状态，而不是等全流程结束后同步返回。第一版就要支持：

- `sync_holdings`
- `classify`
- `search_metadata`
- `market_data`
- `technical_analysis`
- `portfolio_profile`
- `portfolio_observations`
- `policy_eval`，仅在启用策略规则后出现。
- `candidate_actions`，仅在启用策略规则后出现。
- `llm_report`
- `save_snapshot`
- `done`

原因：持仓数量超过 10 支后，搜索、行情、LLM 总耗时可能达到 20 到 60 秒；同步阻塞接口很容易触发浏览器、代理或前端请求超时。

LLM 输入应该包含：

- 当前持仓。
- 技术指标。
- 组合摘要。
- 组合画像。
- 观察项。
- 策略约束，仅在用户已确认 `[policy]` 后传入。
- 分类结果。
- 分类证据。
- 历史变化。
- 风险标签，仅在策略检查启用后传入。
- 候选动作，仅在策略检查启用后传入。

LLM 不应该收到：

- Cookie。
- API key。
- 原始 TZZB 响应。
- 账号 ID。
- 非必要的交易账户信息。

---

## 4. 配置设计

修改：

- `/Users/liyanran/github/stock/config.example.toml`
- `/Users/liyanran/github/stock/stock_assistant.py` 里的 `DEFAULTS`

新增配置：

```toml
[profile]
base_currency = "CNY"
risk_level = "balanced"
investment_style = "long_term_etf"
allow_external_search = false
allow_external_llm = true

[policy]
cash_min_pct = 5
max_single_position_pct = 20
max_sector_pct = 35
max_theme_pct = 25
max_unknown_classification_pct = 10
loss_alert_pct = -8
gain_trim_pct = 20
rebalance_drift_pct = 5
rebalance_target_buffer_pct = 2

[allocation_targets]
broad_index = 40
sector_equity = 25
bond = 15
overseas = 10
commodity = 5
cash = 5

[classification]
mode = "hybrid"
require_user_review_below_confidence = 0.75
cache_ttl_days = 90

[classifications."510300"]
asset_class = "broad_index"
sector = ""
theme = "csi300"
region = "china_a"
strategy = "passive_index"
reviewed_by_user = true

[search]
enabled = false
provider = "manual_json"
cache_dir = "/Users/liyanran/github/stock/data/research"
timeout_seconds = 20
max_results = 5
search_depth = "basic"
include_raw_content = false
api_key_env = ""
manual_results_file = "/Users/liyanran/github/stock/data/research/manual_search_results.json"

[search.providers.tavily]
enabled = false
api_key_env = "TAVILY_API_KEY"
search_depth = "basic"
topic = "finance"

[search.providers.brave]
enabled = false
api_key_env = "BRAVE_SEARCH_API_KEY"

[search.source_tiers]
tier1 = "sse.com.cn,szse.cn,csindex.com.cn,cnindex.com.cn"
tier2 = "eastmoney.com,10jqka.com.cn,fund.eastmoney.com"

[agent]
enabled = true
strict_json = true
llm_can_create_new_actions = false
save_snapshots = true
snapshot_dir = "/Users/liyanran/github/stock/data/state"

# 追加到已有 [llm] 配置中，不是新建第二个 [llm] section。
structured_output = "auto"
repair_attempts = 1
```

`.gitignore` 需要新增：

```gitignore
data/state/*
!data/state/.gitkeep
data/research/*
!data/research/.gitkeep
```

---

## 5. 分类体系设计

第一版不要搞太复杂，先用粗粒度 taxonomy。

资产大类：

```python
ASSET_CLASSES = {
    "broad_index",      # 宽基指数
    "sector_equity",   # 行业 ETF
    "theme_equity",    # 主题 ETF
    "active_equity",   # 主动权益基金
    "mixed_allocation",# 混合配置基金
    "bond",            # 债券
    "bond_fund",       # 债券基金
    "overseas",        # 海外资产
    "qdii",            # QDII 基金
    "commodity",       # 商品，例如黄金
    "cash",            # 现金/货币基金
    "money_market",    # 货币基金
    "active_fund",     # 主动基金
    "fof",             # FOF
    "unknown",         # 未知
}
```

场外主动基金不要强行归到单一行业。很多主动基金名称不包含行业信息，例如“易方达蓝筹精选”“中欧时代先锋”，它们可能是全市场选股、混合配置或风格暴露。第一版应把它们归为 `active_equity`、`mixed_allocation`、`bond_fund`、`money_market`、`qdii`、`fof` 等大类，`sector` 留空或 `unknown`。如果要分析主动基金行业暴露，需要单独接基金季报、前十大重仓、行业配置等数据源，不能靠名字硬猜。

行业分类：

```python
SECTORS = {
    "financials",
    "technology",
    "semiconductor",
    "healthcare",
    "consumer",
    "energy",
    "materials",
    "industrial",
    "defense",
    "real_estate",
    "unknown",
}
```

地区：

```python
REGIONS = {
    "china_a",
    "hong_kong",
    "us",
    "global",
    "unknown",
}
```

策略：

```python
STRATEGIES = {
    "passive_index",
    "active_fund",
    "money_market",
    "bond_index",
    "commodity_backed",
    "unknown",
}
```

分类结果结构：

```json
{
  "code": "512880",
  "name": "证券ETF",
  "asset_class": "sector_equity",
  "sector": "financials",
  "theme": "brokerage",
  "region": "china_a",
  "strategy": "passive_index",
  "tracked_index": "证券公司指数",
  "issuer": "unknown",
  "confidence": 0.82,
  "source": "search_llm_suggested",
  "evidence": [
    {
      "title": "基金产品页",
      "url": "https://example.com/...",
      "retrieved_at": "2026-05-11T15:00:00+08:00"
    }
  ],
  "reviewed_by_user": false
}
```

---

## 6. 数据模型

现有 dataclass 保留：

- `Holding`
- `Bar`

新增 dataclass：

```python
@dataclasses.dataclass(frozen=True)
class InstrumentClassification:
    code: str
    name: str
    asset_class: str = "unknown"
    sector: str = ""
    theme: str = ""
    region: str = "unknown"
    strategy: str = "unknown"
    tracked_index: str = ""
    issuer: str = ""
    confidence: float = 0.0
    source: str = "unknown"
    evidence: tuple[dict[str, str], ...] = ()
    reviewed_by_user: bool = False
```

```python
@dataclasses.dataclass(frozen=True)
class RiskFlag:
    id: str
    code: str
    label: str
    severity: str
    evidence: tuple[str, ...]
```

```python
@dataclasses.dataclass(frozen=True)
class CandidateAction:
    id: str
    type: str
    target_code: str
    target_name: str
    priority: str
    reason: str
    evidence: tuple[str, ...]
    reason_code: str = ""
    current_weight_pct: float | None = None
    target_weight_pct: float | None = None
    current_value: float | None = None
    target_value: float | None = None
    delta_value: float | None = None
    delta_weight_pct: float | None = None
    constraint: str = ""
    source: str = "rule_engine"
    requires_user_confirmation: bool = True
```

`RiskFlag.id` 和 `CandidateAction.id` 必须是稳定 ID。生成 ID 时只使用风险类型、作用域和标的代码/行业，不包含每日波动的小数值。这样历史 diff 才能区分“旧风险延续”和“新风险产生”。

稳定 ID 示例：

```text
risk:single_position_overweight:512880
risk:sector_concentration:financials
action:rebalance:single_overweight:512880
action:watch:weak_trend:510300
```

第二版工具接口可以增加下面两个类型。第一版不强制使用，避免过早抽象；当工具数量超过 6 个且测试开始重复时再引入。

```python
@dataclasses.dataclass(frozen=True)
class ToolResult:
    ok: bool
    data: dict[str, Any]
    error: str = ""
    evidence: tuple[dict[str, str], ...] = ()
```

```python
class AgentTool:
    name: str
    description: str

    def run(self, input: dict[str, Any], context: dict[str, Any]) -> ToolResult:
        raise NotImplementedError
```

第一版不要把这些工具做成 MCP server。MCP 是未来对外暴露工具的适配层，不是 agent 核心逻辑。

后续如果 `stock_assistant.py` 太大，再拆成：

- `core/models.py`
- `core/classification.py`
- `core/policy.py`
- `core/agent.py`
- `core/memory.py`
- `core/search.py`

第一版先不拆，降低变更风险。

---

## 7. 评审问题修正清单

下面 5 个问题都是真问题，已纳入后续任务设计。

| 问题 | 判断 | 优先级 | 修正位置 |
| --- | --- | --- | --- |
| 同步阻塞 API 可能超时 | 真问题 | P0 | Task 10 改为第一版必须 SSE |
| LLM JSON 失败直接丢弃太保守 | 真问题 | P1 | Task 9 增加 JSON mode / repair retry / fallback |
| 候选动作缺定量目标 | 真问题 | P1 | Task 7 增加 target_weight/value/delta 字段 |
| 历史 diff 直接比对象会制造噪音 | 真问题 | P0/P1 | Task 8 增加 stable id diff |
| 主动基金不能按 ETF 名称规则硬分行业 | 真问题 | P1 | 分类体系增加 active_equity/mixed_allocation 等 |

实施顺序建议：

1. Task 0、Task 1、Task 2、Task 4、Task 5 基础版已经完成。
2. 下一步先做 Task 6 的组合画像，不做策略检查。
3. 再做 Task 10 的 SSE，避免第一版 agent 运行时间过长导致 API 超时。
4. 再做 Task 8 的 stable id 和快照，否则记忆系统会产生大量假变化。
5. Task 7 的定量候选动作暂缓，等用户确认 `[policy]` 后再做。
6. Task 9 的结构化输出和修复重试在 LLM 报告阶段做。
7. 已删除独立“本地分类引擎”任务；本地启发式只作为 Task 5 的最后 fallback。

---

## 8. 分阶段实现任务

### Task 0：先修稳当前工程（已完成）

**涉及文件：**

- 修改：`/Users/liyanran/github/stock/stock_assistant.py`
- 修改：`/Users/liyanran/github/stock/frontend/src/App.tsx`
- 测试：`/Users/liyanran/github/stock/tests/test_stock_assistant.py`

**步骤 1：给 TZZB 返回值写回归测试**

在 `tests/test_stock_assistant.py` 里 mock `fetch_tzzb_holdings()`，让它返回：

```python
([holding], Path("snapshot.json"), {"total_asset": 1000})
```

验证 `run(config, holdings_file=None)` 不会因为返回值数量报错。

**步骤 2：运行测试，确认失败**

```bash
uv run python -m unittest tests.test_stock_assistant.StockAssistantTest.test_run_accepts_tzzb_summary_return
```

预期修复前失败：

```text
ValueError: too many values to unpack
```

**步骤 3：修复 `run()`**

把：

```python
holdings, archived = fetch_tzzb_holdings(config)
```

改为：

```python
holdings, archived, _summary = fetch_tzzb_holdings(config)
```

**步骤 4：修复前端 Recharts 类型**

把 `frontend/src/App.tsx` 里 tooltip formatter 改成兼容 `name` 可能为 undefined：

```tsx
formatter={(value, name) => [
  `¥${Number(value).toLocaleString()}`,
  String(name ?? '市值'),
]}
```

**步骤 5：验证**

后端：

```bash
uv run python -m unittest discover -s tests
```

前端：

```bash
cd /Users/liyanran/github/stock/frontend
npm run build
```

验收：

- 后端测试全部通过。
- 前端 build 通过。
- 不提交 git，除非用户明确要求。

---

### Task 1：统一序列化契约（已完成）

**涉及文件：**

- 修改：`/Users/liyanran/github/stock/stock_assistant.py`
- 修改：`/Users/liyanran/github/stock/api.py`
- 新增：`/Users/liyanran/github/stock/tests/test_serialization.py`

当前 `api.py` 里多处手写 dict，后面 agent 数据会越来越多，必须先统一序列化。

新增函数：

```python
def holding_to_dict(holding: Holding) -> dict[str, Any]:
    return {
        "code": holding.code,
        "name": holding.name,
        "quantity": holding.quantity,
        "cost_price": holding.cost_price,
        "market_value": holding.market_value,
        "profit_pct": holding.profit_pct,
        "hold_profit": holding.hold_profit,
        "day_profit": holding.day_profit,
        "asset_type": holding.asset_type,
    }
```

```python
def bar_to_dict(bar: Bar | None) -> dict[str, Any] | None:
    if bar is None:
        return None
    return {
        "date": str(bar.date),
        "open": bar.open,
        "close": bar.close,
        "high": bar.high,
        "low": bar.low,
        "volume": bar.volume,
        "amount": bar.amount,
        "pct_change": bar.pct_change,
    }
```

```python
def analysis_result_to_dict(item: dict[str, Any]) -> dict[str, Any]:
    output = dict(item)
    holding = output.get("holding")
    if isinstance(holding, Holding):
        output["holding"] = holding_to_dict(holding)
    latest = output.get("latest")
    if isinstance(latest, Bar):
        output["latest"] = bar_to_dict(latest)
    return output
```

测试覆盖：

- 普通 ETF 持仓。
- 场外基金持仓。
- 有 `latest` 的分析结果。
- 无 `latest` 的失败结果。
- `行情失败` 结果。

验证：

```bash
uv run python -m unittest tests.test_serialization
uv run python -m unittest discover -s tests
```

---

### Task 2：加入个人策略配置（已完成基础版）

**涉及文件：**

- 修改：`/Users/liyanran/github/stock/config.example.toml`
- 修改：`/Users/liyanran/github/stock/stock_assistant.py`
- 新增：`/Users/liyanran/github/stock/tests/test_policy_config.py`

要解决的问题：

- 现在的规则是通用规则。
- Agent 必须知道“我的策略”。
- 没有策略，就只能写泛泛的报告。

当前实现状态：

- 已在 `stock_assistant/config.py` 的 `DEFAULTS` 中加入 `profile`、`policy`、`allocation_targets`、`classification`、`search`、`agent`。
- 已加入 `policy_value(config, key, fallback)`，支持 `[policy]` 优先、`[analysis]` 兼容。
- 已在 `config.example.toml` 中补充 agent 相关配置示例。

新增默认配置到 `DEFAULTS`：

- `profile`
- `policy`
- `allocation_targets`
- `classification`
- `search`
- `agent`

新增兼容函数：

```python
def policy_value(config: dict[str, Any], key: str, fallback: Any = None) -> Any:
    if key in config.get("policy", {}):
        return config["policy"][key]
    if key in config.get("analysis", {}):
        return config["analysis"][key]
    return fallback
```

把 `decide_action()` 中这些值改成从 `policy_value()` 读取：

- `loss_alert_pct`
- `gain_trim_pct`
- `max_single_position_pct`

测试：

- `DEFAULTS` 包含新增配置。
- 老配置 `[analysis]` 仍然兼容。
- 新配置 `[policy]` 优先级更高。

---

### Task 4：实现分类缓存和搜索工具接口（已完成基础版）

**涉及文件：**

- 修改：`/Users/liyanran/github/stock/stock_assistant.py`
- 修改：`/Users/liyanran/github/stock/config.example.toml`
- 修改：`/Users/liyanran/github/stock/.gitignore`
- 新增：`/Users/liyanran/github/stock/data/research/.gitkeep`
- 新增：`/Users/liyanran/github/stock/tests/test_research_cache.py`

当前实现状态：

- 已拆出 `stock_assistant/classification.py` 和 `stock_assistant/search.py`。
- 已实现 `classification_from_config()`、`research_cache_path()`、`load_cached_classification()`、`save_classification_cache()`、`classification_cache_is_fresh()`、`classify_holding()`。
- 已实现 `DisabledSearchProvider`、`ManualJsonSearchProvider` 和 `build_search_provider()`。
- 已加入 `tests/test_research_cache.py`。
- 当前实际缓存路径为 `data/research/{code}.json`，不是更深的 `data/research/instruments/{code}.json`。第一版保持简单，后续如果缓存文件变多再迁移目录结构。
- 未人工确认的搜索缓存，如果置信度低于 `require_user_review_below_confidence` 或缺少 `snippet/content/raw_content`，不会直接用于分类。

第一版先不要绑定真实搜索 API，先把接口和缓存做好。

新增缓存路径：

```text
data/research/{code}.json
```

新增函数：

- `classification_from_config(holding, config) -> InstrumentClassification | None`
- `research_cache_path(code, config) -> Path`
- `load_cached_classification(holding, config) -> InstrumentClassification | None`
- `save_classification_cache(classification, config) -> Path`
- `classification_cache_is_fresh(record, ttl_days) -> bool`
- `classify_holding(holding, config) -> InstrumentClassification`

`classification_from_config()` 只读取用户显式配置，返回 `confidence=1.0`、`reviewed_by_user=true` 的可信分类。

```python
def classification_from_config(holding: Holding, config: dict[str, Any]) -> InstrumentClassification | None:
    record = config.get("classifications", {}).get(holding.code)
    if not isinstance(record, dict):
        return None
    return InstrumentClassification(
        code=holding.code,
        name=holding.name,
        asset_class=str(record.get("asset_class", "unknown")),
        sector=str(record.get("sector", "")),
        theme=str(record.get("theme", "")),
        region=str(record.get("region", "unknown")),
        strategy=str(record.get("strategy", "unknown")),
        tracked_index=str(record.get("tracked_index", "")),
        issuer=str(record.get("issuer", "")),
        confidence=1.0,
        source="config",
        reviewed_by_user=True,
    )
```

`classify_holding()` 的优先级：

```python
def classify_holding(holding: Holding, config: dict[str, Any]) -> InstrumentClassification:
    return (
        classification_from_config(holding, config)
        or load_reviewed_cached_classification(holding, config)
        or load_verified_search_cache(holding, config)
        or suggest_classification_with_search(holding, config)
        or local_heuristic_fallback(holding)
        or InstrumentClassification(code=holding.code, name=holding.name, source="unknown")
    )
```

注意：`local_heuristic_fallback()` 只允许返回低置信度 hint，不能作为可信分类阻止搜索。它的作用是搜索失败后的兜底，不是主分类引擎。

搜索工具接口：

```python
class SearchProvider:
    def search(self, query: str, max_results: int) -> list[dict[str, str]]:
        raise NotImplementedError
```

禁用搜索 provider：

```python
class DisabledSearchProvider(SearchProvider):
    def search(self, query: str, max_results: int) -> list[dict[str, str]]:
        return []
```

provider 工厂：

```python
def build_search_provider(config: dict[str, Any]) -> SearchProvider:
    search = config.get("search", {})
    if not config_bool(search.get("enabled", False)):
        return DisabledSearchProvider()
    provider = str(search.get("provider", "none")).lower()
    if provider == "none":
        return DisabledSearchProvider()
    raise RuntimeError(f"未知搜索工具 provider: {provider}")
```

搜索分类 stub：

```python
def suggest_classification_with_search(holding: Holding, config: dict[str, Any]) -> InstrumentClassification | None:
    provider = build_search_provider(config)
    query = f"{holding.code} {holding.name} ETF 跟踪指数 行业 基金公司"
    results = provider.search(query, int(config.get("search", {}).get("max_results", 5)))
    if not results:
        return None
    return None
```

测试：

- 用户手动配置优先，并返回可信分类。
- 保存分类缓存。
- 读取分类缓存。
- 过期缓存被忽略。
- 用户确认过的缓存优先。
- 搜索禁用时不报错，返回空结果。
- 没有配置、没有缓存、搜索失败时才使用低置信度 fallback。

---

### Task 5：加入真实搜索适配器和搜索证据分类（已完成基础版，真实外部 API 待实测）

**涉及文件：**

- 修改：`/Users/liyanran/github/stock/stock_assistant.py`
- 修改：`/Users/liyanran/github/stock/config.example.toml`
- 新增：`/Users/liyanran/github/stock/tests/test_search_provider.py`

搜索 provider 决策：

- 第一优先：`manual_json`，用于本地测试和无网络 fallback。
- 第一真实 API：`tavily`，适合 AI/RAG 场景，有免费额度，支持 search/extract、domain 约束和 finance topic。
- 备选真实 API：`brave`，适合通用搜索，有免费 credit，结果质量较稳。
- 暂不推荐：Google Custom Search JSON API 和 Bing Web Search API。Google Custom Search JSON API 已不适合新项目接入；Bing Web Search API 已退休并迁移到 Grounding with Bing，成本和定位都不适合本地个人工具。

搜索 provider 参考表：

| Provider | 免费/低成本情况 | 适合度 | 用途 |
| --- | --- | --- | --- |
| Tavily | 免费额度适合个人轻量使用，basic search 成本低 | 高 | AI/RAG 搜索、网页抽取、标的元数据补全 |
| Brave Search API | 有免费 credit，之后按请求计费 | 高 | 通用 web search 备用 |
| Exa | 有免费额度，偏语义搜索 | 中 | 英文资料、研究型检索备用 |
| SerpApi | 免费额度较小 | 中 | 需要 Google/Baidu 类结果时备用 |
| SearXNG 自建 | 软件免费，维护成本高 | 中 | 强隐私但需要维护 |
| Google Custom Search JSON API | 不推荐新项目 | 低 | 老项目迁移前备用 |
| Bing Web Search API | 已退休 | 低 | 不作为方案 |

参考链接：

- Tavily API credits: `https://docs.tavily.com/documentation/api-credits`
- Tavily Search API: `https://docs.tavily.com/documentation/api-reference/endpoint/search`
- Brave Search API pricing: `https://api-dashboard.search.brave.com/documentation/pricing`
- Exa pricing: `https://exa.ai/pricing`
- SerpApi pricing: `https://serpapi.com/pricing`
- Google Custom Search JSON API: `https://developers.google.com/custom-search/v1/overview`
- Bing Search API retirement: `https://learn.microsoft.com/en-us/lifecycle/announcements/bing-search-api-retirement`

注意：这些免费额度和价格可能变动，真正实现前要重新核对官方页面。

当前实现状态：

- 已实现 `ManualJsonSearchProvider`、`TavilySearchProvider`、`BraveSearchProvider`。
- 已支持 `freshness = "day|week|month|year|none"`。
- Tavily 会把 freshness 映射为 `time_range`，也支持 `start_date` / `end_date`。
- Brave 会把 freshness 映射为 `pd|pw|pm|py`。
- 搜索结果会标准化保存 `title`、`url`、`snippet`、`content`、`raw_content`、`published_date`、`retrieved_at`、`source`、`source_tier`。
- 已加入 `max_stored_content_chars`，避免把过长网页正文全部塞进缓存。
- 已加入分类专用本地 LLM，默认使用 `http://10.33.207.193:1234/v1`、`google/gemma-4-31b`、`disable_thinking = true`。
- 搜索证据分类现在是 LLM 优先，写死规则只作为 `search_rule_fallback`。
- 已加入 `tests/test_search_provider.py` 和 LLM 分类 mock 测试。
- 已通过 `uv run python -m unittest discover -s tests`。
- 尚未真实调用 Tavily / Brave / 本地 Gemma 服务做端到端联调。

配置：

```toml
[search]
enabled = true
provider = "manual_json"
manual_results_file = "/Users/liyanran/github/stock/data/research/manual_search_results.json"
cache_dir = "/Users/liyanran/github/stock/data/research"
max_results = 5
timeout_seconds = 20
include_raw_content = false
freshness = "year"
start_date = ""
end_date = ""
max_stored_content_chars = 4000

[search.providers.tavily]
enabled = false
api_key_env = "TAVILY_API_KEY"
search_depth = "basic"
topic = "finance"

[search.providers.brave]
enabled = false
api_key_env = "BRAVE_SEARCH_API_KEY"
```

手工搜索结果格式：

```json
{
  "512880 证券ETF ETF 跟踪指数 行业 基金公司": [
    {
      "title": "证券ETF 产品页",
      "url": "https://example.com/512880",
      "snippet": "跟踪证券公司指数...",
      "content": "产品资料正文或摘要...",
      "published_date": "2026-05-01"
    }
  ]
}
```

实现：

```python
class ManualJsonSearchProvider(SearchProvider):
    def __init__(self, path: Path):
        self.path = path

    def search(self, query: str, max_results: int) -> list[dict[str, str]]:
        if not self.path.exists():
            return []
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        rows = payload.get(query, [])
        if not isinstance(rows, list):
            return []
        return [row for row in rows[:max_results] if isinstance(row, dict)]
```

Tavily provider：

```python
class TavilySearchProvider(SearchProvider):
    def __init__(self, api_key: str, timeout_seconds: int, search_depth: str = "basic"):
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.search_depth = search_depth

    def search(self, query: str, max_results: int) -> list[dict[str, str]]:
        body = {
            "query": query,
            "topic": "finance",
            "search_depth": self.search_depth,
            "max_results": max_results,
            "include_raw_content": False,
        }
        # 用 urllib.request 发 POST，保持依赖简单。
        # 返回统一 SearchResult dict: title/url/snippet/source/provider/retrieved_at。
        ...
```

Brave provider：

```python
class BraveSearchProvider(SearchProvider):
    def __init__(self, api_key: str, timeout_seconds: int):
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    def search(self, query: str, max_results: int) -> list[dict[str, str]]:
        # 调 Brave Web Search API，统一输出 title/url/snippet/source/provider/retrieved_at。
        ...
```

搜索不是直接把结果交给 LLM。搜索结果必须先进入轻量 RAG/证据缓存流程：

```text
query
  -> search provider
  -> URL 去重
  -> 来源分级
  -> 字段抽取
  -> 交叉验证
  -> 置信度评分
  -> 写入 data/research/{code}.json
  -> 分类和风控只使用缓存后的结构化字段
```

第一版不需要向量数据库。这里的 RAG 是“搜索 + 证据缓存 + 结构化抽取”，不是把所有网页塞进 embedding store。

来源分级：

```text
Tier 1：最高可信
- 基金公司官网产品页
- 上交所 / 深交所页面
- 中证指数、国证指数、标普、MSCI 等指数公司官网
- 基金招募说明书、基金合同、定期报告

Tier 2：可用但需要交叉验证
- 天天基金、东方财富、同花顺等金融数据页
- 主流财经媒体资料页

Tier 3：只用于发现线索
- 博客
- 论坛帖子
- SEO 聚合站
- 未知来源网页
```

字段使用规则：

| 字段 | 可直接使用来源 | 是否需要交叉验证 |
| --- | --- | --- |
| 基金名称 | TZZB / 基金公司 / 交易所 | 一般不用 |
| 基金代码 | TZZB / 官方页面 | 一般不用 |
| 跟踪指数 | 基金公司 / 招募说明书 / 指数公司 | 最好两个来源 |
| 资产类别 | 官方跟踪指数 / 基金类型 / 已确认缓存；本地启发式只作弱证据 | 需要 |
| 行业分类 | 跟踪指数 + 名称 + 官方说明 | 需要 |
| 基金公司 | 官方产品页 / 招募说明书 | 一般不用 |
| 最新净值/价格 | 不从搜索取 | 必须走行情/净值数据源 |
| 新闻/观点 | 默认不用于分类 | 只能做备注 |

可信度评分：

```python
def score_classification_evidence(evidence: list[dict[str, Any]]) -> float:
    score = 0.0
    if has_tier1_source(evidence):
        score += 0.50
    if field_found_in_structured_table(evidence):
        score += 0.20
    if same_field_confirmed_by_second_source(evidence):
        score += 0.20
    if extracted_value_matches_name_rule(evidence):
        score += 0.10
    if only_tier3_sources(evidence):
        score -= 0.30
    if conflicts_with_existing_cache(evidence):
        score -= 0.40
    return max(0.0, min(score, 1.0))
```

使用规则：

```text
score >= 0.85：可以自动使用
0.65 <= score < 0.85：可以展示，但标记待确认
score < 0.65：不用于风控，只提示需要人工确认
```

缓存文件：

```text
data/research/{code}.json
```

缓存字段：

```json
{
  "code": "512880",
  "name": "证券ETF",
  "asset_class": "sector_equity",
  "sector": "financials",
  "tracked_index": "证券公司指数",
  "issuer": "unknown",
  "confidence": 0.86,
  "source": "search_verified",
  "evidence": [
    {
      "title": "...",
      "url": "...",
      "snippet": "...",
      "content": "...",
      "raw_content": "...",
      "published_date": "2026-05-01",
      "retrieved_at": "2026-05-11T20:00:00+08:00",
      "source": "tavily",
      "source_tier": "1"
    }
  ],
  "reviewed_by_user": false
}
```

从搜索证据分类：

```python
def classify_from_search_evidence_with_llm(
    holding: Holding,
    results: list[dict[str, Any]],
    config: dict[str, Any],
    evidence_score: float,
) -> InstrumentClassification | None:
    # 把搜索证据、受控 taxonomy 和标的信息发给本地 LLM。
    # LLM 只能输出 JSON，且 asset_class / sector / strategy 必须在白名单里。
    # 输出不合法、置信度不足或 evidence URL 不匹配时，不采信。
    ...
```

分类专用本地 LLM 配置：

```toml
[classification.llm]
enabled = true
client = "urllib"
base_url = "http://10.33.207.193:1234/v1"
model = "google/gemma-4-31b"
temperature = 0.0
timeout_seconds = 120
max_tokens = 2048
stream = false
disable_thinking = true
```

LLM 搜索归类不是直接“相信模型”。它只能把已筛选的搜索证据映射到受控 taxonomy，输出也要经过字段校验、来源校验和置信度规则。`/no_think`、`enable_thinking=false`、`chat_template_kwargs.enable_thinking=false` 都会用于本地模型，避免分类任务被思考过程拖慢。

---

### Task 6：组合画像和观察项（下一步，暂不做策略检查）

**涉及文件：**

- 新增或修改：`/Users/liyanran/github/stock/stock_assistant/portfolio.py`
- 修改：`/Users/liyanran/github/stock/stock_assistant/__init__.py`
- 新增：`/Users/liyanran/github/stock/tests/test_portfolio_profile.py`

当前决策：

- 用户目前没有设定明确策略规则，暂时也不设定规则。
- 所以 Task 6 第一版不做 `evaluate_policy()`，不生成 `RiskFlag`，不说“超限”。
- 第一版只做组合画像和事实观察项，帮助用户看清当前持仓结构。
- LLM 不参与组合画像计算。所有占比、金额、Top N 都由 Python 确定性计算。
- LLM 后续可以基于组合画像写解释，但不能替用户发明硬规则。

要实现的组合画像：

- 总市值。
- 按资产大类占比。
- 按行业占比。
- 按主题占比。
- 按策略类型占比，例如 `passive_index` / `active_management` / `mixed_allocation`。
- 按区域占比，例如 `china_a` / `overseas` / `unknown`。
- 场内 ETF / 场外基金占比。
- 主动基金 / 被动指数基金占比。
- 单标的仓位。
- 未分类资产占比。
- Top 5 单只持仓。
- Top 5 行业暴露。
- 低置信度分类占比。

函数：

```python
def summarize_portfolio(
    holdings: list[Holding],
    classifications: dict[str, InstrumentClassification],
    config: dict[str, Any],
) -> dict[str, Any]:
    total_value = sum(item.market_value or 0 for item in holdings)
    by_asset_class: dict[str, float] = {}
    by_sector: dict[str, float] = {}
    by_theme: dict[str, float] = {}
    by_strategy: dict[str, float] = {}
    by_region: dict[str, float] = {}
    by_asset_type: dict[str, float] = {}
    positions = []
    for holding in holdings:
        value = holding.market_value or 0
        cls = classifications.get(holding.code)
        asset_class = cls.asset_class if cls else "unknown"
        sector = cls.sector if cls and cls.sector else "unknown"
        theme = cls.theme if cls and cls.theme else "unknown"
        strategy = cls.strategy if cls and cls.strategy else "unknown"
        region = cls.region if cls and cls.region else "unknown"
        asset_type = holding.asset_type or "unknown"
        by_asset_class[asset_class] = by_asset_class.get(asset_class, 0) + value
        by_sector[sector] = by_sector.get(sector, 0) + value
        by_theme[theme] = by_theme.get(theme, 0) + value
        by_strategy[strategy] = by_strategy.get(strategy, 0) + value
        by_region[region] = by_region.get(region, 0) + value
        by_asset_type[asset_type] = by_asset_type.get(asset_type, 0) + value
        positions.append({
            "code": holding.code,
            "name": holding.name,
            "market_value": value,
            "weight": value / total_value * 100 if total_value else None,
            "asset_class": asset_class,
            "sector": sector,
            "theme": theme,
            "strategy": strategy,
            "region": region,
            "asset_type": asset_type,
            "classification_confidence": cls.confidence if cls else 0.0,
        })
    return {
        "total_value": total_value,
        "by_asset_class": value_map_to_pct(by_asset_class, total_value),
        "by_sector": value_map_to_pct(by_sector, total_value),
        "by_theme": value_map_to_pct(by_theme, total_value),
        "by_strategy": value_map_to_pct(by_strategy, total_value),
        "by_region": value_map_to_pct(by_region, total_value),
        "by_asset_type": value_map_to_pct(by_asset_type, total_value),
        "positions": sorted(positions, key=lambda item: item["market_value"], reverse=True),
        "unknown_classification_pct": ...,
        "low_confidence_classification_pct": ...,
    }
```

观察项不是风险标签。它只陈述事实，不判断好坏。

```python
def generate_portfolio_observations(summary: dict[str, Any]) -> list[dict[str, Any]]:
    observations = []
    largest_position = first(summary["positions"])
    if largest_position:
        observations.append({
            "id": f"observation:largest_position:{largest_position['code']}",
            "type": "largest_position",
            "label": "最大单只持仓",
            "evidence": [
                f"{largest_position['name']} weight={largest_position['weight']:.2f}%"
            ],
        })
    for sector, pct in top_items(summary["by_sector"], limit=3):
        observations.append({
            "id": f"observation:top_sector:{sector}",
            "type": "top_sector",
            "label": "主要行业暴露",
            "evidence": [f"{sector}={pct:.2f}%"],
        })
    return observations
```

第一版观察项：

- `largest_position`：最大单只持仓。
- `top_asset_class`：主要资产大类暴露。
- `top_sector`：Top 3 行业暴露。
- `top_theme`：Top 3 主题暴露。
- `unknown_classification`：未知分类占比事实。
- `low_confidence_classification`：低置信度分类占比事实。
- `active_vs_passive`：主动/被动占比事实。
- `on_exchange_vs_off_exchange`：场内/场外占比事实。

不要输出：

- 不要说“超过策略上限”。
- 不要说“必须减仓/必须加仓”。
- 不要生成 `RiskFlag`。
- 不要生成 `CandidateAction`。
- 不要让 LLM 决定阈值。

后续等用户确认策略后，再新增 `Task 6C：策略检查`：

- `single_position_overweight`
- `asset_class_drift`
- `unknown_classification_too_high`
- `cash_below_minimum`
- `sector_concentration`
- `theme_concentration`

测试：

- 构造总市值 10000 的组合。
- 验证资产大类占比。
- 验证行业、主题、策略、区域占比。
- 验证单只持仓权重。
- 验证 unknown 分类占比。
- 验证低置信度分类占比。
- 验证观察项只描述事实，不包含 `severity`、`limit`、`exceeded` 这类策略判断字段。

---

### Task 7：生成候选动作

**涉及文件：**

- 修改：`/Users/liyanran/github/stock/stock_assistant.py`
- 新增：`/Users/liyanran/github/stock/tests/test_candidate_actions.py`

当前决策：

- 用户暂时没有设定策略规则，所以 Task 7 暂缓。
- 没有经过用户确认的 `[policy]`，系统不能生成“减仓到多少”“加仓到多少”这类动作。
- 第一版只允许基于 Task 6 的观察项展示事实，不自动转成候选动作。
- 等用户看过一段时间组合画像后，可以新增“策略草案生成”步骤，由系统根据历史画像建议一份 `[policy]`，用户确认后再启用 Task 7。

候选动作必须来自规则和证据，不直接来自 LLM。

动作类型：

- `watch`：观察。
- `rebalance`：再平衡。
- `reduce`：降低暴露。
- `buy`：仅表示“可考虑分批增加”，不能是确定性买入。
- `hold`：持有。
- `classify_required`：分类不足，需要确认。

候选动作必须包含定量目标。LLM 不负责做仓位算术，Python 规则引擎先算好：

- 当前仓位。
- 目标仓位。
- 当前市值。
- 目标市值。
- 需要增加/减少的金额。
- 触发的约束。

例如：单只持仓策略上限是 20%，当前 24%，总资产 100000。候选动作不应该只写“建议再平衡”，而应该写：

```text
current_weight_pct = 24.0
target_weight_pct = 18.0
current_value = 24000
target_value = 18000
delta_value = -6000
constraint = "max_single_position_pct=20, target_buffer_pct=2"
```

建议目标不要刚好打到上限。比如上限 20%，目标可以设成 18%，留 2% buffer，避免一天波动后再次触发。

实现：

```python
def generate_candidate_actions(
    analysis_results: list[dict[str, Any]],
    summary: dict[str, Any],
    risk_flags: list[RiskFlag],
    config: dict[str, Any],
) -> list[CandidateAction]:
    actions: list[CandidateAction] = []
    for item in analysis_results:
        holding = item.get("holding")
        if not isinstance(holding, Holding):
            continue
        weight = item.get("weight")
        current_value = item.get("current_value")
        max_single = float(policy_value(config, "max_single_position_pct", 20))
        target_buffer = float(policy_value(config, "rebalance_target_buffer_pct", 2))
        if weight is not None and weight > max_single:
            target_weight = max(max_single - target_buffer, 0)
            total_value = summary.get("total_value") or 0
            target_value = total_value * target_weight / 100 if total_value else None
            delta_value = target_value - current_value if target_value is not None and current_value is not None else None
            actions.append(CandidateAction(
                id=f"rebalance-{holding.code}-single-overweight",
                type="rebalance",
                target_code=holding.code,
                target_name=holding.name,
                priority="medium",
                reason="单只持仓超过策略上限",
                evidence=(f"weight={weight:.2f}% > limit {max_single:.2f}%",),
                reason_code="single_overweight",
                current_weight_pct=weight,
                target_weight_pct=target_weight,
                current_value=current_value,
                target_value=target_value,
                delta_value=delta_value,
                delta_weight_pct=target_weight - weight,
                constraint=f"max_single_position_pct={max_single}, rebalance_target_buffer_pct={target_buffer}",
            ))
        if item.get("action") == "减仓/暂停加仓":
            actions.append(CandidateAction(
                id=f"watch-{holding.code}-weak-trend",
                type="watch",
                target_code=holding.code,
                target_name=holding.name,
                priority="medium",
                reason="技术面风险信号较多",
                evidence=(str(item.get("reason", "")),),
                reason_code="weak_trend",
                current_weight_pct=weight,
                current_value=current_value,
            ))
    for flag in risk_flags:
        if flag.code == "sector_concentration":
            actions.append(CandidateAction(
                id=f"rebalance-{flag.code}",
                type="rebalance",
                target_code="",
                target_name=flag.label,
                priority=flag.severity,
                reason=flag.label,
                evidence=flag.evidence,
                reason_code=flag.code,
            ))
    return dedupe_candidate_actions(actions)
```

测试：

- 单标的超限生成 `rebalance`。
- 行业超限生成 `rebalance`。
- 技术面弱生成 `watch`。
- 未知分类超限生成 `classify_required`。
- 重复动作会去重。
- 单标的超限动作包含 `target_weight_pct`、`target_value`、`delta_value`。
- `target_weight_pct` 默认低于上限，保留 buffer。

---

### Task 8：Agent 记忆和历史快照

**涉及文件：**

- 修改：`/Users/liyanran/github/stock/stock_assistant.py`
- 修改：`/Users/liyanran/github/stock/.gitignore`
- 新增：`/Users/liyanran/github/stock/data/state/.gitkeep`
- 新增：`/Users/liyanran/github/stock/tests/test_agent_memory.py`

快照路径：

```text
data/state/snapshots/YYYYMMDD-HHMMSS-agent-snapshot.json
```

快照结构：

```json
{
  "schema_version": 1,
  "generated_at": "2026-05-11T15:30:00+08:00",
  "source": "tzzb_api",
  "portfolio": {},
  "classifications": {},
  "technical_results": [],
  "risk_flags": [],
  "candidate_actions": [],
  "agent_report": {},
  "model": "inclusionAI/Ling-2.6-1T"
}
```

新增函数：

- `agent_snapshot_dir(config) -> Path`
- `save_agent_snapshot(snapshot, config) -> Path`
- `list_agent_snapshots(config) -> list[Path]`
- `load_latest_agent_snapshot(config) -> dict[str, Any] | None`
- `diff_agent_snapshots(previous, current) -> dict[str, Any]`

Diff 引擎必须使用 stable id，不能直接比较整个对象。原因是 `evidence` 里的权重、市值、收益率每天都会波动，如果直接比较对象，会每天产生大量假变动。

RiskFlag stable id：

```python
def risk_flag_id(kind: str, scope: str, target: str) -> str:
    return f"risk:{kind}:{scope}:{target}"
```

示例：

```text
risk:single_position_overweight:holding:512880
risk:sector_concentration:sector:financials
risk:unknown_classification_too_high:portfolio:all
```

CandidateAction stable id：

```python
def candidate_action_id(action_type: str, reason_code: str, target: str) -> str:
    return f"action:{action_type}:{reason_code}:{target}"
```

示例：

```text
action:rebalance:single_overweight:512880
action:watch:weak_trend:510300
action:classify_required:unknown_classification:161725
```

Diff 规则：

```text
当前有、历史没有：新增风险/新增动作
历史有、当前没有：风险解除/动作解除
当前和历史都有，stable id 相同：风险延续/动作延续
stable id 相同但 severity 改变：等级变化
stable id 相同但 evidence 数字改变：只更新详情，不算新增
```

历史对比要输出：

- 新增标的。
- 消失标的。
- 总资产变化。
- 持仓盈亏变化。
- 风险标签新增/消失。
- 候选动作新增/消失。
- 分类从 unknown 变成已知。

测试：

- 保存快照后能读取。
- 多个快照能找到最新。
- 两份快照能比较新增标的和风险变化。
- 同一个 stable id 但 evidence 数字变化时，识别为延续而不是新增。
- 同一个 stable id 的 severity 从 `medium` 到 `high` 时，识别为等级变化。

---

### Task 9：LLM 输出结构化校验

**涉及文件：**

- 修改：`/Users/liyanran/github/stock/stock_assistant.py`
- 新增：`/Users/liyanran/github/stock/tests/test_agent_llm.py`

现在 LLM 输出靠 prompt 约束，后端直接 `json.loads`，这不够稳。必须增加 schema 校验和回退。

优先级策略：

1. 上策：如果当前 LLM provider 支持 JSON Mode、JSON Schema 或 Tool Calling，优先从源头强制结构化输出。
2. 中策：如果模型输出了残缺 JSON，触发一次 repair retry，把错误信息和原始输出发回模型，让它只修 JSON。
3. 下策：repair 仍失败，才回退到规则引擎结果。

不要因为一个括号错误就直接丢弃整段 LLM 分析。LLM 调用耗时长、也可能有成本，fallback 只能作为最后兜底。

新增配置：

```toml
[llm]
structured_output = "auto" # auto/json_object/json_schema/tool_call/none
repair_attempts = 1
```

新增函数：

```python
def strip_json_markdown(text: str) -> str:
    clean = text.strip()
    if clean.startswith("```json"):
        return clean[7:-3].strip()
    if clean.startswith("```"):
        return clean[3:-3].strip()
    return clean
```

```python
def parse_agent_report(
    text: str,
    candidate_actions: list[CandidateAction],
    config: dict[str, Any],
) -> dict[str, Any]:
    try:
        payload = json.loads(strip_json_markdown(text))
    except json.JSONDecodeError as exc:
        repaired = repair_agent_report_json(text, str(exc), candidate_actions, config)
        if repaired is None:
            return fallback_agent_report(candidate_actions, "LLM 输出不是合法 JSON，且修复失败")
        payload = repaired
    return validate_agent_report(payload, candidate_actions, config)
```

结构化输出调用策略：

```python
def llm_structured_kwargs(config: dict[str, Any]) -> dict[str, Any]:
    mode = str(config.get("llm", {}).get("structured_output", "auto")).lower()
    if mode in {"json_object", "auto"}:
        return {"response_format": {"type": "json_object"}}
    if mode == "none":
        return {}
    return {}
```

注意：ModelScope、EasyRouter 或其他 OpenAI-compatible provider 不一定完整支持 `response_format`、JSON Schema 或 Tool Calling。实现时必须支持 provider capability 开关，不能假设所有 provider 都兼容。

repair retry：

```python
def repair_agent_report_json(
    raw_text: str,
    error: str,
    candidate_actions: list[CandidateAction],
    config: dict[str, Any],
) -> dict[str, Any] | None:
    attempts = int(config.get("llm", {}).get("repair_attempts", 1))
    if attempts <= 0:
        return None
    prompt = (
        "下面是一段需要修复的 JSON。只输出修复后的合法 JSON，不要解释。\n"
        f"解析错误: {error}\n"
        f"原始内容:\n{raw_text}"
    )
    try:
        fixed = call_llm([...], config)
        return json.loads(strip_json_markdown(fixed))
    except Exception:
        return None
```

回退报告：

```python
def fallback_agent_report(candidate_actions: list[CandidateAction], reason: str) -> dict[str, Any]:
    return {
        "summary": {
            "health_score": None,
            "status": "fallback",
            "brief": f"AI 诊断失败，已返回规则引擎结果: {reason}",
        },
        "risk_tags": [],
        "action_items": [candidate_action_to_dict(item) for item in candidate_actions],
        "watch_conditions": [],
        "questions": [],
        "evidence": [],
    }
```

LLM prompt 必须明确：

- 只能使用输入 JSON 中的数据。
- 不得编造新闻、宏观、政策、估值。
- 不得新增不在候选动作中的直接操作建议。
- 可以提出问题。
- 可以解释风险。
- 必须输出 JSON。

测试：

- 合法 JSON 能解析。
- markdown 包裹 JSON 能解析。
- provider 支持 `response_format` 时会带上 JSON mode 参数。
- 非法 JSON 会触发一次 repair retry。
- repair 成功时使用修复结果。
- repair 失败时才回退。
- 缺字段能补默认值。
- LLM 新增未知 candidate id 会被拒绝或降级。

---

### Task 10：Agent 编排器

**涉及文件：**

- 修改：`/Users/liyanran/github/stock/stock_assistant.py`
- 修改：`/Users/liyanran/github/stock/api.py`
- 新增：`/Users/liyanran/github/stock/tests/test_agent_orchestrator.py`

实现原则：

- 第一版 orchestrator 直接调用 Python tools。
- 不做 MCP server。
- 不让 LLM 自主决定调用哪些工具。
- LLM 只在 `generate_agent_report_with_llm()` 阶段消费已经准备好的上下文。
- 所有工具调用顺序由后端明确控制，方便测试、回放和定位问题。
- 第一版 API 必须提供 SSE 流式接口，不能只做同步阻塞接口。
- 每完成一个阶段就向前端推送状态，避免 20 到 60 秒长任务导致浏览器或代理超时。

新增主函数：

```python
def run_agent_analysis(
    config: dict[str, Any],
    holdings: list[Holding] | None = None,
    model_override: str | None = None,
    save_snapshot: bool = True,
) -> dict[str, Any]:
    if holdings is None:
        if str(config.get("ledger", {}).get("mode", "")).strip().lower() != "tzzb_api":
            raise RuntimeError("agent 模式当前需要 ledger.mode=tzzb_api 或传入 holdings")
        holdings, source, ledger_summary = fetch_tzzb_holdings(config)
    else:
        source = None
        ledger_summary = {}

    classifications = {h.code: classify_holding(h, config) for h in holdings}
    technical_results = analyze_holdings(holdings, config)
    summary = summarize_portfolio(holdings, classifications, config)
    observations = generate_portfolio_observations(summary)
    risk_flags = []
    candidate_actions = []
    if policy_enabled(config):
        risk_flags = evaluate_policy(summary, config)
        candidate_actions = generate_candidate_actions(technical_results, summary, risk_flags, config)
    previous = load_latest_agent_snapshot(config)

    report = generate_agent_report_with_llm(
        holdings=holdings,
        classifications=classifications,
        technical_results=technical_results,
        summary=summary,
        observations=observations,
        risk_flags=risk_flags,
        candidate_actions=candidate_actions,
        previous_snapshot=previous,
        config=config,
        model_override=model_override,
    )

    snapshot = build_agent_snapshot(
        source=source,
        ledger_summary=ledger_summary,
        holdings=holdings,
        classifications=classifications,
        technical_results=technical_results,
        summary=summary,
        observations=observations,
        risk_flags=risk_flags,
        candidate_actions=candidate_actions,
        agent_report=report,
        model=model_override or config.get("llm", {}).get("model"),
    )

    if save_snapshot and config_bool(config.get("agent", {}).get("save_snapshots", True)):
        save_agent_snapshot(snapshot, config)
    return snapshot
```

新增 API：

- `POST /api/agent/run/stream`：主接口，SSE 流式返回状态和最终 snapshot。
- `POST /api/agent/run`：可选兼容接口，只用于测试或返回 run_id，不作为前端主路径。
- `GET /api/agent/latest`
- `GET /api/agent/history`

SSE 事件：

```json
{"step":"sync_holdings","status":"正在同步投资账本"}
{"step":"classify","status":"正在分类持仓"}
{"step":"search_metadata","status":"正在补全未知标的信息"}
{"step":"market_data","status":"正在拉取 510300 行情"}
{"step":"technical_analysis","status":"正在计算技术指标"}
{"step":"portfolio_profile","status":"正在生成组合画像"}
{"step":"portfolio_observations","status":"正在生成组合观察项"}
{"step":"policy_eval","status":"正在检查策略约束"}
{"step":"candidate_actions","status":"正在生成候选动作"}
{"step":"llm_report","status":"正在请求 LLM 生成解释"}
{"step":"save_snapshot","status":"正在保存快照"}
{"step":"done","snapshot":{}}
```

其中 `policy_eval` 和 `candidate_actions` 只有在用户确认并启用策略规则后才出现。当前无策略阶段，主流程停在组合画像、观察项和 LLM 解释。

后端实现方式：

```python
@app.post("/api/agent/run/stream")
async def run_agent_stream(req: AgentRunRequest):
    async def event_generator():
        async for event in run_agent_analysis_events(config, model_override=req.model):
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
    return StreamingResponse(event_generator(), media_type="text/event-stream")
```

核心编排函数拆成事件版：

```python
async def run_agent_analysis_events(
    config: dict[str, Any],
    model_override: str | None = None,
):
    yield {"step": "sync_holdings", "status": "正在同步投资账本"}
    holdings, source, ledger_summary = fetch_tzzb_holdings(config)

    yield {"step": "classify", "status": "正在分类持仓"}
    classifications = {}
    for holding in holdings:
        classifications[holding.code] = classify_holding(holding, config)
        yield {"step": "classify", "status": f"已分类 {holding.name} ({holding.code})"}

    yield {"step": "market_data", "status": "正在拉取行情"}
    technical_results = []
    # 每个标的拉取后都 yield 一条状态。

    ...

    yield {"step": "done", "snapshot": snapshot}
```

注意：第一版可以用同步函数包在 async generator 里执行，但每个耗时步骤之间必须 yield 状态。后续如果要进一步优化，再考虑并发拉行情、后台任务和 run_id。

测试：

- mock `fetch_tzzb_holdings`
- mock `fetch_bars`
- mock `call_llm`
- 验证完整 snapshot 包含分类、组合画像、观察项、LLM 报告。
- 策略未启用时，`risk_flags=[]`、`candidate_actions=[]`。
- 策略启用后，验证 snapshot 包含风险和候选动作。
- 验证 `/api/agent/run/stream` 至少输出 `sync_holdings`、`market_data`、`llm_report`、`done` 事件。

---

### Task 11：告警引擎和 CLI `check`

**涉及文件：**

- 修改：`/Users/liyanran/github/stock/stock_assistant.py`
- 修改：`/Users/liyanran/github/stock/README.md`
- 新增：`/Users/liyanran/github/stock/tests/test_alerts.py`

告警类型：

- `cookie_expired`
- `market_data_stale`
- `single_position_overweight`
- `sector_overweight`
- `unknown_classification_too_high`
- `technical_breakdown`
- `large_daily_loss`

实现：

```python
def evaluate_alerts(agent_snapshot: dict[str, Any], config: dict[str, Any]) -> list[dict[str, Any]]:
    alerts = []
    for flag in agent_snapshot.get("risk_flags", []):
        if flag.get("severity") in {"high", "critical"}:
            alerts.append({
                "code": flag.get("code"),
                "severity": flag.get("severity"),
                "message": flag.get("label"),
                "evidence": flag.get("evidence", []),
            })
    return alerts
```

新增 CLI：

```bash
uv run python stock_assistant.py --config config.toml check --json
```

`build_parser()` 增加：

```python
check_parser = subparsers.add_parser("check", help="运行 agent 检查并输出告警")
check_parser.add_argument("--json", action="store_true", help="输出 JSON")
```

`main()` 增加：

```python
if command == "check":
    snapshot = run_agent_analysis(config)
    alerts = evaluate_alerts(snapshot, config)
    if args.json:
        print(json.dumps(alerts, ensure_ascii=False, indent=2))
    else:
        print(alerts_markdown(alerts))
    return 0
```

第一版只输出本地报告，不接飞书/邮件。

---

### Task 12：前端升级为 Agent 驾驶舱

**涉及文件：**

- 新增：`/Users/liyanran/github/stock/frontend/src/types.ts`
- 修改：`/Users/liyanran/github/stock/frontend/src/App.tsx`
- 后续可新增：`frontend/src/components/RiskPanel.tsx`
- 后续可新增：`frontend/src/components/ActionItems.tsx`
- 后续可新增：`frontend/src/components/HistoryDiff.tsx`

第一步先加类型：

```ts
export type Holding = {
  code: string;
  name: string;
  quantity: number | null;
  cost_price: number | null;
  market_value: number | null;
  profit_pct: number | null;
  hold_profit: number | null;
  day_profit: number | null;
  asset_type: 'etf' | 'fund' | string;
  weight?: number | null;
  action?: string;
  reason?: string;
};

export type AgentActionItem = {
  id: string;
  type: 'buy' | 'reduce' | 'hold' | 'rebalance' | 'watch' | string;
  target_code?: string;
  target_name?: string;
  priority?: 'low' | 'medium' | 'high' | 'critical' | string;
  reason: string;
  evidence?: string[];
  reason_code?: string;
  current_weight_pct?: number | null;
  target_weight_pct?: number | null;
  current_value?: number | null;
  target_value?: number | null;
  delta_value?: number | null;
  delta_weight_pct?: number | null;
  constraint?: string;
  requires_user_confirmation?: boolean;
};

export type AgentReport = {
  summary?: {
    health_score?: number | null;
    status?: string;
    brief?: string;
  };
  risk_tags?: Array<{
    code?: string;
    label: string;
    severity?: string;
    evidence?: string[];
  }>;
  action_items?: AgentActionItem[];
  watch_conditions?: Array<{
    target_code?: string;
    condition: string;
    meaning?: string;
  }>;
  questions?: Array<{
    id?: string;
    question: string;
  }>;
};
```

前端展示区域：

- 今日总览。
- 风险标签。
- 候选动作。
- 观察条件。
- 待确认分类。
- 历史变化。
- LLM 失败时的规则引擎回退结果。

构建验证：

```bash
cd /Users/liyanran/github/stock/frontend
npm run build
```

---

### Task 13：前端接入 Agent SSE 进度

**涉及文件：**

- 修改：`/Users/liyanran/github/stock/api.py`
- 修改：`/Users/liyanran/github/stock/frontend/src/App.tsx`

后端 SSE 已在 Task 10 中作为第一版主接口实现。本任务只负责前端消费和展示。

前端需要展示的进度步骤：

1. `sync_holdings`
2. `classify`
3. `search_metadata`
4. `market_data`
5. `technical_analysis`
6. `policy_eval`
7. `candidate_actions`
8. `llm_report`
9. `save_snapshot`
10. `done`

新增接口：

```text
POST /api/agent/run/stream
```

事件示例：

```json
{"step":"sync_holdings","status":"正在同步投资账本"}
{"step":"classify","status":"正在分类持仓"}
{"step":"search_metadata","status":"正在补全未知标的信息"}
{"step":"done","snapshot":{}}
```

前端复用现在的日志面板。

前端处理要求：

- 每收到一个 `status` 就追加到日志。
- 收到 `technical_results` 或中间数据时可以先缓存。
- 收到 `snapshot` 时渲染最终 agent 结果。
- 收到 `error` 时保留已有中间结果，并允许“从已完成步骤重试”。
- 请求进行中时禁用重复点击。
- 连接中断时给出明确错误，不要一直转圈。

---

### Task 14：README 文档

**涉及文件：**

- 修改：`/Users/liyanran/github/stock/README.md`
- 修改：`/Users/liyanran/github/stock/config.example.toml`

README 增加：

- Agent 模式是什么。
- Agent 不做什么。
- LLM 用到哪些数据。
- 搜索工具用到哪些数据。
- 如何手动配置分类。
- 如何查看待确认分类。
- 如何运行每日检查。
- 如何理解风险标签。

必须明确写：

```markdown
本工具只生成持仓分析、观察条件和可审阅的候选动作，不自动下单，不构成投资建议或收益承诺。
```

---

## 9. 搜索工具路线图

### 9.1 搜索引擎选择

推荐选择：

```text
默认：manual_json + Tavily
备选：Brave Search API
后续研究：Exa / SerpApi / SearXNG
不推荐作为主方案：Google Custom Search JSON API / Bing Web Search API
```

对比：

| 方案 | 个人项目适合度 | 优点 | 风险 |
| --- | --- | --- | --- |
| `manual_json` | 高 | 不联网、可测试、零成本 | 需要人工准备数据 |
| Tavily | 高 | 面向 AI/RAG，支持搜索/抽取，个人免费额度通常够用 | 免费额度和价格可能变化 |
| Brave Search API | 高 | 独立搜索索引，通用搜索质量较稳，有免费 credit | 中文金融资料覆盖需实测 |
| Exa | 中 | 语义搜索强，适合研究 | 中文基金/ETF 资料不一定最优 |
| SerpApi | 中 | 可接多种搜索结果 | 免费额度较小 |
| SearXNG 自建 | 中 | 隐私好，可控 | 维护成本高，结果稳定性依赖后端 |
| Google Custom Search JSON API | 低 | Google 结果质量高 | 新项目接入不友好，迁移风险 |
| Bing Web Search API | 低 | 曾经稳定 | 已退休，不作为新项目方案 |

参考链接：

- Tavily API credits: `https://docs.tavily.com/documentation/api-credits`
- Tavily Search API: `https://docs.tavily.com/documentation/api-reference/endpoint/search`
- Brave Search API pricing: `https://api-dashboard.search.brave.com/documentation/pricing`
- Exa pricing: `https://exa.ai/pricing`
- SerpApi pricing: `https://serpapi.com/pricing`
- Google Custom Search JSON API: `https://developers.google.com/custom-search/v1/overview`
- Bing Search API retirement: `https://learn.microsoft.com/en-us/lifecycle/announcements/bing-search-api-retirement`

这些价格和免费额度是易变信息。每次真正接入前都要重新查官方文档。

### 9.2 阶段 A：无外部搜索

- `config.toml` 手动分类。
- 本地 JSON 搜索结果。
- 分类缓存。
- 本地启发式 fallback 仅在无配置、无缓存、无搜索结果时使用。

优点：

- 不联网。
- 不泄露持仓。
- 测试简单。
- 开发快。

缺点：

- 未知标的需要手工补。
- 分类完整度有限。

### 9.3 阶段 B：外部搜索 API

配置：

```toml
[search]
enabled = true
provider = "tavily"
api_key_env = "TAVILY_API_KEY"
max_results = 5
timeout_seconds = 20
search_depth = "basic"
include_raw_content = false
```

实现：

- `HttpSearchProvider`
- `TavilySearchProvider`
- `BraveSearchProvider`
- 超时处理。
- 限流处理。
- 错误降级。
- 结果缓存。
- 来源白名单。

优先搜索来源：

1. 基金公司官方产品页。
2. 交易所页面。
3. 指数公司页面。
4. 主流金融数据页面。
5. 普通网页摘要只做兜底。

### 9.4 轻量 RAG 设计

这里确实涉及 RAG，但第一版不要上复杂向量数据库。这个项目当前需要的是“结构化元数据补全”，不是开放域问答。

第一版 RAG 流程：

```text
搜索 query
  -> 候选 URL
  -> 来源分级
  -> 正文/摘要抽取
  -> 字段抽取
  -> 交叉验证
  -> 置信度评分
  -> 写入 metadata cache
  -> 分类/风控只读取 metadata cache
```

不要做：

- 不要把搜索结果直接塞给 LLM 后让它自由判断。
- 不要把所有网页都塞进向量库。
- 不要从搜索结果读取最新价格、净值、涨跌。
- 不要用论坛/博客结论直接影响风控。

什么时候再上向量库：

- 需要长期保存基金公告、招募说明书、季报全文。
- 需要问“某基金过去几个季度行业配置怎么变”这类跨文档问题。
- 需要对大量 PDF/HTML 做语义检索。

在那之前，用 JSON cache 或 SQLite FTS 足够。

### 9.5 来源分级和可信度

来源分级：

```text
Tier 1：最高可信
- 基金公司官网产品页
- 上交所 / 深交所页面
- 中证指数、国证指数、标普、MSCI 等指数公司官网
- 基金招募说明书、基金合同、定期报告

Tier 2：可用但要交叉验证
- 天天基金、东方财富、同花顺、雪球等金融数据页
- 主流财经媒体资料页

Tier 3：只用于发现线索
- 普通博客
- 论坛帖子
- SEO 聚合站
- 未知来源网页
```

字段可信度：

| 字段 | 可直接使用来源 | 是否需要交叉验证 |
| --- | --- | --- |
| 基金名称 | TZZB / 基金公司 / 交易所 | 一般不用 |
| 基金代码 | TZZB / 官方页面 | 一般不用 |
| 跟踪指数 | 基金公司 / 招募说明书 / 指数公司 | 最好两个来源 |
| 资产类别 | 官方跟踪指数 / 基金类型 / 已确认缓存；本地启发式只作弱证据 | 需要 |
| 行业分类 | 跟踪指数 + 名称 + 官方说明 | 需要 |
| 基金公司 | 官方产品页 / 招募说明书 | 一般不用 |
| 费率 | 官方产品页 / 招募说明书 | 需要时再用 |
| 最新净值/价格 | 不从搜索取 | 必须走行情/净值数据源 |
| 新闻/观点 | 默认不用于分类 | 只能做备注 |

置信度评分：

```python
score = 0.0

if has_tier1_source:
    score += 0.50
if field_found_in_structured_table:
    score += 0.20
if same_field_confirmed_by_second_source:
    score += 0.20
if extracted_value_matches_name_rule:
    score += 0.10
if only_tier3_sources:
    score -= 0.30
if conflicts_with_existing_cache:
    score -= 0.40
```

使用规则：

```text
score >= 0.85：自动使用
0.65 <= score < 0.85：展示并标记待确认
score < 0.65：不用于风控，只提示人工确认
```

### 9.6 阶段 C：搜索 + LLM 证据归类

LLM 输入：

```json
{
  "instrument": {
    "code": "512880",
    "name": "证券ETF"
  },
  "search_results": [
    {
      "title": "...",
      "url": "...",
      "snippet": "..."
    }
  ],
  "taxonomy": {
    "asset_classes": ["broad_index", "sector_equity"],
    "sectors": ["financials", "semiconductor"]
  }
}
```

LLM 输出：

```json
{
  "asset_class": "sector_equity",
  "sector": "financials",
  "theme": "brokerage",
  "confidence": 0.86,
  "evidence_urls": ["..."],
  "needs_user_review": false
}
```

校验规则：

- 不允许输出 taxonomy 外的值。
- 搜索来源的分类必须有 evidence URL。
- 低于置信度阈值必须标记 `needs_user_review=true`。
- 用户确认后写入缓存。
- LLM 不能补充搜索证据中不存在的字段。
- 若多个 Tier 1/2 来源冲突，输出 `conflict=true` 并进入人工确认。

---

## 10. 隐私和安全边界

LLM 可以接收：

- 标的代码和名称。
- 四舍五入后的市值。
- 仓位百分比。
- 技术指标。
- 风险标签。
- 候选动作。

LLM 不应该接收：

- Cookie。
- API key。
- 原始 TZZB 响应。
- 账号 ID。
- 不必要的账户名称。

搜索工具可以接收：

- 标的代码。
- 标的名称。
- “跟踪指数”“行业”“基金公司”等公开查询词。

搜索工具不应该接收：

- 你的持仓金额。
- 你的盈亏。
- 你的仓位比例。
- 你的个人风险偏好。

MCP 未来如果实现，也必须遵守这些边界：

- 只暴露低敏、只读工具。
- 不暴露 Cookie、API key、`.env`、`.tzzb-curl`。
- 不暴露原始投资账本响应。
- 不暴露精确账户标识。
- 默认不允许写入本地状态；写操作必须单独确认。

Agent 不允许：

- 自动交易。
- 直接生成下单文件。
- 把 LLM 输出说成确定性投资建议。
- 隐藏建议来源。

每个建议都要能回答：

- 这个建议来自规则、搜索证据还是 LLM 推断？
- 依据是什么？
- 哪些数据不确定？
- 是否需要用户确认？

---

## 11. 需要你确认的问题

这些问题会影响实现细节：

1. 搜索工具：你想先用 `manual_json`，还是已经有想用的搜索 API？ 搜索API
2. 搜索隐私：是否允许把标的代码和名称发给外部搜索服务？  允许
3. LLM 隐私：是否允许把市值、盈亏和仓位发给 LLM？还是只发百分比和模糊区间？ 可以发给LLM
4. 分类粒度：第一版用粗分类，还是一开始就细到申万/中信行业？ 可以分细一点，先申万
5. 低置信度分类：是否必须在 UI 里人工确认后才用于风控？ 是的
6. Agent 自主性：只生成建议，还是也维护一份“目标配置草案”？ 维护一份“目标配置草案”
7. 告警渠道：先本地报告，还是直接接飞书/邮件？ 先本地报告
8. 运行频率：只在 A 股收盘后运行，还是开盘前/盘中也运行？ 开盘后运行
9. 场外基金：第一版只纳入配置和风险，还是也要分析净值和持仓主题？ 
10. 数据保留：历史快照和搜索缓存保留多久？
11. 前端形态：首页继续是驾驶舱，还是改成 agent 任务台？

默认建议：

- 先用 `manual_json` 搜索 provider。
- 搜索只发送代码和名称。
- LLM 只发送百分比、四舍五入金额和技术指标。
- 第一版用粗分类。
- 低置信度分类需要确认。
- 不做自动交易。
- 告警先输出本地报告。
- 每天收盘后运行。
- 场外基金第一版只纳入配置和风险。
- 历史快照保留 180 天。
- 已确认：后端确定性编排，第一版直接写 Python tools，不做 MCP；LLM 只做报告和解释。
- 前端保持驾驶舱，增加 agent 审阅面板。

---

## 12. 验证矩阵

后端测试：

```bash
cd /Users/liyanran/github/stock
uv run python -m unittest discover -s tests
```

前端构建：

```bash
cd /Users/liyanran/github/stock/frontend
npm run build
```

CLI 样例：

```bash
cd /Users/liyanran/github/stock
uv run python stock_assistant.py analyze tests/fixtures/holdings.csv
```

Agent 检查：

```bash
cd /Users/liyanran/github/stock
uv run python stock_assistant.py --config config.toml check --json
```

API smoke：

```bash
cd /Users/liyanran/github/stock
uv run uvicorn api:app --port 8000
```

然后：

```bash
curl -s http://127.0.0.1:8000/api/agent/latest
```

预期：

- 没有快照时返回明确 empty state。
- 有快照时返回最新 agent snapshot。

---

## 13. 推荐里程碑

### Milestone 1：修稳当前应用（基础版已完成）

范围：

- Task 0

退出条件：

- 后端测试通过。
- 前端 build 通过。
- CLI 样例能生成报告。

### Milestone 2：Agent 数据基础

范围：

- Task 1
- Task 2
- Task 4
- Task 5 基础版

退出条件：

- 持仓序列化稳定。
- 策略配置存在。
- 分类支持手动配置、缓存、搜索接口和本地 LLM 证据归类。
- 搜索接口存在但可以禁用。
- 搜索结果内容会写入 JSON 缓存。
- 搜索支持 freshness，避免默认搜到过旧结果。

当前状态：

- 基础版已完成。
- Tavily / Brave / 本地 Gemma 服务还需要真实端到端联调。

### Milestone 3：组合画像

范围：

- Task 6

退出条件：

- 能输出组合摘要。
- 能输出资产大类、行业、主题、策略、区域、场内/场外、主动/被动占比。
- 能输出最大单只持仓和 Top 暴露观察项。
- 不依赖用户预设规则。
- 不输出风险标签。
- 不输出候选动作。

### Milestone 4：记忆和流式编排

范围：

- Task 8
- Task 10

退出条件：

- 能保存和比较历史快照。
- `/api/agent/run/stream` 流式返回进度和完整快照。

### Milestone 5：LLM Agent 报告

范围：

- Task 9

退出条件：

- LLM 输出经过 schema 校验。
- LLM JSON 失败会先 repair retry，仍失败才回退到规则引擎。
- LLM 基于组合画像、观察项、分类证据和技术指标解释，不发明策略阈值。

### Milestone 6：策略规则和候选动作（用户确认 policy 后再做）

范围：

- Task 6C
- Task 7

退出条件：

- 用户确认 `[policy]`。
- 能输出风险标签。
- 能输出带定量目标的候选动作。
- LLM 只解释动作，不计算动作。

### Milestone 7：产品体验

范围：

- Task 11
- Task 12
- Task 13
- Task 14

退出条件：

- 告警可用。
- 前端展示 agent 报告、风险、动作、问题、历史变化。
- README 解释 agent 模式、搜索、隐私、分类和风险边界。

---

## 14. 第一版明确不做

不要在第一版做：

- 自动交易。
- 下单接口。
- 生成券商导入文件。
- MCP server。
- 让 LLM 自主调用敏感工具。
- 多用户系统。
- 云端部署。
- 复杂数据库迁移。
- 新闻情绪引擎。
- 高频盘中监控。
- 策略回测。

先把本地个人 agent 做稳，再考虑这些。
