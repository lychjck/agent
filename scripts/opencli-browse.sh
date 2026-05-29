#!/usr/bin/env bash
# opencli-browse.sh — opencli browser 包装器，防止 tab group 增殖
#
# 核心策略:
#   1. 始终使用固定 session 名 "stock"，确保只维护一个 tab group
#   2. 退出时只释放 lease，不关闭 placeholder 窗口（让扩展下次复用）
#   3. 这样无论调用多少次，Chrome 中始终只有一个 "OpenCLI Browser" group
#
# 用法:
#   opencli-browse.sh <url>
#   opencli-browse.sh close    # 手动释放 session

set -euo pipefail

SESSION="stock"

if [ "${1:-}" = "close" ]; then
    opencli browser "$SESSION" close 2>/dev/null || true
    echo "[opencli] session '$SESSION' 已释放"
    exit 0
fi

URL="${1:?用法: opencli-browse.sh <url>}"

cleanup() {
    opencli browser "$SESSION" close 2>/dev/null || true
}
trap cleanup EXIT INT TERM

opencli browser "$SESSION" open "$URL" 2>&1

echo ""
echo "按 Enter 或 Ctrl+C 退出..."
read -r
