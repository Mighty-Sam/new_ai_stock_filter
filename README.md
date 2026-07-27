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

### A 級第二通道（縮幅回踩）

在**完整通過 v1** 的前提下，若符合下列條件亦給予 **A 級**（與 v2 嚴選並列，任一即 A）：

- **20K 漲幅 ≤ 30%**（超過則即使 v2/縮幅回踩達標亦降為 B 級）

1. 最近 **50** 根 K 棒漲幅 > **15%**
2. **或** 進場型態二擇一：
   - 下影線 > 實體，且低點碰到 MA5 / MA10 / MA20（±1%）
   - 跳空開高 ≥2% + 小十字/紡錘（實體 ≤ 全幅 30%）+ 平盤預判次日 MA5 > MA10
3. MA20 趨勢向上（今日 MA20 > 10 根前 MA20）
4. 最近 **10** 根 K 棒振幅 < **12%**

實作：`src/screener/strategy_consolidation.py`；分級：`src/screener/grading.py`。

### 推播籌碼欄位（三大法人）

每日推播的個股摘要與 K 線圖 caption 可附法人籌碼（需 `FINMIND_TOKEN`）：

- 最近一個已公布交易日：外資 / 投信 / 自營商淨買超（張）
- 近 5 日合計淨買超、連續買超天數

GitHub Actions 排程為台灣 **17:00**，當日籌碼通常尚未公布，欄位日期多為**前一交易日**。資料來源：FinMind `TaiwanStockInstitutionalInvestorsBuySellWide`。

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

1. **前瞻追蹤（A 級批次）**：第二則推播為「20 交易日前信號日」的 A 級 SL/TP 回測；CI 僅記錄/推播 A 級
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

Telegram 摘要會顯示每檔 **產業** 與 **族群**。第二則為 20 交易日前 A 級信號的批次回測。

## 爆量價穩選股（每日預設推播）

與均線回踩、縮幅回踩 **完全獨立**；每日排程預設執行（第三則 Telegram）。

| 條件 | 門檻 |
|------|------|
| 量能 | 當日量 ≥ **4×** 前 **5** 日均量 **且** ≥ **4×** 前 **20** 日均量（均量不含當日） |
| 漲幅 | 當日 `(收盤−昨收)/昨收`，**0% ≤ 漲幅 < 3%** |
| 趨勢 | 收盤價 **> MA60** |
| 均線貼近 | 當日低點 ≤ **MA5 / MA10 / MA20** 中曾觸及的最高均線（容差 1% 略破下影） |
| 最低量 | ≥ **500 張**（500,000 股） |

```bash
# 測試（略過推播）
.venv/bin/python3.11 main.py --dry-run --limit 100 --skip-backtest

# 關閉爆量價穩掃描
.venv/bin/python3.11 main.py --skip-volume-surge
```

- K 線圖：`output/volume_surge/{股號}.png`
- 推播順序：均線回踩 → 前瞻回測 → 爆量價穩 → N漲W底假跌破 → **漲停量縮整理** →（選用）題材動能

## N漲W底假跌破選股（每日預設推播）

與其他策略完全獨立；每日排程預設執行（第四則 Telegram）。前波急漲後拉回，走出「假跌破」的 W 底，第二隻腳跌破第一隻腳低點但當日收盤又收復，視為主力洗盤而非真正破底。

| 條件 | 門檻 |
|------|------|
| 前波漲幅 | 最近 **50** 根 K 棒內，波段低點 → 波段高點漲幅介於 **20% ~ 45%**（回測顯示 45~50% 這段勝率/報酬明顯偏差，故收緊上限） |
| W 底 | 波段高點後第一腳（最低點）→ 反彈（須真正彈過第一腳低點）→ 今日第二腳 |
| 假跌破 | 今日低點 < 第一腳低點，但今日**收盤 ≥ 第一腳低點**，且跌破幅度 ≥ **2%**（避免只是碰一下的雜訊訊號） |
| 量能 | 今日成交量 ≥ **1000 張**（1,000,000 股） |
| 週線動能 | 週線 MA20 **連續 4 週**遞增（最新 5 個週值逐週上升；放寬至 2 週經回測驗證會拉低勝率與報酬） |
| 停利空間 | 訊號日收盤 → 停利目標（50K 次高點）距離 ≥ **5%**（不足者賺賠比太差，直接跳過） |

**回測規則：**
- 買入：訊號日隔日開盤價
- 停利：最近 **50** 根 K 棒的**次高點**（固定價位，非百分比）
- 停損：股價**收盤**跌破第二腳最低價（非盤中觸及）
- 同日同時觸及：保守先判停損；逾 **60** 交易日未觸發則強制收盤出場

```bash
# 測試（略過推播）
.venv/bin/python3.11 main.py --dry-run --limit 100 --skip-backtest

# 關閉 N漲W底假跌破掃描
.venv/bin/python3.11 main.py --skip-w-bottom

# 近 3 年歷史回測（24 小時快取）
.venv/bin/python3.11 scripts/run_w_bottom_backtest.py

# 強制重跑 / 測試用限制檔數
.venv/bin/python3.11 scripts/run_w_bottom_backtest.py --refresh --limit 50
```

- K 線圖：`output/w_bottom/{股號}.png`
- 輸出：`data/w_bottom_backtest_summary.json`、`data/w_bottom_backtest_trades.csv`
- 本階段**不含**獨立前瞻追蹤

## 漲停量縮整理選股（每日預設推播）

與其他策略完全獨立；每日排程預設執行（第五則 Telegram）。漲停後接連幾天量能逐日萎縮、股價守在漲停日區間內盤整，視為主力鎖籌/洗盤蓄勢的型態。型態橫跨 **4 根連續 K 棒**：最新一根為 day4（訊號日），漲停日為往回第 3 根（day1）。

| 條件 | 門檻 |
|------|------|
| day1 漲停 | 當日漲幅 `(收−昨收)/昨收` ≥ **9.5%**（近似，容忍分檔取整） |
| day1 趨勢 | 漲停日前 **5** 個交易日 MA20 嚴格逐日遞增 |
| day1 量能 | 漲停日成交量 ≥ **1000 張**（1,000,000 股；排除無漲停限制商品的假訊號） |
| day2 | **收陰線**（收盤 < 開盤）**且** 量 < **2×** day1 量 |
| day3 | 量 < day2 量 **且** 收盤 ≤ day1 最高 |
| day4 | 量 < day3 量 **且** 收盤 ≤ day1 最高 |
| day2/3/4 | 各收盤 ≥ day1 最低（守住漲停日低點） |

**回測規則：**
- 買入：訊號日（day4）隔日開盤價
- 停利：**進場價 +20%**（當日高點觸及；依進場價計算）
- 停損：股價**收盤**跌破 day1 最低價
- 同日同時觸及：保守先判停損；逾 **20** 交易日未觸發則強制收盤出場

```bash
# 測試（略過推播）
.venv/bin/python3.11 main.py --dry-run --limit 100 --skip-backtest

# 關閉漲停量縮整理掃描
.venv/bin/python3.11 main.py --skip-limit-up

# 近 3 年歷史回測（24 小時快取）
.venv/bin/python3.11 scripts/run_limit_up_contraction_backtest.py

# 強制重跑 / 測試用限制檔數
.venv/bin/python3.11 scripts/run_limit_up_contraction_backtest.py --refresh --limit 50
```

- K 線圖：`output/limit_up_contraction/{股號}.png`
- 輸出：`data/limit_up_contraction_backtest_summary.json`、`data/limit_up_contraction_backtest_trades.csv`
- 本階段**不含**獨立前瞻追蹤

## 低位題材動能選股（選用，預設關閉）

與均線回踩分開掃描；需 `--enable-theme` 才會推播（在漲停量縮整理之後）。

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

**均線回踩 — 第一則**
- 本地預設：優化版 A+B 清單 + K 線圖
- **GitHub Actions / launchd**（`--grade-a-only`）：僅 A 級清單 + K 線圖

**前瞻回測 — 第二則（A 級批次）**
- 以掃描日往回推 **20 個交易日** 的信號日（例：6/29 → 6/1）之 A 級選股
- 停損 -10%/停利 +30% 批次勝率、均報酬與個股明細
- DB 無資料時自動以歷史掃描回補

**爆量價穩 — 第三則（每日預設）**
- 文字摘要（量能倍數、當日漲幅、產業族群）
- 逐檔 K 線圖：`output/volume_surge/{股號}.png`
- 測試關閉：`--skip-volume-surge`

**N漲W底假跌破 — 第四則（每日預設）**
- 文字摘要（前波漲幅、W 底兩腳價位、停利/停損價位、產業族群）
- 逐檔 K 線圖：`output/w_bottom/{股號}.png`
- 測試關閉：`--skip-w-bottom`

**漲停量縮整理 — 第五則（每日預設）**
- 文字摘要（漲停日/漲幅、day1→day4 量縮倍率、整理區間、停損/停利、產業族群）
- 逐檔 K 線圖：`output/limit_up_contraction/{股號}.png`
- 測試關閉：`--skip-limit-up`

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