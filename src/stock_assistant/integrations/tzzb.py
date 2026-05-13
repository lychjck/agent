import dataclasses
import datetime as dt
import json
import os
import re
import shlex
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from stock_assistant.core.config import ROOT
from stock_assistant.core.models import Holding
from stock_assistant.core.utils import extract_code, log, parse_number, pick_value, read_text_if_path, split_csv_setting

TZZB_BASE_URL = "https://tzzb.10jqka.com.cn/caishen_httpserver/tzzb"
TZZB_REFERER = "https://tzzb.10jqka.com.cn/pc/"
TZZB_ACCOUNT_LIST = "/caishen_fund/pc/account/v1/account_list"
TZZB_STOCK_POSITION = "/caishen_fund/pc/asset/v1/stock_position"
TZZB_FUND_POSITION = "/caishen_fund/pc/asset/v1/fund_position"
TZZB_UID_COOKIE_NAMES = ("userid", "user_id", "uid", "hexin_uid", "u")

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

def cookie_from_ledger_config(ledger: dict[str, Any]) -> str:
    cookie = str(ledger.get("cookie", "")).strip()
    if cookie:
        return cookie
    cookie_file = str(ledger.get("cookie_file", "")).strip()
    if cookie_file:
        return read_text_if_path(cookie_file, ROOT)
    curl_file = str(ledger.get("curl_file", "")).strip()
    if curl_file:
        cookie = extract_cookie_from_curl(read_text_if_path(curl_file, ROOT))
        if not cookie:
            raise RuntimeError(f"没有从 curl_file 里解析到 Cookie: {curl_file}")
        return cookie
    env_cookie = os.environ.get("TZZB_COOKIE", "").strip()
    if env_cookie:
        return env_cookie
    raise RuntimeError("ledger.mode=tzzb_api 需要配置 ledger.curl_file、ledger.cookie_file、ledger.cookie 或环境变量 TZZB_COOKIE")

def uid_from_cookie_header(cookie: str) -> str:
    cookies: dict[str, str] = {}
    for item in cookie.split(";"):
        if "=" not in item:
            continue
        key, value = item.split("=", 1)
        cookies[key.strip()] = value.strip()
    for name in TZZB_UID_COOKIE_NAMES:
        if cookies.get(name):
            return cookies[name]
    for key, value in cookies.items():
        if "uid" in key.lower() and value:
            return value
    return ""

def tzzb_build_params(uid: str, extra: dict[str, Any] | None = None) -> dict[str, str]:
    params = {"terminal": "1", "version": "0.0.0", "userid": uid, "user_id": uid}
    for key, value in (extra or {}).items():
        if value is not None and str(value) != "":
            params[key] = str(value)
    return params

def tzzb_post(path: str, params: dict[str, str], cookie: str, timeout: int) -> dict[str, Any]:
    body = urllib.parse.urlencode(params).encode("utf-8")
    request = urllib.request.Request(
        TZZB_BASE_URL + path,
        data=body,
        method="POST",
        headers={
            "content-type": "application/x-www-form-urlencoded",
            "origin": "https://tzzb.10jqka.com.cn",
            "referer": TZZB_REFERER,
            "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124 Safari/537.36",
            "cookie": cookie,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = response.status
            text = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        status = exc.code
        text = exc.read().decode("utf-8", errors="replace")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{path} 返回非 JSON HTTP {status}: {text[:300].replace(chr(10), ' ')}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"{path} 返回 JSON 类型异常: {type(payload).__name__}")
    return payload

def tzzb_ex_data(payload: dict[str, Any], path: str) -> Any:
    if str(payload.get("error_code")) == "0" and "ex_data" in payload:
        return payload["ex_data"]
    if "ex_data" in payload:
        return payload["ex_data"]
    message = payload.get("error_msg") or payload.get("message") or payload.get("msg") or payload
    raise RuntimeError(f"{path} 请求失败: {message}")

def walk_dicts(value: Any) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    if isinstance(value, dict):
        hits.append(value)
        for child in value.values():
            hits.extend(walk_dicts(child))
    elif isinstance(value, list):
        for child in value:
            hits.extend(walk_dicts(child))
    return hits

def find_tzzb_account_records(account_data: Any) -> list[dict[str, Any]]:
    keys = {"manual_id", "manualid", "fund_key", "rzrq_fund_key", "fundid", "fund_id", "fundId"}
    records = [record for record in walk_dicts(account_data) if keys.intersection(record)]
    seen: set[str] = set()
    output: list[dict[str, Any]] = []
    for record in records:
        marker = json.dumps(record, sort_keys=True, ensure_ascii=False, default=str)
        if marker not in seen:
            seen.add(marker)
            output.append(record)
    return output

def first_record_value(record: dict[str, Any], names: tuple[str, ...]) -> str:
    for name in names:
        value = record.get(name)
        if value is not None and str(value) != "":
            return str(value)
    return ""

def tzzb_stock_params(account: dict[str, Any]) -> dict[str, str]:
    return {
        "manual_id": first_record_value(account, ("manual_id", "manualid", "manualId")),
        "fund_key": first_record_value(account, ("fund_key", "fundKey")),
        "rzrq_fund_key": first_record_value(account, ("rzrq_fund_key", "rzrqFundKey")),
    }

def tzzb_fund_id(account: dict[str, Any]) -> str:
    return first_record_value(account, ("fundid", "fund_id", "fundId"))

def tzzb_rate_to_pct(value: Any) -> float | None:
    number = parse_number(value)
    return number * 100 if number is not None else None

def tzzb_stock_holding(row: dict[str, Any], account: dict[str, Any]) -> Holding:
    source_row = {str(key): str(value) for key, value in row.items()}
    source_row["account"] = first_record_value(account, ("manualname", "fundname"))
    return Holding(
        code=extract_code(row.get("code")),
        name=str(row.get("name") or row.get("code") or ""),
        quantity=parse_number(row.get("count")),
        cost_price=parse_number(row.get("cost")),
        market_value=parse_number(row.get("value")),
        profit_pct=tzzb_rate_to_pct(row.get("hold_rate")),
        hold_profit=parse_number(row.get("hold_profit")),
        day_profit=parse_number(row.get("w_profit")),
        source_row=source_row,
        asset_type="etf",
    )

def tzzb_fund_holding(row: dict[str, Any], account: dict[str, Any]) -> Holding:
    source_row = {str(key): str(value) for key, value in row.items()}
    source_row["account"] = first_record_value(account, ("fundname", "manualname"))
    return Holding(
        code=extract_code(row.get("code")),
        name=str(row.get("name") or row.get("code") or ""),
        quantity=parse_number(row.get("count")),
        cost_price=parse_number(row.get("cost")),
        market_value=parse_number(row.get("value")),
        profit_pct=tzzb_rate_to_pct(row.get("hold_rate")),
        hold_profit=parse_number(row.get("hold_profit")),
        day_profit=parse_number(row.get("w_profit")),
        source_row=source_row,
        asset_type="fund",
    )

def archive_tzzb_snapshot(snapshot: dict[str, Any], config: dict[str, Any]) -> Path:
    archive_dir = Path(config["paths"]["archive_dir"]).expanduser()
    archive_dir.mkdir(parents=True, exist_ok=True)
    target = archive_dir / f"{dt.datetime.now():%Y%m%d-%H%M%S}-tzzb-api.json"
    target.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    log(f"归档投资账本 API 快照: {target}")
    return target

def fetch_tzzb_holdings(config: dict[str, Any]) -> tuple[list[Holding], Path, dict[str, Any]]:
    ledger = config["ledger"]
    timeout = int(ledger.get("api_timeout_seconds", config.get("market", {}).get("timeout_seconds", 15)))
    cookie = cookie_from_ledger_config(ledger)
    uid = str(ledger.get("uid", "")).strip() or os.environ.get("TZZB_UID", "").strip() or uid_from_cookie_header(cookie)
    if not uid:
        raise RuntimeError("没有从 Cookie 中解析到 userid，请在 config.toml 的 ledger.uid 或环境变量 TZZB_UID 中指定")

    log(f"请求投资账本账户列表: account_list (uid={uid})")
    account_payload = tzzb_post(TZZB_ACCOUNT_LIST, tzzb_build_params(uid, {"userid": uid}), cookie, timeout)
    account_data = tzzb_ex_data(account_payload, TZZB_ACCOUNT_LIST)
    
    summary = {
        "total_asset": parse_number(account_data.get("total_asset")),
        "total_profit": parse_number(account_data.get("total_profit")),
        "day_profit": parse_number(account_data.get("day_profit")),
        "float_profit": parse_number(account_data.get("float_profit")),
    }
    
    accounts = find_tzzb_account_records(account_data)
    log(f"发现 {len(accounts)} 个有效账户记录")

    snapshot: dict[str, Any] = {
        "ok": True,
        "account_data": account_data,
        "accounts": accounts,
        "stock_positions": [],
        "fund_positions": [],
    }
    holdings: list[Holding] = []

    for index, account in enumerate(accounts):
        account_name = first_record_value(account, ("manualname", "fundname"))
        params = tzzb_stock_params(account)
        if any(params.values()):
            log(f"[{index+1}/{len(accounts)}] 请求股票/ETF持仓: {account_name}")
            payload = tzzb_post(TZZB_STOCK_POSITION, tzzb_build_params(uid, params), cookie, timeout)
            data = tzzb_ex_data(payload, TZZB_STOCK_POSITION)
            snapshot["stock_positions"].append({"account_index": index, "request": params, "data": data})
            position_rows = data.get("position", []) if isinstance(data, dict) else []
            log(f"  - 发现 {len(position_rows)} 条股票/ETF持仓")
            for row in position_rows:
                if isinstance(row, dict):
                    holding = tzzb_stock_holding(row, account)
                    if holding.code:
                        holdings.append(holding)

        fundid = tzzb_fund_id(account)
        if fundid:
            log(f"[{index+1}/{len(accounts)}] 请求基金持仓: {account_name}")
            payload = tzzb_post(TZZB_FUND_POSITION, tzzb_build_params(uid, {"fundid": fundid}), cookie, timeout)
            data = tzzb_ex_data(payload, TZZB_FUND_POSITION)
            snapshot["fund_positions"].append({"account_index": index, "request": {"fundid": fundid}, "data": data})
            position_rows = data.get("position", []) if isinstance(data, dict) else []
            log(f"  - 发现 {len(position_rows)} 条基金持仓")
            for row in position_rows:
                if isinstance(row, dict):
                    holding = tzzb_fund_holding(row, account)
                    if holding.code:
                        holdings.append(holding)

    if not holdings:
        raise RuntimeError("投资账本 API 返回成功，但没有解析到任何持仓")
        
    merged_dict = {}
    for h in holdings:
        key = (h.code, h.asset_type)
        if key not in merged_dict:
            merged_dict[key] = h
        else:
            existing = merged_dict[key]
            new_quantity = (existing.quantity or 0) + (h.quantity or 0)
            new_market_value = (existing.market_value or 0) + (h.market_value or 0)
            new_hold_profit = (existing.hold_profit or 0) + (h.hold_profit or 0)
            new_day_profit = (existing.day_profit or 0) + (h.day_profit or 0)
            
            new_cost_price = existing.cost_price
            new_profit_pct = existing.profit_pct
            
            if new_quantity > 0:
                total_cost_value = new_market_value - new_hold_profit
                new_cost_price = total_cost_value / new_quantity if new_quantity else 0
                new_profit_pct = (new_hold_profit / total_cost_value * 100) if total_cost_value > 0 else 0
                
            merged_dict[key] = dataclasses.replace(existing,
                quantity=new_quantity,
                market_value=new_market_value,
                hold_profit=new_hold_profit,
                day_profit=new_day_profit,
                cost_price=new_cost_price,
                profit_pct=new_profit_pct
            )
                
    merged_holdings = list(merged_dict.values())
        
    source = archive_tzzb_snapshot(snapshot, config)
    return merged_holdings, source, summary


