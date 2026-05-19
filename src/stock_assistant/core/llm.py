import datetime as dt
import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from stock_assistant.core.utils import compact_result_for_llm, config_bool, log


def configured_model_profiles(llm: dict[str, Any]) -> dict[str, dict[str, Any]]:
    raw_profiles = llm.get("model_profiles", {})
    profiles: dict[str, dict[str, Any]] = {}
    if isinstance(raw_profiles, dict):
        for key, value in raw_profiles.items():
            if isinstance(value, dict):
                profiles[str(key)] = dict(value)
    elif isinstance(raw_profiles, list):
        for value in raw_profiles:
            if not isinstance(value, dict):
                continue
            profile_id = str(value.get("id") or value.get("name") or "").strip()
            if profile_id:
                profiles[profile_id] = dict(value)
    return profiles


def resolve_llm_config(config: dict[str, Any], model_override: str | None = None) -> dict[str, Any]:
    llm = dict(config["llm"])
    requested_model = str(model_override or "").strip()
    if not requested_model:
        resolved_model = str(llm.get("model", "")).strip()
    else:
        profiles = configured_model_profiles(llm)
        profile = profiles.get(requested_model)
        if profile:
            llm.update(profile)
            resolved_model = str(profile.get("model") or requested_model)
        else:
            resolved_model = requested_model
    llm["model"] = resolved_model
    actual_config = dict(config)
    actual_config["llm"] = llm
    return actual_config


def llm_enabled(config: dict[str, Any]) -> bool:
    value = config.get("llm", {}).get("enabled", False)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}

def llm_api_key(llm: dict[str, Any]) -> str:
    env_name = str(llm.get("api_key_env", "")).strip()
    if env_name and os.environ.get(env_name):
        return str(os.environ[env_name]).strip()

    key_file = str(llm.get("api_key_file", "")).strip()
    if key_file:
        path = Path(key_file).expanduser()
        if path.exists():
            return path.read_text(encoding="utf-8").strip()

    inline_key = str(llm.get("api_key", "")).strip()
    if inline_key:
        return inline_key
    return ""

def openai_client_llm(
    messages: list[dict[str, str]],
    config: dict[str, Any],
    api_key: str,
    request_kwargs: dict[str, Any] | None = None,
) -> str:
    llm = config["llm"]
    base_url = str(llm["base_url"]).rstrip("/")
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("openai 包未安装；请执行 `uv sync` 或 `python3 -m pip install openai`") from exc

    client = OpenAI(api_key=api_key, base_url=base_url, timeout=float(llm["timeout_seconds"]))
    kwargs: dict[str, Any] = {
        "model": llm["model"],
        "messages": messages,
        "temperature": float(llm["temperature"]),
        "max_tokens": int(llm["max_tokens"]),
    }
    reasoning_effort = str(llm.get("reasoning_effort", "")).strip()
    if reasoning_effort:
        kwargs["reasoning_effort"] = reasoning_effort
    if request_kwargs:
        kwargs.update(request_kwargs)
    if config_bool(llm.get("stream", False)):
        answer_parts: list[str] = []
        reasoning_seen = False
        stream = client.chat.completions.create(stream=True, **kwargs)
        for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            reasoning_chunk = str(getattr(delta, "reasoning_content", "") or "")
            answer_chunk = str(getattr(delta, "content", "") or "")
            if reasoning_chunk:
                reasoning_seen = True
            if answer_chunk:
                answer_parts.append(answer_chunk)
        content = "".join(answer_parts).strip()
        if content:
            return content
        if reasoning_seen:
            raise RuntimeError("LLM stream 只返回了 reasoning_content，正文 content 为空。")
        raise RuntimeError("LLM stream 返回为空。")

    response = client.chat.completions.create(**kwargs)
    message = response.choices[0].message
    content = str(getattr(message, "content", "") or "").strip()
    if content:
        return content
    reasoning = str(getattr(message, "reasoning_content", "") or "").strip()
    if reasoning:
        raise RuntimeError("LLM 只返回了 reasoning_content，正文 content 为空。")
    raise RuntimeError("LLM 返回为空。")

def urllib_llm(
    messages: list[dict[str, str]],
    config: dict[str, Any],
    api_key: str,
    request_kwargs: dict[str, Any] | None = None,
) -> str:
    llm = config["llm"]
    base_url = str(llm["base_url"]).rstrip("/")
    disable_thinking = config_bool(llm.get("disable_thinking", False))
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    body = {
        "model": llm["model"],
        "messages": messages,
        "temperature": float(llm["temperature"]),
        "max_tokens": int(llm["max_tokens"]),
    }
    reasoning_effort = str(llm.get("reasoning_effort", "")).strip()
    if reasoning_effort:
        body["reasoning_effort"] = reasoning_effort
    if request_kwargs:
        extra_body = request_kwargs.get("extra_body")
        body.update({key: value for key, value in request_kwargs.items() if key != "extra_body"})
        if isinstance(extra_body, dict):
            body.update(extra_body)
    if disable_thinking and ("localhost" in base_url or "127.0.0.1" in base_url or "10." in base_url):
        body["enable_thinking"] = False
        body["chat_template_kwargs"] = {"enable_thinking": False}
    request = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=int(llm["timeout_seconds"])) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"LLM HTTP {exc.code}: {error_body}") from exc

    message = payload["choices"][0]["message"]
    content = str(message.get("content") or "").strip()
    if content:
        return content
    reasoning = str(message.get("reasoning_content") or message.get("reasoning") or "").strip()
    if reasoning:
        raise RuntimeError("LLM 只返回了 reasoning_content，正文 content 为空；需要继续增大 max_tokens 或在 LM Studio 里关闭 reasoning 输出。")
    raise RuntimeError(f"LLM 返回为空: {json.dumps(payload, ensure_ascii=False)[:1000]}")

def call_llm(
    messages: list[dict[str, str]],
    config: dict[str, Any],
    model_override: str | None = None,
    request_kwargs: dict[str, Any] | None = None,
) -> str:
    actual_config = resolve_llm_config(config, model_override)
    llm = actual_config["llm"]
    base_url = str(llm["base_url"]).rstrip("/")
    model = llm["model"]
    context = str(llm.get("log_context", "")).strip()
    context_suffix = f" context={context}" if context else ""
    timeout = llm.get("timeout_seconds", 120)
    log(f"正在调用 LLM: {base_url} (model={model}){context_suffix} timeout={timeout}s")
    if config_bool(llm.get("log_payload", False)):
        try:
            payload_str = json.dumps(messages, ensure_ascii=False, indent=2)
            log(f"LLM Request Payload:\n{payload_str}", name="llm_payload")
        except Exception as e:
            log(f"无法记录 LLM Payload: {e}", level="WARN")
    else:
        log(f"LLM payload logging disabled; messages={len(messages)}", name="llm_payload")

    api_key = llm_api_key(llm) or "not-needed"
    
    if str(llm.get("client", "openai")).strip().lower() == "openai":
        return openai_client_llm(messages, actual_config, api_key, request_kwargs=request_kwargs)
    return urllib_llm(messages, actual_config, api_key, request_kwargs=request_kwargs)

def generate_structured_llm_commentary(
    results: list[dict[str, Any]], 
    config: dict[str, Any], 
    model_override: str | None = None,
    snapshot_diff: dict[str, Any] | None = None,
) -> str | None:
    if not llm_enabled(config):
        return None
    log(f"准备 LLM 诊断数据，标的数量: {len(results)}")
    payload = {
        "generated_at": dt.datetime.now().isoformat(timespec="minutes"),
        "rule_engine": {
            "ma20_ma60_ma120": "最近 20/60/120 个交易日收盘价均线",
            "ret5_pct_ret20_pct": "最近 5/20 个交易日收盘价涨跌幅",
            "rsi14": "最近 14 个交易日 RSI",
            "drawdown_from_120d_high_pct": "最新收盘价相对最近 120 个交易日最高收盘价的回撤",
            "volatility20_pct": "最近 20 个交易日收益率标准差",
            "volume_ratio": "最新成交量 / 前 20 个交易日平均成交量",
            "profit_pct": "持仓收益率，优先使用持仓文件里的收益率；缺失时用最新价和成本价估算",
            "portfolio_weight_pct": "单只 ETF 市值 / 当前持仓总市值",
        },
        "holdings": [compact_result_for_llm(item) for item in results],
    }
    if snapshot_diff:
        payload["history_diff"] = snapshot_diff
    
    json_schema = '''{
  "summary": {
    "health_score": 75,
    "status": "良好", 
    "brief": "整体仓位分配合理..."
  },
  "risk_tags": ["半导体集中度高"],
  "action_items": [
    {
      "type": "reduce",
      "target": "华夏半导体ETF",
      "reason": "已积累较大涨幅且偏离均线..."
    }
  ],
  "detailed_analysis": "### 1. 资产配置评估\\n..."
}'''

    prompt = (
        "/no_think\n"
        "请作为专业投资顾问，基于下面 JSON 里的 ETF 持仓和技术指标事实，进行分析。\n"
        "要求：\n"
        "1. 必须并且只能输出 JSON 格式的结果，不要输出任何多余的废话和 markdown 包裹。\n"
        f"2. 返回的 JSON 必须严格遵守以下结构：\n{json_schema}\n"
        "3. 只能使用 JSON 中的数据，不要编造新闻、宏观信息。\n"
        "4. action_items 的 type 只能是 'reduce', 'hold', 'buy', 'rebalance' 之一。\n"
        "5. detailed_analysis 需要使用 Markdown 语法进行详细分析排版。\n\n"
        f"持仓数据 JSON:\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )
    return call_llm(
        [
            {
                "role": "system",
                "content": "你是一个严格遵循 JSON 格式输出的中文 ETF 投资顾问。不要输出除 JSON 以外的任何内容。",
            },
            {"role": "user", "content": prompt},
        ],
        config,
        model_override=model_override
    )

def generate_llm_commentary(results: list[dict[str, Any]], config: dict[str, Any]) -> str | None:
    if not llm_enabled(config):
        return None
    payload = {
        "generated_at": dt.datetime.now().isoformat(timespec="minutes"),
        "rule_engine": {
            "ma20_ma60_ma120": "最近 20/60/120 个交易日收盘价均线",
            "ret5_pct_ret20_pct": "最近 5/20 个交易日收盘价涨跌幅",
            "rsi14": "最近 14 个交易日 RSI",
            "drawdown_from_120d_high_pct": "最新收盘价相对最近 120 个交易日最高收盘价的回撤",
            "volatility20_pct": "最近 20 个交易日收益率标准差",
            "volume_ratio": "最新成交量 / 前 20 个交易日平均成交量",
            "profit_pct": "持仓收益率，优先使用持仓文件里的收益率；缺失时用最新价和成本价估算",
            "portfolio_weight_pct": "单只 ETF 市值 / 当前持仓总市值",
        },
        "holdings": [compact_result_for_llm(item) for item in results],
    }
    prompt = (
        "/no_think\n"
        "请基于下面 JSON 里的 ETF 持仓和技术指标事实，写一段中文日报解读。\n"
        "要求：\n"
        "1. 只能使用 JSON 中的数据，不要编造新闻、估值、政策、财报或宏观信息。\n"
        "2. 必须区分数据事实和你的推断。\n"
        "3. 不要写确定性收益预测，不要承诺买卖点。\n"
        "4. 输出 Markdown，包含：组合层面、单只ETF、今日动作、明日观察条件、风险提示。\n"
        "5. 动作建议只能使用保守表述，例如分批、观察、暂停加仓、再平衡，不要使用满仓/清仓/梭哈。\n\n"
        f"JSON:\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )
    return call_llm(
        [
            {
                "role": "system",
                "content": "你是严谨的中文 ETF 投资日报分析助手。你只基于用户提供的数据做解释，不编造外部事实。",
            },
            {"role": "user", "content": prompt},
        ],
        config,
    )
