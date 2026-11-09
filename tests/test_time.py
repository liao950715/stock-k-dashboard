# -*- coding: utf-8 -*-
"""13.3 時間測試：盤前 / 盤中 / 盤後、週末、國定假日、時區、不可使用未來新聞"""
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import news_sentiment as ns
from conftest import make_item, tpe


def test_friday_after_close_available_next_monday(calendar):
    # 2026/11/6 是週五，14:00 已收盤 → 可用交易日是 11/9（週一）
    assert calendar.availability_session(tpe(2026, 11, 6, 14, 0)) == date(2026, 11, 9)


def test_monday_premarket_fetch_of_friday_after_close_news(calendar):
    pub = tpe(2026, 11, 6, 14, 0)          # 週五盤後
    as_of = tpe(2026, 11, 9, 8, 0)         # 週一盤前取得 / 分析
    assert calendar.as_of_session(as_of) == date(2026, 11, 6)
    assert calendar.age(pub, as_of) == 0
    # 週二開盤後分析：已經過 1 個交易日
    assert calendar.age(pub, tpe(2026, 11, 10, 10, 0)) == 1


def test_intraday_and_premarket_same_session(calendar):
    assert calendar.availability_session(tpe(2026, 11, 10, 8, 0)) == date(2026, 11, 10)   # 盤前
    assert calendar.availability_session(tpe(2026, 11, 10, 11, 0)) == date(2026, 11, 10)  # 盤中
    assert calendar.availability_session(tpe(2026, 11, 10, 13, 30)) == date(2026, 11, 11)  # 收盤那一刻起算盤後


def test_weekend_publication(calendar):
    assert calendar.availability_session(tpe(2026, 11, 7, 12, 0)) == date(2026, 11, 9)   # 週六
    assert calendar.availability_session(tpe(2026, 11, 8, 23, 0)) == date(2026, 11, 9)   # 週日


def test_national_holiday_is_skipped(calendar):
    # 11/4（三）是假日：11/3 盤後的新聞要到 11/5 才可用；11/3 → 11/5 只算 1 個交易日
    assert not calendar.is_trading_day(date(2026, 11, 4))
    assert calendar.availability_session(tpe(2026, 11, 3, 15, 0)) == date(2026, 11, 5)
    assert calendar.trading_days_between(date(2026, 11, 3), date(2026, 11, 5)) == 1


def test_non_trading_day_as_of(calendar):
    # 週日分析 → 分析交易日是週五
    assert calendar.as_of_session(tpe(2026, 11, 8, 15, 0)) == date(2026, 11, 6)


def test_utc_timestamps_are_converted_to_taipei(calendar):
    # UTC 05:30 = 台灣 13:30（剛收盤）→ 下一個交易日
    ts = datetime(2026, 11, 10, 5, 30, tzinfo=timezone.utc)
    assert calendar.availability_session(ts) == date(2026, 11, 11)
    # 紐約時間 11/9 20:00 = 台灣 11/10 09:00（盤中）
    ny = datetime(2026, 11, 9, 20, 0, tzinfo=ZoneInfo("America/New_York"))
    assert calendar.availability_session(ny) == date(2026, 11, 10)


def test_future_days_without_price_data_use_holidays_and_weekdays():
    cal = ns.MarketCalendar(known_days=frozenset({date(2026, 11, 6)}), holidays=frozenset({date(2026, 11, 9)}))
    assert cal.next_trading_day(date(2026, 11, 6)) == date(2026, 11, 10)


def test_backtest_does_not_use_future_news(calendar):
    past = make_item("台積電營收成長", published=tpe(2026, 11, 5, 10))
    future = make_item("台積電重挫 跌停", source="B 報", published=tpe(2026, 11, 12, 10))
    late_fetch = make_item("台積電上修財測", source="C 報", published=tpe(2026, 11, 5, 11),
                           fetched=tpe(2026, 11, 12, 9))
    res = ns.analyze(ns.build_events([past, future, late_fetch]), calendar, tpe(2026, 11, 6, 10))
    titles = [r["event"].title for r in res["rows"]]
    assert past.title in titles
    assert future.title not in titles            # 發布時間在分析時點之後
    assert late_fetch.title not in titles        # 分析時點之後才取得


def test_expired_events_are_excluded(calendar):
    # 一般新聞（H = 1）超過 3 個交易日就排除
    old = make_item("台積電今天開股東會 看好", published=tpe(2026, 10, 27, 10))
    assert old.importance == 1
    res = ns.analyze(ns.build_events([old]), calendar, tpe(2026, 11, 10, 10))
    assert res["K_incl"] == 0
    assert "半衰期" in res["rows"][0]["excluded_reason"]
