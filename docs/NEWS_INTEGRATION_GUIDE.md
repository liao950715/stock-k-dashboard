# 新聞情緒系統：運算機制與移植指南

給「想把新聞情緒功能接到另一個專案」用。完整公式與參數依據見 [NEWS_SENTIMENT_V2_1.md](NEWS_SENTIMENT_V2_1.md)；
可直接執行的範例見 [`examples/news_standalone_demo.py`](../examples/news_standalone_demo.py)（不需要 Streamlit）。

---

## 1. 一張圖看懂

```
新聞來源（Google 新聞 RSS / 證交所、櫃買中心重大訊息 / 任何你自己的來源）
   │  轉成統一格式：{title, source, link, published}
   ▼
news_pipeline.run_analysis()  ←── 交易日曆（MarketCalendar）
   │
   ├─ L1 存原始新聞（news_store.save_news，同內容不重複，記錄第一次取得時間）
   ├─ 讀回這檔股票最近 40 天所有新聞（不只這次抓到的）
   ├─ L2 分類（news_store.classify_cached → news_sentiment.classify_item，規則式，有快取）
   │     五級情緒 s、重要性等級 1～4、相關性 R、傳聞 / 否認 / 更正旗標、期別字樣
   ├─ 事件分群與版本（news_sentiment.build_events）
   │     同事件合併、更正 / 否認升版、確認狀態（官方 / 多來源 / 單一 / 傳聞 / 矛盾 / 否認 / 取代）
   ├─ 量化計算（news_sentiment.analyze）
   │     W = I × R′ × T × U → 40% 上限 → 收縮 NSI、n_eff、A、Ω → 敏感度 → 穩健程度
   ├─ 警示（news_sentiment.build_alerts）：重大利多 / 利空、未確認、矛盾、否認、分歧
   ├─ 每日快照（news_store.save_snapshot）＋ 顯示標籤遲滯
   └─ 5 日動能（news_sentiment.momentum）
   ▼
回傳 dict（NSI、標籤、穩健程度、事件、警示、動能、數量…）
   │
   └─（選用）ai_summary.get_summary()：L4 快取、指紋觸發、驗證、失敗回退
```

---

## 2. 要複製哪些檔案

| 檔案 | 依賴 | 必要性 | 內容 |
|---|---|---|---|
| `news_sentiment.py` | 只有 Python 標準函式庫（3.9+，用 `zoneinfo`） | **必要** | 所有規則與公式；純函式，可單獨使用 |
| `news_store.py` | 標準函式庫（`sqlite3`） | 建議 | 四層快取、歷史新聞、每日快照 |
| `news_pipeline.py` | `pandas` | 建議 | 串接流程、休市日 API、趨勢資料 |
| `ai_summary.py` | 標準函式庫；直連 Anthropic 才需要 `anthropic` | 選用 | AI 摘要（OpenRouter / Anthropic） |
| `tests/` | `pytest` | 建議 | 68 個測試，移植後跑一次確認行為一致 |

**不在這四個檔案裡、要另外搬的：** 新聞抓取函式 `fetch_news()`、`fetch_material_info()` 目前寫在 `stock_dashboard.py` 裡，
而且有 Streamlit 的 `@st.cache_data`。移植時請照 `examples/news_standalone_demo.py` 的 `fetch_google_news()` 寫一個不依賴 Streamlit 的版本，
或直接接你專案自己的新聞來源。

---

## 3. 資料進出規格

### 3.1 輸入

**新聞（raw_news）**：list of dict

| 欄位 | 型別 | 說明 |
|---|---|---|
| `title` | str | 新聞標題（目前只分析標題） |
| `source` | str | 媒體名稱（用來判斷獨立來源） |
| `link` | str | 原文網址（用來產生 news_id；沒有就用 source + title） |
| `published` | **有時區**的 datetime | 原始發布時間；沒有時區會錯 |

`raw_news=None` 代表「這次查詢失敗」，會記錄在 `fetch_log`，不會被當成「沒有新聞」。

**官方重大訊息（material，選用）**：list of dict，欄位 `subject`、`detail`、`date`（YYYY/MM/DD）或 `date_raw`（民國 1151108）、`time_raw`（HHMMSS）。
會被標成官方確認、重要性等級 4。

**交易日曆（MarketCalendar）**：
```python
cal = npl.build_calendar(trading_days, is_tw=True, holidays=休市日集合)
```
* `trading_days`：過去實際有開盤的日期（最可靠；這個專案用大盤指數的日 K 日期）
* `holidays`：未來的休市日（台股可用 `npl.fetch_twse_holidays()`）
* 台股 09:00～13:30（Asia/Taipei），`is_tw=False` 時是美股 09:30～16:00（America/New_York）

**股票識別**：`ticker`（儲存用的鍵，例如 `2330.TW`）、`code`（標題比對用，例如 `2330`）、`names`（公司名稱與別名，例如 `["台積電", "TSMC"]`）。
**別名越完整，相關性判斷越準。**

### 3.2 輸出：`run_analysis()` 回傳的 dict

| 鍵 | 內容 |
|---|---|
| `result["stats"]` | `nsi`（−100～+100，收縮後）、`nsi_raw`、`n_eff`、`A`、`omega`、`valid`、`no_direction` |
| `result["label"]` | 正式五級標籤（強烈偏多…強烈偏空） |
| `display_label` | 畫面用標籤（有 5 分遲滯；量化用途請用 `result["label"]` 或 `stats["nsi"]`） |
| `result["robustness"]` | 高 / 中 / 低 / 無 |
| `result["K_incl"]`、`counts` | N_raw、N_rel、K_events、K_incl、n_eff |
| `result["included"]` / `["rows"]` | 每個事件的 I、R、R′、T、U、W_raw、W、t、是否納入與原因 |
| `result["sensitivity"]` | 四種權重設定的 NSI 與標籤；`sensitivity_consistent` |
| `result["capped"]`、`max_share`、`nsi_cap_diff` | 40% 上限是否觸發與影響 |
| `alerts` | list of dict：`kind`、`event_id`、`title`、`score`、`importance`、`status`、`sources`、`published_at`、`updated_at`、`reason` |
| `momentum` | `value`（M₅）、`label`（改善 / 惡化 / 大致持平 / 趨勢資料不足）、`reason` |
| `events` | `Event` 物件（含 `items`、`version`、`history`、`status`、`superseded_items`） |
| `session`、`data_cutoff`、`event_version_set`、`snapshot_id` | 追溯用 |

要當成**量化模型特徵**，建議只取扁平欄位（範例第 5 段）：
`nsi, label, robustness, k_incl, n_eff, A, omega, 重大利多數, 重大利空數, 未確認數, formula_version`。

---

## 4. 運算重點（濃縮版）

| 步驟 | 規則 |
|---|---|
| 情緒 s | 關鍵字計分：強烈字 2 分、一般字 1 分；多 > 空 → 偏多（只有強烈多方字且無空方字 → 強烈偏多）；分數 ±1 / ±0.5 / 0 |
| 重要性 I | 財報 / 財測 / 重大訊息 3.0；月營收 / 法說 / 訂單 2.0；產業 / 總經 / 籌碼 1.5；一般 1.0 |
| 相關性 R′ | 直接提到公司 0.85 → 1.0；提到但是清單 / 排行 0.5 → 0.5；只有產業關聯 0.35 → 0.5；無關 0.15 → 排除 |
| 時間 T | 2^(−t/H)，t = 交易日年齡，H = 6 / 3 / 2 / 1（依重要性），t > 3H 排除 |
| 權重 W | I × R′ × T × U（U 目前固定 1）；事件數 ≥ 3 時單一事件 ≤ 40% |
| NSI | 100 × Σ(W·s) / (ΣW + 2) |
| 穩健程度 | 高：K ≥ 5、n_eff ≥ 3、敏感度一致、無嚴重矛盾；中：K ≥ 3、n_eff ≥ 2 |
| 去重 | 同股票、48 小時內、標題相似度 ≥ 0.8、同重要性、期別相同 |
| 計分排除 | 相關性不足、已否認、已撤回、未證實傳聞（傳聞只進警示） |

所有數值都在 `news_sentiment.PARAMS`，改參數記得一起改 `PARAMS_VERSION` / `FORMULA_VERSION`；
分類規則改了要改 `CLASSIFIER_VERSION`（L2 快取會自動失效、重新分類）。

---

## 5. 機制

* **時間點正確（point-in-time）**：`analyze(events, calendar, as_of)` 只用 `published_at ≤ as_of` **且** `fetched_at ≤ as_of` 的新聞
* **歷史累積**：每次執行都會把新聞存進 L1，計算時讀回最近 40 天所有新聞，所以「這次 RSS 沒出現」的舊新聞仍會繼續衰減計分
* **快取**：L1 新聞（news_id + content_hash）、L2 分類（+ 模型 / 版本）、快照（股票 + 交易日 + 公式版本）、L4 摘要（+ 事件版本集合 + 模型 / prompt 版本 + 指紋）
  * `quant_cache` 資料表已建立但目前流程沒有使用（計算本身只要幾十毫秒）
* **版本**：公式、參數、分類器、摘要 prompt 各有版本字串，快照與快取都記錄版本，版本不同的快照不會互相計算動能
* **AI 摘要**：指紋（重大事件與狀態、警示、顯示標籤、模型 / prompt 版本）改變才重算；失敗 30 分鐘內不自動重試；同股票同時只生成一份；輸出只能用輸入裡出現過的數字與 event_id

---

## 6. 最小接法

```python
import news_pipeline as npl, news_sentiment as ns, news_store

store = news_store.NewsStore("你的路徑/news.sqlite")            # 或設環境變數 NEWS_DB_PATH
cal = npl.build_calendar(過去交易日清單, is_tw=True, holidays=npl.fetch_twse_holidays())
raw = 你的新聞抓取函式("台積電 2330")                            # [{title, source, link, published}]

out = npl.run_analysis(store, ticker="2330.TW", code="2330", names=["台積電", "TSMC"],
                       raw_news=raw, material=[], calendar=cal)
nsi = out["result"]["stats"]["nsi"]
```

只要純計算（自己管理資料）：

```python
items = [ns.classify_item(ns.NewsItem(...), "2330", ["台積電"]) for ...]
events = ns.build_events(items)
result = ns.analyze(events, cal, as_of)
alerts = ns.build_alerts(result)
```

加上 AI 摘要：

```python
import ai_summary
provider = ai_summary.pick_summarizer(openrouter_key, anthropic_key)    # 都沒有 → 未啟用
inp = ns.summary_input(ticker, name, out["result"], out["momentum"], out["alerts"], out["display_label"])
summary = ai_summary.get_summary(store, provider, ticker, inp, out["event_version_set"], out["snapshot_id"],
                                 out["data_cutoff"], ns.rule_based_summary(out["result"], out["momentum"], out["alerts"]))
# summary["kind"]：ai / ai_stale / rule
```

---

## 7. 移植檢查清單

1. 複製四個模組（＋ tests），安裝 `pandas`（＋ 選用 `anthropic`、`pytest`），執行 `python -m pytest tests`（`test_app_ui.py` 是這個網頁專用的，可刪除）
2. 寫一個不依賴 Streamlit 的新聞抓取函式，輸出 3.1 的格式（`published` 一定要有時區）
3. 準備交易日曆：用你專案既有的交易日資料最好
4. 為每檔股票準備 `code` 與 `names`（公司全名、簡稱、英文名、常見別名）
5. 決定資料庫位置（`NEWS_DB_PATH`）；要長期累積歷史請放在不會被清空的地方
6. 如果是美股或其他市場：`is_tw=False`，並檢查關鍵字清單（目前是繁體中文）
7. 如需 AI 摘要：設定 `OPENROUTER_API_KEY` 或 `ANTHROPIC_API_KEY`

---

## 8. 用在回測或交易系統前要知道的事

* **回測需要歷史新聞資料庫**：Google 新聞 RSS 只有「現在」能搜到的結果。因為 point-in-time 規則同時檢查 `fetched_at`，
  用今天抓到的新聞去算「過去某天」會得到 0 個事件（範例第 4 段就是這個結果）——這是刻意的，避免用到當時不可能取得的資訊。
  若你有可信的歷史新聞來源（例如 MOPS 歷史重大訊息、自己長期保存的新聞），載入時可把 `fetched_at` 設成當時實際可取得的時間；
  **不要直接設成 published_at 而不加說明**，那等於假設發布當下就能取得。
* **去重只看標題**：同一事件被多家媒體用不同寫法報導時，常被拆成多個事件
  （範例實測：10 月營收新聞被拆成 6 個事件、6 個「重大利多」警示），會高估事件數與 n_eff、放大單一消息的影響。
  接到交易決策前，建議先改善事件分群（加入內文、財務數字比對，或改用 AI 分類）。
* **規則式分類未驗證準確率**：情緒、事件類型、相關性都是關鍵字判斷。
* **NSI 是描述性指標**：沒有驗證過對報酬或風險的預測力，不要直接當買賣訊號；
  比較合適的用法是事件警示、風險監測、或作為待驗證的研究特徵。
* **台股關鍵字**：關鍵字清單是繁體中文、以台股新聞為主。

---

## 9. 這個網頁專案裡對應的位置

| 網頁功能 | 程式位置 |
|---|---|
| 新聞抓取（Streamlit 版） | `stock_dashboard.py` 的 `fetch_news()`、`fetch_material_info()` |
| 接資料來源與快取 | `stock_dashboard.py` 的 `news_analysis()`、`get_store()`、`calendar_days()`、`get_holidays()`、`get_summarizer()` |
| 新聞分頁畫面 A～E | `stock_dashboard.py` 的「分頁 2：相關新聞」區塊 |
