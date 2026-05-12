# LLM 工具调用 Agent Milestone 8 实施计划

> **给后续执行者:** 按任务逐项实现、逐项验证。不要一次性大重构。第一目标是跑通“LLM 决定工具调用 -> 后端执行工具 -> observation 回传 -> LLM 继续推理 -> 最终报告”的闭环。

**目标:** 在现有持仓分析流水线旁边新增一个真正的 LLM 工具调用 Agent。用户点击“AI 分析”后，LLM 不只是最后生成报告，而是在受控工具集合内自主选择下一步要查什么，后端校验并执行工具，再把 observation 交还给 LLM 继续分析，直到生成最终报告。

**架构:** 保留现有 `/Users/liyanran/github/stock/stock_assistant/agent.py` 作为稳定 pipeline fallback；新增 `llm_tools.py`、`agent_tools.py`、`agent_workspace.py`、`agent_executor.py`、`agent_loop.py`、`agent_trace.py`。新 Agent 复用现有持仓同步、行情、分类、组合画像、历史快照和 LLM 报告校验逻辑，不重写业务算法。

**Tech Stack:** Python 3.12、FastAPI、Pydantic、OpenAI-compatible Chat Completions、SSE、本地 JSON/JSONL trace、React/Vite/TypeScript。

---

## 1. 核心交互流程

用户只点击一次“AI 分析”，前端请求：

```json
{
  "mode": "tool_agent",
  "goal": "分析当前持仓，给出组合风险、每个 ETF 的建议和需要我确认的问题",
  "model": null,
  "cached_results": null
}
```

后端运行流程：

```text
POST /api/agent/run/stream
  -> create run_id
  -> build tool registry
  -> build initial LLM messages
  -> LLM returns tool_call
  -> backend validates tool name and args
  -> backend executes Python handler
  -> backend appends tool observation to messages
  -> LLM decides next tool or final_report
  -> validate final report with existing agent_llm schema
  -> build snapshot
  -> save trace/snapshot/report
  -> SSE done
```

SSE 最小事件：

```json
{"step":"agent_start","run_id":"agent-20260512-153012","status":"开始 AI 工具调用分析"}
{"step":"llm_turn","turn":1,"status":"AI 正在决定下一步"}
{"step":"tool_call","turn":1,"tool":"get_current_holdings","arguments":{"fields":["code","name","weight_pct"]}}
{"step":"tool_observation","turn":1,"tool":"get_current_holdings","ok":true,"summary":"返回 6 只持仓"}
{"step":"llm_turn","turn":2,"status":"AI 正在决定下一步"}
{"step":"final_report","turn":5,"status":"AI 已生成最终报告"}
{"step":"done","run_id":"agent-20260512-153012","snapshot":{}}
```

---

## 2. 关键设计决策

### 2.1 不替换现有流水线

现有 `/Users/liyanran/github/stock/stock_assistant/agent.py` 已经能固定顺序完成持仓同步、行情分析、分类、组合画像、LLM 报告和快照保存。Milestone 8 不应推翻它。

新实现增加一个并行入口：

- `mode="pipeline"`：继续走 `run_agent_analysis_events()`。
- `mode="tool_agent"`：走新的 `run_tool_agent_events()`。

如果工具调用 Agent 失败，允许回退到 pipeline 或返回明确错误；不要让用户只看到无限转圈。

### 2.2 先支持 JSON fallback，再支持原生 tool_calls

OpenAI-compatible provider 不一定完整支持原生 `tools/tool_calls`。因此第一版必须支持两种协议：

1. **JSON fallback protocol**：LLM 输出普通 JSON，由后端解析工具调用。
2. **Native tool_calls protocol**：provider 支持时，使用 Chat Completions `tools` 和 `tool_calls`。

JSON fallback 示例：

```json
{
  "type": "tool_calls",
  "tool_calls": [
    {
      "id": "call_001",
      "name": "get_current_holdings",
      "arguments": {
        "fields": ["code", "name", "weight_pct", "profit_pct"]
      }
    }
  ]
}
```

最终报告示例：

```json
{
  "type": "final_report",
  "report": {
    "summary": {"status": "review", "brief": "组合权益仓位偏高。"},
    "holding_analysis": []
  }
}
```

### 2.3 后端执行工具，LLM 只提出请求

LLM 返回的工具调用不直接执行任何代码。后端必须做：

- 工具名白名单校验。
- 参数 Pydantic 校验。
- 权限校验。
- 超时控制。
- observation 脱敏和截断。
- trace 记录。

非法工具或非法参数返回 error observation，让 LLM 有机会修正，不要直接崩溃整个 run。

---

## 3. 新增文件总览

计划新增：

- `/Users/liyanran/github/stock/stock_assistant/llm_tools.py`
- `/Users/liyanran/github/stock/stock_assistant/agent_tools.py`
- `/Users/liyanran/github/stock/stock_assistant/agent_workspace.py`
- `/Users/liyanran/github/stock/stock_assistant/agent_executor.py`
- `/Users/liyanran/github/stock/stock_assistant/agent_loop.py`
- `/Users/liyanran/github/stock/stock_assistant/agent_trace.py`
- `/Users/liyanran/github/stock/tests/test_llm_tools.py`
- `/Users/liyanran/github/stock/tests/test_agent_tools.py`
- `/Users/liyanran/github/stock/tests/test_agent_executor.py`
- `/Users/liyanran/github/stock/tests/test_agent_loop.py`
- `/Users/liyanran/github/stock/tests/test_agent_tool_api.py`

计划修改：

- `/Users/liyanran/github/stock/stock_assistant/config.py`
- `/Users/liyanran/github/stock/stock_assistant/__init__.py`
- `/Users/liyanran/github/stock/api.py`
- `/Users/liyanran/github/stock/config.example.toml`
- `/Users/liyanran/github/stock/frontend/src/App.tsx`

---

## 4. 数据结构

### 4.1 LLM 工具调用结果

文件：`/Users/liyanran/github/stock/stock_assistant/llm_tools.py`

```python
from typing import Any, Literal
from pydantic import BaseModel, Field


class LlmToolCall(BaseModel):
    id: str = ""
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class LlmToolStep(BaseModel):
    type: Literal["tool_calls", "final_report"]
    tool_calls: list[LlmToolCall] = Field(default_factory=list)
    final_report: dict[str, Any] | None = None
    raw_text: str = ""
```

### 4.2 工具定义

文件：`/Users/liyanran/github/stock/stock_assistant/agent_tools.py`

```python
from typing import Any, Callable
from pydantic import BaseModel


class AgentToolSpec(BaseModel):
    name: str
    description: str
    args_model: type[BaseModel]
    handler: Callable[..., dict[str, Any]]
    permission: str = "portfolio:read"
    read_only: bool = True
```

### 4.3 observation

文件：`/Users/liyanran/github/stock/stock_assistant/agent_executor.py`

```python
from typing import Any
from pydantic import BaseModel, Field


class ToolObservation(BaseModel):
    call_id: str
    tool_name: str
    ok: bool
    result: dict[str, Any] = Field(default_factory=dict)
    error_type: str = ""
    message: str = ""
    summary: str = ""
```

---

## 5. 工具清单

第一批只读工具：

| 工具 | 参数 | 复用逻辑 | 返回 |
| --- | --- | --- | --- |
| `get_current_holdings` | `fields?: list[str]` | `fetch_tzzb_holdings()` 或 cached results | 脱敏持仓列表 |
| `get_portfolio_profile` | `include?: list[str]` | `classify_for_agent()`、`summarize_portfolio()`、`generate_portfolio_observations()` | 组合画像 |
| `get_holding_technical` | `codes: list[str]`、`lookback_days?: int` | `fetch_bars()`、`analyze_one()` | 指定标的技术指标 |
| `get_classification` | `codes: list[str]` | `classification_from_config()`、`load_cached_classification()` | 分类结果和置信度 |
| `load_snapshot_summary` | `which: "latest"` | `load_latest_agent_snapshot()` | 历史快照摘要 |
| `compare_snapshots` | `current: "workspace"`、`previous: "latest"` | `diff_agent_snapshots()` | 事实变化 diff |
| `generate_candidate_actions` | `codes?: list[str]` | 后续策略规则工具 | 候选动作 |

`search_instrument_metadata` 暂不放第一批默认工具。只有当 `[agent] allow_external_search_tools = true` 时才注册，且只允许传公开标的代码和名称。

---

## 6. 任务拆分

### Task 1：增加配置和 API mode 字段

**文件：**

- 修改：`/Users/liyanran/github/stock/stock_assistant/config.py`
- 修改：`/Users/liyanran/github/stock/config.example.toml`
- 修改：`/Users/liyanran/github/stock/api.py`
- 修改：`/Users/liyanran/github/stock/tests/test_agent_tool_api.py`

新增配置：

```python
"agent": {
    "enabled": True,
    "strict_json": True,
    "llm_can_create_new_actions": False,
    "save_snapshots": True,
    "snapshot_dir": str(ROOT / "data" / "state"),
    "tool_agent_enabled": False,
    "tool_agent_default": False,
    "max_tool_turns": 8,
    "max_tool_calls": 16,
    "save_traces": True,
    "trace_dir": str(ROOT / "data" / "state" / "agent_traces"),
    "allow_external_search_tools": False,
}
```

`AgentRunRequest` 增加：

```python
class AgentRunRequest(BaseModel):
    cached_results: list[dict] | None = None
    model: str | None = None
    mode: str = "pipeline"
    goal: str | None = None
```

验收：

- `mode="pipeline"` 行为不变。
- `mode="tool_agent"` 且配置未开启时返回清晰错误或 SSE error。
- 测试覆盖默认 mode。

命令：

```bash
uv run python -m unittest tests.test_agent_tool_api
```

---

### Task 2：实现 LLM tool step 解析层

**文件：**

- 新增：`/Users/liyanran/github/stock/stock_assistant/llm_tools.py`
- 修改：`/Users/liyanran/github/stock/stock_assistant/__init__.py`
- 新增：`/Users/liyanran/github/stock/tests/test_llm_tools.py`

实现函数：

```python
def parse_llm_tool_step(text: str) -> LlmToolStep:
    ...
```

必须支持：

- markdown 包裹 JSON。
- `type="tool_calls"`。
- `type="final_report"`。
- 单个 `tool_call` 兼容为 `tool_calls`。
- 非法 JSON 抛出可读错误。

测试：

- 合法工具调用能解析。
- 合法最终报告能解析。
- markdown JSON 能解析。
- 缺少 `name` 的 tool call 被拒绝。
- 顶层不是对象时被拒绝。

命令：

```bash
uv run python -m unittest tests.test_llm_tools
```

---

### Task 3：实现 AgentWorkspace

**文件：**

- 新增：`/Users/liyanran/github/stock/stock_assistant/agent_workspace.py`
- 新增：`/Users/liyanran/github/stock/tests/test_agent_tools.py`

职责：

- 缓存本次 run 的 holdings。
- 缓存 technical_results。
- 缓存 classifications。
- 缓存 portfolio_summary 和 observations。
- 缓存 previous_snapshot。
- 统一生成最终 snapshot 所需上下文。

核心方法：

```python
class AgentWorkspace:
    def ensure_holdings(self) -> list[Holding]: ...
    def ensure_classifications(self) -> dict[str, InstrumentClassification]: ...
    def ensure_technical(self, codes: list[str] | None = None) -> list[dict[str, Any]]: ...
    def ensure_portfolio_profile(self) -> dict[str, Any]: ...
    def ensure_history_diff(self) -> dict[str, Any]: ...
    def build_llm_context(self) -> dict[str, Any]: ...
    def build_snapshot(self, agent_report: dict[str, Any], model: str) -> dict[str, Any]: ...
```

注意：

- `cached_results` 存在时，优先从 `cached_results` 还原 holdings，避免重复拉行情。
- 场外基金继续复用现有 `fund_analysis_result()` 逻辑。
- `ensure_history_diff()` 必须复用事实 fingerprint，避免 15:00 后重复快照噪音。

测试：

- `cached_results` 不重复调用 `fetch_bars()`。
- 多次调用 `ensure_holdings()` 不重复同步投资账本。
- 多次调用 `ensure_technical(["510300"])` 不重复拉行情。
- build context 不包含 Cookie/API key/source_row。

---

### Task 4：实现工具注册表

**文件：**

- 新增：`/Users/liyanran/github/stock/stock_assistant/agent_tools.py`
- 修改：`/Users/liyanran/github/stock/tests/test_agent_tools.py`

参数模型：

```python
class GetCurrentHoldingsArgs(BaseModel):
    fields: list[str] = Field(default_factory=list)


class GetHoldingTechnicalArgs(BaseModel):
    codes: list[str] = Field(default_factory=list, min_length=1, max_length=20)
    lookback_days: int = Field(default=120, ge=20, le=250)


class GetClassificationArgs(BaseModel):
    codes: list[str] = Field(default_factory=list, min_length=1, max_length=50)


class GetPortfolioProfileArgs(BaseModel):
    include: list[str] = Field(default_factory=list)


class LoadSnapshotSummaryArgs(BaseModel):
    which: Literal["latest"] = "latest"


class CompareSnapshotsArgs(BaseModel):
    current: Literal["workspace"] = "workspace"
    previous: Literal["latest"] = "latest"
```

白名单：

```python
ALLOWED_HOLDING_FIELDS = {
    "code", "name", "asset_type", "market_value", "weight_pct",
    "profit_pct", "hold_profit", "day_profit",
}
```

验收：

- `get_current_holdings` 只返回允许字段。
- `get_holding_technical` 只接受当前持仓中的 code。
- `get_classification` 不触发外部搜索。
- `get_portfolio_profile` 返回组合画像和 observations。
- `compare_snapshots` 对重复事实返回 `duplicate_of_latest=true`。

---

### Task 5：实现工具执行器

**文件：**

- 新增：`/Users/liyanran/github/stock/stock_assistant/agent_executor.py`
- 新增：`/Users/liyanran/github/stock/tests/test_agent_executor.py`

核心函数：

```python
def execute_tool_call(
    call: LlmToolCall,
    registry: dict[str, AgentToolSpec],
    workspace: AgentWorkspace,
    *,
    max_observation_chars: int = 12000,
) -> ToolObservation:
    ...
```

执行顺序：

1. 校验工具名在 registry。
2. 校验工具是 read-only。
3. 用 `args_model.model_validate(call.arguments)` 校验参数。
4. 执行 handler。
5. 对 result 做脱敏和长度截断。
6. 返回 `ToolObservation`。

错误 observation 示例：

```json
{
  "call_id": "call_002",
  "tool_name": "read_cookie",
  "ok": false,
  "error_type": "unknown_tool",
  "message": "工具 read_cookie 不在允许列表中"
}
```

测试：

- 未知工具返回 `unknown_tool`。
- 非法参数返回 `invalid_arguments`。
- handler 抛错返回 `tool_error`。
- observation 不包含 `COOKIE`、`API_KEY`、`source_row`。

---

### Task 6：实现 trace 记录

**文件：**

- 新增：`/Users/liyanran/github/stock/stock_assistant/agent_trace.py`
- 新增：`/Users/liyanran/github/stock/tests/test_agent_loop.py`

路径：

```text
data/state/agent_traces/{run_id}.jsonl
```

事件类型：

```text
agent_start
llm_request
llm_response
tool_call
tool_observation
final_report
error
done
```

实现：

```python
class AgentTraceWriter:
    def write(self, event_type: str, payload: dict[str, Any]) -> None:
        ...
```

要求：

- trace 目录按需创建。
- 每条 JSONL 包含 `run_id`、`created_at`、`type`。
- 写入前调用脱敏函数。
- `save_traces=false` 时使用 no-op writer。

测试：

- 能写 JSONL。
- 关闭 trace 不创建文件。
- trace 不包含 Cookie/API key/source_row。

---

### Task 7：实现最小 Agent Loop

**文件：**

- 新增：`/Users/liyanran/github/stock/stock_assistant/agent_loop.py`
- 修改：`/Users/liyanran/github/stock/stock_assistant/__init__.py`
- 新增：`/Users/liyanran/github/stock/tests/test_agent_loop.py`

最小目标：用 fake LLM 跑通：

```text
turn 1: get_current_holdings
turn 2: final_report
```

核心函数：

```python
async def run_tool_agent_events(
    config: dict[str, Any],
    *,
    goal: str,
    cached_results: list[dict[str, Any]] | None = None,
    model_override: str | None = None,
    save_snapshot: bool = True,
    save_report: bool = True,
) -> AsyncIterator[dict[str, Any]]:
    ...
```

循环伪代码：

```python
for turn in range(max_tool_turns):
    yield {"step": "llm_turn", "turn": turn + 1}
    step = call_llm_tool_step(messages, tool_schemas, config, model_override)

    if step.type == "final_report":
        context = workspace.build_llm_context()
        report = validate_agent_report(step.final_report, ...)
        snapshot = workspace.build_snapshot(report, model)
        yield {"step": "done", "snapshot": snapshot}
        return

    for call in step.tool_calls:
        yield {"step": "tool_call", ...}
        observation = execute_tool_call(call, registry, workspace)
        yield {"step": "tool_observation", ...}
        messages.append(tool_observation_message(call, observation))

yield {"step": "error", "error": "达到 max_tool_turns，Agent 未完成"}
```

测试：

- fake LLM 连续返回 tool call 和 final report，最终生成 snapshot。
- 达到 `max_tool_turns` 返回 error。
- 达到 `max_tool_calls` 返回 error。
- LLM 返回非法 JSON 时返回 error。
- LLM 请求非法工具时，loop 把 error observation 回传给 LLM。

---

### Task 8：扩展工具到完整第一批

**文件：**

- 修改：`/Users/liyanran/github/stock/stock_assistant/agent_tools.py`
- 修改：`/Users/liyanran/github/stock/stock_assistant/agent_workspace.py`
- 修改：`/Users/liyanran/github/stock/tests/test_agent_tools.py`
- 修改：`/Users/liyanran/github/stock/tests/test_agent_loop.py`

新增并测试：

- `get_portfolio_profile`
- `get_holding_technical`
- `get_classification`
- `load_snapshot_summary`
- `compare_snapshots`

`generate_candidate_actions` 可以先注册为受限工具：

```json
{
  "ok": false,
  "error_type": "not_implemented",
  "message": "候选动作工具需要 policy 规则确认后启用"
}
```

不要为了 Milestone 8 顺手补一套策略规则引擎。策略动作是后续任务。

---

### Task 9：接入 API SSE

**文件：**

- 修改：`/Users/liyanran/github/stock/api.py`
- 新增：`/Users/liyanran/github/stock/tests/test_agent_tool_api.py`

`/api/agent/run/stream` 分流：

```python
if req.mode == "tool_agent":
    async for event in run_tool_agent_events(...):
        yield sse_payload(event)
else:
    async for event in run_agent_analysis_events(...):
        yield sse_payload(event)
```

配置保护：

- `[agent] tool_agent_enabled = false` 时，`mode=tool_agent` 返回 SSE error。
- goal 为空时使用默认 goal。

测试：

- pipeline mode 兼容旧行为。
- tool_agent mode 输出 `agent_start`、`tool_call`、`tool_observation`、`done`。
- 未开启 tool_agent 时返回清晰错误。

---

### Task 10：前端展示工具调用过程

**文件：**

- 修改：`/Users/liyanran/github/stock/frontend/src/App.tsx`

第一版前端只做日志展示，不新增复杂页面。

请求：

```ts
body: JSON.stringify({
  mode: toolAgentEnabled ? 'tool_agent' : 'pipeline',
  goal: '分析当前持仓，给出组合风险、每个 ETF 的建议和需要确认的问题',
  cached_results: cachedResults,
  model: selectedModel || null,
})
```

日志展示：

```text
开始 AI 工具调用分析
AI 正在决定下一步
调用工具：get_current_holdings
工具返回：返回 6 只持仓
调用工具：get_holding_technical
工具返回：返回 3 个标的技术指标
AI 已生成最终报告
完成
```

验收：

- pipeline 模式仍可用。
- tool_agent 模式能看到 tool call 过程。
- error 事件显示清楚。
- done 事件复用现有结果展示。

命令：

```bash
cd /Users/liyanran/github/stock/frontend
npm run build
```

---

### Task 11：真实 LLM 联调

**文件：**

- 修改：`/Users/liyanran/github/stock/stock_assistant/llm_tools.py`
- 修改：`/Users/liyanran/github/stock/config.example.toml`
- 修改：`/Users/liyanran/github/stock/tests/test_llm_tools.py`

先用 JSON fallback 联调，不依赖 provider 原生 tool calling。

System prompt 必须说明：

```text
你是受控工具调用 Agent。
你不能直接编造持仓、行情、分类、历史变化。
需要信息时必须输出 type=tool_calls。
信息足够时输出 type=final_report。
只允许调用工具列表中的工具。
最终报告必须符合 AgentReport schema。
```

首轮建议 prompt：

```text
用户目标：分析当前持仓，给出组合风险、每个 ETF 的建议和需要确认的问题。
可用工具见 tools。
请先决定需要调用哪些工具。不要直接写报告，除非已有足够 observation。
```

真实联调验收：

- LLM 第一轮会调用 `get_current_holdings` 或 `get_portfolio_profile`。
- 至少调用 2 个不同工具后再 final。
- final report 能通过现有 `parse_agent_report()` 或 `validate_agent_report()`。
- trace 能回放每一步。

---

## 7. 最终验收矩阵

后端测试：

```bash
cd /Users/liyanran/github/stock
uv run python -m unittest tests.test_llm_tools
uv run python -m unittest tests.test_agent_tools
uv run python -m unittest tests.test_agent_executor
uv run python -m unittest tests.test_agent_loop
uv run python -m unittest tests.test_agent_tool_api
uv run python -m unittest discover -s tests
```

前端构建：

```bash
cd /Users/liyanran/github/stock/frontend
npm run build
```

API smoke：

```bash
cd /Users/liyanran/github/stock
uv run uvicorn api:app --port 8000
```

请求：

```bash
curl -N -X POST http://127.0.0.1:8000/api/agent/run/stream \
  -H 'Content-Type: application/json' \
  -d '{"mode":"tool_agent","goal":"分析当前持仓，给出每个 ETF 的建议"}'
```

预期：

- SSE 输出 `agent_start`。
- 至少出现一次 `tool_call`。
- 至少出现一次 `tool_observation`。
- 最终出现 `done` 或明确 `error`。
- trace 文件可读。
- trace 和 snapshot 不包含 Cookie/API key/source_row/原始 TZZB 响应。

---

## 8. 不做事项

Milestone 8 第一版不做：

- 自动交易。
- 写入策略配置。
- 让 LLM 读取 Cookie/API key。
- 让 LLM 读取原始 TZZB 响应。
- 让 LLM 访问任意文件路径。
- 默认开放外部搜索工具。
- 复杂多用户权限系统。
- MCP server。
- 并发执行多个工具调用。第一版顺序执行，方便 trace 和调试。

---

## 9. 完成标准

这个 Milestone 完成时，用户点击“AI 分析”后，必须能观察到：

```text
AI 决定下一步
AI 调用 get_current_holdings
后端返回 observation
AI 调用 get_holding_technical
后端返回 observation
AI 调用 compare_snapshots
后端返回 observation
AI 生成最终报告
前端展示每个 ETF 的建议
```

只要 LLM 真正参与“选择工具和下一步推理”，并且工具调用经过后端受控执行，就达到了和现有固定流水线的本质区别。
