import dataclasses
import datetime as dt
import json
import urllib.parse
import urllib.request
from typing import Any

from stock_assistant.core.models import Bar
from stock_assistant.core.utils import extract_code, log

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
    symbol = extract_code(code)
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
