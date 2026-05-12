import csv
import dataclasses
import datetime as dt
import glob
import json
import os
import re
import shlex
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from .config import ROOT
from .models import Holding
from .utils import extract_code, log, parse_number, pick_value, read_text_if_path, split_csv_setting

TZZB_BASE_URL = "https://tzzb.10jqka.com.cn/caishen_httpserver/tzzb"
TZZB_REFERER = "https://tzzb.10jqka.com.cn/pc/"
TZZB_ACCOUNT_LIST = "/caishen_fund/pc/account/v1/account_list"
TZZB_STOCK_POSITION = "/caishen_fund/pc/asset/v1/stock_position"
TZZB_FUND_POSITION = "/caishen_fund/pc/asset/v1/fund_position"
TZZB_UID_COOKIE_NAMES = ("userid", "user_id", "uid", "hexin_uid", "u")

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

def column_index(cell_ref: str) -> int:
    letters = re.sub(r"[^A-Z]", "", cell_ref.upper())
    index = 0
    for letter in letters:
        index = index * 26 + ord(letter) - ord("A") + 1
    return max(index - 1, 0)

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

def archive_holding_file(path: Path, config: dict[str, Any]) -> Path:
    archive_dir = Path(config["paths"]["archive_dir"]).expanduser()
    archive_dir.mkdir(parents=True, exist_ok=True)
    stamped = f"{dt.datetime.now():%Y%m%d-%H%M%S}-{path.name}"
    target = archive_dir / stamped
    target.write_bytes(path.read_bytes())
    log(f"归档持仓文件: {target}")
    return target
