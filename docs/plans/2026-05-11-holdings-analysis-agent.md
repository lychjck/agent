# Holdings Analysis Agent Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a personal holdings analysis agent that syncs the user's portfolio, enriches it with market/search/classification evidence, applies personal policy constraints, remembers history, and produces reviewable recommendations without executing trades.

**Architecture:** Keep the current repository as a local modular monolith first: `stock_assistant.py` remains the core engine until boundaries are stable, `api.py` exposes FastAPI endpoints, and `frontend/src/App.tsx` becomes the dashboard. The agent should not be LLM-only. It should run a deterministic orchestration loop that calls local tools, optional search tools, policy/risk tools, memory tools, and then asks the LLM to explain and rank evidence-backed candidate actions.

**Tech Stack:** Python 3.12, FastAPI, Pydantic, `unittest`, OpenAI-compatible LLM API, TOML config, local JSON/JSONL state files, React/Vite/TypeScript/Recharts.

---

## 1. Current Baseline

The repository currently has these major surfaces:

- CLI and analysis core: `/Users/liyanran/github/stock/stock_assistant.py`
- FastAPI backend: `/Users/liyanran/github/stock/api.py`
- React dashboard: `/Users/liyanran/github/stock/frontend/src/App.tsx`
- Config example: `/Users/liyanran/github/stock/config.example.toml`
- Tests: `/Users/liyanran/github/stock/tests/test_stock_assistant.py`
- Data archives: `/Users/liyanran/github/stock/data/holdings`
- Reports: `/Users/liyanran/github/stock/reports`

Current verified state:

- Backend unit tests pass with:

```bash
uv run python -m unittest discover -s tests
```

- Frontend build currently fails:

```text
frontend/src/App.tsx:368
Type '(value: any, name: string) => [string, string]' is not assignable to type 'Formatter<ValueType, NameType>'
```

Known code issue:

- `/Users/liyanran/github/stock/stock_assistant.py:1385` still expects `fetch_tzzb_holdings(config)` to return 2 values, but the function now returns 3 values: `(holdings, source, summary)`.

Current git state contains unrelated or pre-existing edits:

- Modified: `api.py`
- Modified: `frontend/src/App.tsx`
- Modified: `stock_assistant.py`
- Untracked: `scratch.py`, `scratch2.py`

Implementation must not revert user changes. Inspect diffs before each edit.

---

## 2. Key Product Decisions

### 2.1 The Agent Is Not Only LLM

The LLM should be one reasoning/explanation component, not the whole agent.

The agent needs these tool categories:

| Tool Category | Required For MVP | Purpose | Deterministic Or LLM |
| --- | --- | --- | --- |
| Portfolio source tool | Yes | Read TZZB/API/CSV holdings | Deterministic |
| Market data tool | Yes | Fetch/cached ETF K-lines | Deterministic |
| Policy tool | Yes | Apply user risk and allocation rules | Deterministic |
| Classification tool | Yes | Map instrument to asset/sector/theme | Hybrid |
| Memory tool | Yes | Store snapshots and compare history | Deterministic |
| LLM report tool | Yes | Explain, rank, ask follow-up questions | LLM |
| Search tool | Phase 2+ | Find/cite instrument facts, index, issuer, sector | External deterministic retrieval plus LLM classification |
| Alert tool | Phase 3+ | Notify when conditions trigger | Deterministic |
| Order/trading tool | No | Not included; only produce reviewable actions | Not implemented |

Important: "tool" here does not have to mean an installed plugin at first. In this codebase, a tool can be a Python function with a stable input/output schema. Later, these functions can be exposed as LLM function-calling tools if the selected model/provider reliably supports tool calling.

### 2.2 Search Is Needed, But Not For Every Run

Search should be used for:

- Unknown instrument classification.
- Verifying an ETF's tracked index, issuer, asset class, region, or sector.
- Optional market context, if the user explicitly enables external context.
- Periodic refresh of stale metadata.

Search should not be used for:

- Every daily report by default.
- Replacing the local portfolio/market/risk engine.
- Making up news or macro narratives.
- Querying sensitive portfolio totals or exact position sizes.

Search queries should be limited to instrument code/name and metadata terms, for example:

```text
510300 沪深300ETF 跟踪指数 基金公司
512880 证券ETF 跟踪指数 行业
159915 创业板ETF 跟踪指数
```

Do not search with:

```text
我的持仓 510300 多少钱 要不要买
```

Search outputs must be cached with evidence URLs and timestamps. Classification or external context derived from search must show source evidence.

### 2.3 Industry Classification Should Be Hybrid

Recommended precedence:

1. User manual override in `config.toml`.
2. Local cached metadata in `data/research/instruments/{code}.json`.
3. Local rule-based inference from code/name/index keywords.
4. Search tool fetches public metadata.
5. LLM maps the evidence into the project's taxonomy.
6. User confirms uncertain classifications before they become trusted.

The LLM should not classify holdings from the name alone when the result affects risk limits. It can produce a suggestion with confidence and evidence, but manual override and cached verified metadata win.

Example classification record:

```json
{
  "code": "512880",
  "name": "证券ETF",
  "asset_class": "sector_equity",
  "sector": "financials",
  "theme": "brokerage",
  "region": "china_a",
  "strategy": "passive_index",
  "tracked_index": "证券公司指数",
  "issuer": "unknown",
  "confidence": 0.82,
  "source": "search_llm_suggested",
  "evidence": [
    {
      "title": "基金产品页",
      "url": "https://example.com/...",
      "retrieved_at": "2026-05-11T15:00:00+08:00"
    }
  ],
  "reviewed_by_user": false
}
```

---

## 3. Agent Loop

The daily agent loop should be:

1. Observe portfolio.
2. Normalize holdings.
3. Load memory and previous snapshots.
4. Classify instruments.
5. Fetch or reuse market data.
6. Compute technical indicators.
7. Compute portfolio summary and policy violations.
8. Generate candidate actions with deterministic evidence.
9. Ask LLM to explain, rank, and format output.
10. Validate LLM output against schema.
11. Persist snapshot/report.
12. Trigger alerts if needed.

The LLM input should include:

- Current holdings after normalization.
- Technical indicators.
- Policy constraints.
- Classification metadata.
- Historical diff.
- Candidate actions generated by deterministic code.

The LLM output should not be allowed to invent actions outside the candidate action set unless it labels them as questions or observations.

Target agent output shape:

```json
{
  "summary": {
    "health_score": 74,
    "status": "watch",
    "brief": "组合整体可持有，但行业集中度偏高。"
  },
  "risk_tags": [
    {
      "code": "sector_concentration",
      "label": "行业集中度偏高",
      "severity": "medium",
      "evidence": ["sector_equity actual 42.3% > limit 35%"]
    }
  ],
  "action_items": [
    {
      "id": "reduce-512880-overweight",
      "type": "rebalance",
      "target_code": "512880",
      "target_name": "证券ETF",
      "priority": "medium",
      "candidate_action_id": "candidate-001",
      "reason": "单一行业暴露超过策略上限。",
      "evidence": ["weight 18.2%", "sector financials 42.3%"],
      "requires_user_confirmation": true
    }
  ],
  "watch_conditions": [
    {
      "target_code": "510300",
      "condition": "close < ma60",
      "meaning": "中期趋势走弱，暂停加仓观察。"
    }
  ],
  "questions": [
    {
      "id": "confirm-risk-level",
      "question": "当前策略按 balanced 风险等级执行，是否需要调低单行业上限？"
    }
  ]
}
```

---

## 4. Configuration Design

Modify `/Users/liyanran/github/stock/config.example.toml`.

Add:

```toml
[profile]
base_currency = "CNY"
risk_level = "balanced"
investment_style = "long_term_etf"
allow_external_search = false
allow_external_llm = true

[policy]
cash_min_pct = 5
max_single_position_pct = 20
max_sector_pct = 35
max_theme_pct = 25
max_unknown_classification_pct = 10
loss_alert_pct = -8
gain_trim_pct = 20
rebalance_drift_pct = 5

[allocation_targets]
broad_index = 40
sector_equity = 25
bond = 15
overseas = 10
commodity = 5
cash = 5

[classification]
mode = "hybrid"
require_user_review_below_confidence = 0.75
cache_ttl_days = 90

[classifications."510300"]
asset_class = "broad_index"
sector = ""
theme = "csi300"
region = "china_a"
strategy = "passive_index"
reviewed_by_user = true

[search]
enabled = false
provider = "none"
cache_dir = "/Users/liyanran/github/stock/data/research"
timeout_seconds = 20
max_results = 5

[agent]
enabled = true
strict_json = true
llm_can_create_new_actions = false
save_snapshots = true
snapshot_dir = "/Users/liyanran/github/stock/data/state"
```

Add `.gitignore` entries:

```gitignore
data/state/*
!data/state/.gitkeep
data/research/*
!data/research/.gitkeep
```

---

## 5. Data Model

### 5.1 Existing Models

Keep existing dataclasses:

- `Holding`
- `Bar`

Extend by adding helper data models. Prefer Pydantic models in `api.py` only if keeping core dependency-light is important. If Pydantic is acceptable in core, define them in `stock_assistant.py` first to avoid adding modules too early.

### 5.2 New Core Shapes

Add lightweight dictionaries or dataclasses:

```python
@dataclasses.dataclass(frozen=True)
class InstrumentClassification:
    code: str
    name: str
    asset_class: str = "unknown"
    sector: str = ""
    theme: str = ""
    region: str = "unknown"
    strategy: str = "unknown"
    tracked_index: str = ""
    issuer: str = ""
    confidence: float = 0.0
    source: str = "unknown"
    evidence: tuple[dict[str, str], ...] = ()
    reviewed_by_user: bool = False
```

```python
@dataclasses.dataclass(frozen=True)
class RiskFlag:
    code: str
    label: str
    severity: str
    evidence: tuple[str, ...]
```

```python
@dataclasses.dataclass(frozen=True)
class CandidateAction:
    id: str
    type: str
    target_code: str
    target_name: str
    priority: str
    reason: str
    evidence: tuple[str, ...]
    source: str = "rule_engine"
    requires_user_confirmation: bool = True
```

These can start as dicts if the team wants minimum churn, but dataclasses make tests and serialization easier.

---

## 6. Implementation Tasks

### Task 0: Stabilize Current Build And CLI

**Files:**

- Modify: `/Users/liyanran/github/stock/stock_assistant.py`
- Modify: `/Users/liyanran/github/stock/frontend/src/App.tsx`
- Test: `/Users/liyanran/github/stock/tests/test_stock_assistant.py`

**Step 1: Add a regression test for TZZB run return shape**

Patch `tests/test_stock_assistant.py` with a mocked `fetch_tzzb_holdings` path. Use `tempfile.TemporaryDirectory()` and `unittest.mock.patch`.

Test intent:

- `run(config, holdings_file=None)` should work when `ledger.mode = "tzzb_api"`.
- It should accept the 3-value return from `fetch_tzzb_holdings`.

**Step 2: Run the focused test and verify it fails**

Run:

```bash
uv run python -m unittest tests.test_stock_assistant.StockAssistantTest.test_run_accepts_tzzb_summary_return
```

Expected before fix:

```text
ValueError: too many values to unpack
```

**Step 3: Fix `run()`**

In `/Users/liyanran/github/stock/stock_assistant.py`, change:

```python
holdings, archived = fetch_tzzb_holdings(config)
```

to:

```python
holdings, archived, _summary = fetch_tzzb_holdings(config)
```

**Step 4: Fix frontend Recharts formatter type**

In `/Users/liyanran/github/stock/frontend/src/App.tsx`, change the formatter near line 368 to tolerate an undefined name:

```tsx
formatter={(value, name) => [
  `¥${Number(value).toLocaleString()}`,
  String(name ?? '市值'),
]}
```

If TypeScript still complains, import the Recharts formatter types or use a small helper function with inferred parameters.

**Step 5: Verify**

Run:

```bash
uv run python -m unittest discover -s tests
```

Expected:

```text
OK
```

Run:

```bash
npm run build
```

from:

```bash
/Users/liyanran/github/stock/frontend
```

Expected:

```text
vite build
```

finishes without TypeScript errors.

**Step 6: Commit if requested**

Do not commit unless the user explicitly asks.

---

### Task 1: Add Serialization Contracts

**Files:**

- Modify: `/Users/liyanran/github/stock/stock_assistant.py`
- Modify: `/Users/liyanran/github/stock/api.py`
- Create: `/Users/liyanran/github/stock/tests/test_serialization.py`

**Step 1: Write tests**

Create tests for:

- `holding_to_dict()`
- `bar_to_dict()`
- `analysis_result_to_dict()` with a successful ETF result.
- `analysis_result_to_dict()` with a failed行情 result.
- `analysis_result_to_dict()` with a fund result without `latest`.

Expected serialized holding:

```python
{
    "code": "510300",
    "name": "沪深300ETF",
    "quantity": 100.0,
    "cost_price": 4.0,
    "market_value": 420.0,
    "profit_pct": 5.0,
    "hold_profit": None,
    "day_profit": None,
    "asset_type": "etf",
}
```

**Step 2: Run tests and confirm failure**

```bash
uv run python -m unittest tests.test_serialization
```

Expected:

```text
AttributeError: module 'stock_assistant' has no attribute 'holding_to_dict'
```

**Step 3: Implement serialization helpers**

Add to `/Users/liyanran/github/stock/stock_assistant.py`:

```python
def holding_to_dict(holding: Holding) -> dict[str, Any]:
    return {
        "code": holding.code,
        "name": holding.name,
        "quantity": holding.quantity,
        "cost_price": holding.cost_price,
        "market_value": holding.market_value,
        "profit_pct": holding.profit_pct,
        "hold_profit": holding.hold_profit,
        "day_profit": holding.day_profit,
        "asset_type": holding.asset_type,
    }
```

```python
def bar_to_dict(bar: Bar | None) -> dict[str, Any] | None:
    if bar is None:
        return None
    return {
        "date": str(bar.date),
        "open": bar.open,
        "close": bar.close,
        "high": bar.high,
        "low": bar.low,
        "volume": bar.volume,
        "amount": bar.amount,
        "pct_change": bar.pct_change,
    }
```

```python
def analysis_result_to_dict(item: dict[str, Any]) -> dict[str, Any]:
    output = dict(item)
    holding = output.get("holding")
    if isinstance(holding, Holding):
        output["holding"] = holding_to_dict(holding)
    latest = output.get("latest")
    if isinstance(latest, Bar):
        output["latest"] = bar_to_dict(latest)
    return output
```

**Step 4: Replace repeated API serialization**

In `/Users/liyanran/github/stock/api.py`, replace manual result copying with calls to `holding_to_dict()` and `analysis_result_to_dict()`.

**Step 5: Verify**

```bash
uv run python -m unittest discover -s tests
```

Expected:

```text
OK
```

---

### Task 2: Add Personal Policy Config

**Files:**

- Modify: `/Users/liyanran/github/stock/config.example.toml`
- Modify: `/Users/liyanran/github/stock/stock_assistant.py`
- Test: `/Users/liyanran/github/stock/tests/test_policy.py`

**Step 1: Write tests for config defaults**

Test:

- `DEFAULTS` includes `profile`, `policy`, `allocation_targets`, `classification`, `search`, `agent`.
- `deep_merge()` preserves defaults when partial config is supplied.

**Step 2: Add default config**

Add defaults matching section 4 into `DEFAULTS`.

Keep backward compatibility:

- Existing `analysis.loss_alert_pct` should still work.
- New `policy.loss_alert_pct` should become the canonical value later.

**Step 3: Add config helper**

Add:

```python
def policy_value(config: dict[str, Any], key: str, fallback: Any = None) -> Any:
    if key in config.get("policy", {}):
        return config["policy"][key]
    if key in config.get("analysis", {}):
        return config["analysis"][key]
    return fallback
```

Use it in `decide_action()` for:

- `loss_alert_pct`
- `gain_trim_pct`
- `max_single_position_pct`

**Step 4: Verify existing tests**

```bash
uv run python -m unittest discover -s tests
```

Expected:

```text
OK
```

---

### Task 3: Build Instrument Classification Core

**Files:**

- Modify: `/Users/liyanran/github/stock/stock_assistant.py`
- Modify: `/Users/liyanran/github/stock/config.example.toml`
- Test: `/Users/liyanran/github/stock/tests/test_classification.py`

**Step 1: Define taxonomy**

Start with this controlled vocabulary:

```python
ASSET_CLASSES = {
    "broad_index",
    "sector_equity",
    "theme_equity",
    "bond",
    "overseas",
    "commodity",
    "cash",
    "active_fund",
    "unknown",
}
```

Sector examples:

```python
SECTORS = {
    "financials",
    "technology",
    "semiconductor",
    "healthcare",
    "consumer",
    "energy",
    "materials",
    "industrial",
    "defense",
    "unknown",
}
```

**Step 2: Write classification tests**

Cases:

- User override wins.
- Name containing `沪深300` maps to `broad_index`.
- Name containing `证券` maps to `sector_equity` and `financials`.
- Name containing `半导体` maps to `sector_equity` and `semiconductor`.
- Unknown code/name maps to `unknown` with low confidence.

**Step 3: Implement config override parser**

Add:

```python
def classification_from_config(holding: Holding, config: dict[str, Any]) -> InstrumentClassification | None:
    record = config.get("classifications", {}).get(holding.code)
    if not isinstance(record, dict):
        return None
    return InstrumentClassification(
        code=holding.code,
        name=holding.name,
        asset_class=str(record.get("asset_class", "unknown")),
        sector=str(record.get("sector", "")),
        theme=str(record.get("theme", "")),
        region=str(record.get("region", "unknown")),
        strategy=str(record.get("strategy", "unknown")),
        confidence=1.0 if record.get("reviewed_by_user") else 0.8,
        source="config",
        reviewed_by_user=bool(record.get("reviewed_by_user", False)),
    )
```

**Step 4: Implement name rule classifier**

Add:

```python
def classify_by_name_rules(holding: Holding) -> InstrumentClassification:
    name = holding.name
    if "沪深300" in name or "中证500" in name or "创业板" in name or "科创50" in name:
        return InstrumentClassification(
            code=holding.code,
            name=holding.name,
            asset_class="broad_index",
            region="china_a",
            strategy="passive_index",
            confidence=0.7,
            source="name_rule",
        )
    if "证券" in name or "券商" in name:
        return InstrumentClassification(
            code=holding.code,
            name=holding.name,
            asset_class="sector_equity",
            sector="financials",
            theme="brokerage",
            region="china_a",
            strategy="passive_index",
            confidence=0.65,
            source="name_rule",
        )
    if "半导体" in name or "芯片" in name:
        return InstrumentClassification(
            code=holding.code,
            name=holding.name,
            asset_class="sector_equity",
            sector="semiconductor",
            theme="semiconductor",
            region="china_a",
            strategy="passive_index",
            confidence=0.65,
            source="name_rule",
        )
    return InstrumentClassification(code=holding.code, name=holding.name, source="unknown")
```

**Step 5: Implement public wrapper**

```python
def classify_holding(holding: Holding, config: dict[str, Any]) -> InstrumentClassification:
    return (
        classification_from_config(holding, config)
        or load_cached_classification(holding, config)
        or classify_by_name_rules(holding)
    )
```

For this task, `load_cached_classification()` can return `None`. Implement cache in Task 4.

**Step 6: Verify**

```bash
uv run python -m unittest tests.test_classification
uv run python -m unittest discover -s tests
```

Expected:

```text
OK
```

---

### Task 4: Add Search Tool Interface And Metadata Cache

**Files:**

- Modify: `/Users/liyanran/github/stock/stock_assistant.py`
- Modify: `/Users/liyanran/github/stock/config.example.toml`
- Modify: `/Users/liyanran/github/stock/.gitignore`
- Create: `/Users/liyanran/github/stock/data/research/.gitkeep`
- Test: `/Users/liyanran/github/stock/tests/test_research_cache.py`

**Decision:** Do not require a real web search provider in MVP. Build the interface, cache, and disabled provider first. Then a real provider can be plugged in without changing the agent loop.

**Step 1: Add research cache helpers**

Functions:

- `research_cache_path(code, config) -> Path`
- `load_cached_classification(holding, config) -> InstrumentClassification | None`
- `save_classification_cache(classification, config) -> Path`
- `classification_cache_is_fresh(record, ttl_days) -> bool`

Cache path:

```text
data/research/instruments/{code}.json
```

**Step 2: Write cache tests**

Test:

- Save classification to temp cache dir.
- Load it back.
- Expired cache is ignored when `retrieved_at` is older than TTL.
- Reviewed user cache should be loaded even if confidence is low.

**Step 3: Add search provider protocol**

In `stock_assistant.py` initially:

```python
class SearchProvider:
    def search(self, query: str, max_results: int) -> list[dict[str, str]]:
        raise NotImplementedError
```

Add disabled provider:

```python
class DisabledSearchProvider(SearchProvider):
    def search(self, query: str, max_results: int) -> list[dict[str, str]]:
        return []
```

Add factory:

```python
def build_search_provider(config: dict[str, Any]) -> SearchProvider:
    search = config.get("search", {})
    if not config_bool(search.get("enabled", False)):
        return DisabledSearchProvider()
    provider = str(search.get("provider", "none")).lower()
    if provider == "none":
        return DisabledSearchProvider()
    raise RuntimeError(f"未知搜索工具 provider: {provider}")
```

**Step 4: Add search classification stub**

```python
def suggest_classification_with_search(holding: Holding, config: dict[str, Any]) -> InstrumentClassification | None:
    provider = build_search_provider(config)
    query = f"{holding.code} {holding.name} ETF 跟踪指数 行业 基金公司"
    results = provider.search(query, int(config.get("search", {}).get("max_results", 5)))
    if not results:
        return None
    return None
```

This intentionally returns `None` until a real search adapter and LLM evidence mapper are implemented.

**Step 5: Add `.gitignore` entries**

```gitignore
data/research/*
!data/research/.gitkeep
```

**Step 6: Verify**

```bash
uv run python -m unittest tests.test_research_cache
uv run python -m unittest discover -s tests
```

Expected:

```text
OK
```

---

### Task 5: Add Real Search Adapter Behind Config

**Files:**

- Modify: `/Users/liyanran/github/stock/stock_assistant.py`
- Modify: `/Users/liyanran/github/stock/config.example.toml`
- Test: `/Users/liyanran/github/stock/tests/test_search_provider.py`

**Decision Needed Before Implementation:** Choose the real provider.

Options:

1. Local manual provider: read search results from a local JSON file. No network, easiest to test.
2. HTTP search API provider: Tavily/SerpAPI/Bing/Exa or another provider. Needs API key, network, and cost/privacy review.
3. Browser/search plugin provider: not available inside this app as a stable repo dependency unless explicitly integrated later.

Recommended first implementation:

- Implement `manual_json` provider first.
- Add HTTP provider only after confirming which search API you want to use.

Manual provider config:

```toml
[search]
enabled = true
provider = "manual_json"
manual_results_file = "/Users/liyanran/github/stock/data/research/manual_search_results.json"
```

Manual results shape:

```json
{
  "512880 证券ETF ETF 跟踪指数 行业 基金公司": [
    {
      "title": "证券ETF 产品页",
      "url": "https://example.com/512880",
      "snippet": "跟踪证券公司指数..."
    }
  ]
}
```

**Step 1: Write tests for manual provider**

Test:

- Exact query returns results.
- Missing query returns empty list.
- Missing file returns empty list or a clear recoverable error.

**Step 2: Implement provider**

Add:

```python
class ManualJsonSearchProvider(SearchProvider):
    def __init__(self, path: Path):
        self.path = path

    def search(self, query: str, max_results: int) -> list[dict[str, str]]:
        if not self.path.exists():
            return []
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        rows = payload.get(query, [])
        if not isinstance(rows, list):
            return []
        return [row for row in rows[:max_results] if isinstance(row, dict)]
```

Update factory to support `manual_json`.

**Step 3: Add LLM classification from evidence**

Add function:

```python
def classify_from_search_evidence(holding: Holding, results: list[dict[str, str]], config: dict[str, Any]) -> InstrumentClassification | None:
    # Phase 1 can be rule-based over snippets.
    # Phase 2 can call LLM with strict JSON.
    text = " ".join(str(item.get("snippet", "")) for item in results)
    if "证券" in text or "券商" in text:
        return InstrumentClassification(...)
    return None
```

Do not call LLM in this task unless the strict schema parser from Task 9 exists.

**Step 4: Verify**

```bash
uv run python -m unittest tests.test_search_provider
uv run python -m unittest discover -s tests
```

Expected:

```text
OK
```

---

### Task 6: Portfolio Summary And Policy Evaluation

**Files:**

- Modify: `/Users/liyanran/github/stock/stock_assistant.py`
- Test: `/Users/liyanran/github/stock/tests/test_portfolio_policy.py`

**Step 1: Write tests**

Build holdings:

- `510300` broad index, market value 4000.
- `512880` financials sector, market value 3000.
- `159995` semiconductor sector, market value 2500.
- `cash` or money fund, market value 500.

Expected:

- Total value = 10000.
- broad_index = 40%.
- sector_equity = 55%.
- cash = 5%.
- `max_sector_pct=35` triggers a risk flag.

**Step 2: Implement summary**

Add:

```python
def summarize_portfolio(
    holdings: list[Holding],
    classifications: dict[str, InstrumentClassification],
    config: dict[str, Any],
) -> dict[str, Any]:
    total_value = sum(item.market_value or 0 for item in holdings)
    by_asset_class: dict[str, float] = {}
    by_sector: dict[str, float] = {}
    positions = []
    for holding in holdings:
        value = holding.market_value or 0
        cls = classifications.get(holding.code)
        asset_class = cls.asset_class if cls else "unknown"
        sector = cls.sector if cls and cls.sector else "unknown"
        by_asset_class[asset_class] = by_asset_class.get(asset_class, 0) + value
        by_sector[sector] = by_sector.get(sector, 0) + value
        positions.append({
            "code": holding.code,
            "name": holding.name,
            "market_value": value,
            "weight": value / total_value * 100 if total_value else None,
            "asset_class": asset_class,
            "sector": sector,
        })
    return {
        "total_value": total_value,
        "by_asset_class": value_map_to_pct(by_asset_class, total_value),
        "by_sector": value_map_to_pct(by_sector, total_value),
        "positions": positions,
    }
```

**Step 3: Implement policy evaluation**

Add:

```python
def evaluate_policy(summary: dict[str, Any], config: dict[str, Any]) -> list[RiskFlag]:
    flags: list[RiskFlag] = []
    policy = config.get("policy", {})
    max_sector_pct = float(policy.get("max_sector_pct", 35))
    for sector, pct in summary.get("by_sector", {}).items():
        if sector != "unknown" and pct > max_sector_pct:
            flags.append(RiskFlag(
                code="sector_concentration",
                label=f"{sector} 行业集中度偏高",
                severity="medium",
                evidence=(f"{sector}={pct:.2f}% > limit {max_sector_pct:.2f}%",),
            ))
    return flags
```

**Step 4: Verify**

```bash
uv run python -m unittest tests.test_portfolio_policy
uv run python -m unittest discover -s tests
```

Expected:

```text
OK
```

---

### Task 7: Generate Candidate Actions

**Files:**

- Modify: `/Users/liyanran/github/stock/stock_assistant.py`
- Test: `/Users/liyanran/github/stock/tests/test_candidate_actions.py`

**Step 1: Write tests**

Cases:

- Single holding above `max_single_position_pct` creates `rebalance` candidate.
- Sector above `max_sector_pct` creates sector rebalance candidate.
- `action == "减仓/暂停加仓"` from technical rule creates `reduce` or `watch` candidate depending on severity.
- Unknown classification above `max_unknown_classification_pct` creates `classify_required` candidate, not buy/sell advice.

**Step 2: Implement candidate generator**

Add:

```python
def generate_candidate_actions(
    analysis_results: list[dict[str, Any]],
    summary: dict[str, Any],
    risk_flags: list[RiskFlag],
    config: dict[str, Any],
) -> list[CandidateAction]:
    actions: list[CandidateAction] = []
    for item in analysis_results:
        holding = item.get("holding")
        if not isinstance(holding, Holding):
            continue
        weight = item.get("weight")
        max_single = float(policy_value(config, "max_single_position_pct", 20))
        if weight is not None and weight > max_single:
            actions.append(CandidateAction(
                id=f"rebalance-{holding.code}-single-overweight",
                type="rebalance",
                target_code=holding.code,
                target_name=holding.name,
                priority="medium",
                reason="单只持仓超过策略上限",
                evidence=(f"weight={weight:.2f}% > limit {max_single:.2f}%",),
            ))
        if item.get("action") == "减仓/暂停加仓":
            actions.append(CandidateAction(
                id=f"watch-{holding.code}-weak-trend",
                type="watch",
                target_code=holding.code,
                target_name=holding.name,
                priority="medium",
                reason="技术面风险信号较多",
                evidence=(str(item.get("reason", "")),),
            ))
    for flag in risk_flags:
        if flag.code == "sector_concentration":
            actions.append(CandidateAction(
                id=f"rebalance-{flag.code}",
                type="rebalance",
                target_code="",
                target_name=flag.label,
                priority=flag.severity,
                reason=flag.label,
                evidence=flag.evidence,
            ))
    return dedupe_candidate_actions(actions)
```

**Step 3: Verify**

```bash
uv run python -m unittest tests.test_candidate_actions
uv run python -m unittest discover -s tests
```

Expected:

```text
OK
```

---

### Task 8: Agent Snapshot Memory

**Files:**

- Modify: `/Users/liyanran/github/stock/stock_assistant.py`
- Modify: `/Users/liyanran/github/stock/.gitignore`
- Create: `/Users/liyanran/github/stock/data/state/.gitkeep`
- Test: `/Users/liyanran/github/stock/tests/test_agent_memory.py`

**Step 1: Define snapshot shape**

Snapshot file:

```text
data/state/snapshots/YYYYMMDD-HHMMSS-agent-snapshot.json
```

Shape:

```json
{
  "schema_version": 1,
  "generated_at": "2026-05-11T15:30:00+08:00",
  "source": "tzzb_api",
  "portfolio": {},
  "classifications": {},
  "technical_results": [],
  "risk_flags": [],
  "candidate_actions": [],
  "agent_report": {},
  "model": "inclusionAI/Ling-2.6-1T"
}
```

**Step 2: Write tests**

Tests:

- `save_agent_snapshot()` writes JSON.
- `load_latest_agent_snapshot()` returns newest snapshot.
- `diff_agent_snapshots()` identifies new holdings, removed holdings, total value delta, risk flag delta.

**Step 3: Implement helpers**

Functions:

- `agent_snapshot_dir(config) -> Path`
- `save_agent_snapshot(snapshot, config) -> Path`
- `list_agent_snapshots(config) -> list[Path]`
- `load_latest_agent_snapshot(config) -> dict[str, Any] | None`
- `diff_agent_snapshots(previous, current) -> dict[str, Any]`

**Step 4: Add `.gitignore` entries**

```gitignore
data/state/*
!data/state/.gitkeep
```

**Step 5: Verify**

```bash
uv run python -m unittest tests.test_agent_memory
uv run python -m unittest discover -s tests
```

Expected:

```text
OK
```

---

### Task 9: Strict LLM Agent Report Validation

**Files:**

- Modify: `/Users/liyanran/github/stock/stock_assistant.py`
- Test: `/Users/liyanran/github/stock/tests/test_agent_llm.py`

**Step 1: Write tests**

Tests:

- Valid JSON parses.
- Markdown-wrapped JSON parses.
- Missing fields are filled with defaults.
- Invalid JSON returns a fallback report built from candidate actions.
- LLM cannot create an action not in candidate action IDs when `llm_can_create_new_actions=false`.

**Step 2: Add parser**

Add:

```python
def strip_json_markdown(text: str) -> str:
    clean = text.strip()
    if clean.startswith("```json"):
        return clean[7:-3].strip()
    if clean.startswith("```"):
        return clean[3:-3].strip()
    return clean
```

Add:

```python
def parse_agent_report(text: str, candidate_actions: list[CandidateAction], config: dict[str, Any]) -> dict[str, Any]:
    try:
        payload = json.loads(strip_json_markdown(text))
    except json.JSONDecodeError:
        return fallback_agent_report(candidate_actions, "LLM 输出不是合法 JSON")
    return validate_agent_report(payload, candidate_actions, config)
```

**Step 3: Add fallback report**

```python
def fallback_agent_report(candidate_actions: list[CandidateAction], reason: str) -> dict[str, Any]:
    return {
        "summary": {
            "health_score": None,
            "status": "fallback",
            "brief": f"AI 诊断失败，已返回规则引擎结果: {reason}",
        },
        "risk_tags": [],
        "action_items": [candidate_action_to_dict(item) for item in candidate_actions],
        "watch_conditions": [],
        "questions": [],
        "evidence": [],
    }
```

**Step 4: Modify LLM prompt**

The LLM prompt must include:

- Candidate actions with IDs.
- Explicit instruction: only choose, rank, explain, or ask questions.
- Explicit instruction: do not invent holdings, news, macro facts, or order instructions.
- JSON schema.

**Step 5: Verify**

```bash
uv run python -m unittest tests.test_agent_llm
uv run python -m unittest discover -s tests
```

Expected:

```text
OK
```

---

### Task 10: Agent Orchestrator

**Files:**

- Modify: `/Users/liyanran/github/stock/stock_assistant.py`
- Modify: `/Users/liyanran/github/stock/api.py`
- Test: `/Users/liyanran/github/stock/tests/test_agent_orchestrator.py`

**Step 1: Write test with mocked tools**

Mock:

- `fetch_tzzb_holdings`
- `fetch_bars`
- `call_llm`

Expected:

- Classifications are produced.
- Analysis results are produced.
- Summary and policy flags are produced.
- Candidate actions are produced.
- Agent report is parsed.
- Snapshot is saved when enabled.

**Step 2: Implement orchestrator**

Add:

```python
def run_agent_analysis(
    config: dict[str, Any],
    holdings: list[Holding] | None = None,
    model_override: str | None = None,
    save_snapshot: bool = True,
) -> dict[str, Any]:
    if holdings is None:
        if str(config.get("ledger", {}).get("mode", "")).strip().lower() != "tzzb_api":
            raise RuntimeError("agent 模式当前需要 ledger.mode=tzzb_api 或传入 holdings")
        holdings, source, ledger_summary = fetch_tzzb_holdings(config)
    else:
        source = None
        ledger_summary = {}

    classifications = {h.code: classify_holding(h, config) for h in holdings}
    technical_results = analyze_holdings(holdings, config)
    summary = summarize_portfolio(holdings, classifications, config)
    risk_flags = evaluate_policy(summary, config)
    candidate_actions = generate_candidate_actions(technical_results, summary, risk_flags, config)
    previous = load_latest_agent_snapshot(config)

    report = generate_agent_report_with_llm(
        holdings=holdings,
        classifications=classifications,
        technical_results=technical_results,
        summary=summary,
        risk_flags=risk_flags,
        candidate_actions=candidate_actions,
        previous_snapshot=previous,
        config=config,
        model_override=model_override,
    )

    snapshot = build_agent_snapshot(...)
    if save_snapshot and config_bool(config.get("agent", {}).get("save_snapshots", True)):
        save_agent_snapshot(snapshot, config)
    return snapshot
```

**Step 3: Add API endpoint**

In `/Users/liyanran/github/stock/api.py`:

- Keep `/api/analyze` for compatibility.
- Add `POST /api/agent/run`.
- Add `GET /api/agent/latest`.
- Add `GET /api/agent/history`.

The first version of `/api/agent/run` can be synchronous JSON. SSE can come after correctness.

**Step 4: Verify**

```bash
uv run python -m unittest tests.test_agent_orchestrator
uv run python -m unittest discover -s tests
```

Expected:

```text
OK
```

---

### Task 11: Alert Engine And CLI Check Command

**Files:**

- Modify: `/Users/liyanran/github/stock/stock_assistant.py`
- Modify: `/Users/liyanran/github/stock/README.md`
- Test: `/Users/liyanran/github/stock/tests/test_alerts.py`

**Step 1: Define alert types**

Alert codes:

- `cookie_expired`
- `market_data_stale`
- `single_position_overweight`
- `sector_overweight`
- `unknown_classification_too_high`
- `technical_breakdown`
- `large_daily_loss`

**Step 2: Implement alert evaluation**

```python
def evaluate_alerts(agent_snapshot: dict[str, Any], config: dict[str, Any]) -> list[dict[str, Any]]:
    alerts = []
    for flag in agent_snapshot.get("risk_flags", []):
        if flag.get("severity") in {"high", "critical"}:
            alerts.append({
                "code": flag.get("code"),
                "severity": flag.get("severity"),
                "message": flag.get("label"),
                "evidence": flag.get("evidence", []),
            })
    return alerts
```

**Step 3: Add CLI command**

In `build_parser()` add:

```python
check_parser = subparsers.add_parser("check", help="运行 agent 检查并输出告警")
check_parser.add_argument("--json", action="store_true", help="输出 JSON")
```

In `main()`:

```python
if command == "check":
    snapshot = run_agent_analysis(config)
    alerts = evaluate_alerts(snapshot, config)
    if args.json:
        print(json.dumps(alerts, ensure_ascii=False, indent=2))
    else:
        print(alerts_markdown(alerts))
    return 0
```

**Step 4: Verify**

```bash
uv run python -m unittest tests.test_alerts
uv run python -m unittest discover -s tests
```

Expected:

```text
OK
```

---

### Task 12: Frontend Types And Agent Panels

**Files:**

- Create: `/Users/liyanran/github/stock/frontend/src/types.ts`
- Modify: `/Users/liyanran/github/stock/frontend/src/App.tsx`
- Optional create later: `/Users/liyanran/github/stock/frontend/src/components/RiskPanel.tsx`
- Optional create later: `/Users/liyanran/github/stock/frontend/src/components/ActionItems.tsx`
- Optional create later: `/Users/liyanran/github/stock/frontend/src/components/HistoryDiff.tsx`

**Step 1: Add types**

Create `types.ts`:

```ts
export type Holding = {
  code: string;
  name: string;
  quantity: number | null;
  cost_price: number | null;
  market_value: number | null;
  profit_pct: number | null;
  hold_profit: number | null;
  day_profit: number | null;
  asset_type: 'etf' | 'fund' | string;
  weight?: number | null;
  action?: string;
  reason?: string;
};

export type AgentActionItem = {
  id: string;
  type: 'buy' | 'reduce' | 'hold' | 'rebalance' | 'watch' | string;
  target_code?: string;
  target_name?: string;
  priority?: 'low' | 'medium' | 'high' | 'critical' | string;
  reason: string;
  evidence?: string[];
  requires_user_confirmation?: boolean;
};

export type AgentReport = {
  summary?: {
    health_score?: number | null;
    status?: string;
    brief?: string;
  };
  risk_tags?: Array<{
    code?: string;
    label: string;
    severity?: string;
    evidence?: string[];
  }>;
  action_items?: AgentActionItem[];
  watch_conditions?: Array<{
    target_code?: string;
    condition: string;
    meaning?: string;
  }>;
  questions?: Array<{
    id?: string;
    question: string;
  }>;
};
```

**Step 2: Replace core `any` usage**

Do not try to eliminate every `any` in one pass. First replace:

- `data`
- `aiData`
- `technicalResults`
- `HoldingCard` props

**Step 3: Add agent run button**

Call:

```ts
await fetch('/api/agent/run', { method: 'POST' })
```

Render:

- Summary.
- Risk tags.
- Action items.
- Watch conditions.
- Questions requiring user input.

**Step 4: Verify**

```bash
npm run build
```

from:

```bash
/Users/liyanran/github/stock/frontend
```

Expected:

```text
vite build
```

finishes without errors.

---

### Task 13: Optional SSE For Agent Progress

**Files:**

- Modify: `/Users/liyanran/github/stock/api.py`
- Modify: `/Users/liyanran/github/stock/frontend/src/App.tsx`
- Test: Add API-level smoke test only if test client is introduced.

**Step 1: Define progress steps**

Steps:

1. `sync_holdings`
2. `classify`
3. `market_data`
4. `technical_analysis`
5. `policy_eval`
6. `llm_report`
7. `save_snapshot`
8. `done`

**Step 2: Add `POST /api/agent/run/stream`**

Return event-stream messages:

```json
{"step":"sync_holdings","status":"正在同步投资账本"}
{"step":"classify","status":"正在分类持仓"}
{"step":"done","snapshot":{...}}
```

**Step 3: Update frontend**

Reuse existing log panel from `/api/analyze`.

**Step 4: Verify manually**

Start API:

```bash
uv run uvicorn api:app --reload --port 8000
```

Start frontend:

```bash
npm run dev
```

Then click agent run and inspect browser console.

---

### Task 14: Documentation

**Files:**

- Modify: `/Users/liyanran/github/stock/README.md`
- Modify: `/Users/liyanran/github/stock/config.example.toml`

Add sections:

- What the agent does.
- What the agent does not do.
- LLM privacy warning.
- Search privacy warning.
- How classification works.
- How to override classification.
- How to run daily check.
- How to read risk tags.

Important wording:

```markdown
本工具只生成持仓分析、观察条件和可审阅的候选动作，不自动下单，不构成投资建议或收益承诺。
```

---

## 7. Search Tool Roadmap

### Phase A: No External Search

Use:

- Config overrides.
- Name rules.
- Manual JSON search results.
- Cache.

Pros:

- Private.
- Testable.
- No API key.
- No network dependency.

Cons:

- Unknown instruments require manual work.
- Classification can be incomplete.

### Phase B: External Search Provider

Add:

```toml
[search]
enabled = true
provider = "http_api"
base_url = "..."
api_key_env = "SEARCH_API_KEY"
```

Implement:

- `HttpSearchProvider`
- Timeout handling.
- Rate-limit handling.
- Result cache.
- Source allowlist if desired.

Recommended source priority:

1. Fund company official product pages.
2. Exchange pages.
3. Index provider pages.
4. Major financial data pages.
5. General web snippets only as fallback.

### Phase C: Search Plus LLM Evidence Mapper

Input:

```json
{
  "instrument": {"code": "512880", "name": "证券ETF"},
  "search_results": [
    {"title": "...", "url": "...", "snippet": "..."}
  ],
  "taxonomy": {
    "asset_classes": ["broad_index", "sector_equity", "..."],
    "sectors": ["financials", "semiconductor", "..."]
  }
}
```

Output:

```json
{
  "asset_class": "sector_equity",
  "sector": "financials",
  "theme": "brokerage",
  "confidence": 0.86,
  "evidence_urls": ["..."],
  "needs_user_review": false
}
```

Validation:

- Reject taxonomy values not in the controlled vocabulary.
- Reject classification without evidence URL when source is search.
- Require user review below configured confidence.

---

## 8. Privacy And Safety Rules

LLM can receive:

- Holdings code/name.
- Market value if user allows external LLM.
- Technical indicators.
- Risk flags.
- Candidate actions.

LLM should not receive unless user explicitly allows:

- Account identifiers.
- Raw cookies.
- TZZB response dumps.
- Exact broker/account names.
- API keys.

Search provider can receive:

- Instrument code/name.
- Metadata query terms.

Search provider should not receive:

- Portfolio weights.
- Profit/loss.
- Position size.
- User risk profile.

The agent must not:

- Execute trades.
- Generate order files for direct import unless explicitly requested in a later phase.
- Present LLM output as guaranteed investment advice.
- Hide whether a recommendation came from rules, search evidence, or LLM inference.

---

## 9. Open Questions To Confirm

These decisions should be confirmed before implementing Tasks 5, 10, 12, and 13.

1. Search provider: do you want to start with `manual_json`, or do you already have a preferred search API/tool?
2. External search privacy: is it acceptable to send instrument code/name to an external search provider?
3. External LLM privacy: is it acceptable to send market value, profit/loss, and weights to the LLM provider, or should values be bucketed/rounded?
4. Classification taxonomy: do you want a coarse taxonomy like `broad_index/sector/bond/overseas`, or a more detailed industry taxonomy from the start?
5. User review: should uncertain classifications require explicit confirmation in the UI before being used in policy limits?
6. Agent autonomy: should the agent only produce recommendations, or should it also maintain an "intended target allocation" draft?
7. Alerts: where should alerts go first: local report only, Feishu, email, or desktop notification?
8. Schedule: run only after A-share close, or also before open / midday?
9. Field coverage: should场外基金 be analyzed by净值 and持仓主题, or only included in allocation/risk at first?
10. Data retention: how long should snapshots and search cache be kept?
11. Model capability: do you want model-side tool calling later, or keep deterministic orchestration and use the LLM only for report generation?
12. UI direction: should the first screen remain a dashboard, or become an agent task console with history and review queue?

Recommended defaults if not confirmed:

- Start with `manual_json` search provider.
- Allow search only for code/name metadata.
- Allow LLM to see rounded values and percentages, not raw account data.
- Use coarse taxonomy first.
- Require review for confidence below `0.75`.
- No auto trading.
- Local alert report first.
- Daily after market close.
- Include场外基金 only in allocation/risk in the first version.
- Keep snapshots for 180 days.
- Keep deterministic orchestration.
- Keep dashboard first, add an agent review panel.

---

## 10. Verification Matrix

Backend:

```bash
uv run python -m unittest discover -s tests
```

Frontend:

```bash
cd /Users/liyanran/github/stock/frontend
npm run build
```

CLI smoke:

```bash
cd /Users/liyanran/github/stock
uv run python stock_assistant.py analyze tests/fixtures/holdings.csv
```

Agent smoke after Task 10:

```bash
cd /Users/liyanran/github/stock
uv run python stock_assistant.py --config config.toml check --json
```

API smoke after Task 10:

```bash
cd /Users/liyanran/github/stock
uv run uvicorn api:app --port 8000
```

Then:

```bash
curl -s http://127.0.0.1:8000/api/agent/latest
```

Expected:

- If no snapshot exists, returns a clear empty state.
- If snapshot exists, returns latest agent snapshot.

---

## 11. Suggested Milestones

### Milestone 1: Stable Current App

Scope:

- Task 0.

Exit criteria:

- Backend tests pass.
- Frontend build passes.
- CLI sample report works.

### Milestone 2: Agent Data Foundation

Scope:

- Task 1.
- Task 2.
- Task 3.
- Task 4.

Exit criteria:

- Holdings serialize consistently.
- Policy config exists.
- Classification works with config/name/cache.
- Search interface exists but can remain disabled.

### Milestone 3: Deterministic Agent Brain

Scope:

- Task 6.
- Task 7.
- Task 8.

Exit criteria:

- Portfolio summary exists.
- Risk flags exist.
- Candidate actions exist.
- Snapshots and diff exist.

### Milestone 4: LLM Agent Report

Scope:

- Task 9.
- Task 10.

Exit criteria:

- LLM output is schema-validated.
- Invalid LLM output falls back to rule engine.
- `/api/agent/run` returns complete agent snapshot.

### Milestone 5: Search-Backed Classification

Scope:

- Task 5.

Exit criteria:

- Unknown instruments can be enriched from search/manual evidence.
- Evidence is cached.
- Low-confidence classification is marked for review.

### Milestone 6: Product Experience

Scope:

- Task 11.
- Task 12.
- Task 13.
- Task 14.

Exit criteria:

- Alerts work.
- Frontend shows agent report, risks, actions, questions, and history.
- README explains operation and privacy.

---

## 12. Non-Goals For The First Version

Do not implement these in the first version:

- Automatic trading.
- Broker order submission.
- Multi-user auth.
- Cloud deployment.
- Complex database migration.
- Full news sentiment engine.
- Intraday high-frequency monitoring.
- Backtesting allocation strategy.

These can come later after the local personal agent is stable.

