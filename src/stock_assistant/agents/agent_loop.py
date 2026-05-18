import datetime as dt
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, AsyncIterator

from stock_assistant.agents.agent import save_ai_report
from stock_assistant.agents.agent_executor import ToolObservation, execute_tool_call, tool_observation_message
from stock_assistant.agents.agent_llm import agent_report_schema_hint, parse_agent_report
from stock_assistant.agents.agent_tools import build_agent_tool_registry, is_etf_like_holding, tool_schemas
from stock_assistant.agents.agent_trace import AgentTraceWriter
from stock_assistant.agents.agent_workspace import AgentWorkspace
from stock_assistant.core.llm import llm_enabled
from stock_assistant.core.llm_tools import LlmToolCall, call_llm_tool_step
from stock_assistant.core.memory import agent_snapshots_have_same_facts, save_agent_snapshot
from stock_assistant.core.utils import config_bool, log


def agent_run_id() -> str:
    return f"agent-{dt.datetime.now():%Y%m%d-%H%M%S-%f}"


def tool_agent_event(step: str, status: str = "", **extra: Any) -> dict[str, Any]:
    event = {"step": step}
    if status:
        event["status"] = status
    event.update(extra)
    return event


def compact_for_log(value: Any, max_chars: int = 500) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str, sort_keys=True)
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + f"...[truncated {len(text)} chars]"


def agent_log(run_id: str, message: str, *, turn: int | None = None, level: str = "INFO") -> None:
    turn_part = f" turn={turn}" if turn is not None else ""
    log(f"[tool-agent run={run_id}{turn_part}] {message}", level=level, name="tool_agent")


def build_initial_agent_messages(goal: str, tools: list[dict[str, Any]], use_native_tools: bool = False) -> list[dict[str, str]]:
    tool_text = ""
    if not use_native_tools:
        tool_json = json.dumps(tools, ensure_ascii=False, indent=2)
        tool_text = f"可用工具如下。你只能调用这些工具，arguments 必须符合 parameters。\n{tool_json}\n\n"
    tool_names = {
        str(tool.get("function", {}).get("name") or tool.get("name") or "")
        for tool in tools
    }
    skill_rule = ""
    if {"list_skills", "read_skill"} <= tool_names:
        skill_rule = (
            "Skill 使用硬性规则：如果 list_skills 返回 count>0，下一步必须 read_skill 读取最相关 skill 的 SKILL.md；"
            "在 read_skill 完成前，不能把 skill 发现视为已满足，不能输出 final_report。"
        )
        if "read_skill_file" in tool_names:
            skill_rule += (
                "如果 read_skill 内容提到 references、advanced search、examples、配置或配套文件，"
                "必须用 list_skill_files/read_skill_file 读取与当前任务最相关的一个配套文件。"
            )
    search_rule = ""
    if {"web_search", "web_read"} <= tool_names:
        opencli_rule = ""
        if "opencli_command" in tool_names:
            opencli_rule = (
                "当前可用 opencli_command。它不是普通搜索框，而是 opencli 的站点适配器命令入口；"
                "必须根据任务先选择 site/command，再组织 positionals/options。"
                "财经/持仓任务优先使用：eastmoney quote/etf/kline/sectors/kuaixun/rank，"
                "sinafinance news/stock，xueqiu search/stock/kline/hot-stock，yahoo-finance quote；"
                "通用检索再用 duckduckgo/google/brave/yahoo search；打开具体 URL 正文可用 web read 或 web_read。"
                "典型调用格式："
                "opencli_command(site='duckduckgo', command='search', positionals=['查询词'], options={'limit':10,'region':'cn-zh'});"
                "opencli_command(site='eastmoney', command='quote', positionals=['SH510300'], options={});"
                "opencli_command(site='eastmoney', command='kline', positionals=['SH510300'], options={'period':'day','limit':60});"
                "opencli_command(site='web', command='read', positionals=[], options={'url':'https://...','stdout':true,'download-images':false})。"
            )
        search_rule = (
            f"{opencli_rule}"
            "外部研究硬性规则：如果任务涉及 ETF/基金/股票/市场/行业/宏观背景/今日行情，且可用外部工具，"
            "必须执行分层外部研究，不能只搜索一次就结束。分层研究至少包括："
            "1) 组合层面搜索：查询今日市场、宏观、A股/港股/美股、利率、汇率、商品等背景；"
            "2) 主题层面搜索：按组合主要暴露主题搜索，例如红利低波、债券基金、恒生科技、纳指100、医药、消费、有色、能源、电力；"
            "3) 标的层面搜索：对用户持仓中每个权重>=1%的标的逐一搜索；低于1%的标的如数量较多，可按主题分组搜索，但 final_report 必须列出未逐一搜索的标的。"
            "如果 opencli_command 可用，优先用它调用具体站点命令抓结构化数据；只有站点命令不覆盖当前问题时才退回 web_search。"
            "web_search 底层统一走 opencli；每个通用 web_search 必须设置 max_results=8 到 10，不要指定 engines。"
            "每个重要主题或核心标的至少 web_read 一个相关来源页。"
            "除非外部工具报错或连续返回无关结果，否则不能在未完成组合层面+主题层面+核心标的层面研究前输出 final_report。"
            "如果已安装的 skill 是搜索类 skill，例如 multi-search-engine，必须先 read_skill，再按该 skill 选择搜索引擎和查询语法。"
        )
        
    report_schema = json.dumps(agent_report_schema_hint(), ensure_ascii=False, indent=2)
    system = (
        "你是一个受控的中文持仓分析工具调用 Agent。"
        "你不能直接读取文件、Cookie、API key 或原始账户响应。"
        "你必须先从任务本身推导信息需求，再把信息需求映射到可用工具。"
        "如果任务可能受益于用户安装的 skill、专门流程或领域方法，必须把 skill 发现纳入信息需求。"
        f"{skill_rule}"
        f"{search_rule}"
        "不要因为当前没有工具就假装信息足够；缺工具时必须显式记录 missing_capabilities。"
        "需要信息时，只能从给定工具列表中选择工具，并输出合法 JSON。"
        "信息不足时继续调用工具；信息足够时输出 final_report。"
        "每次工具 observation 之后，下一轮必须先输出 observation_reflection，不能直接输出 final_report。"
        "每次输出都要包含 reasoning_summary 和 thinking_trace，用中文充分说明可审计的决策依据。"
        "不要输出隐藏推理链；但 thinking_trace 不能过短，必须覆盖事实、缺口、影响、下一步。"
        "不要输出 Markdown 包裹。"
    )
    user = (
        f"用户目标：{goal}\n\n"
        f"{tool_text}"
        "第一轮必须输出 research_plan，不能调用工具，不能输出 final_report。"
        "research_plan 必须先从任务出发列出 information_needs，再列 available_tool_mapping 和 missing_capabilities。\n"
        "ETF/基金分析的信息需求至少考虑：当前组合权重、标的类型、跟踪指数、底层持仓/前十大持仓、行业/区域/风格暴露、"
        "标的自身 K 线、底层核心资产趋势、同类替代品、历史变化、限制条件。"
        "研究深度要求：不能只基于本地技术指标给结论；必须把本地持仓/分类/技术面与外部搜索证据交叉验证。"
        "对每个权重>=1%的标的，information_needs 必须包含：跟踪指数/投资范围、近期新闻或驱动因素、同类或替代品、当前风险点、与组合中其他标的的重叠/相关性。"
        "对权重<1%的标的，也必须至少按行业/主题分组纳入搜索和限制说明。"
        "如果可用工具里有 list_skills/read_skill，且任务可能匹配用户安装的 skill，先列出 skill 发现需求；"
        "读取 skill 后必须按其 SKILL.md 的约束工作，并在 observation_reflection 中说明采用了哪个 skill。"
        "如果可用工具里有 opencli_command，外部研究必须优先把需求映射成 opencli 的 site/command："
        "市场/指数/板块/ETF 行情优先 eastmoney；财经快讯优先 eastmoney kuaixun 或 sinafinance news；"
        "股票/ETF 搜索优先 xueqiu/eastmoney/sinafinance；通用网页检索才用 duckduckgo/google/brave/yahoo search；"
        "如果可用工具里有 web_search/web_read，搜索任务可用 web_search 获取结构化结果，再用 web_read 打开具体来源；"
        "对于 ETF/基金/持仓分析，外部搜索需求至少包括：市场背景、相关行业/主题近期表现、重大新闻或宏观风险；"
        "外部搜索计划必须分成 market_context、theme_research、holding_research 三类，并在 available_tool_mapping 中映射到 opencli_command/web_search/web_read。"
        "holding_research 至少覆盖所有权重>=1%的标的；如果用户要求每个 ETF 建议，必须在 coverage_notes 逐项说明每个 ETF 是否完成外部搜索、技术指标、分类信息三类覆盖。"
        "如果 opencli_command 或 web_search/web_read 可用，这些需求必须映射到可用外部工具，而不是写成 missing_capabilities。"
        "只有需要直接访问某个 URL 时才使用底层 web_fetch。"
        "当前工具无法获取的信息必须写入 missing_capabilities，例如 ETF 底层持仓、跟踪指数、指数成分、成分股 K 线等。\n\n"
        "每次输出必须包含 reasoning_summary 和 thinking_trace。thinking_trace 用对象表达："
        "task_understanding、known_facts、information_needs、available_tool_mapping、missing_capabilities、decision_basis、next_step。\n\n"
        "第一轮研究计划输出格式：\n"
        "{\"type\":\"research_plan\",\"reasoning_summary\":\"我先从 ETF 分析任务推导需要验证的信息，而不是直接按工具列表行动。\","
        "\"thinking_trace\":{\"task_understanding\":\"...\",\"information_needs\":[\"...\"],"
        "\"available_tool_mapping\":[{\"need\":\"当前组合权重\",\"tool\":\"get_current_holdings\"}],"
        "\"missing_capabilities\":[\"ETF 底层持仓工具\"],\"decision_basis\":\"...\",\"next_step\":\"...\"},"
        "\"research_plan\":{\"information_needs\":[\"...\"],\"available_tool_mapping\":[{\"need\":\"...\",\"tool\":\"...\"}],"
        "\"missing_capabilities\":[\"...\"],\"execution_strategy\":\"先获取已有工具可验证的信息，同时保留缺失能力限制。\"}}\n\n"
        "工具调用输出格式：\n"
        "{\"type\":\"tool_calls\",\"reasoning_summary\":\"我还缺少当前持仓明细，所以先读取脱敏持仓。\","
        "\"thinking_trace\":{\"known_facts\":[\"...\"],\"information_needs\":[\"...\"],"
        "\"available_tool_mapping\":[{\"need\":\"...\",\"tool\":\"get_current_holdings\"}],"
        "\"missing_capabilities\":[\"...\"],\"decision_basis\":\"...\",\"next_step\":\"...\"},"
        "\"tool_calls\":[{\"id\":\"call_001\",\"name\":\"get_current_holdings\",\"arguments\":{}}]}\n\n"
        "工具结果反思输出格式：\n"
        "{\"type\":\"observation_reflection\",\"reasoning_summary\":\"我根据刚返回的工具结果更新了研究状态。\","
        "\"thinking_trace\":{\"known_facts\":[\"...\"],\"satisfied_needs\":[\"...\"],"
        "\"unsatisfied_needs\":[\"...\"],\"missing_capabilities\":[\"...\"],"
        "\"observation_impact\":\"...\",\"decision_basis\":\"...\",\"next_step\":\"...\"},"
        "\"observation_reflection\":{\"satisfied_needs\":[\"...\"],\"unsatisfied_needs\":[\"...\"],"
        "\"observation_impact\":\"工具结果改变/确认了什么判断\","
        "\"coverage_notes\":\"哪些标的已经覆盖，哪些还没覆盖\","
        "\"next_action\":\"continue_tools 或 final_report\","
        "\"required_tool_calls\":[{\"tool\":\"get_holding_technical\",\"reason\":\"...\"}]}}\n\n"
        "最终报告输出格式：\n"
        "{\"type\":\"final_report\",\"reasoning_summary\":\"已经读取了持仓、画像和必要技术指标，可以生成报告。\","
        "\"thinking_trace\":{\"known_facts\":[\"...\"],\"missing_capabilities\":[\"...\"],"
        "\"decision_basis\":\"哪些证据足够，哪些只能作为限制说明\"},"
        "\"report\":{...}}\n\n"
        f"最终 report 必须符合这个 schema：\n{report_schema}\n\n"
        "最终报告前必须至少有一次 observation_reflection，并且最近一次 observation_reflection 的 next_action 必须是 final_report。"
        "如果目标要求每个 ETF 的建议，必须在 reflection.coverage_notes 中说明覆盖范围；没有足够数据的标的要列为未覆盖或限制。"
        "最终报告不能只给笼统建议；每个标的建议必须引用至少一种本地证据（持仓/分类/技术）和一种外部证据（搜索/网页读取），"
        "如果缺外部证据，action_type 只能是 hold/watch，且必须在 limitations 中说明该标的结论可信度不足。"
        "现在只输出第一轮 research_plan。"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def llm_decision_payload(step: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "type": step.type,
        "reasoning_summary": step.reasoning_summary,
        "thinking_trace": step.thinking_trace,
        "missing_capabilities": step.missing_capabilities,
    }
    if step.type == "research_plan":
        payload["research_plan"] = step.research_plan or {}
    elif step.type == "observation_reflection":
        payload["observation_reflection"] = step.observation_reflection or {}
    elif step.type == "tool_calls":
        payload["tool_calls"] = [call.model_dump() for call in step.tool_calls]
    else:
        payload["final_report"] = step.final_report or {}
    return payload


def build_act_prompt() -> str:
    return (
        "已记录 research_plan。现在进入执行阶段："
        "只能对 available_tool_mapping 中当前工具能满足的信息需求调用工具。"
        "如果计划中包含 skill 发现，且 list_skills/read_skill 可用，必须先调用 list_skills；"
        "如果已经知道存在可用 skill 但尚未 read_skill，下一步必须调用 read_skill。"
        "如果 opencli_command 可用且任务涉及 ETF/基金/股票/市场/行业/宏观背景，"
        "优先调用 opencli_command 的具体 site/command 获取结构化证据，例如 eastmoney quote/kline/sectors/kuaixun、"
        "sinafinance news/stock、xueqiu search/stock/kline、duckduckgo/google search、web read。"
        "如果 web_search/web_read 可用，且任务涉及 ETF/基金/股票/市场/行业/宏观背景，"
        "必须按 market_context、theme_research、holding_research 三层安排外部研究；通用检索用 web_search，具体站点数据用 opencli_command，得到 URL 后用 web_read 打开相关来源。"
        "每次最多并行调用 6 个搜索/读取工具，优先覆盖权重>=1%的标的；不要因为已有技术指标就跳过外部搜索。"
        "每次调用工具前，在 thinking_trace.decision_basis 中说明为什么这个工具能推进任务。"
        "如果已有证据不足，不要输出 final_report；如果缺少 ETF 底层持仓/指数成分等能力，"
        "继续在 missing_capabilities 中保留，不要臆测。"
    )


def build_reflection_prompt() -> str:
    return (
        "你刚收到了一个或多个工具 observation。下一步必须输出 observation_reflection，不能调用工具，也不能输出 final_report。"
        "请审查：哪些 information_needs 已满足、哪些未满足、工具结果如何改变判断、"
        "是否覆盖了用户要求的每个 ETF 建议、下一步应该继续调用哪些工具或是否可以最终报告。"
        "如果 list_skills observation 显示 count>0，而当前上下文还没有 read_skill observation，"
        "next_action 必须是 continue_tools，required_tool_calls 必须包含 read_skill。"
        "如果 read_skill 显示这是搜索类 skill，且外部工具可用，同时任务涉及 ETF/基金/股票/市场/行业/宏观背景，"
        "next_action 必须是 continue_tools，required_tool_calls 必须包含 opencli_command 或 web_search，并说明属于 market_context、theme_research 还是 holding_research；"
        "如果已有 opencli_command/web_search 结果且存在相关 URL，required_tool_calls 必须包含 web_read 或 opencli_command(site='web', command='read')。"
        "如果还没有覆盖所有权重>=1%的标的外部搜索，next_action 不得为 final_report；"
        "如果低权重标的未逐一搜索，coverage_notes 必须按主题列出它们被哪一次分组搜索覆盖。"
        "如果还缺 ETF 底层持仓/指数成分等能力，要继续保留在 missing_capabilities，并说明这对建议强度的影响。"
        "observation_reflection.next_action 只能是 continue_tools 或 final_report。"
    )


def build_after_reflection_prompt(reflection: dict[str, Any]) -> str:
    next_action = str(reflection.get("next_action", "")).strip()
    if next_action == "final_report":
        return (
            "已记录 observation_reflection，且 next_action=final_report。"
            "现在可以输出 final_report，但必须在 limitations 中保留缺失能力造成的限制，"
            "并区分已验证结论和数据不足的标的。"
        )
    return (
        "已记录 observation_reflection。现在请根据 required_tool_calls 或 unsatisfied_needs 继续调用工具。"
        "如果 required_tool_calls 中提到 read_skill、opencli_command、web_search 或 web_read，且这些工具在可用工具列表中，优先执行这些工具。"
        "如果还没有完成 market_context、theme_research、holding_research 三层外部研究，不要输出 final_report；继续调用 opencli_command/web_search 或读取来源页。"
        "如果当前工具无法满足某个需求，不要臆测；保留 missing_capabilities，并选择还能推进任务的可用工具。"
    )


def reflection_next_action(reflection: dict[str, Any] | None) -> str:
    if not isinstance(reflection, dict):
        return ""
    return str(reflection.get("next_action", "")).strip()


def chunked(items: list[str], size: int) -> list[list[str]]:
    return [items[index:index + size] for index in range(0, len(items), size)]


def split_oversized_tool_call(call: LlmToolCall) -> list[LlmToolCall]:
    if call.name not in {"get_holding_technical", "get_classification"}:
        return [call]
    codes = call.arguments.get("codes")
    if not isinstance(codes, list):
        return [call]
    limit = 20 if call.name == "get_holding_technical" else 50
    if len(codes) <= limit:
        return [call]
    split_calls: list[LlmToolCall] = []
    for index, code_chunk in enumerate(chunked([str(code) for code in codes], limit), start=1):
        arguments = dict(call.arguments)
        arguments["codes"] = code_chunk
        split_calls.append(
            LlmToolCall(
                id=f"{call.id or call.name}_part_{index:02d}",
                name=call.name,
                arguments=arguments,
            )
        )
    return split_calls


def split_oversized_tool_calls(calls: list[LlmToolCall]) -> list[LlmToolCall]:
    output: list[LlmToolCall] = []
    for call in calls:
        output.extend(split_oversized_tool_call(call))
    return output


def externally_slow_tool(name: str) -> bool:
    return name in {"web_search", "web_read", "opencli_command", "web_fetch"}


def merge_unique_strings(left: Any, right: Any) -> list[str]:
    output: list[str] = []
    for value in list(left or []) + list(right or []):
        text = str(value).strip()
        if text and text not in output:
            output.append(text)
    return output


def normalize_report_payload(report_payload: dict[str, Any] | None) -> dict[str, Any]:
    report = dict(report_payload or {})
    if isinstance(report.get("report"), dict):
        report = dict(report["report"])
    return report


def merge_final_report_patch(base_payload: dict[str, Any] | None, patch_payload: dict[str, Any] | None) -> dict[str, Any]:
    base = normalize_report_payload(base_payload)
    patch = normalize_report_payload(patch_payload)
    merged = dict(base)
    for key, value in patch.items():
        if key in {"holding_analysis", "limitations", "evidence"}:
            continue
        if value not in (None, "", [], {}):
            merged[key] = value
    base_items = [
        item for item in base.get("holding_analysis", [])
        if isinstance(item, dict)
    ] if isinstance(base.get("holding_analysis"), list) else []
    patch_items = [
        item for item in patch.get("holding_analysis", [])
        if isinstance(item, dict)
    ] if isinstance(patch.get("holding_analysis"), list) else []
    by_code: dict[str, dict[str, Any]] = {}
    ordered_codes: list[str] = []
    for item in base_items + patch_items:
        code = str(item.get("target_code") or item.get("code") or "").strip()
        key = code or f"__index_{len(ordered_codes)}"
        if key not in ordered_codes:
            ordered_codes.append(key)
        by_code[key] = item
    if ordered_codes:
        merged["holding_analysis"] = [by_code[key] for key in ordered_codes]
    merged["limitations"] = merge_unique_strings(base.get("limitations"), patch.get("limitations"))
    merged["evidence"] = merge_unique_strings(base.get("evidence"), patch.get("evidence"))
    return merged


def goal_requires_full_technical_coverage(goal: str) -> bool:
    normalized = goal.strip()
    return any(token in normalized for token in ("每个", "全部", "所有", "逐个"))


def missing_technical_codes(workspace: AgentWorkspace, goal: str) -> list[str]:
    if not goal_requires_full_technical_coverage(goal):
        return []
    existing = {
        str((item.get("holding") or {}).get("code", ""))
        for item in workspace.technical_results
        if isinstance(item.get("holding"), dict)
    }
    return [
        holding.code
        for holding in workspace.ensure_holdings()
        if holding.code and holding.code not in existing
    ]


def build_coverage_prompt(missing_codes: list[str]) -> str:
    return (
        "最终报告暂缓：用户目标要求逐个覆盖当前持仓，但仍有标的缺少 technical observation。"
        f"后端已补充请求缺失标的技术指标，缺失数量={len(missing_codes)}。"
        "收到这些 observation 后必须重新做 observation_reflection；只有覆盖缺口清零，"
        "或 observation 明确说明某个标的不可分析，才可以 final_report。"
    )


def goal_requires_external_research(goal: str) -> bool:
    normalized = goal.strip()
    return any(token in normalized for token in ("持仓", "ETF", "基金", "股票", "市场", "行业", "宏观", "行情"))


def important_holding_records(workspace: AgentWorkspace, *, min_weight_pct: float = 1.0) -> list[dict[str, Any]]:
    total_value = workspace.total_value()
    if not total_value:
        return []
    records: list[dict[str, Any]] = []
    for holding in workspace.ensure_holdings():
        if holding.market_value is None:
            continue
        weight_pct = holding.market_value / total_value * 100
        if weight_pct >= min_weight_pct:
            records.append({
                "code": holding.code,
                "name": holding.name,
                "weight_pct": round(weight_pct, 4),
            })
    return sorted(records, key=lambda item: float(item.get("weight_pct") or 0), reverse=True)


def external_research_gap(
    workspace: AgentWorkspace,
    goal: str,
    registry: dict[str, Any],
    web_search_queries: list[str],
    web_read_count: int,
) -> dict[str, Any] | None:
    if "web_search" not in registry and "opencli_command" not in registry:
        return None
    if "web_read" not in registry and "opencli_command" not in registry:
        return None
    if not goal_requires_external_research(goal):
        return None
    important = important_holding_records(workspace)
    query_blob = "\n".join(web_search_queries).lower()
    missing = [
        item for item in important
        if str(item.get("code", "")).lower() not in query_blob
        and str(item.get("name", "")).lower() not in query_blob
    ]
    reasons: list[str] = []
    if not web_search_queries:
        reasons.append("尚未执行 opencli_command/web_search")
    if web_read_count <= 0:
        reasons.append("尚未执行 web_read/opencli web read，只有搜索结果摘要，没有打开来源页")
    if missing:
        reasons.append(f"权重>=1%的标的仍有 {len(missing)} 个未在搜索 query 中逐项覆盖")
    if not reasons:
        return None
    return {
        "reasons": reasons,
        "important_count": len(important),
        "searched_queries": web_search_queries,
        "web_read_count": web_read_count,
        "missing_holding_research": missing,
    }


def build_external_research_gate_prompt(gap: dict[str, Any]) -> str:
    missing = gap.get("missing_holding_research") or []
    missing_text = ", ".join(
        f"{item.get('code')} {item.get('name')}({item.get('weight_pct')}%)"
        for item in missing[:12]
    )
    return (
        "最终报告暂缓：后端检查发现外部研究覆盖不足，不能把未完成的搜索说成已经覆盖。"
        f"原因：{'; '.join(str(item) for item in gap.get('reasons', []))}。"
        f"尚未逐项搜索的核心标的：{missing_text or '无'}。"
        "下一步必须继续调用工具："
        "1) 如果 web_read_count=0，先从已有 opencli_command/web_search 结果中选择最相关 URL 调用 web_read 或 opencli_command(site='web', command='read')；"
        "2) 对 missing_holding_research 中的标的分批调用 opencli_command 或 web_search，每次最多 6 个工具调用，query/positionals 必须包含标的代码和名称；"
        "3) 之后重新 observation_reflection，coverage_notes 必须基于实际工具调用，不得虚报。"
    )


def final_report_missing_holding_analysis(
    workspace: AgentWorkspace,
    goal: str,
    report_payload: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if not goal_requires_full_technical_coverage(goal):
        return []
    report = report_payload or {}
    if isinstance(report.get("report"), dict):
        report = report["report"]
    items = report.get("holding_analysis")
    if not isinstance(items, list):
        items = []
    covered = {
        str(item.get("target_code") or item.get("code") or "").strip()
        for item in items
        if isinstance(item, dict)
    }
    target_holdings = [
        holding for holding in workspace.ensure_holdings()
        if holding.code
        and (
            ("ETF" not in goal and "etf" not in goal.lower())
            or is_etf_like_holding(holding.code, holding.name, holding.asset_type)
        )
    ]
    return [
        {"code": holding.code, "name": holding.name, "asset_type": holding.asset_type}
        for holding in target_holdings
        if holding.code not in covered
    ]


def build_holding_analysis_gate_prompt(missing: list[dict[str, Any]]) -> str:
    missing_text = ", ".join(f"{item.get('code')} {item.get('name')}" for item in missing[:20])
    return (
        "最终报告暂缓：已收集到的证据没有丢失；问题是 final_report.holding_analysis 没有逐项写入每个 ETF 的建议。"
        f"缺少 {len(missing)} 个标的：{missing_text}。"
        "下一步不要重新做无关总结；请基于已经获得的本地技术、分类、组合画像和外部搜索证据，"
        "只输出合法 JSON，type 必须是 final_report；report 可以只包含 holding_analysis、limitations、evidence，"
        "holding_analysis 只写这些缺失标的，后端会与上一版 final_report 合并。不要输出 final_report_patch。"
        "如果某个标的缺少外部证据，action_type 只能是 hold/watch，并在 reason 与 limitations 中说明证据不足。"
    )


async def run_tool_agent_events(
    config: dict[str, Any],
    *,
    goal: str,
    cached_results: list[dict[str, Any]] | None = None,
    model_override: str | None = None,
    save_snapshot: bool = True,
    save_report: bool = True,
    resume_state: dict[str, Any] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    if not config_bool(config.get("agent", {}).get("tool_agent_enabled", False)):
        log("[tool-agent] disabled by agent.tool_agent_enabled=false", level="WARN", name="tool_agent")
        yield tool_agent_event("error", "LLM 工具调用 Agent 未启用", error="agent.tool_agent_enabled=false")
        return
    if not llm_enabled(config):
        log("[tool-agent] disabled because llm.enabled=false", level="WARN", name="tool_agent")
        yield tool_agent_event("error", "LLM 未启用，无法运行工具调用 Agent", error="llm.enabled=false")
        return

    run_id = agent_run_id()
    model = model_override or config.get("llm", {}).get("model", "unknown")
    trace = AgentTraceWriter(config, run_id)
    state = resume_state or {}
    workspace = AgentWorkspace(config, cached_results=state.get("cached_results") or cached_results)
    registry = build_agent_tool_registry(config)
    schemas = tool_schemas(registry)
    use_native_tools = config_bool(config.get("agent", {}).get("use_native_tools", False))
    messages = list(state.get("messages") or build_initial_agent_messages(goal, schemas, use_native_tools=use_native_tools))
    max_turns = int(config.get("agent", {}).get("max_tool_turns", 12) or 12)
    start_turn = int(state.get("next_turn", 1) or 1)
    if resume_state:
        max_turns = start_turn + max_turns
    max_calls = int(config.get("agent", {}).get("max_tool_calls", 16) or 16)
    tool_call_count = int(state.get("tool_call_count", 0) or 0)
    reflection_required = bool(state.get("reflection_required", False))
    reflection_seen = bool(state.get("reflection_seen", False))
    last_reflection: dict[str, Any] | None = state.get("last_reflection") if isinstance(state.get("last_reflection"), dict) else None
    web_search_queries: list[str] = [str(item) for item in state.get("web_search_queries", [])]
    web_read_count = int(state.get("web_read_count", 0) or 0)
    pending_final_report: dict[str, Any] | None = (
        state.get("pending_final_report") if isinstance(state.get("pending_final_report"), dict) else None
    )

    def checkpoint(next_turn: int) -> dict[str, Any]:
        return {
            "messages": messages,
            "cached_results": workspace.technical_results,
            "next_turn": next_turn,
            "tool_call_count": tool_call_count,
            "reflection_required": reflection_required,
            "reflection_seen": reflection_seen,
            "last_reflection": last_reflection or {},
            "web_search_queries": web_search_queries,
            "web_read_count": web_read_count,
            "pending_final_report": pending_final_report or {},
        }

    def record_external_coverage(call: LlmToolCall, observation: ToolObservation) -> None:
        nonlocal web_read_count
        if observation.ok and call.name == "web_search":
            queries = (observation.result or {}).get("queries")
            if isinstance(queries, list):
                for item in queries:
                    query = str(item).strip()
                    if query:
                        web_search_queries.append(query)
            else:
                query = str((observation.result or {}).get("query") or call.arguments.get("query") or "").strip()
                if query:
                    web_search_queries.append(query)
        if observation.ok and call.name == "opencli_command":
            site = str(call.arguments.get("site", "")).strip()
            command = str(call.arguments.get("command", "")).strip()
            positionals = call.arguments.get("positionals") or []
            options = call.arguments.get("options") or {}
            query = " ".join(
                [site, command]
                + [str(item) for item in positionals if str(item).strip()]
                + [str(value) for value in options.values() if str(value).strip()]
            ).strip()
            if query:
                web_search_queries.append(query)
            result_site = str((observation.result or {}).get("site") or site)
            result_command = str((observation.result or {}).get("command") or command)
            if result_site == "web" and result_command == "read":
                web_read_count += 1
        if observation.ok and call.name == "web_read":
            web_read_count += 1

    def execute_call_batch(calls: list[LlmToolCall]) -> list[tuple[LlmToolCall, ToolObservation, float]]:
        if len(calls) <= 1 or not all(externally_slow_tool(call.name) for call in calls):
            output: list[tuple[LlmToolCall, ToolObservation, float]] = []
            for call in calls:
                started = time.monotonic()
                observation = execute_tool_call(call, registry, workspace)
                output.append((call, observation, time.monotonic() - started))
            return output
        output_by_id: dict[str, tuple[LlmToolCall, ToolObservation, float]] = {}
        max_workers = min(6, len(calls))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_map = {}
            for call in calls:
                started = time.monotonic()
                future = executor.submit(execute_tool_call, call, registry, workspace)
                future_map[future] = (call, started)
            for future in as_completed(future_map):
                call, started = future_map[future]
                try:
                    observation = future.result()
                except Exception as exc:  # noqa: BLE001
                    observation = ToolObservation(
                        call_id=call.id,
                        tool_name=call.name,
                        ok=False,
                        error_type="tool_error",
                        message=str(exc),
                    )
                output_by_id[call.id] = (call, observation, time.monotonic() - started)
        return [output_by_id[call.id] for call in calls]

    trace.write("agent_start", {"goal": goal, "model": model, "tools": list(registry)})
    trace_status = str(trace.path) if trace.enabled else "disabled"
    agent_log(
        run_id,
        (
            f"start model={model} tools={len(registry)} max_turns={max_turns} "
            f"max_calls={max_calls} cached_results={len(cached_results or [])} trace={trace_status} "
            f"goal={goal[:160]}"
        ),
    )
    yield tool_agent_event("agent_start", "继续 AI 工具调用分析" if resume_state else "开始 AI 工具调用分析", run_id=run_id)

    for turn in range(start_turn, max_turns + 1):
        trace.write("llm_request", {"turn": turn, "message_count": len(messages)})
        agent_log(run_id, f"llm_request messages={len(messages)}", turn=turn)
        yield tool_agent_event("llm_turn", "AI 正在决定下一步", run_id=run_id, turn=turn)

        try:
            started = time.monotonic()
            step = call_llm_tool_step(messages, schemas, config, model_override=model_override)
            elapsed = time.monotonic() - started
        except Exception as exc:  # noqa: BLE001
            detail = str(exc)
            if reflection_required and not detail:
                detail = "工具 observation 后必须先输出 observation_reflection"
            trace.write("error", {"turn": turn, "error": detail})
            agent_log(run_id, f"llm_response_parse_failed paused error={detail}", turn=turn, level="ERROR")
            messages.append({
                "role": "user",
                "content": (
                    "上一轮输出无法解析，运行已暂停并保留上下文。继续时请只输出一个合法 JSON 对象，"
                    "不要 Markdown，不要 channel 标记。根据当前证据继续：如果还缺信息输出 tool_calls，"
                    "如果刚收到工具 observation 输出 observation_reflection，如果信息足够输出 final_report。"
                ),
            })
            yield tool_agent_event(
                "paused",
                "LLM 输出无法解析，已暂停并保存可继续状态",
                run_id=run_id,
                turn=turn,
                error=detail,
                checkpoint=checkpoint(turn + 1),
            )
            return

        trace.write("llm_response", {"turn": turn, "type": step.type, "raw_text": step.raw_text})
        messages.append({"role": "assistant", "content": step.raw_text})
        decision_payload = llm_decision_payload(step)
        if turn == 1 and step.type != "research_plan":
            message = f"第一轮必须输出 research_plan，实际输出 {step.type}"
            trace.write("error", {"turn": turn, "error": message, "raw_text": step.raw_text})
            agent_log(run_id, message, turn=turn, level="ERROR")
            yield tool_agent_event(
                "error",
                "LLM 未先生成研究计划",
                run_id=run_id,
                turn=turn,
                error=message,
                raw_text=step.raw_text,
            )
            return
        if reflection_required and step.type != "observation_reflection":
            message = f"工具 observation 后必须先输出 observation_reflection，实际输出 {step.type}"
            trace.write("error", {"turn": turn, "error": message, "raw_text": step.raw_text})
            agent_log(run_id, message, turn=turn, level="WARN")
            yield tool_agent_event(
                "protocol_repair",
                "LLM 未先反思工具结果，已要求重新输出 observation_reflection",
                run_id=run_id,
                turn=turn,
                warning=message,
                raw_text=step.raw_text,
            )
            messages.append({
                "role": "user",
                "content": (
                    "协议纠偏：你刚收到工具 observation 后，必须先输出 observation_reflection，"
                    f"但你输出了 {step.type}。请忽略上一条格式错误的输出，"
                    "现在只输出一个合法 JSON 对象，type 必须是 observation_reflection；"
                    "不能输出 research_plan、tool_calls 或 final_report。"
                    "observation_reflection.next_action 只能是 continue_tools 或 final_report。"
                ),
            })
            continue
        if turn > 1 and step.type == "research_plan":
            message = "research_plan 只能在第一轮输出，中途重新规划会丢失执行上下文"
            trace.write("error", {"turn": turn, "error": message, "raw_text": step.raw_text})
            agent_log(run_id, message, turn=turn, level="WARN")
            yield tool_agent_event(
                "protocol_repair",
                "LLM 中途重新输出研究计划，已要求回到当前执行状态",
                run_id=run_id,
                turn=turn,
                warning=message,
                raw_text=step.raw_text,
            )
            messages.append({
                "role": "user",
                "content": (
                    "协议纠偏：research_plan 只能在第一轮输出，当前已经在执行阶段。"
                    "请忽略上一条 research_plan，基于已有 messages 和 observations 继续。"
                    "如果还缺信息，输出 tool_calls；如果刚收到工具 observation，输出 observation_reflection；"
                    "如果证据足够，输出 final_report。只能输出一个合法 JSON 对象。"
                ),
            })
            continue
        if step.type == "research_plan":
            agent_log(
                run_id,
                (
                    f"research_plan needs={len((step.research_plan or {}).get('information_needs', []) or [])} "
                    f"missing={len(step.missing_capabilities)} elapsed={elapsed:.2f}s"
                ),
                turn=turn,
            )
            yield tool_agent_event(
                "research_plan",
                "AI 已生成研究计划",
                run_id=run_id,
                turn=turn,
                decision_type=step.type,
                elapsed_seconds=round(elapsed, 2),
                reasoning_summary=step.reasoning_summary,
                thinking_trace=step.thinking_trace,
                missing_capabilities=step.missing_capabilities,
                raw_text=step.raw_text,
                parsed=decision_payload,
                research_plan=step.research_plan or {},
            )
            messages.append({"role": "user", "content": build_act_prompt()})
            continue

        if step.type == "observation_reflection":
            reflection_required = False
            reflection_seen = True
            last_reflection = step.observation_reflection or {}
            next_action = reflection_next_action(last_reflection)
            agent_log(
                run_id,
                (
                    f"observation_reflection satisfied={len(last_reflection.get('satisfied_needs', []) or [])} "
                    f"unsatisfied={len(last_reflection.get('unsatisfied_needs', []) or [])} "
                    f"next_action={next_action or '-'} elapsed={elapsed:.2f}s"
                ),
                turn=turn,
            )
            yield tool_agent_event(
                "observation_reflection",
                "AI 已反思工具结果",
                run_id=run_id,
                turn=turn,
                decision_type=step.type,
                elapsed_seconds=round(elapsed, 2),
                reasoning_summary=step.reasoning_summary,
                thinking_trace=step.thinking_trace,
                missing_capabilities=step.missing_capabilities,
                raw_text=step.raw_text,
                parsed=decision_payload,
                observation_reflection=last_reflection,
            )
            missing_codes = missing_technical_codes(workspace, goal)
            if missing_codes:
                agent_log(
                    run_id,
                    f"coverage_gate_after_reflection missing_technical={len(missing_codes)}",
                    turn=turn,
                    level="WARN",
                )
                yield tool_agent_event(
                    "coverage_gate",
                    f"仍有 {len(missing_codes)} 个标的缺少技术指标，后端自动补查",
                    run_id=run_id,
                    turn=turn,
                    missing_codes=missing_codes,
                )
                for index, code_chunk in enumerate(chunked(missing_codes, 20), start=1):
                    tool_call_count += 1
                    if tool_call_count > max_calls:
                        message = f"达到 max_tool_calls={max_calls}，Agent 未完成"
                        trace.write("error", {"turn": turn, "error": message})
                        agent_log(run_id, message, turn=turn, level="ERROR")
                        yield tool_agent_event("error", message, run_id=run_id, error=message)
                        return
                    call = LlmToolCall(
                        id=f"auto_coverage_{turn}_{index:02d}",
                        name="get_holding_technical",
                        arguments={"codes": code_chunk},
                    )
                    trace.write("tool_call", {"turn": turn, "call": call.model_dump(), "reason": "coverage_gate"})
                    yield tool_agent_event(
                        "tool_call",
                        f"补查缺失技术指标：{len(code_chunk)} 个标的",
                        run_id=run_id,
                        turn=turn,
                        tool=call.name,
                        arguments=call.arguments,
                        auto=True,
                    )
                    started = time.monotonic()
                    observation = execute_tool_call(call, registry, workspace)
                    elapsed = time.monotonic() - started
                    trace.write("tool_observation", {"turn": turn, "observation": observation.model_dump()})
                    agent_log(
                        run_id,
                        (
                            f"coverage_observation ok={observation.ok} elapsed={elapsed:.2f}s "
                            f"summary={observation.summary or observation.message}"
                        ),
                        turn=turn,
                        level="INFO" if observation.ok else "WARN",
                    )
                    yield tool_agent_event(
                        "tool_observation",
                        observation.summary or observation.message,
                        run_id=run_id,
                        turn=turn,
                        tool=call.name,
                        ok=observation.ok,
                        summary=observation.summary,
                        error_type=observation.error_type,
                        message=observation.message,
                        observation=observation.model_dump(),
                        auto=True,
                    )
                    messages.append(tool_observation_message(call, observation))
                reflection_required = True
                messages.append({"role": "user", "content": build_coverage_prompt(missing_codes)})
                messages.append({"role": "user", "content": build_reflection_prompt()})
                continue
            external_gap = external_research_gap(workspace, goal, registry, web_search_queries, web_read_count)
            if next_action == "final_report" and external_gap:
                agent_log(
                    run_id,
                    (
                        "external_research_gate_after_reflection "
                        f"missing={len(external_gap.get('missing_holding_research') or [])} "
                        f"web_read_count={web_read_count} web_search_count={len(web_search_queries)}"
                    ),
                    turn=turn,
                    level="WARN",
                )
                trace.write("coverage_gate", {"turn": turn, "type": "external_research", "gap": external_gap})
                yield tool_agent_event(
                    "coverage_gate",
                    "外部研究覆盖不足，暂缓最终报告",
                    run_id=run_id,
                    turn=turn,
                    missing_holding_research=external_gap.get("missing_holding_research", []),
                    web_read_count=web_read_count,
                    searched_queries=web_search_queries,
                )
                messages.append({"role": "user", "content": build_external_research_gate_prompt(external_gap)})
                continue
            messages.append({"role": "user", "content": build_after_reflection_prompt(last_reflection)})
            continue

        if step.type == "tool_calls":
            step.tool_calls = split_oversized_tool_calls(step.tool_calls)
            tool_names = [call.name for call in step.tool_calls]
            agent_log(
                run_id,
                f"llm_decision type=tool_calls count={len(tool_names)} tools={tool_names} elapsed={elapsed:.2f}s",
                turn=turn,
            )
            yield tool_agent_event(
                "llm_decision",
                f"AI 决定调用 {len(tool_names)} 个工具",
                run_id=run_id,
                turn=turn,
                decision_type=step.type,
                elapsed_seconds=round(elapsed, 2),
                reasoning_summary=step.reasoning_summary,
                thinking_trace=step.thinking_trace,
                missing_capabilities=step.missing_capabilities,
                raw_text=step.raw_text,
                parsed=decision_payload,
            )
        else:
            agent_log(run_id, f"llm_decision type=final_report elapsed={elapsed:.2f}s", turn=turn)
            yield tool_agent_event(
                "llm_decision",
                "AI 决定生成最终报告",
                run_id=run_id,
                turn=turn,
                decision_type=step.type,
                elapsed_seconds=round(elapsed, 2),
                reasoning_summary=step.reasoning_summary,
                thinking_trace=step.thinking_trace,
                missing_capabilities=step.missing_capabilities,
                raw_text=step.raw_text,
                parsed=decision_payload,
            )

        if step.type == "final_report":
            if pending_final_report:
                step.final_report = merge_final_report_patch(pending_final_report, step.final_report)
                pending_final_report = None
            if not reflection_seen:
                message = "final_report 前必须至少有一次 observation_reflection"
                trace.write("error", {"turn": turn, "error": message, "raw_text": step.raw_text})
                agent_log(run_id, message, turn=turn, level="ERROR")
                yield tool_agent_event("error", message, run_id=run_id, turn=turn, error=message)
                return
            if reflection_next_action(last_reflection) != "final_report":
                message = "最近一次 observation_reflection.next_action 不是 final_report，不能生成最终报告"
                trace.write("error", {"turn": turn, "error": message, "raw_text": step.raw_text})
                agent_log(run_id, message, turn=turn, level="ERROR")
                yield tool_agent_event("error", message, run_id=run_id, turn=turn, error=message)
                return
            missing_codes = missing_technical_codes(workspace, goal)
            if missing_codes:
                agent_log(
                    run_id,
                    f"final_report deferred missing_technical={len(missing_codes)}",
                    turn=turn,
                    level="WARN",
                )
                yield tool_agent_event(
                    "coverage_gate",
                    f"最终报告暂缓：仍有 {len(missing_codes)} 个标的缺少技术指标",
                    run_id=run_id,
                    turn=turn,
                    missing_codes=missing_codes,
                )
                for index, code_chunk in enumerate(chunked(missing_codes, 20), start=1):
                    tool_call_count += 1
                    if tool_call_count > max_calls:
                        message = f"达到 max_tool_calls={max_calls}，Agent 未完成"
                        trace.write("error", {"turn": turn, "error": message})
                        agent_log(run_id, message, turn=turn, level="ERROR")
                        yield tool_agent_event("error", message, run_id=run_id, error=message)
                        return
                    call = LlmToolCall(
                        id=f"auto_coverage_{turn}_{index:02d}",
                        name="get_holding_technical",
                        arguments={"codes": code_chunk},
                    )
                    trace.write("tool_call", {"turn": turn, "call": call.model_dump(), "reason": "coverage_gate"})
                    yield tool_agent_event(
                        "tool_call",
                        f"补查缺失技术指标：{len(code_chunk)} 个标的",
                        run_id=run_id,
                        turn=turn,
                        tool=call.name,
                        arguments=call.arguments,
                        auto=True,
                    )
                    started = time.monotonic()
                    observation = execute_tool_call(call, registry, workspace)
                    elapsed = time.monotonic() - started
                    trace.write("tool_observation", {"turn": turn, "observation": observation.model_dump()})
                    agent_log(
                        run_id,
                        (
                            f"coverage_observation ok={observation.ok} elapsed={elapsed:.2f}s "
                            f"summary={observation.summary or observation.message}"
                        ),
                        turn=turn,
                        level="INFO" if observation.ok else "WARN",
                    )
                    yield tool_agent_event(
                        "tool_observation",
                        observation.summary or observation.message,
                        run_id=run_id,
                        turn=turn,
                        tool=call.name,
                        ok=observation.ok,
                        summary=observation.summary,
                        error_type=observation.error_type,
                        message=observation.message,
                        observation=observation.model_dump(),
                        auto=True,
                    )
                    messages.append(tool_observation_message(call, observation))
                reflection_required = True
                messages.append({"role": "user", "content": build_coverage_prompt(missing_codes)})
                messages.append({"role": "user", "content": build_reflection_prompt()})
                continue
            external_gap = external_research_gap(workspace, goal, registry, web_search_queries, web_read_count)
            if external_gap:
                agent_log(
                    run_id,
                    (
                        "final_report deferred external_research "
                        f"missing={len(external_gap.get('missing_holding_research') or [])} "
                        f"web_read_count={web_read_count} web_search_count={len(web_search_queries)}"
                    ),
                    turn=turn,
                    level="WARN",
                )
                trace.write("coverage_gate", {"turn": turn, "type": "external_research", "gap": external_gap})
                yield tool_agent_event(
                    "coverage_gate",
                    "最终报告暂缓：外部搜索/阅读覆盖不足",
                    run_id=run_id,
                    turn=turn,
                    missing_holding_research=external_gap.get("missing_holding_research", []),
                    web_read_count=web_read_count,
                    searched_queries=web_search_queries,
                )
                reflection_required = False
                messages.append({"role": "user", "content": build_external_research_gate_prompt(external_gap)})
                continue
            missing_holding_analysis = final_report_missing_holding_analysis(workspace, goal, step.final_report)
            if missing_holding_analysis:
                agent_log(
                    run_id,
                    f"final_report deferred missing_holding_analysis={len(missing_holding_analysis)}",
                    turn=turn,
                    level="WARN",
                )
                trace.write(
                    "coverage_gate",
                    {"turn": turn, "type": "holding_analysis", "missing": missing_holding_analysis},
                )
                yield tool_agent_event(
                    "coverage_gate",
                    "最终报告暂缓：报告缺少部分 ETF 的逐项建议",
                    run_id=run_id,
                    turn=turn,
                    missing_holding_analysis=missing_holding_analysis,
                )
                pending_final_report = normalize_report_payload(step.final_report)
                messages.append({"role": "user", "content": build_holding_analysis_gate_prompt(missing_holding_analysis)})
                continue
            llm_context = workspace.build_llm_context()
            report = parse_agent_report(
                json.dumps(step.final_report or {}, ensure_ascii=False),
                [],
                llm_context.get("evidence_index", {}),
                config,
                observations=list(llm_context.get("observations", [])),
                holdings=list(llm_context.get("holdings", [])),
            )
            snapshot = workspace.build_snapshot(report, model)
            trace.write("final_report", {"turn": turn, "summary": report.get("summary", {})})
            summary = report.get("summary", {}) if isinstance(report.get("summary"), dict) else {}
            agent_log(
                run_id,
                (
                    f"final_report status={summary.get('status', '')} "
                    f"health={summary.get('health_score', '')} brief={str(summary.get('brief', ''))[:180]}"
                ),
                turn=turn,
            )
            yield tool_agent_event("final_report", "AI 已生成最终报告", run_id=run_id, turn=turn)

            if save_snapshot and config_bool(config.get("agent", {}).get("save_snapshots", True)):
                if agent_snapshots_have_same_facts(workspace.previous_snapshot(), snapshot):
                    agent_log(run_id, "snapshot skipped reason=same_facts")
                    yield tool_agent_event("save_snapshot", "事实数据未变化，跳过重复保存", run_id=run_id)
                else:
                    save_agent_snapshot(snapshot, config)
                    agent_log(run_id, "snapshot saved")
                    yield tool_agent_event("save_snapshot", "已保存 Agent 快照", run_id=run_id)
            if save_report:
                save_ai_report(workspace.technical_results, report, model, config)
                agent_log(run_id, f"ai_report saved technical_results={len(workspace.technical_results)}")

            trace.write("done", {"turn": turn})
            agent_log(run_id, "done")
            yield tool_agent_event("done", "Agent 分析完成", run_id=run_id, snapshot=snapshot)
            return

        executable_calls: list[LlmToolCall] = []
        for call in step.tool_calls:
            tool_call_count += 1
            if tool_call_count > max_calls:
                message = f"达到 max_tool_calls={max_calls}，Agent 未完成"
                trace.write("error", {"turn": turn, "error": message})
                agent_log(run_id, message, turn=turn, level="ERROR")
                yield tool_agent_event("error", message, run_id=run_id, error=message)
                return

            trace.write("tool_call", {"turn": turn, "call": call.model_dump()})
            agent_log(
                run_id,
                f"tool_call index={tool_call_count}/{max_calls} name={call.name} args={compact_for_log(call.arguments)}",
                turn=turn,
            )
            yield tool_agent_event(
                "tool_call",
                f"调用工具：{call.name}",
                run_id=run_id,
                turn=turn,
                tool=call.name,
                arguments=call.arguments,
            )
            executable_calls.append(call)

        if len(executable_calls) > 1 and all(externally_slow_tool(call.name) for call in executable_calls):
            agent_log(run_id, f"parallel_tool_batch count={len(executable_calls)}", turn=turn)

        for call, observation, elapsed in execute_call_batch(executable_calls):
            trace.write("tool_observation", {"turn": turn, "observation": observation.model_dump()})
            observation_log_level = "INFO" if observation.ok else "WARN"
            agent_log(
                run_id,
                (
                    f"tool_observation name={call.name} ok={observation.ok} elapsed={elapsed:.2f}s "
                    f"summary={observation.summary or observation.message} "
                    f"error_type={observation.error_type or '-'}"
                ),
                turn=turn,
                level=observation_log_level,
            )
            yield tool_agent_event(
                "tool_observation",
                observation.summary or observation.message,
                run_id=run_id,
                turn=turn,
                tool=call.name,
                ok=observation.ok,
                summary=observation.summary,
                error_type=observation.error_type,
                message=observation.message,
                observation=observation.model_dump(),
            )
            record_external_coverage(call, observation)
            messages.append(tool_observation_message(call, observation))

        if step.type == "tool_calls":
            reflection_required = True
            messages.append({"role": "user", "content": build_reflection_prompt()})

    message = f"达到 max_tool_turns={max_turns}，Agent 未完成"
    trace.write("error", {"error": message})
    agent_log(run_id, message, level="ERROR")
    yield tool_agent_event("error", message, run_id=run_id, error=message)
