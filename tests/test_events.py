# -*- coding: utf-8 -*-
"""13.2 新聞事件測試：去重、期別、版本、否認、轉載、來源矛盾、傳聞"""
import news_sentiment as ns
from conftest import make_item, tpe


def test_same_story_from_two_outlets_is_one_event():
    a = make_item("台積電10月營收創新高 年增三成", source="A 報", published=tpe(2026, 11, 10, 14))
    b = make_item("台積電10月營收創新高，年增三成！", source="B 報", published=tpe(2026, 11, 10, 15))
    events = ns.build_events([a, b])
    assert len(events) == 1 and len(events[0].items) == 2


def test_syndicated_copies_are_not_independent_sources():
    a = make_item("台積電10月營收創新高 年增三成", source="A 報", published=tpe(2026, 11, 10, 14))
    b = make_item("台積電10月營收創新高 年增三成", source="B 報", published=tpe(2026, 11, 10, 14, 30))
    ev = ns.build_events([a, b])[0]
    assert ns._independent_sources(ev.items) == 1
    assert ev.status == ns.STATUS_SINGLE


def test_independently_written_reports_count_as_multi_source():
    a = make_item("台積電10月營收創新高 年增三成", source="A 報", published=tpe(2026, 11, 10, 14))
    b = make_item("台積電10月營收創新高，年增三成優於預期", source="B 報", published=tpe(2026, 11, 10, 16))
    ev = ns.build_events([a, b])[0]
    assert len(ev.items) == 2
    assert ev.status == ns.STATUS_MULTI


def test_different_quarter_reports_are_not_merged():
    q2 = make_item("台積電Q2財報 EPS創新高", source="A 報", published=tpe(2026, 11, 2, 14))
    q3 = make_item("台積電Q3財報 EPS創新高", source="B 報", published=tpe(2026, 11, 2, 15))
    assert len(ns.build_events([q2, q3])) == 2


def test_same_title_far_apart_in_time_is_a_different_event():
    a = make_item("外資賣超台積電", source="A 報", published=tpe(2026, 11, 2, 15))
    b = make_item("外資賣超台積電", source="A 報", published=tpe(2026, 11, 9, 15))
    assert len(ns.build_events([a, b])) == 2


def test_correction_creates_new_version_and_old_not_double_counted(calendar):
    orig = make_item("台積電10月營收年增50%", source="A 報", published=tpe(2026, 11, 10, 14))
    fix = make_item("更正：台積電10月營收年增30%", source="A 報", published=tpe(2026, 11, 10, 18))
    events = ns.build_events([orig, fix])
    assert len(events) == 1
    ev = events[0]
    assert ev.version == 2 and ev.items == [fix] and orig in ev.superseded_items
    assert len(ev.history) == 2
    res = ns.analyze(events, calendar, tpe(2026, 11, 11, 10))
    counted = [it for r in res["included"] for it in r["event"].items]
    assert orig not in counted


def test_company_denial_removes_rumor_from_nsi(calendar):
    rumor = make_item("傳台積電將砍單 營收恐下滑", source="A 報", published=tpe(2026, 11, 9, 10))
    deny = make_item("台積電否認將砍單 營收恐下滑傳聞不實", source="B 報", published=tpe(2026, 11, 9, 16))
    events = ns.build_events([rumor, deny])
    assert len(events) == 1 and events[0].status == ns.STATUS_DENIED
    res = ns.analyze(events, calendar, tpe(2026, 11, 10, 10))
    assert res["K_incl"] == 0
    assert any(a["kind"] == "已否認" for a in ns.build_alerts(res))


def test_conflicting_sources():
    a = make_item("台積電法說會 財測上修", source="A 報", published=tpe(2026, 11, 10, 14))
    b = make_item("台積電法說會 財測下修", source="B 報", published=tpe(2026, 11, 10, 15))
    ev = ns.build_events([a, b])[0]
    assert len(ev.items) == 2
    assert ev.status == ns.STATUS_CONFLICT
    assert ev.divergence > 0


def test_unconfirmed_rumor_goes_to_alert_channel_not_nsi(calendar):
    r = make_item("傳台積電將併購某記憶體大廠", source="A 報", published=tpe(2026, 11, 10, 10))
    events = ns.build_events([r])
    assert events[0].status == ns.STATUS_RUMOR
    res = ns.analyze(events, calendar, tpe(2026, 11, 10, 12))
    assert res["K_incl"] == 0
    assert [a["kind"] for a in ns.build_alerts(res)] == ["未確認重大消息"]


def test_official_announcement_is_confirmed_level4():
    o = make_item("本公司董事會決議配發現金股利", source="公開資訊觀測站", official=True,
                  published=tpe(2026, 11, 10, 17))
    ev = ns.build_events([o])[0]
    assert ev.status == ns.STATUS_OFFICIAL and ev.importance == 4


def test_relevance_not_high_just_because_name_appears():
    listy = make_item("盤後成交量TOP 20：0050、2330 台積電、2317 鴻海、2454 聯發科")
    direct = make_item("台積電宣布在美國擴產")
    unrelated = make_item("今天天氣晴朗")
    assert listy.relevance == 0.5
    assert direct.relevance >= 0.7
    assert ns.relevance_weight(unrelated.relevance) == 0


def test_five_level_sentiment():
    assert make_item("台積電漲停 創新高").label == "強烈偏多"
    assert make_item("台積電營收成長").label == "偏多"
    assert make_item("台積電今天開股東會").label == "中性"
    assert make_item("台積電營收衰退").label == "偏空"
    assert make_item("台積電重挫 跌停").label == "強烈偏空"
    assert make_item("台股盤中最大跌點後V轉").label == "偏多"     # 「最大跌點」不再被當成「大跌」，V 轉是反彈
    assert make_item("台積電傳統產業部門開會").rumor is False      # 「傳統」不是傳聞
