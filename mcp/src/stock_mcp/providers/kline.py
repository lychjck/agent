import datetime as dt
import json
from typing import Any, Dict, List

from stock_mcp.core import logger
from stock_mcp.core.http import HttpClient
from stock_mcp.domain import Bar
from stock_mcp.providers.tzzb import extract_code

class KlineClient:
    def __init__(self, config: Dict[str, Any]) -> None:
        self.config = config
        self.market_cfg = config.get("market", {})
        self.timeout = float(self.market_cfg.get("timeout_seconds", 15))
        self.history_days = int(self.market_cfg.get("history_days", 260))

    def _secid(self, code: str) -> str:
        clean = extract_code(code)
        if clean.startswith(("50", "51", "52", "56", "58", "60", "68", "90")):
            return f"1.{clean}"
        return f"0.{clean}"

    def fetch_sina_bars(self, code: str) -> List[Bar]:
        clean = extract_code(code)
        symbol = ("sh" if self._secid(code).startswith("1") else "sz") + clean
        params = {"symbol": symbol, "scale": "240", "ma": "no", "datalen": str(self.history_days)}
        # Encode query parameters
        import urllib.parse
        encoded_params = urllib.parse.urlencode(params)
        url = f"https://quotes.sina.cn/cn/api/openapi.php/CN_MarketDataService.getKLineData?{encoded_params}"
        headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"}
        
        resp_str = HttpClient.request(url, method="GET", headers=headers, timeout=self.timeout)
        payload = json.loads(resp_str)
        
        rows = payload.get("result", {}).get("data") or []
        bars = []
        for row in rows:
            bars.append(Bar(
                date=str(row["day"]),
                open=float(row["open"]),
                close=float(row["close"]),
                high=float(row["high"]),
                low=float(row["low"]),
                volume=float(row.get("volume") or 0),
            ))
        bars.sort(key=lambda b: b.date)
        return bars

    def fetch_eastmoney_bars(self, code: str) -> List[Bar]:
        end = dt.date.today()
        begin = end - dt.timedelta(days=self.history_days * 2)
        params = {
            "secid": self._secid(code),
            "ut": "fa5fd1943c7b386f172d6893dbfba10b",
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
            "klt": "101",
            "fqt": "1",
            "beg": begin.strftime("%Y%m%d"),
            "end": end.strftime("%Y%m%d"),
        }
        import urllib.parse
        encoded_params = urllib.parse.urlencode(params)
        url = f"https://push2his.eastmoney.com/api/qt/stock/kline/get?{encoded_params}"
        
        resp_str = HttpClient.request(url, method="GET", headers={"User-Agent": "Mozilla/5.0"}, timeout=self.timeout)
        payload = json.loads(resp_str)
        
        klines = payload.get("data", {}).get("klines") or []
        bars = []
        for line in klines:
            parts = str(line).split(",")
            if len(parts) < 6:
                continue
            bars.append(Bar(
                date=parts[0],
                open=float(parts[1]),
                close=float(parts[2]),
                high=float(parts[3]),
                low=float(parts[4]),
                volume=float(parts[5]),
            ))
        bars.sort(key=lambda b: b.date)
        return bars

    def fetch_bars(self, code: str) -> List[Bar]:
        provider = str(self.market_cfg.get("provider", "sina")).lower()
        if provider == "eastmoney":
            try:
                return self.fetch_eastmoney_bars(code)
            except Exception:
                logger.warn("Eastmoney KLine failed, switching to Sina")
                return self.fetch_sina_bars(code)
        else:
            try:
                return self.fetch_sina_bars(code)
            except Exception:
                logger.warn("Sina KLine failed, switching to Eastmoney")
                return self.fetch_eastmoney_bars(code)
