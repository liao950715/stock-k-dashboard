# -*- coding: utf-8 -*-
# =====================================================================
# 理財機器人 期中報告：股票分析網頁
# ---------------------------------------------------------------------
# 資料來源：Yahoo Finance（yfinance）、證交所 / 櫃買中心 OpenAPI、Google 新聞 RSS
# 網頁框架：Streamlit          圖表套件：Plotly
#
# 功能：
#   1. 頂部導覽列：股票搜尋、深色 / 淺色主題切換
#   2. 資料跨度：1 天、5 天、10 天、20 天、1 個月 ～ 5 年
#   3. K 棒週期：5 分、15 分、60 分、日線、週線、月線（切換跨度時自動帶入合適週期，仍可手動改）
#   4. 均線：MA5、MA10、MA20、MA60（季線）、MA120（半年線）、MA240（年線）；布林通道
#   5. 副圖：成交量、MACD、KDJ
#   6. 相關新聞：Google 新聞 RSS + 證交所 / 櫃買中心每日重大訊息，關鍵字判斷偏多 / 偏空
#   7. 台股慣例：紅漲綠跌（同時用 ▲▼ 與正負號，不只靠顏色）
#
# 執行方式：streamlit run stock_dashboard.py
# =====================================================================

import html
import json
import time
import urllib.request

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import yfinance as yf
from plotly.subplots import make_subplots

# 新聞情緒量化分析 V2.1（自己寫的模組）
import ai_summary
import news_pipeline as npl
import news_sentiment as ns
import news_store

st.set_page_config(page_title="股票分析", page_icon="📈", layout="wide", initial_sidebar_state="collapsed")

# Streamlit 版本：有些參數（例如 vertical_alignment、Material 圖示）是新版才有，舊版要退回別的寫法
ST_VERSION = tuple(int(x) for x in st.__version__.split(".")[:2])

# ---------------------------------------------------------------------
# 一、配色與常數
# ---------------------------------------------------------------------
# 深色主題：主背景比圖表背景再暗一點，畫面才有層次，不會整頁同一種黑
# 淺色主題：另外定義一組可讀性好的顏色，不直接沿用深色的文字顏色
THEMES = {
    "dark": dict(
        bg="#0B0E14", chart="#141A23", toolbar="#171D27", border="#293241",
        text="#F1F4F8", muted="#8994A6", up="#DF737B", down="#45B18A", accent="#91B4E8",
        grid="rgba(137,148,166,0.12)", hover="#1C2330",
    ),
    "light": dict(
        bg="#F7F8FA", chart="#FFFFFF", toolbar="#EEF1F5", border="#DDE2E9",
        text="#161B26", muted="#5F6B7C", up="#D0434F", down="#16875F", accent="#2F64C8",
        grid="rgba(95,107,124,0.12)", hover="#E7ECF3",
    ),
}

# 每條均線的顏色（不刺眼、深淺兩種背景都看得清楚）
MA_COLORS = {
    5: "#E2B04A",    # MA5   黃
    10: "#E08A4F",   # MA10  橙
    20: "#6FA0E6",   # MA20  藍
    60: "#3FB5A0",   # MA60  青綠（季線）
    120: "#A08BE0",  # MA120 紫（半年線）
    240: "#8A94A6",  # MA240 灰（年線）
}
MA_NAMES = {5: "MA5", 10: "MA10", 20: "MA20", 60: "MA60 季線", 120: "MA120 半年線", 240: "MA240 年線"}

# K 棒週期：
#   yf         ：丟給 yfinance 的參數
#   intraday   ：是不是分 K
#   max_days   ：Yahoo 對分 K 的限制（5 / 15 分 K 只有最近 60 天，60 分 K 約 730 天）
#   warmup_days：分 K 多抓幾天當暖機資料（讓 MA、MACD 從畫面最左邊就有值）
#   resample   ：週線、月線不直接跟 Yahoo 要，而是自己用日線 resample（原因寫在 resample_ohlcv 裡）
INTERVALS = {
    "5 分":  {"yf": "5m",  "intraday": True,  "max_days": 59,  "warmup_days": 10, "resample": None},
    "15 分": {"yf": "15m", "intraday": True,  "max_days": 59,  "warmup_days": 20, "resample": None},
    "60 分": {"yf": "60m", "intraday": True,  "max_days": 729, "warmup_days": 80, "resample": None},
    "日線":  {"yf": "1d",  "intraday": False, "resample": None},
    "週線":  {"yf": "1d",  "intraday": False, "resample": "W"},
    "月線":  {"yf": "1d",  "intraday": False, "resample": "M"},
}

# 資料跨度：("d", N) = 最近 N 個交易日；("m", N) = 最近 N 個月
SPANS = {
    "1 天": ("d", 1), "5 天": ("d", 5), "10 天": ("d", 10), "20 天": ("d", 20),
    "1 個月": ("m", 1), "3 個月": ("m", 3), "6 個月": ("m", 6), "1 年": ("m", 12),
    "2 年": ("m", 24), "3 年": ("m", 36), "5 年": ("m", 60),
}
# 切換跨度時自動帶入的 K 棒週期（像 Yahoo 的 1 日 / 5 日走勢會用分 K）；使用者之後仍可自己改
DEFAULT_INTERVAL = {"1 天": "5 分", "5 天": "15 分", "10 天": "60 分", "20 天": "60 分"}

# 週線、月線如果跨度太短，K 棒會少到看不出東西，所以設最短跨度（月）
MIN_MONTHS = {"週線": 3, "月線": 12}

# 日 / 週 / 月線的暖機資料（天）：例如日線 MA240 需要前面 240 個交易日
WARMUP_DAYS = {"日線": 400, "週線": 240 * 7 + 30, "月線": 240 * 31 + 60}


def build_css(t):
    """
    依照主題顏色產生整個網頁的 CSS。
    Streamlit 沒辦法在執行中切換主題，所以用 CSS 把背景、文字、輸入框、分頁等顏色蓋過去。
    有些元件新舊版 Streamlit 的 HTML 結構不同，所以會看到兩種寫法並列。
    """
    search_icon = (
        "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='16' height='16' viewBox='0 0 24 24' "
        "fill='none' stroke='%23" + t["muted"].lstrip("#") + "' stroke-width='2' stroke-linecap='round'>"
        "<circle cx='11' cy='11' r='7'/><line x1='16.5' y1='16.5' x2='21' y2='21'/></svg>"
    )
    css = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=Noto+Sans+TC:wght@400;500;600&display=swap');

/* ---------- 全站：字體、背景、內容寬度 ---------- */
html, body, .stApp { font-family: 'Inter', 'Noto Sans TC', -apple-system, 'Segoe UI', 'Microsoft JhengHei', sans-serif; }
.stApp, [data-testid="stHeader"] { background: __bg__ !important; }
.stApp, .stApp p, .stApp label, .stApp li, [data-testid="stMarkdownContainer"] { color: __text__; }
[data-testid="stCaptionContainer"], [data-testid="stCaptionContainer"] * { color: __muted__ !important; }
[data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"], [data-testid="collapsedControl"] { display: none !important; }
/* 上方留 56px 給 Streamlit 自己的工具列（Deploy / 選單），導覽列才不會被蓋住 */
.block-container, [data-testid="stMainBlockContainer"] { max-width: 1440px; padding: 56px 48px 48px 48px !important; }
@media (max-width: 1100px) { .block-container, [data-testid="stMainBlockContainer"] { padding: 56px 20px 40px 20px !important; } }
[data-testid="stVerticalBlock"] { gap: 0.75rem; }
/* 只用來給 CSS 辨識區塊的「記號」元素，本身不佔版面 */
[data-testid="stElementContainer"]:has(.qk-mark), .element-container:has(.qk-mark) { display: none !important; }
.qk-num { font-variant-numeric: tabular-nums; }

/* ---------- 輸入框、下拉選單、按鈕：統一高度 38px、圓角 8px ---------- */
.stApp input { color: __text__ !important; }
[data-testid="stTextInputRootElement"], div[data-baseweb="input"], div[data-baseweb="base-input"],
div[data-baseweb="select"] > div, [data-testid="stSelectbox"] [role="group"] {
    background: __toolbar__ !important; border: 1px solid __border__ !important; border-radius: 8px !important;
    min-height: 38px !important; color: __text__ !important; box-shadow: none !important; }
div[data-baseweb="input"] > div, div[data-baseweb="base-input"] > input, div[data-baseweb="select"] div,
[data-testid="stSelectbox"] [role="group"] > *:not(svg) { background: transparent !important; color: __text__ !important; }
[data-testid="stTextInputRootElement"]:focus-within, div[data-baseweb="input"]:focus-within,
[data-testid="stSelectbox"] [role="group"]:focus-within { border-color: __accent__ !important; }
div[data-baseweb="popover"] ul, div[data-baseweb="popover"] li, [role="listbox"], [role="listbox"] [role="option"] {
    background: __toolbar__ !important; color: __text__ !important; }
[role="listbox"] [role="option"]:hover, div[data-baseweb="popover"] li:hover { background: __hover__ !important; }
[data-testid="stPopoverBody"], div[data-baseweb="popover"] > div { background: __toolbar__ !important; border-color: __border__ !important; }
.stButton button, [data-testid="stPopover"] button, .stDownloadButton button {
    min-height: 38px; border-radius: 8px; background: __toolbar__; color: __text__; border: 1px solid __border__; box-shadow: none; }
.stButton button:hover, [data-testid="stPopover"] button:hover, .stDownloadButton button:hover {
    border-color: __accent__; color: __accent__; background: __hover__; }
.stButton button p, [data-testid="stPopover"] button p, .stDownloadButton button p { color: inherit !important; font-size: 13px; }
/* 沒勾選的核取方塊 / 單選圓點：底色跟著主題 */
[data-testid="stCheckbox"]:not(:has(input:checked)) label div:not([data-testid]),
label[data-baseweb="checkbox"]:not(:has(input:checked)) > span { background: __toolbar__ !important; border-color: __muted__ !important; }

/* ---------- 頂部導覽列 ---------- */
div[data-testid="stHorizontalBlock"]:has(.qk-nav) { align-items: center; padding: 6px 0 10px 0;
    border-bottom: 1px solid __border__; margin-bottom: 8px; }
.qk-brand { display: flex; align-items: center; gap: 10px; font-size: 18px; font-weight: 600; color: __text__; height: 40px; }
.qk-brand svg { stroke: __accent__; }
div[data-testid="stHorizontalBlock"]:has(.qk-nav) input {
    padding-left: 36px !important; background: url("__search_icon__") no-repeat 12px center !important; font-size: 14px; }
div[data-testid="stHorizontalBlock"]:has(.qk-nav) [data-testid="stCheckbox"],
div[data-testid="stHorizontalBlock"]:has(.qk-nav) .stCheckbox { display: flex; justify-content: flex-end; }
div[data-testid="stHorizontalBlock"]:has(.qk-nav) [data-testid="stWidgetLabel"] *,
div[data-testid="stHorizontalBlock"]:has(.qk-nav) label p { color: __muted__ !important; font-size: 18px; }

/* ---------- 股票名稱與價格 ---------- */
.qk-head { margin: 16px 0 0 0; }
.qk-name { font-size: 26px; font-weight: 600; color: __text__; line-height: 1.3; }
.qk-code { font-size: 14px; font-weight: 400; color: __muted__; margin-left: 10px; }
.qk-price-row { display: flex; align-items: baseline; gap: 14px; margin-top: 8px; flex-wrap: wrap; }
.qk-price { font-size: 42px; font-weight: 600; color: __text__; font-variant-numeric: tabular-nums; line-height: 1.1; }
.qk-chg { font-size: 17px; font-weight: 500; font-variant-numeric: tabular-nums; }
.qk-up { color: __up__ !important; }
.qk-down { color: __down__ !important; }
.qk-flat { color: __muted__ !important; }
.qk-meta { font-size: 13px; color: __muted__; margin-top: 6px; }
.qk-stats { display: flex; flex-wrap: wrap; gap: 8px 40px; margin-top: 20px; }
.qk-stat label { display: block; font-size: 12px; color: __muted__; margin-bottom: 2px; }
.qk-stat b { font-size: 17px; font-weight: 500; color: __text__; font-variant-numeric: tabular-nums; }

/* ---------- 頁面分頁（用 radio 做成文字分頁，底線標示目前頁面） ---------- */
div[data-testid="stHorizontalBlock"]:has(.qk-pagenav) { border-bottom: 1px solid __border__; margin-top: 20px; }
div[data-testid="stHorizontalBlock"]:has(.qk-pagenav) [role="radiogroup"] { gap: 28px !important; flex-wrap: nowrap; }
div[data-testid="stHorizontalBlock"]:has(.qk-pagenav) [role="radiogroup"] > div { display: flex; gap: 28px !important; }
div[data-testid="stHorizontalBlock"]:has(.qk-pagenav) [role="radiogroup"] label {
    margin: 0 !important; padding: 8px 0 10px 0; border-bottom: 2px solid transparent; cursor: pointer; }
div[data-testid="stHorizontalBlock"]:has(.qk-pagenav) [role="radiogroup"] label p { font-size: 14px; color: __muted__ !important; }
div[data-testid="stHorizontalBlock"]:has(.qk-pagenav) [role="radiogroup"] label:has(input:checked) { border-bottom-color: __accent__; }
div[data-testid="stHorizontalBlock"]:has(.qk-pagenav) [role="radiogroup"] label:has(input:checked) p { color: __text__ !important; font-weight: 500; }

/* ---------- 圖表工具列：資料跨度做成一排小按鈕 ---------- */
div[data-testid="stHorizontalBlock"]:has(.qk-toolbar) { align-items: center; margin-top: 8px; }
div[data-testid="stHorizontalBlock"]:has(.qk-toolbar) [role="radiogroup"] {
    background: __toolbar__; border: 1px solid __border__; border-radius: 8px; padding: 3px; gap: 0 !important;
    display: inline-flex; flex-wrap: nowrap; max-width: 100%; overflow-x: auto; scrollbar-width: none; }
div[data-testid="stHorizontalBlock"]:has(.qk-toolbar) [role="radiogroup"] > div { display: flex; flex-wrap: nowrap; gap: 0 !important; }
div[data-testid="stHorizontalBlock"]:has(.qk-toolbar) [role="radiogroup"] label {
    margin: 0 !important; padding: 0 11px; min-height: 30px; border-radius: 6px; display: flex; align-items: center; cursor: pointer; flex: 0 0 auto; }
div[data-testid="stHorizontalBlock"]:has(.qk-toolbar) [role="radiogroup"] label p { font-size: 13px; color: __muted__ !important; white-space: nowrap; }
div[data-testid="stHorizontalBlock"]:has(.qk-toolbar) [role="radiogroup"] label:hover { background: __hover__; }
div[data-testid="stHorizontalBlock"]:has(.qk-toolbar) [role="radiogroup"] label:has(input:checked) { background: __hover__; }
div[data-testid="stHorizontalBlock"]:has(.qk-toolbar) [role="radiogroup"] label:has(input:checked) p { color: __text__ !important; font-weight: 500; }
/* 兩種 radio 都把圓點藏起來（舊版：label 的第一個 div；新版：label > div > 第一個 div） */
div[data-testid="stHorizontalBlock"]:has(.qk-toolbar) [role="radiogroup"] label > div:first-child:not(:has([data-testid="stMarkdownContainer"])),
div[data-testid="stHorizontalBlock"]:has(.qk-toolbar) [role="radiogroup"] label > div > div:first-child:not([data-testid="stMarkdownContainer"]),
div[data-testid="stHorizontalBlock"]:has(.qk-pagenav) [role="radiogroup"] label > div:first-child:not(:has([data-testid="stMarkdownContainer"])),
div[data-testid="stHorizontalBlock"]:has(.qk-pagenav) [role="radiogroup"] label > div > div:first-child:not([data-testid="stMarkdownContainer"]) {
    display: none !important; }

/* 較窄的螢幕：資料跨度自己一排，三個選單排在下一排平分寬度，才不會被擠到看不到字 */
@media (max-width: 1100px) {
  div[data-testid="stHorizontalBlock"]:has(.qk-toolbar) { flex-wrap: wrap !important; row-gap: 8px; }
  div[data-testid="stHorizontalBlock"]:has(.qk-toolbar) > div:first-child { flex: 1 1 100% !important; min-width: 100% !important; }
  div[data-testid="stHorizontalBlock"]:has(.qk-toolbar) > div:not(:first-child) { flex: 1 1 0 !important; min-width: 0 !important; }
}

/* ---------- 圖表：比主背景亮一點的底色、圓角、無外框無陰影 ---------- */
[data-testid="stPlotlyChart"] { background: __chart__; border-radius: 10px; overflow: hidden; padding: 4px 2px 0 2px; }

/* ---------- 新聞 ---------- */
.qk-section { display: flex; justify-content: space-between; align-items: baseline; margin-top: 28px; }
.qk-section-title { font-size: 16px; font-weight: 600; color: __text__; }
/* 標題和右側小選單 / 按鈕放在同一排時：外距加在整排上，標題本身不再往下推，兩者才會對齊 */
div[data-testid="stHorizontalBlock"]:has(.qk-section) { margin-top: 24px; align-items: center !important; }
div[data-testid="stHorizontalBlock"] .qk-section { margin-top: 0; }
.qk-section-title span { font-size: 13px; font-weight: 400; color: __muted__; margin-left: 8px; }
.qk-news { max-width: 1080px; }
.qk-item { display: flex; justify-content: space-between; gap: 16px; padding: 18px 0; border-bottom: 1px solid __border__; }
.qk-item a, .qk-item-title { font-size: 15px; font-weight: 500; color: __text__ !important; text-decoration: none; line-height: 1.5; }
.qk-item a:hover { color: __accent__ !important; }
.qk-item-meta { font-size: 12px; color: __muted__; margin-top: 4px; }
.qk-tag { flex: 0 0 auto; font-size: 12px; padding: 1px 8px; border-radius: 4px; border: 1px solid; height: fit-content; white-space: nowrap; }
.qk-tag.qk-up { border-color: __up__; } .qk-tag.qk-down { border-color: __down__; } .qk-tag.qk-flat { border-color: __border__; }
details.qk-detail { border-bottom: 1px solid __border__; }
details.qk-detail summary { list-style: none; cursor: pointer; }
details.qk-detail summary::-webkit-details-marker { display: none; }
details.qk-detail summary .qk-item { border-bottom: none; }
details.qk-detail summary:hover .qk-item-title { color: __accent__ !important; }
.qk-detail-body { padding: 0 0 18px 0; font-size: 13px; color: __muted__; line-height: 1.8; }
.qk-detail-body a { color: __accent__ !important; }
.qk-overview { display: flex; flex-wrap: wrap; gap: 12px 48px; align-items: flex-end; padding: 8px 0 4px 0; }
.qk-score { font-size: 42px; font-weight: 600; font-variant-numeric: tabular-nums; line-height: 1.1; }
.qk-ov label { display: block; font-size: 12px; color: __muted__; margin-bottom: 4px; }
.qk-ov b { font-size: 17px; font-weight: 500; color: __text__; }
.qk-bar { display: flex; height: 8px; border-radius: 4px; overflow: hidden; margin-top: 20px; background: __border__; }
.qk-bar-legend { display: flex; gap: 24px; font-size: 12px; color: __muted__; margin-top: 8px; flex-wrap: wrap; }
.qk-empty { background: __chart__; border-radius: 10px; padding: 36px 24px; text-align: center; color: __muted__; font-size: 13px; line-height: 1.8; }
.qk-panel { border-top: 1px solid __border__; padding-top: 16px; }
.qk-panel-head { display: flex; justify-content: space-between; align-items: baseline; }
.qk-note { font-size: 12px; color: __muted__; }

/* ---------- Expander、資料表、提示框 ---------- */
[data-testid="stExpander"] { background: transparent !important; border: 1px solid __border__ !important; border-radius: 8px; }
[data-testid="stExpander"] summary p, [data-testid="stExpander"] summary span { color: __text__ !important; }
[data-testid="stAlert"] { border-radius: 8px; }
/* 「查看全部」這種文字按鈕：不要外框和底色，只用藍色文字 */
.stButton button[kind="tertiary"], .stButton button[data-testid="stBaseButton-tertiary"] {
    background: transparent !important; border: none !important; color: __accent__ !important; min-height: 30px; padding: 0; }
.stButton button[kind="tertiary"] p { color: __accent__ !important; }
div[data-testid="stHorizontalBlock"]:has(.qk-section) .stButton { display: flex; justify-content: flex-end; }
</style>
"""
    for key, value in t.items():
        css = css.replace(f"__{key}__", value)
    return css.replace("__search_icon__", search_icon)


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
    日 K 用 start（從某天抓到今天）；分 K 用 period（例如 "30d" = 最近 30 天）。
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
# 二之一、新聞與重大訊息
#   流程：抓取 → 整理欄位 → 去除重複 → 依時間排序 → 關鍵字判斷偏多 / 偏空 → 顯示
#   每則新聞都保留：原始標題、來源、發布時間、原文連結，方便回去查證
# ---------------------------------------------------------------------
# 關鍵字判斷用的字詞（很簡單的規則，只是幫忙快速掃過標題，不代表真正的影響）
POSITIVE_WORDS = ["大漲", "上漲", "漲停", "創高", "新高", "成長", "增長", "優於預期", "調升", "上修", "買超",
                  "獲利", "利多", "看好", "強勢", "突破", "反彈", "回升", "攀升", "加碼", "雙增"]
NEGATIVE_WORDS = ["大跌", "下跌", "跌停", "重挫", "下滑", "衰退", "減少", "不如預期", "調降", "下修", "賣超",
                  "虧損", "利空", "看淡", "疲弱", "跌破", "崩跌", "走弱", "減碼", "雙減", "警示"]


def news_tone(title):
    """
    數標題裡出現幾個「偏多」和「偏空」的關鍵字，多的那一邊就是這則新聞的傾向。
    先把「漲跌」「止跌」這種不代表方向的詞拿掉，避免誤判。
    """
    text = title.replace("漲跌", "").replace("止跌", "")
    pos = sum(text.count(w) for w in POSITIVE_WORDS)
    neg = sum(text.count(w) for w in NEGATIVE_WORDS)
    if pos > neg:
        return "偏多"
    if neg > pos:
        return "偏空"
    return "中性"


@st.cache_data(ttl=1800, show_spinner="正在搜尋相關新聞…")
def fetch_news(query, limit=20):
    """
    用 Google 新聞的 RSS（公開的新聞搜尋訂閱格式）搜尋這檔股票的新聞。
    RSS 是 XML 格式，每則新聞是一個 <item>，裡面有 title、link、pubDate、source。
    """
    import email.utils
    import urllib.parse
    import xml.etree.ElementTree as ET

    url = ("https://news.google.com/rss/search?q=" + urllib.parse.quote(query)
           + "&hl=zh-TW&gl=TW&ceid=TW:zh-Hant")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        root = ET.fromstring(resp.read())

    news, seen = [], set()
    for item in root.findall(".//item"):
        title = item.findtext("title", "").strip()
        source = item.findtext("source", "").strip()
        # Google 新聞的標題後面會接「 - 來源名稱」，把它拿掉比較乾淨
        if source and title.endswith(" - " + source):
            title = title[: -len(" - " + source)]
        # 去重：不同網站轉載同一則新聞時標題幾乎一樣，用「去掉空白後的標題」當作判斷依據
        key = title.replace(" ", "")
        if not title or key in seen:
            continue
        seen.add(key)
        # 發布時間是英文格式（例如 Mon, 09 Nov 2026 02:15:00 GMT），轉成台灣時間
        try:
            published = pd.Timestamp(email.utils.parsedate_to_datetime(item.findtext("pubDate"))).tz_convert("Asia/Taipei")
        except Exception:
            published = None
        news.append({"title": title, "link": item.findtext("link", ""), "source": source,
                     "published": published, "tone": news_tone(title)})

    # 依發布時間由新到舊排序（沒有時間的排最後）
    news.sort(key=lambda n: n["published"] or pd.Timestamp("1900-01-01", tz="Asia/Taipei"), reverse=True)
    return news[:limit]


def roc_to_date(text):
    """民國日期轉西元：例如 1151108 → 2026/11/08"""
    text = str(text).strip()
    if len(text) < 7:
        return text
    return f"{int(text[:-4]) + 1911}/{text[-4:-2]}/{text[-2:]}"


@st.cache_data(ttl=1800, show_spinner="正在讀取公司重大訊息…")
def fetch_material_info():
    """
    抓證交所（上市）與櫃買中心（上櫃）OpenAPI 的「每日重大訊息」，也就是公開資訊觀測站的公告。
    這兩個 API 只提供「當天」的公告，所以大部分的股票當天不會有資料。
    兩邊的欄位名稱不太一樣（上櫃用英文、上市用中文，而且「主旨」後面還多一個空白），所以先統一。
    """
    sources = [
        "https://openapi.twse.com.tw/v1/opendata/t187ap04_L",
        "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap04_O",
    ]
    result = []
    for url in sources:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                rows = json.loads(resp.read().decode("utf-8"))
        except Exception:
            continue
        for row in rows:
            row = {k.strip(): v for k, v in row.items()}   # 把欄位名稱前後的空白拿掉
            result.append({
                "code": str(row.get("公司代號") or row.get("SecuritiesCompanyCode") or "").strip(),
                "date": roc_to_date(row.get("發言日期", "")),
                "subject": str(row.get("主旨", "")).replace("\r\n", ""),
                "date_raw": str(row.get("發言日期", "")).strip(),
                "time_raw": str(row.get("發言時間", "")).strip(),
                "detail": str(row.get("說明", "")),
            })
    return result


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


def add_bollinger(df, n=20, k=2):
    """
    布林通道（Bollinger Bands，參數 20, 2）。

    計算步驟：
      1. 中軌 = 最近 20 根收盤價的平均，也就是 MA20
      2. 標準差 σ = 最近 20 根收盤價的標準差，用 rolling(20).std(ddof=0)
         ddof=0 是「母體標準差」（除以 n），台灣券商軟體的布林通道大多用這種算法；
         pandas 預設 ddof=1 是「樣本標準差」（除以 n−1），算出來的通道會稍微寬一點
      3. 上軌 = 中軌 + 2σ，下軌 = 中軌 − 2σ

    意義：如果收盤價是常態分布，大約 95% 的時間會落在上下軌之間；
    通道變窄代表波動變小（常被視為大行情前的盤整），價格碰到上軌或下軌代表相對偏高或偏低。
    """
    mid = df["Close"].rolling(window=n).mean()
    std = df["Close"].rolling(window=n).std(ddof=0)
    df["BB_MID"] = mid
    df["BB_UP"] = mid + k * std
    df["BB_LOW"] = mid - k * std
    return df


# ---------------------------------------------------------------------
# 五、畫圖
# ---------------------------------------------------------------------
def draw_chart(df, show_ma, show_bb, show_vol, sub_ind, x_format, vol_unit, t):
    """
    主圖：K 線 + 均線（+ 布林通道），約 490px 高
    副圖：成交量（約 130px，可關掉）、MACD / KDJ（約 150px，可不顯示）
    全部用 make_subplots(shared_xaxes=True) 共用同一條時間軸，縮放、平移時會一起動。
    t 是目前主題的顏色（深色 / 淺色），漲跌顏色也跟著主題，但一樣是紅漲綠跌。
    """
    up, down = t["up"], t["down"]
    # x 軸用文字（類別軸），這樣週末、假日、收盤後的時間不會在圖上留下空白
    x = df.index.strftime(x_format)

    # 依照要顯示的副圖決定有幾列、每列多高（像素）
    rows = [("price", 490)]
    if show_vol:
        rows.append(("vol", 130))
    if sub_ind != "無":
        rows.append(("ind", 150))
    heights = [h for _, h in rows]
    row_of = {name: i + 1 for i, (name, _) in enumerate(rows)}

    fig = make_subplots(rows=len(rows), cols=1, shared_xaxes=True, vertical_spacing=0.025,
                        row_heights=[h / sum(heights) for h in heights])

    # --- K 線：收盤 >= 開盤用紅色（漲），收盤 < 開盤用綠色（跌） ---
    fig.add_trace(go.Candlestick(
        x=x, open=df["Open"], high=df["High"], low=df["Low"], close=df["Close"],
        increasing_line_color=up, increasing_fillcolor=up,
        decreasing_line_color=down, decreasing_fillcolor=down,
        name="K線",
    ), row=1, col=1)

    # --- 布林通道：上下軌虛線，中間填很淡的顏色 ---
    if show_bb:
        fig.add_trace(go.Scatter(x=x, y=df["BB_UP"], mode="lines", name="布林上軌",
                                 line=dict(color=t["muted"], width=1, dash="dot")), row=1, col=1)
        fig.add_trace(go.Scatter(x=x, y=df["BB_LOW"], mode="lines", name="布林下軌",
                                 line=dict(color=t["muted"], width=1, dash="dot"),
                                 fill="tonexty", fillcolor="rgba(137,148,166,0.08)"), row=1, col=1)

    # --- 均線：只畫有勾選、而且有算出數值的 ---
    for n, checked in show_ma.items():
        if checked and df[f"MA{n}"].notna().any():
            fig.add_trace(go.Scatter(x=x, y=df[f"MA{n}"], mode="lines", name=f"MA{n}",
                                     line=dict(color=MA_COLORS[n], width=1.4)), row=1, col=1)

    # --- 成交量：跟 K 棒同一套顏色規則 ---
    if show_vol:
        colors = [up if c >= o else down for o, c in zip(df["Open"], df["Close"])]
        fig.add_trace(go.Bar(x=x, y=df["Volume"], marker_color=colors, marker_line_width=0,
                             name=f"成交量({vol_unit})", opacity=0.8), row=row_of["vol"], col=1)
        fig.update_yaxes(title_text=f"量({vol_unit})", row=row_of["vol"], col=1)

    # --- 技術指標副圖 ---
    if sub_ind == "MACD":
        r = row_of["ind"]
        osc_colors = [up if v >= 0 else down for v in df["OSC"].fillna(0)]
        fig.add_trace(go.Bar(x=x, y=df["OSC"], marker_color=osc_colors, marker_line_width=0, name="OSC"), row=r, col=1)
        fig.add_trace(go.Scatter(x=x, y=df["DIF"], mode="lines", name="DIF", line=dict(color=MA_COLORS[5], width=1.2)), row=r, col=1)
        fig.add_trace(go.Scatter(x=x, y=df["DEA"], mode="lines", name="DEA", line=dict(color=MA_COLORS[20], width=1.2)), row=r, col=1)
        fig.update_yaxes(title_text="MACD", row=r, col=1)
    elif sub_ind == "KDJ":
        r = row_of["ind"]
        fig.add_trace(go.Scatter(x=x, y=df["K"], mode="lines", name="K", line=dict(color=MA_COLORS[5], width=1.2)), row=r, col=1)
        fig.add_trace(go.Scatter(x=x, y=df["D"], mode="lines", name="D", line=dict(color=MA_COLORS[20], width=1.2)), row=r, col=1)
        fig.add_trace(go.Scatter(x=x, y=df["J"], mode="lines", name="J", line=dict(color=MA_COLORS[120], width=1)), row=r, col=1)
        fig.add_hline(y=80, line_dash="dot", line_color=up, line_width=1, row=r, col=1)
        fig.add_hline(y=20, line_dash="dot", line_color=down, line_width=1, row=r, col=1)
        fig.update_yaxes(title_text="KDJ", row=r, col=1)

    fig.update_layout(
        height=sum(heights) + 60,
        paper_bgcolor=t["chart"], plot_bgcolor=t["chart"],
        font=dict(family="Inter, Noto Sans TC, sans-serif", color=t["muted"], size=12),
        xaxis_rangeslider_visible=False,      # 關掉 K 線預設的拉桿
        hovermode="x unified",                # 游標移到某根 K 棒，所有數值（OHLC、均線、量）一起顯示
        hoverlabel=dict(bgcolor=t["toolbar"], bordercolor=t["border"], font=dict(color=t["text"])),
        legend=dict(orientation="h", yanchor="bottom", y=1.01, x=0, font=dict(size=11, color=t["muted"]),
                    bgcolor="rgba(0,0,0,0)"),
        margin=dict(l=12, r=12, t=36, b=8),
        dragmode="zoom",
    )
    fig.update_xaxes(type="category", nticks=10, showgrid=False, linecolor=t["border"],
                     showspikes=True, spikemode="across", spikethickness=1, spikecolor=t["muted"], spikedash="dot")
    # automargin：讓右側的價格刻度自動保留空間，數字才不會被切掉
    fig.update_yaxes(gridcolor=t["grid"], zeroline=False, linecolor=t["border"], side="right", automargin=True)
    return fig


# ---------------------------------------------------------------------
# 六、新聞情緒 V2.1：資料、計算與顯示用小工具
#   計算本身都在 news_sentiment.py / news_pipeline.py，這裡只負責「接上資料來源」與「畫成 HTML」
# ---------------------------------------------------------------------
@st.cache_data(ttl=1800, show_spinner=False)
def get_news(query):
    """呼叫 fetch_news 抓新聞，並記錄抓取的時間（顯示「資料更新時間」用）"""
    return fetch_news(query), pd.Timestamp.now(tz="Asia/Taipei").strftime("%Y/%m/%d %H:%M")


@st.cache_resource(show_spinner=False)
def get_store():
    """SQLite 儲存（所有使用者共用同一個連線，寫入有鎖保護）"""
    return news_store.NewsStore()


@st.cache_data(ttl=86400, show_spinner=False)
def get_holidays():
    """證交所休市日；抓不到時回傳空集合（未來日期就只用「週一到週五」判斷）"""
    try:
        return frozenset(npl.fetch_twse_holidays()), True
    except Exception:
        return frozenset(), False


@st.cache_data(ttl=3600, show_spinner=False)
def calendar_days(is_tw):
    """交易日曆：用大盤指數（台股 ^TWII、美股 ^GSPC）最近半年的日 K 日期當作實際交易日"""
    try:
        idx = yf.Ticker("^TWII" if is_tw else "^GSPC").history(period="6mo", interval="1d").index
        return [d.date() for d in idx]
    except Exception:
        return []


def get_summarizer():
    """
    AI 摘要：讀 Streamlit Secrets（或環境變數）裡的金鑰
      OPENROUTER_API_KEY → 透過 OpenRouter 呼叫 Claude（優先）
      ANTHROPIC_API_KEY  → 直接呼叫 Anthropic Claude API
    都沒有就不啟用，畫面顯示規則式摘要。
    """
    def secret(name):
        try:
            return st.secrets.get(name)
        except Exception:
            return None
    return ai_summary.pick_summarizer(secret("OPENROUTER_API_KEY"), secret("ANTHROPIC_API_KEY"))


def news_analysis(symbol, code, name, is_tw, query):
    """抓新聞 → 跑 V2.1 流程（存檔、分類、事件、NSI、快照），回傳畫面需要的所有東西"""
    fetch_error = ""
    try:
        news_list, news_time = get_news(query)
    except Exception as e:
        news_list, news_time, fetch_error = None, "", str(e)
    material = []
    if is_tw:
        try:
            material = [a for a in fetch_material_info() if a["code"] == code]
        except Exception:
            material = []
    holidays, _ = get_holidays() if is_tw else (frozenset(), False)
    cal = npl.build_calendar(calendar_days(is_tw), is_tw, holidays)
    names = [str(name)] + ([symbol] if not is_tw else [])
    out = npl.run_analysis(get_store(), symbol, code, names, news_list, material, cal, fetch_error=fetch_error)
    out.update({"news_list": news_list, "news_time": news_time, "calendar": cal, "fetch_error": fetch_error,
                "material": material})
    # news_id → 分類後的新聞、所屬事件、權重
    out["item_by_id"] = {it.news_id: it for it in out["history_items"]}
    out["event_by_news"] = {}
    for row in out["result"]["rows"]:
        for it in row["event"].items:
            out["event_by_news"][it.news_id] = row
    return out


LABEL_CLASS = {"強烈偏多": "qk-up", "偏多": "qk-up", "中性": "qk-flat", "偏空": "qk-down", "強烈偏空": "qk-down"}
ALERT_ICON = {"重大利多": "▲", "重大利空": "▼", "未確認重大消息": "?", "來源矛盾": "≠",
              "已否認": "✕", "已更正或撤回": "↺", "多空高度分歧": "⇅"}


def news_row(n, info, with_detail=False):
    """
    一則新聞的 HTML：左邊標題與來源時間，右邊小標籤（五級情緒）。
    with_detail=True 時可以點開看：判斷依據、事件類型、重要性、相關性、確認狀態、權重、原文連結。
    """
    nid = ns.make_news_id(n.get("link", ""), n.get("source", ""), n["title"])
    it = info["item_by_id"].get(nid)
    row = info["event_by_news"].get(nid)
    label = it.label if it else "中性"
    title = html.escape(n["title"])
    link = html.escape(n["link"], quote=True)
    when = n["published"].strftime("%Y/%m/%d %H:%M") if n["published"] is not None else ""
    meta = f'{html.escape(n["source"])}　{when}'
    tag = f'<span class="qk-tag {LABEL_CLASS[label]}">{label}</span>'
    if not with_detail:
        return (f'<div class="qk-item"><div><a href="{link}" target="_blank">{title}</a>'
                f'<div class="qk-item-meta">{meta}</div></div>{tag}</div>')
    lines = []
    if it:
        lines.append(f"判斷依據：{html.escape(it.reason)}")
        lines.append(f"事件類型：{html.escape(it.event_type)}（重要性等級 {it.importance}）　·　個股相關性 R = {it.relevance:.2f}")
    if row:
        ev = row["event"]
        same = f"，同一事件共 {len(ev.items)} 篇報導" if len(ev.items) > 1 else ""
        lines.append(f"事件 {ev.event_id}（第 {ev.version} 版）　·　確認狀態：{html.escape(ev.status)}{same}")
        if row["eligible"]:
            lines.append(f"納入 NSI：權重 W = {row.get('W', 0):.2f}"
                         f"（I {row['I']} × R′ {row['R_prime']} × T {row['T']:.2f} × U {row['U']:.0f}，經過 {row['t']} 個交易日）")
        else:
            lines.append(f"未納入 NSI：{html.escape(row['excluded_reason'])}")
    lines.append(f'<a href="{link}" target="_blank">開啟原文 ↗</a>')
    return (f'<details class="qk-detail"><summary><div class="qk-item"><div><div class="qk-item-title">{title}</div>'
            f'<div class="qk-item-meta">{meta}</div></div>{tag}</div></summary>'
            f'<div class="qk-detail-body">{"<br>".join(lines)}</div></details>')


@st.cache_data(ttl=86400, show_spinner=False)
def get_currency(symbol):
    """海外股票的幣別（台股固定 TWD）"""
    if symbol.endswith((".TW", ".TWO")):
        return "TWD"
    try:
        return yf.Ticker(symbol).fast_info["currency"] or ""
    except Exception:
        return ""


def go_to_news():
    """「查看全部」按鈕：切換到相關新聞分頁"""
    st.session_state.page = "相關新聞"


def on_span_change():
    """切換資料跨度時，自動帶入合適的 K 棒週期（使用者之後仍然可以自己改）"""
    st.session_state.interval = DEFAULT_INTERVAL.get(st.session_state.span, "日線")


# =====================================================================
# 七、網頁畫面
# =====================================================================
# --- 所有設定的預設值（存在 session_state，重新整理畫面時不會跑掉） ---
DEFAULTS = {"ticker": "2330", "dark": True, "span": "1 年", "interval": "日線", "show_vol": True,
            "sub_ind": "無", "show_bb": False, "page": "走勢圖", "news_filter": "全部新聞"}
DEFAULTS.update({f"ma{n}": n in (5, 10, 20) for n in MA_COLORS})
for k, v in DEFAULTS.items():
    st.session_state.setdefault(k, v)

theme = THEMES["dark" if st.session_state.dark else "light"]
st.markdown(build_css(theme), unsafe_allow_html=True)

# ---------------------------------------------------------------------
# 頂部導覽列：左邊網站名稱，右邊搜尋框 + 主題切換
# ---------------------------------------------------------------------
col_kwargs = {"vertical_alignment": "center"} if ST_VERSION >= (1, 36) else {}
nav1, nav2, nav3 = st.columns([6, 2.4, 0.5], **col_kwargs)
nav1.markdown('<span class="qk-mark qk-nav"></span>', unsafe_allow_html=True)   # 給 CSS 辨識導覽列用的記號（不顯示）
nav1.markdown(
    '<div class="qk-brand"><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke-width="2" '
    'stroke-linecap="round" stroke-linejoin="round"><polyline points="3 17 9 11 13 15 21 7"/>'
    '<polyline points="15 7 21 7 21 13"/></svg>股票分析</div>',
    unsafe_allow_html=True,
)
nav2.text_input("搜尋股票", key="ticker", placeholder="搜尋代碼，例如 2330、AAPL", label_visibility="collapsed")
theme_icon = ":material/contrast:" if ST_VERSION >= (1, 36) else "◐"
nav3.toggle(theme_icon, key="dark")   # 打開 = 深色主題，關掉 = 淺色主題

stock_code = st.session_state.ticker
if stock_code.strip() == "":
    st.info("請在右上角輸入股票代碼，例如 2330（台積電）、6488（環球晶）、AAPL（蘋果）。")
    st.stop()

# ---------------------------------------------------------------------
# 決定要抓多少資料（依照「資料跨度」和「K 棒週期」）
# ---------------------------------------------------------------------
span = st.session_state.span
interval_name = st.session_state.interval
iv = INTERVALS[interval_name]
kind, amount = SPANS[span]
span_label = span           # 畫面上「近 X 最高 / 最低」的 X
notice = None
today = pd.Timestamp.today().normalize()

if iv["intraday"]:
    # 分 K：Yahoo 有天數限制，跨度太長就自動縮短
    want_days = int(amount * 1.5) + 6 if kind == "d" else amount * 31    # 換算成日曆天（含週末）
    if want_days > iv["max_days"]:
        notice = f"Yahoo Finance 的 {interval_name} K 只提供最近約 {iv['max_days'] + 1} 天資料，已改為顯示最近 {iv['max_days']} 天。"
        kind, amount = "cal", iv["max_days"]
        want_days = iv["max_days"]
        span_label = f"{iv['max_days']} 天"
    fetch_days = min(iv["max_days"], want_days + iv["warmup_days"])
    fetch_kwargs = {"period": f"{fetch_days}d"}
else:
    # 日 / 週 / 月線
    min_m = MIN_MONTHS.get(interval_name, 0)
    if (kind == "d" and min_m) or (kind == "m" and amount < min_m):
        new_span = "1 年" if min_m >= 12 else f"{min_m} 個月"
        notice = f"{interval_name}選 {span} 只會有很少幾根 K 棒，已改為顯示 {new_span}。"
        kind, amount, span_label = "m", min_m, new_span
    if kind == "m":
        show_start = today - pd.DateOffset(months=amount)
        fetch_start = show_start - pd.Timedelta(days=WARMUP_DAYS[interval_name])
    else:   # 日線 + 1～20 天：抓最近一段日 K，再取最後 N 個交易日
        fetch_start = today - pd.Timedelta(days=amount * 2 + 15 + WARMUP_DAYS[interval_name])
    fetch_kwargs = {"start": fetch_start.strftime("%Y-%m-%d")}

# ---------------------------------------------------------------------
# 抓資料（台股上市、上櫃都試，有資料就停；Yahoo 偶爾失敗會自動重試）
# ---------------------------------------------------------------------
df, symbol, last_error = None, None, None
for s in to_yahoo_symbols(stock_code):
    for attempt in range(3):
        try:
            data = download_history(s, iv["yf"], **fetch_kwargs)
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

# 只保留需要的欄位；把時區拿掉（保留交易所當地時間）
df = df[["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Close"])
if df.index.tz is not None:
    df.index = df.index.tz_localize(None)
if iv["resample"]:
    df = resample_ohlcv(df, iv["resample"])

# 計算指標：一定要用「完整資料（含暖機）」算完，再切出要顯示的範圍
df = add_moving_averages(df)
df = add_macd(df)
df = add_kdj(df)
df = add_bollinger(df)

# 切出要顯示的範圍
if kind == "d":
    # 最近 N 個「交易日」（週末、連假時會往前找有開盤的日子）
    keep_days = sorted(set(df.index.date))[-amount:]
    show_df = df[df.index.date >= keep_days[0]]
elif kind == "cal":
    show_df = df[df.index >= today - pd.Timedelta(days=amount)]
else:
    show_df = df[df.index >= show_start]

if iv["intraday"]:
    x_format = "%H:%M" if len(set(show_df.index.date)) == 1 else "%m/%d %H:%M"
    date_fmt = "%Y/%m/%d %H:%M"
else:
    x_format = "%Y/%m" if interval_name == "月線" else "%Y/%m/%d"
    date_fmt = "%Y/%m/%d"

if show_df.empty:
    st.warning("這個區間沒有資料，請換一個資料跨度或 K 棒週期。")
    st.stop()

# 台股成交量換成「張」（1 張 = 1000 股）；其他市場維持「股」
is_tw = symbol.endswith((".TW", ".TWO"))
vol_unit = "張" if is_tw else "股"
show_df = show_df.copy()
if is_tw:
    show_df["Volume"] = show_df["Volume"] / 1000

# ---------------------------------------------------------------------
# 股票名稱與價格
# ---------------------------------------------------------------------
name = get_stock_name(symbol)
last = show_df.iloc[-1]
if iv["intraday"]:
    # 分 K：跟「前一個交易日的收盤價」比，跟看盤軟體一樣
    before = df[df.index.date < show_df.index[-1].date()]
    prev_close = before["Close"].iloc[-1] if len(before) else show_df["Open"].iloc[0]
else:
    # 日 / 週 / 月線：跟前一根 K 棒的收盤價比（用完整資料，所以跨度只有 1 根時也算得出來）
    prev_close = df["Close"].iloc[-2] if len(df) > 1 else last["Open"]
change = last["Close"] - prev_close
pct = change / prev_close * 100 if prev_close else 0
# 漲跌除了顏色，也用 ▲▼ 和正負號表示，色弱的人也分得出來
if change > 0:
    chg_cls, arrow = "qk-up", "▲"
elif change < 0:
    chg_cls, arrow = "qk-down", "▼"
else:
    chg_cls, arrow = "qk-flat", ""

market = "台灣上櫃" if symbol.endswith(".TWO") else ("台灣上市" if symbol.endswith(".TW") else "海外市場")
currency = get_currency(symbol)
vol_label = "成交量（最新一根）" if iv["intraday"] else ("成交量" if interval_name == "日線" else f"成交量（本{interval_name[0]}）")
kd_text = f"{last['K']:.1f} / {last['D']:.1f}" if pd.notna(last["K"]) else "—"

st.markdown(
    f'<div class="qk-head">'
    f'<div class="qk-name">{html.escape(str(name))}<span class="qk-code">{html.escape(symbol)}</span></div>'
    f'<div class="qk-price-row"><span class="qk-price">{last["Close"]:,.2f}</span>'
    f'<span class="qk-chg {chg_cls}">{arrow} {change:+,.2f}（{pct:+.2f}%）</span></div>'
    f'<div class="qk-meta">{market} · {html.escape(currency)} · 資料時間 {show_df.index[-1].strftime(date_fmt)}'
    f'（交易所當地時間，Yahoo 資料可能延遲）</div>'
    f'<div class="qk-stats">'
    f'<div class="qk-stat"><label>近 {span_label}最高</label><b>{show_df["High"].max():,.2f}</b></div>'
    f'<div class="qk-stat"><label>近 {span_label}最低</label><b>{show_df["Low"].min():,.2f}</b></div>'
    f'<div class="qk-stat"><label>{vol_label}（{vol_unit}）</label><b>{last["Volume"]:,.0f}</b></div>'
    f'<div class="qk-stat"><label>K / D</label><b>{kd_text}</b></div>'
    f'</div></div>',
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------
# 分頁：用 radio 做成簡約的文字分頁（這樣「查看全部」按鈕才能切換到新聞頁）
# ---------------------------------------------------------------------
pg1 = st.columns(1)[0]   # 放進 columns 裡，CSS 才能用 qk-pagenav 這個記號找到這一排
pg1.radio("分頁", ["走勢圖", "相關新聞", "歷史資料", "指標說明"], key="page", horizontal=True,
          label_visibility="collapsed")
pg1.markdown('<span class="qk-mark qk-pagenav"></span>', unsafe_allow_html=True)
page = st.session_state.page
code = symbol.replace(".TWO", "").replace(".TW", "")
news_query = f"{name} {code}" if is_tw else f"{name} {symbol} 股票"

# =====================================================================
# 分頁 1：走勢圖
# =====================================================================
if page == "走勢圖":
    # --- 圖表工具列：左邊資料跨度，右邊 K 棒週期、均線 / 指標、副圖 ---
    tb1, tb2, tb3, tb4 = st.columns([5.2, 1.1, 1.2, 1.1], **col_kwargs)
    tb1.radio("資料跨度", list(SPANS), key="span", horizontal=True, label_visibility="collapsed",
              on_change=on_span_change)
    tb1.markdown('<span class="qk-mark qk-toolbar"></span>', unsafe_allow_html=True)
    tb2.selectbox("K 棒週期", list(INTERVALS), key="interval", label_visibility="collapsed",
                  format_func=lambda v: f"K 棒：{v}")
    pop_kwargs = {"width": "stretch"} if ST_VERSION >= (1, 46) else {"use_container_width": True}
    with tb3.popover("均線・指標", **pop_kwargs):
        st.caption("均線（依目前 K 棒週期計算）")
        for n in MA_COLORS:
            st.checkbox(MA_NAMES[n], key=f"ma{n}")
        st.caption("其他")
        st.checkbox("布林通道 (20, 2)", key="show_bb")
        st.checkbox("成交量", key="show_vol")
    tb4.selectbox("副圖指標", ["無", "MACD", "KDJ"], key="sub_ind", label_visibility="collapsed",
                  format_func=lambda v: "副圖：無" if v == "無" else f"副圖：{v}")

    if notice:
        st.caption("ⓘ " + notice)

    show_ma = {n: st.session_state[f"ma{n}"] for n in MA_COLORS}
    missing = [MA_NAMES[n] for n, c in show_ma.items() if c and show_df[f"MA{n}"].isna().all()]
    if missing:
        st.caption(f"ⓘ {'、'.join(missing)} 需要的 K 棒數超過可取得的資料，因此沒有畫出。")

    fig = draw_chart(show_df, show_ma, st.session_state.show_bb, st.session_state.show_vol,
                     st.session_state.sub_ind, x_format, vol_unit, theme)
    # theme=None：不要套用 Streamlit 自己的圖表主題，否則顏色會被蓋掉
    st.plotly_chart(fig, use_container_width=True, theme=None,
                    config={"displaylogo": False, "modeBarButtonsToRemove": ["lasso2d", "select2d"]})
    st.caption(f"顯示 {show_df.index[0].strftime(date_fmt)} ～ {show_df.index[-1].strftime(date_fmt)}，"
               f"共 {len(show_df)} 根 {interval_name} K　·　拖曳可縮放，雙擊還原")

    # --- 新聞預覽：最近 5 則 ---
    nh1, nh2, _ = st.columns([5.6, 1.1, 1.9], **col_kwargs)   # 跟新聞列表同寬（約 1080px），按鈕才會對齊在列表右上角
    nh1.markdown('<div class="qk-section"><div class="qk-section-title">相關新聞</div></div>', unsafe_allow_html=True)
    btn_kwargs = {"type": "tertiary"} if ST_VERSION >= (1, 42) else {}
    nh2.button("查看全部 →", on_click=go_to_news, **btn_kwargs)
    info = news_analysis(symbol, code, name, is_tw, news_query)   # 同時更新 V2.1 快照（計算很輕）
    news_list = info["news_list"]
    if news_list:
        st.markdown('<div class="qk-news">' + "".join(news_row(n, info) for n in news_list[:5]) + "</div>",
                    unsafe_allow_html=True)
    elif news_list is None:
        st.caption("新聞暫時抓不到，請稍後再試。")
    else:
        st.caption("找不到這檔股票的相關新聞。")

# =====================================================================
# 分頁 2：相關新聞（新聞情緒量化分析 V2.1）
# =====================================================================
elif page == "相關新聞":
    info = news_analysis(symbol, code, name, is_tw, news_query)
    res, stats, mom = info["result"], info["result"]["stats"], info["momentum"]
    counts = info["counts"]
    news_list = info["news_list"]
    if news_list is None:
        st.warning(f"新聞來源暫時查詢失敗，以下只用先前已儲存的新聞計算。（{info['fetch_error'][:120]}）")

    # --- 今日公司重大訊息（只有台股，而且 API 只提供當天公告） ---
    for a in info["material"]:
        with st.expander(f"📢 今日重大訊息｜{a['date']}｜{a['subject'][:60]}"):
            st.text(a["detail"])

    # ================= A. 新聞情緒總覽 =================
    shown_label = info["display_label"]
    if stats["valid"]:
        nsi_txt = f"{stats['nsi']:+.0f}"
        cls = LABEL_CLASS.get(shown_label, "qk-flat")
    else:
        nsi_txt, cls, shown_label = "—", "qk-flat", "無有效事件"
    mom_txt = (f"{'▲' if mom['value'] > 0 else ('▼' if mom['value'] < 0 else '')} {mom['value']:+.0f}　{mom['label']}"
               if mom["value"] is not None else mom["label"])
    a_txt = f"{stats['A']:.2f}" if stats["A"] is not None else "—"
    o_txt = ("無明顯方向性新聞" if stats["valid"] and stats["no_direction"]
             else (f"{stats['omega']:.2f}" if stats["omega"] is not None else "—"))
    st.markdown(
        f'<div class="qk-news"><div class="qk-section"><div class="qk-section-title">新聞情緒總覽</div>'
        f'<div class="qk-note">資料更新 {info["news_time"] or "—"}　·　分析截點 {pd.Timestamp(info["data_cutoff"]).tz_convert("Asia/Taipei").strftime("%m/%d %H:%M")}</div></div>'
        f'<div class="qk-overview">'
        f'<div class="qk-ov"><label>新聞情緒指數 NSI（−100 ～ +100）</label><span class="qk-score {cls}">{nsi_txt}</span></div>'
        f'<div class="qk-ov"><label>情緒方向</label><b class="{cls}">{shown_label}</b></div>'
        f'<div class="qk-ov"><label>5 日情緒動能</label><b>{mom_txt}</b></div>'
        f'<div class="qk-ov"><label>分析穩健程度</label><b>{res["robustness"]}</b></div>'
        f'<div class="qk-ov"><label>新聞 / 獨立事件</label><b>{counts["N_raw"]} 則 / {counts["K_events"]} 個</b></div>'
        f'</div>'
        f'<div class="qk-bar-legend" style="margin-top:14px">'
        f'<span>相關新聞 {counts["N_rel"]} 則</span><span>納入計算 {counts["K_incl"]} 個事件</span>'
        f'<span>等效事件數 n_eff {counts["n_eff"]:.1f}</span><span>平均情緒強度 A {a_txt}</span>'
        f'<span>情緒抵銷率 Ω {o_txt}</span></div>'
        f'<div class="qk-note" style="margin-top:6px">NSI 是新聞情緒的收縮加權平均，用來描述新聞面，不代表股價預測。'
        f'{"　" + "；".join(res["notes"]) if res["notes"] else ""}</div></div>',
        unsafe_allow_html=True,
    )
    with st.expander("計算細節：敏感度分析與權重"):
        sens_rows = [{"設定": k, "NSI": None if v["nsi"] is None else round(v["nsi"], 1), "標籤": v["label"] or "—"}
                     for k, v in res["sensitivity"].items()]
        st.dataframe(pd.DataFrame(sens_rows), hide_index=True, use_container_width=True)
        cap_txt = ("未觸發" if not res["capped"] else f"已觸發（調整前後 NSI 差 {res['nsi_cap_diff']:+.1f}）")
        st.caption(f"敏感度結果{'一致' if res['sensitivity_consistent'] else '不一致'}　·　"
                   f"最大單一事件權重占比 {res['max_share'] * 100:.0f}%　·　40% 集中度上限：{cap_txt}"
                   if res["max_share"] is not None else "沒有可計算的事件")
        st.caption(f"公式 {ns.FORMULA_VERSION}　·　參數 {ns.PARAMS_VERSION}　·　分類 {ns.CLASSIFIER_MODEL} {ns.CLASSIFIER_VERSION}"
                   f"（規則式關鍵字判斷，未經準確率驗證）")

    # ================= B. 情緒趨勢 =================
    tr1, tr2, _ = st.columns([3, 1.2, 1.2], **({"vertical_alignment": "bottom"} if ST_VERSION >= (1, 36) else {}))
    tr1.markdown('<div class="qk-section"><div class="qk-section-title">情緒趨勢</div></div>', unsafe_allow_html=True)
    trend_days = tr2.selectbox("趨勢區間", [7, 30], key="trend_days", label_visibility="collapsed",
                               format_func=lambda v: f"近 {v} 個交易日")
    series = npl.trend_series(get_store(), symbol, info["calendar"], info["session"], trend_days)
    ok_pts = [p for p in series if p["status"] == "ok"]
    n_failed = sum(1 for p in series if p["status"] == "failed")
    n_missing = sum(1 for p in series if p["status"] == "not_collected")
    if len(ok_pts) >= 2:
        tfig = go.Figure(go.Scatter(
            x=[p["date"].strftime("%m/%d") for p in ok_pts], y=[p["nsi"] for p in ok_pts], mode="lines+markers",
            line=dict(color=theme["accent"], width=2), marker=dict(size=6),
            customdata=[p["robustness"] for p in ok_pts],
            hovertemplate="%{x}　NSI %{y:+.0f}<br>穩健程度 %{customdata}<extra></extra>"))
        tfig.add_hline(y=0, line_color=theme["border"], line_width=1)
        tfig.update_layout(height=240, paper_bgcolor=theme["chart"], plot_bgcolor=theme["chart"],
                           margin=dict(l=12, r=12, t=12, b=8), showlegend=False,
                           font=dict(color=theme["muted"], size=12),
                           hoverlabel=dict(bgcolor=theme["toolbar"], font=dict(color=theme["text"])))
        tfig.update_xaxes(type="category", showgrid=False, linecolor=theme["border"])
        tfig.update_yaxes(range=[-100, 100], gridcolor=theme["grid"], zeroline=False, side="right")
        st.plotly_chart(tfig, use_container_width=True, theme=None, config={"displayModeBar": False})
        st.caption(f"有效快照 {len(ok_pts)} 天　·　查詢失敗 {n_failed} 天　·　未蒐集 {n_missing} 天（不以 0 分補值）")
    else:
        st.markdown(
            f'<div class="qk-empty">趨勢資料不足（近 {trend_days} 個交易日只有 {len(ok_pts)} 天有效快照）<br>'
            f'系統從開始使用後，每個交易日打開這頁時會記錄一次 NSI 快照；至少要有 2 天才會畫出趨勢。'
            f'查詢失敗 {n_failed} 天、未蒐集 {n_missing} 天不會被當成 0 分。</div>',
            unsafe_allow_html=True)

    # ================= C. 重大事件與多空分歧 =================
    alerts = info["alerts"]
    st.markdown('<div class="qk-section"><div class="qk-section-title">重大事件與多空分歧'
                f'<span>{len(alerts)} 項</span></div></div>', unsafe_allow_html=True)
    if alerts:
        rows_html = []
        for a in alerts:
            kind_cls = ("qk-up" if a["kind"] == "重大利多" else ("qk-down" if a["kind"] == "重大利空" else "qk-flat"))
            when = a.get("updated_at").astimezone().strftime("%m/%d %H:%M") if a.get("updated_at") else ""
            src = "、".join(a.get("sources", [])[:3])
            sub = "　·　".join(x for x in [a.get("status", ""), src, when] if x)
            rows_html.append(
                f'<div class="qk-item"><div><div class="qk-item-title">{ALERT_ICON.get(a["kind"], "")} {html.escape(a["title"])}</div>'
                f'<div class="qk-item-meta">{html.escape(sub)}</div>'
                f'<div class="qk-item-meta">{html.escape(a.get("reason", ""))}</div></div>'
                f'<span class="qk-tag {kind_cls}">{a["kind"]}</span></div>')
        st.markdown('<div class="qk-news">' + "".join(rows_html) + "</div>", unsafe_allow_html=True)
        st.caption("「未確認重大消息」是傳聞，尚未被證實，不代表已經發生；已否認、已更正的事件不再以原方向計分。")
    else:
        st.caption("目前沒有重大利多、重大利空、未確認消息或高度分歧的情況。")

    # ================= D. AI 分析摘要 =================
    summarizer = get_summarizer()
    inp = ns.summary_input(symbol, str(name), res, mom, alerts, info["display_label"])
    rule_sum = ns.rule_based_summary(res, mom, alerts)
    force = st.session_state.pop(f"force_summary_{symbol}", False)
    summary = ai_summary.get_summary(get_store(), summarizer, symbol, inp, info["event_version_set"],
                                     info["snapshot_id"], info["data_cutoff"], rule_sum, force=force)
    kind_txt = {"ai": f"AI 生成（{getattr(summarizer, 'display_name', summarizer.model)}）", "ai_stale": "AI 摘要（已過期，最新一次更新失敗）",
                "rule": "規則式摘要（非 AI 生成）"}[summary["kind"]]
    gen_at = pd.Timestamp(summary["generated_at"]).tz_convert("Asia/Taipei").strftime("%m/%d %H:%M")
    cut_at = pd.Timestamp(summary["data_cutoff"]).tz_convert("Asia/Taipei").strftime("%m/%d %H:%M")
    s1, s2, _ = st.columns([5.6, 1.1, 1.9], **col_kwargs)   # 按鈕對齊在摘要區（約 1080px）右上角
    s1.markdown(f'<div class="qk-section"><div class="qk-section-title">AI 分析摘要<span>{kind_txt}</span></div>'
                f'<div class="qk-note">更新 {gen_at}　·　資料截點 {cut_at}</div></div>', unsafe_allow_html=True)

    def request_reanalysis(tk=symbol):
        ok_, wait = ai_summary.manual_allowed(get_store(), tk, st.session_state.get(f"manual_at_{tk}"))
        if ok_:
            st.session_state[f"force_summary_{tk}"] = True
            st.session_state[f"manual_at_{tk}"] = pd.Timestamp.now(tz="UTC").to_pydatetime()
            get_store().record_manual_run(tk)
        else:
            st.session_state[f"cooldown_msg_{tk}"] = f"請等 {wait} 秒後再重新分析"

    s2.button("重新分析", on_click=request_reanalysis, disabled=not summarizer.available,
              help=None if summarizer.available else f"AI 摘要未啟用：{summarizer.reason}")
    msg = st.session_state.pop(f"cooldown_msg_{symbol}", None)
    if msg:
        st.caption("ⓘ " + msg)
    c = summary["content"]

    def bullet(items):
        out = []
        for x in items or []:
            txt = x["text"] if isinstance(x, dict) else x
            out.append(f"<li>{html.escape(str(txt))}</li>")
        return "<ul style='margin:4px 0 0 18px'>" + "".join(out) + "</ul>" if out else "<div class='qk-note'>無</div>"

    st.markdown(
        f'<div class="qk-news qk-panel" style="font-size:14px; line-height:1.8">'
        f'<b>整體情緒</b><div>{html.escape(c.get("overall", ""))}</div>'
        f'<b>情緒變化</b><div>{html.escape(c.get("change", ""))}</div>'
        f'<b>正面因素</b>{bullet(c.get("positives"))}'
        f'<b>負面因素</b>{bullet(c.get("negatives"))}'
        f'<b>重大風險</b>{bullet(c.get("risks"))}'
        f'<b>分析限制</b>{bullet(c.get("limitations"))}</div>',
        unsafe_allow_html=True)
    if summary["kind"] != "ai":
        st.caption("ⓘ " + ("AI 摘要功能未啟用（" + summary["error"] + "），目前顯示依數據自動組成的規則式摘要。"
                           if summary["kind"] == "rule" else f"AI 摘要更新失敗：{summary['error'][:120]}"))

    # ================= E. 新聞明細 =================
    if news_list:
        d1, d2, _ = st.columns([3, 1.2, 1.2], **({"vertical_alignment": "bottom"} if ST_VERSION >= (1, 36) else {}))
        def label_of(n):
            it = info["item_by_id"].get(ns.make_news_id(n.get("link", ""), n.get("source", ""), n["title"]))
            return it.label if it else "中性"
        f = st.session_state.news_filter
        shown = [n for n in news_list if f == "全部新聞" or label_of(n) == f]
        d1.markdown(f'<div class="qk-section"><div class="qk-section-title">新聞明細<span>{len(shown)} 則</span></div></div>',
                    unsafe_allow_html=True)
        d2.selectbox("篩選", ["全部新聞", "強烈偏多", "偏多", "中性", "偏空", "強烈偏空"], key="news_filter",
                     label_visibility="collapsed", format_func=lambda v: f"篩選：{v}")
        if shown:
            st.markdown('<div class="qk-news">' + "".join(news_row(n, info, with_detail=True) for n in shown) + "</div>",
                        unsafe_allow_html=True)
        else:
            st.caption("這個分類目前沒有新聞。")
        st.caption("新聞來源：Google 新聞 RSS 搜尋結果；點新聞可展開判斷依據、事件類型、確認狀態與權重。"
                   "畫面顯示最新 20 則，計算則使用系統保存的所有相關新聞。")
    elif news_list is not None:
        st.info("找不到這檔股票的相關新聞。")

# =====================================================================
# 分頁 3：歷史資料
# =====================================================================
elif page == "歷史資料":
    cols = ["Open", "High", "Low", "Close", "Volume"] + [f"MA{n}" for n in MA_COLORS] + \
           ["DIF", "DEA", "OSC", "K", "D", "J", "BB_UP", "BB_LOW"]
    table = show_df[cols].sort_index(ascending=False)
    table.index = table.index.strftime(date_fmt)
    table.index.name = "時間" if iv["intraday"] else "日期"
    table = table.rename(columns={"Open": "開盤", "High": "最高", "Low": "最低", "Close": "收盤",
                                  "Volume": f"成交量({vol_unit})", "BB_UP": "布林上軌", "BB_LOW": "布林下軌"})
    st.write("")
    if st.session_state.dark:
        # 深色模式：用 pandas Styler 把每一格換成深色
        styled = table.round(2).style.format(precision=2).set_properties(
            **{"background-color": theme["chart"], "color": theme["text"]})
        st.dataframe(styled, use_container_width=True, height=520)
    else:
        st.dataframe(table.round(2), use_container_width=True, height=520)
    st.download_button("下載 CSV", table.round(4).to_csv().encode("utf-8-sig"),
                       file_name=f"{symbol}_{iv['yf']}.csv", mime="text/csv")

# =====================================================================
# 分頁 4：指標說明
# =====================================================================
else:
    st.markdown(
        """
**移動平均線 MA**：最近 N 根 K 棒收盤價的平均（`rolling(N).mean()`）。依目前選的 K 棒週期計算，
例如週線的 MA20 是 20 根週 K 的平均。日線的 MA20 / 60 / 120 / 240 分別俗稱月線、季線、半年線、年線。

**MACD (12, 26, 9)**：DIF = EMA12 − EMA26；DEA = DIF 的 9 期 EMA；柱狀圖 OSC = DIF − DEA。

**KDJ (9, 3, 3)**：RSV = (收盤 − 9 根最低) ÷ (9 根最高 − 9 根最低) × 100；
K = 2/3 × 前 K + 1/3 × RSV；D = 2/3 × 前 D + 1/3 × K；J = 3K − 2D。K、D 初始值為 50。

**布林通道 (20, 2)**：中軌 = MA20；上軌 / 下軌 = 中軌 ± 2 × 最近 20 根收盤價的標準差（母體標準差）。

**週線 / 月線**：由日線 resample 而來：開盤取第一天、最高取最大、最低取最小、收盤取最後一天、成交量加總。

**分 K**：資料跨度選 1 / 5 / 10 / 20 天時，自動帶入 5 / 15 / 60 分 K（可以再手動改）。
Yahoo Finance 的 5、15 分 K 只提供最近 60 天，60 分 K 約 730 天，超過會自動縮短。

**新聞**：Google 新聞 RSS 搜尋「股票名稱 + 代碼」，依標題去除重複後按時間排序；偏多 / 偏空為標題關鍵字判斷。
重大訊息來自證交所、櫃買中心 OpenAPI 的每日公告。
        """
    )

st.caption("資料來源：Yahoo Finance（yfinance）、證交所 / 櫃買中心 OpenAPI、Google 新聞｜台股慣例紅漲綠跌｜僅供學習研究，非投資建議")
