import datetime as dt
import json
import os
import re
import shlex
from pathlib import Path
from typing import Any, Dict, List, Tuple
import urllib.parse

from stock_mcp.core import logger, ProviderError
from stock_mcp.core.http import HttpClient
from stock_mcp.domain import Holding

# --- Helpers ---
def extract_cookie_from_curl(curl_text: str) -> str:
    try:
        parts = shlex.split(curl_text)
    except ValueError:
        parts = curl_text.split()
    for index, part in enumerate(parts):
        lower = part.lower()
        if lower in {"-h", "--header"} and index + 1 < len(parts):
            header = parts[index + 1]
            if header.lower().startswith("cookie:"):
                return header.split(":", 1)[1].strip()
        if lower.startswith("cookie:"):
            return part.split(":", 1)[1].strip()
        if lower in {"-b", "--cookie"} and index + 1 < len(parts) and "=" in parts[index + 1]:
            return parts[index + 1].strip()
    for line in curl_text.splitlines():
        stripped = line.strip().strip("'\"")
        if stripped.lower().startswith("cookie:"):
            return stripped.split(":", 1)[1].strip()
    return ""

def extract_code(raw: Any) -> str:
    if not raw:
        return ""
    raw_str = str(raw).strip()
    
    # 1. 优先匹配 6 位数字代码 (A股，或基金代码，或带前缀如 1.689009)
    match_6d = re.search(r"\d{6}", raw_str)
    if match_6d:
        return match_6d.group(0)
        
    # 2. 其次匹配 5 位数字代码 (港股，如 00700 或 116.00700)
    match_5d = re.search(r"\d{5}", raw_str)
    if match_5d:
        return match_5d.group(0)
        
    # 3. 匹配美股等纯英文字符或带连字符的代码 (如 PDD, AAPL, BRK-A)
    match_us = re.search(r"[A-Za-z\-]+", raw_str)
    if match_us:
        return match_us.group(0)
        
    # 4. 兜底返回原字符串
    return raw_str

def parse_number(val: Any) -> float | None:
    if val is None or str(val).strip() == "":
        return None
    try:
        cleaned = str(val).replace("%", "").replace(",", "").strip()
        return float(cleaned)
    except ValueError:
        return None

# --- TZZB Client ---
class TzzbClient:
    BASE_URL = "https://tzzb.10jqka.com.cn/caishen_httpserver/tzzb"
    REFERER = "https://tzzb.10jqka.com.cn/pc/"
    ACCOUNT_LIST = "/caishen_fund/pc/account/v1/account_list"
    STOCK_POSITION = "/caishen_fund/pc/asset/v1/stock_position"
    FUND_POSITION = "/caishen_fund/pc/asset/v1/fund_position"
    ASSET_TREND = "/caishen_fund/pc/asset/v1/asset_trend"
    BS_POINT = "/caishen_fund/fund_quota/v1/bs_point"

    def __init__(self, config: Dict[str, Any]) -> None:
        self.config = config
        self.ledger_cfg = config.get("ledger", {}).get("tzzb", {})
        self.timeout = float(self.ledger_cfg.get("api_timeout_seconds", 30))

    def _get_cookie(self) -> str:
        # 1. config 中直接配置的 cookie
        cookie = str(self.ledger_cfg.get("cookie", "")).strip()
        if cookie:
            return cookie
        # 2. config 中指定的 cookie_file
        cookie_file = str(self.ledger_cfg.get("cookie_file", "")).strip()
        if cookie_file and Path(cookie_file).exists():
            return Path(cookie_file).read_text(encoding="utf-8").strip()
        
        # 3. .env 环境变量 TZZB_COOKIE（优先于 .tzzb-curl）
        env_cookie = os.environ.get("TZZB_COOKIE", "").strip()
        if env_cookie:
            logger.info("Using TZZB cookie from environment variable TZZB_COOKIE")
            return env_cookie

        # 4. 尝试通过 .tzzb-curl 提取
        curl_file = str(self.ledger_cfg.get("curl_file", ".tzzb-curl")).strip()
        curl_path = Path(curl_file)
        if curl_path.exists():
            cookie = extract_cookie_from_curl(curl_path.read_text(encoding="utf-8"))
            if cookie:
                logger.info(f"Using TZZB cookie extracted from curl file: {curl_file}")
                return cookie
            raise ProviderError(f"未能在 curl 临时文件中发现 Cookie: {curl_file}")
            
        raise ProviderError("未配置 TZZB Cookie，请在 .env 中设置 TZZB_COOKIE 或提供 .tzzb-curl 文件")

    def _get_uid(self, cookie: str) -> str:
        uid = str(self.ledger_cfg.get("uid", "")).strip() or os.environ.get("TZZB_UID", "").strip()
        if uid:
            return uid
        # 从 Cookie 自动提取
        cookies = {}
        for item in cookie.split(";"):
            if "=" not in item:
                continue
            k, v = item.split("=", 1)
            cookies[k.strip()] = v.strip()
        for name in ("userid", "user_id", "uid", "hexin_uid", "u"):
            if cookies.get(name):
                return cookies[name]
        for k, v in cookies.items():
            if "uid" in k.lower() and v:
                return v
        raise ProviderError("未能解析到 TZZB userid，请在配置或 TZZB_UID 环境变量中指定")

    def _post(self, path: str, params: Dict[str, str], cookie: str) -> Dict[str, Any]:
        url = self.BASE_URL + path
        headers = {
            "content-type": "application/x-www-form-urlencoded",
            "origin": "https://tzzb.10jqka.com.cn",
            "referer": self.REFERER,
            "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124 Safari/537.36",
            "cookie": cookie,
        }
        try:
            data_str = urllib.parse.urlencode(params)
            resp_str = HttpClient.request(url, method="POST", headers=headers, data=data_str, timeout=self.timeout)
            payload = json.loads(resp_str)
        except Exception as e:
            raise ProviderError(f"请求 TZZB 失败: {path} -> {e}")
        
        if str(payload.get("error_code")) != "0":
            msg = payload.get("error_msg") or payload.get("message") or "请求失败"
            raise ProviderError(f"TZZB 业务失败 {path}: {msg}")
        return payload

    def fetch_holdings(self) -> Tuple[List[Holding], Dict[str, Any]]:
        cookie = self._get_cookie()
        uid = self._get_uid(cookie)

        # 1. 账户列表
        params = {"terminal": "1", "version": "0.0.0", "userid": uid, "user_id": uid}
        logger.info(f"Fetching TZZB accounts for uid: {uid}")
        acct_payload = self._post(self.ACCOUNT_LIST, params, cookie)
        acct_data = acct_payload.get("ex_data", {})
        
        summary = {
            "total_asset": parse_number(acct_data.get("total_asset")),
            "total_profit": parse_number(acct_data.get("total_profit")),
            "day_profit": parse_number(acct_data.get("day_profit")),
            "float_profit": parse_number(acct_data.get("float_profit")),
        }

        # 遍历递归抓取账号的 records
        records = []
        def walk(val):
            if isinstance(val, dict):
                if any(k in val for k in ("manual_id", "manualid", "fund_key", "rzrq_fund_key", "fundid", "fund_id", "fundId")):
                    records.append(val)
                for child in val.values():
                    walk(child)
            elif isinstance(val, list):
                for child in val:
                    walk(child)
        walk(acct_data)

        # 去重 records
        seen = set()
        unique_records = []
        for r in records:
            marker = json.dumps(r, sort_keys=True)
            if marker not in seen:
                seen.add(marker)
                unique_records.append(r)

        holdings: List[Holding] = []
        for idx, acct in enumerate(unique_records):
            acct_name = acct.get("manualname") or acct.get("fundname") or f"Account_{idx}"
            manual_id = acct.get("manual_id") or acct.get("manualid") or acct.get("manualId")
            fund_key = acct.get("fund_key") or acct.get("fundKey")
            rzrq = acct.get("rzrq_fund_key") or acct.get("rzrqFundKey")
            
            # 2. 股票/ETF 持仓
            if manual_id or fund_key or rzrq:
                sub_params = {
                    "terminal": "1", "version": "0.0.0", "userid": uid, "user_id": uid,
                    "manual_id": str(manual_id or ""),
                    "fund_key": str(fund_key or ""),
                    "rzrq_fund_key": str(rzrq or ""),
                }
                try:
                    logger.info(f"Fetching TZZB stock positions for {acct_name}")
                    stock_payload = self._post(self.STOCK_POSITION, sub_params, cookie)
                    rows = stock_payload.get("ex_data", {}).get("position", [])
                    for row in rows:
                        code = extract_code(row.get("code"))
                        if code:
                            holdings.append(Holding(
                                code=code,
                                name=str(row.get("name") or code),
                                amount=float(parse_number(row.get("count")) or 0),
                                price=float(parse_number(row.get("price")) or 0),
                                cost=float(parse_number(row.get("cost")) or 0),
                                value=float(parse_number(row.get("value")) or 0),
                                profit=float(parse_number(row.get("hold_profit")) or 0),
                                profit_rate=float(parse_number(row.get("hold_rate")) or 0) * 100,
                                hold_days=float(parse_number(row.get("hold_days")) or 0),
                                close_profit=float(parse_number(row.get("close_profit")) or 0),
                                asset_class="Equity",
                                sector="Unknown",
                            ))
                except Exception as e:
                    logger.warn(f"Failed to fetch stock position for {acct_name}: {e}")

            # 3. 基金持仓
            fundid = acct.get("fundid") or acct.get("fund_id") or acct.get("fundId")
            if fundid:
                sub_params = {
                    "terminal": "1", "version": "0.0.0", "userid": uid, "user_id": uid,
                    "fundid": str(fundid),
                }
                try:
                    logger.info(f"Fetching TZZB fund positions for {acct_name}")
                    fund_payload = self._post(self.FUND_POSITION, sub_params, cookie)
                    rows = fund_payload.get("ex_data", {}).get("position", [])
                    for row in rows:
                        code = extract_code(row.get("code"))
                        if code:
                            holdings.append(Holding(
                                code=code,
                                name=str(row.get("name") or code),
                                amount=float(parse_number(row.get("count")) or 0),
                                price=float(parse_number(row.get("price")) or 0),
                                cost=float(parse_number(row.get("cost")) or 0),
                                value=float(parse_number(row.get("value")) or 0),
                                profit=float(parse_number(row.get("hold_profit")) or 0),
                                profit_rate=float(parse_number(row.get("hold_rate")) or 0) * 100,
                                hold_days=float(parse_number(row.get("hold_days")) or 0),
                                close_profit=float(parse_number(row.get("close_profit")) or 0),
                                asset_class="Equity",
                                sector="Unknown",
                            ))
                except Exception as e:
                    logger.warn(f"Failed to fetch fund position for {acct_name}: {e}")

        # 合并相同证券的多账号持仓
        merged: Dict[str, Holding] = {}
        for h in holdings:
            if h.code not in merged:
                merged[h.code] = h
            else:
                exist = merged[h.code]
                new_amount = exist.amount + h.amount
                new_value = exist.value + h.value
                new_profit = exist.profit + h.profit
                
                # 重新加权成本
                total_cost = new_value - new_profit
                new_cost = total_cost / new_amount if new_amount > 0 else 0
                new_profit_rate = (new_profit / total_cost * 100) if total_cost > 0 else 0
                
                # 合并 hold_days 和 close_profit
                new_hold_days = max(exist.hold_days, h.hold_days)
                new_close_profit = exist.close_profit + h.close_profit
                
                merged[h.code] = Holding(
                    code=h.code,
                    name=h.name,
                    amount=new_amount,
                    price=h.price,
                    cost=new_cost,
                    value=new_value,
                    profit=new_profit,
                    profit_rate=new_profit_rate,
                    hold_days=new_hold_days,
                    close_profit=new_close_profit,
                    asset_class=exist.asset_class,
                    sector=exist.sector,
                )
        
        return list(merged.values()), summary

    def fetch_asset_trend(self) -> Dict[str, Any]:
        cookie = self._get_cookie()
        uid = self._get_uid(cookie)
        params = {"terminal": "1", "version": "0.0.0", "userid": uid, "user_id": uid}
        res = self._post(self.ASSET_TREND, params, cookie)
        return res.get("ex_data", {})

    def fetch_bs_point(self, code: str) -> Dict[str, Any]:
        cookie = self._get_cookie()
        uid = self._get_uid(cookie)
        clean = extract_code(code)
        params = {"terminal": "1", "version": "0.0.0", "userid": uid, "user_id": uid, "fund_code": clean}
        res = self._post(self.BS_POINT, params, cookie)
        return res.get("ex_data", {})

    def fetch_fund_trans_history(self, code: str) -> List[Dict[str, Any]]:
        """拉取场外基金的真实买卖、分红、确权交易历史明细流水"""
        import datetime
        cookie = self._get_cookie()
        uid = self._get_uid(cookie)
        clean = extract_code(code)
        
        # 默认回溯从2020年至今的所有历史成交
        params = {
            "terminal": "1",
            "version": "0.0.0",
            "userid": uid,
            "user_id": uid,
            "fund_code": clean,
            "start_date": "20200101",
            "end_date": datetime.datetime.now().strftime("%Y%m%d")
        }
        res = self._post("/caishen_fund/fund_quota/v1/trans_history", params, cookie)
        return res.get("ex_data", {}).get("list", [])
