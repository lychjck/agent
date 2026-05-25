# ETF 持仓助手升级规划：LangGraph 驱动与专业解耦策略引擎

为了消除原生手写 Agent 引擎导致的复杂纠偏管道（Plumbing Code），并构建一个具备工业级容错、自主探案思维与专业级投资策略审查能力的智能投顾系统，我们对实施方案进行全新升级。

---

## 架构演进蓝图

系统将彻底解耦为三个层次：
1. **数据与工具层 (MCP Server)**：提供高内聚、高并发的本地金融数据源。
2. **策略计算层 (Strategy Engine)**：**新增独立模块**。专注于数理公式、网格参数和对冲规则的精准计算，输出纯数理的“硬信号”。
3. **自主规划层 (LangGraph Agent)**：**引入框架**。负责理解用户意图、动态异常判定、调度策略引擎、进行联网根因调查，并最终对策略引擎给出的“硬信号”进行**主观分析与风控审查**。

```mermaid
graph TD
    subgraph 决策层 (LangGraph Agent)
        A[StateGraph 状态图]
        A -->|条件路由| B[探案 Node: 根因调查]
        A -->|条件路由| C[投顾 Node: 决策审查]
    end

    subgraph 策略层 (Strategy Engine)
        D[策略引擎核心]
        D -->|规则计算| E[网格策略模块]
        D -->|规则计算| F[红利对冲模块]
    end

    subgraph 数据与工具层 (MCP / Workspace)
        G[持仓快照]
        H[技术指标+2-Sigma计算]
        I[网络搜索 opencli]
    end

    %% 数据与调用流
    A -->|1. 动态异常判定| H
    B -->|2. 根因追溯| I
    C -->|3. 请求策略信号| D
    D -->|读取配置| G
    C -->|4. AI 结合舆情否决或批准信号| A
```

---

## User Review Required

> [!IMPORTANT]
> **在升级前，请确认以下设计要点：**
> 1. **环境依赖更新**：引入 LangGraph 需要在 `pyproject.toml` 中使用 `uv` 添加 `langgraph` 及相关依赖包。我们将使用 `uv add langgraph` 管理。
> 2. **策略引擎的配置文件设计**：策略引擎将依赖独立的配置文件或 `config.toml` 中新增的 `[strategies]` 节点。例如：
>    ```toml
>    [strategies.grid.510300]
>    center_price = 3.50
>    grid_width = 0.05
>    position_slots = 10
>    ```
>    你需要确认是否接受在 `config.toml` 中以这种结构化格式录入策略的具体参数。

---

## Open Questions

> [!WARNING]
> * **人机协作（Human-in-the-Loop）的中断节点**：LangGraph 的核心优势是支持在特定节点拦截并挂起。您是否希望在 Agent 执行“策略调仓建议”并准备下单/生成报告前，**强制暂停并向您弹出确认按钮**，由您批准后再继续？
> * **策略否决权（Veto Power）的尺度**：当策略引擎根据网格规则算出“应当买入 1 份”，但 Agent 联网查到大宗商品系统性暴跌时，Agent 拥有何种级别的否决权？是直接拦截该交易，还是仅在报告中输出警告供您参考？

---

## Proposed Changes

---

### Component 1: 独立解耦的策略引擎 (NEW MODULE)

在 `src/stock_assistant/core/strategy/` 目录下创建一套全新的非 Agent 纯数理策略计算引擎。

#### [NEW] [engine.py](file:///Users/liyanran/github/stock/src/stock_assistant/core/strategy/engine.py)
* 负责管理和初始化各个具体策略类。
* 输入当前持仓快照与技术面指标，自动路由并汇总所有标的的数理推荐信号。

#### [NEW] [grid.py](file:///Users/liyanran/github/stock/src/stock_assistant/core/strategy/grid.py)
* **经典网格策略实现**：计算当前市价在网格区间的位置，计算格距，输出明确的数理动作：`BUY_GRID(quantity)`，`SELL_GRID(quantity)` 或 `HOLD`。

#### [NEW] [dividend_hedge.py](file:///Users/liyanran/github/stock/src/stock_assistant/core/strategy/dividend_hedge.py)
* **红利对冲策略实现**：跟踪红利股息率变动与关联对冲资产的强弱偏离度，输出纯规则视角的加减仓硬信号。

---

### Component 2: 增强 MCP 层的统计学支撑 (阶段一)

#### [MODIFY] [agent_tools.py](file:///Users/liyanran/github/stock/src/stock_assistant/agents/agent_tools.py)
* **动态异常判定支持**：修改 `stock_get_holding_technical` 工具，使其不仅返回均线和 RSI，还要自动回溯 120 天历史数据，计算并输出：
  * **历史日振幅标准差 ($\sigma$)**
  * **当日波幅与标准差的偏离倍数 ($Z-Score$)**
  * **异常偏离标志位**：当当日振幅绝对值 $> 2\sigma$ 时，自动标记 `is_abnormal_deviation = True`。
* **重仓股检索工具**：
  * **[NEW TOOL]** `stock_get_etf_constituents`：支持查询 ETF 对应的前十大权重股，让 Agent 有线索可以向下层追溯重仓股异动。

---

### Component 3: 用 LangGraph 重构 Agent 决策大脑 (阶段二)

彻底放弃原有的 `agent_loop.py` 循环，采用 LangGraph 的 `StateGraph` 进行定义。

#### [NEW] [state.py](file:///Users/liyanran/github/stock/src/stock_assistant/agents/graph/state.py)
* 定义 `AgentState` 结构体，包含当前持仓快照、历史 Trace、待调查的异常标的列表、策略引擎给出的硬信号列表、以及最终报告状态。

#### [NEW] [nodes.py](file:///Users/liyanran/github/stock/src/stock_assistant/agents/graph/nodes.py)
* **异常诊断 Node (Anomaly Assessor)**：获取技术指标，动态筛选出 $Z-Score > 2.0$ 的异常 ETF，将其推入待调查队列。
* **联网探案 Node (Investigator)**：针对异常标的，调用重仓股查询与 `opencli` 检索，追溯波动背后的行业政策或基本面突变。
* **策略决策 Node (Strategy Auditor)**：调用独立的 `Strategy Engine` 拿到规则硬信号，接着驱动 LLM 结合“探案 Node”搜集到的舆情噪音，进行**主观审查与偏好验证**，生成最终批准或否决的动作建议。

#### [NEW] [workflow.py](file:///Users/liyanran/github/stock/src/stock_assistant/agents/graph/workflow.py)
* 构建 `StateGraph` 并编译（Compile）。
* 设计条件路由边（Conditional Edges）：
  * `if 存在待调查标的 -> 路由至 Investigator 节点`
  * `if 调查完成 -> 路由至 Strategy Auditor 审查节点`

---

## Verification Plan

### 策略引擎单元测试
* 编写 `tests/test_strategy_engine.py`，模拟多只 ETF 处于网格买入线、卖出线和平衡区，验证 `grid.py` 和 `dividend_hedge.py` 算出的数理信号 100% 准确。

### LangGraph 决策流验证
* 模拟异常场景（如黄金 ETF 大跌 5%），验证 LangGraph 的条件路由能够精准地进入 `Investigator` 节点抓取重仓股及新闻，并最终进入 `Strategy Auditor` 节点输出带有 AI 主观否决或批准论证的调仓建议。
