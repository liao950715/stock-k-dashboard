# -*- coding: utf-8 -*-
# =====================================================
# 理財機器人 期中報告：股票歷史 K 線圖網頁
# 資料來源：Yahoo Finance（用 yfinance 套件抓取公開歷史股價）
# 網頁框架：Streamlit，圖表：Plotly
#
# 題目要求：
#   1. 週期選項：日線、週線、月線
#   2. 均線勾選：MA5、MA10、MA20
#
# 執行方式：streamlit run stock_dashboard.py
# =====================================================

import json
import urllib.request

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import yfinance as yf
from plotly.subplots import make_subplots

st.set_page_config(page_title="股票 K 線圖", page_icon="📈", layout="wide")

# 台股習慣是紅漲綠跌（跟美股相反）
UP_COLOR = "#E0283B"    # 漲：紅
DOWN_COLOR = "#0F9D58"  # 跌：綠

# 三條均線各自的顏色
MA_COLORS = {5: "#E6A817", 10: "#8E44AD", 20: "#2F80ED"}

# 每種週期要抓多久的資料、畫面上顯示幾根 K 棒
# 抓的資料比顯示的多，這樣 MA20 從畫面最左邊就有值
PERIOD_SETTINGS = {
    "日線": {"fetch": "2y", "show": 120},
    "週線": {"fetch": "5y", "show": 104},
    "月線": {"fetch": "max", "show": 60},
}


# -----------------------------------------------------
# 抓資料
# -----------------------------------------------------
def to_yahoo_symbol(code):
    """台股只輸入數字的話，要先試上市(.TW)，找不到再試上櫃(.TWO)"""
    code = code.strip().upper()
    if code.isdigit():
        return [code + ".TW", code + ".TWO"]
    return [code]


@st.cache_data(ttl=600, show_spinner="正在向 Yahoo Finance 抓取資料…")  # 快取 10 分鐘，避免一直重複向 Yahoo 抓資料
def get_daily_data(symbol, period):
    df = yf.Ticker(symbol).history(period=period, interval="1d", auto_adjust=False)
    return df


@st.cache_data(ttl=86400, show_spinner="正在讀取股票名稱…")  # 股票名稱一天抓一次就好
def get_tw_stock_names():
    """從證交所、櫃買中心的開放資料抓台股中文名稱"""
    names = {}
    urls = [
        ("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", "Code", "Name"),
        ("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes", "SecuritiesCompanyCode", "CompanyName"),
    ]
    for url, code_key, name_key in urls:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                for row in json.loads(resp.read().decode("utf-8")):
                    names[row[code_key]] = row[name_key]
        except Exception:
            pass  # 抓不到名稱沒關係，最後就顯示代碼
    return names


def get_stock_name(symbol):
    code = symbol.replace(".TWO", "").replace(".TW", "")
    if symbol.endswith(".TW") or symbol.endswith(".TWO"):
        return get_tw_stock_names().get(code, code)
    try:
        return yf.Ticker(symbol).info.get("shortName", symbol)  # 美股就用 Yahoo 的名稱
    except Exception:
        return symbol


# -----------------------------------------------------
# 資料處理
# -----------------------------------------------------
def resample_data(df, rule):
    """將日線資料依週線或月線進行重採樣(resample)
    開盤取第一天、最高取最大、最低取最小、收盤取最後一天、成交量加總"""
    agg = {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
    new_df = df.resample(rule).agg(agg)
    return new_df.dropna()  # 沒有交易的週/月會是空的，要刪掉


def add_ma(df):
    # 計算題目要求的 MA5、MA10、MA20 均線（最近 N 根 K 棒收盤價的平均）
    for n in [5, 10, 20]:
        df[f"MA{n}"] = df["Close"].rolling(n).mean()
    return df


# -----------------------------------------------------
# 畫圖
# -----------------------------------------------------
def draw_chart(df, show_ma, period_name, vol_unit):
    # x 軸用文字日期，這樣假日不會在圖上留空白
    date_format = "%Y/%m" if period_name == "月線" else "%Y/%m/%d"
    x = df.index.strftime(date_format)

    # 上面是 K 線（佔 75%），下面是成交量（佔 25%），兩張圖共用 x 軸
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.03, row_heights=[0.75, 0.25])

    # K 線圖：increasing 是收盤比開盤高（紅），decreasing 是收盤比開盤低（綠）
    fig.add_trace(go.Candlestick(
        x=x, open=df["Open"], high=df["High"], low=df["Low"], close=df["Close"],
        increasing_line_color=UP_COLOR, increasing_fillcolor=UP_COLOR,
        decreasing_line_color=DOWN_COLOR, decreasing_fillcolor=DOWN_COLOR,
        name="K線",
    ), row=1, col=1)

    # 有勾選的均線才畫上去
    for n in [5, 10, 20]:
        if show_ma[n]:
            fig.add_trace(go.Scatter(
                x=x, y=df[f"MA{n}"], mode="lines", name=f"MA{n}",
                line=dict(color=MA_COLORS[n], width=1.5),
            ), row=1, col=1)

    # 成交量：跟 K 棒同樣的規則，收盤 >= 開盤是紅色，否則綠色
    vol_colors = [UP_COLOR if c >= o else DOWN_COLOR for o, c in zip(df["Open"], df["Close"])]
    fig.add_trace(go.Bar(x=x, y=df["Volume"], marker_color=vol_colors, name=f"成交量({vol_unit})"), row=2, col=1)

    fig.update_layout(
        height=650,
        xaxis_rangeslider_visible=False,  # 關掉 K 線預設的下方拉桿，畫面比較乾淨
        hovermode="x unified",            # 滑鼠移上去會顯示同一天的所有數值
        legend=dict(orientation="h", y=1.05, x=0),
        margin=dict(l=10, r=10, t=40, b=10),
        template="plotly_white",
    )
    fig.update_xaxes(type="category", nticks=10)
    fig.update_yaxes(title_text="股價", row=1, col=1)
    fig.update_yaxes(title_text=f"成交量({vol_unit})", row=2, col=1)
    return fig


# -----------------------------------------------------
# 網頁主畫面
# -----------------------------------------------------
st.title("📈 股票歷史 K 線圖")
st.caption("資料來源：Yahoo Finance｜台股顏色：紅漲綠跌")

# 上方控制列：股票代碼、週期、均線
col1, col2, col3 = st.columns([1.2, 1.5, 2])
with col1:
    stock_code = st.text_input("股票代碼", value="2330", help="台股輸入數字即可，例如 2330；美股輸入英文代碼，例如 AAPL")
with col2:
    period_name = st.radio("週期", ["日線", "週線", "月線"], horizontal=True)
with col3:
    st.write("均線")
    c5, c10, c20 = st.columns(3)
    show_ma = {
        5: c5.checkbox("MA5", value=True),
        10: c10.checkbox("MA10", value=True),
        20: c20.checkbox("MA20", value=True),
    }

if stock_code.strip() == "":
    st.info("請輸入股票代碼")
    st.stop()

# 依序試每個可能的代碼，抓到資料就停
df, symbol = None, None
setting = PERIOD_SETTINGS[period_name]
try:
    for s in to_yahoo_symbol(stock_code):
        data = get_daily_data(s, setting["fetch"])
        if not data.empty:
            df, symbol = data, s
            break
except Exception as e:
    st.error(f"資料下載失敗，請檢查網路連線後再試一次。（{e}）")
    st.stop()

if df is None:
    st.warning("找不到此股票代碼，請重新輸入")
    st.stop()

# 只留需要的欄位，並把時區拿掉（不然 resample 和日期顯示會有問題）
df = df[["Open", "High", "Low", "Close", "Volume"]]
df.index = df.index.tz_localize(None)

# 週線、月線要把日線資料重新整理
if period_name == "週線":
    df = resample_data(df, "W-FRI")  # 每週五當作一週的結束
elif period_name == "月線":
    try:
        df = resample_data(df, "ME")  # 新版 pandas 的月底寫法
    except ValueError:
        df = resample_data(df, "M")   # 舊版 pandas 用 "M"

# 先用全部資料算均線，再取最後幾根來畫圖
df = add_ma(df)
show_df = df.tail(setting["show"])

# 台股成交量單位換成「張」（1 張 = 1000 股）
is_tw = symbol.endswith(".TW") or symbol.endswith(".TWO")
vol_unit = "張" if is_tw else "股"
if is_tw:
    show_df = show_df.copy()
    show_df["Volume"] = show_df["Volume"] / 1000

# 顯示股票名稱和最新價格
name = get_stock_name(symbol)
st.subheader(f"{name}（{symbol}）")

last = show_df.iloc[-1]
prev = show_df.iloc[-2] if len(show_df) > 1 else last
change = last["Close"] - prev["Close"]
pct = change / prev["Close"] * 100 if prev["Close"] else 0

m1, m2, m3, m4 = st.columns(4)
# delta_color="inverse" 讓上漲變紅、下跌變綠，符合台股習慣
m1.metric("收盤價", f"{last['Close']:,.2f}", f"{change:+,.2f} ({pct:+.2f}%)", delta_color="inverse")
m2.metric("最高價", f"{last['High']:,.2f}")
m3.metric("最低價", f"{last['Low']:,.2f}")
m4.metric(f"成交量（{vol_unit}）", f"{last['Volume']:,.0f}")
st.caption(f"最新資料日期：{show_df.index[-1].strftime('%Y/%m/%d')}（{period_name}，顯示最近 {len(show_df)} 根 K 棒）")

# 畫 K 線圖
fig = draw_chart(show_df, show_ma, period_name, vol_unit)
st.plotly_chart(fig, use_container_width=True)

# 原始資料表（可以展開來看）
with st.expander("查看歷史資料"):
    table = show_df[["Open", "High", "Low", "Close", "Volume", "MA5", "MA10", "MA20"]].sort_index(ascending=False)
    table.index = table.index.strftime("%Y/%m/%d")
    table.columns = ["開盤", "最高", "最低", "收盤", f"成交量({vol_unit})", "MA5", "MA10", "MA20"]
    st.dataframe(table.round(2), use_container_width=True)
