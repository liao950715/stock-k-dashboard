# -*- coding: utf-8 -*-
"""測試共用工具：建立假新聞、假交易日曆（全部是測試資料，不會寫進正式資料庫）"""
import os
import sys
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import news_sentiment as ns  # noqa: E402

TPE = ZoneInfo("Asia/Taipei")


def tpe(y, m, d, hh=10, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=TPE)


def make_item(title, source="測試媒體", published=None, fetched=None, ticker="2330.TW", official=False,
              code="2330", names=("台積電",), url=None):
    published = published or tpe(2026, 11, 2, 10)
    fetched = fetched or published
    it = ns.NewsItem(news_id=ns.make_news_id(url or f"https://example.test/{source}/{title}", source, title),
                     ticker=ticker, title=title, source=source, source_url=url or f"https://example.test/{source}/{title}",
                     published_at=published, fetched_at=fetched,
                     content_hash=ns.make_content_hash(title, source), official=official)
    return ns.classify_item(it, code, list(names))


@pytest.fixture
def calendar():
    """
    測試用台股日曆：2026/10/26（一）～ 2026/11/13（五）的平日都是交易日，
    但 2026/11/04（三）設定為國定假日（不在 known_days 裡）。
    """
    days = set()
    d = date(2026, 10, 26)
    while d <= date(2026, 11, 13):
        if d.weekday() < 5 and d != date(2026, 11, 4):
            days.add(d)
        d += timedelta(days=1)
    return ns.MarketCalendar(known_days=frozenset(days), holidays=frozenset({date(2026, 11, 4)}))
