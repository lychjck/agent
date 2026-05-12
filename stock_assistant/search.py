import datetime as dt
import json
import os
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .llm import call_llm
from .models import Holding, InstrumentClassification
from .utils import config_bool, log

ASSET_CLASSES = {
    "broad_index",       # 宽基指数基金 (如沪深300 ETF)
    "sector_equity",     # 行业权益基金 (如半导体 ETF)
    "theme_equity",      # 主题权益基金 (如碳中和 ETF)
    "active_equity",     # 主动权益基金
    "mixed_allocation",  # 混合型基金
    "bond",              # 债券 (个债)
    "bond_fund",         # 债券型基金
    "overseas",          # 海外资产基金
    "qdii",              # QDII 基金 (投资境外市场)
    "commodity",         # 商品类基金 (如黄金、豆粕 ETF)
    "cash",              # 现金及等价物
    "money_market",      # 货币市场基金
    "active_fund",       # 其他主动管理基金
    "fof",               # 基金中的基金 (Fund of Funds)
    "unknown",           # 未知
}

SECTORS = {
    "",                  # 不适用/全行业
    "financials",        # 金融 (银行、证券、保险)
    "semiconductor",     # 半导体
    "technology",        # 科技/信息技术
    "healthcare",        # 医疗保健/医药
    "consumer",          # 消费 (必选/可选消费)
    "energy",            # 能源
    "materials",         # 材料 (化工、金属、矿产)
    "industrials",       # 工业
    "military",          # 军工/航天
    "agriculture",       # 农业
    "real_estate",       # 房地产
    "infrastructure",    # 基建
    "media",             # 传媒/互联网
    "dividend",          # 红利策略 (虽然是风格，但在行业分类中常用)
    "multi_sector",      # 跨行业/多行业综合
    "unknown",           # 未知
}

STRATEGIES = {
    "passive_index",      # 被动指数策略
    "active_management",  # 主动管理策略
    "enhanced_index",     # 指数增强策略
    "mixed_allocation",   # 混合配置策略
    "bond_income",        # 债券收益策略
    "money_market",       # 货币市场策略
    "commodity_tracking", # 商品跟踪策略
    "fof",                # FOF 策略
    "unknown",            # 未知
}

class SearchProvider:
    def search(self, query: str, max_results: int) -> list[dict[str, str]]:
        raise NotImplementedError

class DisabledSearchProvider(SearchProvider):
    def search(self, query: str, max_results: int) -> list[dict[str, str]]:
        return []

class ManualJsonSearchProvider(SearchProvider):
    def __init__(self, path: Path):
        self.path = path

    def search(self, query: str, max_results: int) -> list[dict[str, str]]:
        if not self.path.exists():
            return []
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return []
        rows = payload.get(query, [])
        if not isinstance(rows, list):
            return []
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        results = []
        for row in rows[:max_results]:
            if not isinstance(row, dict):
                continue
            normalized = dict(row)
            normalized.setdefault("source", "manual_json")
            normalized.setdefault("retrieved_at", now)
            normalized.setdefault("content", normalized.get("snippet", ""))
            results.append(normalized)
        return results

class TavilySearchProvider(SearchProvider):
    def __init__(
        self,
        api_key: str,
        timeout_seconds: int,
        search_depth: str = "basic",
        topic: str = "finance",
        include_raw_content: bool | str = False,
        freshness: str = "",
        start_date: str = "",
        end_date: str = "",
    ):
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.search_depth = search_depth
        self.topic = topic
        self.include_raw_content = include_raw_content
        self.freshness = freshness
        self.start_date = start_date
        self.end_date = end_date

    def search(self, query: str, max_results: int) -> list[dict[str, str]]:
        body = {
            "query": query,
            "topic": self.topic,
            "search_depth": self.search_depth,
            "max_results": max_results,
            "include_raw_content": self.include_raw_content,
        }
        if self.freshness:
            body["time_range"] = self.freshness
        if self.start_date:
            body["start_date"] = self.start_date
        if self.end_date:
            body["end_date"] = self.end_date
        request = urllib.request.Request(
            "https://api.tavily.com/search",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}"
            },
            method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception as e:
            log(f"Tavily search failed: {e}")
            return []
        
        results = []
        retrieved_at = dt.datetime.now(dt.timezone.utc).isoformat()
        for result in payload.get("results", []):
            results.append({
                "title": result.get("title", ""),
                "url": result.get("url", ""),
                "snippet": result.get("content", ""),
                "content": result.get("content", ""),
                "raw_content": result.get("raw_content", "") or "",
                "published_date": result.get("published_date", "") or "",
                "score": str(result.get("score", "")),
                "source": "tavily",
                "retrieved_at": retrieved_at,
            })
        return results

class BraveSearchProvider(SearchProvider):
    def __init__(self, api_key: str, timeout_seconds: int, freshness: str = ""):
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.freshness = freshness

    def search(self, query: str, max_results: int) -> list[dict[str, str]]:
        params_payload: dict[str, Any] = {"q": query, "count": max_results}
        if self.freshness:
            params_payload["freshness"] = self.freshness
        params = urllib.parse.urlencode(params_payload)
        request = urllib.request.Request(
            f"https://api.search.brave.com/res/v1/web/search?{params}",
            headers={
                "Accept": "application/json",
                "Accept-Encoding": "gzip",
                "X-Subscription-Token": self.api_key
            }
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                if response.info().get('Content-Encoding') == 'gzip':
                    import gzip
                    payload = json.loads(gzip.decompress(response.read()).decode("utf-8"))
                else:
                    payload = json.loads(response.read().decode("utf-8"))
        except Exception as e:
            log(f"Brave search failed: {e}")
            return []
        
        results = []
        retrieved_at = dt.datetime.now(dt.timezone.utc).isoformat()
        for result in payload.get("web", {}).get("results", []):
            snippet_parts = [str(result.get("description", ""))]
            snippet_parts.extend(str(item) for item in result.get("extra_snippets", []) if item)
            snippet = "\n".join(part for part in snippet_parts if part).strip()
            results.append({
                "title": result.get("title", ""),
                "url": result.get("url", ""),
                "snippet": snippet,
                "content": snippet,
                "published_date": result.get("age", "") or result.get("page_age", "") or "",
                "source": "brave",
                "retrieved_at": retrieved_at,
            })
        return results

def search_freshness_for_provider(provider: str, config: dict[str, Any]) -> str:
    value = str(config.get("search", {}).get("freshness", "")).strip().lower()
    if value in {"", "none", "false", "0"}:
        return ""
    if provider == "brave":
        return {
            "day": "pd",
            "d": "pd",
            "week": "pw",
            "w": "pw",
            "month": "pm",
            "m": "pm",
            "year": "py",
            "y": "py",
        }.get(value, value)
    if provider == "tavily":
        return {
            "pd": "day",
            "pw": "week",
            "pm": "month",
            "py": "year",
        }.get(value, value)
    return value

def build_search_provider(config: dict[str, Any]) -> SearchProvider:
    search = config.get("search", {})
    if not config_bool(search.get("enabled", False)):
        return DisabledSearchProvider()
    provider = str(search.get("provider", "none")).lower()
    if provider == "none":
        return DisabledSearchProvider()
    if provider == "manual_json":
        manual_results_file = str(search.get("manual_results_file", ""))
        return ManualJsonSearchProvider(Path(manual_results_file).expanduser())
    
    timeout_seconds = int(search.get("timeout_seconds", 20))
    if provider == "tavily":
        tavily_config = search.get("providers", {}).get("tavily", {})
        api_key_env = tavily_config.get("api_key_env", "TAVILY_API_KEY")
        api_key = os.environ.get(api_key_env, "")
        if not api_key:
            raise RuntimeError(f"Tavily search enabled but API key not found in {api_key_env}")
        return TavilySearchProvider(
            api_key,
            timeout_seconds,
            search_depth=tavily_config.get("search_depth", search.get("search_depth", "basic")),
            topic=tavily_config.get("topic", "finance"),
            include_raw_content=search.get("include_raw_content", False),
            freshness=search_freshness_for_provider("tavily", config),
            start_date=str(search.get("start_date", "")).strip(),
            end_date=str(search.get("end_date", "")).strip(),
        )
        
    if provider == "brave":
        brave_config = search.get("providers", {}).get("brave", {})
        api_key_env = brave_config.get("api_key_env", "BRAVE_SEARCH_API_KEY")
        api_key = os.environ.get(api_key_env, "")
        if not api_key:
            raise RuntimeError(f"Brave search enabled but API key not found in {api_key_env}")
        return BraveSearchProvider(api_key, timeout_seconds, freshness=search_freshness_for_provider("brave", config))
        
    raise RuntimeError(f"未知搜索工具 provider: {provider}")

def get_source_tier(url: str, config: dict[str, Any]) -> int:
    domain = urllib.parse.urlparse(url).netloc.lower()
    tiers = config.get("search", {}).get("source_tiers", {})
    tier1 = [x.strip().lower() for x in str(tiers.get("tier1", "")).split(",") if x.strip()]
    tier2 = [x.strip().lower() for x in str(tiers.get("tier2", "")).split(",") if x.strip()]
    
    if any(d in domain for d in tier1):
        return 1
    if any(d in domain for d in tier2):
        return 2
    return 3

def score_classification_evidence(evidence: list[dict[str, Any]], config: dict[str, Any]) -> float:
    score = 0.0
    if not evidence:
        return 0.0
        
    if any(get_source_tier(e.get("url", ""), config) == 1 for e in evidence):
        score += 0.50
    if len(evidence) >= 2:
        score += 0.20
    if True:
        score += 0.10
    if all(get_source_tier(e.get("url", ""), config) == 3 for e in evidence):
        score -= 0.30
    return max(0.0, min(score, 1.0))

def truncate_search_text(value: Any, max_chars: int) -> str:
    text = str(value or "")
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "...[truncated]"

def search_result_evidence(result: dict[str, Any], config: dict[str, Any]) -> dict[str, str]:
    max_chars = int(config.get("search", {}).get("max_stored_content_chars", 4000))
    url = str(result.get("url", ""))
    evidence = {
        "title": truncate_search_text(result.get("title", ""), 500),
        "url": url,
        "snippet": truncate_search_text(result.get("snippet", ""), max_chars),
        "content": truncate_search_text(result.get("content", result.get("snippet", "")), max_chars),
        "raw_content": truncate_search_text(result.get("raw_content", ""), max_chars),
        "published_date": str(result.get("published_date", "")),
        "retrieved_at": str(result.get("retrieved_at", "")),
        "source": str(result.get("source", "")),
        "source_tier": str(get_source_tier(url, config)),
    }
    return {key: value for key, value in evidence.items() if value != ""}

def extract_json_object(text: str) -> dict[str, Any] | None:
    cleaned = text.strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:].strip()
    if cleaned.startswith("```"):
        cleaned = cleaned[3:].strip()
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3].strip()
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            payload = json.loads(cleaned[start:end + 1])
        except json.JSONDecodeError:
            return None
    return payload if isinstance(payload, dict) else None

def classification_llm_enabled(config: dict[str, Any]) -> bool:
    llm = config.get("classification", {}).get("llm", {})
    return config_bool(llm.get("enabled", False))

def classification_llm_config(config: dict[str, Any]) -> dict[str, Any]:
    llm = dict(config.get("llm", {}))
    llm.update({
        "enabled": True,
        "client": "urllib",
        "base_url": "http://10.33.207.193:1234/v1",
        "model": "google/gemma-4-31b",
        "api_key_env": "",
        "api_key_file": "",
        "api_key": "",
        "temperature": 0.0,
        "timeout_seconds": 120,
        "max_tokens": 2048,
        "stream": False,
        "disable_thinking": True,
        "reasoning_effort": "",
    })
    llm.update(dict(config.get("classification", {}).get("llm", {})))
    llm_config = dict(config)
    llm_config["llm"] = llm
    return llm_config

def clamp_confidence(value: Any, fallback: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = fallback
    return max(0.0, min(number, 1.0))

def classification_from_llm_result(
    holding: Holding,
    results: list[dict[str, Any]],
    model_result: dict[str, Any],
    evidence_score: float,
    config: dict[str, Any],
) -> InstrumentClassification | None:
    asset_class = str(model_result.get("asset_class", "unknown")).strip()
    sector = str(model_result.get("sector", "")).strip()
    strategy = str(model_result.get("strategy", "unknown")).strip()
    if asset_class not in ASSET_CLASSES:
        log(f"分类 LLM 返回了非法 asset_class: {asset_class}")
        return None
    if sector not in SECTORS:
        log(f"分类 LLM 返回了非法 sector: {sector}")
        return None
    if strategy not in STRATEGIES:
        log(f"分类 LLM 返回了非法 strategy: {strategy}")
        return None

    evidence = tuple(search_result_evidence(item, config) for item in results)
    confidence = clamp_confidence(model_result.get("confidence"), evidence_score)
    return InstrumentClassification(
        code=holding.code,
        name=holding.name,
        asset_class=asset_class,
        sector=sector,
        theme=str(model_result.get("theme", "")).strip(),
        region=str(model_result.get("region", "china_a")).strip() or "china_a",
        strategy=strategy,
        tracked_index=str(model_result.get("tracked_index", "")).strip(),
        issuer=str(model_result.get("issuer", "")).strip(),
        confidence=confidence,
        source="search_llm",
        evidence=evidence,
        reviewed_by_user=False,
    )

def classify_from_search_evidence_with_llm(
    holding: Holding,
    results: list[dict[str, Any]],
    config: dict[str, Any],
    evidence_score: float,
) -> InstrumentClassification | None:
    if not classification_llm_enabled(config):
        return None
    evidence = [search_result_evidence(item, config) for item in results]
    payload = {
        "instrument": {"code": holding.code, "name": holding.name},
        "taxonomy": {
            "asset_classes": sorted(ASSET_CLASSES),
            "sectors": sorted(SECTORS),
            "strategies": sorted(STRATEGIES),
        },
        "search_evidence": evidence,
        "evidence_score": evidence_score,
    }
    prompt = (
        "/no_think\n"
        "你是基金和 ETF 标的分类器。只能基于给定搜索证据分类，不要补充证据中没有的信息。\n"
        "必须只输出一个 JSON 对象，不要 markdown，不要解释。\n"
        "字段要求：\n"
        "- asset_class 必须来自 taxonomy.asset_classes。\n"
        "- sector 必须来自 taxonomy.sectors；无法确定时用 unknown 或空字符串。\n"
        "- strategy 必须来自 taxonomy.strategies。\n"
        "- confidence 是 0 到 1 的数字；证据不足时必须低于 0.65。\n"
        "- evidence_urls 只能使用 search_evidence 中出现过的 URL。\n"
        "- 主动权益/混合基金不要强行归入单一行业，优先使用 active_equity 或 mixed_allocation。\n"
        "输出 JSON schema 示例：\n"
        "{\"asset_class\":\"sector_equity\",\"sector\":\"financials\",\"theme\":\"brokerage\","
        "\"region\":\"china_a\",\"strategy\":\"passive_index\",\"tracked_index\":\"\","
        "\"issuer\":\"\",\"confidence\":0.82,\"evidence_urls\":[\"...\"]}\n\n"
        f"输入 JSON:\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )
    try:
        answer = call_llm(
            [
                {
                    "role": "system",
                    "content": "你是严格输出 JSON 的金融标的分类器。不要输出思考过程。",
                },
                {"role": "user", "content": prompt},
            ],
            classification_llm_config(config),
        )
    except Exception as exc:  # noqa: BLE001
        log(f"分类 LLM 调用失败: {exc}")
        return None

    model_result = extract_json_object(answer)
    if model_result is None:
        log(f"分类 LLM 未返回合法 JSON: {answer[:300]}")
        return None
    return classification_from_llm_result(holding, results, model_result, evidence_score, config)

def fallback_classification_from_search_rules(
    holding: Holding,
    results: list[dict[str, Any]],
    config: dict[str, Any],
    evidence_score: float,
) -> InstrumentClassification | None:
    text = " ".join(str(item.get("snippet", "")) for item in results)
    if "证券公司" not in text and "券商" not in text:
        return None
    return InstrumentClassification(
        code=holding.code,
        name=holding.name,
        asset_class="sector_equity",
        sector="financials",
        theme="brokerage",
        region="china_a",
        strategy="passive_index",
        confidence=min(evidence_score, 0.65),
        source="search_rule_fallback",
        evidence=tuple(search_result_evidence(item, config) for item in results),
    )

def suggest_classification_with_search(holding: Holding, config: dict[str, Any]) -> InstrumentClassification | None:
    provider = build_search_provider(config)
    query = f"{holding.code} {holding.name} ETF 跟踪指数 行业 基金公司"
    max_results = int(config.get("search", {}).get("max_results", 5))
    results = provider.search(query, max_results)
    if not results:
        return None
    
    score = score_classification_evidence(results, config)
    cls = (
        classify_from_search_evidence_with_llm(holding, results, config, score)
        or fallback_classification_from_search_rules(holding, results, config, score)
    )
    if cls is not None:
        from .classification import save_classification_cache
        save_classification_cache(cls, config)
        return cls
    return None
