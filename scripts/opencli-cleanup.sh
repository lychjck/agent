#!/usr/bin/env bash
# opencli-cleanup.sh — 清理 opencli 残留的 tab group
#
# 问题:
#   opencli 的 "OpenCLI Browser" tab group 在 close 后会保留 placeholder。
#   daemon/扩展重启可能导致产生多个孤儿 tab group。
#
# 方案:
#   1. 关闭 opencli 的 placeholder 窗口（只含 about:blank 的窗口）
#   2. 重启 daemon 让扩展重新初始化状态
#   3. 下次 opencli browser open 时会创建一个干净的新 group
#
# 注意:
#   已有的空 tab group 无法通过脚本删除（需要 Chrome 扩展 API）。
#   如果仍有残留的空 group，需要在 Chrome 中手动右键 → "关闭群组"。
#   清理后，后续使用只要保持用固定 session 名，就不会再产生多余 group。
#
# 用法:
#   opencli-cleanup.sh

set -euo pipefail

echo "[cleanup] Step 1: 关闭 opencli placeholder 窗口..."

RESULT=$(osascript <<'APPLESCRIPT'
tell application "Google Chrome"
    set closedCount to 0
    set windowCount to count of windows
    repeat with w from windowCount to 1 by -1
        set tabCount to count of tabs of window w
        set allBlank to true
        repeat with t from 1 to tabCount
            if URL of tab t of window w is not "about:blank" then
                set allBlank to false
                exit repeat
            end if
        end repeat
        if allBlank and tabCount > 0 then
            close window w
            set closedCount to closedCount + 1
        end if
    end repeat
    return closedCount
end tell
APPLESCRIPT
)
echo "  关闭了 $RESULT 个 placeholder 窗口"

echo "[cleanup] Step 2: 重启 opencli daemon 清除旧状态..."
opencli daemon restart 2>/dev/null || true
sleep 2

# 等待扩展重连
for i in {1..5}; do
    STATUS=$(opencli daemon status 2>&1)
    if echo "$STATUS" | grep -q "Extension: connected"; then
        echo "  daemon 已重启，扩展已连接"
        break
    fi
    sleep 1
done

echo ""
echo "[cleanup] 清理完成。"
echo ""
echo "如果 Chrome 中仍有残留的空 'OpenCLI Browser' tab group："
echo "  → 右键点击 tab group 标题 → 选择 '关闭群组' / 'Close group'"
echo ""
echo "后续使用建议："
echo "  → 所有 opencli browser 操作使用固定 session 名（如 'stock'）"
echo "  → 不要手动关闭 opencli 的 placeholder 窗口（让它保持以便复用）"
echo "  → 这样就只会维护一个 tab group，不会再产生多余的"
