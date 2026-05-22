# 架构与代码地图

> 本文档目标：让你下次改代码时，先看这一页就能知道"该进哪条路、哪些文件可以直接跳过"，
> 不用再通读全部 16 个 agent 文件。

## 1. 项目本质

这是一个**本地持仓日报工具**，主流程一句话：

> 拉持仓 → 拉 K 线 → 算技术指标 → 让 LLM 写一段解读 → 输出 Markdown。

业务核心代码大约 1100 行，剩下 ~9000 行 Python + 1900 行单文件前端，
绝大部分是**让 LLM 安全循环调工具的协议引擎、报告 schema 校验、HTTP 端的状态机/SSE/暂停恢复**。
读代码"读不懂"基本都源于这部分。

---

## 2. 两条核心路径（一定要分开看）

### 路径 A：业务路径（你日常关心的）

```
fetch_tzzb_holdings   →  fetch_bars        →  analyze_one      →  generate_agent_report_with_llm
(投资账本同步持仓)        (sina/eastmoney K 线)   (MA/RSI/回撤等)      (LLM 写报告)
       │                      │                     │                      │
integrations/tzzb.py    services/market.py   services/analysis.py    agents/agent_llm.py
                                                                    + core/llm.py
持仓画像/分类/历史 diff:
services/portfolio.py     services/classification.py    core/memory.py

输出:
services/report.py (Markdown 报告)        agents/agent.py:save_ai_report (JSON 报告)
```

**这条路径全程不需要 LLM 自主调用工具**，是固定流水线，约 1100 行业务逻辑就够。

### 路径 B：Agent 协议路径（让 LLM 自己决定调哪些工具）

```
api/main.py: run_agent_job
      ↓
agents/agent_loop.py: run_tool_agent_events  ← 主循环 (815 行)
      ↓
状态机:  research_plan → tool_calls → observation_reflection → final_report
      ↓
组件:
  agent_loop_runtime.py    把 config 装配成 runtime 对象
  agent_loop_state.py      消息历史 + reflection 状态 + URL 去重 + 上下文压缩
  agent_loop_handlers.py   缺失补查 / 自动 web_read / 工具调用 / 最终报告
  agent_loop_events.py     事件包装
  agent_protocol.py        prompt 模板 + 协议规则
  agent_tools.py           9 个只读工具的 schema 与 handler  (1026 行)
  agent_executor.py        工具执行 + 敏感字段过滤 + payload 截断
  agent_workspace.py       持仓/分类/技术结果懒加载缓存
  agent_coverage.py        三种 coverage gate 的判定
  agent_llm.py             最终 report schema + 校验 + 修复  (1012 行)
  agent_report_merge.py    final_report 分批合并
  agent_tool_batch.py      并行工具调用
  agent_trace.py           trace JSONL 落盘
```

**这条路径的复杂度几乎全部来自"对抗 LLM 不可控行为"**：
- LLM 没按格式输出 → 协议纠偏，重发 prompt
- LLM 重复同样工具 3 次 / ABAB 模式 → 强制终结
- LLM 想直接写报告但 coverage 不够 → coverage gate 暂缓
- LLM 输出被截断 → 上下文压缩 + 重试
- coverage gate 连续 3 次没收敛 → 降级放行
- LLM 中途崩了 → trace 文件恢复 final_report

---

## 3. 文件读代码优先级

| 你想做什么 | 先看这些 | 可以跳过 |
|---|---|---|
| 改持仓拉取 | `integrations/tzzb.py` | `agents/*` 全部 |
| 改 K 线源 / 改技术指标 | `services/market.py`, `services/analysis.py` | `agents/*` 全部 |
| 改报告输出格式（Markdown） | `services/report.py` | `agents/*` 全部 |
| 改 LLM 解读内容 | `core/llm.py`, `agents/agent_llm.py` | `agents/agent_loop*.py`, `agents/agent_coverage.py` |
| 改 Agent 工具 / 加新工具 | `agents/agent_tools.py`, `agents/agent_executor.py` | `agents/agent_llm.py` 大部分 |
| 改 Agent 循环行为 | `agents/agent_loop.py`, `agents/agent_loop_handlers.py`, `agents/agent_protocol.py` | 业务路径全部 |
| 改 HTTP API | `api/main.py` 路由部分 | `agents/agent_loop*.py` |
| 改前端 | `frontend/src/App.tsx`（单文件 1934 行，建议拆组件） | — |
| 改配置 | `config.example.toml`（兜底默认）→ `config.toml`（你的覆盖） | — |
| 改 Skills 加载 | `core/skills.py` | `agents/*` |
| 改外部搜索 | `integrations/search.py`（896 行多 provider） | — |

---

## 4. 入口与调用链

```
CLI:                uv run python -m stock_assistant.cli run
                      ↓
                    cli/cli.py:run() → 业务路径 A

HTTP:               uvicorn api.main:app
                      ↓
                    /api/holdings        → integrations/tzzb.py        (路径 A 局部)
                    /api/profile         → cli/cli.py:build_portfolio_profile (路径 A 局部)
                    /api/agent/run/start → agents/agent_loop.py         (路径 B)

MCP server:         uv run python -m stock_assistant.mcp_server
                      ↓
                    mcp_server.py 暴露 agent_tools.py 中的只读工具
                    （让外部 Claw/OpenClaw 等 Agent 接进来用）
```

---

## 5. 数据流向与产物

```
持仓快照:  data/holdings/YYYYMMDD-HHMMSS-tzzb-api.json
研究缓存:  data/research/<code>.json     (分类 + 搜索证据)
状态快照:  data/state/snapshots/*-agent-snapshot.json
Agent trace: data/state/agent_traces/agent-*.jsonl
暂停断点:    data/state/agent_runs/<run_id>.json
Skills 安装目录: data/skills/<skill-name>/
报告:        reports/YYYY-MM-DD-etf-report.md
            reports/ai-report-YYYYMMDD-HHMMSS.json
```

---

## 6. 配置覆盖规则（避免改了不生效）

```
config.example.toml   ← 兜底默认值（被 core/config.py 加载为 DEFAULTS）
       ↓ 被覆盖
config.toml           ← 你本地的实际配置
       ↓ 被覆盖（仅 LLM 模型字段）
[llm.model_profiles."<model_id>"]   ← 选定模型时覆盖 [llm] 顶层字段
```

只覆盖**叶子字段**，不会做 dict 深合并 — 这意味着 `config.toml` 里没写的字段直接走 example 默认。

---

## 7. 可以减小复杂度的方向（参考，不是承诺）

按代价从低到高：

1. **agents/ 16 个文件可以合并到 4-5 个**：
   - `loop.py`（合并 agent_loop / agent_loop_runtime / agent_loop_handlers / agent_loop_state / agent_loop_events / agent_coverage / agent_report_merge）
   - `tools.py`（合并 agent_tools / agent_executor / agent_tool_batch）
   - `protocol.py`（合并 agent_protocol / agent_llm 中的 schema 部分）
   - `workspace.py`
2. **api/main.py 的 agent_runs 状态机抽到 `api/agent_runs.py`**，让 main.py 只剩路由。
3. **frontend/src/App.tsx 1934 行拆组件**。
4. **协议层简化**：现在的 4 步状态机（research_plan/tool_calls/reflection/final_report）+ 三种 coverage gate 是可以塌缩的。
   - 如果你的真实任务路径其实是固定的（拉持仓 → 拉 K 线 → 写报告），LLM 就**不需要自主决定调哪些工具**，
     一次结构化输出就够，agents/ 整个目录可以删 80%。
   - 保留 Agent 的唯一理由是 MCP server，让外部 Agent 进来研究；
     这种场景下 mcp_server.py 才是产品，本地 Agent 循环可以直接删。
