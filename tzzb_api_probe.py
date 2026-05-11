#!/usr/bin/env python3
"""Probe Tonghuashun investment ledger APIs.

This is a standalone diagnostic script. It tries to fetch the account list
and then position endpoints discovered from the local "tzzb" browser plugin.

Default mode uses Playwright with a dedicated local browser profile so you can
log in once without copying cookies into files or shell history.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shlex
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Protocol


ROOT = Path(__file__).resolve().parent
LOGIN_URL = "https://tzzb.10jqka.com.cn/pc/"
BASE_URL = "https://tzzb.10jqka.com.cn/caishen_httpserver/tzzb"
ACCOUNT_LIST = "/caishen_fund/pc/account/v1/account_list"
STOCK_POSITION = "/caishen_fund/pc/asset/v1/stock_position"
FUND_POSITION = "/caishen_fund/pc/asset/v1/fund_position"

UID_COOKIE_NAMES = (
    "userid",
    "user_id",
    "uid",
    "hexin_uid",
    "u",
)


class ApiClient(Protocol):
    def post(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        ...


def log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def build_params(uid: str, extra: dict[str, Any] | None = None) -> dict[str, str]:
    params: dict[str, str] = {
        "terminal": "1",
        "version": "0.0.0",
        "userid": uid,
        "user_id": uid,
    }
    for key, value in (extra or {}).items():
        if value is not None and str(value) != "":
            params[key] = str(value)
    return params


def parse_json_response(path: str, status: int, text: str) -> dict[str, Any]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        preview = text[:300].replace("\n", " ")
        raise RuntimeError(f"{path} returned non-JSON HTTP {status}: {preview}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"{path} returned unexpected JSON type: {type(payload).__name__}")
    return payload


def unwrap_ex_data(payload: dict[str, Any]) -> Any:
    if str(payload.get("error_code")) == "0" and "ex_data" in payload:
        return payload["ex_data"]
    if "ex_data" in payload:
        return payload["ex_data"]
    return payload


def error_summary(payload: dict[str, Any]) -> str:
    code = payload.get("error_code")
    msg = payload.get("error_msg") or payload.get("message") or payload.get("msg")
    return f"error_code={code!r} error_msg={msg!r}"


class CookieClient:
    def __init__(self, cookie: str, timeout: int) -> None:
        self.cookie = cookie
        self.timeout = timeout

    def post(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        body = urllib.parse.urlencode(params).encode("utf-8")
        request = urllib.request.Request(
            BASE_URL + path,
            data=body,
            method="POST",
            headers={
                "content-type": "application/x-www-form-urlencoded",
                "origin": "https://tzzb.10jqka.com.cn",
                "referer": LOGIN_URL,
                "user-agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
                ),
                "cookie": self.cookie,
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                status = response.status
                text = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            status = exc.code
            text = exc.read().decode("utf-8", errors="replace")
        return parse_json_response(path, status, text)


def uid_from_cookie_header(cookie: str) -> str:
    cookies: dict[str, str] = {}
    for item in cookie.split(";"):
        if "=" not in item:
            continue
        key, value = item.split("=", 1)
        cookies[key.strip()] = value.strip()
    for name in UID_COOKIE_NAMES:
        if cookies.get(name):
            return cookies[name]
    for key, value in cookies.items():
        if "uid" in key.lower() and value:
            return value
    return ""


def read_text_file(path: str) -> str:
    return Path(path).expanduser().read_text(encoding="utf-8").strip()


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
        if lower in {"-b", "--cookie", "--cookie-jar"} and index + 1 < len(parts):
            value = parts[index + 1].strip()
            if "=" in value:
                return value

    for line in curl_text.splitlines():
        stripped = line.strip().strip("'\"")
        if stripped.lower().startswith("cookie:"):
            return stripped.split(":", 1)[1].strip()
    return ""


def resolve_cookie(args: argparse.Namespace) -> str:
    if args.cookie:
        return args.cookie
    if args.cookie_file:
        return read_text_file(args.cookie_file)
    if args.curl_file:
        cookie = extract_cookie_from_curl(read_text_file(args.curl_file))
        if not cookie:
            raise RuntimeError("No Cookie header found in --curl-file.")
        return cookie
    return os.environ.get("TZZB_COOKIE", "")


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


def dedupe_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    output: list[dict[str, Any]] = []
    for record in records:
        key = json.dumps(record, sort_keys=True, ensure_ascii=False, default=str)
        if key not in seen:
            seen.add(key)
            output.append(record)
    return output


def find_account_records(account_data: Any) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    keys = {"manual_id", "fund_key", "rzrq_fund_key", "fundid", "fund_id", "fundId"}
    for record in walk_dicts(account_data):
        if keys.intersection(record):
            records.append(record)
    return dedupe_records(records)


def first_value(record: dict[str, Any], names: tuple[str, ...]) -> str:
    for name in names:
        value = record.get(name)
        if value is not None and str(value) != "":
            return str(value)
    return ""


def stock_params(account: dict[str, Any]) -> dict[str, str]:
    return {
        "manual_id": first_value(account, ("manual_id", "manualId")),
        "fund_key": first_value(account, ("fund_key", "fundKey")),
        "rzrq_fund_key": first_value(account, ("rzrq_fund_key", "rzrqFundKey")),
    }


def fund_id(account: dict[str, Any]) -> str:
    return first_value(account, ("fundid", "fund_id", "fundId"))


def position_count(position_data: Any, keys: tuple[str, ...]) -> int:
    if isinstance(position_data, dict):
        for key in keys:
            value = position_data.get(key)
            if isinstance(value, list):
                return len(value)
    return 0


def sample_positions(position_data: Any, keys: tuple[str, ...], limit: int) -> list[dict[str, str]]:
    if limit <= 0 or not isinstance(position_data, dict):
        return []
    rows: list[Any] = []
    for key in keys:
        value = position_data.get(key)
        if isinstance(value, list):
            rows = value
            break
    samples: list[dict[str, str]] = []
    for row in rows[:limit]:
        if not isinstance(row, dict):
            continue
        code = first_value(row, ("code", "stockcode", "fundcode", "zqdm"))
        name = first_value(row, ("name", "stockname", "fundname", "zqmc"))
        market = first_value(row, ("market", "scdm"))
        samples.append({"code": code, "name": name, "market": market})
    return samples


def probe(client: ApiClient, uid: str, show_items: int = 0, include_full: bool = False) -> dict[str, Any]:
    account_payload = client.post(ACCOUNT_LIST, build_params(uid, {"userid": uid}))
    if str(account_payload.get("error_code")) not in {"0", "None"} and "ex_data" not in account_payload:
        return {
            "ok": False,
            "stage": "account_list",
            "api": ACCOUNT_LIST,
            "error": error_summary(account_payload),
            "raw_keys": sorted(account_payload.keys()),
        }

    account_data = unwrap_ex_data(account_payload)
    accounts = find_account_records(account_data)
    summary: dict[str, Any] = {
        "ok": True,
        "uid_found": bool(uid),
        "account_count": len(accounts),
        "stock_requests": 0,
        "fund_requests": 0,
        "stock_position_count": 0,
        "fund_position_count": 0,
        "stock_errors": [],
        "fund_errors": [],
        "stock_samples": [],
        "fund_samples": [],
    }
    if include_full:
        summary["account_data"] = account_data
        summary["accounts"] = accounts
        summary["stock_positions"] = []
        summary["fund_positions"] = []

    for account_index, account in enumerate(accounts):
        params = stock_params(account)
        if any(params.values()):
            summary["stock_requests"] += 1
            payload = client.post(STOCK_POSITION, build_params(uid, params))
            if str(payload.get("error_code")) == "0" or "ex_data" in payload:
                data = unwrap_ex_data(payload)
                summary["stock_position_count"] += position_count(data, ("position", "stocks"))
                summary["stock_samples"].extend(sample_positions(data, ("position", "stocks"), show_items))
                if include_full:
                    summary["stock_positions"].append(
                        {"account_index": account_index, "request": params, "data": data}
                    )
            else:
                summary["stock_errors"].append(error_summary(payload))
                if include_full:
                    summary["stock_positions"].append(
                        {"account_index": account_index, "request": params, "error": payload}
                    )

        fid = fund_id(account)
        if fid:
            summary["fund_requests"] += 1
            payload = client.post(FUND_POSITION, build_params(uid, {"fundid": fid}))
            if str(payload.get("error_code")) == "0" or "ex_data" in payload:
                data = unwrap_ex_data(payload)
                summary["fund_position_count"] += position_count(data, ("position", "funds"))
                summary["fund_samples"].extend(sample_positions(data, ("position", "funds"), show_items))
                if include_full:
                    summary["fund_positions"].append(
                        {"account_index": account_index, "request": {"fundid": fid}, "data": data}
                    )
            else:
                summary["fund_errors"].append(error_summary(payload))
                if include_full:
                    summary["fund_positions"].append(
                        {"account_index": account_index, "request": {"fundid": fid}, "error": payload}
                    )

    summary["stock_samples"] = summary["stock_samples"][:show_items]
    summary["fund_samples"] = summary["fund_samples"][:show_items]
    return summary


async def run_browser_mode(args: argparse.Namespace) -> dict[str, Any]:
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Playwright is not installed. Run: python3 -m pip install playwright && "
            "python3 -m playwright install chromium"
        ) from exc

    profile_dir = Path(args.profile_dir).expanduser()
    profile_dir.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as pw:
        launch_kwargs: dict[str, Any] = {
            "headless": args.headless,
            "viewport": {"width": 1280, "height": 900},
        }
        if args.channel:
            launch_kwargs["channel"] = args.channel
        context = await pw.chromium.launch_persistent_context(str(profile_dir), **launch_kwargs)
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto(LOGIN_URL, wait_until="domcontentloaded")

        if not args.headless and not args.no_wait:
            log("Browser opened. Log in to tzzb if needed, then press Enter here to continue.")
            await asyncio.to_thread(input)

        uid = args.uid or await get_uid_from_page(page, context)
        if not uid:
            await context.close()
            raise RuntimeError("No uid found from page/cookies. Confirm login completed, or pass --uid.")

        class BrowserClient:
            def __init__(self, page: Any, timeout: int) -> None:
                self.page = page
                self.timeout = timeout

            def post(self, path: str, params: dict[str, str]) -> dict[str, Any]:
                return asyncio.get_event_loop().run_until_complete(self.apost(path, params))

            async def apost(self, path: str, params: dict[str, str]) -> dict[str, Any]:
                result = await self.page.evaluate(
                    """async ({baseUrl, path, params, timeout}) => {
                        const controller = new AbortController();
                        const timer = setTimeout(() => controller.abort(), timeout * 1000);
                        try {
                            const response = await fetch(baseUrl + path, {
                                method: "POST",
                                credentials: "include",
                                headers: {"content-type": "application/x-www-form-urlencoded"},
                                body: new URLSearchParams(params).toString(),
                                signal: controller.signal,
                            });
                            return {status: response.status, text: await response.text()};
                        } finally {
                            clearTimeout(timer);
                        }
                    }""",
                    {"baseUrl": BASE_URL, "path": path, "params": params, "timeout": self.timeout},
                )
                return parse_json_response(path, int(result["status"]), str(result["text"]))

        client = BrowserClient(page, args.timeout)
        summary = await async_probe(client, uid, args.show_items, args.full)
        await context.close()
        return summary


async def async_probe(client: Any, uid: str, show_items: int, include_full: bool = False) -> dict[str, Any]:
    account_payload = await client.apost(ACCOUNT_LIST, build_params(uid, {"userid": uid}))
    if str(account_payload.get("error_code")) not in {"0", "None"} and "ex_data" not in account_payload:
        return {
            "ok": False,
            "stage": "account_list",
            "api": ACCOUNT_LIST,
            "error": error_summary(account_payload),
            "raw_keys": sorted(account_payload.keys()),
        }

    account_data = unwrap_ex_data(account_payload)
    accounts = find_account_records(account_data)
    summary: dict[str, Any] = {
        "ok": True,
        "uid_found": bool(uid),
        "account_count": len(accounts),
        "stock_requests": 0,
        "fund_requests": 0,
        "stock_position_count": 0,
        "fund_position_count": 0,
        "stock_errors": [],
        "fund_errors": [],
        "stock_samples": [],
        "fund_samples": [],
    }
    if include_full:
        summary["account_data"] = account_data
        summary["accounts"] = accounts
        summary["stock_positions"] = []
        summary["fund_positions"] = []

    for account_index, account in enumerate(accounts):
        params = stock_params(account)
        if any(params.values()):
            summary["stock_requests"] += 1
            payload = await client.apost(STOCK_POSITION, build_params(uid, params))
            if str(payload.get("error_code")) == "0" or "ex_data" in payload:
                data = unwrap_ex_data(payload)
                summary["stock_position_count"] += position_count(data, ("position", "stocks"))
                summary["stock_samples"].extend(sample_positions(data, ("position", "stocks"), show_items))
                if include_full:
                    summary["stock_positions"].append(
                        {"account_index": account_index, "request": params, "data": data}
                    )
            else:
                summary["stock_errors"].append(error_summary(payload))
                if include_full:
                    summary["stock_positions"].append(
                        {"account_index": account_index, "request": params, "error": payload}
                    )

        fid = fund_id(account)
        if fid:
            summary["fund_requests"] += 1
            payload = await client.apost(FUND_POSITION, build_params(uid, {"fundid": fid}))
            if str(payload.get("error_code")) == "0" or "ex_data" in payload:
                data = unwrap_ex_data(payload)
                summary["fund_position_count"] += position_count(data, ("position", "funds"))
                summary["fund_samples"].extend(sample_positions(data, ("position", "funds"), show_items))
                if include_full:
                    summary["fund_positions"].append(
                        {"account_index": account_index, "request": {"fundid": fid}, "data": data}
                    )
            else:
                summary["fund_errors"].append(error_summary(payload))
                if include_full:
                    summary["fund_positions"].append(
                        {"account_index": account_index, "request": {"fundid": fid}, "error": payload}
                    )

    summary["stock_samples"] = summary["stock_samples"][:show_items]
    summary["fund_samples"] = summary["fund_samples"][:show_items]
    return summary


async def get_uid_from_page(page: Any, context: Any) -> str:
    uid = await page.evaluate(
        """() => {
            const values = [];
            try {
                if (window.PCUid && typeof window.PCUid.getCookieUid === "function") {
                    values.push(window.PCUid.getCookieUid());
                }
            } catch (e) {}
            const cookies = Object.fromEntries(document.cookie.split(";").map(x => {
                const i = x.indexOf("=");
                return i >= 0 ? [x.slice(0, i).trim(), x.slice(i + 1).trim()] : ["", ""];
            }).filter(([k]) => k));
            for (const name of ["userid", "user_id", "uid", "hexin_uid", "u"]) {
                if (cookies[name]) values.push(cookies[name]);
            }
            for (const [key, value] of Object.entries(cookies)) {
                if (key.toLowerCase().includes("uid") && value) values.push(value);
            }
            return values.find(Boolean) || "";
        }"""
    )
    if uid:
        return str(uid)
    cookies = await context.cookies("https://tzzb.10jqka.com.cn")
    by_name = {cookie.get("name", ""): cookie.get("value", "") for cookie in cookies}
    for name in UID_COOKIE_NAMES:
        if by_name.get(name):
            return str(by_name[name])
    for name, value in by_name.items():
        if "uid" in name.lower() and value:
            return str(value)
    return ""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Probe Tonghuashun tzzb account and position APIs.")
    parser.add_argument(
        "--mode",
        choices=("browser", "cookie"),
        default="browser",
        help="browser reuses a Playwright profile; cookie sends TZZB_COOKIE/--cookie directly.",
    )
    parser.add_argument("--profile-dir", default=str(ROOT / ".tzzb-api-probe" / "profile"))
    parser.add_argument("--channel", default="", help="Optional Playwright browser channel, e.g. chrome.")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--no-wait", action="store_true", help="Do not wait for Enter after opening browser.")
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--uid", default=os.environ.get("TZZB_UID", ""))
    parser.add_argument("--cookie", default="", help="Raw Cookie header. Prefer --cookie-file to avoid shell history.")
    parser.add_argument("--cookie-file", default="", help="File containing the raw Cookie header.")
    parser.add_argument("--curl-file", default="", help="File containing Chrome 'Copy as cURL' output.")
    parser.add_argument("--show-items", type=int, default=0, help="Show first N code/name samples.")
    parser.add_argument("--full", action="store_true", help="Include full account and position API data.")
    parser.add_argument("--output", default="", help="Write JSON output to this file instead of stdout.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.mode == "cookie":
            cookie = resolve_cookie(args)
            if not cookie:
                raise RuntimeError("Cookie mode needs --cookie, --cookie-file, --curl-file, or TZZB_COOKIE.")
            uid = args.uid or uid_from_cookie_header(cookie)
            if not uid:
                raise RuntimeError("No uid found from cookie. Pass --uid or TZZB_UID.")
            summary = probe(CookieClient(cookie, args.timeout), uid, args.show_items, args.full)
        else:
            summary = asyncio.run(run_browser_mode(args))
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1

    output = json.dumps(summary, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).expanduser().write_text(output + "\n", encoding="utf-8")
        print(json.dumps({"ok": summary.get("ok"), "output": args.output}, ensure_ascii=False, indent=2))
    else:
        print(output)
    return 0 if summary.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
