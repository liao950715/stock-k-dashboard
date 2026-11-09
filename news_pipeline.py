# -*- coding: utf-8 -*-
"""
新聞分析流程：把抓到的新聞存檔、分類、整理成事件、計算 V2.1 指標、存每日快照。
這個檔案不碰畫面，Streamlit 頁面只負責呼叫 run_analysis() 和顯示結果。
"""

from __future__ import annotations

import json
import urllib.request
from datetime import date, datetime, timedelta, timezone

import pandas as pd

import news_sentiment as ns

TW_TZ = "Asia/Taipei"


def roc_to_date(text: str) -> date | None:
    """民國日期（1151108）→ 西元日期"""
    text = str(text).strip()
    if len(text) < 7 or not text.isdigit():
        return None
    return date(int(text[:-4]) + 1911, int(text[-4:-2]), int(text[-2:]))


def fetch_twse_holidays(timeout: float = 10.0) -> set:
    """
    證交所休市日 OpenAPI。注意：這份清單也包含「開始交易日」「最後交易日」這類有交易的日子，
    所以名稱裡有「交易日」的要排除；只留下真正休市的日期。
    """
    url = "https://openapi.twse.com.tw/v1/holidaySchedule/holidaySchedule"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        rows = json.loads(resp.read().decode("utf-8"))
    out = set()
    for r in rows:
        name = str(r.get("Name", ""))
        if "交易日" in name and ("開始" in name or "最後" in name):
            continue
        d = roc_to_date(r.get("Date", ""))
        if d:
            out.add(d)
    return out


def build_calendar(trading_index, is_tw: bool, holidays=frozenset()) -> ns.MarketCalendar:
    """用實際行情資料（日 K 的日期）建立交易日曆；台股 / 美股的時區與收盤時間不同"""
    days = frozenset(pd.Timestamp(d).date() for d in trading_index)
    if is_tw:
        return ns.MarketCalendar(tz=TW_TZ, known_days=days, holidays=frozenset(holidays))
    from datetime import time as dtime
    return ns.MarketCalendar(tz="America/New_York", open_time=dtime(9, 30), close_time=dtime(16, 0),
                             known_days=days, holidays=frozenset(holidays))


def to_items(ticker: str, raw_news: list, material: list, fetched_at: datetime) -> list:
    """把 fetch_news / fetch_material_info 的結果轉成 NewsItem"""
    items = []
    for n in raw_news:
        pub = n.get("published")
        pub = pub.to_pydatetime() if hasattr(pub, "to_pydatetime") else pub
        items.append(ns.NewsItem(
            news_id=ns.make_news_id(n.get("link", ""), n.get("source", ""), n["title"]),
            ticker=ticker, title=n["title"], source=n.get("source", ""), source_url=n.get("link", ""),
            published_at=pub, fetched_at=fetched_at, content_hash=ns.make_content_hash(n["title"], n.get("source", ""))))
    for a in material:
        d = roc_to_date(a.get("date_raw", "")) if a.get("date_raw") else None
        if d is None and a.get("date"):
            try:
                d = datetime.strptime(a["date"], "%Y/%m/%d").date()
            except ValueError:
                d = None
        if d is None:
            continue
        hhmmss = str(a.get("time_raw", "")).zfill(6) if a.get("time_raw") else "000000"
        try:
            pub = datetime(d.year, d.month, d.day, int(hhmmss[:2]) % 24, int(hhmmss[2:4]) % 60,
                           tzinfo=pd.Timestamp.now(tz=TW_TZ).tzinfo)
        except ValueError:
            pub = datetime(d.year, d.month, d.day, tzinfo=pd.Timestamp.now(tz=TW_TZ).tzinfo)
        title = a.get("subject", "")
        items.append(ns.NewsItem(
            news_id=ns.make_news_id("", "公開資訊觀測站", f"{d}|{title}"), ticker=ticker, title=title,
            source="公開資訊觀測站（重大訊息）", source_url="https://mops.twse.com.tw/", published_at=pub,
            fetched_at=fetched_at, content_hash=ns.make_content_hash(title, "MOPS"), official=True))
    return items


def run_analysis(store, ticker: str, code: str, names: list, raw_news: list | None, material: list,
                 calendar: ns.MarketCalendar, now: datetime | None = None, fetch_error: str = "") -> dict:
    """
    完整流程（每次打開新聞頁時執行；計算很輕，AI 不在這裡呼叫）：
      1. 記錄這次查詢成功或失敗
      2. 新聞存入 L1（同內容不重複），讀回這檔股票所有歷史新聞
      3. L2 分類快取
      4. 事件分群與版本、V2.1 計算、警示
      5. 存當日快照、計算 5 日動能
    raw_news 為 None 代表新聞來源查詢失敗。
    """
    now = now or datetime.now(timezone.utc)
    session = calendar.as_of_session(now)
    session_str = session.isoformat()
    if raw_news is None:
        store.log_fetch(ticker, session_str, "failed", 0, fetch_error)
        raw_news = []
    else:
        store.log_fetch(ticker, session_str, "ok", len(raw_news))
    store.save_news(to_items(ticker, raw_news, material, now))

    # 只看最近一段時間的新聞（最長半衰期 6 交易日 × 3 = 18 交易日，約 30 天，多抓一點）
    history = store.load_news(ticker, since=now - timedelta(days=40), until=now)
    for it in history:
        store.classify_cached(it, code, names)
    n_raw = len(history)
    n_rel = sum(1 for it in history if ns.relevance_weight(it.relevance) > 0)
    events = ns.build_events(history)
    store.save_events(events)
    evset = store.event_version_set(events)
    result = ns.analyze(events, calendar, now)
    alerts = ns.build_alerts(result)

    # 遲滯：UI 顯示標籤參考前一個交易日的顯示標籤
    prev_label = store.last_display_label(ticker, session_str)
    shown = ns.display_label(result["stats"]["nsi"], prev_label)
    st_ = result["stats"]
    snap = {"formula_version": ns.FORMULA_VERSION, "nsi": st_["nsi"], "label": result["label"], "display_label": shown,
            "K_incl": result["K_incl"], "n_eff": st_["n_eff"], "A": st_["A"], "omega": st_["omega"],
            "robustness": result["robustness"],
            "major_events": [a["event_id"] for a in alerts if a["kind"] in ("重大利多", "重大利空")],
            "n_raw": n_raw, "n_rel": n_rel, "k_events": len(events), "data_cutoff": now.isoformat()}
    if st_["valid"] or raw_news:
        store.save_snapshot(ticker, session_str, snap)

    snaps = store.snapshots(ticker)
    # d−5：往前數 5 個交易日的快照
    past_day = session
    for _ in range(ns.PARAMS["momentum_window"]):
        past_day = calendar.prev_or_same_trading_day(past_day - timedelta(days=1))
    mom = ns.momentum(snaps.get(session_str), snaps.get(past_day.isoformat()))
    return {"result": result, "alerts": alerts, "events": events, "momentum": mom, "display_label": shown,
            "counts": {"N_raw": n_raw, "N_rel": n_rel, "K_events": len(events), "K_incl": result["K_incl"],
                       "n_eff": st_["n_eff"]},
            "session": session, "event_version_set": evset, "snapshot_id": f"{ticker}|{session_str}|{ns.FORMULA_VERSION}",
            "data_cutoff": now.isoformat(), "history_items": history}


def trend_series(store, ticker: str, calendar: ns.MarketCalendar, session: date, days: int) -> list:
    """
    最近 N 個交易日的趨勢資料，每一天標記狀態：
      ok          ：有有效快照（包含「查詢成功但沒有新事件、舊事件仍在衰減」的情況）
      failed      ：當天只有查詢失敗紀錄，不能當成 0 分
      not_collected：當天沒有執行資料蒐集
    """
    snaps = store.snapshots(ticker)
    fetch = store.fetch_status_by_day(ticker)
    out, d = [], session
    for _ in range(days):
        key = d.isoformat()
        s = snaps.get(key)
        if s and s["nsi"] is not None:
            out.append({"date": d, "status": "ok", "nsi": s["nsi"], "robustness": s["robustness"]})
        elif fetch.get(key) == "failed":
            out.append({"date": d, "status": "failed", "nsi": None, "robustness": None})
        else:
            out.append({"date": d, "status": "not_collected", "nsi": None, "robustness": None})
        d = calendar.prev_or_same_trading_day(d - timedelta(days=1))
    return list(reversed(out))
