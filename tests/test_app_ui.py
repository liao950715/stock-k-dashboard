# -*- coding: utf-8 -*-
"""
13.5 UI 整合測試（Streamlit AppTest，實際執行整個網頁；需要網路抓 Yahoo 與新聞）
使用暫存資料庫，不會動到正式的歷史紀錄。
Streamlit 1.32 的 AppTest 不支援有 format_func 的下拉選單操作，所以舊版會略過。
"""
import os
import tempfile

import pytest
import streamlit as st

APP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "stock_dashboard.py")
ST_VERSION = tuple(int(x) for x in st.__version__.split(".")[:2])


@pytest.mark.skipif(ST_VERSION < (1, 40), reason="舊版 AppTest 無法操作 format_func 下拉選單")
def test_full_app_flow(monkeypatch):
    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv("NEWS_DB_PATH", os.path.join(tempfile.mkdtemp(), "test.sqlite"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    R = []


    def ok(name, cond, info=""):
        R.append((name, bool(cond), info))


    class _Wrap:
        """舊版 AppTest 的 selectbox 若有 format_func，set_value 要用格式化後的文字；這裡自動退回"""
        def __init__(self, w):
            self.w = w
        def __getattr__(self, name):
            return getattr(self.w, name)
        def set_value(self, v):
            try:
                return self.w.set_value(v)
            except ValueError:
                opts = [o for o in self.w.options if o.endswith(str(v))]
                return self.w.set_value(opts[0])


    def widget(at, kind, key):
        w = [w for w in getattr(at, kind) if getattr(w, "key", None) == key][0]
        return _Wrap(w) if kind == "selectbox" else w


    def md(at):
        return " ".join(m.value for m in at.markdown)


    def chart(at):
        c = at.get("plotly_chart")
        return str(c[0].proto) if c else ""


    def state(at):
        return dict(exc=[e.value for e in at.exception], warn=[w.value for w in at.warning],
                    charts=len(at.get("plotly_chart")))


    at = AppTest.from_file(APP, default_timeout=240).run()
    s = state(at)
    ok("預設載入 2330 走勢圖", not s["exc"] and s["charts"] == 1 and "台積電" in md(at), s)
    ok("沒有側邊欄元件", len(at.sidebar.checkbox) == 0 and len(at.sidebar.toggle) == 0)
    ok("預設深色主題", "#0B0E14" in md(at) and "#141A23" in chart(at))
    ok("價格區無大型 st.metric", len(at.metric) == 0)
    ok("近 1 年最高標示", "近 1 年最高" in md(at))
    ok("成交量單位為張", "成交量（張）" in md(at))
    ok("預設 MA5/10/20、無 MA60", all(f"MA{n}" in chart(at) for n in (5, 10, 20)) and "MA60" not in chart(at))
    ok("新聞預覽 5 則", md(at).count('class="qk-item"') == 5, md(at).count('class="qk-item"'))

    # 跨度 → 自動帶入週期，之後可以手動改
    for span, iv in [("1 天", "5 分"), ("5 天", "15 分"), ("10 天", "60 分"), ("20 天", "60 分"), ("1 個月", "日線")]:
        widget(at, "radio", "span").set_value(span).run()
        ok(f"跨度 {span} → {iv}", widget(at, "selectbox", "interval").value == iv and not at.exception and len(at.get("plotly_chart")) == 1,
           widget(at, "selectbox", "interval").value)
    widget(at, "radio", "span").set_value("1 天").run()
    widget(at, "selectbox", "interval").set_value("15 分").run()
    ok("1 天可手動改 15 分", widget(at, "selectbox", "interval").value == "15 分" and len(at.get("plotly_chart")) == 1 and not at.exception)
    widget(at, "selectbox", "interval").set_value("日線").run()
    ok("1 天 + 日線也能畫", len(at.get("plotly_chart")) == 1 and not at.exception, state(at))
    widget(at, "radio", "span").set_value("1 年").run()
    widget(at, "selectbox", "interval").set_value("5 分").run()
    ok("5 分 + 1 年 自動縮短並提示", any("59 天" in c.value for c in at.caption) and len(at.get("plotly_chart")) == 1)
    for iv in ["週線", "月線", "日線"]:
        widget(at, "selectbox", "interval").set_value(iv).run()
        ok(f"1 年 {iv}", len(at.get("plotly_chart")) == 1 and not at.exception)
    widget(at, "radio", "span").set_value("1 天").run()
    widget(at, "selectbox", "interval").set_value("月線").run()
    ok("月線 + 1 天 自動拉長提示", any("已改為顯示 1 年" in c.value for c in at.caption) and not at.exception)
    widget(at, "radio", "span").set_value("1 年").run()
    widget(at, "selectbox", "interval").set_value("日線").run()

    # 均線、布林、成交量、副圖
    for n in (60, 120, 240):
        widget(at, "checkbox", f"ma{n}").check()
    widget(at, "checkbox", "show_bb").check()
    at.run()
    ok("MA60/120/240 + 布林", all(f"MA{n}" in chart(at) for n in (60, 120, 240)) and "tonexty" in chart(at) and not at.exception)
    widget(at, "checkbox", "show_vol").uncheck().run()
    ok("關掉成交量", "bar" not in chart(at).lower() or "(\\u5f35)" not in chart(at))
    widget(at, "checkbox", "show_vol").check().run()
    for sub, keyword in [("MACD", "DIF"), ("KDJ", "\"J\""), ("無", None)]:
        widget(at, "selectbox", "sub_ind").set_value(sub).run()
        if keyword:
            ok(f"副圖 {sub}", keyword.replace('"', "") in chart(at) and not at.exception)
        else:
            ok("副圖 無", "DIF" not in chart(at) and not at.exception)

    # 主題切換：設定不能被重置
    widget(at, "radio", "span").set_value("3 個月").run()
    widget(at, "toggle", "dark").set_value(False).run()
    ok("切淺色", "#F7F8FA" in md(at) and "#FFFFFF" in chart(at))
    ok("切主題後跨度保留", widget(at, "radio", "span").value == "3 個月" and widget(at, "checkbox", "ma60").value)
    widget(at, "toggle", "dark").set_value(True).run()

    # 分頁
    widget(at, "radio", "page").set_value("相關新聞").run()
    m = md(at)
    for sec in ["新聞情緒總覽", "情緒趨勢", "重大事件與多空分歧", "AI 分析摘要", "新聞明細"]:
        ok(f"新聞頁區塊 {sec}", sec in m)
    ok("NSI 與穩健程度", "新聞情緒指數 NSI" in m and "分析穩健程度" in m and "n_eff" in m)
    ok("沒有 AI 金鑰時標示規則式", "規則式摘要（非 AI 生成）" in m)
    ok("趨勢圖不虛構資料（空狀態或真實快照）", "趨勢資料不足" in m or len(at.get("plotly_chart")) >= 1)
    ok("新聞明細可展開（判斷依據、事件、權重）", m.count("qk-detail") >= 5 and "判斷依據" in m and "確認狀態" in m)
    ok("篩選為小型下拉，五級選項", widget(at, "selectbox", "news_filter").options[1:] and len(widget(at, "selectbox", "news_filter").options) == 6)
    ok("沒有四個大型分類按鈕", len([r for r in at.radio if getattr(r, "key", None) not in ("page", "span")]) == 0)
    widget(at, "selectbox", "news_filter").set_value("偏多").run()
    ok("篩選偏多只剩偏多", all(x in ("偏多",) for x in __import__("re").findall(r'qk-tag qk-(?:up|down|flat)">([^<]+)<', md(at).split("新聞明細")[-1])) and not at.exception)
    widget(at, "toggle", "dark").set_value(False).run()
    ok("新聞頁淺色模式", "#F7F8FA" in md(at) and "新聞情緒總覽" in md(at) and not at.exception)
    widget(at, "toggle", "dark").set_value(True).run()
    widget(at, "selectbox", "news_filter").set_value("全部新聞").run()
    widget(at, "radio", "page").set_value("歷史資料").run()
    ok("歷史資料頁", len(at.dataframe) == 1 and len(at.get("download_button")) == 1 and not at.exception)
    widget(at, "radio", "page").set_value("指標說明").run()
    ok("指標說明頁", "布林通道" in md(at) and not at.exception)
    widget(at, "radio", "page").set_value("走勢圖").run()
    btn = [b for b in at.button if "查看全部" in b.label]
    ok("查看全部按鈕存在", len(btn) == 1)
    if btn:
        btn[0].click().run()
        ok("查看全部 → 新聞頁", widget(at, "radio", "page").value == "相關新聞")
    widget(at, "radio", "page").set_value("走勢圖").run()

    # 搜尋
    widget(at, "text_input", "ticker").set_value("6488").run()
    ok("上櫃 6488", "6488.TWO" in md(at) and "台灣上櫃" in md(at))
    widget(at, "text_input", "ticker").set_value("AAPL").run()
    ok("美股 AAPL 單位為股", "AAPL" in md(at) and "成交量（股）" in md(at) and "USD" in md(at), md(at)[:0])
    widget(at, "text_input", "ticker").set_value("ZZZQQ9").run()
    ok("錯誤代碼 warning", "找不到此股票代碼，請重新輸入" in str(state(at)["warn"]))
    widget(at, "text_input", "ticker").set_value("").run()
    ok("空白代碼提示", len(at.info) == 1 and not at.exception)


    failures = [(n, i) for n, c, i in R if not c]
    assert not failures, failures
    assert len(R) >= 40
