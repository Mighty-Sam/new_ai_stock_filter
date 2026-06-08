#!/usr/bin/env bash
# 安裝 / 移除 macOS 每日 16:00 本機排程（launchd）
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.new_ai_stock_filter.daily"
PLIST_SRC="$ROOT/scripts/com.new_ai_stock_filter.daily.plist"
PLIST_DST="$HOME/Library/LaunchAgents/${LABEL}.plist"
SCAN_SH="$ROOT/scripts/daily_scan.sh"

usage() {
  cat <<EOF
用法: $0 [install|uninstall|status|run-now]

  install   安裝每日 16:00 排程（本機時區）
  uninstall 移除排程
  status    顯示 launchd 狀態
  run-now   立即執行一次（測試用）
EOF
}

cmd="${1:-install}"

case "$cmd" in
  install)
    chmod +x "$SCAN_SH"
    mkdir -p "$ROOT/logs"
    sed "s|__PROJECT_ROOT__|$ROOT|g" "$PLIST_SRC" > "$PLIST_DST"
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$PLIST_DST"
    launchctl enable "gui/$(id -u)/$LABEL"
    echo "已安裝：每日 16:00 執行 $SCAN_SH"
    echo "plist：$PLIST_DST"
    echo "日誌：$ROOT/logs/"
    ;;
  uninstall)
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
    rm -f "$PLIST_DST"
    echo "已移除排程 $LABEL"
    ;;
  status)
    launchctl print "gui/$(id -u)/$LABEL" 2>/dev/null || echo "排程未安裝"
    ;;
  run-now)
    chmod +x "$SCAN_SH"
    echo "立即執行…"
    "$SCAN_SH"
    echo "完成，請查看 $ROOT/logs/"
    ;;
  *)
    usage
    exit 1
    ;;
esac
