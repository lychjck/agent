# Stock Agent (LangGraph)

基于 LangGraph 的投资诊断 Agent，通过 MCP 协议调用 stock_mcp 工具层。

## 架构

```
agent/
├── src/stock_agent/
│   ├── __init__.py
│   ├── config.py          # 配置加载
│   ├── state.py           # AgentState 定义
│   ├── graph.py           # StateGraph 构建
│   ├── nodes/             # 各节点实现
│   │   ├── __init__.py
│   │   ├── diagnose.py    # 持仓诊断节点
│   │   ├── investigate.py # 异常探案节点
│   │   └── report.py     # 报告生成节点
│   ├── tools.py           # MCP 工具绑定
│   └── llm.py            # LLM 客户端
├── pyproject.toml
└── README.md
```

## 运行

```bash
cd agent
uv run python -m stock_agent
```

## 依赖

- langgraph: 状态图执行引擎
- langchain-core: 消息/工具抽象
- httpx: MCP HTTP 客户端
- openai: LLM 调用
