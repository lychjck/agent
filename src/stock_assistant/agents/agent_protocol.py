import json
from typing import Any

from stock_assistant.agents.agent_llm import agent_report_schema_hint


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
        "如果任务可能受益于用户安装的 skill、专门流程或领域方法，必须把 skill 发现纳入信息需求。"
        f"{skill_rule}"
        "协议硬性规则：所有回复必须是一个合法 JSON 对象，不要 Markdown 包裹。"
    )
    user = (
        f"用户目标：{goal}\n\n"
        f"{tool_text}"
        "需要信息时只能从给定工具列表中选择工具；信息不足时继续调用工具；信息足够时输出 final_report。"
        "每次工具 observation 之后，下一轮必须先输出 observation_reflection，不能直接输出 final_report。"
        "每次输出都要包含 reasoning_summary 和 thinking_trace；thinking_trace 只写可审计摘要，不要输出隐藏推理链。"
        "当前工具无法获取的信息必须保留在 missing_capabilities，不要臆测或假装信息足够。"
        "第一轮必须输出 research_plan，不能调用工具，不能输出 final_report。"
        "research_plan 必须先从任务出发列出 information_needs，再列 available_tool_mapping 和 missing_capabilities。\n"
        "ETF/基金分析的信息需求至少考虑：当前组合权重、标的类型、跟踪指数、底层持仓/前十大持仓、行业/区域/风格暴露、"
        "标的自身 K 线、底层核心资产趋势、同类替代品、历史变化、限制条件。"
        "研究深度要求：不能只基于本地技术指标给结论；必须把本地持仓/分类/技术面与外部搜索证据交叉验证。"
        "对每个权重>=1%的标的，information_needs 必须包含：跟踪指数/投资范围、近期新闻或驱动因素、同类或替代品、当前风险点、与组合中其他标的的重叠/相关性。"
        "对权重<1%的标的，也必须至少按行业/主题分组纳入搜索和限制说明。"
        f"{search_rule}"
        "外部搜索计划必须分成 market_context、theme_research、holding_research 三类，并在 available_tool_mapping 中映射到 opencli_command/web_search/web_read。"
        "holding_research 至少覆盖所有权重>=1%的标的；如果用户要求每个 ETF 建议，必须在 coverage_notes 逐项说明每个 ETF 是否完成外部搜索、技术指标、分类信息三类覆盖。"
        "如果 opencli_command 或 web_search/web_read 可用，这些需求必须映射到可用外部工具，而不是写成 missing_capabilities。"
        "只有需要直接访问某个 URL 时才使用底层 web_fetch。"
        "当前工具无法获取的信息必须写入 missing_capabilities，例如 ETF 底层持仓、跟踪指数、指数成分、成分股 K 线等。\n\n"
        "thinking_trace 用对象表达："
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
        "做单标的 holding_research 时，web_search 必须使用 targets=[{code,name}]，topic 可省略，每个 target 只放一个标的，单次最多 4 个 target；"
        "query 只用于市场/主题级通用检索，不要把多个持仓代码和名称塞进同一个 query。"
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
        "如果 required_tool_calls 是单标的调研，web_search 必须使用 targets 数组，单次最多 4 个 target，不要使用一坨 query 覆盖多个标的。"
        "如果还没有完成 market_context、theme_research、holding_research 三层外部研究，不要输出 final_report；继续调用 opencli_command/web_search 或读取来源页。"
        "如果当前工具无法满足某个需求，不要臆测；保留 missing_capabilities，并选择还能推进任务的可用工具。"
    )


def reflection_next_action(reflection: dict[str, Any] | None) -> str:
    if not isinstance(reflection, dict):
        return ""
    return str(reflection.get("next_action", "")).strip()
