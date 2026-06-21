# 台股均線回踩選股

依均線回踩條件掃描台股上市 + 上櫃個股，繪製 K 線圖，並透過 **Telegram Bot** 推播結果。

## 選股條件

1. **漲幅**：最近 20 根 K 棒內 `(最高 - 最低) / 最低 > 10%`
2. **均線回踩**：
   - 5MA 下穿 10MA
   - 5MA 在 10MA 下方整理 3～10 根
   - 5MA 上穿 10MA，上穿當天起至第 5 根內出現回踩 K 棒
   - 回踩 K 棒最低價在 MA5 或 MA10 的 ±1% 以內
3. **多頭排列**：MA20 > MA60 > MA120
4. **量能**：當日成交量 > 500 張

**Telegram 推播（優化後，2026-06-01 回測衍生）**：在 v1 通過的 A+B 中再篩選 — A 級全收；B 級需 20K 漲幅 ≤30%、量比 ≥1.0×、距 20 日高 ≥3%；MA10 回踩另需量比 ≥1.2×。詳見 `src/screener/optimized_filter.py`。還原全部 A+B：`--legacy-v1-all`。

## 快速開始

### 1. 安裝

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# 編輯 .env 填入憑證
```

### 2. 環境變數

| 變數 | 必填 | 說明 |
|------|------|------|
| `TELEGRAM_BOT_TOKEN` | 是 | Telegram Bot Token（向 [@BotFather](https://t.me/BotFather) 申請） |
| `TELEGRAM_CHAT_ID` | 是 | 您的 Chat ID（個人或群組） |
| `FINMIND_TOKEN` | 建議 | FinMind API Token，加速資料抓取 |

#### 取得 Telegram Chat ID

1. 在 Telegram 搜尋並對您的 Bot 傳送任意訊息
2. 瀏覽器開啟：`https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates`
3. 在回應 JSON 中找到 `"chat":{"id": 123456789}`，即為 `TELEGRAM_CHAT_ID`

### 3. 本地執行

```bash
# 建議使用 venv 內的 Python 3.11
.venv/bin/python3.11 main.py --dry-run

# 限制 50 檔測試
.venv/bin/python3.11 main.py --dry-run --limit 50

# 正式執行 + Telegram 推播（優化版雙則）
.venv/bin/python3.11 main.py --skip-backtest

# 強制重跑 3 年歷史回測（離線分析，不進 Telegram）
.venv/bin/python3.11 main.py --refresh-backtest --dry-run
```

### 4. 單元測試

```bash
pytest tests/ -v
```

## macOS 本機排程（MacBook 每日 16:00）

使用 `launchd` 在**本機時區**每天下午 **16:00** 執行掃描並推播 Telegram。

```bash
# 安裝排程（需已設定 .env）
chmod +x scripts/install_mac_schedule.sh scripts/daily_scan.sh
./scripts/install_mac_schedule.sh install

# 立即測試一次
./scripts/install_mac_schedule.sh run-now

# 查看狀態 / 移除
./scripts/install_mac_schedule.sh status
./scripts/install_mac_schedule.sh uninstall
```

- 執行腳本：`scripts/daily_scan.sh`（`--skip-backtest --skip-theme`）
- 日誌目錄：`logs/`（含 `daily_scan_YYYYMMDD_HHMMSS.log`）
- **注意**：Mac 需保持開機或喚醒，且已登入使用者帳號，排程才會觸發

## GitHub Actions 排程（選用）

Workflow 檔：`.github/workflows/daily_scan.yml`

- **排程**：週日至週五 09:00 UTC（台灣時間 17:00；週六不跑）
- **手動觸發**：GitHub → Actions → Daily Stock Scan → Run workflow
- **前瞻累積**：Actions Cache 保存 `data/backtest_optimized.db`、`benchmark_0050.parquet`、`market_cap.json` 等

### 設定 GitHub Secrets

在 repo **Settings → Secrets and variables → Actions** 新增：

| Secret | 說明 |
|--------|------|
| `TELEGRAM_BOT_TOKEN` | Telegram Bot Token |
| `TELEGRAM_CHAT_ID` | 您的 Chat ID |
| `FINMIND_TOKEN` | FinMind API Token |

## 回測功能

每次掃描後自動：

1. **前瞻追蹤（優化版）**：僅記錄優化後信號，隔日開盤買入，依停損/停利規則結算；第二則 Telegram 推播累計統計與當日結算明細
2. **歷史回測**：近 3 年全市場信號回測（`--refresh-backtest` 離線用，**不進** Telegram 推播）

**報酬計算（每日排程 / Telegram 回測統計）：**
- 買入：信號日隔日開盤價
- 停損：**-10%**（當日 low 觸及）
- 停利：**+30%**（當日 high 觸及）
- 到期：最多持有 **20** 交易日，未觸發則第 20 日收盤賣出
- 同日同時觸及：保守先判停損
- 基準：0050 同區間報酬（alpha = 個股報酬 - 0050 報酬）

**輸出檔案：**
- `data/backtest_optimized.db` — 優化版前瞻追蹤 SQLite
- `data/backtest.db` — 舊版前瞻追蹤（已停用寫入）
- `data/backtest_summary.json` — 歷史回測摘要快取
- `data/backtest_trades.csv` — 歷史回測交易明細

**信號日固定持有期回測**（指定出場日，非 SL/TP 規則）：

```bash
.venv/bin/python3.11 scripts/analyze_signal_date_hold.py \
  --signal-date 2026-06-01 --exit-date 2026-06-18
```

輸出：`data/reports/signal_hold_YYYYMMDD_YYYYMMDD.{json,csv}`。詳見 `.cursor/skills/strategy1-signal-hold-analysis/SKILL.md`。

Telegram 摘要會顯示每檔 **產業** 與 **族群**，以及今日信號的產業/族群分布。前瞻回測統計改由**第二則訊息**獨立推播。

## 低位題材動能選股（選用，預設關閉）

與均線回踩分開掃描；需 `--enable-theme` 才會推播第三則訊息。

| 條件 | 門檻 |
|------|------|
| 低位階 | 收盤價 ≤ **80** 元 |
| 小市值 | 市值 < **300** 億（TWSE/TPEX 公開資料） |
| 籌碼集中 | 董監持股 ≥ **25%**（TWSE 公開資料） |
| 動能 / 題材 | 20 日漲幅 ≥ **12%** |
| 突破量能 | 量比 > **1.5×**（20 日均量）且收盤突破 20 日高 |
| 熱門產業 | 候選股依產業密度 + 漲幅動態排名，取前 **5** 名產業 |

```bash
# 啟用題材動能推播
.venv/bin/python3.11 main.py --enable-theme

# 測試
.venv/bin/python3.11 main.py --dry-run --limit 100 --skip-backtest
```

- K 線圖：`output/theme/{股號}.png`
- 快取：`data/market_cap.json`、`data/shareholding.json`（7 天 TTL）
- 本階段**不含**獨立前瞻追蹤 / 歷史回測

## 止損/止盈組合回測

獨立腳本，沿用相同選股信號池，對 **3×3 = 9 種** 止損/止盈組合進行近 3 年回測：

| 止損 | 止盈 |
|------|------|
| 上穿均價（golden_cross 當日 `(MA5+MA10)/2`） | +10% |
| -5% | +20% |
| -10% | +30% |

**規則：**
- 買入：信號日隔日開盤價
- 逐日檢查：保守先判止損（同日觸及 SL/TP 以止損為準）
- 強制平倉：第 20 交易日收盤（若未觸發 SL/TP）

```bash
# 執行回測（24 小時快取）
.venv/bin/python3.11 scripts/run_sl_tp_backtest.py

# 強制重跑
.venv/bin/python3.11 scripts/run_sl_tp_backtest.py --refresh

# 測試用：限制 50 檔
.venv/bin/python3.11 scripts/run_sl_tp_backtest.py --limit 50 --refresh
```

**輸出：**
- `data/sl_tp_backtest_summary.json` — 9 組合勝率、均報酬、Profit Factor、盈虧比
- `data/sl_tp_backtest_trades.csv` — 每筆交易明細

## 策略 v2 與參數網格回測

**v1**（`main.py` 每日掃描）維持不變。**v2** 為更嚴格選股 + 優化 SL/TP，僅供回測分析。

### v2 選股條件（在 v1 基礎上）

- 20K 漲幅 > **15%**（v1 為 10%）
- 收盤站回 **回踩均線** 上方（MA5 或 MA10）
- **MA5 > MA10**、**收盤 > MA20**、**MA20 斜率 > 0**
- 信號日成交量 > **5 日均量 × 1.2**（且 > 500 張）
- 上穿後 **3 日內** 回踩（v1 為 5 日）

### v2 SL/TP

| 止損 | 止盈 | 持有 |
|------|------|------|
| -10% | +25% / +30% | 20 / 30 日 |
| 上穿日最低價 | | |
| 上穿均價（進場日不判止損） | | |

進場時機網格：**隔日開盤** / **信號日收盤**。整理期下限網格：**3 / 5 / 6** 根。

```bash
# 近 1 年參數網格（72 組合，24 小時快取）
.venv/bin/python3.11 scripts/run_strategy_grid.py --years 1

# 強制重跑
.venv/bin/python3.11 scripts/run_strategy_grid.py --years 1 --refresh

# 測試：限制 50 檔
.venv/bin/python3.11 scripts/run_strategy_grid.py --limit 50 --refresh
```

**輸出：**
- `data/strategy_grid_summary.json`
- `data/strategy_grid_trades.csv`

## 專案結構

```
├── main.py                     # CLI 入口
├── scripts/
│   ├── run_sl_tp_backtest.py   # v1 止損/止盈 9 組合回測
│   └── run_strategy_grid.py    # v2 參數網格回測
├── src/
│   ├── backtest/               # 回測、前瞻追蹤
│   ├── data/                   # 股號清單、OHLCV、0050 基準
│   ├── indicators/             # 均線計算
│   ├── screener/               # 選股條件與掃描（均線回踩 + 題材動能）
│   ├── chart/                  # K 線圖
│   └── notify/                 # Telegram 推播
├── tests/                      # 單元測試
└── .github/workflows/          # 排程
```

## Telegram 推播說明

**均線回踩 — 優化版（第一則）**
- 文字摘要（v1→優化檔數、A/B 分級、產業分布）
- 逐檔 K 線圖：`output/{股號}.png`

**前瞻回測 — 優化版（第二則）**
- 停損 -10%/停利 +30% 累計勝率、均報酬、追蹤中檔數
- 當日新結算明細（停損 / 停利 / 到期）

**低位題材動能（選用，`--enable-theme`）**
- 文字摘要（熱門產業、市值/籌碼/漲幅）
- 逐檔 K 線圖：`output/theme/{股號}.png`

- 若無符合標的：各策略分別推送「今日無符合條件個股」
- 非交易日：推送「非交易日，略過掃描」

## 注意事項

- 全市場掃描約需 5～15 分鐘（視 FinMind / 網路速度）
- 強烈建議設定 `FINMIND_TOKEN`，yfinance 備援較慢且不穩
- **請勿將 `.env` 或 Token 提交至 Git**
- GitHub Actions 免費額度：私有 repo 每月 2,000 分鐘
- K 線圖會上傳至 GitHub Actions artifact（保留 7 天）
- 歷史回測首次執行約 15～30 分鐘，之後使用 24 小時快取