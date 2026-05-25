# Auto-register all tools when this package is imported
from stock_mcp.tools.holdings import (
    get_current_holdings, CurrentHoldingsArgs,
    get_portfolio_profile, PortfolioProfileArgs,
    get_classification, ClassificationArgs,
    get_current_account_bundle, AccountBundleArgs
)
from stock_mcp.tools.technical import get_holding_technical, HoldingTechnicalArgs
from stock_mcp.tools.etf import get_etf_constituents, EtfConstituentsArgs
from stock_mcp.tools.ledger import get_asset_trend, get_bs_point
from stock_mcp.tools.snapshots import (
    load_snapshot_summary, SnapshotSummaryArgs,
    compare_snapshots, CompareSnapshotsArgs,
    save_snapshot, SaveSnapshotArgs
)
from stock_mcp.tools.web import (
    web_search, WebSearchArgs,
    web_read, WebReadArgs,
    web_fetch, WebFetchArgs,
    opencli_command, OpenCliArgs
)
