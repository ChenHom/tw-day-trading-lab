# Price Action P1 Design — Shioaji Tick → normalized MarketTick

Date: 2026-08-14

本文件是 `docs/price-action-intraday-plan.md` 的 P1 架構檢查結果。這一階段不實作，只界定範圍、契約、順序與邊界。

## Baseline Finding

`src/` 目前完全沒有任何 tick 相關程式碼。`grep -rn 'tick|Tick' src/` 只命中 `cost.py` 的 tick size（最小升降單位）與 `cli.py` 的 slippage 參數。

Shioaji SDK 在本 repo 的接觸面只有兩處，都在 `simulation.py`，而且都是**委託回報**，不是行情：

- `ShioajiSdkSimulationGateway`（`simulation.py:602`）— login / place_order / cancel_order / Contracts
- `ShioajiCallbackStream`（`simulation.py:741`）— `api.set_order_callback`

`api.quote.*`（行情訂閱）在整個 repo 是零接觸。P1 是新路線，不是改寫既有元件。

## 1. 現有可沿用元件

| 元件 | 位置 | 沿用方式 |
|---|---|---|
| Shioaji 邊界模式 | `simulation.py:602,741` | `ShioajiTickStream` 照抄此形狀：建構子檢查 `getattr(api,"simulation",True) is not True` → raise；SDK 用 lazy import；fake api 可測 |
| Gated smoke 模式 | `simulation.py:1656` `run_gated_shioaji_simulation_login_smoke` | 回傳 `{status:"blocked", checks:{...}, side_effects:[], review_reason:...}`，預設 blocked，必須 `--enable-*` 才有副作用 |
| Raw cache 路徑慣例 | `finmind_ingestion.py:33` `raw_cache_path` | 現有 layout 是 `data/raw/{source}/{dataset}/{date}/{stock_id}.jsonl`。plan 要的 `data/raw/shioaji/ticks/{date}/{symbol}.jsonl` 完全吻合（source=`shioaji`, dataset=`ticks`），不需要新的 storage 概念 |
| JSONL writer | `finmind_ingestion.py:39` `write_jsonl` | 直接 import 重用（純函式，無 FinMind 耦合） |
| JSON/JSONL reader | `cli.py:1028` `_read_rows_from_json_or_jsonl` | replay tick 檔時重用 |
| Contract dataclass 慣例 | `models.py` / `ledger.py:7` / `strategy.py:8` | frozen dataclass + `to_dict()` / `from_dict()`；`MarketTick` 照此 |
| `.gitignore` | `data/raw/*` 已忽略 | tick raw 檔不會誤入 git，不需要改 |
| 測試慣例 | `tests/test_*.py` | 純 `unittest`，無 pytest / fixture 框架，無外部依賴 |
| `pyproject.toml` | `dependencies = []` | 目前零第三方硬依賴，shioaji 全靠 lazy import；P1 必須維持 |

## 2. 缺少元件

全部都缺，但需要的量很小。

### 新增（2 個必要檔案）

1. `src/tw_day_trading_lab/market_data.py`
   - `MarketTick` frozen dataclass + `to_dict()`
   - `normalize_shioaji_tick(exchange, tick, *, sequence) -> MarketTick | None` — 純函式，不 import shioaji，只吃 duck-typed 物件
   - `ShioajiTickStream` — 薄 adapter：拒絕 `simulation is not True`、`quote.subscribe` 只訂閱候選名單、指派 sequence、把 raw 丟給 sink
   - `append_raw_ticks(cache_dir, date, symbol, rows)` — 重用 `write_jsonl`
2. `tests/test_market_data.py`

### 修改（4 處，都很小）

3. `cli.py` — 新增 `simulate shioaji-tick-smoke` subparser + `cmd_` 函式（照 `cmd_simulate_shioaji_smoke` 的 gate 寫法，預設 blocked、不 import shioaji）
4. `docs/data-contracts.md` — 新增 `## MarketTick Contract` 章節（只新增，不改既有章節）
5. `AGENTS.md` — Hard Boundaries 加一條：quote / tick stream 也必須拒絕 `api.simulation=False`
6. `README.md` — Project Shape 補 `market_data.py`

### P1 明確不做（屬於 P2–P4）

1m aggregator、`market_bars` table 與 `sql/003_*.sql`、5m 聚合、late tick finalize、bar revision、Fugle。

## 3. Data Contract

Shioaji SDK 1.3.2 已安裝於本機。以下欄位是從 `shioaji/stream_data_type.py` 與 `shioaji/shioaji.py` 實際讀出驗證，非憑記憶：

```text
TickSTKv1: code, datetime, open, avg_price, close, high, low, amount,
           total_amount, volume, total_volume, tick_type, chg_type,
           price_chg, pct_chg, bid_side_total_vol, ask_side_total_vol,
           bid_side_total_cnt, ask_side_total_cnt, closing_oddlot_shares,
           fixed_trade_vol, suspend, simtrade, intraday_odd

callback:  func(exchange: Exchange, tick: TickSTKv1) -> None
subscribe: api.quote.subscribe(contract, quote_type=QuoteType.Tick,
                               intraday_odd=False, version=QuoteVersion.v1)
Exchange:  TSE / OTC / OES / TAIFEX
```

### 映射表

| MarketTick | 來源 | 註記 |
|---|---|---|
| `symbol` | `tick.code` | |
| `exchange` | callback 第一個參數 `exchange.value` | `TSE` / `OTC` |
| `timestamp` | `tick.datetime` → ISO 字串 | naive local time，不要擅自掛 tzinfo |
| `price` | `tick.close`（成交價）→ `float` | 原型是 `Decimal`，不轉會讓 `json.dumps` 直接失敗 |
| `trade_volume` | `tick.volume` | 單筆成交增量 |
| `cumulative_volume` | `tick.total_volume` | 當日累積；不可直接相加 |
| `source` | 常數 `"shioaji_tick"` | |
| `sequence` | SDK 沒有此欄位，由 stream 指派 | 見下方決策 |

### 四個必須在 contract 層定案的決策

**D1 — `sequence` 自建。** 建議 per-`(date, symbol)` 單調遞增計數器，從 0 開始。重啟會歸零，所以 replay 的真正排序／去重鍵是 `(timestamp, cumulative_volume)`，`sequence` 只是同一 process 內的 tie-breaker。此上限需寫成 `ponytail:` 註解。

**D2 — `simtrade` 必須擋掉。** 試撮 tick 從 08:30 就開始流，價格不是真成交。不濾掉的話 P2 的 1m aggregator 會生出 08:30–09:00 的幽靈 K 棒，且 `open` 直接錯。建議 `normalize_shioaji_tick` 對 `simtrade` / `intraday_odd` / `suspend` 回傳 `None`，raw JSONL 仍原封保存（可稽核），stream 用 counter 統計被丟掉幾筆。

**D3 — `intraday_odd` 必須擋掉。** SDK docstring 明講整股 `volume` 單位是 K shares（張），盤中零股是 share（股）。混進同一條流會讓成交量差 1000 倍。

**D4 — canonical volume 單位。** 建議 MarketTick 一律存**股**（整股 tick 乘 1000），因為 FinMind `TaiwanStockPrice.Trading_Volume` 是股。但 `TaiwanStockPriceMinute` 的單位**未查證**（本機 `data/raw/finmind/` 沒有 `TaiwanStockPriceMinute` 目錄，一筆分 K 都沒抓過）。P5 TOD-RVOL 要拿它當歷史基準，因此在寫 P5 之前必須先抓一天分 K，把當日總量對 `TaiwanStockPrice.Trading_Volume` 驗一次。

## 4. 資料流

P1 只做標示 `← P1` 的範圍：

```text
api.quote.subscribe(candidate contracts only)   ← 只訂候選名單，不掃全市場
        ↓
on_tick_stk_v1(exchange, TickSTKv1)
        ↓
ShioajiTickStream.handle_tick
        ├─→ append_raw_ticks → data/raw/shioaji/ticks/{date}/{symbol}.jsonl  (全部保存，含 simtrade)
        └─→ normalize_shioaji_tick → MarketTick | None                        (過濾 + 正規化)
                ↓
            in-memory sink (list / callable)                     ← P1 到此為止
- - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -
                ↓  P2: 1m Bar Aggregator → BarClosed
                ↓  P3: 5m Aggregator
                ↓  P4: market_bars 持久化 / replay
```

`ShioajiTickStream` 只依賴傳進來的 `api` 物件與一個 sink callable，不 import shioaji、不 import strategy、不 import simulation。Strategy Engine 對 Shioaji 的依賴維持為零。

## 5. 實作順序

每一步都可獨立跑測試，不需要真實連線：

1. `MarketTick` + `normalize_shioaji_tick` + 測試 — 純函式，P1 的價值全在這裡（濾除規則、單位、Decimal、時間）
2. `ShioajiTickStream` + fake api 測試 — 訂閱範圍、`simulation=False` 拒絕、sequence 指派
3. `append_raw_ticks` + 測試 — 重用 `write_jsonl`，append 語意
4. CLI `simulate shioaji-tick-smoke` + 測試 — 先驗「預設 blocked 且不 import shioaji」，再接 `--enable-tick-stream`
5. 文件（`data-contracts.md` / `AGENTS.md` / `README.md`）

步驟 1–3 完全不碰網路。步驟 4 之後才會遇到風險 A（見下方），該風險應優先解決。

## 6. 測試案例

### `normalize_shioaji_tick`

1. 正常整股 tick → 8 個欄位皆正確；`price` 為 float；`volume` 已轉股；`timestamp` 為 ISO 字串
2. `simtrade=True` → `None`，reason=`simtrade`
3. `intraday_odd=True` → `None`，reason=`intraday_odd`
4. `suspend=True` → `None`，reason=`suspend`
5. Decimal 欄位 → `json.dumps(tick.to_dict())` 不拋例外
6. `exchange` enum → `"TSE"` / `"OTC"` 字串
7. `code` 為空 / `datetime` 為 `None` → `None`，reason=`needs_review`，不拋例外

### `ShioajiTickStream`

8. `api.simulation=False` → 建構子 raise `ValueError`（與既有 gateway 行為一致）
9. `start()` 只對傳入的候選 contract 呼叫 `quote.subscribe`；候選 3 檔就只訂 3 檔
10. `sequence` per-symbol 各自從 0 遞增，兩檔互不干擾
11. 後到 tick 的 `cumulative_volume` < 前一筆 → 計入 `out_of_order` counter，仍照常輸出
12. `stop()` 對每個已訂閱 contract 呼叫 `unsubscribe`

### raw sink

13. 同一 `(date, symbol)` 連續兩批寫入 → append 而非覆蓋；路徑 = `data/raw/shioaji/ticks/{date}/{symbol}.jsonl`
14. `simtrade` tick 仍寫進 raw（可稽核），但不出現在 MarketTick 輸出

### CLI gate

15. 無 `--enable-tick-stream` → `status=blocked`、`side_effects=[]`、`review_reason=enable_tick_stream_required`、不 import shioaji
16. 有 flag 但缺 credential → `shioaji_credentials_required`，不 login

## 7. 不應修改的既有邊界

- **委託鏈路整條不碰**：`ShioajiSdkSimulationGateway`、`ShioajiCallbackStream`、`build_shioaji_order_request`、`normalize_shioaji_order_callback`、`FileExecutionSyncStore` 及其 dedupe / precedence / terminal-state 規則。行情與委託是兩條獨立的 Shioaji 通道，P1 只開行情那條。
- **`sj.Shioaji(simulation=False)` 禁令維持**，行情訂閱同樣受此拘束。
- **trading-day-cycle 現有 contract 不動**：`load_intraday_bars_for_cycle`（`cli.py:1110`）、`_normalize_intraday_bar`、`watch_events.json` 的 payload shape、`intraday_data_adapter` 摘要欄位。tick 流要等 P2 產出 canonical 1m 之後才接進去；P1 硬接只會同時破壞 fixture 測試與 raw-cache adapter。
- **FinMind 側不動**：`raw_cache_path` 的 `finmind/{dataset}/{date}/{stock}.jsonl`、`fetch_ledger` 三條 skip 規則、quota gate。
- **`replay.py` / `ledger.py` / `cost.py` / `strategy.py` 不動。**
- **`pyproject.toml` `dependencies = []` 維持空的** — shioaji 一律 lazy import，測試一律用 fake api。這是目前 `python3 -m unittest discover` 能在沒有 SDK 的環境跑起來的原因。
- **`data-contracts.md` 只新增章節，不改寫既有章節。**

## 8. 風險與前置作業

### 風險 A（阻斷級，未查證）

**Shioaji simulation 帳號能不能收到即時 tick，尚未驗證。** AGENTS.md 禁止 `simulation=False`，所以整條即時路線只能走 simulation 登入。萬一 simulation 環境沒有行情推送，P1–P3 寫完會全部無法在真實環境驗證。

建議把第 5 節步驟 4 的 gated smoke **提前到 P2 之前**跑一次：登入 simulation、訂一檔、開盤時段收 60 秒、印出收到幾筆／幾筆 `simtrade`。這是 5 分鐘的事，但它決定後面三個 phase 值不值得寫。

### 風險 B（非阻斷，但有前置時間）

`data/raw/finmind/TaiwanStockPriceMinute/` 目前完全不存在，一天分 K 都沒抓過。因此：

- `trading-day-cycle --require-intraday-bars` 現在對任何日期都會 `blocked`
- `fixtures/` 目錄同樣不存在（`README.md:186` 引用了 `fixtures/2026-06-04-intraday-bars.json`）

這不影響 P1，但 P5 的 TOD-RVOL 需要 20 個交易日的歷史分 K，補資料的前置時間要現在就排。

## Implementation Status

P1 已實作完成（`src/tw_day_trading_lab/market_data.py`、`tests/test_market_data.py`、`cli.py` 的 `simulate shioaji-tick-smoke`）。P2 tick → 1m 未實作，維持本文件第 2 節的範圍界定。

實作時與本設計的兩處偏離：

1. **`normalize_shioaji_tick` 回傳 `tuple[MarketTick | None, str]`**，而非只回 `MarketTick | None`。第 6 節測試案例 2/3/4/7 要求 reject reason 可觀察，用 tuple 比再開一個分類函式短。
2. **`append_raw_ticks` 沒有重用 `finmind_ingestion.write_jsonl`**。該函式用 `path.write_text` 整檔覆寫，tick 流每筆都改寫整個檔案會變成 O(n²)。改成直接以 `"a"` 模式 append，四行 stdlib。

另外新增了設計時未列出的 `contract_not_found` reject reason：`api.Contracts.Stocks[symbol]` 查不到候選股時記錄並跳過，不中斷其他標的的訂閱。

## Verification

本文件的事實依據來自以下實際執行的檢查：

```bash
grep -rn 'intraday_bars\|TaiwanStockPriceMinute\|_load_intraday\|tick\|Tick' src/
grep -rln 'market_bars\|MarketTick' docs/ src/ sql/ tests/
find data -maxdepth 4 -type d
python3 -c "import shioaji; print(shioaji.__version__)"          # 1.3.2
grep -n "class TickSTKv1" -A 40 .../shioaji/stream_data_type.py
sed -n '95,145p' .../shioaji/shioaji.py                          # Quote.subscribe / set_on_tick_stk_v1_callback
```
