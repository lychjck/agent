#!/usr/bin/env python3
"""Daily ETF position assistant.

This tool opens an investment ledger, waits for/export-downloads a holdings
file, fetches daily ETF K-lines, and writes a rule-based portfolio report.
"""

from __future__ import annotations

import argparse
import csv
import dataclasses
import datetime as dt
import glob
import json
import math
import os
import re
import shlex
import statistics
import sys
import time
import tomllib
import urllib.parse
import urllib.request
import urllib.error
import webbrowser
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree


ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = ROOT / "config.toml"


DEFAULTS: dict[str, Any] = {
    "paths": {
        "download_dir": str(ROOT / "downloads"),
        "report_dir": str(ROOT / "reports"),
        "archive_dir": str(ROOT / "data" / "holdings"),
    },
    "ledger": {
        "url": "",
        "mode": "manual",
        "download_glob": "*.csv,*.xlsx",
        "wait_seconds": 600,
        "open_browser": True,
        "curl_file": "",
        "cookie_file": "",
        "cookie": "",
        "uid": "",
        "api_timeout_seconds": 30,
    },
    "columns": {
        "code": "证券代码,基金代码,代码,产品代码,symbol,code",
        "name": "证券名称,基金名称,名称,产品名称,name",
        "quantity": "持仓数量,可用份额,持有份额,数量,份额,quantity,shares",
        "cost_price": "成本价,持仓成本价,买入均价,成本,cost_price",
        "market_value": "持仓市值,市值,最新市值,market_value,value",
        "profit_pct": "收益率,持仓收益率,盈亏比例,profit_pct,return_pct",
    },
    "market": {
        "provider": "sina",
        "history_days": 260,
        "timeout_seconds": 15,
    },
    "analysis": {
        "min_history_days": 80,
        "loss_alert_pct": -8.0,
        "gain_trim_pct": 20.0,
        "max_single_position_pct": 35.0,
    },
    "llm": {
        "enabled": False,
        "client": "openai",
        "base_url": "https://api-inference.modelscope.cn/v1",
        "model": "deepseek-ai/DeepSeek-V4-Pro",
        "api_key_env": "MODELSCOPE_API_KEY",
        "api_key_file": "",
        "temperature": 0.2,
        "timeout_seconds": 900,
        "max_tokens": 65536,
        "stream": True,
        "disable_thinking": False,
        "reasoning_effort": "",
    },
}


def log(message: str) -> None:
    now = dt.datetime.now().strftime("%H:%M:%S")
    print(f"[{now}] {message}", file=sys.stderr, flush=True)


@dataclasses.dataclass(frozen=True)
class Holding:
    code: str
    name: str
    quantity: float | None = None
    cost_price: float | None = None
    market_value: float | None = None
    profit_pct: float | None = None
    hold_profit: float | None = None
    day_profit: float | None = None
    source_row: dict[str, str] = dataclasses.field(default_factory=dict)
    asset_type: str = "etf"


@dataclasses.dataclass(frozen=True)
class Bar:
    date: dt.date
    open: float
    close: float
    high: float
    low: float
    volume: float
    amount: float
    pct_change: float


TZZB_BASE_URL = "https://tzzb.10jqka.com.cn/caishen_httpserver/tzzb"
TZZB_REFERER = "https://tzzb.10jqka.com.cn/pc/"
TZZB_ACCOUNT_LIST = "/caishen_fund/pc/account/v1/account_list"
TZZB_STOCK_POSITION = "/caishen_fund/pc/asset/v1/stock_position"
TZZB_FUND_POSITION = "/caishen_fund/pc/asset/v1/fund_position"
TZZB_UID_COOKIE_NAMES = ("userid", "user_id", "uid", "hexin_uid", "u")


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config(path: Path) -> dict[str, Any]:
    load_env_file(ROOT / ".env")
    config = DEFAULTS
    if path.exists():
        log(f"读取配置: {path}")
        with path.open("rb") as fh:
            config = deep_merge(DEFAULTS, tomllib.load(fh))
    else:
        log(f"未找到配置文件: {path}，使用内置默认配置。")
    return config


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"'")
        if key and key not in os.environ:
            os.environ[key] = value


def ensure_dirs(config: dict[str, Any]) -> None:
    for key in ("download_dir", "report_dir", "archive_dir"):
        Path(config["paths"][key]).expanduser().mkdir(parents=True, exist_ok=True)


def split_csv_setting(value: str | list[str]) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [item.strip() for item in str(value).split(",") if item.strip()]


def normalize_header(value: str) -> str:
    return re.sub(r"[\s_（）()%()]+", "", value.strip().lower())


def parse_number(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text in {"--", "-", "nan", "None"}:
        return None
    text = text.replace(",", "").replace("，", "").replace("%", "")
    text = text.replace("元", "").replace("份", "").replace("股", "")
    try:
        return float(text)
    except ValueError:
        return None


def extract_code(value: Any) -> str:
    if value is None:
        return ""
    match = re.search(r"(?<!\d)(\d{6})(?!\d)", str(value))
    return match.group(1) if match else str(value).strip().upper()


def pick_value(row: dict[str, str], aliases: str | list[str]) -> str:
    normalized_row = {normalize_header(key): value for key, value in row.items()}
    for alias in split_csv_setting(aliases):
        hit = normalized_row.get(normalize_header(alias))
        if hit not in (None, ""):
            return hit
    return ""


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    raw = path.read_bytes()
    last_error: UnicodeDecodeError | None = None
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "big5"):
        try:
            text = raw.decode(encoding)
            reader = csv.DictReader(text.splitlines())
            return [{str(k): str(v or "") for k, v in row.items()} for row in reader]
        except UnicodeDecodeError as exc:
            last_error = exc
    raise ValueError(f"无法识别 CSV 编码: {path}") from last_error


def xlsx_cell_value(cell: ElementTree.Element, shared: list[str], ns: dict[str, str]) -> str:
    value_node = cell.find("main:v", ns)
    if value_node is None or value_node.text is None:
        inline = cell.find("main:is/main:t", ns)
        return inline.text if inline is not None and inline.text else ""
    value = value_node.text
    if cell.attrib.get("t") == "s":
        index = int(value)
        return shared[index] if 0 <= index < len(shared) else ""
    return value


def read_xlsx_rows(path: Path) -> list[dict[str, str]]:
    ns = {"main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(path) as archive:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
            for item in root.findall("main:si", ns):
                shared.append("".join(node.text or "" for node in item.findall(".//main:t", ns)))

        sheet_names = sorted(name for name in archive.namelist() if re.match(r"xl/worksheets/sheet\d+\.xml", name))
        if not sheet_names:
            raise ValueError(f"XLSX 中没有工作表: {path}")
        sheet = ElementTree.fromstring(archive.read(sheet_names[0]))

    table: list[list[str]] = []
    for row in sheet.findall(".//main:row", ns):
        cells: dict[int, str] = {}
        for cell in row.findall("main:c", ns):
            ref = cell.attrib.get("r", "")
            col = column_index(ref)
            cells[col] = xlsx_cell_value(cell, shared, ns)
        if cells:
            max_col = max(cells)
            table.append([cells.get(index, "") for index in range(max_col + 1)])

    header = next((row for row in table if any(value.strip() for value in row)), [])
    if not header:
        return []
    rows = []
    for values in table[table.index(header) + 1 :]:
        row = {header[index]: values[index] if index < len(values) else "" for index in range(len(header))}
        if any(value.strip() for value in row.values()):
            rows.append(row)
    return rows


def column_index(cell_ref: str) -> int:
    letters = re.sub(r"[^A-Z]", "", cell_ref.upper())
    index = 0
    for letter in letters:
        index = index * 26 + ord(letter) - ord("A") + 1
    return max(index - 1, 0)


def read_table(path: Path) -> list[dict[str, str]]:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return read_csv_rows(path)
    if suffix == ".xlsx":
        return read_xlsx_rows(path)
    raise ValueError(f"暂不支持的持仓文件格式: {path.suffix}")


def parse_holdings(path: Path, config: dict[str, Any]) -> list[Holding]:
    rows = read_table(path)
    columns = config["columns"]
    holdings: list[Holding] = []
    for row in rows:
        code = extract_code(pick_value(row, columns["code"]))
        name = pick_value(row, columns["name"]) or code
        if not code:
            continue
        holdings.append(
            Holding(
                code=code,
                name=name,
                quantity=parse_number(pick_value(row, columns["quantity"])),
                cost_price=parse_number(pick_value(row, columns["cost_price"])),
                market_value=parse_number(pick_value(row, columns["market_value"])),
                profit_pct=parse_number(pick_value(row, columns["profit_pct"])),
                source_row=row,
            )
        )
    if not holdings:
        raise ValueError(f"没有从持仓文件中解析到证券代码: {path}")
    return holdings


def read_text_if_path(value: str) -> str:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = ROOT / path
    return path.read_text(encoding="utf-8").strip()


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
        return read_text_if_path(cookie_file)
    curl_file = str(ledger.get("curl_file", "")).strip()
    if curl_file:
        cookie = extract_cookie_from_curl(read_text_if_path(curl_file))
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
    
    # 提取官方汇总数据
    summary = {
        "total_asset": parse_number(account_data.get("total_asset")),
        "total_profit": parse_number(account_data.get("total_profit")),
        "day_profit": parse_number(account_data.get("day_profit")),
        "float_profit": parse_number(account_data.get("float_profit")),
    }
    
    accounts = find_tzzb_account_records(account_data)
    log(f"发现 {len(accounts)} 个有效账户记录")
    # ...

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
                
            import dataclasses
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


def newest_file(patterns: list[str], directories: list[Path], since: float | None = None) -> Path | None:
    candidates: list[Path] = []
    for directory in directories:
        for pattern in patterns:
            candidates.extend(Path(item) for item in glob.glob(str(directory / pattern)))
    existing = [path for path in candidates if path.is_file() and (since is None or path.stat().st_mtime >= since)]
    return max(existing, key=lambda path: path.stat().st_mtime) if existing else None


def wait_for_download(config: dict[str, Any], since: float) -> Path:
    ledger = config["ledger"]
    patterns = split_csv_setting(ledger["download_glob"])
    dirs = [Path(config["paths"]["download_dir"]).expanduser(), Path.home() / "Downloads"]
    deadline = time.time() + int(ledger["wait_seconds"])
    log(f"等待新的持仓文件: patterns={patterns}, dirs={[str(path) for path in dirs]}")
    log("如果浏览器已经打开，请登录投资账本并导出持仓 CSV/XLSX。")
    while time.time() < deadline:
        hit = newest_file(patterns, dirs, since=since)
        if hit:
            log(f"发现持仓文件: {hit}")
            return hit
        time.sleep(3)
    raise TimeoutError(f"等待持仓文件超时，目录: {', '.join(str(path) for path in dirs)}")


def open_ledger_and_download(config: dict[str, Any]) -> Path:
    ledger = config["ledger"]
    started_at = time.time()
    if ledger.get("mode") == "playwright":
        return download_with_playwright(config, started_at)

    url = str(ledger.get("url", "")).strip()
    if url and bool(ledger.get("open_browser", True)):
        log(f"打开投资账本: {url}")
        webbrowser.open(url)
    elif not url:
        log("ledger.url 为空，无法自动打开投资账本。你需要手动打开账本并导出持仓文件。")
    return wait_for_download(config, started_at)


def download_with_playwright(config: dict[str, Any], started_at: float) -> Path:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("playwright 未安装；请先执行 `python3 -m pip install playwright` 和 `python3 -m playwright install chromium`") from exc

    ledger = config["ledger"]
    url = str(ledger.get("url", "")).strip()
    if not url:
        raise ValueError("ledger.url 为空，无法用 Playwright 打开投资账本")

    download_dir = Path(config["paths"]["download_dir"]).expanduser()
    selectors = split_csv_setting(ledger.get("download_selectors", ""))
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()
        page.goto(url, wait_until="domcontentloaded")
        if selectors:
            with page.expect_download(timeout=int(ledger["wait_seconds"]) * 1000) as download_info:
                for selector in selectors:
                    page.locator(selector).first.click()
            download = download_info.value
            target = download_dir / download.suggested_filename
            download.save_as(target)
            browser.close()
            return target
        browser.close()
    return wait_for_download(config, started_at)


def archive_holding_file(path: Path, config: dict[str, Any]) -> Path:
    archive_dir = Path(config["paths"]["archive_dir"]).expanduser()
    archive_dir.mkdir(parents=True, exist_ok=True)
    stamped = f"{dt.datetime.now():%Y%m%d-%H%M%S}-{path.name}"
    target = archive_dir / stamped
    target.write_bytes(path.read_bytes())
    log(f"归档持仓文件: {target}")
    return target


def secid_for_cn_etf(code: str) -> str:
    clean = extract_code(code)
    if clean.startswith(("50", "51", "52", "56", "58", "60", "68", "90")):
        return f"1.{clean}"
    if clean.startswith(("00", "12", "15", "16", "18", "30", "39")):
        return f"0.{clean}"
    return f"1.{clean}" if clean.startswith("6") else f"0.{clean}"


def sina_symbol_for_cn_etf(code: str) -> str:
    secid = secid_for_cn_etf(code)
    market, clean = secid.split(".", 1)
    return ("sh" if market == "1" else "sz") + clean


def fetch_sina_bars(code: str, config: dict[str, Any]) -> list[Bar]:
    market = config["market"]
    params = {
        "symbol": sina_symbol_for_cn_etf(code),
        "scale": "240",
        "ma": "no",
        "datalen": str(int(market["history_days"])),
    }
    url = "https://quotes.sina.cn/cn/api/openapi.php/CN_MarketDataService.getKLineData?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://finance.sina.com.cn/",
            "Accept": "application/json,text/plain,*/*",
        },
    )
    with urllib.request.urlopen(request, timeout=int(market["timeout_seconds"])) as response:
        payload = json.loads(response.read().decode("utf-8"))
    rows = payload.get("result", {}).get("data") or []
    bars: list[Bar] = []
    for row in rows:
        bars.append(
            Bar(
                date=dt.date.fromisoformat(str(row["day"])),
                open=float(row["open"]),
                close=float(row["close"]),
                high=float(row["high"]),
                low=float(row["low"]),
                volume=float(row.get("volume") or 0),
                amount=float(row.get("amount") or 0),
                pct_change=0.0,
            )
        )
    bars.sort(key=lambda bar: bar.date)
    for index in range(1, len(bars)):
        previous = bars[index - 1].close
        if previous:
            bars[index] = dataclasses.replace(bars[index], pct_change=(bars[index].close / previous - 1) * 100)
    return bars


def fetch_eastmoney_bars(code: str, config: dict[str, Any]) -> list[Bar]:
    market = config["market"]
    end = dt.date.today()
    begin = end - dt.timedelta(days=int(market["history_days"]) * 2)
    params = {
        "secid": secid_for_cn_etf(code),
        "ut": "fa5fd1943c7b386f172d6893dbfba10b",
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "klt": "101",
        "fqt": "1",
        "beg": begin.strftime("%Y%m%d"),
        "end": end.strftime("%Y%m%d"),
    }
    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=int(market["timeout_seconds"])) as response:
        payload = json.loads(response.read().decode("utf-8"))
    klines = payload.get("data", {}).get("klines") or []
    bars: list[Bar] = []
    for line in klines:
        parts = str(line).split(",")
        if len(parts) < 11:
            continue
        bars.append(
            Bar(
                date=dt.date.fromisoformat(parts[0]),
                open=float(parts[1]),
                close=float(parts[2]),
                high=float(parts[3]),
                low=float(parts[4]),
                volume=float(parts[5]),
                amount=float(parts[6]),
                pct_change=float(parts[8]),
            )
        )
    bars.sort(key=lambda bar: bar.date)
    return bars


def fetch_wangge_bars(code: str, config: dict[str, Any]) -> list[Bar]:
    market = config["market"]
    # 转换为 wangge 接口需要的格式，如果是 6 开头加 SH. 等
    symbol = extract_code(code)
    # yinglian.site 的 /api/klines/daily?symbol=xxx
    url = f"https://yinglian.site/api/klines/daily?symbol={symbol}"
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=int(market["timeout_seconds"])) as response:
        payload = json.loads(response.read().decode("utf-8"))
    
    rows = payload.get("data") or []
    bars: list[Bar] = []
    for row in rows:
        bars.append(
            Bar(
                date=dt.datetime.strptime(row["timestamp"].split(" ")[0], "%Y-%m-%d").date(),
                open=float(row["open"]),
                close=float(row["close"]),
                high=float(row["high"]),
                low=float(row["low"]),
                volume=float(row.get("volume") or 0),
                amount=float(row.get("amount") or 0),
                pct_change=float(row.get("change_pct") or 0),
            )
        )
    bars.sort(key=lambda bar: bar.date)
    return bars


def fetch_bars(code: str, config: dict[str, Any]) -> list[Bar]:
    provider = str(config["market"].get("provider", "sina")).lower()
    if provider == "wangge" or provider == "yinglian":
        try:
            return fetch_wangge_bars(code, config)
        except Exception as e:
            log(f"Wangge 行情获取失败: {e}，尝试切换备用源")
    if provider == "eastmoney":
        try:
            return fetch_eastmoney_bars(code, config)
        except Exception:
            return fetch_sina_bars(code, config)
    if provider == "sina":
        try:
            return fetch_sina_bars(code, config)
        except Exception:
            return fetch_eastmoney_bars(code, config)
    raise ValueError(f"未知行情源: {provider}")


def moving_average(values: list[float], window: int) -> float | None:
    if len(values) < window:
        return None
    return sum(values[-window:]) / window


def pct_change(values: list[float], window: int) -> float | None:
    if len(values) <= window or values[-window - 1] == 0:
        return None
    return (values[-1] / values[-window - 1] - 1) * 100


def rsi(values: list[float], window: int = 14) -> float | None:
    if len(values) <= window:
        return None
    changes = [values[index] - values[index - 1] for index in range(1, len(values))]
    recent = changes[-window:]
    gains = [max(change, 0) for change in recent]
    losses = [abs(min(change, 0)) for change in recent]
    avg_gain = sum(gains) / window
    avg_loss = sum(losses) / window
    if avg_loss == 0:
        return 100.0
    return 100 - 100 / (1 + avg_gain / avg_loss)


def max_drawdown_from_high(values: list[float], window: int = 120) -> float | None:
    recent = values[-window:]
    if not recent:
        return None
    high = max(recent)
    if high == 0:
        return None
    return (recent[-1] / high - 1) * 100


def volatility(values: list[float], window: int = 20) -> float | None:
    if len(values) <= window:
        return None
    returns = [(values[index] / values[index - 1] - 1) * 100 for index in range(len(values) - window, len(values))]
    return statistics.pstdev(returns) if len(returns) >= 2 else None


def fmt(value: float | None, suffix: str = "", digits: int = 2) -> str:
    if value is None or (isinstance(value, float) and (math.isnan(value) or math.isinf(value))):
        return "-"
    return f"{value:.{digits}f}{suffix}"


def get_attr(obj: Any, key: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def compact_result_for_llm(item: dict[str, Any]) -> dict[str, Any]:
    holding = item["holding"]
    latest = item.get("latest")
    return {
        "code": get_attr(holding, "code"),
        "name": get_attr(holding, "name"),
        "quantity": get_attr(holding, "quantity"),
        "cost_price": get_attr(holding, "cost_price"),
        "market_value": get_attr(holding, "market_value"),
        "latest_date": str(get_attr(latest, "date")) if latest else None,
        "latest_close": get_attr(latest, "close"),
        "daily_pct_change": get_attr(latest, "pct_change"),
        "ma20": item.get("ma20"),
        "ma60": item.get("ma60"),
        "ma120": item.get("ma120"),
        "ret5_pct": item.get("ret5"),
        "ret20_pct": item.get("ret20"),
        "rsi14": item.get("rsi14"),
        "drawdown_from_120d_high_pct": item.get("drawdown"),
        "volatility20_pct": item.get("vol20"),
        "volume_ratio": item.get("vol_ratio"),
        "profit_pct": item.get("profit_pct"),
        "portfolio_weight_pct": item.get("weight"),
        "rule_action": item.get("action"),
        "rule_reason": item.get("reason"),
    }


def llm_enabled(config: dict[str, Any]) -> bool:
    value = config.get("llm", {}).get("enabled", False)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def config_bool(value: Any) -> bool:
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


def openai_client_llm(messages: list[dict[str, str]], config: dict[str, Any], api_key: str) -> str:
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


def urllib_llm(messages: list[dict[str, str]], config: dict[str, Any], api_key: str) -> str:
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


def call_llm(messages: list[dict[str, str]], config: dict[str, Any], model_override: str | None = None) -> str:
    llm = config["llm"]
    base_url = str(llm["base_url"]).rstrip("/")
    model = model_override or llm["model"]
    log(f"正在调用 LLM: {base_url} (model={model})")
    api_key = llm_api_key(llm)
    
    # ... keep remaining logic but use the local 'model' variable ...
    if not api_key and (base_url.startswith("https://api-inference.modelscope.cn") or base_url.startswith("https://easyrouter.io")):
        env_name = str(llm.get("api_key_env", "")).strip() or "LLM_API_KEY"
        raise RuntimeError(f"LLM API key 未配置，请设置环境变量 {env_name} 或在 .env 中写入 {env_name}=...")
    
    # 修改原本直接从 llm["model"] 取值的逻辑，改为使用 model 变量
    actual_config = config.copy()
    actual_config["llm"] = llm.copy()
    actual_config["llm"]["model"] = model
    
    if str(llm.get("client", "openai")).strip().lower() == "openai":
        return openai_client_llm(messages, actual_config, api_key)
    return urllib_llm(messages, actual_config, api_key)


def generate_structured_llm_commentary(results: list[dict[str, Any]], config: dict[str, Any], model_override: str | None = None) -> str | None:
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
        "请作为专业投资顾问，基于下面 JSON 里的 ETF 持仓、技术指标和规则信号，进行分析。\n"
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
        "请基于下面 JSON 里的 ETF 持仓、技术指标和规则信号，写一段中文日报解读。\n"
        "要求：\n"
        "1. 只能使用 JSON 中的数据，不要编造新闻、估值、政策、财报或宏观信息。\n"
        "2. 必须区分数据事实、规则信号和你的推断。\n"
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


def analyze_one(holding: Holding, bars: list[Bar], config: dict[str, Any], total_value: float | None) -> dict[str, Any]:
    min_days = int(config["analysis"]["min_history_days"])
    if len(bars) < min_days:
        return {
            "holding": holding,
            "ok": False,
            "action": "数据不足",
            "reason": f"K 线只有 {len(bars)} 条，低于阈值 {min_days}",
        }

    closes = [bar.close for bar in bars]
    volumes = [bar.volume for bar in bars]
    latest = bars[-1]
    ma20 = moving_average(closes, 20)
    ma60 = moving_average(closes, 60)
    ma120 = moving_average(closes, 120)
    ret5 = pct_change(closes, 5)
    ret20 = pct_change(closes, 20)
    rsi14 = rsi(closes)
    drawdown = max_drawdown_from_high(closes)
    vol20 = volatility(closes)
    vol_ratio = None
    if len(volumes) >= 21 and moving_average(volumes[:-1], 20):
        vol_ratio = volumes[-1] / moving_average(volumes[:-1], 20)

    profit_pct = holding.profit_pct
    if profit_pct is None and holding.cost_price and holding.cost_price > 0:
        profit_pct = (latest.close / holding.cost_price - 1) * 100

    current_value = holding.market_value
    if current_value is None and holding.quantity:
        current_value = holding.quantity * latest.close
    weight = current_value / total_value * 100 if current_value and total_value else None

    action, reasons = decide_action(
        latest.close,
        ma20,
        ma60,
        ma120,
        rsi14,
        drawdown,
        profit_pct,
        weight,
        config,
    )
    return {
        "holding": holding,
        "ok": True,
        "latest": latest,
        "ma20": ma20,
        "ma60": ma60,
        "ma120": ma120,
        "ret5": ret5,
        "ret20": ret20,
        "rsi14": rsi14,
        "drawdown": drawdown,
        "vol20": vol20,
        "vol_ratio": vol_ratio,
        "profit_pct": profit_pct,
        "current_value": current_value,
        "weight": weight,
        "action": action,
        "reason": "；".join(reasons),
    }


def decide_action(
    close: float,
    ma20: float | None,
    ma60: float | None,
    ma120: float | None,
    rsi14: float | None,
    drawdown: float | None,
    profit_pct: float | None,
    weight: float | None,
    config: dict[str, Any],
) -> tuple[str, list[str]]:
    reasons: list[str] = []
    risk_reasons: list[str] = []
    buy_reasons: list[str] = []

    if profit_pct is not None and profit_pct <= float(config["analysis"]["loss_alert_pct"]):
        risk_reasons.append(f"持仓收益 {profit_pct:.2f}% 已触发亏损警戒")
    if weight is not None and weight >= float(config["analysis"]["max_single_position_pct"]):
        risk_reasons.append(f"单只仓位 {weight:.2f}% 偏高")
    if ma60 is not None and close < ma60:
        risk_reasons.append("收盘价低于 MA60，中期趋势偏弱")
    if ma20 is not None and ma60 is not None and ma20 < ma60:
        risk_reasons.append("MA20 低于 MA60，短中期均线未修复")
    if drawdown is not None and drawdown <= -12:
        risk_reasons.append(f"距 120 日高点回撤 {drawdown:.2f}%")
    if rsi14 is not None and rsi14 >= 75:
        risk_reasons.append(f"RSI14={rsi14:.2f}，短线过热")

    if ma20 is not None and ma60 is not None and close > ma20 > ma60:
        buy_reasons.append("价格站上 MA20 且 MA20 高于 MA60")
    if ma120 is not None and close > ma120:
        buy_reasons.append("价格位于 MA120 上方")
    if rsi14 is not None and 45 <= rsi14 <= 68:
        buy_reasons.append(f"RSI14={rsi14:.2f}，未明显过热")

    if len(risk_reasons) >= 2:
        return "减仓/暂停加仓", risk_reasons
    if risk_reasons:
        return "持有观察", risk_reasons + buy_reasons[:1]
    if len(buy_reasons) >= 2:
        reasons.extend(buy_reasons)
        return "可分批加仓", reasons
    return "持有观察", buy_reasons or ["趋势信号不充分"]


def analyze_holdings(holdings: list[Holding], config: dict[str, Any]) -> list[dict[str, Any]]:
    total_value = sum(item.market_value or 0 for item in holdings) or None
    results: list[dict[str, Any]] = []
    for holding in holdings:
        if holding.asset_type == "fund":
            results.append({
                "holding": holding, 
                "ok": True, 
                "action": "持有场外基金", 
                "reason": "场外基金，不参与K线分析",
                "profit_pct": holding.profit_pct,
                "current_value": holding.market_value,
                "weight": holding.market_value / total_value * 100 if holding.market_value and total_value else None
            })
            continue
        try:
            log(f"拉取行情并分析: {holding.code} {holding.name}")
            bars = fetch_bars(holding.code, config)
            results.append(analyze_one(holding, bars, config, total_value))
        except Exception as exc:  # noqa: BLE001 - report should continue for other holdings.
            results.append({"holding": holding, "ok": False, "action": "行情失败", "reason": str(exc)})
    return results


def report_markdown(results: list[dict[str, Any]], source_file: Path, llm_commentary: str | None = None) -> str:
    today = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    ok_count = sum(1 for item in results if item.get("ok"))
    lines = [
        f"# ETF 持仓日报 - {today}",
        "",
        f"- 持仓文件: `{source_file}`",
        f"- 已分析: {ok_count}/{len(results)}",
        "- 说明: 本报告是基于 K 线和持仓数据的规则化风险提示，不构成投资建议或收益承诺。",
        "",
        "## 总览",
        "",
        "| 代码 | 名称 | 最新价 | 5日 | 20日 | 持仓收益 | 仓位 | 动作 | 依据 |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | --- | --- |",
    ]
    for item in results:
        holding: Holding = item["holding"]
        latest = item.get("latest")
        lines.append(
            "| {code} | {name} | {price} | {ret5} | {ret20} | {profit} | {weight} | {action} | {reason} |".format(
                code=holding.code,
                name=holding.name,
                price=fmt(latest.close if latest else None),
                ret5=fmt(item.get("ret5"), "%"),
                ret20=fmt(item.get("ret20"), "%"),
                profit=fmt(item.get("profit_pct"), "%"),
                weight=fmt(item.get("weight"), "%"),
                action=item.get("action", "-"),
                reason=str(item.get("reason", "-")).replace("|", "/"),
            )
        )

    if llm_commentary:
        lines.extend(["", "## AI 综合解读", "", llm_commentary.strip(), ""])

    lines.extend(["", "## 明细", ""])
    for item in results:
        holding = item["holding"]
        lines.extend([f"### {holding.code} {holding.name}", ""])
        if not item.get("ok"):
            lines.extend([f"- 状态: {item.get('action')}", f"- 原因: {item.get('reason')}", ""])
            continue
        latest: Bar = item["latest"]
        lines.extend(
            [
                f"- 最新交易日: {latest.date}, 收盘价: {fmt(latest.close)}, 当日涨跌: {fmt(latest.pct_change, '%')}",
                f"- 均线: MA20={fmt(item.get('ma20'))}, MA60={fmt(item.get('ma60'))}, MA120={fmt(item.get('ma120'))}",
                f"- 动量: 5日={fmt(item.get('ret5'), '%')}, 20日={fmt(item.get('ret20'), '%')}, RSI14={fmt(item.get('rsi14'))}",
                f"- 风险: 120日高点回撤={fmt(item.get('drawdown'), '%')}, 20日波动率={fmt(item.get('vol20'), '%')}, 量比={fmt(item.get('vol_ratio'), '', 2)}",
                f"- 建议动作: {item.get('action')}",
                f"- 依据: {item.get('reason')}",
                "",
            ]
        )
    return "\n".join(lines)


def write_report(markdown: str, config: dict[str, Any]) -> Path:
    report_dir = Path(config["paths"]["report_dir"]).expanduser()
    report_dir.mkdir(parents=True, exist_ok=True)
    target = report_dir / f"{dt.date.today():%Y-%m-%d}-etf-report.md"
    target.write_text(markdown, encoding="utf-8")
    log(f"写入报告: {target}")
    return target


def run(config: dict[str, Any], holdings_file: Path | None) -> Path:
    ensure_dirs(config)
    if holdings_file:
        log(f"使用指定持仓文件: {holdings_file}")
        source = holdings_file
        archived = archive_holding_file(source, config)
        holdings = parse_holdings(archived, config)
    elif str(config.get("ledger", {}).get("mode", "")).strip().lower() == "tzzb_api":
        holdings, archived, _ = fetch_tzzb_holdings(config)
    else:
        source = open_ledger_and_download(config)
        archived = archive_holding_file(source, config)
        holdings = parse_holdings(archived, config)
    log(f"解析到持仓数量: {len(holdings)}")
    results = analyze_holdings(holdings, config)
    llm_commentary = None
    if llm_enabled(config):
        log(f"请求 LLM 解读: {config['llm']['base_url']} model={config['llm']['model']}")
        try:
            llm_commentary = generate_llm_commentary(results, config)
        except Exception as exc:  # noqa: BLE001 - report should still be written.
            llm_commentary = f"AI 解读失败: `{exc}`"
            log(llm_commentary)
    return write_report(report_markdown(results, archived, llm_commentary), config)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="每日 ETF 持仓分析工具")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="配置文件路径，默认 ./config.toml")
    subparsers = parser.add_subparsers(dest="command")

    run_parser = subparsers.add_parser("run", help="打开账本/读取持仓并生成报告")
    run_parser.add_argument("--holdings", help="跳过浏览器下载，直接分析指定持仓 CSV/XLSX")

    subparsers.add_parser("download", help="只打开账本并等待持仓文件下载")

    analyze_parser = subparsers.add_parser("analyze", help="只分析指定持仓 CSV/XLSX")
    analyze_parser.add_argument("holdings", help="持仓 CSV/XLSX 文件")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        print(
            "\n示例:\n"
            "  python3 stock_assistant.py analyze tests/fixtures/holdings.csv\n"
            "  python3 stock_assistant.py --config config.toml run\n"
            "\n说明: 直接 run 会等待你从投资账本导出新的 CSV/XLSX 持仓文件。\n",
            file=sys.stderr,
        )
        return 2
    config = load_config(Path(args.config).expanduser())
    command = args.command
    if command == "download":
        ensure_dirs(config)
        print(open_ledger_and_download(config))
        return 0
    if command == "analyze":
        report = run(config, Path(args.holdings).expanduser())
        print(report)
        return 0
    holdings = Path(args.holdings).expanduser() if getattr(args, "holdings", None) else None
    report = run(config, holdings)
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
