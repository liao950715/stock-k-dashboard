# -*- coding: utf-8 -*-
"""
====================================================================
  理財機器人 期中報告 ── QuantumK 智慧 K 線看盤儀表板
====================================================================
功能總覽：
  1. 以 yfinance 爬取 Yahoo Finance 公開歷史股價（台股 / 美股 / 指數 / 加密貨幣）
  2. 週期切換：5分、15分、30分、60分、日線、週線、月線
  3. 均線勾選：MA5、MA10、MA20、MA60（季線）、MA120（半年線）、MA240（年線）
  4. 副圖技術指標：成交量（含均量線）＋ MACD / RSI / KD 下拉切換，與 K 線時間軸同步
  5. AI 技術面自動診斷：多頭/空頭排列、黃金/死亡交叉、MACD、RSI、KD 綜合評分
  6. 深色 / 淺色主題切換、三種字體（Orbitron／Noto Sans TC／JetBrains Mono）
  7. 台股規範紅漲綠跌、十字準心、OHLCV 即時游標數值、區域縮放、滾輪縮放
  8. 完整錯誤處理：代碼錯誤、網路斷線、資料不足、Yahoo 流量限制，皆以美觀 Alert 呈現

執行方式：
  pip install streamlit yfinance plotly pandas numpy
  streamlit run stock_dashboard.py
====================================================================
"""

import socket
from datetime import datetime

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import yfinance as yf

# ------------------------------------------------------------------
# Streamlit 版本相容：新版（>= 1.46）以 width="stretch" 取代已棄用的 use_container_width
# 讓程式在本機舊版與雲端最新版都能正常執行、不出現棄用警告
# ------------------------------------------------------------------
_ST_VER = tuple(int(p) for p in st.__version__.split(".")[:2] if p.isdigit())
STRETCH = {"width": "stretch"} if _ST_VER >= (1, 46) else {"use_container_width": True}

# ------------------------------------------------------------------
# 0. 頁面基本設定（必須是第一個 Streamlit 指令）
# ------------------------------------------------------------------
st.set_page_config(
    page_title="QuantumK 智慧看盤｜理財機器人",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ------------------------------------------------------------------
# 1. 全域常數設定
# ------------------------------------------------------------------

# 主題配色：深色（dark）與淺色（light）兩套，up / down 依台股「紅漲綠跌」
THEMES = {
    "dark": dict(
        bg="#0B0E14", bg2="#0F131C", panel="#121826", panel2="#182033",
        border="#243047", text="#E6EAF2", muted="#8B95A9",
        accent="#00D1FF", accent2="#7C5CFF",
        up="#FF4D5E", down="#1FC77E", flat="#A0A7B4",
        grid="rgba(255,255,255,0.06)", plot_bg="#0E121B",
        hover_bg="rgba(21,28,43,0.95)", spike="rgba(230,234,242,0.55)",
        input_bg="#141B2A",
    ),
    "light": dict(
        bg="#F4F6FA", bg2="#EDF1F7", panel="#FFFFFF", panel2="#F6F8FC",
        border="#E2E7F0", text="#111827", muted="#6B7280",
        accent="#2563EB", accent2="#7C3AED",
        up="#E0283B", down="#0F9D58", flat="#6B7280",
        grid="rgba(17,24,39,0.07)", plot_bg="#FFFFFF",
        hover_bg="rgba(255,255,255,0.96)", spike="rgba(17,24,39,0.45)",
        input_bg="#FFFFFF",
    ),
}

# 均線設定：每條均線有獨立柔和配色（深色模式 / 淺色模式各一組，淺色模式稍深以維持對比）
MA_CONFIG = {
    5:   dict(name="MA5",   desc="週線",   dark="#F2C94C", light="#D4A514", emoji="🟨", default=True),
    10:  dict(name="MA10",  desc="雙週線", dark="#F2994A", light="#E07B24", emoji="🟧", default=True),
    20:  dict(name="MA20",  desc="月線",   dark="#56A8F5", light="#2F80ED", emoji="🟦", default=True),
    60:  dict(name="MA60",  desc="季線",   dark="#3DD6A0", light="#14A37F", emoji="🟩", default=False),
    120: dict(name="MA120", desc="半年線", dark="#B39DFA", light="#8B5CF6", emoji="🟪", default=False),
    240: dict(name="MA240", desc="年線",   dark="#A0A7B4", light="#6B7280", emoji="⬜", default=False),
}
MA_LIST = list(MA_CONFIG.keys())

# 週期設定：interval = yfinance 資料間隔；period = 抓取長度（多抓一些讓長均線從第一根就有值）
# resample：週線 / 月線由日線重新取樣，確保 OHLCV 與均線計算正確一致
PERIODS = {
    "5分":  dict(interval="5m",  period="60d",  resample=None, fmt="%m/%d %H:%M", label="5 分鐘線"),
    "15分": dict(interval="15m", period="60d",  resample=None, fmt="%m/%d %H:%M", label="15 分鐘線"),
    "30分": dict(interval="30m", period="60d",  resample=None, fmt="%m/%d %H:%M", label="30 分鐘線"),
    "60分": dict(interval="60m", period="730d", resample=None, fmt="%m/%d %H:%M", label="60 分鐘線"),
    "日":   dict(interval="1d",  period="5y",   resample=None, fmt="%Y/%m/%d",    label="日線"),
    "週":   dict(interval="1d",  period="max",  resample="W",  fmt="%Y/%m/%d",    label="週線"),
    "月":   dict(interval="1d",  period="max",  resample="M",  fmt="%Y/%m",       label="月線"),
}

# 熱門快選清單（同時作為中文名稱對照表）
POPULAR = {
    "2330 台積電": "2330", "2317 鴻海": "2317", "2454 聯發科": "2454",
    "2308 台達電": "2308", "2382 廣達": "2382", "2881 富邦金": "2881",
    "2603 長榮": "2603", "3008 大立光": "3008", "0050 元大台灣50": "0050",
    "0056 元大高股息": "0056", "^TWII 台灣加權指數": "^TWII",
    "AAPL 蘋果": "AAPL", "NVDA 輝達": "NVDA", "TSLA 特斯拉": "TSLA",
    "MSFT 微軟": "MSFT", "BTC-USD 比特幣": "BTC-USD",
}
NAME_MAP = {code: label.split(" ", 1)[1] for label, code in POPULAR.items()}

# 副圖指標選項
SUB_INDICATORS = ["MACD 指數平滑異同移動平均", "RSI 相對強弱指標", "KD 隨機指標", "不顯示"]


# ------------------------------------------------------------------
# 2. 樣式（CSS）：三種字體 + 深淺主題 + 卡片化儀表板
# ------------------------------------------------------------------
def inject_css(t: dict) -> None:
    """依主題注入全站 CSS。使用 __TOKEN__ 取代避免 f-string 大括號衝突。"""
    css = """
    <style>
    /* ===== 三種字體：Orbitron（科技標題）、Noto Sans TC（中文內文）、JetBrains Mono（數字） ===== */
    @import url('https://fonts.googleapis.com/css2?family=Orbitron:wght@500;700;900&family=Noto+Sans+TC:wght@400;500;700;900&family=JetBrains+Mono:wght@400;600;700&display=swap');

    html, body, .stApp { font-family: 'Noto Sans TC', 'Microsoft JhengHei', sans-serif; }
    .stApp { background: radial-gradient(1200px 600px at 10% -10%, __ACCENT2__14, transparent 60%),
                         radial-gradient(1000px 500px at 110% 0%, __ACCENT__12, transparent 55%),
                         __BG__; color: __TEXT__; }
    [data-testid="stHeader"] { background: transparent; }
    .block-container { padding-top: 1.6rem; padding-bottom: 2rem; max-width: 1500px; }

    /* 一般文字顏色 */
    .stApp p, .stApp li, .stApp label, .stApp h1, .stApp h2, .stApp h3, .stApp h4,
    [data-testid="stWidgetLabel"] p, [data-testid="stMarkdownContainer"] { color: __TEXT__; }
    [data-testid="stCaptionContainer"], .stApp small { color: __MUTED__ !important; }

    /* ===== 側邊欄 ===== */
    [data-testid="stSidebar"] { background: __BG2__; border-right: 1px solid __BORDER__; }
    [data-testid="stSidebar"] * { color: __TEXT__; }

    /* ===== 輸入框 / 下拉選單 ===== */
    .stApp input, .stApp textarea { background: __INPUT_BG__ !important; color: __TEXT__ !important;
        font-family: 'JetBrains Mono', monospace !important; }
    div[data-baseweb="input"], div[data-baseweb="base-input"], div[data-baseweb="select"] > div {
        background: __INPUT_BG__ !important; border-color: __BORDER__ !important; color: __TEXT__ !important; }
    div[data-baseweb="select"] svg { fill: __MUTED__; }
    ul[data-baseweb="menu"], div[data-baseweb="popover"] li { background: __PANEL__ !important; color: __TEXT__ !important; }
    div[data-baseweb="slider"] div[role="slider"] { background: __ACCENT__ !important; }

    /* ===== 週期選擇：把 radio 變成分段按鈕（類似 Yahoo 股市） ===== */
    div[role="radiogroup"] { gap: 0 !important; background: __PANEL__; border: 1px solid __BORDER__;
        border-radius: 12px; padding: 4px; display: inline-flex; flex-wrap: wrap; }
    div[role="radiogroup"] > div { display: flex; flex-wrap: wrap; gap: 0 !important; }  /* 新版多一層包裝 */
    div[role="radiogroup"] label { margin: 0 !important; padding: 6px 18px; border-radius: 9px;
        cursor: pointer; transition: all .15s ease; }
    div[role="radiogroup"] label > div:first-child { display: none; }
    /* 新版 Streamlit（>= 1.4x）：圓點包在 label > div > div:first-child 裡 */
    div[role="radiogroup"] label > div > div:first-child:not([data-testid="stMarkdownContainer"]) { display: none; }
    div[role="radiogroup"] label p { font-weight: 700; color: __MUTED__ !important; font-size: 0.95rem; }
    div[role="radiogroup"] label:hover { background: __PANEL2__; }
    div[role="radiogroup"] label:has(input:checked) { background: linear-gradient(135deg, __ACCENT__, __ACCENT2__); }
    div[role="radiogroup"] label:has(input:checked) p { color: #FFFFFF !important; }

    /* ===== st.metric 卡片化 ===== */
    [data-testid="stMetric"] { background: __PANEL__; border: 1px solid __BORDER__; border-radius: 14px;
        padding: 14px 16px 10px 16px; box-shadow: 0 4px 18px rgba(0,0,0,0.12); }
    [data-testid="stMetricLabel"] p { color: __MUTED__ !important; font-size: 0.82rem; letter-spacing: .5px; }
    [data-testid="stMetricValue"] { font-family: 'JetBrains Mono', monospace; color: __TEXT__;
        font-size: clamp(1.05rem, 1.55vw, 1.55rem); font-weight: 700; }
    /* 新版 Streamlit 會把過長數字截成「…」，這裡改為完整顯示 */
    [data-testid="stMetricValue"] p, [data-testid="stMetricValue"] div {
        font-size: inherit !important; font-family: inherit !important; font-weight: inherit !important;
        overflow: visible !important; text-overflow: clip !important; white-space: nowrap; }
    [data-testid="stMetricDelta"] { font-family: 'JetBrains Mono', monospace; font-size: .8rem; }
    [data-testid="stMetricDelta"] div { white-space: normal !important; overflow: visible !important; }

    /* ===== Expander / Dataframe / 按鈕 ===== */
    [data-testid="stExpander"] { background: __PANEL__; border: 1px solid __BORDER__; border-radius: 14px; }
    [data-testid="stExpander"] summary p { font-weight: 700; }
    .stDownloadButton button, .stButton button { background: __PANEL2__; color: __TEXT__; border: 1px solid __BORDER__;
        border-radius: 10px; font-weight: 600; }
    .stDownloadButton button:hover, .stButton button:hover { border-color: __ACCENT__; color: __ACCENT__; }
    hr { border-color: __BORDER__ !important; }

    /* ===== 自訂元件 ===== */
    .brand { display:flex; align-items:center; justify-content:space-between; margin-bottom: 14px; }
    .brand-title { font-family: 'Orbitron', sans-serif; font-weight: 900; font-size: 1.9rem; letter-spacing: 2px;
        background: linear-gradient(90deg, __ACCENT__, __ACCENT2__); -webkit-background-clip: text;
        -webkit-text-fill-color: transparent; line-height: 1.1; }
    .brand-sub { color: __MUTED__; font-size: .9rem; margin-top: 4px; }
    .live-dot { font-family: 'JetBrains Mono', monospace; font-size: .8rem; color: __MUTED__;
        border: 1px solid __BORDER__; border-radius: 999px; padding: 6px 12px; background: __PANEL__; }
    .live-dot b { color: __DOWN__; }

    .section-title { font-family: 'Orbitron', 'Noto Sans TC', sans-serif; font-weight: 700; font-size: 1.0rem;
        letter-spacing: 1.5px; color: __TEXT__; margin: 18px 0 10px 0; display:flex; align-items:center; gap:10px; }
    .section-title:before { content:''; width: 4px; height: 18px; border-radius: 4px;
        background: linear-gradient(180deg, __ACCENT__, __ACCENT2__); display:inline-block; }

    .quote-card { background: __PANEL__; border: 1px solid __BORDER__; border-radius: 16px; padding: 16px 20px; }
    .quote-head { display:flex; align-items:baseline; justify-content:space-between; flex-wrap:wrap; gap: 8px; }
    .quote-name { font-size: 1.45rem; font-weight: 900; color: __TEXT__; }
    .quote-name span { font-family: 'JetBrains Mono', monospace; font-size: 1.0rem; color: __MUTED__; margin-left: 8px; }
    .quote-time { font-family: 'JetBrains Mono', monospace; color: __MUTED__; font-size: .85rem; }
    .quote-bar { margin-top: 8px; font-family: 'JetBrains Mono', monospace; font-size: .98rem; color: __TEXT__;
        display:flex; flex-wrap: wrap; gap: 6px 22px; }
    .quote-bar em { font-style: normal; color: __MUTED__; font-family: 'Noto Sans TC', sans-serif; margin-right: 6px; }
    .up { color: __UP__ !important; font-weight: 700; }
    .down { color: __DOWN__ !important; font-weight: 700; }
    .flat { color: __FLAT__ !important; font-weight: 700; }

    .ai-card { background: linear-gradient(135deg, __PANEL__, __PANEL2__); border: 1px solid __BORDER__;
        border-radius: 18px; padding: 18px 22px; height: 100%; }
    .ai-verdict { font-size: 1.5rem; font-weight: 900; margin-bottom: 4px; }
    .ai-note { color: __MUTED__; font-size: .82rem; }
    .badge { display:inline-flex; align-items:center; gap:6px; padding: 6px 12px; margin: 4px 6px 4px 0;
        border-radius: 999px; font-size: .86rem; font-weight: 700; border: 1px solid transparent; }
    .badge-bull { background: __UP__22; color: __UP__; border-color: __UP__55; }
    .badge-bear { background: __DOWN__22; color: __DOWN__; border-color: __DOWN__55; }
    .badge-neutral { background: __ACCENT__1A; color: __ACCENT__; border-color: __ACCENT__44; }
    .badge-warn { background: #F2C94C22; color: #E0A800; border-color: #F2C94C55; }
    .ai-list { margin: 10px 0 0 0; padding-left: 18px; color: __TEXT__; font-size: .92rem; line-height: 1.75; }

    .alert { border-radius: 16px; padding: 18px 22px; margin: 10px 0; display:flex; gap: 16px; align-items:flex-start;
        border: 1px solid; }
    .alert-icon { font-size: 1.8rem; line-height: 1; }
    .alert-title { font-weight: 900; font-size: 1.1rem; margin-bottom: 4px; }
    .alert-msg { font-size: .93rem; line-height: 1.7; color: __TEXT__; }
    .alert-error { background: __UP__14; border-color: __UP__66; }
    .alert-error .alert-title { color: __UP__; }
    .alert-warn { background: #F2C94C18; border-color: #F2C94C66; }
    .alert-warn .alert-title { color: #E0A800; }
    .alert-info { background: __ACCENT__12; border-color: __ACCENT__55; }
    .alert-info .alert-title { color: __ACCENT__; }

    .footer { text-align:center; color: __MUTED__; font-size: .8rem; margin-top: 24px; }
    .mono { font-family: 'JetBrains Mono', monospace; }
    </style>
    """
    token_map = {
        "__BG__": t["bg"], "__BG2__": t["bg2"], "__PANEL__": t["panel"], "__PANEL2__": t["panel2"],
        "__BORDER__": t["border"], "__TEXT__": t["text"], "__MUTED__": t["muted"],
        "__ACCENT2__": t["accent2"], "__ACCENT__": t["accent"], "__UP__": t["up"], "__DOWN__": t["down"],
        "__FLAT__": t["flat"], "__INPUT_BG__": t["input_bg"],
    }
    # 注意：__ACCENT2__ 要先於 __ACCENT__ 取代，避免被部分取代
    for k, v in token_map.items():
        css = css.replace(k, v)
    st.markdown(css, unsafe_allow_html=True)


def alert(kind: str, title: str, msg: str) -> None:
    """美觀的提示框。kind = error / warn / info"""
    icons = {"error": "⛔", "warn": "⚠️", "info": "💡"}
    st.markdown(
        f"""<div class="alert alert-{kind}">
              <div class="alert-icon">{icons.get(kind, "💡")}</div>
              <div><div class="alert-title">{title}</div><div class="alert-msg">{msg}</div></div>
            </div>""",
        unsafe_allow_html=True,
    )


def section_title(text: str) -> None:
    """區塊標題（Orbitron 字體 + 漸層色條）"""
    st.markdown(f'<div class="section-title">{text}</div>', unsafe_allow_html=True)


# ------------------------------------------------------------------
# 3. 資料爬取與錯誤處理
# ------------------------------------------------------------------
def resolve_symbol(raw: str) -> list:
    """
    將使用者輸入轉成 Yahoo Finance 代碼候選清單：
      - 純數字（如 2330、0050）→ 先試上市 .TW，再試上櫃 .TWO
      - 已帶後綴或英文代碼（AAPL、^TWII、BTC-USD）→ 直接使用
    """
    s = (raw or "").strip().upper().replace(" ", "")
    if not s:
        return []
    if s.isdigit() or (len(s) <= 6 and s[:-1].isdigit() and s[-1].isalpha()):  # 例如 2330、00631L
        return [f"{s}.TW", f"{s}.TWO"]
    return [s]


def internet_ok(host: str = "query1.finance.yahoo.com", port: int = 443, timeout: float = 3.0) -> bool:
    """快速檢查是否能連到 Yahoo Finance，用來區分「代碼錯誤」與「網路斷線」"""
    try:
        socket.create_connection((host, port), timeout=timeout).close()
        return True
    except OSError:
        return False


@st.cache_data(ttl=600, show_spinner=False)
def fetch_history(symbol: str, interval: str, period: str) -> pd.DataFrame:
    """實際向 Yahoo Finance 爬取歷史資料（快取 10 分鐘，避免重複請求被限流）"""
    tk = yf.Ticker(symbol)
    df = tk.history(period=period, interval=interval, auto_adjust=False, actions=False)
    return df


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_name(symbol: str) -> str:
    """取得股票名稱：優先使用內建中文對照表，否則嘗試向 Yahoo 查詢英文名稱"""
    base = symbol.replace(".TWO", "").replace(".TW", "")
    if base in NAME_MAP:
        return NAME_MAP[base]
    try:
        info = yf.Ticker(symbol).info
        return info.get("shortName") or info.get("longName") or base
    except Exception:
        return base


def clean_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    """資料清洗：保留 OHLCV、移除空值與異常值、去除時區（保留交易所當地時間）"""
    df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
    df = df.dropna(subset=["Open", "High", "Low", "Close"])
    df = df[(df["Open"] > 0) & (df["Close"] > 0)]
    df["Volume"] = df["Volume"].fillna(0)
    if getattr(df.index, "tz", None) is not None:
        df.index = df.index.tz_localize(None)
    df = df[~df.index.duplicated(keep="last")].sort_index()
    return df


def classify_error(exc: Exception) -> tuple:
    """把例外分類成使用者看得懂的訊息"""
    text = f"{type(exc).__name__}: {exc}"
    low = text.lower()
    if "ratelimit" in low or "too many" in low or "429" in low:
        return ("ratelimit", text)
    if any(k in low for k in ["connection", "timeout", "timed out", "resolve", "network", "curl", "ssl", "proxy"]):
        return ("network", text)
    return ("unknown", text)


def load_data(raw: str, period_key: str):
    """
    依使用者輸入與週期載入資料。
    回傳 (df, symbol, error)；error 為 None 代表成功，否則為 (錯誤類型, 詳細訊息)
    """
    cfg = PERIODS[period_key]
    candidates = resolve_symbol(raw)
    if not candidates:
        return None, None, ("empty", "")

    last_exc = None
    for sym in candidates:
        try:
            df = fetch_history(sym, cfg["interval"], cfg["period"])
        except Exception as e:  # 網路斷線、限流、Yahoo 回應異常等
            last_exc = e
            continue
        if df is not None and not df.empty and df["Close"].notna().sum() > 0:
            df = clean_ohlcv(df)
            if cfg["resample"]:
                df = resample_ohlcv(df, cfg["resample"])
            return df, sym, None

    # 全部候選代碼都失敗 → 判斷原因
    if last_exc is not None:
        return None, None, classify_error(last_exc)
    if not internet_ok():
        return None, None, ("network", "無法連線到 query1.finance.yahoo.com")
    return None, None, ("notfound", ", ".join(candidates))


def resample_ohlcv(df: pd.DataFrame, freq: str) -> pd.DataFrame:
    """
    將日線重新取樣成週線（W）或月線（M）：
      開=第一天開盤、高=區間最高、低=區間最低、收=最後一天收盤、量=加總
      日期標籤採用「該週/該月最後一個交易日」，與券商軟體一致
    """
    periods = df.index.to_period(freq)
    g = df.groupby(periods)
    out = pd.DataFrame({
        "Open": g["Open"].first(),
        "High": g["High"].max(),
        "Low": g["Low"].min(),
        "Close": g["Close"].last(),
        "Volume": g["Volume"].sum(),
    })
    last_dates = pd.Series(df.index, index=df.index).groupby(periods).last()
    out.index = pd.DatetimeIndex(last_dates.values)
    return out


# ------------------------------------------------------------------
# 4. 技術指標計算（在「完整」資料上計算，再切出顯示區間 → 長均線從畫面最左邊就正確）
# ------------------------------------------------------------------
def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    close, high, low = df["Close"], df["High"], df["Low"]

    # 4-1 移動平均線
    for n in MA_LIST:
        df[f"MA{n}"] = close.rolling(n).mean()

    # 4-2 漲跌、漲跌幅、振幅
    prev_close = close.shift(1)
    df["Chg"] = close - prev_close
    df["ChgPct"] = df["Chg"] / prev_close * 100
    df["Amp"] = (high - low) / prev_close * 100

    # 4-3 均量線
    df["MV5"] = df["Volume"].rolling(5).mean()
    df["MV20"] = df["Volume"].rolling(20).mean()

    # 4-4 MACD(12, 26, 9)：DIF = EMA12 - EMA26；MACD(訊號線) = DIF 的 9 日 EMA；OSC = DIF - MACD
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    df["DIF"] = ema12 - ema26
    df["MACD"] = df["DIF"].ewm(span=9, adjust=False).mean()
    df["OSC"] = df["DIF"] - df["MACD"]

    # 4-5 RSI（Wilder 平滑法），提供 RSI6（快）與 RSI14（標準）
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    for n in (6, 14):
        avg_gain = gain.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
        avg_loss = loss.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        rsi = 100 - 100 / (1 + rs)
        rsi = rsi.where(avg_loss != 0, 100.0)          # 完全沒跌 → RSI = 100
        rsi = rsi.where(~((avg_gain == 0) & (avg_loss == 0)), 50.0)  # 完全沒動 → 50
        df[f"RSI{n}"] = rsi

    # 4-6 KD(9, 3, 3)：台灣常用隨機指標，K、D 初始值 50
    low9 = low.rolling(9).min()
    high9 = high.rolling(9).max()
    rng = (high9 - low9).replace(0, np.nan)
    rsv = ((close - low9) / rng * 100).fillna(50).values
    k_vals, d_vals = np.full(len(df), np.nan), np.full(len(df), np.nan)
    k_prev, d_prev = 50.0, 50.0
    for i in range(len(df)):
        if i < 8:  # 前 8 根資料不足 9 根，不計算
            continue
        k_prev = k_prev * 2 / 3 + rsv[i] / 3
        d_prev = d_prev * 2 / 3 + k_prev / 3
        k_vals[i], d_vals[i] = k_prev, d_prev
    df["K"], df["D"] = k_vals, d_vals

    # 4-7 布林通道(20, 2)
    std20 = close.rolling(20).std(ddof=0)
    df["BB_MID"] = df["MA20"]
    df["BB_UP"] = df["MA20"] + 2 * std20
    df["BB_LOW"] = df["MA20"] - 2 * std20
    return df


def find_last_cross(fast: pd.Series, slow: pd.Series, lookback: int = 10):
    """
    找出最近 lookback 根內最後一次交叉。
    回傳 ("golden"/"death", 幾根前) 或 None
    """
    diff = (fast - slow).dropna()
    if len(diff) < 2:
        return None
    sign = np.sign(diff.values)
    sign = pd.Series(sign).replace(0, np.nan).ffill().fillna(0).values  # 剛好相等時沿用前一狀態
    n = len(sign)
    for i in range(n - 1, max(0, n - 1 - lookback), -1):
        if sign[i] != 0 and sign[i - 1] != 0 and sign[i] != sign[i - 1]:
            return ("golden" if sign[i] > 0 else "death", n - 1 - i)
    return None


# ------------------------------------------------------------------
# 5. AI 技術面自動診斷
# ------------------------------------------------------------------
def diagnose(df: pd.DataFrame) -> dict:
    """
    綜合均線排列、交叉、MACD、RSI、KD 計算 0~100 的技術面分數，
    並產生彩色狀態標籤與文字解讀。
    """
    last = df.iloc[-1]
    score = 50
    badges, notes = [], []

    # 5-1 均線排列（MA5 > MA10 > MA20 > MA60 為多頭排列）
    align_set = [n for n in (5, 10, 20, 60) if pd.notna(last[f"MA{n}"])]
    if len(align_set) >= 3:
        vals = [last[f"MA{n}"] for n in align_set]
        names = " > ".join(f"MA{n}" for n in align_set)
        if all(vals[i] > vals[i + 1] for i in range(len(vals) - 1)):
            badges.append(("🚀 多頭排列", "bull"))
            notes.append(f"短中長期均線依序排列（{names}），趨勢偏多。")
            score += 20
        elif all(vals[i] < vals[i + 1] for i in range(len(vals) - 1)):
            badges.append(("🧊 空頭排列", "bear"))
            notes.append(f"均線由短到長依序向下排列（{names.replace('>', '<')}），趨勢偏空。")
            score -= 20
        else:
            badges.append(("🌀 均線糾結", "neutral"))
            notes.append("各期均線交錯，方向尚未明朗，屬整理格局。")
    else:
        badges.append(("⏳ 均線資料不足", "warn"))
        notes.append("資料筆數不足以判斷均線排列，可切換較短週期或較長資料。")

    # 5-2 短期交叉：MA5 × MA20
    cross = find_last_cross(df["MA5"], df["MA20"], lookback=10)
    if cross:
        kind, ago = cross
        when = "本根" if ago == 0 else f"{ago} 根前"
        if kind == "golden":
            badges.append((f"✨ 黃金交叉 MA5↗MA20（{when}）", "bull"))
            notes.append(f"{when} MA5 由下往上穿越 MA20，短線轉強訊號。")
            score += 10
        else:
            badges.append((f"💀 死亡交叉 MA5↘MA20（{when}）", "bear"))
            notes.append(f"{when} MA5 由上往下跌破 MA20，短線轉弱訊號。")
            score -= 10
    elif pd.notna(last["MA5"]) and pd.notna(last["MA20"]):
        above = last["MA5"] > last["MA20"]
        badges.append((f"MA5 {'位於' if above else '低於'} MA20", "bull" if above else "bear"))
        score += 5 if above else -5

    # 5-3 中期交叉：MA20 × MA60
    cross2 = find_last_cross(df["MA20"], df["MA60"], lookback=20)
    if cross2:
        kind, ago = cross2
        when = "本根" if ago == 0 else f"{ago} 根前"
        if kind == "golden":
            badges.append((f"🌟 中期黃金交叉 MA20↗MA60（{when}）", "bull"))
            notes.append("月線向上穿越季線，中期趨勢有轉多跡象。")
            score += 8
        else:
            badges.append((f"⚠️ 中期死亡交叉 MA20↘MA60（{when}）", "bear"))
            notes.append("月線向下跌破季線，中期趨勢有轉空疑慮。")
            score -= 8

    # 5-4 價格與季線、月線關係
    if pd.notna(last["MA60"]):
        if last["Close"] >= last["MA60"]:
            badges.append(("📈 站上季線", "bull"))
            score += 7
        else:
            badges.append(("📉 跌破季線", "bear"))
            score -= 7
    if pd.notna(last["MA20"]) and len(df) > 5 and pd.notna(df["MA20"].iloc[-6]):
        slope_up = last["MA20"] > df["MA20"].iloc[-6]
        notes.append(f"月線（MA20）近 5 根{'向上彎' if slope_up else '向下彎'}，"
                     f"收盤價{'高於' if last['Close'] >= last['MA20'] else '低於'}月線。")
        score += 5 if slope_up else -5

    # 5-5 MACD
    if pd.notna(last["OSC"]):
        prev_osc = df["OSC"].iloc[-2] if len(df) > 1 else np.nan
        if last["OSC"] > 0:
            txt = "MACD 柱狀體翻紅" if pd.notna(prev_osc) and prev_osc <= 0 else "MACD 柱狀體為正"
            badges.append((f"🟥 {txt}", "bull"))
            score += 8
        else:
            txt = "MACD 柱狀體翻綠" if pd.notna(prev_osc) and prev_osc > 0 else "MACD 柱狀體為負"
            badges.append((f"🟩 {txt}", "bear"))
            score -= 8

    # 5-6 RSI 超買超賣
    rsi = last["RSI14"]
    if pd.notna(rsi):
        if rsi >= 70:
            badges.append((f"🔥 RSI 超買 {rsi:.1f}", "warn"))
            notes.append(f"RSI14 = {rsi:.1f}，進入超買區，留意短線拉回風險。")
            score -= 5
        elif rsi <= 30:
            badges.append((f"🧲 RSI 超賣 {rsi:.1f}", "warn"))
            notes.append(f"RSI14 = {rsi:.1f}，進入超賣區，可能出現跌深反彈。")
            score += 5
        else:
            badges.append((f"RSI 中性 {rsi:.1f}", "neutral"))

    # 5-7 KD
    if pd.notna(last["K"]) and pd.notna(last["D"]):
        kd_cross = find_last_cross(df["K"], df["D"], lookback=3)
        if kd_cross and kd_cross[0] == "golden":
            badges.append(("KD 黃金交叉", "bull"))
            score += 4
        elif kd_cross and kd_cross[0] == "death":
            badges.append(("KD 死亡交叉", "bear"))
            score -= 4

    score = int(max(0, min(100, score)))
    if score >= 75:
        verdict, vclass = "強勢多頭 🐂", "up"
    elif score >= 60:
        verdict, vclass = "偏多格局", "up"
    elif score > 40:
        verdict, vclass = "盤整觀望", "flat"
    elif score > 25:
        verdict, vclass = "偏空格局", "down"
    else:
        verdict, vclass = "強勢空頭 🐻", "down"
    return dict(score=score, verdict=verdict, vclass=vclass, badges=badges, notes=notes)


# ------------------------------------------------------------------
# 6. 圖表繪製（plotly.graph_objects）
# ------------------------------------------------------------------
def build_chart(view: pd.DataFrame, t: dict, theme_key: str, selected_ma: list, sub_ind: str,
                fmt: str, vol_div: float, vol_unit: str, show_bb: bool, show_cross: bool,
                show_hl: bool, uirev: str) -> go.Figure:
    """
    主圖：K 線 + 均線（+布林通道、交叉標記、最高最低標示）
    副圖 1：成交量（紅漲綠跌）+ 均量線
    副圖 2：MACD / RSI / KD
    所有圖共用同一條 x 軸 → 時間軸完全同步、十字準心貫穿全部圖層
    """
    x = view.index.strftime(fmt).tolist()  # 使用類別軸：自動跳過假日與非交易時段，K 棒不會出現缺口
    up_c, down_c = t["up"], t["down"]
    is_up = (view["Close"] >= view["Open"]).values
    bar_colors = [up_c if u else down_c for u in is_up]
    has_sub = sub_ind != "不顯示"

    # 版面高度分配（三層 / 兩層）
    if has_sub:
        dom_main, dom_vol, dom_sub = [0.42, 1.0], [0.235, 0.395], [0.0, 0.205]
    else:
        dom_main, dom_vol, dom_sub = [0.27, 1.0], [0.0, 0.235], [0.0, 0.0]

    fig = go.Figure()

    # ---- 6-1 K 線（紅漲綠跌，空心/實心由顏色區分）----
    fig.add_trace(go.Candlestick(
        x=x, open=view["Open"], high=view["High"], low=view["Low"], close=view["Close"],
        increasing=dict(line=dict(color=up_c, width=1), fillcolor=up_c),
        decreasing=dict(line=dict(color=down_c, width=1), fillcolor=down_c),
        name="K線", hoverinfo="skip", yaxis="y", xaxis="x", showlegend=False,
    ))

    # ---- 6-2 隱形資料點：提供游標即時 OHLCV 數值 ----
    vol_show = view["Volume"] / vol_div
    custom = np.column_stack([
        view["Open"], view["High"], view["Low"], view["Close"], vol_show,
        view["Chg"].fillna(0), view["ChgPct"].fillna(0),
    ])
    fig.add_trace(go.Scatter(
        x=x, y=view["Close"], mode="markers", marker=dict(size=1, opacity=0),
        customdata=custom, name="行情", yaxis="y", xaxis="x", showlegend=False,
        text=[f"{v:+.2f}%" for v in view["ChgPct"].fillna(0)],  # 漲跌幅預先格式化成文字
        hovertemplate=(
            "開 <b>%{customdata[0]:,.2f}</b>　高 <b>%{customdata[1]:,.2f}</b><br>"
            "低 <b>%{customdata[2]:,.2f}</b>　收 <b>%{customdata[3]:,.2f}</b><br>"
            f"量 <b>%{{customdata[4]:,.0f}}</b> {vol_unit}　"
            "漲跌 <b>%{customdata[5]:+,.2f}</b>（%{text}）<extra></extra>"
        ),
    ))

    # ---- 6-3 布林通道（選用）----
    if show_bb:
        bb_color = t["accent2"]
        fig.add_trace(go.Scatter(x=x, y=view["BB_UP"], name="布林上軌", line=dict(color=bb_color, width=1, dash="dot"),
                                 yaxis="y", xaxis="x", showlegend=False, hovertemplate="%{y:,.2f}"))
        fig.add_trace(go.Scatter(x=x, y=view["BB_LOW"], name="布林下軌", line=dict(color=bb_color, width=1, dash="dot"),
                                 fill="tonexty", fillcolor=hex_rgba(bb_color, 0.07),
                                 yaxis="y", xaxis="x", showlegend=False, hovertemplate="%{y:,.2f}"))

    # ---- 6-4 均線疊加（依勾選動態加入）----
    for n in selected_ma:
        cfg = MA_CONFIG[n]
        col = view[f"MA{n}"]
        if col.notna().sum() == 0:
            continue
        fig.add_trace(go.Scatter(
            x=x, y=col, name=f"{cfg['name']}", mode="lines",
            line=dict(color=cfg[theme_key], width=1.6), yaxis="y", xaxis="x",
            showlegend=False, hovertemplate="%{y:,.2f}", connectgaps=False,
        ))

    # ---- 6-5 黃金 / 死亡交叉標記（MA5 × MA20）----
    if show_cross:
        diff = view["MA5"] - view["MA20"]
        sgn = np.sign(diff)
        prev = sgn.shift(1)
        golden = (sgn > 0) & (prev < 0)
        death = (sgn < 0) & (prev > 0)
        if golden.any():
            fig.add_trace(go.Scatter(
                x=[x[i] for i in np.where(golden.values)[0]], y=view["Low"][golden] * 0.985,
                mode="markers", marker=dict(symbol="triangle-up", size=11, color=up_c, line=dict(width=1, color="#FFFFFF")),
                name="黃金交叉", yaxis="y", xaxis="x", showlegend=False, hoverinfo="skip",
            ))
        if death.any():
            fig.add_trace(go.Scatter(
                x=[x[i] for i in np.where(death.values)[0]], y=view["High"][death] * 1.015,
                mode="markers", marker=dict(symbol="triangle-down", size=11, color=down_c, line=dict(width=1, color="#FFFFFF")),
                name="死亡交叉", yaxis="y", xaxis="x", showlegend=False, hoverinfo="skip",
            ))

    # ---- 6-6 區間最高 / 最低價標示（類似 Yahoo 股市）----
    annotations = []
    if show_hl and len(view) > 1:
        hi_pos = int(np.argmax(view["High"].values))
        lo_pos = int(np.argmin(view["Low"].values))
        annotations.append(dict(x=x[hi_pos], y=view["High"].iloc[hi_pos], xref="x", yref="y",
                                text=f"<b>{view['High'].iloc[hi_pos]:,.2f}</b>", showarrow=True, arrowhead=0,
                                arrowcolor=up_c, ax=0, ay=-22, font=dict(color=up_c, size=12)))
        annotations.append(dict(x=x[lo_pos], y=view["Low"].iloc[lo_pos], xref="x", yref="y",
                                text=f"<b>{view['Low'].iloc[lo_pos]:,.2f}</b>", showarrow=True, arrowhead=0,
                                arrowcolor=down_c, ax=0, ay=22, font=dict(color=down_c, size=12)))

    # ---- 6-7 成交量（紅漲綠跌）+ 均量線 ----
    fig.add_trace(go.Bar(
        x=x, y=vol_show, marker=dict(color=bar_colors, line=dict(width=0)), opacity=0.85,
        name=f"成交量", yaxis="y2", xaxis="x", showlegend=False,
        hovertemplate=f"%{{y:,.0f}} {vol_unit}",
    ))
    fig.add_trace(go.Scatter(x=x, y=view["MV5"] / vol_div, name="均量5", line=dict(color=MA_CONFIG[5][theme_key], width=1.1),
                             yaxis="y2", xaxis="x", showlegend=False, hovertemplate="%{y:,.0f}"))
    fig.add_trace(go.Scatter(x=x, y=view["MV20"] / vol_div, name="均量20", line=dict(color=MA_CONFIG[20][theme_key], width=1.1),
                             yaxis="y2", xaxis="x", showlegend=False, hovertemplate="%{y:,.0f}"))

    # ---- 6-8 副圖技術指標 ----
    shapes = []
    sub_title = ""
    if sub_ind.startswith("MACD"):
        sub_title = "MACD (12,26,9)"
        osc_colors = [up_c if v >= 0 else down_c for v in view["OSC"].fillna(0)]
        fig.add_trace(go.Bar(x=x, y=view["OSC"], marker=dict(color=osc_colors, line=dict(width=0)), opacity=0.8,
                             name="OSC", yaxis="y3", xaxis="x", showlegend=False, hovertemplate="%{y:,.3f}"))
        fig.add_trace(go.Scatter(x=x, y=view["DIF"], name="DIF", line=dict(color="#F2C94C", width=1.3),
                                 yaxis="y3", xaxis="x", showlegend=False, hovertemplate="%{y:,.3f}"))
        fig.add_trace(go.Scatter(x=x, y=view["MACD"], name="MACD", line=dict(color="#56A8F5", width=1.3),
                                 yaxis="y3", xaxis="x", showlegend=False, hovertemplate="%{y:,.3f}"))
    elif sub_ind.startswith("RSI"):
        sub_title = "RSI (6, 14)"
        fig.add_trace(go.Scatter(x=x, y=view["RSI6"], name="RSI6", line=dict(color="#F2994A", width=1.3),
                                 yaxis="y3", xaxis="x", showlegend=False, hovertemplate="%{y:.2f}"))
        fig.add_trace(go.Scatter(x=x, y=view["RSI14"], name="RSI14", line=dict(color="#B39DFA", width=1.3),
                                 yaxis="y3", xaxis="x", showlegend=False, hovertemplate="%{y:.2f}"))
        for lvl, col in ((70, up_c), (30, down_c)):
            shapes.append(dict(type="line", xref="paper", x0=0, x1=1, yref="y3", y0=lvl, y1=lvl,
                               line=dict(color=col, width=1, dash="dash")))
        shapes.append(dict(type="rect", xref="paper", x0=0, x1=1, yref="y3", y0=30, y1=70,
                           fillcolor=t["accent"], opacity=0.05, line=dict(width=0)))
    elif sub_ind.startswith("KD"):
        sub_title = "KD (9,3,3)"
        fig.add_trace(go.Scatter(x=x, y=view["K"], name="K", line=dict(color="#F2C94C", width=1.3),
                                 yaxis="y3", xaxis="x", showlegend=False, hovertemplate="%{y:.2f}"))
        fig.add_trace(go.Scatter(x=x, y=view["D"], name="D", line=dict(color="#56A8F5", width=1.3),
                                 yaxis="y3", xaxis="x", showlegend=False, hovertemplate="%{y:.2f}"))
        for lvl, col in ((80, up_c), (20, down_c)):
            shapes.append(dict(type="line", xref="paper", x0=0, x1=1, yref="y3", y0=lvl, y1=lvl,
                               line=dict(color=col, width=1, dash="dash")))

    # 副圖左上角小標題
    annotations.append(dict(text=f"成交量（{vol_unit}）", xref="paper", yref="paper", x=0.002, y=dom_vol[1],
                            xanchor="left", yanchor="top", showarrow=False, font=dict(size=11, color=t["muted"])))
    if has_sub:
        annotations.append(dict(text=sub_title, xref="paper", yref="paper", x=0.002, y=dom_sub[1],
                                xanchor="left", yanchor="top", showarrow=False, font=dict(size=11, color=t["muted"])))

    # ---- 6-9 共用座標軸樣式（十字準心）----
    axis_common = dict(
        gridcolor=t["grid"], zeroline=False, showline=False, color=t["muted"],
        showspikes=True, spikemode="across", spikesnap="cursor", spikethickness=1,
        spikedash="dot", spikecolor=t["spike"], tickfont=dict(family="JetBrains Mono", size=11),
    )
    n_bars = len(x)
    layout = dict(
        height=820 if has_sub else 680,
        margin=dict(l=10, r=10, t=10, b=10),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor=t["plot_bg"],
        font=dict(family="JetBrains Mono, Noto Sans TC, sans-serif", color=t["text"], size=12),
        hovermode="x unified",
        hoversubplots="axis",          # 滑鼠移到任一副圖，所有子圖的數值一起顯示
        hoverdistance=-1,
        spikedistance=-1,              # 十字線永遠跟隨游標
        dragmode="zoom",               # 預設為框選區域縮放
        uirevision=uirev,              # 勾選均線時保持目前縮放位置 → 疊加更流暢
        showlegend=False,
        bargap=0.25,
        hoverlabel=dict(bgcolor=t["hover_bg"], bordercolor=t["border"],
                        font=dict(family="JetBrains Mono, Noto Sans TC", color=t["text"], size=12)),
        xaxis=dict(
            **axis_common, type="category", anchor="y3" if has_sub else "y2",
            rangeslider=dict(visible=False), nticks=10, tickangle=0,
            categoryorder="array", categoryarray=x,
            range=[-0.8, n_bars - 0.2],
        ),
        yaxis=dict(**axis_common, domain=dom_main, side="right", tickformat=",.2f", fixedrange=False),
        yaxis2=dict(**axis_common, domain=dom_vol, side="right", tickformat="~s", rangemode="tozero"),
        annotations=annotations,
        shapes=shapes,
    )
    if has_sub:
        layout["yaxis3"] = dict(**axis_common, domain=dom_sub, side="right",
                                range=[0, 100] if (sub_ind.startswith("RSI") or sub_ind.startswith("KD")) else None)
    fig.update_layout(**layout)
    return fig


def hex_rgba(hex_color: str, alpha: float) -> str:
    """把 #RRGGBB 轉成 Plotly 可接受的 rgba() 字串（Plotly 不支援 8 位數含透明度的 hex）"""
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"


def build_gauge(score: int, t: dict) -> go.Figure:
    """AI 綜合分數儀表（0=極空、100=極多）"""
    color = t["up"] if score >= 60 else (t["down"] if score <= 40 else t["accent"])
    fig = go.Figure(go.Indicator(
        mode="gauge+number",
        value=score,
        number=dict(font=dict(family="Orbitron, JetBrains Mono", size=44, color=color), suffix=""),
        gauge=dict(
            axis=dict(range=[0, 100], tickwidth=0, tickcolor=t["muted"], tickfont=dict(color=t["muted"], size=10)),
            bar=dict(color=color, thickness=0.28),
            bgcolor="rgba(0,0,0,0)",
            borderwidth=0,
            steps=[
                dict(range=[0, 25], color=hex_rgba(t["down"], 0.25)),
                dict(range=[25, 40], color=hex_rgba(t["down"], 0.12)),
                dict(range=[40, 60], color=t["grid"]),
                dict(range=[60, 75], color=hex_rgba(t["up"], 0.12)),
                dict(range=[75, 100], color=hex_rgba(t["up"], 0.25)),
            ],
        ),
        title=dict(text="AI 技術面綜合分數", font=dict(size=13, color=t["muted"], family="Noto Sans TC")),
    ))
    fig.update_layout(height=230, margin=dict(l=20, r=20, t=40, b=0), paper_bgcolor="rgba(0,0,0,0)",
                      font=dict(color=t["text"]))
    return fig


# ------------------------------------------------------------------
# 7. 小工具
# ------------------------------------------------------------------
def cls_of(v: float) -> str:
    """依數值正負回傳 CSS 類別（紅漲綠跌）"""
    if pd.isna(v) or v == 0:
        return "flat"
    return "up" if v > 0 else "down"


def fmt_num(v: float, digits: int = 2) -> str:
    return "—" if pd.isna(v) else f"{v:,.{digits}f}"


def fmt_vol(v: float) -> str:
    """成交量中文單位縮寫：萬、億（避免數字過長被卡片截斷）"""
    if pd.isna(v):
        return "—"
    if v >= 1e8:
        return f"{v / 1e8:,.2f} 億"
    if v >= 1e5:
        return f"{v / 1e4:,.1f} 萬"
    return f"{v:,.0f}"


def set_ticker_from_quickpick() -> None:
    """熱門快選的 callback：選擇後自動帶入代碼輸入框"""
    choice = st.session_state.get("quickpick")
    if choice and choice in POPULAR:
        st.session_state.ticker = POPULAR[choice]


# ==================================================================
# 8. 主程式
# ==================================================================
def main() -> None:
    # ---- 8-1 Session 預設值 ----
    if "ticker" not in st.session_state:
        st.session_state.ticker = "2330"

    # ---- 8-2 側邊欄：主題、代碼、顯示設定 ----
    with st.sidebar:
        st.markdown('<div class="brand-title" style="font-size:1.35rem">QUANTUM·K</div>', unsafe_allow_html=True)
        st.caption("理財機器人｜智慧 K 線看盤系統")
        dark_mode = st.toggle("🌙 深色模式（Dark Mode）", value=True)
        theme_key = "dark" if dark_mode else "light"
        t = THEMES[theme_key]
        inject_css(t)

        st.markdown("---")
        st.markdown("##### 🔎 股票代碼")
        st.text_input("輸入代碼（台股免加 .TW，例：2330、0050、AAPL、^TWII）", key="ticker")
        st.selectbox("⚡ 熱門快選", options=list(POPULAR.keys()), index=None,
                     placeholder="選擇熱門標的…", key="quickpick", on_change=set_ticker_from_quickpick)

        st.markdown("---")
        st.markdown("##### 🧭 圖表設定")
        bars = st.slider("顯示 K 棒數量", min_value=30, max_value=600, value=150, step=10,
                         help="均線與指標皆以完整歷史資料計算，再擷取最後 N 根顯示，確保長天期均線從第一根就正確。")
        sub_ind = st.selectbox("📊 副圖技術指標", SUB_INDICATORS, index=0)
        show_bb = st.checkbox("布林通道 Bollinger (20, 2)", value=False)
        show_cross = st.checkbox("標示 MA5 × MA20 黃金／死亡交叉", value=True)
        show_hl = st.checkbox("標示區間最高／最低價", value=True)

        st.markdown("---")
        with st.expander("📘 操作說明"):
            st.markdown(
                "- **框選**：在圖上拖曳即可區域放大\n"
                "- **滾輪**：縮放時間軸\n"
                "- **雙擊**：還原圖表\n"
                "- **十字準心**：游標移動即時顯示 OHLCV\n"
                "- 台股慣例：**紅漲綠跌**"
            )
        st.caption("資料來源：Yahoo Finance（yfinance）｜僅供學術研究，不構成投資建議")

    # ---- 8-3 品牌標題列 ----
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    st.markdown(
        f"""<div class="brand">
              <div><div class="brand-title">QUANTUM·K TERMINAL</div>
                   <div class="brand-sub">理財機器人期中報告 · Python 爬蟲 × 技術分析 × AI 智慧診斷</div></div>
              <div class="live-dot"><b>●</b> LIVE · {now_str}</div>
            </div>""",
        unsafe_allow_html=True,
    )

    # ---- 8-4 網頁上方控制列：週期選項 ----
    top = st.container()
    with top:
        period_key = st.radio("K 線週期", list(PERIODS.keys()), index=4, horizontal=True,
                              label_visibility="collapsed")
    cfg = PERIODS[period_key]

    # ---- 8-5 載入資料（含完整錯誤處理）----
    raw = st.session_state.ticker
    with st.spinner(f"正在從 Yahoo Finance 爬取 {raw} 的{cfg['label']}資料…"):
        df, symbol, err = load_data(raw, period_key)

    if err is not None:
        kind, detail = err
        if kind == "empty":
            alert("info", "請輸入股票代碼", "請於左側欄輸入代碼，例如 <b>2330</b>（台積電）、<b>AAPL</b>（蘋果）、<b>^TWII</b>（加權指數）。")
        elif kind == "notfound":
            alert("error", f"查無此代碼：{raw}",
                  f"已嘗試：<span class='mono'>{detail}</span><br>"
                  "可能原因：① 代碼輸入錯誤 ② 該股已下市 ③ 該週期無資料（分鐘線僅提供近 60 天）。<br>"
                  "建議：台股直接輸入數字代碼即可（系統會自動判斷上市 .TW / 上櫃 .TWO）。")
        elif kind == "network":
            alert("error", "網路連線失敗",
                  "無法連線到 Yahoo Finance 伺服器，請檢查網路連線（Wi-Fi / 防火牆 / Proxy）後重新整理頁面。"
                  f"<br><span class='mono' style='opacity:.7;font-size:.8rem'>{detail}</span>")
        elif kind == "ratelimit":
            alert("warn", "Yahoo Finance 請求過於頻繁",
                  "已被暫時限制流量，請稍候 1～2 分鐘再試。系統已啟用 10 分鐘快取以降低請求次數。")
        else:
            alert("error", "資料讀取發生未預期錯誤",
                  f"請稍後再試或更換代碼。<br><span class='mono' style='opacity:.7;font-size:.8rem'>{detail}</span>")
        st.stop()

    if len(df) < 2:
        alert("warn", "資料筆數不足", f"{symbol} 在「{cfg['label']}」僅取得 {len(df)} 筆資料，無法繪製 K 線，請切換其他週期。")
        st.stop()

    # ---- 8-6 計算指標（完整資料）→ 擷取顯示區間 ----
    full = add_indicators(df)
    view = full.tail(bars)
    last = full.iloc[-1]
    prev_bar = full.iloc[-2]

    is_tw = symbol.endswith(".TW") or symbol.endswith(".TWO")
    vol_div, vol_unit = (1000, "張") if is_tw else (1, "股")
    name = fetch_name(symbol)
    currency = "TWD" if is_tw else ""

    # ---- 8-7 均線勾選（放在網頁上方，標籤即時顯示均線數值與方向）----
    with top:
        ma_cols = st.columns(6)
        selected_ma = []
        for col, n in zip(ma_cols, MA_LIST):
            c = dict(MA_CONFIG[n])
            if period_key != "日":  # 「週線、月線、季線」等俗稱只在日線週期才成立
                c["desc"] = f"{n} 根均線"
            v = last[f"MA{n}"]
            pv = prev_bar[f"MA{n}"]
            if pd.isna(v):
                label = f"{c['emoji']} {c['name']} {c['desc']}　:gray[資料不足]"
            else:
                arrow = ":red[▲]" if (pd.notna(pv) and v > pv) else (":green[▼]" if (pd.notna(pv) and v < pv) else "–")
                label = f"{c['emoji']} **{c['name']}** {c['desc']}  \n`{v:,.2f}` {arrow}"
            with col:
                if st.checkbox(label, value=c["default"], key=f"ma_{n}"):
                    selected_ma.append(n)

    # ---- 8-8 報價資訊卡（仿 Yahoo 股市資訊列）----
    data_time = full.index[-1].strftime("%Y/%m/%d %H:%M" if cfg["interval"].endswith("m") else "%Y/%m/%d")
    chg_cls = cls_of(last["Chg"])
    arrow = "▲" if last["Chg"] > 0 else ("▼" if last["Chg"] < 0 else "")
    ohlc_cls = lambda v: cls_of(v - prev_bar["Close"])
    st.markdown(
        f"""<div class="quote-card">
              <div class="quote-head">
                <div class="quote-name">{name}<span>{symbol}</span>
                     <span style="font-size:.85rem">· {cfg['label']}</span></div>
                <div class="quote-time">資料時間：{data_time}</div>
              </div>
              <div class="quote-bar">
                <div><em>開</em><span class="{ohlc_cls(last['Open'])}">{fmt_num(last['Open'])}</span></div>
                <div><em>高</em><span class="{ohlc_cls(last['High'])}">{fmt_num(last['High'])}</span></div>
                <div><em>低</em><span class="{ohlc_cls(last['Low'])}">{fmt_num(last['Low'])}</span></div>
                <div><em>收</em><span class="{chg_cls}">{fmt_num(last['Close'])}</span></div>
                <div><em>量({vol_unit})</em><b>{last['Volume'] / vol_div:,.0f}</b></div>
                <div><em>漲跌</em><span class="{chg_cls}">{arrow}{fmt_num(abs(last['Chg']))}
                     （{fmt_num(last['ChgPct'])}%）</span></div>
              </div>
            </div>""",
        unsafe_allow_html=True,
    )

    # ---- 8-9 關鍵指標 st.metric（delta_color="inverse" → 正值紅、負值綠，符合台股）----
    st.write("")
    m = st.columns(6)
    m[0].metric(f"收盤價 {currency}", fmt_num(last["Close"]),
                f"{last['Chg']:+,.2f} ({last['ChgPct']:+.2f}%)" if pd.notna(last["Chg"]) else None,
                delta_color="inverse")
    period_high, period_low = view["High"].max(), view["Low"].min()
    m[1].metric("區間最高", fmt_num(period_high), f"距今 {(period_high / last['Close'] - 1) * 100:+.2f}%", delta_color="off")
    m[2].metric("區間最低", fmt_num(period_low), f"距今 {(period_low / last['Close'] - 1) * 100:+.2f}%", delta_color="off")
    vol_delta = (last["Volume"] / prev_bar["Volume"] - 1) * 100 if prev_bar["Volume"] > 0 else np.nan
    m[3].metric(f"成交量（{vol_unit}）", fmt_vol(last["Volume"] / vol_div),
                f"{vol_delta:+.1f}% vs 前一根" if pd.notna(vol_delta) else None, delta_color="off")
    m[4].metric("振幅", f"{last['Amp']:.2f}%" if pd.notna(last["Amp"]) else "—",
                f"RSI14 {last['RSI14']:.1f}" if pd.notna(last["RSI14"]) else None, delta_color="off")
    view_ret = (view["Close"].iloc[-1] / view["Close"].iloc[0] - 1) * 100
    m[5].metric(f"區間報酬（{len(view)} 根）", f"{view_ret:+.2f}%",
                f"{view['Close'].iloc[-1] - view['Close'].iloc[0]:+,.2f}", delta_color="inverse")

    # ---- 8-10 AI 技術面自動診斷面板 ----
    section_title("AI 技術面智慧診斷")
    diag = diagnose(full)
    g_col, d_col = st.columns([1, 2.3])
    with g_col:
        st.plotly_chart(build_gauge(diag["score"], t), config={"displayModeBar": False}, **STRETCH)
    with d_col:
        badges_html = "".join(f'<span class="badge badge-{k}">{txt}</span>' for txt, k in diag["badges"])
        notes_html = "".join(f"<li>{n}</li>" for n in diag["notes"])
        st.markdown(
            f"""<div class="ai-card">
                  <div class="ai-note">依目前週期（{cfg['label']}）自動計算 · 共 {len(full):,} 根歷史 K 棒</div>
                  <div class="ai-verdict {diag['vclass']}">{diag['verdict']}</div>
                  <div>{badges_html}</div>
                  <ul class="ai-list">{notes_html}</ul>
                  <div class="ai-note" style="margin-top:8px">※ 本診斷為技術指標規則式運算結果，僅供學習參考，不構成任何投資建議。</div>
                </div>""",
            unsafe_allow_html=True,
        )

    # ---- 8-11 主圖：K 線 + 均線 + 成交量 + 副圖指標 ----
    section_title(f"K 線圖 · {cfg['label']}")
    if not selected_ma:
        st.caption("目前未勾選任何均線，可於上方勾選 MA5 ~ MA240 疊加至 K 線圖。")
    insufficient = [MA_CONFIG[n]["name"] for n in selected_ma if view[f"MA{n}"].notna().sum() == 0]
    if insufficient:
        alert("warn", "部分均線資料不足",
              f"{'、'.join(insufficient)} 需要的歷史 K 棒數超過目前可取得的資料量（共 {len(full)} 根），因此無法繪製。")

    fig = build_chart(
        view=view, t=t, theme_key=theme_key, selected_ma=selected_ma, sub_ind=sub_ind,
        fmt=cfg["fmt"], vol_div=vol_div, vol_unit=vol_unit, show_bb=show_bb,
        show_cross=show_cross, show_hl=show_hl, uirev=f"{symbol}-{period_key}-{bars}-{sub_ind}",
    )
    st.plotly_chart(
        fig, **STRETCH,
        config={
            "scrollZoom": True,
            "displaylogo": False,
            "modeBarButtonsToRemove": ["lasso2d", "select2d", "autoScale2d"],
            "toImageButtonOptions": {"format": "png", "filename": f"{symbol}_{period_key}_K線", "scale": 2},
        },
    )

    # ---- 8-12 歷史資料表 + CSV 下載 ----
    with st.expander("🗂️ 歷史資料明細與下載"):
        table = view[["Open", "High", "Low", "Close", "Volume", "Chg", "ChgPct"] +
                     [f"MA{n}" for n in MA_LIST] + ["DIF", "MACD", "OSC", "RSI14", "K", "D"]].copy()
        table["Volume"] = table["Volume"] / vol_div
        table.index = table.index.strftime(cfg["fmt"])
        table.index.name = "日期"
        table = table.rename(columns={"Open": "開盤", "High": "最高", "Low": "最低", "Close": "收盤",
                                      "Volume": f"成交量({vol_unit})", "Chg": "漲跌", "ChgPct": "漲跌幅%"})
        table = table.iloc[::-1].round(2)
        st.dataframe(table, height=360, **STRETCH)
        st.download_button(
            "⬇️ 下載 CSV（Excel 可直接開啟）",
            data=table.to_csv().encode("utf-8-sig"),
            file_name=f"{symbol}_{period_key}_history.csv",
            mime="text/csv",
        )

    st.markdown(
        '<div class="footer">QUANTUM·K TERMINAL · Built with Python · Streamlit · yfinance · Plotly '
        '｜ 台股慣例紅漲綠跌 ｜ 資料可能延遲，僅供教學使用</div>',
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
