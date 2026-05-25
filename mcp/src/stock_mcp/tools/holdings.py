import os
import json
import re
import urllib.request
from collections import Counter
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from stock_mcp.registry import registry
from stock_mcp.context import ToolContext
from stock_mcp.providers import TzzbClient, extract_code
from stock_mcp.analytics.portfolio import summarize_portfolio
from stock_mcp.domain.holding import Holding
from stock_mcp.core import logger
from stock_mcp.providers.eastmoney_fund import EastmoneyFundClient

# --- Local File Cache for Stock Industries to Prevent HTTP 456 ---
CACHE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "data"))
CACHE_FILE = os.path.join(CACHE_DIR, "stock_industry_cache.json")
STOCK_INDUSTRY_CACHE: Dict[str, str] = {}
_cache_dirty = False

def load_stock_industry_cache():
    global STOCK_INDUSTRY_CACHE
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                STOCK_INDUSTRY_CACHE = json.load(f)
            logger.info(f"Loaded {len(STOCK_INDUSTRY_CACHE)} stock industry cache entries from {CACHE_FILE}")
        except Exception as e:
            logger.warning(f"Failed to load stock industry cache: {e}")
            STOCK_INDUSTRY_CACHE = {}
    else:
        STOCK_INDUSTRY_CACHE = {}

def save_stock_industry_cache():
    global _cache_dirty
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(STOCK_INDUSTRY_CACHE, f, ensure_ascii=False, indent=2)
        logger.info(f"Saved {len(STOCK_INDUSTRY_CACHE)} stock industry cache entries to {CACHE_FILE}")
        _cache_dirty = False
    except Exception as e:
        logger.warning(f"Failed to save stock industry cache: {e}")

# --- Pydantic Argument Schemas ---

class CurrentHoldingsArgs(BaseModel):
    fields: Optional[List[str]] = Field(None, description="过滤返回字段的白名单")
    asset_class: Optional[str] = Field(None, description="按资产大类过滤，例如：个股、货币基金、债券基金、行业主题、宽基指数")
    sector: Optional[str] = Field(None, description="按二级细分板块/行业过滤，例如：医药医疗、半导体、固定收益、银行等")
    min_profit_rate: Optional[float] = Field(None, description="最低盈亏比例(%)，筛选在此之上的盈利标的")
    max_profit_rate: Optional[float] = Field(None, description="最高盈亏比例(%)，筛选在此之下的亏损标的")
    include_trace: Optional[bool] = Field(False, description="是否在返回结果中包含分类推理链路数据（classification_trace）")

class PortfolioProfileArgs(BaseModel):
    include: Optional[List[str]] = Field(None, description="要计算包含的画像指标")

class ClassificationArgs(BaseModel):
    codes: List[str] = Field(..., description="标的代码列表")

class AccountBundleArgs(BaseModel):
    fields: Optional[List[str]] = Field(None, description="持仓白名单字段")
    include: Optional[List[str]] = Field(None, description="画像白名单指标")

# --- Simplified High-Precision Read-Only Asset Classifier ---

def fetch_stock_industry(code: str, timeout: float = 10.0) -> str:
    """直接查询新浪财经获取股票最权威的官方申万行业分类（包含本地永久缓存拦截，消灭 HTTP 456 限流）"""
    global _cache_dirty
    clean = extract_code(code)
    if not clean:
        return "Unknown"
    
    # 优先从本地缓存读取
    if clean in STOCK_INDUSTRY_CACHE:
        val = STOCK_INDUSTRY_CACHE[clean]
        if val:
            return val
            
    url = f"http://vip.stock.finance.sina.com.cn/corp/go.php/vCI_CorpOtherInfo/stockid/{clean}.phtml"
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    }
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw_bytes = response.read()
            try:
                text = raw_bytes.decode("gbk")
            except Exception:
                text = raw_bytes.decode("utf-8", errors="ignore")
        
        # 精准匹配申万行业分类表格内容
        match = re.search(r"所属行业板块.*?<td[^>]*class=\"ct\"[^>]*>([^<]+)</td>", text, re.S)
        if match:
            industry = match.group(1).strip()
            if industry:
                STOCK_INDUSTRY_CACHE[clean] = industry
                _cache_dirty = True
                return industry
    except Exception as e:
        logger.warning(f"Failed to fetch stock industry for {clean}: {e}")
    
    # 抓取失败、解析不出或触发 456 时，我们依然写盘缓存为 "Unknown" 并设 _cache_dirty = True
    # 这样可永久消除对该个股代码的网络轮询抓取，避开新浪 456 防刷机制，使后续的网络请求数完全归零！
    STOCK_INDUSTRY_CACHE[clean] = "Unknown"
    _cache_dirty = True
    return "Unknown"

def get_classifications_for_holdings(holdings: List[Holding], config: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """高精度混合资产与分类器（中文化大类与本地缓存优化版）"""
    # 首步：加载个股行业本地永久缓存
    load_stock_industry_cache()
    
    classifications = {}
    fund_client = EastmoneyFundClient(config)
    timeout = float(config.get("market", {}).get("timeout_seconds", 15))
    
    for h in holdings:
        code = h.code
        clean = extract_code(code)
        if not clean:
            continue
            
        # 1. 尝试从天天基金 JS 获取大类和名称
        base_info = fund_client.fetch_fund_base_info(clean)
        
        # 2. 判断是否是基金：如果 base_info 拿到了，或者以基金特征前缀开头
        is_fund = base_info is not None or any(clean.startswith(prefix) for prefix in ("15", "16", "18", "50", "51", "56", "58"))
        
        if not is_fund:
            # 股票直查行业
            industry = fetch_stock_industry(clean, timeout=timeout)
            classifications[code] = {
                "code": code,
                "primary_class": "个股",
                "sector": industry,
                "source": "sina_finance_industry",
                "confidence": 1.0 if industry != "Unknown" else 0.5
            }
        else:
            # 基金分类逻辑
            official_type = base_info.get("official_type", "") if base_info else ""
            fund_name = base_info.get("name", h.name or "") if base_info else (h.name or "")
            
            # 货币基金闪电定性
            if "货币" in official_type:
                classifications[code] = {
                    "code": code,
                    "primary_class": "货币基金",
                    "sector": "货币资金",
                    "source": "official_fund_type",
                    "confidence": 1.0,
                    "name": fund_name
                }
            # 债券基金闪电定性
            elif "债券" in official_type:
                classifications[code] = {
                    "code": code,
                    "primary_class": "债券基金",
                    "sector": "固定收益",
                    "source": "official_fund_type",
                    "confidence": 1.0,
                    "name": fund_name
                }
            # 权益类/偏股型基金三层漏斗高精度分类
            else:
                # 步骤一：名字强特征行业与市场属性直连拦截（高置信度，秒级避让穿透干扰）
                name_mappings = {
                    "半导体|芯片|集成电路": ("半导体", "行业主题"),
                    "医药|医疗|健康|生物|疫苗|新药|创新药": ("医药医疗", "行业主题"),
                    "消费|白酒|食品|饮料|酒|消费品": ("消费食品", "行业主题"),
                    "新能源|光伏|电池|锂电|绿色能源": ("新能源光伏", "行业主题"),
                    "军工|航天|国防": ("高端制造", "行业主题"),
                    "证券|券商|银行|金融|保险": ("金融地产", "行业主题"),
                    "有色|黄金|贵金属|金属|矿业|资源|能源|电力|化工|煤炭|石油": ("周期资源", "行业主题"),
                    "纳指|纳斯达克|标普|S&P|恒生|HS|港股通|日经|德国|美股|海外": ("海外宽基", "宽基指数"),
                    "300|500|1000|800|科创|创业板|双创|红利|低波|上证|国证|深证|中证|综合|价值|大盘|中小盘|A500|A50|180|A股|50ETF|50指数": ("宽基大盘", "宽基指数")
                }
                
                matched_sec = None
                matched_pclass = None
                for pattern, (sec, pclass) in name_mappings.items():
                    if re.search(pattern, fund_name, re.I):
                        matched_sec = sec
                        matched_pclass = pclass
                        break
                
                # 步骤二：无强特征名字，则穿透前五重仓股多数票决（深度挖掘主动管理型基金的真实科技暴露）
                voted_sec = None
                voted_pclass = None
                confidence = 0.7
                source = "name_keyword_fallback"
                
                if matched_sec is None:
                    try:
                        constituents = fund_client.fetch_fund_holdings(clean)
                    except Exception:
                        constituents = []
                        
                    if constituents:
                        # 批量穿透前 5 大重仓股的行业属性
                        industries = []
                        for stock in constituents[:5]:
                            stock_code = extract_code(stock.get("code"))
                            if stock_code:
                                ind = fetch_stock_industry(stock_code, timeout=timeout)
                                if ind and ind != "Unknown":
                                    industries.append(ind)
                                    
                        if industries:
                            counter = Counter(industries)
                            most_common_industry, count = counter.most_common(1)[0]
                            # 多数票决规则：如果有 2 个及以上的股票属于同一申万板块，定义为该行业主题
                            if count >= 2:
                                voted_sec = most_common_industry
                                voted_pclass = "行业主题"
                                confidence = round(count / len(industries), 2)
                                source = "penetrate_majority_vote"
                            else:
                                voted_sec = "宽基大盘"
                                voted_pclass = "宽基指数"
                                confidence = 0.9
                                source = "penetrate_majority_vote"
                
                # 步骤三：合并输出，辅以大盘宽基做最终安全兜底
                final_sector = matched_sec or voted_sec or "宽基大盘"
                final_pclass = matched_pclass or voted_pclass or "宽基指数"
                final_source = "name_keyword_direct" if matched_sec else source
                final_confidence = 0.95 if matched_sec else confidence
                
                classifications[code] = {
                    "code": code,
                    "primary_class": final_pclass,
                    "sector": final_sector,
                    "source": final_source,
                    "confidence": final_confidence,
                    "name": fund_name
                }
                    
    # 如果本次运行周期中获取到了有效的个股申万行业，一次性写入本地永久 JSON 缓存
    if _cache_dirty:
        save_stock_industry_cache()
        
    return classifications

# --- Tool Handlers ---

@registry.register("get_current_holdings", "获取投资账本当前持仓列表", CurrentHoldingsArgs)
def get_current_holdings(args: CurrentHoldingsArgs, ctx: ToolContext) -> dict:
    client = TzzbClient(ctx.config)
    holdings, _ = client.fetch_holdings()
    
    classifications = get_classifications_for_holdings(holdings, ctx.config)
    
    # 附加分类信息
    for h in holdings:
        c = classifications.get(h.code)
        if c:
            h.asset_class = c["primary_class"]
            h.sector = c["sector"]
            
    # 执行多维高精度条件过滤
    filtered_holdings = holdings
    if args.asset_class:
        filtered_holdings = [h for h in filtered_holdings if h.asset_class == args.asset_class]
    if args.sector:
        filtered_holdings = [h for h in filtered_holdings if h.sector == args.sector]
    if args.min_profit_rate is not None:
        filtered_holdings = [h for h in filtered_holdings if h.profit_rate >= args.min_profit_rate]
    if args.max_profit_rate is not None:
        filtered_holdings = [h for h in filtered_holdings if h.profit_rate <= args.max_profit_rate]
        
    # 转化模型为字典，并动态注入 classification_trace 推理链路
    res = []
    for h in filtered_holdings:
        h_dict = h.model_dump()
        
        if args.include_trace:
            c = classifications.get(h.code)
            if c:
                h_dict["classification_trace"] = {
                    "source": c.get("source"),
                    "confidence": c.get("confidence"),
                    "official_name": c.get("name")
                }
        res.append(h_dict)
        
    # 执行字段白名单投影过滤
    if args.fields:
        # 如果要求了 fields，需要确保 include_trace 时的 classification_trace 不被意外裁剪掉
        allowed_fields = set(args.fields)
        if args.include_trace:
            allowed_fields.add("classification_trace")
        res = [{k: item[k] for k in allowed_fields if k in item} for item in res]
        
    return {"ok": True, "count": len(res), "holdings": res}

@registry.register("get_portfolio_profile", "根据持仓计算行业与资产分布画像", PortfolioProfileArgs)
def get_portfolio_profile(args: PortfolioProfileArgs, ctx: ToolContext) -> dict:
    client = TzzbClient(ctx.config)
    holdings, _ = client.fetch_holdings()
    
    classifications = get_classifications_for_holdings(holdings, ctx.config)
    for h in holdings:
        c = classifications.get(h.code)
        if c:
            h.asset_class = c["primary_class"]
            h.sector = c["sector"]
            
    profile = summarize_portfolio(holdings)
    return {"ok": True, "portfolio": profile}

@registry.register("get_classification", "获取指定证券资产与行业分类特征", ClassificationArgs)
def get_classification(args: ClassificationArgs, ctx: ToolContext) -> dict:
    dummy_holdings = [Holding(code=c, name="", amount=0) for c in args.codes]
    res = get_classifications_for_holdings(dummy_holdings, ctx.config)
    return {"ok": True, "classifications": res}

@registry.register("get_current_account_bundle", "一键大礼包：聚合大盘持仓、组合画像及证券分类Facts", AccountBundleArgs)
def get_current_account_bundle(args: AccountBundleArgs, ctx: ToolContext) -> dict:
    logger.info("Executing get_current_account_bundle (unified facts request)")
    tzzb = TzzbClient(ctx.config)
    
    holdings, _ = tzzb.fetch_holdings()
    
    classifications = get_classifications_for_holdings(holdings, ctx.config)
    for h in holdings:
        c = classifications.get(h.code)
        if c:
            h.asset_class = c["primary_class"]
            h.sector = c["sector"]
            
    profile = summarize_portfolio(holdings)
    
    h_dicts = [h.model_dump() for h in holdings]
    if args.fields:
        h_dicts = [{k: h[k] for k in args.fields if k in h} for h in h_dicts]
        
    return {
        "ok": True,
        "holdings": h_dicts,
        "portfolio_profile": profile,
        "classifications": classifications
    }

