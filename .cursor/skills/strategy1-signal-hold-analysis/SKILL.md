---
name: strategy1-signal-hold-analysis
description: >-
  策略一（均線回踩）指定信號日回測：隔日開盤買、固定出場日收盤賣、個股報酬與
  漲跌分桶統計（累計 ±10/20/30%）、A/B 與 MA5/MA10 分拆、策略優化分析。
  Use when user asks for 策略一回測、信號日持有分析、固定出場回測、
  signal date hold analysis、某日出場報酬統計、優化篩選清單。
disable-model-invocation: true
---

# 策略一信號日固定持有期回測

## 適用情境

使用者指定：
- **信號日**（例如 2026-06-01）：當日收盤後符合策略一（均線回踩 v1）的 A+B 信號
- **出場日**（例如 2026-06-18）：該日收盤價賣出
- 進場規則：**信號日 T+1 開盤**買入一張（不含手續費/滑價）

**預設已套用優化篩選**（見下方）；`--legacy-v1-all` 可還原優化前全部 A+B。

## 優化篩選規則（2026-06-01 回測衍生）

依 `2026-06-01 → 2026-06-18` 固定持有回測（70 檔 v1 → 優化後精簡）制定，實作於 `src/screener/optimized_filter.py`：

| 規則 | 門檻 | 理由 |
|------|------|------|
| A 級 | **全收** | 2 檔均報酬 +12.6%、勝率 100% |
| B 級 20K 漲幅 | **≤ 30%** | 漲幅 >30% 的 B 級（如 4768、6426）後續跌 -22% |
| B 級 量比 | **≥ 1.0×** | 量能偏弱 B 級拖累整體 |
| B 級 MA10 回踩 | 量比 **≥ 1.2×** | MA10 子樣本差於 MA5，需更高量能 |
| B 級 距 20 日高 | **≥ 3%** | 距高 ≤2% 易遇壓力回檔 |

**日常推播**（`main.py`）預設套用上述篩選；`--legacy-v1-all` 還原全部 A+B；`--grade-a-only` 僅 A 級。

## 每日 Telegram 推播（優化版）

預設推播**兩則**（題材動能預設關閉，需 `--enable-theme` 才推）：

| 順序 | 內容 | 說明 |
|------|------|------|
| 1 | 均線回踩（優化版） | v1→優化檔數、A/B 清單、K 線圖；**不含**近 3 年歷史回測 |
| 2 | 前瞻回測（優化版） | 停損 -10%/停利 +30% 累計勝率/均報酬 + **當日新結算明細** |

前瞻追蹤僅記錄**優化後信號**，存於 `data/backtest_optimized.db`（與舊 `backtest.db` 分離）。

GitHub Actions 排程：週日至週五 09:00 UTC（`.github/workflows/daily_scan.yml`）。

## 執行命令

```bash
cd /Users/sam.rm.lee/Desktop/AI_side_project/new_ai_stock_filter

# 僅列出優化後信號清單
.venv/bin/python3.11 scripts/analyze_signal_date_hold.py \
  --signal-date 2026-06-01 \
  --exit-date 2026-06-18 \
  --list-only

# 優化後信號 + 固定持有回測
.venv/bin/python3.11 scripts/analyze_signal_date_hold.py \
  --signal-date 2026-06-01 \
  --exit-date 2026-06-18

# 優化前（全部 A+B）對照
.venv/bin/python3.11 scripts/analyze_signal_date_hold.py \
  --signal-date 2026-06-01 \
  --exit-date 2026-06-18 \
  --legacy-v1-all
```

可選參數：
- `--limit N`：只掃前 N 檔（smoke test）
- `--compare-sl-tp`：附加「固定出場 vs 停損 -10%/停利 +30%」對照
- `--list-only`：只輸出信號清單，不做持有期模擬
- `--legacy-v1-all`：不套用優化篩選

## 輸出

| 檔案 | 內容 |
|------|------|
| `data/reports/signal_hold_{signal}_{exit}_optimized.json` | 優化後摘要、分桶、trades |
| `data/reports/signal_hold_{signal}_{exit}_optimized.csv` | 優化後個股明細 |
| `data/reports/signal_hold_{signal}_{exit}_optimized_list.json` | `--list-only` 信號清單 |
| `data/reports/signal_hold_{signal}_{exit}.json` | `--legacy-v1-all` 未優化報告 |

### 分桶定義（累計）

- 漲：`>=10%`、`>=20%`、`>=30%`
- 跌：`<=-10%`、`<=-20%`、`<=-30%`

## 實作位置

| 模組 | 路徑 |
|------|------|
| **優化篩選** | `src/screener/optimized_filter.py` |
| 固定出場模擬 | `src/backtest/trade_simulator.py` → `simulate_fixed_exit` |
| 分桶統計 | `src/backtest/return_buckets.py` |
| CLI | `scripts/analyze_signal_date_hold.py` |
| 歷史日掃描 | `src/screener/scanner.py` → `scan_market(end_date=...)` |
| 日常推播 | `main.py`（預設 `filter_optimized`、雙則 Telegram） |
| 前瞻 DB | `data/backtest_optimized.db` |

## 完成後必做：優化分析 checklist

依 JSON/CSV 結果撰寫分析（繁體中文），至少涵蓋：

1. **整體**：平均/中位數報酬、勝率、打敗 0050 比例；樣本數是否足夠
2. **優化前後對照**：v1 檔數 vs 優化後檔數、報酬是否改善
3. **A vs B**：A 級是否仍明顯優於 B
4. **回踩均線**：ma5 vs ma10 子樣本
5. **極端值**：漲跌幅前 5
6. **大盤環境**：同期 0050 報酬
7. **停損停利對照**（若有 `--compare-sl-tp`）
8. **下一輪可調參數**：記錄於本 skill 的「優化篩選規則」表

## 注意事項

- 信號日須為**交易日**
- 2330 等個股**不一定**出現在指定信號日
- 全市場掃描約 **10～15 分鐘**
- 優化規則源自**單日回測**，需累積更多信號日驗證後再調參

## 基準回測（優化前）

2026-06-01 v1 全檔 70 檔 → 6/2 開買 → 6/18 收賣：平均 -1.81%、0050 +0.94%、跌 ≤-10% 有 22 檔。
