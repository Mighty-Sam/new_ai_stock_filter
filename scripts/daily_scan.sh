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

# 週日重跑回測；其餘使用快取（與 GitHub Actions 一致）
DOW="$(date +%u)"
if [[ "$DOW" == "7" ]]; then
  EXTRA_ARGS=(--refresh-backtest)
else
  EXTRA_ARGS=(--skip-backtest)
fi

"$PYTHON" "$ROOT/main.py" "${EXTRA_ARGS[@]}"
echo "=== daily_scan end $(date -Iseconds) exit=$? ==="
