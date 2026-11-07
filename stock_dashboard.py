# -*- coding: utf-8 -*-
"""
====================================================================
  理財機器人 期中報告 ── QUANTUM·K Financial Research Platform
====================================================================
設計方向：Minimal Financial Research Platform（極簡金融研究平台）
  · 讓金融數據成為主角：暖白 / 淺灰 / 深灰 / 海軍藍的安靜層次
  · 版面順序：股票資訊 → 週期與均線控制 → K 線 → 市場指標 → 技術面研究摘要 → 歷史資料

功能總覽：
  1. 以 yfinance 爬取 Yahoo Finance 公開歷史股價（台股上市/上櫃、美股、指數、加密貨幣）
  2. 週期：5分、15分、30分、60分、日線、週線、月線（週/月線由日線正確彙整 OHLCV）
  3. 均線：MA5、MA10、MA20、MA60、MA120、MA240，依「目前週期」的 K 棒計算，各自獨立開關
  4. 副圖：成交量（含均量線）＋ MACD / RSI / KD 切換，所有圖層共用同一條時間軸
  5. 規則式技術診斷：均線排列、交叉、MACD、RSI、KD 綜合判讀（偏多/偏空/盤整/訊號分歧/資料不足）
  6. 淺色（預設）/ 深色主題、三種字體分工、台股紅漲綠跌、十字準心、OHLCV 游標數值、區域縮放
  7. 響應式排版（桌面 / 平板 / 手機）
  8. 完整錯誤處理：代碼錯誤、查無資料、網路中斷、逾時、限流、資料不足，皆以中文 Alert 呈現

執行方式：
  pip install -r requirements.txt
  streamlit run stock_dashboard.py
====================================================================
"""

import html
import socket
import time
from datetime import datetime

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import yfinance as yf

# ------------------------------------------------------------------
# 0. 頁面基本設定（必須是第一個 Streamlit 指令）
# ------------------------------------------------------------------
st.set_page_config(
    page_title="QUANTUM·K｜金融研究平台",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="auto",  # 桌面展開、手機預設收合,
)

# Streamlit 版本相容：新版（>= 1.46）以 width="stretch" 取代已棄用的 use_container_width
_ST_VER = tuple(int(p) for p in st.__version__.split(".")[:2] if p.isdigit())
STRETCH = {"width": "stretch"} if _ST_VER >= (1, 46) else {"use_container_width": True}


# ------------------------------------------------------------------
# 1. 設計系統：主題色彩 Tokens
# ------------------------------------------------------------------
THEMES = {
    # 淺色主題（預設）：暖白背景、純白卡片、海軍藍強調
    "light": dict(
        bg="#F6F8FB", sidebar="#F0F3F8", card="#FFFFFF", card2="#F8FAFD", plot_bg="#FFFFFF",
        text="#182230", muted="#64748B", faint="#94A3B8", border="#E3E9F1",
        accent="#4169C9", accent_hover="#3457B0", accent_soft="#EDF3FF",
        up="#D94E5D", down="#16876C", flat="#8390A4", warn="#C58B31",
        grid="rgba(100,116,139,0.10)", hover_bg="#FFFFFF", hover_border="#CBD5E1",
        input_bg="#FFFFFF", shadow="0 2px 10px rgba(20,35,60,0.035)",
        err_bg="#FDF1F2", err_text="#A8323F", err_border="#F3C9CE",
        warn_bg="#FDF7EA", warn_text="#94600F", warn_border="#F1DDB0",
        info_bg="#EDF3FF", info_text="#3457B0", info_border="#C9D8F6",
        spike="rgba(24,34,48,0.35)",
    ),
    # 深色主題：低彩度石墨色，而非霓虹電競風
    "dark": dict(
        bg="#0C1017", sidebar="#111722", card="#161D29", card2="#1A2230", plot_bg="#0F141D",
        text="#EDF2F7", muted="#94A3B8", faint="#64748B", border="#263143",
        accent="#7BA8F8", accent_hover="#9BBEFA", accent_soft="rgba(123,168,248,0.12)",
        up="#F07178", down="#43B995", flat="#8B97AA", warn="#E5B86C",
        grid="rgba(148,163,184,0.08)", hover_bg="#202A39", hover_border="#38465A",
        input_bg="#111722", shadow="0 2px 10px rgba(0,0,0,0.25)",
        err_bg="rgba(240,113,120,0.10)", err_text="#F59BA0", err_border="rgba(240,113,120,0.35)",
        warn_bg="rgba(229,184,108,0.10)", warn_text="#E5B86C", warn_border="rgba(229,184,108,0.35)",
        info_bg="rgba(123,168,248,0.10)", info_text="#9BBEFA", info_border="rgba(123,168,248,0.35)",
        spike="rgba(237,242,247,0.40)",
    ),
}

# 均線設定：專屬柔和配色（淺色模式略加深以維持白底對比）與線寬
MA_CONFIG = {
    5:   dict(name="MA5",   nick="週線",   dark="#E9BD59", light="#C99A2E", width=1.7, default=True),
    10:  dict(name="MA10",  nick="雙週線", dark="#DB966D", light="#C77A4E", width=1.5, default=True),
    20:  dict(name="MA20",  nick="月線",   dark="#72A7E8", light="#4F8AD6", width=1.8, default=True),
    60:  dict(name="MA60",  nick="季線",   dark="#4CC2B5", light="#2EA597", width=1.5, default=False),
    120: dict(name="MA120", nick="半年線", dark="#A694E0", light="#8A74D1", width=1.5, default=False),
    240: dict(name="MA240", nick="年線",   dark="#969FAC", light="#7C8696", width=1.3, default=False),
}
MA_LIST = list(MA_CONFIG.keys())

# 週期設定：interval = yfinance 資料間隔；period = 抓取長度（多抓暖機資料讓長均線從畫面第一根就正確）
PERIODS = {
    "5分":  dict(interval="5m",  period="60d",  resample=None, fmt="%m/%d %H:%M", label="5 分鐘線",  unit="5 分 K"),
    "15分": dict(interval="15m", period="60d",  resample=None, fmt="%m/%d %H:%M", label="15 分鐘線", unit="15 分 K"),
    "30分": dict(interval="30m", period="60d",  resample=None, fmt="%m/%d %H:%M", label="30 分鐘線", unit="30 分 K"),
    "60分": dict(interval="60m", period="730d", resample=None, fmt="%m/%d %H:%M", label="60 分鐘線", unit="60 分 K"),
    "日":   dict(interval="1d",  period="5y",   resample=None, fmt="%Y/%m/%d",    label="日線",      unit="日 K"),
    "週":   dict(interval="1d",  period="max",  resample="W",  fmt="%Y/%m/%d",    label="週線",      unit="週 K"),
    "月":   dict(interval="1d",  period="max",  resample="M",  fmt="%Y/%m",       label="月線",      unit="月 K"),
}

# 熱門快選清單（同時作為中文名稱對照表）
POPULAR = {
    "2330 台積電": "2330", "2317 鴻海": "2317", "2454 聯發科": "2454",
    "2308 台達電": "2308", "2382 廣達": "2382", "2881 富邦金": "2881",
    "2603 長榮": "2603", "3008 大立光": "3008", "6488 環球晶（上櫃）": "6488",
    "0050 元大台灣50": "0050", "0056 元大高股息": "0056", "^TWII 台灣加權指數": "^TWII",
    "AAPL 蘋果": "AAPL", "NVDA 輝達": "NVDA", "TSLA 特斯拉": "TSLA",
    "MSFT 微軟": "MSFT", "BTC-USD 比特幣": "BTC-USD",
}
NAME_MAP = {code: label.split(" ", 1)[1].replace("（上櫃）", "") for label, code in POPULAR.items()}

# 副圖指標選項
SUB_INDICATORS = ["MACD", "RSI", "KD", "不顯示"]


# ------------------------------------------------------------------
# 2. 樣式（CSS）：設計系統 + 響應式 Breakpoints
# ------------------------------------------------------------------
def inject_css(t: dict) -> None:
    """依主題注入全站 CSS。以 CSS Variables 管理色彩，元件樣式只寫一次。"""
    root_vars = "\n".join(f"  --qk-{k.replace('_', '-')}: {v};" for k, v in t.items())
    # 每條均線的專屬色：桌面為標籤前的線段色塊，手機為卡片左側色條
    ma_swatches = "\n".join(
        f'div[data-testid="stHorizontalBlock"]:has(.qk-ma-val) > div:nth-child({i + 1}) '
        f'[data-testid="stCheckbox"] [data-testid="stMarkdownContainer"] p::before '
        f'{{ background: var(--qk-ma{n}); }}\n'
        f'div[data-testid="stHorizontalBlock"]:has(.qk-ma-val) > div:nth-child({i + 1}) '
        f'{{ --qk-ma-cur: var(--qk-ma{n}); }}'
        for i, n in enumerate(MA_LIST)
    )
    theme_key = "dark" if t["bg"] == THEMES["dark"]["bg"] else "light"
    ma_vars = "\n".join(f"  --qk-ma{n}: {MA_CONFIG[n][theme_key]};" for n in MA_LIST)

    css = """
<style>
/* ===== 三種字體分工：Orbitron（英文品牌）、Noto Sans TC（中文介面）、JetBrains Mono（數字） ===== */
@import url('https://fonts.googleapis.com/css2?family=Orbitron:wght@600;700&family=Noto+Sans+TC:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap');

:root {
__ROOT_VARS__
__MA_VARS__
}

/* ===== 全域 ===== */
html, body, .stApp { font-family: 'Noto Sans TC', 'Microsoft JhengHei', sans-serif; }
.stApp { background: var(--qk-bg); color: var(--qk-text); font-size: 14px; line-height: 1.65; }
[data-testid="stHeader"] { background: transparent; }
.block-container, [data-testid="stMainBlockContainer"] {
  padding: 1.5rem 28px 2rem 28px !important; max-width: 1500px; }
.stApp p, .stApp li, .stApp label, [data-testid="stWidgetLabel"] p,
[data-testid="stMarkdownContainer"] { color: var(--qk-text); }
[data-testid="stCaptionContainer"], .stApp small { color: var(--qk-muted) !important; }
[data-testid="stVerticalBlock"] { gap: 0.9rem; }
.qk-mono, .qk-num { font-family: 'JetBrains Mono', ui-monospace, monospace; font-variant-numeric: tabular-nums; }
*:focus-visible { outline: 2px solid var(--qk-accent) !important; outline-offset: 2px; }

/* ===== 側邊欄 ===== */
[data-testid="stSidebar"] { background: var(--qk-sidebar); border-right: 1px solid var(--qk-border); }
[data-testid="stSidebar"] * { color: var(--qk-text); }
[data-testid="stSidebar"] [data-testid="stWidgetLabel"] p { font-size: .8125rem; font-weight: 500; color: var(--qk-muted); }
@media (min-width: 1200px) {
  section[data-testid="stSidebar"][aria-expanded="true"] { width: 260px !important; min-width: 260px !important; }
}
.qk-side-brand { font-family: 'Orbitron', sans-serif; font-weight: 700; font-size: 1.1rem; letter-spacing: 1.5px; color: var(--qk-text); }
.qk-side-sub { font-size: .75rem; color: var(--qk-muted); margin-top: -2px; }
.qk-side-h { font-size: .75rem; font-weight: 600; letter-spacing: .08em; color: var(--qk-faint); text-transform: uppercase;
  margin: 6px 0 -4px 0; }

/* ===== 表單元件：輸入框 / 下拉 / 按鈕 ===== */
.stApp input, .stApp textarea { background: var(--qk-input-bg) !important; color: var(--qk-text) !important;
  font-family: 'JetBrains Mono', monospace !important; font-variant-numeric: tabular-nums; }
div[data-baseweb="input"], div[data-baseweb="base-input"], div[data-baseweb="select"] > div {
  background: var(--qk-input-bg) !important; border-color: var(--qk-border) !important; color: var(--qk-text) !important;
  border-radius: 8px !important; min-height: 40px; }
div[data-baseweb="select"] svg { fill: var(--qk-muted); }
/* Streamlit 內部包裝層會帶入設定檔的底色，統一改為主題輸入框底色（避免深色模式殘留淺色塊） */
[data-testid="stTextInputRootElement"], div[data-baseweb="select"] div,
div[data-baseweb="input"] > div { background-color: var(--qk-input-bg) !important; }
/* 新版 Streamlit（≥ 1.6x）的下拉選單改用 react-aria（role="group"），另外指定 */
[data-testid="stSelectbox"] [role="group"], [data-testid="stSelectbox"] [role="group"] > *:not(svg) {
  background-color: var(--qk-input-bg) !important; color: var(--qk-text) !important; }
[data-testid="stSelectbox"] [role="group"] { border-color: var(--qk-border) !important; border-radius: 8px !important; }
[role="listbox"], [role="listbox"] [role="option"] { background-color: var(--qk-card) !important; color: var(--qk-text) !important; }
[role="listbox"] [role="option"]:hover, [role="listbox"] [role="option"][data-focused] { background-color: var(--qk-accent-soft) !important; }
.stApp input::placeholder, div[data-baseweb="select"] [class*="placeholder"] { color: var(--qk-faint) !important; }
div[data-baseweb="popover"] ul, div[data-baseweb="popover"] li, ul[data-baseweb="menu"] {
  background: var(--qk-card) !important; color: var(--qk-text) !important; }
div[data-baseweb="popover"] li:hover { background: var(--qk-accent-soft) !important; }
div[data-baseweb="tooltip"] div, [data-testid="stTooltipContent"] { background: var(--qk-hover-bg) !important;
  color: var(--qk-text) !important; border: 1px solid var(--qk-hover-border); font-size: .75rem; }
.stButton button, .stDownloadButton button { min-height: 38px; border-radius: 8px; font-weight: 500;
  background: var(--qk-card); color: var(--qk-text); border: 1px solid var(--qk-border); }
.stButton button:hover, .stDownloadButton button:hover { border-color: var(--qk-accent); color: var(--qk-accent); }
.stButton button[kind="primary"] { background: var(--qk-accent); color: #fff; border-color: var(--qk-accent); }

/* ===== 週期 segmented control（以 radio 實作，舊版 / 新版 Streamlit DOM 皆適用） ===== */
div[role="radiogroup"] { gap: 0 !important; background: var(--qk-card); border: 1px solid var(--qk-border);
  border-radius: 10px; padding: 3px; display: inline-flex; flex-wrap: nowrap; max-width: 100%; overflow-x: auto;
  scrollbar-width: none; }
div[role="radiogroup"]::-webkit-scrollbar { display: none; }
div[role="radiogroup"] > div { display: flex; flex-wrap: nowrap; gap: 0 !important; }
div[role="radiogroup"] label { margin: 0 !important; padding: 0 16px; min-height: 36px; border-radius: 7px;
  cursor: pointer; display: flex; align-items: center; border: 1px solid transparent; transition: background .12s ease;
  white-space: nowrap; flex: 0 0 auto; }
div[role="radiogroup"] > label > div:first-child { display: none; }
div[role="radiogroup"] label > div > div:first-child:not([data-testid="stMarkdownContainer"]) { display: none; }
div[role="radiogroup"] label p { font-weight: 500; color: var(--qk-muted) !important; font-size: .875rem; margin: 0; }
div[role="radiogroup"] label:hover { background: var(--qk-card2); }
div[role="radiogroup"] label:has(input:checked) { background: var(--qk-accent-soft); border-color: var(--qk-info-border); }
div[role="radiogroup"] label:has(input:checked) p { color: var(--qk-accent) !important; font-weight: 600; }

/* ===== 均線選擇列（以 .qk-ma-val 辨識該列 columns） ===== */
div[data-testid="stHorizontalBlock"]:has(.qk-ma-val) { gap: 8px !important; flex-wrap: wrap; }
div[data-testid="stHorizontalBlock"]:has(.qk-ma-val) > div {
  background: var(--qk-card); border: 1px solid var(--qk-border); border-radius: 10px;
  padding: 6px 12px 6px 12px; min-width: 0; }
div[data-testid="stHorizontalBlock"]:has(.qk-ma-val) > div [data-testid="stVerticalBlock"] { gap: 0 !important; }
/* Streamlit 的 markdown 區塊預設有負的下邊距，會讓數值溢出卡片邊框，這裡歸零 */
div[data-testid="stHorizontalBlock"]:has(.qk-ma-val) .stMarkdown,
div[data-testid="stHorizontalBlock"]:has(.qk-ma-val) [data-testid="stMarkdown"],
div[data-testid="stHorizontalBlock"]:has(.qk-ma-val) [data-testid="stMarkdownContainer"] { margin-bottom: 0 !important; }
.qk-ma-val { padding-bottom: 4px; }
div[data-testid="stHorizontalBlock"]:has(.qk-ma-val) [data-testid="stCheckbox"] label { min-height: 30px; align-items: center; }
div[data-testid="stHorizontalBlock"]:has(.qk-ma-val) [data-testid="stCheckbox"] p {
  font-family: 'JetBrains Mono', monospace; font-weight: 600; font-size: .875rem; white-space: nowrap; }
div[data-testid="stHorizontalBlock"]:has(.qk-ma-val) [data-testid="stCheckbox"] [data-testid="stMarkdownContainer"] p::before {
  content: ''; display: inline-block; width: 14px; height: 3px; border-radius: 2px; margin-right: 7px; vertical-align: middle; }
__MA_SWATCHES__
.qk-ma-val { font-family: 'JetBrains Mono', monospace; font-variant-numeric: tabular-nums; font-size: .78rem;
  color: var(--qk-muted); padding-left: 28px; line-height: 1.3; white-space: nowrap; }

/* ===== 頂部導航 ===== */
.qk-nav { display: flex; justify-content: space-between; align-items: center; gap: 12px; flex-wrap: wrap;
  padding-bottom: 14px; border-bottom: 1px solid var(--qk-border); margin-bottom: 4px; }
.qk-brand { font-family: 'Orbitron', sans-serif; font-weight: 600; font-size: 1.5rem; letter-spacing: 2px;
  color: var(--qk-text); line-height: 1.3; }
.qk-brand span { color: var(--qk-accent); }
.qk-brand-sub { font-size: .75rem; color: var(--qk-muted); letter-spacing: .06em; }
.qk-status { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
.qk-chip { font-size: .75rem; color: var(--qk-muted); border: 1px solid var(--qk-border); border-radius: 999px;
  padding: 4px 10px; background: var(--qk-card); white-space: nowrap; }
.qk-chip b { font-weight: 500; color: var(--qk-text); }
.qk-dot { display: inline-block; width: 7px; height: 7px; border-radius: 50%; margin-right: 6px; vertical-align: 1px; }

/* ===== 股票標題與價格 ===== */
.qk-card { background: var(--qk-card); border: 1px solid var(--qk-border); border-radius: 12px;
  box-shadow: var(--qk-shadow); }
.qk-quote { padding: 18px 20px; display: flex; justify-content: space-between; align-items: flex-end; gap: 16px; flex-wrap: wrap; }
.qk-q-name { font-size: 1.4375rem; font-weight: 700; line-height: 1.3; color: var(--qk-text); }
.qk-q-tags { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 6px; }
.qk-tag { font-size: .75rem; border-radius: 6px; padding: 2px 8px; background: var(--qk-card2);
  border: 1px solid var(--qk-border); color: var(--qk-muted); white-space: nowrap; }
.qk-tag.mono { font-family: 'JetBrains Mono', monospace; }
.qk-q-meta { font-size: .75rem; color: var(--qk-faint); margin-top: 8px; }
.qk-q-right { text-align: right; }
.qk-price { font-family: 'JetBrains Mono', monospace; font-variant-numeric: tabular-nums; font-size: 2rem;
  font-weight: 600; line-height: 1.2; }
.qk-chg { font-family: 'JetBrains Mono', monospace; font-variant-numeric: tabular-nums; font-size: .95rem; font-weight: 500; }
.qk-ohlc { display: flex; gap: 6px 22px; flex-wrap: wrap; padding: 10px 20px; border-top: 1px solid var(--qk-border);
  font-size: .8125rem; color: var(--qk-muted); }
.qk-ohlc b { font-family: 'JetBrains Mono', monospace; font-variant-numeric: tabular-nums; font-weight: 500;
  color: var(--qk-text); margin-left: 6px; }
.up { color: var(--qk-up) !important; }
.down { color: var(--qk-down) !important; }
.flat { color: var(--qk-flat) !important; }

/* ===== 區塊標題 ===== */
.qk-h { display: flex; align-items: baseline; justify-content: space-between; gap: 10px; flex-wrap: wrap;
  margin: 10px 0 0 0; padding-bottom: 8px; border-bottom: 1px solid var(--qk-border); }
.qk-h-title { font-size: 1rem; font-weight: 600; color: var(--qk-text); line-height: 1.3; }
.qk-h-title em { font-style: normal; color: var(--qk-muted); font-weight: 500; margin-left: 6px; font-size: .875rem; }
.qk-h-note { font-size: .75rem; color: var(--qk-faint); }

/* ===== 市場指標卡（CSS Grid：桌面 4 欄 / 平板與手機 2 欄） ===== */
.qk-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }
.qk-stat { padding: 14px 16px; min-height: 88px; }
.qk-stat-label { font-size: .8125rem; color: var(--qk-muted); font-weight: 500; }
.qk-stat-value { font-family: 'JetBrains Mono', monospace; font-variant-numeric: tabular-nums; font-size: 1.375rem;
  font-weight: 600; line-height: 1.2; margin-top: 4px; color: var(--qk-text); white-space: nowrap; }
.qk-stat-sub { font-size: .75rem; color: var(--qk-faint); margin-top: 4px; }
.qk-stat-sub .qk-mono { color: var(--qk-muted); }
.qk-strip { display: flex; flex-wrap: wrap; gap: 6px 24px; padding: 10px 16px; margin-top: 12px;
  font-size: .8125rem; color: var(--qk-muted); }
.qk-strip b { font-family: 'JetBrains Mono', monospace; font-variant-numeric: tabular-nums; font-weight: 500; color: var(--qk-text); margin-left: 6px; }

/* ===== 技術面研究摘要 ===== */
.qk-outlook { padding: 18px 20px; }
.qk-o-head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.qk-o-verdict { font-size: 1.25rem; font-weight: 700; line-height: 1.3; }
.qk-o-badge { font-size: .72rem; color: var(--qk-muted); border: 1px solid var(--qk-border); border-radius: 6px; padding: 1px 8px; }
.qk-o-summary { margin: 10px 0 12px 0; font-size: .9rem; line-height: 1.75; color: var(--qk-text); max-width: 980px; }
.qk-pills { display: flex; flex-wrap: wrap; gap: 6px; }
.qk-pill { display: inline-flex; align-items: center; font-size: .78rem; color: var(--qk-text); background: var(--qk-card2);
  border: 1px solid var(--qk-border); border-radius: 6px; padding: 3px 9px; white-space: nowrap; }
.qk-pill .qk-dot { width: 6px; height: 6px; }
.qk-o-cols { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px 28px; margin-top: 14px; }
.qk-o-item { font-size: .85rem; line-height: 1.65; padding: 8px 0; border-top: 1px dashed var(--qk-border); }
.qk-o-item b { display: block; font-size: .75rem; color: var(--qk-muted); font-weight: 600; letter-spacing: .04em; }
.qk-o-foot { font-size: .75rem; color: var(--qk-faint); margin-top: 12px; }

/* ===== Alert ===== */
.qk-alert { border-radius: 10px; padding: 14px 16px; border: 1px solid; display: flex; gap: 12px; align-items: flex-start; }
.qk-alert-icon { font-weight: 700; font-family: 'JetBrains Mono', monospace; width: 22px; height: 22px; border-radius: 50%;
  display: inline-flex; align-items: center; justify-content: center; flex: 0 0 22px; font-size: .8rem; color: #fff; }
.qk-alert-title { font-weight: 600; font-size: .95rem; line-height: 1.4; }
.qk-alert-msg { font-size: .85rem; line-height: 1.7; margin-top: 2px; }
.qk-alert.error { background: var(--qk-err-bg); border-color: var(--qk-err-border); }
.qk-alert.error .qk-alert-title, .qk-alert.error .qk-alert-msg { color: var(--qk-err-text); }
.qk-alert.error .qk-alert-icon { background: var(--qk-up); }
.qk-alert.warn { background: var(--qk-warn-bg); border-color: var(--qk-warn-border); }
.qk-alert.warn .qk-alert-title, .qk-alert.warn .qk-alert-msg { color: var(--qk-warn-text); }
.qk-alert.warn .qk-alert-icon { background: var(--qk-warn); }
.qk-alert.info { background: var(--qk-info-bg); border-color: var(--qk-info-border); }
.qk-alert.info .qk-alert-title, .qk-alert.info .qk-alert-msg { color: var(--qk-info-text); }
.qk-alert.info .qk-alert-icon { background: var(--qk-accent); }

/* ===== Expander / 資料表 ===== */
[data-testid="stExpander"] { background: var(--qk-card); border: 1px solid var(--qk-border) !important; border-radius: 12px; }
[data-testid="stExpander"] details { border: none !important; }
[data-testid="stExpander"] summary p { font-weight: 600; font-size: .9rem; }
[data-testid="stExpander"] summary:hover p { color: var(--qk-accent); }
.qk-table-wrap { max-height: 420px; overflow: auto; border: 1px solid var(--qk-border); border-radius: 8px; }
table.qk-table { border-collapse: collapse; width: 100%; font-size: .78rem; }
table.qk-table th { position: sticky; top: 0; background: var(--qk-card2); color: var(--qk-muted); font-weight: 600;
  text-align: right; padding: 8px 10px; border-bottom: 1px solid var(--qk-border); white-space: nowrap; z-index: 1; }
table.qk-table td { font-family: 'JetBrains Mono', monospace; font-variant-numeric: tabular-nums; text-align: right;
  padding: 6px 10px; border-bottom: 1px solid var(--qk-border); color: var(--qk-text); white-space: nowrap; }
table.qk-table th:first-child, table.qk-table td:first-child { text-align: left; position: sticky; left: 0;
  background: var(--qk-card); }
table.qk-table th:first-child { background: var(--qk-card2); z-index: 2; }
table.qk-table tr:hover td { background: var(--qk-card2); }

/* ===== 圖表容器 ===== */
[data-testid="stPlotlyChart"] { background: var(--qk-plot-bg); border: 1px solid var(--qk-border); border-radius: 12px;
  padding: 6px 4px 2px 4px; box-shadow: var(--qk-shadow); }

/* ===== 頁尾 ===== */
.qk-footer { border-top: 1px solid var(--qk-border); margin-top: 18px; padding-top: 14px; font-size: .75rem;
  color: var(--qk-faint); display: flex; flex-wrap: wrap; gap: 4px 18px; justify-content: space-between; }

/* ===== 響應式：平板 768–1199px ===== */
@media (max-width: 1199px) {
  .qk-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  div[data-testid="stHorizontalBlock"]:has(.qk-ma-val) > div {
    flex: 1 1 calc(33.333% - 8px) !important; min-width: calc(33.333% - 8px) !important; width: auto !important; }
}
/* ===== 響應式：手機 ≤767px ===== */
@media (max-width: 767px) {
  .block-container, [data-testid="stMainBlockContainer"] { padding: 3.2rem 14px 1.5rem 14px !important; }
  .qk-brand { font-size: 1.2rem; }
  .qk-quote { padding: 14px 16px; align-items: flex-start; }
  .qk-q-right { text-align: left; }
  .qk-q-name { font-size: 1.25rem; }
  .qk-price { font-size: 1.625rem; }
  .qk-ohlc { padding: 10px 16px; gap: 4px 16px; }
  .qk-stat-value { font-size: 1.2rem; }
  .qk-o-cols { grid-template-columns: 1fr; }
  div[role="radiogroup"] { display: flex; width: 100%; }
  div[role="radiogroup"] label { padding: 0 13px; min-height: 40px; }
  div[data-testid="stHorizontalBlock"]:has(.qk-ma-val) > div {
    flex: 1 1 calc(33.333% - 8px) !important; min-width: calc(33.333% - 8px) !important; width: auto !important; }
  div[data-testid="stHorizontalBlock"]:has(.qk-ma-val) [data-testid="stCheckbox"] label { min-height: 40px; }
  /* 觸控裝置無法 hover：隱藏 Tooltip 圖示，色塊改為卡片左側色條，讓標籤完整顯示 */
  div[data-testid="stHorizontalBlock"]:has(.qk-ma-val) [data-testid="stTooltipIcon"],
  div[data-testid="stHorizontalBlock"]:has(.qk-ma-val) [data-testid="stTooltipHoverTarget"] { display: none !important; }
  div[data-testid="stHorizontalBlock"]:has(.qk-ma-val) [data-testid="stCheckbox"] [data-testid="stMarkdownContainer"] p::before { display: none; }
  div[data-testid="stHorizontalBlock"]:has(.qk-ma-val) > div { border-left: 3px solid var(--qk-ma-cur) !important; padding-left: 10px; }
  .qk-ma-val { padding-left: 0; }
  [data-testid="stPlotlyChart"] { padding: 2px 0; }
}
</style>
"""
    css = (css.replace("__ROOT_VARS__", root_vars)
              .replace("__MA_VARS__", ma_vars)
              .replace("__MA_SWATCHES__", ma_swatches))
    st.markdown(css, unsafe_allow_html=True)


def esc(s) -> str:
    """HTML 跳脫，避免股票名稱等外部文字破壞版面"""
    return html.escape(str(s))


def alert(kind: str, title: str, msg: str, detail: str = None) -> None:
    """
    一般使用者看得懂的中文提示框。kind = error / warn / info
    detail：原始例外訊息，收進可展開的「技術細節」，不直接把 traceback 丟給使用者
    """
    icon = {"error": "!", "warn": "!", "info": "i"}.get(kind, "i")
    st.markdown(
        f'<div class="qk-alert {kind}"><span class="qk-alert-icon">{icon}</span>'
        f'<div><div class="qk-alert-title">{title}</div><div class="qk-alert-msg">{msg}</div></div></div>',
        unsafe_allow_html=True,
    )
    if detail:
        with st.expander("技術細節（供程式除錯）"):
            st.code(detail, language=None)


def section_header(title: str, sub: str = "", note: str = "") -> None:
    """區塊標題：深灰標題＋淡灰分隔線，不使用漸層裝飾"""
    sub_html = f"<em>{sub}</em>" if sub else ""
    st.markdown(
        f'<div class="qk-h"><div class="qk-h-title">{title}{sub_html}</div>'
        f'<div class="qk-h-note">{note}</div></div>',
        unsafe_allow_html=True,
    )


# ------------------------------------------------------------------
# 3. 資料爬取與錯誤處理
# ------------------------------------------------------------------
def resolve_symbol(raw: str) -> list:
    """
    將使用者輸入轉成 Yahoo Finance 代碼候選清單：
      - 純數字（如 2330、0050）→ 先試上市 .TW，再試上櫃 .TWO
      - 已帶後綴或英文代碼（AAPL、^TWII、BTC-USD、2330.TW）→ 直接使用
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
def fetch_history(symbol: str, interval: str, period: str, adjusted: bool):
    """
    實際向 Yahoo Finance 爬取歷史資料（快取 10 分鐘，避免重複請求被限流）。
    adjusted=True 時使用還原權息價格（auto_adjust）。
    回傳 (DataFrame, 抓取時間字串)
    """
    tk = yf.Ticker(symbol)
    df = tk.history(period=period, interval=interval, auto_adjust=adjusted, actions=False, timeout=20)
    return df, datetime.now().strftime("%Y-%m-%d %H:%M")


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_meta(symbol: str) -> dict:
    """
    取得股票名稱、市場、幣別：
      - 台股：名稱優先用內建中文對照表，市場由 .TW / .TWO 判斷
      - 其他：嘗試向 Yahoo 查詢（失敗時退回代碼本身）
    """
    base = symbol.replace(".TWO", "").replace(".TW", "")
    meta = {"name": NAME_MAP.get(base, base), "market": "", "currency": ""}
    if symbol.endswith(".TWO"):
        meta.update(market="台灣上櫃（TPEx）", currency="TWD")
    elif symbol.endswith(".TW"):
        meta.update(market="台灣上市（TWSE）", currency="TWD")
    elif symbol.startswith("^"):
        meta["market"] = "指數"
    elif symbol.endswith("-USD"):
        meta.update(market="加密貨幣", currency="USD")
    need_info = (base not in NAME_MAP) or not symbol.endswith((".TW", ".TWO"))
    if need_info:
        try:
            info = yf.Ticker(symbol).info or {}
            if base not in NAME_MAP:
                meta["name"] = info.get("shortName") or info.get("longName") or base
            if not meta["market"]:
                meta["market"] = info.get("fullExchangeName") or info.get("exchange") or "海外市場"
            if not meta["currency"]:
                meta["currency"] = info.get("currency") or ""
        except Exception:
            if not meta["market"]:
                meta["market"] = "海外市場"
    return meta


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
    """把例外分類成使用者看得懂的錯誤類型"""
    text = f"{type(exc).__name__}: {exc}"
    low = text.lower()
    if "ratelimit" in low or "too many" in low or "429" in low:
        return ("ratelimit", text)
    if "timed out" in low or "timeout" in low or "(28)" in low:
        return ("timeout", text)
    if any(k in low for k in ["connection", "resolve", "network", "curl", "ssl", "proxy", "unreachable"]):
        return ("network", text)
    return ("unknown", text)


def load_data(raw: str, period_key: str, adjusted: bool):
    """
    依使用者輸入與週期載入資料。
    回傳 (df, symbol, fetched_at, error)；error 為 None 代表成功，否則為 (錯誤類型, 詳細訊息)
    """
    cfg = PERIODS[period_key]
    candidates = resolve_symbol(raw)
    if not candidates:
        return None, None, None, ("empty", "")

    last_exc = None
    for sym in candidates:
        df = None
        for attempt in range(3):  # Yahoo 偶發回傳空資料或連線 / crumb 錯誤：最多重試 2 次（間隔遞增）
            try:
                df, fetched_at = fetch_history(sym, cfg["interval"], cfg["period"], adjusted)
                if df is not None and not df.empty:
                    break
            except Exception as e:  # 網路斷線、逾時、限流、Yahoo 回應異常等
                last_exc = e
                df = None
            if attempt < 2:
                try:  # 只清除這組參數的快取（舊版 Streamlit 不支援帶參數時改清全部）
                    fetch_history.clear(sym, cfg["interval"], cfg["period"], adjusted)
                except TypeError:
                    fetch_history.clear()
                time.sleep(0.8 * (attempt + 1))
        if df is None:
            continue
        if df is not None and not df.empty and df["Close"].notna().sum() > 0:
            df = clean_ohlcv(df)
            if cfg["resample"]:
                df = resample_ohlcv(df, cfg["resample"])
            return df, sym, fetched_at, None

    # 全部候選代碼都失敗 → 判斷原因
    if last_exc is not None:
        kind, detail = classify_error(last_exc)
        if kind == "unknown" or kind == "network":
            # 例外可能只是單一代碼查無資料；若網路其實正常就改判為查無代碼
            if internet_ok():
                return None, None, None, ("notfound", ", ".join(candidates) + f"\n{detail}")
        return None, None, None, (kind, detail)
    if not internet_ok():
        return None, None, None, ("network", "無法連線到 query1.finance.yahoo.com")
    return None, None, None, ("notfound", ", ".join(candidates))


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
#    所有指標都以「目前週期」的 K 棒計算：週 K 的 MA20 = 最近 20 根週 K 收盤平均
# ------------------------------------------------------------------
def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    close, high, low = df["Close"], df["High"], df["Low"]

    # 4-1 移動平均線
    for n in MA_LIST:
        df[f"MA{n}"] = close.rolling(n).mean()

    # 4-2 漲跌、漲跌幅、振幅（相對前一根 K 棒）
    prev_close = close.shift(1)
    df["Chg"] = close - prev_close
    df["ChgPct"] = df["Chg"] / prev_close * 100
    df["Amp"] = (high - low) / prev_close * 100

    # 4-3 均量線
    df["MV5"] = df["Volume"].rolling(5).mean()
    df["MV20"] = df["Volume"].rolling(20).mean()

    # 4-4 MACD(12, 26, 9)：DIF = EMA12 - EMA26；MACD(訊號線) = DIF 的 9 期 EMA；OSC = DIF - MACD
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    df["DIF"] = ema12 - ema26
    df["MACD"] = df["DIF"].ewm(span=9, adjust=False).mean()
    df["OSC"] = df["DIF"] - df["MACD"]
    df.loc[df.index[:33], ["DIF", "MACD", "OSC"]] = np.nan  # 暖機期（26+9-2 根）不顯示，避免失真

    # 4-5 RSI（Wilder 平滑法），提供 RSI6（快）與 RSI14（標準）
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    for n in (6, 14):
        avg_gain = gain.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
        avg_loss = loss.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        rsi = 100 - 100 / (1 + rs)
        rsi = rsi.where(avg_loss != 0, 100.0)                         # 完全沒跌 → RSI = 100
        rsi = rsi.where(~((avg_gain == 0) & (avg_loss == 0)), 50.0)   # 完全沒動 → 50
        rsi = rsi.where(avg_gain.notna())                              # 暖機期維持空值
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
    sign = pd.Series(np.sign(diff.values)).replace(0, np.nan).ffill().fillna(0).values  # 相等時沿用前一狀態
    n = len(sign)
    for i in range(n - 1, max(0, n - 1 - lookback), -1):
        if sign[i] != 0 and sign[i - 1] != 0 and sign[i] != sign[i - 1]:
            return ("golden" if sign[i] > 0 else "death", n - 1 - i)
    return None


def cross_points(view: pd.DataFrame):
    """回傳顯示區間內 MA5 × MA20 的黃金交叉與死亡交叉布林遮罩"""
    sgn = np.sign(view["MA5"] - view["MA20"])
    prev = sgn.shift(1)
    return (sgn > 0) & (prev < 0), (sgn < 0) & (prev > 0)


# ------------------------------------------------------------------
# 5. 規則式技術診斷（純 Python 規則判讀，不是大型語言模型）
# ------------------------------------------------------------------
def diagnose(df: pd.DataFrame, unit: str) -> dict:
    """
    蒐集多項技術訊號，每項給予方向（+1 偏多 / -1 偏空 / 0 中性）與權重，
    再依多空權重得出：偏多趨勢、偏空趨勢、盤整、訊號分歧、資料不足。
    不輸出單一分數，避免被誤解為獲利機率或投資建議。
    """
    last = df.iloc[-1]
    signals = []  # (標籤, 方向, 權重)
    texts = {"trend": "", "momentum": "", "rsi": "", "risk": ""}

    # 資料不足判斷：至少要有 MA20 與 MACD
    if len(df) < 35 or pd.isna(last["MA20"]) or pd.isna(last["OSC"]):
        return dict(verdict="資料不足", vclass="flat", pills=[("資料筆數不足", 0)],
                    summary=f"目前僅有 {len(df)} 根 {unit}，不足以計算 MA20 與 MACD 等指標（約需 35 根以上），"
                            "無法進行可靠判讀。請切換較長週期或較長資料範圍。",
                    items=[], bull=0, bear=0)

    # 5-1 均線排列（MA5 > MA10 > MA20 > MA60 為多頭排列）
    align_set = [n for n in (5, 10, 20, 60) if pd.notna(last[f"MA{n}"])]
    vals = [last[f"MA{n}"] for n in align_set]
    if len(align_set) >= 3 and all(vals[i] > vals[i + 1] for i in range(len(vals) - 1)):
        signals.append(("MA 多頭排列", 1, 2.0))
        texts["trend"] = f"短中長期均線依序向上排列（{' > '.join(f'MA{n}' for n in align_set)}），趨勢結構偏多。"
    elif len(align_set) >= 3 and all(vals[i] < vals[i + 1] for i in range(len(vals) - 1)):
        signals.append(("MA 空頭排列", -1, 2.0))
        texts["trend"] = f"均線由短到長依序向下排列（{' < '.join(f'MA{n}' for n in align_set)}），趨勢結構偏空。"
    else:
        signals.append(("均線糾結", 0, 0))
        texts["trend"] = "各期均線交錯，尚未形成明確排列，方向未明。"

    # 5-2 短期交叉：MA5 × MA20
    cross = find_last_cross(df["MA5"], df["MA20"], lookback=10)
    if cross:
        kind, ago = cross
        when = "最新一根" if ago == 0 else f"{ago} 根前"
        if kind == "golden":
            signals.append((f"MA5 黃金交叉 MA20（{when}）", 1, 1.5))
            texts["trend"] += f" {when} MA5 向上穿越 MA20，短線轉強。"
        else:
            signals.append((f"MA5 死亡交叉 MA20（{when}）", -1, 1.5))
            texts["trend"] += f" {when} MA5 向下跌破 MA20，短線轉弱。"
    else:
        above = last["MA5"] > last["MA20"]
        signals.append((f"MA5 {'高於' if above else '低於'} MA20", 1 if above else -1, 1.0))

    # 5-3 中期交叉：MA20 × MA60
    cross2 = find_last_cross(df["MA20"], df["MA60"], lookback=20)
    if cross2:
        kind, ago = cross2
        when = "最新一根" if ago == 0 else f"{ago} 根前"
        signals.append((f"MA20 {'黃金' if kind == 'golden' else '死亡'}交叉 MA60（{when}）",
                        1 if kind == "golden" else -1, 1.0))

    # 5-4 價格與 MA60、MA20 斜率
    if pd.notna(last["MA60"]):
        above60 = last["Close"] >= last["MA60"]
        signals.append((f"收盤{'站上' if above60 else '跌破'} MA60", 1 if above60 else -1, 1.0))
    if len(df) > 5 and pd.notna(df["MA20"].iloc[-6]):
        slope_up = last["MA20"] > df["MA20"].iloc[-6]
        signals.append((f"MA20 {'上彎' if slope_up else '下彎'}", 1 if slope_up else -1, 1.0))

    # 5-5 MACD 動能
    prev_osc = df["OSC"].iloc[-2]
    if last["OSC"] > 0:
        turned = pd.notna(prev_osc) and prev_osc <= 0
        signals.append(("MACD 柱翻紅" if turned else "MACD 偏多", 1, 1.0))
        texts["momentum"] = (f"MACD 柱狀體{'剛由負轉正' if turned else '維持正值'}"
                             f"（OSC {last['OSC']:+.2f}），短期動能偏多。")
    else:
        turned = pd.notna(prev_osc) and prev_osc > 0
        signals.append(("MACD 柱翻綠" if turned else "MACD 偏空", -1, 1.0))
        texts["momentum"] = (f"MACD 柱狀體{'剛由正轉負' if turned else '維持負值'}"
                             f"（OSC {last['OSC']:+.2f}），短期動能疲弱。")

    # 5-6 RSI：超買超賣只視為風險提示，不等同買賣訊號
    rsi = last["RSI14"]
    if pd.notna(rsi):
        if rsi >= 70:
            signals.append((f"RSI 超買 {rsi:.1f}", 0, 0))
            texts["rsi"] = f"RSI14 為 {rsi:.1f}，位於超買區（≥70），短線可能出現拉回或整理。"
        elif rsi <= 30:
            signals.append((f"RSI 超賣 {rsi:.1f}", 0, 0))
            texts["rsi"] = f"RSI14 為 {rsi:.1f}，位於超賣區（≤30），可能出現技術性反彈，但超賣不等於買進訊號。"
        else:
            signals.append((f"RSI 中性 {rsi:.1f}", 0, 0))
            texts["rsi"] = f"RSI14 為 {rsi:.1f}，介於 30–70 的中性區間。"

    # 5-7 KD 近 3 根交叉
    if pd.notna(last["K"]) and pd.notna(last["D"]):
        kd = find_last_cross(df["K"], df["D"], lookback=3)
        if kd:
            signals.append((f"KD {'黃金' if kd[0] == 'golden' else '死亡'}交叉", 1 if kd[0] == "golden" else -1, 0.5))

    bull = sum(w for _, d, w in signals if d > 0)
    bear = sum(w for _, d, w in signals if d < 0)
    net = bull - bear

    # 5-8 綜合判讀
    if bull >= 3 and bear >= 3:
        verdict, vclass = "訊號分歧", "flat"
        summary = "多空訊號同時存在且力道相近，趨勢與動能指標彼此矛盾，宜等待方向更明確。"
    elif net >= 3:
        verdict, vclass = "偏多趨勢", "up"
        summary = "多數趨勢與動能指標偏多，技術結構相對強勢。"
    elif net <= -3:
        verdict, vclass = "偏空趨勢", "down"
        summary = "多數趨勢與動能指標偏空，技術結構相對弱勢。"
    else:
        verdict, vclass = "盤整", "flat"
        lean = "多方略占優勢" if net > 0 else ("空方略占優勢" if net < 0 else "多空力道均衡")
        summary = f"訊號強度不足以形成明確趨勢，{lean}，偏向區間整理。"
    if texts["rsi"] and pd.notna(rsi) and (rsi >= 70 or rsi <= 30):
        summary += " " + texts["rsi"]

    # 5-9 風險與不確定性
    risks = []
    if pd.notna(rsi) and rsi <= 30 and vclass == "down":
        risks.append("超賣區的反彈可能只是短暫修正，尚不足以確認趨勢反轉。")
    if pd.notna(rsi) and rsi >= 70 and vclass == "up":
        risks.append("價格已進入超買區，追價風險升高。")
    if verdict == "訊號分歧":
        risks.append("指標互相矛盾時，假突破或假跌破的機率較高。")
    if pd.isna(last["MA60"]):
        risks.append("MA60 資料不足，中期趨勢判斷可信度較低。")
    risks.append("技術指標反映的是歷史價格，無法預測突發消息與基本面變化。")
    texts["risk"] = " ".join(risks)

    pills = [(label, d) for label, d, _ in signals]
    items = [("均線結構", texts["trend"]), ("MACD 動能", texts["momentum"]),
             ("RSI 位置", texts["rsi"] or "RSI 資料不足。"), ("風險與不確定性", texts["risk"])]
    return dict(verdict=verdict, vclass=vclass, pills=pills, summary=summary, items=items, bull=bull, bear=bear)


# ------------------------------------------------------------------
# 6. 圖表繪製（plotly.graph_objects）
# ------------------------------------------------------------------
def build_chart(view: pd.DataFrame, t: dict, theme_key: str, selected_ma: list, sub_ind: str,
                fmt: str, vol_div: float, vol_unit: str, show_bb: bool, show_cross: bool,
                show_hl: bool, uirev: str, mobile: bool) -> go.Figure:
    """
    主圖：K 線 + 均線（+布林通道、交叉標記、最高最低標示）
    副圖 1：成交量（紅漲綠跌）+ 均量線
    副圖 2：MACD / RSI / KD
    所有 trace 共用同一條 x 軸（各自使用不同 y 軸 domain），時間點天生精準對齊，
    十字準心也能貫穿全部圖層；這比 make_subplots 的多條 x 軸更適合 x unified hover。
    """
    x = view.index.strftime(fmt).tolist()  # 類別軸：自動跳過假日與非交易時段，K 棒不會出現缺口
    up_c, down_c = t["up"], t["down"]
    bar_colors = [up_c if u else down_c for u in (view["Close"] >= view["Open"]).values]
    has_sub = sub_ind != "不顯示"
    ma_col = lambda n: MA_CONFIG[n][theme_key]

    # 版面高度分配：K 線約 60%、成交量 15%、副圖 20%，其餘為間距
    if has_sub:
        dom_main, dom_vol, dom_sub = [0.40, 1.0], [0.225, 0.375], [0.0, 0.20]
        height = 560 if mobile else 740
    else:
        dom_main, dom_vol, dom_sub = [0.24, 1.0], [0.0, 0.20], [0.0, 0.0]
        height = 480 if mobile else 620

    fig = go.Figure()

    # ---- 6-1 K 線（紅漲綠跌）----
    fig.add_trace(go.Candlestick(
        x=x, open=view["Open"], high=view["High"], low=view["Low"], close=view["Close"],
        increasing=dict(line=dict(color=up_c, width=1), fillcolor=up_c),
        decreasing=dict(line=dict(color=down_c, width=1), fillcolor=down_c),
        name="K線", hoverinfo="skip", yaxis="y", xaxis="x", showlegend=False,
    ))

    # ---- 6-2 隱形 Hover 資料點：每根 K 棒精準對應自己的 OHLCV（不會顯示錯誤 K 棒） ----
    golden, death = cross_points(view)
    sig_text = []
    for i in range(len(view)):
        tail = f"{view['ChgPct'].iloc[i]:+.2f}%" if pd.notna(view["ChgPct"].iloc[i]) else "—"
        if show_cross and golden.iloc[i]:
            tail += "）<br><b>▲ 黃金交叉</b> MA5 向上穿越 MA20"
        elif show_cross and death.iloc[i]:
            tail += "）<br><b>▼ 死亡交叉</b> MA5 向下跌破 MA20"
        else:
            tail += "）"
        sig_text.append(tail)
    custom = np.column_stack([
        view["Open"], view["High"], view["Low"], view["Close"], view["Volume"] / vol_div, view["Chg"].fillna(0),
    ])
    fig.add_trace(go.Scatter(
        x=x, y=view["Close"], mode="markers", marker=dict(size=1, opacity=0),
        customdata=custom, text=sig_text, name="行情", yaxis="y", xaxis="x", showlegend=False,
        hovertemplate=(
            "開 <b>%{customdata[0]:,.2f}</b>　高 <b>%{customdata[1]:,.2f}</b><br>"
            "低 <b>%{customdata[2]:,.2f}</b>　收 <b>%{customdata[3]:,.2f}</b><br>"
            f"量 <b>%{{customdata[4]:,.0f}}</b> {vol_unit}　"
            "漲跌 <b>%{customdata[5]:+,.2f}</b>（%{text}<extra></extra>"
        ),
    ))

    # ---- 6-3 布林通道（選用）：柔和紫灰虛線＋極淡填色 ----
    if show_bb:
        bb_color = "#8E8CB8" if theme_key == "light" else "#9B99C6"
        fill = "rgba(142,140,184,0.07)" if theme_key == "light" else "rgba(155,153,198,0.07)"
        fig.add_trace(go.Scatter(x=x, y=view["BB_UP"], name="布林上軌", line=dict(color=bb_color, width=1, dash="dot"),
                                 yaxis="y", xaxis="x", showlegend=False, hovertemplate="%{y:,.2f}"))
        fig.add_trace(go.Scatter(x=x, y=view["BB_LOW"], name="布林下軌", line=dict(color=bb_color, width=1, dash="dot"),
                                 fill="tonexty", fillcolor=fill, yaxis="y", xaxis="x", showlegend=False,
                                 hovertemplate="%{y:,.2f}"))

    # ---- 6-4 均線疊加（依勾選動態加入；已計算好的欄位，切換不會重新下載資料）----
    for n in selected_ma:
        col = view[f"MA{n}"]
        if col.notna().sum() == 0:
            continue
        fig.add_trace(go.Scatter(
            x=x, y=col, name=MA_CONFIG[n]["name"], mode="lines",
            line=dict(color=ma_col(n), width=MA_CONFIG[n]["width"]), yaxis="y", xaxis="x",
            showlegend=False, hovertemplate="%{y:,.2f}",
        ))

    # ---- 6-5 黃金 / 死亡交叉標記（預設關閉；標記放在 K 棒外側避免重疊）----
    if show_cross:
        pad = float((view["High"] - view["Low"]).mean()) * 0.9
        if golden.any():
            idx = np.where(golden.values)[0]
            fig.add_trace(go.Scatter(
                x=[x[i] for i in idx], y=view["Low"].values[idx] - pad, mode="markers",
                marker=dict(symbol="triangle-up", size=9, color=up_c, line=dict(width=1, color=t["plot_bg"])),
                name="黃金交叉", yaxis="y", xaxis="x", showlegend=False, hoverinfo="skip",
            ))
        if death.any():
            idx = np.where(death.values)[0]
            fig.add_trace(go.Scatter(
                x=[x[i] for i in idx], y=view["High"].values[idx] + pad, mode="markers",
                marker=dict(symbol="triangle-down", size=9, color=down_c, line=dict(width=1, color=t["plot_bg"])),
                name="死亡交叉", yaxis="y", xaxis="x", showlegend=False, hoverinfo="skip",
            ))

    # ---- 6-6 區間最高 / 最低價標示（僅顯示所選區間內的極值）----
    annotations = []
    if show_hl and len(view) > 1:
        hi_pos = int(np.argmax(view["High"].values))
        lo_pos = int(np.argmin(view["Low"].values))
        for pos, val, label, col, ay in ((hi_pos, view["High"].iloc[hi_pos], "最高", up_c, -24),
                                         (lo_pos, view["Low"].iloc[lo_pos], "最低", down_c, 24)):
            # 極值靠近左右邊緣時，標籤往內側對齊，避免超出圖表
            anchor = "left" if pos < len(view) * 0.12 else ("right" if pos > len(view) * 0.88 else "center")
            annotations.append(dict(
                x=x[pos], y=val, xref="x", yref="y", text=f"{label} {val:,.2f}", showarrow=True, arrowhead=0,
                xanchor=anchor,
                arrowwidth=1, arrowcolor=col, ax=0, ay=ay, font=dict(color=col, size=11, family="JetBrains Mono"),
                bgcolor=t["plot_bg"], borderpad=2,
            ))

    # ---- 6-7 成交量（紅漲綠跌）+ 均量線 ----
    fig.add_trace(go.Bar(
        x=x, y=view["Volume"] / vol_div, marker=dict(color=bar_colors, line=dict(width=0)), opacity=0.75,
        name="成交量", yaxis="y2", xaxis="x", showlegend=False, hovertemplate=f"%{{y:,.0f}} {vol_unit}",
    ))
    fig.add_trace(go.Scatter(x=x, y=view["MV5"] / vol_div, name="均量5", line=dict(color=ma_col(5), width=1.1),
                             yaxis="y2", xaxis="x", showlegend=False, hovertemplate="%{y:,.0f}"))
    fig.add_trace(go.Scatter(x=x, y=view["MV20"] / vol_div, name="均量20", line=dict(color=ma_col(20), width=1.1),
                             yaxis="y2", xaxis="x", showlegend=False, hovertemplate="%{y:,.0f}"))

    # ---- 6-8 副圖技術指標 ----
    shapes, sub_title = [], ""
    gold, blue, orange, purple = ma_col(5), ma_col(20), ma_col(10), ma_col(120)
    if sub_ind == "MACD":
        sub_title = "MACD (12, 26, 9)　DIF ━ 金　MACD ━ 藍"
        osc_colors = [up_c if v >= 0 else down_c for v in view["OSC"].fillna(0)]
        fig.add_trace(go.Bar(x=x, y=view["OSC"], marker=dict(color=osc_colors, line=dict(width=0)), opacity=0.7,
                             name="OSC", yaxis="y3", xaxis="x", showlegend=False, hovertemplate="%{y:,.3f}"))
        fig.add_trace(go.Scatter(x=x, y=view["DIF"], name="DIF", line=dict(color=gold, width=1.3),
                                 yaxis="y3", xaxis="x", showlegend=False, hovertemplate="%{y:,.3f}"))
        fig.add_trace(go.Scatter(x=x, y=view["MACD"], name="MACD", line=dict(color=blue, width=1.3),
                                 yaxis="y3", xaxis="x", showlegend=False, hovertemplate="%{y:,.3f}"))
    elif sub_ind == "RSI":
        sub_title = "RSI　RSI6 ━ 橙　RSI14 ━ 紫"
        fig.add_trace(go.Scatter(x=x, y=view["RSI6"], name="RSI6", line=dict(color=orange, width=1.3),
                                 yaxis="y3", xaxis="x", showlegend=False, hovertemplate="%{y:.2f}"))
        fig.add_trace(go.Scatter(x=x, y=view["RSI14"], name="RSI14", line=dict(color=purple, width=1.3),
                                 yaxis="y3", xaxis="x", showlegend=False, hovertemplate="%{y:.2f}"))
        shapes.append(dict(type="rect", xref="paper", x0=0, x1=1, yref="y3", y0=30, y1=70,
                           fillcolor=t["accent"], opacity=0.04, line=dict(width=0), layer="below"))
        for lvl, col in ((70, up_c), (30, down_c)):
            shapes.append(dict(type="line", xref="paper", x0=0, x1=1, yref="y3", y0=lvl, y1=lvl,
                               line=dict(color=col, width=1, dash="dot")))
    elif sub_ind == "KD":
        sub_title = "KD (9, 3, 3)　K ━ 金　D ━ 藍"
        fig.add_trace(go.Scatter(x=x, y=view["K"], name="K", line=dict(color=gold, width=1.3),
                                 yaxis="y3", xaxis="x", showlegend=False, hovertemplate="%{y:.2f}"))
        fig.add_trace(go.Scatter(x=x, y=view["D"], name="D", line=dict(color=blue, width=1.3),
                                 yaxis="y3", xaxis="x", showlegend=False, hovertemplate="%{y:.2f}"))
        for lvl, col in ((80, up_c), (20, down_c)):
            shapes.append(dict(type="line", xref="paper", x0=0, x1=1, yref="y3", y0=lvl, y1=lvl,
                               line=dict(color=col, width=1, dash="dot")))

    # 副圖左上角小標題
    annotations.append(dict(text=f"成交量（{vol_unit}）　均量5 / 均量20", xref="paper", yref="paper", x=0.004,
                            y=dom_vol[1], xanchor="left", yanchor="top", showarrow=False,
                            font=dict(size=11, color=t["muted"])))
    if has_sub:
        annotations.append(dict(text=sub_title, xref="paper", yref="paper", x=0.004, y=dom_sub[1],
                                xanchor="left", yanchor="top", showarrow=False, font=dict(size=11, color=t["muted"])))

    # ---- 6-9 座標軸（十字準心）----
    axis_common = dict(
        gridcolor=t["grid"], zeroline=False, showline=False, color=t["muted"],
        showspikes=True, spikemode="across", spikesnap="cursor", spikethickness=1,
        spikedash="dot", spikecolor=t["spike"], tickfont=dict(family="JetBrains Mono", size=11, color=t["muted"]),
    )
    n_bars = len(x)
    # 時間軸刻度：平均挑選少量刻度並使用較短日期格式，避免手機上日期互相重疊
    intraday_fmt = "%H" in fmt
    n_ticks = (3 if intraday_fmt else 4) if mobile else (7 if intraday_fmt else 9)
    if mobile and fmt == "%Y/%m/%d":
        short_fmt = "%y/%m/%d"
    else:
        short_fmt = fmt
    tick_idx = sorted(set(np.linspace(0, n_bars - 1, min(n_ticks, n_bars)).round().astype(int).tolist()))
    tick_vals = [x[i] for i in tick_idx]
    tick_text = [view.index[i].strftime(short_fmt) for i in tick_idx]
    layout = dict(
        height=height,
        margin=dict(l=6, r=6, t=26, b=6),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor=t["plot_bg"],
        font=dict(family="JetBrains Mono, Noto Sans TC, sans-serif", color=t["text"], size=12),
        hovermode="x unified",
        hoversubplots="axis",          # 游標在任一子圖時，同一時間點的所有數值一起顯示
        hoverdistance=-1,
        spikedistance=-1,              # 十字線永遠跟隨游標
        dragmode="zoom",               # 預設為框選區域縮放
        uirevision=uirev,              # 勾選均線時保持目前縮放位置
        showlegend=False,
        bargap=0.25,
        hoverlabel=dict(bgcolor=t["hover_bg"], bordercolor=t["hover_border"],
                        font=dict(family="JetBrains Mono, Noto Sans TC", color=t["text"], size=12)),
        xaxis=dict(
            **axis_common, type="category", anchor="y3" if has_sub else "y2",
            rangeslider=dict(visible=False), tickangle=0,
            # 手機：固定少量刻度＋短日期；桌面：自動刻度（縮放時會自動補上更細的日期）
            **(dict(tickmode="array", tickvals=tick_vals, ticktext=tick_text) if mobile else dict(nticks=n_ticks)),
            categoryorder="array", categoryarray=x, range=[-0.8, n_bars - 0.2],
        ),
        yaxis=dict(**axis_common, domain=dom_main, side="right", tickformat=",.2f"),
        yaxis2=dict(**axis_common, domain=dom_vol, side="right", tickformat="~s", rangemode="tozero"),
        annotations=annotations,
        shapes=shapes,
    )
    if has_sub:
        layout["yaxis3"] = dict(**axis_common, domain=dom_sub, side="right",
                                range=[0, 100] if sub_ind in ("RSI", "KD") else None)
    fig.update_layout(**layout)
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


def is_mobile_client() -> bool:
    """以 User-Agent 判斷是否為手機（Streamlit ≥ 1.37 提供 st.context；舊版一律視為桌面）"""
    try:
        ua = st.context.headers.get("User-Agent", "") or ""
    except Exception:
        return False
    return ("Mobi" in ua or "iPhone" in ua or "Android" in ua) and "iPad" not in ua


def set_ticker_from_quickpick() -> None:
    """熱門快選的 callback：選擇後自動帶入代碼輸入框"""
    choice = st.session_state.get("quickpick")
    if choice and choice in POPULAR:
        st.session_state.ticker = POPULAR[choice]


def reload_data() -> None:
    """清除行情快取，讓下一次執行重新向 Yahoo Finance 抓取"""
    fetch_history.clear()


def history_table_html(table: pd.DataFrame, vol_label: str) -> str:
    """把歷史資料轉成主題一致的 HTML 表格（深淺模式皆正確，數字統一格式）"""
    fmt = table.copy()
    for c in fmt.columns:
        if c == vol_label:
            fmt[c] = fmt[c].map(lambda v: "—" if pd.isna(v) else f"{v:,.0f}")
        elif c == "漲跌幅%":
            fmt[c] = fmt[c].map(lambda v: "—" if pd.isna(v) else f"{v:+.2f}")
        elif c == "漲跌":
            fmt[c] = fmt[c].map(lambda v: "—" if pd.isna(v) else f"{v:+,.2f}")
        else:
            fmt[c] = fmt[c].map(lambda v: "—" if pd.isna(v) else f"{v:,.2f}")
    return f'<div class="qk-table-wrap">{fmt.to_html(classes="qk-table", border=0, escape=True)}</div>'


# ==================================================================
# 8. 主程式
# ==================================================================
def main() -> None:
    # ---- 8-1 Session 預設值 ----
    if "ticker" not in st.session_state:
        st.session_state.ticker = "2330"
    mobile = is_mobile_client()

    # ---- 8-2 側邊欄 ----
    with st.sidebar:
        st.markdown('<div class="qk-side-brand">QUANTUM·K</div>'
                    '<div class="qk-side-sub">Financial Research Platform</div>', unsafe_allow_html=True)
        dark_mode = st.toggle("深色模式", value=False, key="dark_mode")
        theme_key = "dark" if dark_mode else "light"
        t = THEMES[theme_key]
        inject_css(t)

        st.markdown('<div class="qk-side-h">標的</div>', unsafe_allow_html=True)
        st.text_input("股票代碼", key="ticker",
                      help="台股直接輸入數字（如 2330、6488），系統自動判斷上市 .TW / 上櫃 .TWO；"
                           "美股輸入代碼（AAPL）；指數如 ^TWII；加密貨幣如 BTC-USD。")
        st.selectbox("熱門快選", options=list(POPULAR.keys()), index=None,
                     placeholder="選擇熱門標的…", key="quickpick", on_change=set_ticker_from_quickpick)

        st.markdown('<div class="qk-side-h">顯示</div>', unsafe_allow_html=True)
        bars = st.slider("顯示 K 棒數量", min_value=30, max_value=600, value=150, step=10, key="bars",
                         help="均線與指標以完整歷史資料計算後，再擷取最後 N 根顯示，長天期均線從畫面第一根就正確。")
        show_hl = st.checkbox("標示區間最高／最低價", value=True, key="show_hl")
        show_cross = st.checkbox("標示 MA5 × MA20 交叉訊號", value=False, key="show_cross")
        show_bb = st.checkbox("布林通道 (20, 2)", value=False, key="show_bb")
        adjusted = st.checkbox("還原權息價格", value=False, key="adjusted",
                               help="開啟後使用 Yahoo 的調整後價格（考慮除權息、分割）；"
                                    "預設為未還原的原始成交價，與券商報價一致。")

        st.markdown('<div class="qk-side-h">說明</div>', unsafe_allow_html=True)
        with st.expander("操作說明"):
            st.markdown(
                "- **框選**：在圖上拖曳即可區域放大\n"
                "- **滾輪**：縮放時間軸\n"
                "- **雙擊**或工具列 ⌂：還原圖表\n"
                "- **十字準心**：游標移動即時顯示 OHLCV\n"
                "- 顏色慣例：**紅漲綠跌**（台股慣例，海外標的同樣適用）"
            )
        st.caption("資料來源：Yahoo Finance（yfinance）。僅供學術研究，不構成投資建議。")

    # ---- 8-3 版面骨架：先建立容器，資料載入後再填入，維持「資訊 → 控制 → K 線」順序 ----
    nav_slot = st.container()
    quote_slot = st.container()

    # ---- 8-4 週期與副圖指標工具列 ----
    tb1, tb2 = st.columns([4, 1.3], vertical_alignment="bottom") if _ST_VER >= (1, 36) else st.columns([4, 1.3])
    with tb1:
        period_key = st.radio("K 線週期", list(PERIODS.keys()), index=4, horizontal=True,
                              key="period", label_visibility="collapsed")
    with tb2:
        sub_ind = st.selectbox("副圖指標", SUB_INDICATORS, index=0, key="sub_ind")
    cfg = PERIODS[period_key]
    intraday = cfg["interval"].endswith("m")

    # ---- 8-5 載入資料（含完整錯誤處理）----
    raw = st.session_state.ticker
    with st.spinner(f"正在從 Yahoo Finance 取得 {raw} 的{cfg['label']}資料…"):
        df, symbol, fetched_at, err = load_data(raw, period_key, adjusted)

    with nav_slot:
        status = (f'<span class="qk-chip"><span class="qk-dot" style="background:var(--qk-flat)"></span>'
                  f'{"盤中資料 · 可能延遲 15–20 分鐘" if intraday else "歷史收盤資料（非即時報價）"}</span>')
        upd = f'<span class="qk-chip">資料更新 <b class="qk-mono">{fetched_at}</b></span>' if fetched_at else ""
        st.markdown(
            f'<div class="qk-nav"><div><div class="qk-brand">QUANTUM<span>·</span>K</div>'
            f'<div class="qk-brand-sub">Financial Research Platform　｜　理財機器人期中報告</div></div>'
            f'<div class="qk-status"><span class="qk-chip">來源 <b>Yahoo Finance</b></span>{status}{upd}</div></div>',
            unsafe_allow_html=True,
        )

    if err is not None:
        kind, detail = err
        with quote_slot:
            if kind == "empty":
                alert("info", "請輸入股票代碼",
                      "於左側欄輸入代碼，例如 <b>2330</b>（台積電）、<b>6488</b>（上櫃）、<b>AAPL</b>、<b>^TWII</b>，"
                      "或從「熱門快選」選擇。")
            elif kind == "notfound":
                hint = ("分鐘線資料僅提供近 60 天（60 分線約 2 年），若該標的近期無交易將查無資料。"
                        if intraday else "")
                alert("error", f"查無此代碼：{esc(raw)}",
                      f"Yahoo Finance 沒有回傳這個代碼的資料。可能是代碼輸入錯誤、已下市或停止交易。{hint}<br>"
                      "建議：台股直接輸入數字代碼；美股使用英文代碼；或改用左側「熱門快選」。",
                      detail=detail)
            elif kind == "network":
                alert("error", "網路連線失敗",
                      "無法連線到 Yahoo Finance，請檢查網路（Wi-Fi、防火牆或 Proxy）後按「重新載入資料」。",
                      detail=detail)
            elif kind == "timeout":
                alert("error", "下載逾時",
                      "Yahoo Finance 回應過慢。請稍後重新載入，或切換較短的週期（例如日線）以減少資料量。",
                      detail=detail)
            elif kind == "ratelimit":
                alert("warn", "資料來源暫時限制請求",
                      "Yahoo Finance 暫時限制了請求頻率，請等待 1～2 分鐘後再重新載入。系統已啟用 10 分鐘快取以降低請求次數。",
                      detail=detail)
            else:
                alert("error", "資料讀取發生未預期的錯誤",
                      "資料來源可能暫時無法使用，請稍後重新載入或更換股票代碼。", detail=detail)
            st.button("重新載入資料", on_click=reload_data, type="primary")
        st.stop()

    if len(df) < 2:
        with quote_slot:
            alert("warn", "資料筆數不足",
                  f"{esc(symbol)} 在「{cfg['label']}」只取得 {len(df)} 筆資料，無法繪製 K 線。請切換其他週期。")
        st.stop()

    # ---- 8-6 計算指標（完整資料）→ 擷取顯示區間 ----
    full = add_indicators(df)
    view = full.tail(bars)
    last, prev_bar = full.iloc[-1], full.iloc[-2]
    meta = fetch_meta(symbol)
    is_tw = symbol.endswith((".TW", ".TWO"))
    vol_div, vol_unit = (1000, "張") if is_tw else (1, "股")
    last_dt = full.index[-1]
    data_time = last_dt.strftime("%Y/%m/%d %H:%M" if intraday else "%Y/%m/%d")

    # ---- 8-7 股票標題與價格 ----
    with quote_slot:
        chg_cls = cls_of(last["Chg"])
        arrow = "▲" if last["Chg"] > 0 else ("▼" if last["Chg"] < 0 else "")
        ohlc_cls = lambda v: cls_of(v - prev_bar["Close"])
        tags = [f'<span class="qk-tag mono">{esc(symbol)}</span>']
        if meta["market"]:
            tags.append(f'<span class="qk-tag">{esc(meta["market"])}</span>')
        tags.append(f'<span class="qk-tag">{cfg["label"]}</span>')
        if adjusted:
            tags.append('<span class="qk-tag">還原權息</span>')
        color_note = "" if is_tw else "　·　顏色採台股慣例：紅漲綠跌"
        cur = f" {esc(meta['currency'])}" if meta["currency"] else ""
        chg_txt = (f"{arrow} {fmt_num(abs(last['Chg']))}　{last['ChgPct']:+.2f}%"
                   if pd.notna(last["Chg"]) else "—")
        st.markdown(
            f'<div class="qk-card">'
            f'<div class="qk-quote">'
            f'  <div><div class="qk-q-name">{esc(meta["name"])}</div>'
            f'       <div class="qk-q-tags">{"".join(tags)}</div>'
            f'       <div class="qk-q-meta">最新 K 棒：<span class="qk-mono">{data_time}</span>'
            f'（交易所當地時間）{color_note}</div></div>'
            f'  <div class="qk-q-right"><div class="qk-price {chg_cls}">{fmt_num(last["Close"])}'
            f'<span style="font-size:.9rem;font-weight:500;color:var(--qk-muted)">{cur}</span></div>'
            f'       <div class="qk-chg {chg_cls}">{chg_txt}</div>'
            f'       <div class="qk-q-meta">較前一根 K 棒</div></div>'
            f'</div>'
            f'<div class="qk-ohlc">'
            f'  <span>開<b class="{ohlc_cls(last["Open"])}">{fmt_num(last["Open"])}</b></span>'
            f'  <span>高<b class="{ohlc_cls(last["High"])}">{fmt_num(last["High"])}</b></span>'
            f'  <span>低<b class="{ohlc_cls(last["Low"])}">{fmt_num(last["Low"])}</b></span>'
            f'  <span>量（{vol_unit}）<b>{last["Volume"] / vol_div:,.0f}</b></span>'
            f'  <span>振幅<b>{fmt_num(last["Amp"])}%</b></span>'
            f'</div></div>',
            unsafe_allow_html=True,
        )
        # 休市 / 資料延遲提示：日線最新資料距今超過 5 天時提醒
        if not intraday and cfg["resample"] is None and (datetime.now() - last_dt).days > 5:
            alert("info", "最新資料並非近期交易日",
                  f"最新一根日 K 為 {data_time}，可能遇到休市、停牌，或資料來源尚未更新。")

    # ---- 8-8 均線選擇列（勾選即時疊加；俗稱以 Tooltip 呈現）----
    ma_cols = st.columns(6)
    selected_ma = []
    for col, n in zip(ma_cols, MA_LIST):
        c = MA_CONFIG[n]
        v, pv = last[f"MA{n}"], prev_bar[f"MA{n}"]
        if period_key == "日":
            tip = f"{c['nick']}：最近 {n} 根日 K 收盤價平均"
        else:
            tip = f"最近 {n} 根{cfg['unit']}收盤價平均（依目前週期計算）"
        with col:
            if st.checkbox(c["name"], value=c["default"], key=f"ma_{n}", help=tip):
                selected_ma.append(n)
            if pd.isna(v):
                val_html = '<div class="qk-ma-val">資料不足</div>'
            else:
                arr = ('<span class="up">▲</span>' if pd.notna(pv) and v > pv
                       else ('<span class="down">▼</span>' if pd.notna(pv) and v < pv else ""))
                val_html = f'<div class="qk-ma-val">{v:,.2f} {arr}</div>'
            st.markdown(val_html, unsafe_allow_html=True)

    # ---- 8-9 主要 K 線圖 ----
    insufficient = [MA_CONFIG[n]["name"] for n in selected_ma if view[f"MA{n}"].notna().sum() == 0]
    if insufficient:
        alert("warn", "部分均線資料不足",
              f"{'、'.join(insufficient)} 需要的 K 棒數超過目前可取得的 {len(full)} 根{cfg['unit']}，因此無法繪製。")
    if sub_ind != "不顯示" and len(full) < 35:
        alert("warn", f"{sub_ind} 資料不足", f"{sub_ind} 指標約需 35 根以上 K 棒，目前只有 {len(full)} 根，副圖可能為空。")

    fig = build_chart(
        view=view, t=t, theme_key=theme_key, selected_ma=selected_ma, sub_ind=sub_ind,
        fmt=cfg["fmt"], vol_div=vol_div, vol_unit=vol_unit, show_bb=show_bb,
        show_cross=show_cross, show_hl=show_hl, uirev=f"{symbol}-{period_key}-{bars}-{sub_ind}", mobile=mobile,
    )
    st.plotly_chart(
        fig,
        config={
            "displayModeBar": False if mobile else "hover",  # 手機隱藏工具列（雙擊即可重設），避免遮住標註
            "scrollZoom": True,
            "displaylogo": False,
            "responsive": True,
            "modeBarButtonsToRemove": ["lasso2d", "select2d", "autoScale2d"],
            "toImageButtonOptions": {"format": "png", "filename": f"{symbol}_{period_key}_K線", "scale": 2},
        },
        **STRETCH,
    )

    # ---- 8-10 市場指標（顯示區間內）----
    range_txt = f"{view.index[0].strftime(cfg['fmt'])} – {view.index[-1].strftime(cfg['fmt'])}"
    section_header("市場指標", f"顯示區間 {len(view)} 根{cfg['unit']}", range_txt)
    hi_v, lo_v = view["High"].max(), view["Low"].min()
    hi_d = view["High"].idxmax().strftime(cfg["fmt"])
    lo_d = view["Low"].idxmin().strftime(cfg["fmt"])
    avg_vol = view["Volume"].mean() / vol_div
    sum_vol = view["Volume"].sum() / vol_div
    ret = (view["Close"].iloc[-1] / view["Close"].iloc[0] - 1) * 100
    ret_abs = view["Close"].iloc[-1] - view["Close"].iloc[0]
    to_px = lambda v: (v / last["Close"] - 1) * 100
    st.markdown(
        f'<div class="qk-grid">'
        f'<div class="qk-card qk-stat"><div class="qk-stat-label">區間最高價</div>'
        f'<div class="qk-stat-value">{fmt_num(hi_v)}</div>'
        f'<div class="qk-stat-sub"><span class="qk-mono">{hi_d}</span>　距現價 <span class="qk-mono">{to_px(hi_v):+.2f}%</span></div></div>'
        f'<div class="qk-card qk-stat"><div class="qk-stat-label">區間最低價</div>'
        f'<div class="qk-stat-value">{fmt_num(lo_v)}</div>'
        f'<div class="qk-stat-sub"><span class="qk-mono">{lo_d}</span>　距現價 <span class="qk-mono">{to_px(lo_v):+.2f}%</span></div></div>'
        f'<div class="qk-card qk-stat"><div class="qk-stat-label">平均成交量（{vol_unit} / 根）</div>'
        f'<div class="qk-stat-value">{fmt_vol(avg_vol)}</div>'
        f'<div class="qk-stat-sub">區間累積 <span class="qk-mono">{fmt_vol(sum_vol)}</span> {vol_unit}</div></div>'
        f'<div class="qk-card qk-stat"><div class="qk-stat-label">區間報酬率</div>'
        f'<div class="qk-stat-value {cls_of(ret)}">{ret:+.2f}%</div>'
        f'<div class="qk-stat-sub">價差 <span class="qk-mono">{ret_abs:+,.2f}</span>（首根收盤至最新收盤）</div></div>'
        f'</div>',
        unsafe_allow_html=True,
    )
    vol_delta = (last["Volume"] / prev_bar["Volume"] - 1) * 100 if prev_bar["Volume"] > 0 else np.nan
    st.markdown(
        f'<div class="qk-card qk-strip">'
        f'<span>最新成交量<b>{fmt_vol(last["Volume"] / vol_div)} {vol_unit}</b>'
        f'{f"（較前根 {vol_delta:+.1f}%）" if pd.notna(vol_delta) else ""}</span>'
        f'<span>最新振幅<b>{fmt_num(last["Amp"])}%</b></span>'
        f'<span>RSI14<b>{fmt_num(last["RSI14"], 1)}</b></span>'
        f'<span>K / D<b>{fmt_num(last["K"], 1)} / {fmt_num(last["D"], 1)}</b></span>'
        f'<span>價格基準<b style="font-family:inherit">{"還原權息" if adjusted else "原始成交價"}</b></span>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # ---- 8-11 技術面研究摘要（規則式技術診斷）----
    section_header("Technical Outlook", "技術面研究摘要", f"資料截至 {data_time}（{cfg['label']}）")
    diag = diagnose(full, cfg["unit"])
    dot_color = {1: "var(--qk-up)", -1: "var(--qk-down)", 0: "var(--qk-flat)"}
    pills = "".join(
        f'<span class="qk-pill"><span class="qk-dot" style="background:{dot_color[d]}"></span>{esc(label)}</span>'
        for label, d in diag["pills"]
    )
    items = "".join(f'<div class="qk-o-item"><b>{k}</b>{v}</div>' for k, v in diag["items"] if v)
    weights = (f'多方訊號權重 <span class="qk-mono">{diag["bull"]:.1f}</span>　·　'
               f'空方訊號權重 <span class="qk-mono">{diag["bear"]:.1f}</span>　·　' if diag["items"] else "")
    st.markdown(
        f'<div class="qk-card qk-outlook">'
        f'<div class="qk-o-head"><span class="qk-o-verdict {diag["vclass"]}">{diag["verdict"]}</span>'
        f'<span class="qk-o-badge">規則式技術診斷</span></div>'
        f'<div class="qk-o-summary">{diag["summary"]}</div>'
        f'<div class="qk-pills">{pills}</div>'
        f'<div class="qk-o-cols">{items}</div>'
        f'<div class="qk-o-foot">{weights}依 {len(full):,} 根{cfg["unit"]}以固定規則計算（均線排列與交叉、MACD、RSI、KD），'
        f'非 AI 模型推論，亦不構成投資建議。</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # ---- 8-12 歷史資料與下載 ----
    st.write("")
    with st.expander(f"歷史資料明細（{len(view)} 根{cfg['unit']}，含技術指標）與 CSV 下載"):
        vol_label = f"成交量({vol_unit})"
        table = view[["Open", "High", "Low", "Close", "Volume", "Chg", "ChgPct"] +
                     [f"MA{n}" for n in MA_LIST] + ["DIF", "MACD", "OSC", "RSI6", "RSI14", "K", "D"]].copy()
        table["Volume"] = table["Volume"] / vol_div
        table.index = table.index.strftime(cfg["fmt"])
        table.index.name = "日期"
        table = table.rename(columns={"Open": "開盤", "High": "最高", "Low": "最低", "Close": "收盤",
                                      "Volume": vol_label, "Chg": "漲跌", "ChgPct": "漲跌幅%"})
        table = table.iloc[::-1]
        st.markdown(history_table_html(table, vol_label), unsafe_allow_html=True)
        st.write("")
        st.download_button(
            "下載 CSV（Excel 可直接開啟）",
            data=table.round(4).to_csv().encode("utf-8-sig"),
            file_name=f"{symbol}_{period_key}_history.csv",
            mime="text/csv",
        )
    with st.expander("指標說明"):
        st.markdown(
            f"- **均線 MA**：最近 N 根{cfg['unit']}收盤價的簡單平均，隨週期切換重新計算。\n"
            "- **MACD (12, 26, 9)**：DIF = EMA12 − EMA26；MACD = DIF 的 9 期 EMA；柱狀體 OSC = DIF − MACD。\n"
            "- **RSI**：Wilder 平滑法，RSI6 為快線、RSI14 為標準線；≥70 超買、≤30 超賣。\n"
            "- **KD (9, 3, 3)**：RSV 以 9 根高低區間計算，K、D 為 1/3 平滑，起始值 50。\n"
            "- **布林通道**：MA20 ± 2 倍標準差。\n"
            "- **交叉訊號**：MA5 由下往上穿越 MA20 為黃金交叉，反之為死亡交叉。\n"
            "- **週 / 月線**：由日線彙整（開＝首日開盤、高＝最高、低＝最低、收＝末日收盤、量＝加總）。"
        )

    # ---- 8-13 頁尾 ----
    st.markdown(
        f'<div class="qk-footer"><span>資料來源：Yahoo Finance（yfinance）· 最後更新 '
        f'<span class="qk-mono">{fetched_at}</span></span>'
        f'<span>技術指標僅供研究與教學用途，非投資建議 · 台股慣例紅漲綠跌</span></div>',
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
