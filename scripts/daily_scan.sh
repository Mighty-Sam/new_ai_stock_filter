#!/usr/bin/env bash
# 每日選股掃描（供 launchd / 手動執行）
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

LOG_DIR="$ROOT/logs"
mkdir -p "$LOG_DIR"
STAMP="$(date +%Y%m%d_%H%M%S)"
LOG_FILE="$LOG_DIR/daily_scan_${STAMP}.log"

exec >>"$LOG_FILE" 2>&1
echo "=== daily_scan start $(date -Iseconds) ==="
echo "ROOT=$ROOT"

PYTHON="$ROOT/.venv/bin/python3.11"
if [[ ! -x "$PYTHON" ]]; then
  echo "ERROR: 找不到 $PYTHON，請先建立 venv"
  exit 1
fi

if [[ -f "$ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/.env"
  set +a
fi

# 優化版推播：略過 3 年歷史回測與題材動能，僅推播 A 級
"$PYTHON" "$ROOT/main.py" --skip-backtest --skip-theme --grade-a-only
echo "=== daily_scan end $(date -Iseconds) exit=$? ==="
