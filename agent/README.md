# Stock Agent (LangGraph)

基于 LangGraph 的投资诊断 Agent，通过 MCP 协议调用 stock_mcp 工具层。

## 设计要点

- **完全自包含**：不再依赖主项目 `stock_assistant`，所有 schema / 校验 / 渲染逻辑都在 agent 内部。
- **条件边路由**：诊断后根据是否有可研究标的决定是否进 investigate；任一节点出错走兜底。
- **Send 并行 fan-out**：核心持仓 / 主题分别用 `Send` 派发到 `research_holding` / `research_theme` 子节点，由 LangGraph 调度并发，结果通过 `add` reducer 自动合并。
- **统一错误兜底**：每个节点用 `@safe_node` 装饰器把异常转成 `state.errors`，由 `error_handler` 节点统一产出降级报告。

## 架构

```
agent/
├── src/stock_agent/
│   ├── cli.py                # 命令行入口（支持流式输出）
│   ├── config.py             # 配置加载
│   ├── llm.py                # ChatOpenAI 工厂
│   ├── mcp_client.py         # JSON-RPC 客户端，错误统一转 ok:false
│   ├── state.py              # AgentState（投递区与累积区分明）
│   ├── graph.py              # 图构建：条件边 / Send / error 节点
│   ├── schema/               # 自包含 schema
│   │   ├── report.py         # AgentReport 等 Pydantic 模型
│   │   └── validate.py       # validate_agent_report / fallback
│   ├── render/
│   │   └── markdown.py       # 报告 → Markdown
│   └── nodes/
│       ├── _safe.py          # safe_node 装饰器
│       ├── diagnose.py       # 拉持仓 + 异常识别
│       ├── investigate.py    # dispatch（Send 派发）
│       ├── research.py       # research_holding / research_theme（并发）
│       ├── report.py         # LLM 生成结构化 JSON
│       ├── render.py         # 校验 + Markdown + 落盘
│       └── error_handler.py  # 兜底节点
├── pyproject.toml
└── README.md
```

## 运行

```bash
cd agent
uv run python -m stock_agent
# 或
uv run stock-agent --config ../config.toml
```

参数：
- `--config <path>`：配置文件
- `--profile <name>`：LLM model_profiles 切换
- `--mcp-url <url>` / `--mcp-token <token>`：覆盖 MCP 端点
- `--no-stream`：关闭节点级流式输出

## 图流转

```
START → diagnose ──┬─ errors? ─yes─→ error_handler → END
                   └─ no
                       │
                       ▼
                should_investigate?
                  ├─ no  ────────────────┐
                  └─ yes                  │
                       ▼                  │
                   investigate            │
                       │                  │
                  Send fan-out:           │
                  ├─→ research_holding ──┤
                  └─→ research_theme  ───┤
                                          ▼
                                       report ──┬─ errors? ─yes→ error_handler → END
                                                └─ no → render → END
```

## 依赖

- langgraph：状态图、Send fan-out、reducer
- langchain-core / langchain-openai：消息模型与 LLM 客户端
- httpx：MCP HTTP 客户端
- pydantic：报告 schema 校验
