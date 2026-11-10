# -*- coding: utf-8 -*-
"""
不靠 Streamlit、單獨使用新聞情緒模組的範例（給其他專案參考）

需要的檔案：news_sentiment.py、news_store.py、news_pipeline.py（＋ 選用 ai_summary.py）
需要的套件：pandas（只有 news_pipeline 用到）

執行：python examples/news_standalone_demo.py
"""
import email.utils
import os
import sys
import tempfile
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import news_pipeline as npl   # noqa: E402
import news_sentiment as ns   # noqa: E402
import news_store             # noqa: E402

TPE = ZoneInfo("Asia/Taipei")


# ---------------------------------------------------------------------
# 1. 新聞來源：任何來源都可以，只要轉成這個格式
#    [{"title": str, "source": str, "link": str, "published": 有時區的 datetime}, ...]
# ---------------------------------------------------------------------
def fetch_google_news(query: str, limit: int = 20) -> list:
    url = ("https://news.google.com/rss/search?q=" + urllib.parse.quote(query)
           + "&hl=zh-TW&gl=TW&ceid=TW:zh-Hant")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        root = ET.fromstring(resp.read())
    out = []
    for item in root.findall(".//item")[:limit]:
        title, source = item.findtext("title", ""), item.findtext("source", "")
        if source and title.endswith(" - " + source):
            title = title[: -len(" - " + source)]
        out.append({"title": title, "source": source, "link": item.findtext("link", ""),
                    "published": email.utils.parsedate_to_datetime(item.findtext("pubDate"))})
    return out


# ---------------------------------------------------------------------
# 2. 交易日曆：給「過去實際有開盤的日期」＋「未來的休市日」
#    其他專案如果已經有交易日資料（例如 TWSE 日行情），直接拿那份日期清單即可
# ---------------------------------------------------------------------
def simple_calendar(days_back: int = 60) -> ns.MarketCalendar:
    try:
        holidays = npl.fetch_twse_holidays()
    except Exception:
        holidays = set()
    today = datetime.now(TPE).date()
    known = [today - timedelta(days=i) for i in range(days_back)
             if (today - timedelta(days=i)).weekday() < 5 and (today - timedelta(days=i)) not in holidays]
    return npl.build_calendar(known, is_tw=True, holidays=holidays)


def main():
    db = os.path.join(tempfile.mkdtemp(), "demo.sqlite")       # 範例用暫存資料庫
    store = news_store.NewsStore(db)
    calendar = simple_calendar()

    # -----------------------------------------------------------------
    # 3. 即時分析：一行跑完整流程（存檔 → 分類 → 事件 → NSI → 警示 → 快照 → 動能）
    # -----------------------------------------------------------------
    raw = fetch_google_news("台積電 2330")
    out = npl.run_analysis(store, ticker="2330.TW", code="2330", names=["台積電", "TSMC"],
                           raw_news=raw, material=[], calendar=calendar)
    r, st = out["result"], out["result"]["stats"]
    print("== 即時分析")
    print("數量", {k: round(v, 1) if isinstance(v, float) else v for k, v in out["counts"].items()})
    print("NSI", None if st["nsi"] is None else round(st["nsi"], 1), r["label"],
          "| 穩健程度", r["robustness"], "| 動能", out["momentum"]["label"])
    print("警示", [(a["kind"], a["title"][:20]) for a in out["alerts"]])

    # -----------------------------------------------------------------
    # 4. 回測 / 特定時間點：直接用 analyze(events, calendar, as_of)
    #    只會用到 as_of 之前「已發布且已取得」的新聞，不會用到未來資訊
    # -----------------------------------------------------------------
    items = store.load_news("2330.TW")              # 從資料庫讀回所有歷史新聞
    for it in items:
        store.classify_cached(it, "2330", ["台積電", "TSMC"])
    events = ns.build_events(items)
    as_of = datetime.now(timezone.utc) - timedelta(days=1)
    past = ns.analyze(events, calendar, as_of)
    print("== 一天前的時間點", as_of.astimezone(TPE).strftime("%m/%d %H:%M"))
    print("納入事件", past["K_incl"], "| NSI",
          None if past["stats"]["nsi"] is None else round(past["stats"]["nsi"], 1))

    # -----------------------------------------------------------------
    # 5. 給其他程式用的扁平結果（例如當成量化模型的一個特徵）
    # -----------------------------------------------------------------
    feature = {"ticker": "2330.TW", "as_of_session": str(out["session"]), "nsi": st["nsi"],
               "label": r["label"], "robustness": r["robustness"], "k_incl": r["K_incl"],
               "n_eff": st["n_eff"], "A": st["A"], "omega": st["omega"],
               "major_bull": sum(a["kind"] == "重大利多" for a in out["alerts"]),
               "major_bear": sum(a["kind"] == "重大利空" for a in out["alerts"]),
               "unconfirmed": sum(a["kind"] == "未確認重大消息" for a in out["alerts"]),
               "formula_version": ns.FORMULA_VERSION}
    print("== 特徵", feature)


if __name__ == "__main__":
    main()
