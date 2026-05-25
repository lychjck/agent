# MCP 独立服务重写实施计划

> 作者：Kiro · 2026-05-22
> 上游文档：
> - `docs/plans/2026-05-22-tzzb-api-exploration.md`（tzzb 物理实证）
> - `docs/plans/2026-05-22-mcp-capability-design.md`（MCP 定位与能力蓝图）
> - `docs/architecture.md`（当前主库架构地图）

## 0. 一句话目标

把 MCP 工具服务从主库里**剥离出来**，单独建一个 `mcp/` 目录，**完全独立可部署**：
`cp -r mcp/ ~/anywhere && cd ~/anywhere && uv sync && uv run python -m stock_mcp` 就能跑。
不依赖、不导入主库任何代码；同时借这次拆分**重写一遍**，扔掉历史包袱（agent 的 reflection / coverage gate / 报告 schema 校验全部不进 MCP）。

## 1. 决策记录（已对齐）

| # | 决策 | 备注 |
|---|---|---|
| D1 | MCP 不复制主库代码，**重写实现** | 借机重构，不背老逻辑 |
| D2 | 主库 Agent 后续改造为走 MCP（删除自己的工具实现） | 工具实现唯一来源 = MCP |
| D3 | ETF 穿透**主轨用天天基金**（已物理可用） | tzzb `heavy_held_stock` 物理 404，不做 |
| D4 | tzzb 4 个 HTTP 400 接口（流水/盈亏）**占位**返回 `capability_unavailable` | 等签名 `s` 复刻方案稳定后再单独立项 |
| D5 | tzzb 2 个已实证接口（`asset_trend` / `bs_point`）**做** | 但承认它们当前返回空槽，仍标记 ok=true |
| D6 | 复刻 tzzb 签名 `s` **不进本计划**，单独 P7 评估 | 不能让进度被一个不确定项卡死 |
| D7 | 不引入 z-score 的"是否异常"判定逻辑到 MCP | MCP 只暴露原始 σ/μ/z 数值，是否 abnormal 由调用方决定 |
| D8 | **Skills 不进 MCP** | Skills 是 Agent 的资源读取，不是业务工具；主库 Agent 未来走 MCP 后在主库进程内直接读文件；MCP 红线 = 访问外部世界的业务能力 |
| D9 | 设计 `get_current_account_bundle` 聚合工具并重塑缓存边界 | 彻底废除跨工具共享 context 缓存的错误假设，确立单工具调用语义，通过聚合接口一次性吐出持仓、组合画像和分类，彻底避免往返刷频打 tzzb ；缓存槽仅用于单工具内部多标的并发查询时的局部缓存。 |
| D10 | 天天基金穿透引入重试与 1 小时本地缓存 | 在 `providers/eastmoney_fund.py` 中引入带指数退避与随机抖动的重试机制，并在 `persistence/` 中设计 1 小时步长本地缓存，规避高频并发查询导致 IP 被封禁的风险。 |
| D11 | 快照生命周期管理（显式存盘、单日覆盖、滚动清理） | 只有在诊断成功结束时由调用方通过 `save_snapshot` 显式写入，MCP 内部实现按天覆盖去重与 180 天滚动清理，杜绝每次工具调用自动生成快照。 |

## 2. 目录与依赖边界

```
stock/                                    ← 当前 git 仓库根
├── mcp/                                  ← ★ 独立的、可剥离的子项目
│   ├── pyproject.toml                    项目自己的依赖；与根 pyproject.toml 互不引用
│   ├── uv.lock                           独立锁定
│   ├── README.md                         独立 README，部署一份即可看懂
│   ├── .env.example                      独立环境变量
│   ├── config.example.toml               MCP 自己的配置（与主库 config.toml 不共享）
│   ├── Dockerfile                        独立镜像
│   │
│   ├── src/stock_mcp/                    包名 stock_mcp（避免和主库 stock_assistant 冲突）
│   │   ├── __init__.py
│   │   ├── __main__.py                   uv run python -m stock_mcp
│   │   ├── cli.py                        argparse + main()
│   │   │
│   │   ├── server/                       JSON-RPC 协议层
│   │   │   ├── jsonrpc.py                请求/响应/错误码
│   │   │   ├── handler.py                method 路由
│   │   │   ├── stdio.py                  stdio transport
│   │   │   └── http.py                   FastAPI app + bearer auth
│   │   │
│   │   ├── core/                         无状态基础设施（不依赖任何业务）
│   │   │   ├── config.py                 TOML 加载
│   │   │   ├── logging.py                日志
│   │   │   ├── http.py                   urllib 薄封装：GET/POST + JSON
│   │   │   └── errors.py                 统一异常类型
│   │   │
│   │   ├── domain/                       领域模型（dataclass，不依赖外部库）
│   │   │   ├── holding.py                Holding / Bar
│   │   │   ├── classification.py         InstrumentClassification
│   │   │   └── snapshot.py               历史快照 schema
│   │   │
│   │   ├── providers/                    外部数据源客户端（HTTP 出去的地方）
│   │   │   ├── tzzb.py                   account/stock_position/fund_position/asset_trend/bs_point
│   │   │   ├── eastmoney_fund.py         天天基金：ETF 重仓 + 基金信息
│   │   │   ├── kline.py                  K 线（sina + eastmoney 备轨）
│   │   │   └── search/                   外部搜索：opencli / web fetch
│   │   │       ├── opencli.py
│   │   │       ├── web_read.py
│   │   │       └── web_fetch.py
│   │   │
│   │   ├── analytics/                    纯数理函数（无 IO，无副作用）
│   │   │   ├── technical.py              MA/RSI/回撤
│   │   │   ├── stats.py                  σ/μ/z-score
│   │   │   └── portfolio.py              组合画像
│   │   │
│   │   ├── context.py                    ★ ToolContext：工具调用的唯一入参
│   │   ├── registry.py                   ★ ToolSpec + 工具注册
│   │   │
│   │   ├── tools/                        ★ 唯一对外暴露的能力面
│   │   │   ├── holdings.py               get_current_holdings / get_portfolio_profile / get_classification / get_current_account_bundle
│   │   │   ├── technical.py              get_holding_technical（含 z-score）
│   │   │   ├── etf.py                    get_etf_constituents（主轨 eastmoney）
│   │   │   ├── ledger.py                 get_asset_trend / get_bs_point / get_trade_history(占位) / get_pnl(占位)
│   │   │   ├── snapshots.py              load_snapshot_summary / compare_snapshots
│   │   │   └── web.py                    web_search / web_read / web_fetch / opencli_command
│   │   │
│   │   └── persistence/                  状态文件读写
│   │       ├── snapshots.py              历史快照目录
│   │       └── classification_cache.py   分类证据缓存
│   │
│   └── tests/                            独立测试，不 import 主库
│       ├── conftest.py
│       ├── test_protocol.py              JSON-RPC 协议
│       ├── test_tools_holdings.py
│       ├── test_tools_technical.py
│       ├── test_tools_etf.py
│       ├── test_tools_ledger.py
│       └── test_providers_tzzb.py        用 fixture，不打真网
│
├── src/                                  ← 主库（保持原状，本计划不动它）
└── docs/, frontend/, ...
```

### 依赖边界（强约束）

- **mcp/ 内部**：
  - `tools/` 只依赖 `analytics/ + providers/ + persistence/ + domain/ + context.py + core/`
  - `providers/` 只依赖 `core/`，不依赖 `domain/`（用 dict 传数据）
  - `analytics/` 是纯函数，不依赖任何 mcp 内部模块
  - `server/` 只依赖 `registry.py + tools/ + context.py + core/`
- **mcp/ 与主库**：
  - 任何 `from stock_assistant.* import` = 编译失败级别错误
  - CI 加一条静态检查（grep 防呆）

## 3. 核心抽象设计

### 3.1 ToolContext 与缓存槽边界

工具调用的唯一入参。**彻底废除“tools/call 跨多工具共享 context 缓存”的错误假设**。因为标准 MCP 协议的每个工具调用（`tools/call`）都是完全隔离的，其背后的 JSON-RPC 请求生命周期各自独立，对应的 `ToolContext` 实例在单次调用结束时即被销毁，跨工具完全无状态。

因此，`ToolContext` 的缓存槽设计边界必须明确：**仅用于单个工具内多标的并发查询时的局部缓存**（如 `get_holding_technical` 在多标的并发拉取行情时的 K 线局部缓存，避免在同一个工具内部进行重复的物理网络请求）。

```python
# mcp/src/stock_mcp/context.py（接口设计，不是实现）
class ToolContext:
    config: dict                          # 加载好的 TOML 配置
    request_id: str                       # 单次 JSON-RPC 调用的 id，用于日志关联

    # 局部缓存槽：仅用于单个工具执行期间，处理多标的并发拉取时的局部缓存
    # 例如：在 get_holding_technical 内部多标的并发查询 K 线
    def get_cached_kline(self, code: str) -> list[Bar] | None: ...
    def set_cached_kline(self, code: str, kline: list[Bar]) -> None: ...

    # 直通的 provider 句柄（不缓存，每次新连接）
    def tzzb(self) -> TzzbClient: ...
    def eastmoney_fund(self) -> EastmoneyFundClient: ...
    def kline(self) -> KlineClient: ...
```

设计要点：
- **不是 `AgentWorkspace` 的复刻**。Agent 层的复杂状态机（如 reflection 状态、跨轮对话记忆）由 Agent 自身持有，MCP 坚持无状态原则。
- **生命周期彻底隔离**：每次 MCP `tools/call` 都会实例化一个全新且独立的 `ToolContext`，并在工具执行完毕后立即销毁。跨工具间无法通过 `ToolContext` 共享任何数据。
- **缓存边界局部化**：禁止在 `ToolContext` 中设计任何跨工具的持久化缓存或全局单例缓存。如果单次工具内部存在多标的拉取（例如批量获取行情），可以通过局部缓存槽在同一请求内复用网络响应，工具退出后即行失效。

### 3.2 Registry / ToolSpec

```python
# mcp/src/stock_mcp/registry.py（接口设计）
@dataclass
class ToolSpec:
    name: str                             # 内部名，例如 "get_current_holdings"
    description: str
    args_schema: type[BaseModel]          # pydantic 模型
    handler: Callable[[BaseModel, ToolContext], dict]
    permission: str = "portfolio:read"
    read_only: bool = True                # MCP 只暴露只读
    external_name: str = ""               # 对外暴露的名字，默认 f"stock_{name}"

def build_registry(config: dict) -> dict[str, ToolSpec]: ...
def tool_schemas(registry: dict[str, ToolSpec]) -> list[dict]: ...
```

注册表按配置开关动态构建：
- `[search] enabled = false` → 不注册 web 工具
- `[mcp] expose_tzzb_legacy = true` → 注册占位的流水/盈亏工具

### 3.3 协议层

**JSON-RPC 2.0**，方法集合：
- `initialize` / `ping` / `shutdown`
- `tools/list`：返回所有 ToolSpec 的 JSON Schema
- `tools/call`：参数 `{name, arguments, call_id?}`，返回 `{content, structuredContent, isError}`

错误码沿用现有 mcp_server.py 那套（-32700 / -32600 / -32601 / -32602 / -32603），不发明新的。

工具失败 vs 协议失败的区分：
- **协议失败**：JSON-RPC 层面错误（method 不存在、参数不是对象），返回标准 error
- **工具失败**：业务层面失败（持仓为空、网络超时），返回 `{isError: true, structuredContent: {ok: false, error_type: "...", message: "..."}}`，HTTP 仍然 200

## 4. 工具清单与设计

> 表格列说明：**P** 列是优先级（P0=骨架阶段就要、P1=主体阶段、P2=新能力）。

### 4.1 持仓 / 画像 / 分类 / 聚合账户包（P1）

为了根本解决跨调用时由于 MCP 协议天然隔离导致先调持仓、后调画像与分类不得不高频往返调用打 tzzb 刷频同花顺的问题，特新增 **`get_current_account_bundle`** 聚合工具。该工具单次调用内仅打一次 tzzb，一次性吐出持仓（holdings）、组合画像（profile）、分类（classifications）的全部 Facts 聚合，在内存中完成全套事实的生产。

| 工具名 | 输入 | 输出关键字段 | 数据来源 |
|---|---|---|---|
| `get_current_holdings` | `fields: list[str]` 白名单 | `count, holdings[], summary` | `providers/tzzb.fetch_holdings` |
| `get_portfolio_profile` | `include: list[str]` | `portfolio{by_asset_class,by_sector,...}, observations[]` | `analytics/portfolio` |
| `get_classification` | `codes: list[str]` | `classifications{code: {...}}` | `persistence/classification_cache` + `[classifications.<code>]` config |
| `get_current_account_bundle` | `fields: list[str]` 白名单, `include: list[str]` 白名单 | `holdings[]`, `portfolio_profile`, `classifications{}` | 一键聚合打 tzzb：拉取持仓后，内存中直接跑画像分析与分类匹配，打包输出 |

**聚合账户包设计规范 (`get_current_account_bundle`)**：
- **物理流程**：
  1. 调用 `providers/tzzb.fetch_holdings` 从 tzzb 物理拉取一次大盘持仓列表；
  2. 将获取到的 `list[Holding]` 直接作为输入传给 `analytics/portfolio`，在内存中计算出组合画像数据（`portfolio_profile`）；
  3. 收集持仓中所有的标的代码，批量从 `persistence/classification_cache` 及 TOML 配置中匹配分类，获取分类数据（`classifications`）；
  4. 将这三部分事实组装成统一的数据结构包返回。
- **输入 Schema**：
  - `fields`: `list[str]` (可选，用于过滤持仓中返回的字段白名单)
  - `include`: `list[str]` (可选，画像中要计算/包含的指标白名单)
- **输出 Schema**：
  ```json
  {
    "holdings": [
      { "code": "510300", "name": "300ETF", "amount": 10000, ... }
    ],
    "portfolio_profile": {
      "by_asset_class": { "Equity": 0.8, "FixedIncome": 0.2 },
      "observations": [ ... ]
    },
    "classifications": {
      "510300": { "code": "510300", "primary_class": "Equity", "source": "config" }
    }
  }
  ```

**分类工具的简化**：现有主库的 `classify_holding` 会**触发 LLM 分类**，那是个隐式副作用（耗钱、耗时、缓存写入）。MCP 这一版**只做只读**：
- 命中 config 配置 → 返回
- 命中缓存 → 返回  
- 都没命中 → 返回 `{source: "unknown", confidence: 0}`，不触发任何 LLM/搜索

如果调用方想"主动分类"，未来再开 `refresh_classification` 工具，那是**写工具**，单独走授权。

### 4.2 技术指标 + 统计学（P2）

| 工具名 | 输入 | 输出 |
|---|---|---|
| `get_holding_technical` | `codes, lookback_days(default=120)` | `technical{code: {ma20, ma60, ma120, rsi14, drawdown_120d, vol_120, mu_120, sigma_120, z_score_today, daily_pct_change}}` |

设计要点：
- **不计算 `is_abnormal_deviation`**。MCP 只暴露原始 `z_score_today`，"是否异常"由调用方根据自己的阈值判断。
  - 主库 Agent 传 2.0 阈值 → 自己判断
  - LangGraph 节点传 1.5 阈值 → 自己判断
  - MCP 不做策略
- 复用 `analytics/technical.py + analytics/stats.py` 纯函数，工具层只做组装。
- 行情来源走 `providers/kline.py`，主轨 sina，备轨 eastmoney（保留现有兜底逻辑，重写一遍但不创新）。

### 4.3 ETF 穿透（P2）

| 工具名 | 输入 | 输出 |
|---|---|---|
| `get_etf_constituents` | `codes: list[str]` | `results{code: {constituents: [{code, name, weight}], count, source}}` |

**主轨**：天天基金 `https://fundf10.eastmoney.com/FundArchivesDatas.aspx?type=jjcc&code={code}&topline=10`
- 现有 `integrations/eastmoney.py:fetch_fund_holdings` 已经物理可用，重写一遍但保留逻辑
- 输出加上 `source: "eastmoney_fund"` 字段，便于上游审计
- **风控与缓存优化**：
  - 引入带指数退避和随机抖动的重试机制，规避网络波动和爬虫风控。
  - 在 `persistence/` 下设计轻量级本地文件缓存（1 小时失效），以基金代码为 Key 缓存穿透结果事实，大幅降低同时间段的重复网络请求。

**备轨**：暂无。tzzb `heavy_held_stock` 物理 404，明确不做。
- 文档第十节实证说明：HTTP 404 是**路由不通**，不是"等签名就能用"
- 如果未来用户切换为移动端登录态、tzzb 后台对 PC 端开放，再补

错误处理：
- 单个 code 失败不影响其他 code，分别在 `results[code]` 里给出错误信息
- 整体 ok 标记：只要至少一个 code 成功就 ok=true

### 4.4 账本扩展接口（P2）

| 工具名 | 输入 | 输出 / 状态 |
|---|---|---|
| `get_asset_trend` | `(无)` | `{total_asset[], month_profit[], year_profit[], ...}` 物理可用，承认空槽 |
| `get_bs_point` | `code` | `{tradelist[], fundcode}` 物理可用，承认空 list |
| `get_trade_history` | `account_id, start_date, end_date` | **占位**：`{ok: false, error_type: "capability_unavailable", message: "tzzb 流水接口需要动态签名 s（来自 JS 混淆），当前未实现，不要重试"}` |
| `get_daily_pnl` | `code, date` | 占位（同上） |
| `get_monthly_pnl` | `month` | 占位（同上） |
| `get_yearly_pnl` | `code, year` | 占位（同上） |

**为什么需要这些占位工具**：让 LLM 能在 `tools/list` 里看到"项目知道有这个能力，但当前不可用"，比"这个能力完全不存在"更利于决策。占位工具明确说**不要重试**，避免 LLM 进入无效循环。

可以通过 `[mcp] expose_legacy_tzzb_placeholders = false` 一键关掉这 4 个占位。

### 4.5 历史快照（P1）

| 工具名 | 输入 | 输出 |
|---|---|---|
| `load_snapshot_summary` | `which: "latest"` | 摘要：generated_at / source / model / portfolio_top / risk_count |
| `compare_snapshots` | `current_facts: dict, previous: "latest"` | diff：portfolio_changes / risk_changes / classification_changes |
| `save_snapshot` | `snapshot_data: dict` | `{ok: true, filepath: str}` 保存快照并执行去重/清理 |

**关键变化与设计规范**：
- **无状态设计**：MCP 这里不持有 Workspace 状态。`compare_snapshots` 的 `current` 参数替换为 `current_facts`。调用方（Agent）自行传递当前持仓事实，由 MCP 从磁盘读取历史快照并完成比对。
- **快照存盘触发 (`save_snapshot`)**：普通的查询类工具（持仓、画像、技术指标等）**绝不自动存盘**。只有在一次完整的诊断分析流成功结束、输出报告后，调用方才显式调用 `save_snapshot` 存盘。
- **单日去重与合并 (Deduplication)**：同一天内多次运行诊断并保存时，MCP 默认以天为单位（如命名为 `YYYY-MM-DD-snapshot.json`）更新/覆盖当天的文件，确保一天内只保留一个最终状态，防止碎片文件泛滥。
- **滚动清理 (Retention)**：每次写入快照时，MCP 根据配置（如 `history_days = 180`）自动扫描并清除 180 天前的历史快照，维持磁盘空间的健康。

### 4.6 Web 工具（P1）

| 工具名 | 输入 | 输出 |
|---|---|---|
| `web_search` | `query OR targets[], max_results, max_chars` | `results[{title,url,snippet}], auto_read[]?` |
| `web_read` | `url, max_chars` | `content (markdown), content_quality` |
| `web_fetch` | `url, max_chars` | `content, content_type` |
| `opencli_command` | `site, command, positionals[], options{}` | `result, count` |

重写时砍掉的复杂度：
- 现有 `agent_tools.py:handle_web_search` 有 `target_search_queries` / `split_multi_holding_query` 这套"持仓代码自动拆分查询"的逻辑 — **不要进 MCP**。MCP 接受调用方已经拆好的 query/targets，拆分逻辑是 Agent 的事。
- `auto_read_top_result` 选项保留（实用），但作为 args 传入而不是从 config 读。

### 4.7 Skills（不做）

**Skills 不进 MCP**。理由：
- Skills 是给"有自主决策权的实体"读的（LLM Agent / LangGraph 节点），用来改变它的下一步决策
- MCP 工具是输入参数固定、输出 schema 固定的函数，没有"按流程办事"这一说
- doc2 自己写的 MCP 定位是"无状态、高可靠、确定性的数据供给与物理执行层"——skill 读取既不是数据供给也不是物理执行
- 主库 Agent 未来走 MCP 后，Agent 进程仍然在主库进程里，直接读文件即可，没必要让 Agent 通过 MCP 多走一跳来读自己进程能直接读到的文件

**主库需要做的事**（不在本计划范围）：
- 主库 Agent 改造为走 MCP 时，保留 `core/skills.py` 与 `list_skills/read_skill` 工具的本地版本
- 这些工具直接在 Agent 进程内执行，不走 MCP 协议
- 未来如果出现"远程 Agent 通过 HTTPS MCP 接进来读用户安装的 skill"这种场景，再单独设计 `agent_resources/*` 工具组，名字反映用途，不复用 skills 这个误导性名词

## 5. 配置文件设计

`mcp/config.example.toml`（与主库 `config.toml` **完全独立**）：

```toml
[mcp]
expose_legacy_tzzb_placeholders = true   # 是否暴露 4 个占位工具
log_level = "INFO"

[server]
# 默认 stdio；HTTP 模式启动时通过 CLI 参数覆盖
default_transport = "stdio"
http_host = "127.0.0.1"
http_port = 8766
http_path = "/mcp"
# token 通过 STOCK_MCP_TOKEN 环境变量传，不写在配置里
auth_token_env = "STOCK_MCP_TOKEN"

[ledger.tzzb]
# 与主库 [ledger] 不同的命名，避免共用同一份 config 时混淆
mode = "tzzb_api"
curl_file = ".tzzb-curl"
cookie_file = ""
api_timeout_seconds = 30

[market]
provider = "sina"             # sina | eastmoney
history_days = 260
timeout_seconds = 15

[paths]
# MCP 部署后这些路径相对自己的工作目录
snapshots_dir = "./data/snapshots"
classification_cache_dir = "./data/research"

[search]
# 沿用现有 search 配置，整段挪过来
enabled = true
provider = "opencli"
timeout_seconds = 20
max_results = 5
auto_read_top_result = true
# providers.* / source_tiers / allowed_commands 同主库
```

**与主库 config 的关系**：
- 字段命名相似但**没有任何共享**
- 主库不读这个文件，MCP 也不读主库的
- 部署 MCP 时给一份独立的 config.toml + 独立的 .tzzb-curl

## 6. 部署形态

### 6.1 独立 pyproject.toml

```toml
# mcp/pyproject.toml（最小依赖）
[project]
name = "stock-mcp"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.110",
    "uvicorn>=0.30",
    "pydantic>=2.0",
    # 不依赖 openai —— MCP 不调 LLM
    # 不依赖 langgraph
    # 不依赖 stock_assistant 任何东西
]

[project.scripts]
stock-mcp = "stock_mcp.cli:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/stock_mcp"]
```

### 6.2 启动方式

```bash
# 本地开发（在 mcp/ 目录下）
cd mcp/
uv sync
uv run python -m stock_mcp --config config.toml

# stdio (Qclaw/OpenClaw 本地接入)
uv run python -m stock_mcp --config config.toml --transport stdio

# HTTP (远程接入)
export STOCK_MCP_TOKEN="..."
uv run python -m stock_mcp --config config.toml \
  --transport http --host 127.0.0.1 --port 8766
```

### 6.3 Dockerfile

```dockerfile
# mcp/Dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN pip install uv && uv sync --frozen --no-dev
COPY src/ ./src/
ENV PYTHONPATH=/app/src
EXPOSE 8766
CMD ["python", "-m", "stock_mcp", "--config", "/app/config.toml", \
     "--transport", "http", "--host", "0.0.0.0", "--port", "8766"]
```

镜像体积目标：< 200MB（python:3.12-slim ≈ 130MB + 依赖 ≈ 50MB）。

### 6.4 剥离验证

CI 加一项：
```bash
# 把 mcp/ 拷贝到 /tmp，断网装依赖跑测试
cp -r mcp /tmp/mcp-isolated
cd /tmp/mcp-isolated
uv sync --offline    # 用本地 wheel cache
uv run pytest        # 测试通过 = 真的独立了
```

## 7. 主库改造方向（只描述不实施）

> 这部分**不在本计划范围内**，但写在这里让你知道终局长什么样，避免 P0~P7 做出方向冲突的决定。

### 7.1 终局形态

主库 `agents/` 里：
- `agent_tools.py 1026 行` → 删掉，改成 `agent_mcp_client.py`（一个 stdio MCP client）
- `agent_workspace.py` → 大幅瘦身。它现在是"持仓 + 技术 + 分类 + 历史"的缓存中心，将来变成"reflection 状态 + 外部证据" 的**对话状态**容器，事实数据走 MCP 实时取。
- `agent_executor.py` → 改成"调 MCP client 而不是本地 handler"
- `agent_loop.py` 不变

### 7.2 迁移单独立项

预计 4-5 个提交：
1. 在主库引入 MCP client 抽象（child process + stdio）
2. 用一个工具试点（先迁 `get_current_holdings`），双跑对比
3. 全量迁移
4. 删除主库 `agent_tools.py` 工具实现
5. 简化 `agent_workspace.py`

**前置条件**：本计划 P0~P5 完成，MCP 已经稳定跑了一周以上没问题。

## 8. 实施步骤

每步独立提交、独立可回滚。每步必须满足"验证标准"才进下一步。

### P0：骨架（约 1 天）

**做什么**：
- 建 `mcp/` 目录结构
- `pyproject.toml`、`uv.lock`、`README.md`、`config.example.toml`、`Dockerfile`
- `core/`、`server/`（stdio + http）、`registry.py`、`context.py` 的最小可运行版
- 一个 demo 工具：`mcp_ping`（不依赖任何业务，返回 `{ok: true, message: "pong"}`）

**验证标准**：
- `cd mcp && uv run python -m stock_mcp --config config.example.toml --transport stdio` 启动成功
- 用 `echo '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | uv run ...` 能拿到 `mcp_ping` 工具
- 调一次 `mcp_ping`，返回 ok
- HTTP 模式启动 + curl 验证 `/health` 返回 200
- **拷贝验证**：`cp -r mcp /tmp/x && cd /tmp/x && uv sync && uv run python -m stock_mcp` 不报 import 错误

**提交**：`feat(mcp): 建立独立 mcp 服务骨架`

### P1：持仓 / 画像 / 分类 / 快照 / Web 工具（约 2-3 天）

**做什么**：
- `providers/tzzb.py`：实现 `account_list / stock_position / fund_position`，输出 `list[Holding]`
- `providers/kline.py`：sina + eastmoney 备轨
- `providers/eastmoney_fund.py`：天天基金 ETF 重仓（仅 fetch_fund_holdings，不要现有的 LLM 分类逻辑）
- `providers/search/*`：opencli + web_read + web_fetch
- `analytics/portfolio.py`：组合画像（重写 portfolio.summarize_portfolio，但去掉 unknown/low_confidence 的累计逻辑能简化的就简化）
- `persistence/snapshots.py`：JSON 文件读写
- `tools/holdings.py / tools/snapshots.py / tools/web.py`

**验证标准**：
- 每个工具都有单元测试，覆盖：成功路径 + 至少一个失败路径
- `tools/list` 返回 8 个工具（不算 P2 的 4 个）
- 端到端：用真实 `.tzzb-curl` 调一次 `get_current_holdings + get_portfolio_profile`，与主库 `/api/profile` 输出对比一致（允许字段命名差异）
- 测试时 `mcp/tests` 不能 import `stock_assistant.*`

**提交**（拆 3 个）：
1. `feat(mcp): providers + analytics 基础层`
2. `feat(mcp): 持仓 / 画像 / 分类 / 快照工具`
3. `feat(mcp): web search / read / fetch / opencli 工具`

### P2：技术指标（含 z-score）+ ETF 穿透（约 2 天）

**做什么**：
- `analytics/technical.py`：MA / RSI / 回撤（重写）
- `analytics/stats.py`：σ / μ / z_score（新加）
- `tools/technical.py`：组装上述，输出新增 `mu_120 / sigma_120 / z_score_today`
- `tools/etf.py`：调 `providers/eastmoney_fund`

**验证标准**：
- 单元测试覆盖：
  - 给定一段固定 closes，z_score 计算结果与 numpy/scipy 用同一公式手算一致
  - lookback < 20 天 → 返回 `null` 而不是抛异常
- 端到端：调 `get_holding_technical(["510300"])`，`z_score_today` 字段存在且为 float
- `get_etf_constituents(["510300"])` 返回前十大成分股

**提交**：
1. `feat(mcp): technical + stats analytics`
2. `feat(mcp): ETF 穿透工具（主轨天天基金）`

### P3：tzzb 已实证扩展接口（约 1 天）

**做什么**：
- `providers/tzzb.py` 加：
  - `fetch_asset_trend()` → 调 `/caishen_fund/pc/asset/v1/asset_trend`
  - `fetch_bs_point(code)` → 调 `/caishen_fund/fund_quota/v1/bs_point`
- `tools/ledger.py`：暴露 `get_asset_trend / get_bs_point`

**验证标准**：
- 用真实 cookie 调一次，返回 `error_code: "0"`，承认数据槽是空 list（这是 tzzb 当前现实）
- 失败时（cookie 过期）返回 `{ok: false, error_type: "auth_expired"}`，不要把 stack trace 透传给 LLM

**提交**：`feat(mcp): tzzb asset_trend + bs_point 工具`

### P4：tzzb 占位工具（约 0.5 天）

**做什么**：
- `tools/ledger.py` 加 4 个占位：
  - `get_trade_history`
  - `get_daily_pnl`
  - `get_monthly_pnl`
  - `get_yearly_pnl`
- 全部直接返回 `{ok: false, error_type: "capability_unavailable", message: "..."}`
- 由 `[mcp] expose_legacy_tzzb_placeholders` 控制是否注册

**验证标准**：
- 单元测试：调用任一占位工具，message 必须包含"不要重试"字样
- `tools/list` 在配置开启时能看到这 4 个工具

**提交**：`feat(mcp): tzzb 流水/盈亏占位工具`

### P5：文档 + 部署（约 1 天）

**做什么**：
- `mcp/README.md`：怎么部署、配置、接 Qclaw/OpenClaw
- `mcp/Dockerfile` 验证：`docker build && docker run` 能跑
- 主库根 `README.md` 加一行：MCP 已剥离到 `mcp/`，部署见其内部 README
- 更新 `docs/architecture.md`：把 mcp/ 加到架构图里
- 主库 `src/stock_assistant/mcp_server.py` **暂不删**（避免影响现有 launchd），但加 deprecated 注释，说明"新部署请使用 mcp/"

**验证标准**：
- 一个**完全没看过这项目**的人按 README 能在 30 分钟内启动 MCP
- Docker 镜像 < 200MB
- 拷贝隔离测试通过：`cp -r mcp /tmp && cd /tmp/mcp && uv sync --offline && uv run python -m stock_mcp` 跑通

**提交**：
1. `docs(mcp): 独立部署 README + Dockerfile 验证`
2. `chore(mcp_server): 标注主库内置 mcp_server 为 deprecated`

### P6：CI 强约束（约 0.5 天）

**做什么**：
- 加一个 GitHub Action（或本地脚本）：
  ```bash
  # mcp/scripts/check_isolation.sh
  if grep -r "from stock_assistant" mcp/src; then
    echo "FAIL: mcp/ imports from stock_assistant"
    exit 1
  fi
  ```
- 加到 pre-commit / CI

**验证标准**：
- 故意在 mcp/ 加一行 `from stock_assistant.core.utils import log`，CI 必须红
- 删掉那行后 CI 必须绿

**提交**：`ci(mcp): 强制依赖隔离检查`

### P7（可选，本计划之外）：复刻 tzzb 签名 `s`

**为什么单独立项**：成本极不确定（可能 1 天，可能 1 周，看 JS 混淆程度）。
方案候选（按成本递增）：
1. 上 playwright，用真实浏览器拿签名后请求 → 慢但必中
2. 抓 JS 文件，复刻签名算法 → 快但脆弱
3. 用 mitmproxy 抓 tzzb PC 客户端的请求 → 中等

**触发条件**：
- 用户对历史交易流水 / 月度盈亏的需求量大到值得做
- 当前占位工具的 `capability_unavailable` 反馈让 LLM 体验不可接受

P7 完成后回头改：把 `tools/ledger.py` 里 4 个占位的实现替换成真调用，配置开关从默认 false 改成默认 true。

## 9. 时间估算

| 阶段 | 天 | 累计 |
|---|---|---|
| P0 骨架 | 1 | 1 |
| P1 主体工具 | 3-4 | 4-5 |
| P2 技术 + ETF | 2 | 6-7 |
| P3 tzzb 实证接口 | 1 | 7-8 |
| P4 占位工具 | 0.5 | 7.5-8.5 |
| P5 文档部署 | 1 | 8.5-9.5 |
| P6 CI 隔离 | 0.5 | 9-10 |
| **合计** | **~10 天** | |

P7 不计入，单独评估。

## 10. 风险与对策

| 风险 | 影响 | 对策 |
|---|---|---|
| 重写过程中漏掉主库的某个边界 case | MCP 工具行为与主库不一致 | P1 端到端测试用真实 .tzzb-curl 跑一遍，输出与主库 `/api/profile` 对比 |
| ToolContext 跨工具缓存失效导致刷频打 tzzb | 被同花顺封禁或限流，影响核心功能使用 | 废除跨工具缓存假设，确立单工具调用语义；对于需要持仓、画像、分类的全事实分析场景，一律使用新设计的 `get_current_account_bundle` 聚合接口，实现单次物理请求获取全套事实，彻底解决多次往返的性能与风控隐患。 |
| Docker 镜像太大 | 部署慢 | python:3.12-slim 起步；不装 numpy/pandas，统计学函数用纯 Python |
| 主库后续走 MCP 时发现 ToolContext 不够用 | 迁移卡住 | P1 完成后用主库 Agent 试调一次（双跑模式），暴露不够用的地方 |

## 11. 不在本计划范围

明确标记，避免 scope creep：

- ❌ 删除主库 `agent_tools.py / agent_executor.py / agent_workspace.py`（迁移期间共存）
- ❌ 修改主库 Agent 走 MCP（单独立项）
- ❌ 复刻 tzzb 签名 `s`（P7，单独评估）
- ❌ ETF 穿透的 tzzb 备轨实现（heavy_held_stock 物理 404，等 PC 端开放再说）
- ❌ z-score 阈值判定 / 异常标记（MCP 只给原始数值，策略由调用方决定）
- ❌ "主动分类"工具（写工具，单独授权）
- ❌ 前端改造
- ❌ 主库 cli/cli.py 改造

## 12. 检查清单（开工前对齐）

开工前请确认：

- [ ] 同意目录结构：`mcp/` 与 `src/` 平级，包名 `stock_mcp`
- [ ] 同意"工具实现唯一来源 = MCP"的终局，主库迁移单独立项
- [ ] 同意 tzzb 4 个 400 接口先做占位，签名复刻不进本计划
- [ ] 同意 ETF 穿透单轨：天天基金，承认 tzzb 404 不做
- [ ] 同意 z-score 不做"是否异常"判定，只暴露原始数值
- [ ] 同意 P5 不删旧 mcp_server.py，只标 deprecated
- [ ] 同意 ~10 天工期估算（不含 P7）
