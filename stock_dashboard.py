# -*- coding: utf-8 -*-
# =====================================================================
# 理財機器人 期中報告：股票歷史 K 線圖與技術指標網頁
# ---------------------------------------------------------------------
# 資料來源：Yahoo Finance（使用 yfinance 套件抓取公開的歷史股價）
# 網頁框架：Streamlit          圖表套件：Plotly
#
# 功能：
#   1. 週期（Interval）：1 分 K、5 分 K、15 分 K、日線、週線、月線
#   2. 資料跨度（Period）：1 個月、3 個月、6 個月、1 年、2 年、3 年、5 年
#   3. 均線：MA5、MA10、MA20、MA60（季線）、MA120（半年線）、MA240（年線）、
#      以及分 K 才有意義的「當日均價線（VWAP）」
#   4. 副圖指標（下拉切換）：成交量、MACD、KDJ
#   5. 台股慣例：紅漲綠跌
#
# 執行方式：streamlit run stock_dashboard.py
# =====================================================================

import json
import time
import urllib.request

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import yfinance as yf
from plotly.subplots import make_subplots

st.set_page_config(page_title="股票 K 線與技術指標", page_icon="📈", layout="wide")

# 一點點 CSS：筆電寬度時 st.metric 的數字會被截成「2,13…」，
# 這裡讓字級跟著螢幕寬度縮放，並且不要截斷
st.markdown(
    """
    <style>
    [data-testid="stMetricValue"] { font-size: clamp(1.1rem, 1.9vw, 2rem); }
    [data-testid="stMetricValue"] * { overflow: visible !important; text-overflow: clip !important; white-space: nowrap; }
    [data-testid="stMetricDelta"] * { overflow: visible !important; text-overflow: clip !important; white-space: normal !important; }
    </style>
    """,
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------
# 一、常數設定
# ---------------------------------------------------------------------

# 台股習慣「紅漲綠跌」，剛好跟美股相反，所以顏色要自己指定
UP_COLOR = "#E0283B"     # 上漲：紅色
DOWN_COLOR = "#0F9D58"   # 下跌：綠色

# 每條均線的顏色（盡量挑對比清楚、不刺眼的顏色）
MA_COLORS = {
    5: "#E6A817",    # MA5   黃
    10: "#E07B24",   # MA10  橙
    20: "#2F80ED",   # MA20  藍
    60: "#14A37F",   # MA60  綠（季線）
    120: "#8B5CF6",  # MA120 紫（半年線）
    240: "#6B7280",  # MA240 灰（年線）
}
MA_NAMES = {5: "MA5", 10: "MA10", 20: "MA20", 60: "MA60 季線", 120: "MA120 半年線", 240: "MA240 年線"}
VWAP_COLOR = "#D946EF"   # 均價線用洋紅色，跟其他均線區分

# 週期選項：
#   yf_interval：丟給 yfinance 的參數
#   intraday   ：是不是分 K（分 K 的抓取限制跟日線完全不同，要分開處理）
#   resample   ：週線、月線不直接跟 Yahoo 要，而是自己用日線 resample（原因寫在 resample_ohlcv 裡）
INTERVALS = {
    "1 分 K":  {"yf_interval": "1m",  "intraday": True,  "resample": None},
    "5 分 K":  {"yf_interval": "5m",  "intraday": True,  "resample": None},
    "15 分 K": {"yf_interval": "15m", "intraday": True,  "resample": None},
    "日線":    {"yf_interval": "1d",  "intraday": False, "resample": None},
    "週線":    {"yf_interval": "1d",  "intraday": False, "resample": "W"},
    "月線":    {"yf_interval": "1d",  "intraday": False, "resample": "M"},
}

# 資料跨度選項（以「月」為單位，之後用 pd.DateOffset 往回推起始日期）
PERIODS = {"1 個月": 1, "3 個月": 3, "6 個月": 6, "1 年": 12, "2 年": 24, "3 年": 36, "5 年": 60}

# yfinance（其實是 Yahoo）對分 K 的限制：
#   1 分 K：一次最多只能抓 7 天，而且只保留最近約 30 天
#   5 分 K、15 分 K：只能抓最近 60 天
# 所以使用者選的跨度如果超過，就自動縮成允許的最大值，並在畫面上提醒
INTRADAY_MAX_DAYS = {"1m": 7, "5m": 60, "15m": 60}

# 週線、月線如果跨度太短，K 棒會少到看不出東西（例如月線只看 1 個月 = 1 根），
# 所以設一個合理的最短跨度
MIN_MONTHS = {"週線": 3, "月線": 12}

# 長天期均線需要「暖機資料」：例如日線 MA240 需要前面 240 個交易日的收盤價，
# 如果只抓使用者要看的 1 年，畫面最左邊的 MA240 會是空的。
# 所以實際抓資料時，會比顯示範圍再往前多抓一段（單位：天）
WARMUP_DAYS = {"日線": 400, "週線": 240 * 7 + 30, "月線": 240 * 31 + 60}


# ---------------------------------------------------------------------
# 二、抓資料
# ---------------------------------------------------------------------
def to_yahoo_symbols(code):
    """
    把使用者輸入的代碼轉成 Yahoo Finance 的格式。
    台股只輸入數字（例如 2330）時，Yahoo 要加上市場後綴：
    上市是 .TW、上櫃是 .TWO，我們不知道是哪一種，所以兩個都試。
    """
    code = code.strip().upper()
    if code.isdigit():
        return [code + ".TW", code + ".TWO"]
    return [code]


@st.cache_data(ttl=600, show_spinner="正在向 Yahoo Finance 抓取資料…")
def download_history(symbol, yf_interval, start=None, period=None):
    """
    向 Yahoo Finance 抓歷史資料。
    日線用 start（起始日期）抓，分 K 用 period（例如 "7d"）抓。
    加上 st.cache_data 快取 10 分鐘：切換均線、副圖時不會重新下載，也避免被 Yahoo 擋。
    """
    ticker = yf.Ticker(symbol)
    if period is not None:
        df = ticker.history(period=period, interval=yf_interval, auto_adjust=False)
    else:
        df = ticker.history(start=start, interval=yf_interval, auto_adjust=False)
    return df


@st.cache_data(ttl=86400, show_spinner="正在讀取股票名稱…")
def get_tw_stock_names():
    """從證交所、櫃買中心的開放資料（OpenAPI）抓所有台股的中文名稱，一天更新一次"""
    names = {}
    sources = [
        ("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", "Code", "Name"),
        ("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes", "SecuritiesCompanyCode", "CompanyName"),
    ]
    for url, code_key, name_key in sources:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                for row in json.loads(resp.read().decode("utf-8")):
                    names[row[code_key]] = row[name_key]
        except Exception:
            pass  # 名稱抓不到不影響主要功能，最後顯示代碼就好
    return names


def get_stock_name(symbol):
    """台股用中文名稱，其他市場用 Yahoo 提供的名稱"""
    code = symbol.replace(".TWO", "").replace(".TW", "")
    if symbol.endswith((".TW", ".TWO")):
        return get_tw_stock_names().get(code, code)
    try:
        return yf.Ticker(symbol).info.get("shortName", symbol)
    except Exception:
        return symbol


# ---------------------------------------------------------------------
# 三、資料處理：重採樣（resample）
# ---------------------------------------------------------------------
def resample_ohlcv(df, rule):
    """
    把「日線」整理成「週線」或「月線」。

    為什麼不直接跟 Yahoo 要週線、月線？
      Yahoo 的週 K 是以「週一」當作日期，而且遇到假日時有時會怪怪的；
      自己用日線算，可以完全掌握規則，也確保跟均線的計算基準一致。

    一根週 K（或月 K）的 OHLCV 怎麼來：
      開盤 Open  = 這段期間「第一個交易日」的開盤價  → first
      最高 High  = 這段期間所有日 K 的最高價         → max
      最低 Low   = 這段期間所有日 K 的最低價         → min
      收盤 Close = 這段期間「最後一個交易日」的收盤價 → last
      成交量 Volume = 這段期間每天成交量加總         → sum

    日期標籤：
      pandas 的 resample 預設會把日期標在「週五」或「月底」，
      但如果那天剛好放假（或這週還沒結束），圖上就會出現一個沒有交易的日期。
      所以這裡多記一欄 Date（原本的交易日），取每段期間的最後一筆，
      用「實際最後一個交易日」當作這根 K 棒的日期，跟券商軟體的顯示方式一樣。
    """
    if rule == "W":
        freq = "W-FRI"          # 每週以週五結束
    else:
        freq = "ME"             # 新版 pandas 的「月底」寫法
    tmp = df.copy()
    tmp["Date"] = tmp.index
    agg = {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum", "Date": "last"}
    try:
        out = tmp.resample(freq).agg(agg)
    except ValueError:
        out = tmp.resample("M").agg(agg)  # 舊版 pandas 只認得 "M"
    out = out.dropna(subset=["Close"])   # 沒有任何交易日的週 / 月（例如連假）會是空的，刪掉
    out.index = pd.DatetimeIndex(out["Date"])
    return out.drop(columns="Date")


# ---------------------------------------------------------------------
# 四、技術指標計算（全部用 pandas 自己算，不依賴 ta-lib）
# ---------------------------------------------------------------------
def add_moving_averages(df):
    """
    簡單移動平均線 SMA（Simple Moving Average）。

    公式：MA(n) 第 t 根 = (第 t-n+1 根 ~ 第 t 根，共 n 根收盤價的總和) / n

    pandas 寫法：df["Close"].rolling(n).mean()
      rolling(n) 會建立一個長度 n 的「滑動視窗」，每往下一根就整個視窗往後移一格，
      .mean() 就是對視窗裡的 n 個收盤價取平均。
      前 n-1 根資料不足 n 筆，pandas 會回傳 NaN（圖上就不畫），這是正確的行為，
      不能用 0 或部分平均去補，不然均線開頭會是錯的。

    各條均線的意義（以日線為例）：
      MA5   ≈ 一週（5 個交易日）
      MA10  ≈ 兩週
      MA20  ≈ 一個月，俗稱「月線」
      MA60  ≈ 一季，俗稱「季線」
      MA120 ≈ 半年，俗稱「半年線」
      MA240 ≈ 一年，俗稱「年線」
    注意：均線是依「目前選的週期」計算。週線的 MA20 是最近 20 根「週 K」的平均，
    不是日線的 MA20，所以切換週期一定要重新算。
    """
    for n in MA_COLORS:
        df[f"MA{n}"] = df["Close"].rolling(window=n).mean()
    return df


def add_vwap(df):
    """
    當日均價線 VWAP（Volume Weighted Average Price，成交量加權平均價）。
    只有分 K 才有意義：它代表「今天開盤到現在，所有人的平均成交成本」。

    公式：
      典型價格 TP = (最高 + 最低 + 收盤) / 3
      VWAP = 今天累計的 (TP × 成交量) / 今天累計的成交量

    重點：每天開盤要「重新累計」，所以要用 groupby(日期) 分組後再做 cumsum。
    如果某段時間成交量剛好是 0，分母會是 0，用 replace 換成 NaN 避免除以 0。
    """
    tp = (df["High"] + df["Low"] + df["Close"]) / 3
    day = df.index.date
    cum_pv = (tp * df["Volume"]).groupby(day).cumsum()
    cum_vol = df["Volume"].groupby(day).cumsum()
    df["VWAP"] = cum_pv / cum_vol.replace(0, np.nan)
    return df


def add_macd(df, fast=12, slow=26, signal=9):
    """
    MACD（Moving Average Convergence Divergence，指數平滑異同移動平均線）。

    計算步驟：
      1. 先算兩條指數移動平均 EMA：
           EMA(n) 第 t 根 = α × 收盤價_t + (1 − α) × EMA_(t−1)，其中 α = 2 / (n + 1)
         EMA 跟 SMA 不同：越近的價格權重越大，反應比較快。
         pandas：Close.ewm(span=n, adjust=False).mean()
           span=n 就是 α = 2/(n+1)；adjust=False 代表用上面那條遞迴公式（跟看盤軟體一致）
      2. 快線 DIF = EMA(12) − EMA(26)
         → 短期均價減長期均價，DIF > 0 代表短期比長期強
      3. 慢線 DEA（台灣常叫 MACD 線）= DIF 的 9 期 EMA
      4. 柱狀圖 OSC = DIF − DEA
         → 柱子由負轉正代表快線往上穿過慢線（動能轉強），反之轉弱
         （有些中國的看盤軟體會把柱狀圖再乘以 2，這裡採台灣常用的 DIF − DEA）

    前面大約 26 + 9 根資料，EMA 還在「暖機」，數值不太準，所以把前 33 根設成 NaN 不畫。
    """
    ema_fast = df["Close"].ewm(span=fast, adjust=False).mean()
    ema_slow = df["Close"].ewm(span=slow, adjust=False).mean()
    df["DIF"] = ema_fast - ema_slow
    df["DEA"] = df["DIF"].ewm(span=signal, adjust=False).mean()
    df["OSC"] = df["DIF"] - df["DEA"]
    warmup = slow + signal - 2
    df.iloc[:warmup, df.columns.get_indexer(["DIF", "DEA", "OSC"])] = np.nan
    return df


def add_kdj(df, n=9):
    """
    KDJ 隨機指標（台灣常用參數 9, 3, 3）。

    計算步驟：
      1. RSV（未成熟隨機值）：今天收盤價在「最近 9 根 K 棒的高低區間」中的相對位置
           RSV = (收盤價 − 最近 9 根最低價) / (最近 9 根最高價 − 最近 9 根最低價) × 100
           → 0 代表收在 9 根以來最低點，100 代表收在最高點
           最高價 = 最低價（完全沒波動）時分母為 0，這時 RSV 設 50（中間值）
      2. K 值 = 前一天 K × 2/3 + 今天 RSV × 1/3   （RSV 的平滑，第一天的「前一天 K」設 50）
      3. D 值 = 前一天 D × 2/3 + 今天 K × 1/3     （K 的再平滑，所以 D 比 K 慢）
      4. J 值 = 3K − 2D
           → J 是 K 偏離 D 的放大版，反應最快，可以超過 100 或低於 0

    為什麼用 for 迴圈：K、D 都要用到「前一天自己的值」（遞迴），
    用迴圈寫最直觀，也確保初始值 50 的規則跟台灣看盤軟體一樣。
    """
    low_n = df["Low"].rolling(window=n).min()
    high_n = df["High"].rolling(window=n).max()
    denom = (high_n - low_n).replace(0, np.nan)
    rsv = ((df["Close"] - low_n) / denom * 100).fillna(50)

    k_list, d_list = [], []
    k_prev, d_prev = 50.0, 50.0
    for i in range(len(df)):
        if i < n - 1:                # 前 8 根資料不足 9 根，算不出 RSV
            k_list.append(np.nan)
            d_list.append(np.nan)
            continue
        k_now = k_prev * 2 / 3 + rsv.iloc[i] / 3
        d_now = d_prev * 2 / 3 + k_now / 3
        k_list.append(k_now)
        d_list.append(d_now)
        k_prev, d_prev = k_now, d_now

    df["K"] = k_list
    df["D"] = d_list
    df["J"] = 3 * df["K"] - 2 * df["D"]
    return df


# ---------------------------------------------------------------------
# 五、畫圖
# ---------------------------------------------------------------------
def draw_chart(df, show_ma, show_vwap, sub_chart, x_format, vol_unit):
    """
    上方主圖：K 線 + 均線（+ VWAP）
    下方副圖：成交量 / MACD / KDJ 三選一
    兩張圖用 make_subplots(shared_xaxes=True) 共用 x 軸，縮放時會一起動。
    """
    # x 軸用文字（類別軸），這樣週末、假日、收盤後的時間不會在圖上留下空白
    x = df.index.strftime(x_format)

    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.04, row_heights=[0.72, 0.28])

    # --- K 線：收盤 >= 開盤用紅色（漲），收盤 < 開盤用綠色（跌） ---
    fig.add_trace(go.Candlestick(
        x=x, open=df["Open"], high=df["High"], low=df["Low"], close=df["Close"],
        increasing_line_color=UP_COLOR, increasing_fillcolor=UP_COLOR,
        decreasing_line_color=DOWN_COLOR, decreasing_fillcolor=DOWN_COLOR,
        name="K線",
    ), row=1, col=1)

    # --- 均線：只畫有勾選、而且有算出數值的 ---
    for n, checked in show_ma.items():
        if checked and df[f"MA{n}"].notna().any():
            fig.add_trace(go.Scatter(
                x=x, y=df[f"MA{n}"], mode="lines", name=f"MA{n}",
                line=dict(color=MA_COLORS[n], width=1.4),
            ), row=1, col=1)

    # --- 均價線 VWAP（只有分 K 會傳進 True） ---
    if show_vwap and "VWAP" in df:
        fig.add_trace(go.Scatter(
            x=x, y=df["VWAP"], mode="lines", name="均價線 VWAP",
            line=dict(color=VWAP_COLOR, width=1.4, dash="dot"),
        ), row=1, col=1)

    # --- 副圖 ---
    if sub_chart == "成交量":
        # 成交量顏色跟 K 棒同一套規則：當根收盤 >= 開盤 → 紅，否則 → 綠
        colors = [UP_COLOR if c >= o else DOWN_COLOR for o, c in zip(df["Open"], df["Close"])]
        fig.add_trace(go.Bar(x=x, y=df["Volume"], marker_color=colors, name=f"成交量({vol_unit})"), row=2, col=1)
        fig.update_yaxes(title_text=f"成交量({vol_unit})", row=2, col=1)

    elif sub_chart == "MACD":
        # 柱狀圖 OSC：正值紅色、負值綠色
        osc_colors = [UP_COLOR if v >= 0 else DOWN_COLOR for v in df["OSC"].fillna(0)]
        fig.add_trace(go.Bar(x=x, y=df["OSC"], marker_color=osc_colors, name="OSC 柱狀圖"), row=2, col=1)
        fig.add_trace(go.Scatter(x=x, y=df["DIF"], mode="lines", name="DIF 快線",
                                 line=dict(color="#E6A817", width=1.3)), row=2, col=1)
        fig.add_trace(go.Scatter(x=x, y=df["DEA"], mode="lines", name="DEA 慢線",
                                 line=dict(color="#2F80ED", width=1.3)), row=2, col=1)
        fig.update_yaxes(title_text="MACD", row=2, col=1)

    elif sub_chart == "KDJ":
        fig.add_trace(go.Scatter(x=x, y=df["K"], mode="lines", name="K",
                                 line=dict(color="#E6A817", width=1.3)), row=2, col=1)
        fig.add_trace(go.Scatter(x=x, y=df["D"], mode="lines", name="D",
                                 line=dict(color="#2F80ED", width=1.3)), row=2, col=1)
        fig.add_trace(go.Scatter(x=x, y=df["J"], mode="lines", name="J",
                                 line=dict(color="#8B5CF6", width=1.1)), row=2, col=1)
        # 80 以上常被視為超買區、20 以下為超賣區，畫兩條虛線當參考
        fig.add_hline(y=80, line_dash="dash", line_color=UP_COLOR, line_width=1, row=2, col=1)
        fig.add_hline(y=20, line_dash="dash", line_color=DOWN_COLOR, line_width=1, row=2, col=1)
        fig.update_yaxes(title_text="KDJ", row=2, col=1)

    fig.update_layout(
        height=700,
        template="plotly_white",           # 乾淨的白底樣式
        xaxis_rangeslider_visible=False,   # 關掉 K 線預設的拉桿
        hovermode="x unified",             # 滑鼠移到某一根，所有數值一起顯示
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
        margin=dict(l=10, r=10, t=30, b=10),
    )
    fig.update_xaxes(type="category", nticks=10, showspikes=True, spikemode="across", spikethickness=1)
    fig.update_yaxes(title_text="股價", row=1, col=1)
    return fig


# =====================================================================
# 六、網頁畫面
# =====================================================================
st.title("📈 股票 K 線與技術指標")
st.caption("資料來源：Yahoo Finance（yfinance）｜台股慣例：紅漲綠跌｜僅供學習研究，非投資建議")

# --- 上方：代碼、週期、跨度 ---
c1, c2, c3 = st.columns([1.2, 1, 1])
stock_code = c1.text_input("股票代碼", value="2330", help="台股輸入數字即可（例如 2330、6488），美股輸入英文代碼（例如 AAPL）")
interval_name = c2.selectbox("週期", list(INTERVALS.keys()), index=3)
period_name = c3.selectbox("資料跨度", list(PERIODS.keys()), index=3)
interval = INTERVALS[interval_name]

# --- 側邊欄：均線與均價線 ---
with st.sidebar:
    st.header("均線設定")
    st.caption("預設只顯示 MA5 / 10 / 20，需要時再勾選其他均線")
    show_ma = {}
    for n in MA_COLORS:
        show_ma[n] = st.checkbox(MA_NAMES[n], value=(n in (5, 10, 20)))
    st.divider()
    show_vwap = st.checkbox("均價線 VWAP（分 K 專用）", value=True, disabled=not interval["intraday"],
                            help="當日成交量加權平均價，每天開盤重新計算，只有分 K 才會顯示")

if stock_code.strip() == "":
    st.info("請輸入股票代碼")
    st.stop()

# ---------------------------------------------------------------------
# 決定要抓多少資料
# ---------------------------------------------------------------------
months = PERIODS[period_name]
notice = None

if interval["intraday"]:
    # 分 K：受 Yahoo 限制，跨度超過就縮短
    max_days = INTRADAY_MAX_DAYS[interval["yf_interval"]]
    want_days = months * 30
    use_days = min(want_days, max_days)
    if want_days > max_days:
        notice = f"Yahoo Finance 的 {interval_name} 最多只提供最近 {max_days} 天資料，已自動改為顯示最近 {max_days} 天。"
    fetch_kwargs = {"period": f"{use_days}d"}
else:
    # 週線、月線跨度太短時自動拉長
    min_m = MIN_MONTHS.get(interval_name, 0)
    if months < min_m:
        notice = f"{interval_name}選 {period_name} 只會有很少幾根 K 棒，已自動改為 {min_m // 12 if min_m >= 12 else min_m}{' 年' if min_m >= 12 else ' 個月'}。"
        months = min_m
    # 顯示的起始日期 = 今天往前推 N 個月；實際抓的起始日期再往前多抓暖機資料
    show_start = pd.Timestamp.today().normalize() - pd.DateOffset(months=months)
    fetch_start = show_start - pd.Timedelta(days=WARMUP_DAYS[interval_name])
    fetch_kwargs = {"start": fetch_start.strftime("%Y-%m-%d")}

# ---------------------------------------------------------------------
# 抓資料（台股上市、上櫃都試，有資料就停）
# ---------------------------------------------------------------------
df, symbol = None, None
last_error = None
for s in to_yahoo_symbols(stock_code):
    # Yahoo 偶爾會在短時間內回傳空資料或連線失敗，所以每個代碼最多試 3 次，
    # 每次失敗先清掉快取（不然會一直拿到快取裡的空資料），再等一下重抓
    for attempt in range(3):
        try:
            data = download_history(s, interval["yf_interval"], **fetch_kwargs)
        except Exception as e:
            data, last_error = None, e
        if data is not None and not data.empty:
            df, symbol = data, s
            break
        download_history.clear()
        time.sleep(0.8 * (attempt + 1))
    if df is not None:
        break

if df is None and last_error is not None:
    st.error(f"資料下載失敗，請檢查網路連線後再試一次。（錯誤訊息：{last_error}）")
    st.stop()

if df is None:
    st.warning("找不到此股票代碼，請重新輸入")
    st.stop()

if notice:
    st.info(notice)

# 只保留需要的欄位；把時區拿掉（保留交易所當地時間），resample 和顯示才不會出錯
df = df[["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Close"])
if df.index.tz is not None:
    df.index = df.index.tz_localize(None)

# 週線、月線：用日線自己 resample
if interval["resample"]:
    df = resample_ohlcv(df, interval["resample"])

# ---------------------------------------------------------------------
# 計算指標：一定要用「完整資料（含暖機）」算完，再切出要顯示的範圍
# ---------------------------------------------------------------------
df = add_moving_averages(df)
df = add_macd(df)
df = add_kdj(df)
if interval["intraday"]:
    df = add_vwap(df)

if interval["intraday"]:
    show_df = df
    x_format = "%m/%d %H:%M"
else:
    show_df = df[df.index >= show_start]
    x_format = "%Y/%m" if interval_name == "月線" else "%Y/%m/%d"

if len(show_df) < 2:
    st.warning("這個區間的資料太少，畫不出 K 線，請選擇較長的資料跨度。")
    st.stop()

# 台股成交量換成「張」（1 張 = 1000 股）
is_tw = symbol.endswith((".TW", ".TWO"))
vol_unit = "張" if is_tw else "股"
show_df = show_df.copy()
if is_tw:
    show_df["Volume"] = show_df["Volume"] / 1000

# ---------------------------------------------------------------------
# 標題與重點數字
# ---------------------------------------------------------------------
name = get_stock_name(symbol)
st.subheader(f"{name}（{symbol}）")

last = show_df.iloc[-1]
prev = show_df.iloc[-2]
change = last["Close"] - prev["Close"]
pct = change / prev["Close"] * 100

m1, m2, m3, m4, m5 = st.columns(5)
# delta_color="inverse"：讓正數顯示紅色、負數顯示綠色，符合台股習慣
m1.metric("收盤價", f"{last['Close']:,.2f}", f"{change:+,.2f} ({pct:+.2f}%)", delta_color="inverse")
m2.metric("區間最高", f"{show_df['High'].max():,.2f}")
m3.metric("區間最低", f"{show_df['Low'].min():,.2f}")
m4.metric(f"最新成交量（{vol_unit}）", f"{last['Volume']:,.0f}")
m5.metric("K / D", f"{last['K']:.1f} / {last['D']:.1f}" if pd.notna(last["K"]) else "—")

date_fmt = "%Y/%m/%d %H:%M" if interval["intraday"] else "%Y/%m/%d"
st.caption(f"顯示範圍：{show_df.index[0].strftime(date_fmt)} ～ {show_df.index[-1].strftime(date_fmt)}，"
           f"共 {len(show_df)} 根 {interval_name}")

# ---------------------------------------------------------------------
# 用頁籤分開：圖表 / 資料表 / 指標說明，畫面才不會一次攤太多東西
# ---------------------------------------------------------------------
tab_chart, tab_data, tab_help = st.tabs(["📊 K 線圖", "📋 歷史資料", "📖 指標說明"])

with tab_chart:
    sub_chart = st.selectbox("副圖指標", ["成交量", "MACD", "KDJ"], index=0)

    # 長均線資料不夠時提醒（例如月線只抓得到幾年，MA240 需要 20 年）
    missing = [MA_NAMES[n] for n, c in show_ma.items() if c and show_df[f"MA{n}"].isna().all()]
    if missing:
        st.warning(f"{'、'.join(missing)} 需要的 K 棒數超過可取得的資料，因此無法畫出。")

    fig = draw_chart(show_df, show_ma, show_vwap and interval["intraday"], sub_chart, x_format, vol_unit)
    st.plotly_chart(fig, use_container_width=True)

with tab_data:
    cols = ["Open", "High", "Low", "Close", "Volume"] + [f"MA{n}" for n in MA_COLORS] + ["DIF", "DEA", "OSC", "K", "D", "J"]
    if interval["intraday"]:
        cols.append("VWAP")
    table = show_df[cols].sort_index(ascending=False)
    table.index = table.index.strftime(date_fmt)
    table.index.name = "時間"
    table = table.rename(columns={"Open": "開盤", "High": "最高", "Low": "最低", "Close": "收盤",
                                  "Volume": f"成交量({vol_unit})"})
    st.dataframe(table.round(2), use_container_width=True, height=450)
    st.download_button("下載 CSV", table.round(4).to_csv().encode("utf-8-sig"),
                       file_name=f"{symbol}_{interval['yf_interval']}.csv", mime="text/csv")

with tab_help:
    st.markdown(
        """
**移動平均線 MA**：最近 N 根 K 棒收盤價的平均（`rolling(N).mean()`）。依目前選的週期計算，
例如週線的 MA20 是 20 根週 K 的平均。日線的 MA20 / 60 / 120 / 240 分別俗稱月線、季線、半年線、年線。

**均價線 VWAP**：當日累計（典型價格 × 成交量）÷ 當日累計成交量，每天開盤重新計算，只在分 K 顯示。

**MACD (12, 26, 9)**：DIF = EMA12 − EMA26；DEA = DIF 的 9 期 EMA；柱狀圖 OSC = DIF − DEA。

**KDJ (9, 3, 3)**：RSV = (收盤 − 9 根最低) ÷ (9 根最高 − 9 根最低) × 100；
K = 2/3 × 前 K + 1/3 × RSV；D = 2/3 × 前 D + 1/3 × K；J = 3K − 2D。K、D 初始值為 50。

**週線 / 月線**：由日線 resample 而來：開盤取第一天、最高取最大、最低取最小、收盤取最後一天、成交量加總。

**分 K 的限制**：Yahoo Finance 的 1 分 K 只提供最近 7 天、5 / 15 分 K 只提供最近 60 天，超過會自動縮短。
        """
    )
