import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List

from stock_mcp.core import logger, ProviderError
from stock_mcp.core.http import HttpClient
from stock_mcp.providers.tzzb import extract_code

class EastmoneyFundClient:
    def __init__(self, config: Dict[str, Any]) -> None:
        self.config = config
        self.cache_dir = Path(config.get("paths", {}).get("classification_cache_dir", "./data/research"))
        self.timeout = float(config.get("market", {}).get("timeout_seconds", 15))

    def _get_with_retry(self, url: str) -> str:
        """带指数退避和随机抖动的重试机制"""
        retries = 3
        delay = 1.0
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124 Safari/537.36",
            "Referer": "https://fund.eastmoney.com/",
        }
        for attempt in range(retries):
            try:
                return HttpClient.request(url, method="GET", headers=headers, timeout=self.timeout)
            except Exception as e:
                if attempt == retries - 1:
                    raise ProviderError(f"HTTP GET 失败: {url} -> {e}")
                sleep_time = delay * (2 ** attempt) + (time.time() % 0.5)
                logger.warn(f"Get {url} failed: {e}. Retrying in {sleep_time:.2f}s...")
                time.sleep(sleep_time)
        return ""

    def fetch_fund_holdings(self, code: str) -> List[Dict[str, str]]:
        """获取 ETF/公募基金十大重仓股 (带1小时本地文件缓存)"""
        clean = extract_code(code)
        if not clean:
            return []
        
        # 1. 尝试缓存
        cache_file = self.cache_dir / f"fund_{clean}_constituents.json"
        if cache_file.exists():
            try:
                mtime = cache_file.stat().st_mtime
                if time.time() - mtime < 3600:  # 1小时过期
                    logger.info(f"Cache hit for fund {clean} constituents")
                    return json.loads(cache_file.read_text(encoding="utf-8"))
            except Exception as e:
                logger.warn(f"Failed to read cache for fund {clean}: {e}")

        # 2. 网络拉取
        url = f"https://fundf10.eastmoney.com/FundArchivesDatas.aspx?type=jjcc&code={clean}&topline=10"
        try:
            logger.info(f"Fetching fund constituents from Eastmoney: {clean}")
            text = self._get_with_retry(url)
            holdings = []
            matches = re.finditer(
                r"<a\s+href=['\"]//quote\.eastmoney\.com/[^'\"]*['\"]>(\d{6})</a>"
                r"</td>\s*<td[^>]*>\s*<a\s+href=['\"]//quote\.eastmoney\.com/[^'\"]*['\"]>([^<]+)</a>",
                text,
            )
            for m in matches:
                holdings.append({"code": m.group(1), "name": m.group(2)})
            
            # 3. 写入缓存
            if holdings:
                try:
                    self.cache_dir.mkdir(parents=True, exist_ok=True)
                    cache_file.write_text(json.dumps(holdings, ensure_ascii=False), encoding="utf-8")
                except Exception as e:
                    logger.warn(f"Failed to write cache for fund {clean}: {e}")
            return holdings
        except Exception as e:
            logger.error(f"Failed to fetch constituents for fund {clean}: {e}")
            return []

    def fetch_fund_info(self, code: str) -> Dict[str, Any] | None:
        """拉取品种 JS 数据"""
        clean = extract_code(code)
        if not clean:
            return None
        url = f"http://fund.eastmoney.com/pingzhongdata/{clean}.js"
        try:
            text = self._get_with_retry(url)
            result = {}
            for match in re.finditer(r'var\s+(\w+)\s*=\s*"([^"]*)"', text):
                result[match.group(1)] = match.group(2)
            for match in re.finditer(r'var\s+(stockCodes|stockCodesNew|zqCodes|zqCodesNew)\s*=\s*(\[.*?\])', text):
                try:
                    result[match.group(1)] = json.loads(match.group(2))
                except json.JSONDecodeError:
                    pass
            m = re.search(r'var\s+Data_fundSharesPositions\s*=\s*(\[\[.*?\]\])', text)
            if m:
                try:
                    positions = json.loads(m.group(1))
                    if positions:
                        result["latest_equity_pct"] = float(positions[-1][1])
                except Exception:
                    pass
            return result
        except Exception as e:
            logger.warn(f"Failed to fetch fund info for {clean}: {e}")
            return None

    def fetch_fund_base_info(self, code: str) -> Dict[str, str] | None:
        """从全量 fundcode_search.js 缓存或网络拉取中查找基金的官方名称和官方类型"""
        clean = extract_code(code)
        if not clean:
            return None
        
        cache_file = self.cache_dir / "fundcode_search.js"
        should_download = True
        
        # 检查缓存是否过期（24小时过期）
        if cache_file.exists():
            try:
                mtime = cache_file.stat().st_mtime
                if time.time() - mtime < 86400:
                    should_download = False
            except Exception as e:
                logger.warn(f"Failed to check fundcode_search.js stat: {e}")
                
        if should_download:
            url = "http://fund.eastmoney.com/js/fundcode_search.js"
            try:
                logger.info("Downloading latest fundcode_search.js from Eastmoney...")
                text = self._get_with_retry(url)
                if text:
                    self.cache_dir.mkdir(parents=True, exist_ok=True)
                    cache_file.write_text(text, encoding="utf-8")
            except Exception as e:
                logger.error(f"Failed to download fundcode_search.js: {e}")
                # 如果网络请求失败且本地缓存存在，降级使用本地缓存
                if not cache_file.exists():
                    return None
                    
        # 读取本地缓存文件检索
        try:
            content = cache_file.read_text(encoding="utf-8")
            pattern = rf'\["{clean}","[^"]*","([^"]*)","([^"]*)","[^"]*"'
            match = re.search(pattern, content)
            if match:
                return {
                    "name": match.group(1),
                    "official_type": match.group(2)
                }
        except Exception as e:
            logger.error(f"Failed to read/search fundcode_search.js cache: {e}")
        return None

