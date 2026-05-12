"""天天基金 (eastmoney) 直接 API 数据获取与规则分类。

绕过搜索引擎爬虫无法解析 JS 渲染页面的问题，
直接调用天天基金的结构化数据接口来获取基金信息。
"""

import json
import re
import urllib.request
from typing import Any

from .utils import log

# ---------------------------------------------------------------------------
# 关键词 → 行业映射表
# ---------------------------------------------------------------------------
STOCK_SECTOR_KEYWORDS: dict[str, list[str]] = {
    "financials": [
        "银行", "证券", "保险", "信托", "金融",
    ],
    "materials": [
        "矿业", "矿", "铝", "铜", "钢", "铁", "锂", "钼", "锌", "镍",
        "黄金", "金属", "化工", "有色", "材料", "稀土", "能源",
    ],
    "energy": [
        "石油", "石化", "煤", "天然气", "能源",
    ],
    "technology": [
        "科技", "软件", "电子", "芯片", "半导体", "信息", "通信", "互联网",
        "计算机", "数据",
    ],
    "semiconductor": [
        "芯片", "半导体", "集成电路", "晶圆",
    ],
    "healthcare": [
        "医药", "医疗", "生物", "制药", "健康",
    ],
    "consumer": [
        "消费", "食品", "饮料", "白酒", "零售", "家电", "纺织", "服装",
    ],
    "military": [
        "军工", "航天", "国防", "航空",
    ],
    "agriculture": [
        "农业", "养殖", "种业", "农",
    ],
    "real_estate": [
        "地产", "房产", "物业",
    ],
    "infrastructure": [
        "基建", "建筑", "水利", "交通", "铁路", "公路",
    ],
    "media": [
        "传媒", "游戏", "影视", "广告",
    ],
}

# 基金名称关键词 → asset_class
NAME_ASSET_CLASS_RULES: list[tuple[list[str], str]] = [
    (["货币", "现金"], "money_market"),
    (["债券", "纯债", "信用债", "利率债", "短债", "中债"], "bond_fund"),
    (["QDII", "qdii"], "qdii"),
    (["FOF", "fof", "基金中基金"], "fof"),
    (["ETF联接", "ETF 联接"], "broad_index"),
]

# 基金名称关键词 → strategy
NAME_STRATEGY_RULES: list[tuple[list[str], str]] = [
    (["被动", "指数", "ETF"], "passive_index"),
    (["增强"], "enhanced_index"),
    (["混合"], "mixed_allocation"),
    (["货币", "现金"], "money_market"),
    (["债券", "纯债", "信用债", "短债", "中债"], "bond_income"),
    (["FOF", "fof", "基金中基金"], "fof"),
    (["黄金", "豆粕", "商品", "原油"], "commodity_tracking"),
]


def _http_get(url: str, timeout: int = 15) -> str | None:
    """简单的 HTTP GET，失败返回 None。"""
    try:
        request = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (compatible; StockAssistant/1.0)",
            "Referer": "https://fund.eastmoney.com/",
        })
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001
        log(f"eastmoney HTTP GET 失败: {url} -> {exc}")
        return None


# ---------------------------------------------------------------------------
# 解析 pingzhongdata JS
# ---------------------------------------------------------------------------
def parse_pingzhongdata_js(text: str) -> dict[str, Any]:
    """解析 fund.eastmoney.com/pingzhongdata/{code}.js 返回的 JS 变量。

    这个 JS 文件以 var xxx = "yyy"; 或 var xxx = [...]; 形式提供数据。
    我们只提取分类需要的关键字段。
    """
    result: dict[str, Any] = {}

    # 提取字符串变量: var fS_name = "华宝资源优选混合C";
    for match in re.finditer(r'var\s+(\w+)\s*=\s*"([^"]*)"', text):
        result[match.group(1)] = match.group(2)

    # 提取数组变量: var stockCodes = ["xxx","yyy"];
    for match in re.finditer(r'var\s+(stockCodes|stockCodesNew|zqCodes|zqCodesNew)\s*=\s*(\[.*?\])', text):
        try:
            result[match.group(1)] = json.loads(match.group(2))
        except json.JSONDecodeError:
            pass

    # 提取 zqCodes 字符串形式: var zqCodes = "1136991";
    if "zqCodes" not in result:
        m = re.search(r'var\s+zqCodes\s*=\s*"([^"]*)"', text)
        if m:
            val = m.group(1).strip()
            result["zqCodes"] = [val] if val else []

    # 提取布尔: var ishb=false;
    m = re.search(r'var\s+ishb\s*=\s*(true|false)', text)
    if m:
        result["ishb"] = m.group(1) == "true"

    # 提取仓位数据 (只取最后一个点位来判断股票仓位)
    m = re.search(r'var\s+Data_fundSharesPositions\s*=\s*(\[\[.*?\]\])', text)
    if m:
        try:
            positions = json.loads(m.group(1))
            if positions:
                result["latest_equity_pct"] = float(positions[-1][1])
        except (json.JSONDecodeError, IndexError, TypeError):
            pass

    return result


def fetch_fund_info(code: str, timeout: int = 15) -> dict[str, Any] | None:
    """从天天基金获取基金品种数据。"""
    url = f"http://fund.eastmoney.com/pingzhongdata/{code}.js"
    text = _http_get(url, timeout)
    if not text:
        return None
    info = parse_pingzhongdata_js(text)
    if not info.get("fS_code"):
        log(f"eastmoney pingzhongdata 无有效数据: {code}")
        return None
    return info


# ---------------------------------------------------------------------------
# 解析持仓明细
# ---------------------------------------------------------------------------
def parse_fund_holdings_html(text: str) -> list[dict[str, str]]:
    """解析 FundArchivesDatas 返回的数据，提取重仓股信息。

    API 返回格式: var apidata={ content:"<div>...</div>",binddata:...}
    HTML 中每只股票以两个连续 <a> 标签出现：
      <td><a href='//quote.eastmoney.com/unify/r/1.601899'>601899</a></td>
      <td class='tol'><a href='...'>紫金矿业</a></td>

    返回 [{"code": "601899", "name": "紫金矿业"}, ...]
    """
    holdings: list[dict[str, str]] = []
    # 匹配: <a href='//quote.eastmoney.com/...'>601899</a></td><td ...><a href='...'>紫金矿业</a>
    for match in re.finditer(
        r"<a\s+href=['\"]//quote\.eastmoney\.com/[^'\"]*['\"]>(\d{6})</a>"
        r"</td>\s*<td[^>]*>\s*<a\s+href=['\"]//quote\.eastmoney\.com/[^'\"]*['\"]>([^<]+)</a>",
        text,
    ):
        holdings.append({"code": match.group(1), "name": match.group(2)})
    return holdings


def fetch_fund_holdings(code: str, timeout: int = 15) -> list[dict[str, str]]:
    """从天天基金获取前十大重仓股。"""
    url = f"https://fundf10.eastmoney.com/FundArchivesDatas.aspx?type=jjcc&code={code}&topline=10"
    text = _http_get(url, timeout)
    if not text:
        return []
    return parse_fund_holdings_html(text)


# ---------------------------------------------------------------------------
# 基于结构化数据的规则分类
# ---------------------------------------------------------------------------
def _match_keywords(text: str, keywords: list[str]) -> bool:
    return any(kw in text for kw in keywords)


def infer_asset_class_from_name(name: str) -> str:
    """从基金名称推断 asset_class。"""
    for keywords, asset_class in NAME_ASSET_CLASS_RULES:
        if _match_keywords(name, keywords):
            return asset_class

    # 基于名称中的 "混合" / "股票" / "灵活配置" 等
    if "混合" in name or "灵活配置" in name:
        return "mixed_allocation"
    if "股票" in name:
        return "active_equity"
    return ""


def infer_strategy_from_name(name: str) -> str:
    """从基金名称推断 strategy。"""
    for keywords, strategy in NAME_STRATEGY_RULES:
        if _match_keywords(name, keywords):
            return strategy
    return ""


def infer_sector_from_holdings(
    holdings: list[dict[str, str]],
    fund_name: str = "",
) -> tuple[str, list[str]]:
    """从重仓股名称推断行业，返回 (sector, matched_reasons)。

    逻辑：统计每个行业关键词命中了多少只重仓股，
    如果某行业命中 >= 3 只则判定为该行业。
    否则如果有多个行业各命中 1-2 只，判定为 multi_sector。
    """
    sector_hits: dict[str, list[str]] = {}
    all_names = [h["name"] for h in holdings]
    # 也考虑基金名本身
    combined_text = " ".join(all_names) + " " + fund_name

    for sector, keywords in STOCK_SECTOR_KEYWORDS.items():
        matched = [name for name in all_names if _match_keywords(name, keywords)]
        if matched:
            sector_hits[sector] = matched

    if not sector_hits:
        return "", []

    # 取命中最多的行业
    best_sector = max(sector_hits, key=lambda s: len(sector_hits[s]))
    best_count = len(sector_hits[best_sector])
    total = len(holdings) if holdings else 1

    reasons = [f"{best_sector}: {', '.join(sector_hits[best_sector])}"]

    # 如果最大命中 >= 50% 的持仓，判定为该行业
    if best_count >= max(3, total * 0.5):
        return best_sector, reasons

    # 有多个行业各有命中，判定为 multi_sector
    if len(sector_hits) >= 2:
        for s, names in sector_hits.items():
            if s != best_sector:
                reasons.append(f"{s}: {', '.join(names)}")
        return "multi_sector", reasons

    # 单一行业但命中数不够多
    if best_count >= 2:
        return best_sector, reasons

    return "", reasons


def classify_fund_by_structured_data(
    code: str,
    name: str,
    fund_info: dict[str, Any] | None,
    holdings: list[dict[str, str]],
) -> dict[str, Any]:
    """基于天天基金结构化数据做规则分类。

    返回:
        {
            "asset_class": str,
            "sector": str,
            "strategy": str,
            "theme": str,
            "region": str,
            "issuer": str,
            "confidence": float,
            "classification_reasons": [str, ...],
            "structured_evidence": {...},
        }
    """
    reasons: list[str] = []
    fund_name = name or (fund_info or {}).get("fS_name", "")

    # --- asset_class ---
    asset_class = infer_asset_class_from_name(fund_name)
    if asset_class:
        reasons.append(f"asset_class from name '{fund_name}': {asset_class}")
    else:
        # 从仓位数据推断
        equity_pct = (fund_info or {}).get("latest_equity_pct")
        if equity_pct is not None:
            if equity_pct >= 80:
                asset_class = "active_equity"
                reasons.append(f"asset_class from equity_pct={equity_pct}%: active_equity")
            elif equity_pct >= 40:
                asset_class = "mixed_allocation"
                reasons.append(f"asset_class from equity_pct={equity_pct}%: mixed_allocation")
            elif equity_pct <= 10:
                asset_class = "bond_fund"
                reasons.append(f"asset_class from equity_pct={equity_pct}%: bond_fund")
            else:
                asset_class = "mixed_allocation"
                reasons.append(f"asset_class from equity_pct={equity_pct}%: mixed_allocation (default)")

    if not asset_class:
        asset_class = "unknown"
        reasons.append("asset_class: unknown (insufficient data)")

    # --- strategy ---
    strategy = infer_strategy_from_name(fund_name)
    if strategy:
        reasons.append(f"strategy from name '{fund_name}': {strategy}")
    else:
        strategy = "active_management"
        reasons.append("strategy: active_management (default for non-index fund)")

    # --- sector ---
    sector = ""
    sector_reasons: list[str] = []
    if holdings:
        sector, sector_reasons = infer_sector_from_holdings(holdings, fund_name)
        if sector:
            reasons.append(f"sector from top holdings: {sector}")
            reasons.extend(f"  - {r}" for r in sector_reasons)
        else:
            reasons.append("sector: could not determine from holdings")

    if not sector:
        sector = ""
        # 尝试从基金名称推断
        for s, keywords in STOCK_SECTOR_KEYWORDS.items():
            if _match_keywords(fund_name, keywords):
                sector = s
                reasons.append(f"sector from name '{fund_name}': {s}")
                break

    # --- theme ---
    theme = ""
    if "资源" in fund_name:
        theme = "resources"
        reasons.append(f"theme from name: resources")
    elif "新能源" in fund_name:
        theme = "new_energy"
    elif "碳中和" in fund_name:
        theme = "carbon_neutral"
    elif "红利" in fund_name or "高股息" in fund_name:
        theme = "dividend"

    # --- region ---
    region = "china_a"
    if any(kw in fund_name for kw in ["美国", "纳斯达克", "标普", "港股", "恒生", "海外"]):
        region = "overseas"
        reasons.append(f"region from name: overseas")

    # --- issuer ---
    issuer = ""
    issuer_prefixes = [
        "华宝", "华夏", "易方达", "南方", "广发", "招商", "博时", "工银",
        "嘉实", "富国", "天弘", "中欧", "汇添富", "兴证", "鹏华", "国泰",
        "银华", "交银", "景顺", "中银", "建信", "大成", "长信", "华安",
        "万家", "前海", "平安", "诺德", "诺安", "长城",
    ]
    for prefix in issuer_prefixes:
        if fund_name.startswith(prefix):
            issuer = f"{prefix}基金"
            break

    # --- confidence ---
    confidence = 0.5
    if holdings:
        confidence += 0.2
    if fund_info:
        confidence += 0.1
    if sector:
        confidence += 0.1
    confidence = min(confidence, 0.95)

    # --- 构造结构化证据摘要 ---
    structured_evidence = {
        "fund_code": code,
        "fund_name": fund_name,
    }
    if fund_info:
        structured_evidence["equity_position_pct"] = fund_info.get("latest_equity_pct")
        structured_evidence["return_1y"] = fund_info.get("syl_1n")
        structured_evidence["return_6m"] = fund_info.get("syl_6y")
        structured_evidence["return_3m"] = fund_info.get("syl_3y")
        structured_evidence["is_money_fund"] = fund_info.get("ishb", False)
        stock_codes = fund_info.get("stockCodesNew") or fund_info.get("stockCodes") or []
        structured_evidence["stock_codes_count"] = len(stock_codes)
        bond_codes = fund_info.get("zqCodes") or []
        if isinstance(bond_codes, str):
            bond_codes = [bond_codes] if bond_codes else []
        structured_evidence["bond_codes_count"] = len(bond_codes)

    if holdings:
        structured_evidence["top_holdings"] = [
            {"code": h["code"], "name": h["name"]} for h in holdings[:10]
        ]

    return {
        "asset_class": asset_class,
        "sector": sector,
        "strategy": strategy,
        "theme": theme,
        "region": region,
        "issuer": issuer,
        "confidence": confidence,
        "classification_reasons": reasons,
        "structured_evidence": structured_evidence,
    }


def fetch_and_classify(
    code: str,
    name: str,
    timeout: int = 15,
) -> dict[str, Any] | None:
    """完整流程：获取天天基金数据 + 规则分类。

    返回 None 表示无法获取数据（网络故障等）。
    """
    log(f"eastmoney 直接 API 获取: {name} ({code})")
    fund_info = fetch_fund_info(code, timeout)
    holdings = fetch_fund_holdings(code, timeout)

    if not fund_info and not holdings:
        log(f"eastmoney 直接 API 无数据: {name} ({code})")
        return None

    result = classify_fund_by_structured_data(code, name, fund_info, holdings)
    log(
        f"eastmoney 规则分类: {name} ({code}) "
        f"asset_class={result['asset_class']} sector={result['sector']} "
        f"strategy={result['strategy']} confidence={result['confidence']:.2f} "
        f"top_holdings={len(holdings)}"
    )
    return result
