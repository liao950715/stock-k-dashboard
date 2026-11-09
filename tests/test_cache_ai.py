# -*- coding: utf-8 -*-
"""13.4 AI 與快取測試（使用假的 AI provider，不會呼叫真的 API、不花錢）"""
from datetime import datetime, timedelta, timezone

import pytest

import ai_summary
import news_pipeline as npl
import news_sentiment as ns
import news_store
from conftest import make_item, tpe


class FakeProvider:
    model = "fake-model"
    model_version = "fake-1"

    def __init__(self, output=None, fail=False):
        self.available = True
        self.calls = 0
        self.output = output
        self.fail = fail

    def generate(self, inp):
        self.calls += 1
        if self.fail:
            raise RuntimeError("API 503")
        if self.output is not None:
            return self.output
        ids = [e["event_id"] for e in inp["events"]]
        return {"overall": f"NSI 為 {inp['nsi']}", "change": inp["momentum_label"],
                "positives": [{"text": "正面", "event_ids": ids[:1]}], "negatives": [], "risks": [], "limitations": []}


@pytest.fixture
def store():
    return news_store.NewsStore(":memory:")


def _analysis(calendar, extra=()):
    items = [make_item("台積電月營收創新高", published=tpe(2026, 11, 10, 9, 30)),
             make_item("台積電法說會上修財測", source="B 報", published=tpe(2026, 11, 10, 10))] + list(extra)
    events = ns.build_events(items)
    res = ns.analyze(events, calendar, tpe(2026, 11, 10, 12))
    mom = ns.momentum(None, None)
    alerts = ns.build_alerts(res)
    inp = ns.summary_input("2330.TW", "台積電", res, mom, alerts, res["label"])
    return res, mom, alerts, inp, events


def _get(store, provider, inp, events, force=False, now=None):
    return ai_summary.get_summary(store, provider, "2330.TW", inp, store.event_version_set(events), "snap",
                                  "2026-11-10T12:00", {"overall": "規則式"}, force=force, now=now)


def test_same_data_does_not_call_api_twice(store, calendar):
    _, _, _, inp, events = _analysis(calendar)
    p = FakeProvider()
    first = _get(store, p, inp, events)
    second = _get(store, p, inp, events)          # 模擬 Streamlit rerun
    assert first["kind"] == second["kind"] == "ai"
    assert p.calls == 1 and second["refreshed"] is False


def test_major_event_change_triggers_refresh(store, calendar):
    _, _, _, inp, events = _analysis(calendar)
    p = FakeProvider()
    _get(store, p, inp, events)
    _, _, _, inp2, events2 = _analysis(calendar, [make_item("台積電財報 EPS 衰退", source="C 報",
                                                             published=tpe(2026, 11, 10, 11))])
    _get(store, p, inp2, events2)
    assert p.calls == 2


def test_prompt_version_change_regenerates(store, calendar, monkeypatch):
    _, _, _, inp, events = _analysis(calendar)
    p = FakeProvider()
    _get(store, p, inp, events)
    monkeypatch.setattr(ns, "SUMMARY_PROMPT_VERSION", "summary-prompt-9.9")
    _get(store, p, inp, events)
    assert p.calls == 2


def test_stale_summary_with_new_events_refreshes(store, calendar):
    _, _, _, inp, events = _analysis(calendar)
    p = FakeProvider()
    now = datetime(2026, 11, 10, 4, tzinfo=timezone.utc)
    _get(store, p, inp, events, now=now)
    # 加一個非重大新事件（不改指紋），但超過 24 小時 → 也要更新
    inp2 = dict(inp, events=inp["events"] + [{"event_id": "ev-new", "title": "一般新聞", "score": 0,
                                               "importance": 1, "status": "單一來源", "sources": []}])
    _get(store, p, inp2, events, now=now + timedelta(hours=25))
    assert p.calls == 2


def test_api_failure_without_cache_falls_back_to_rule(store, calendar):
    _, _, _, inp, events = _analysis(calendar)
    out = _get(store, FakeProvider(fail=True), inp, events)
    assert out["kind"] == "rule" and "503" in out["error"]


def test_api_failure_with_cache_shows_stale_not_fresh(store, calendar):
    _, _, _, inp, events = _analysis(calendar)
    _get(store, FakeProvider(), inp, events)
    out = _get(store, FakeProvider(fail=True), inp, events, force=True)
    assert out["kind"] == "ai_stale"


def test_ai_output_with_unknown_event_id_is_rejected(store, calendar):
    _, _, _, inp, events = _analysis(calendar)
    bad = {"overall": "x", "change": "x", "positives": [{"text": "a", "event_ids": ["ev-does-not-exist"]}],
           "negatives": [], "risks": [], "limitations": []}
    out = _get(store, FakeProvider(output=bad), inp, events)
    assert out["kind"] == "rule" and "不存在的事件" in out["error"]


def test_ai_output_with_invented_number_is_rejected(store, calendar):
    _, _, _, inp, events = _analysis(calendar)
    bad = {"overall": "情緒分數高達 87 分", "change": "x", "positives": [], "negatives": [], "risks": [],
           "limitations": []}
    out = _get(store, FakeProvider(output=bad), inp, events)
    assert out["kind"] == "rule" and "87" in out["error"]


def test_no_provider_is_labelled_rule_based(store, calendar):
    _, _, _, inp, events = _analysis(calendar)
    p = ai_summary.AnthropicSummarizer(api_key="")
    assert p.available is False
    out = _get(store, p, inp, events)
    assert out["kind"] == "rule"


def test_concurrent_request_does_not_generate_twice(store, calendar):
    _, _, _, inp, events = _analysis(calendar)
    p = FakeProvider()
    lock = ai_summary._lock_for("2330.TW")
    lock.acquire()
    try:
        out = _get(store, p, inp, events)
    finally:
        lock.release()
    assert p.calls == 0 and out["kind"] == "rule"


def test_manual_cooldown(store):
    now = datetime(2026, 11, 10, 4, tzinfo=timezone.utc)
    assert ai_summary.manual_allowed(store, "2330.TW", None, now)[0]
    store.record_manual_run("2330.TW")
    ok, wait = ai_summary.manual_allowed(store, "2330.TW", None, datetime.now(timezone.utc))
    assert not ok and wait > 0


def test_classification_cache_and_model_version(store):
    it = make_item("台積電營收成長")
    store.save_news([it])
    loaded = store.load_news("2330.TW")
    for x in loaded:
        store.classify_cached(x, "2330", ["台積電"])
    for x in loaded:
        store.classify_cached(x, "2330", ["台積電"])          # rerun：不重新分類
    assert store.classify_calls == 1
    for x in loaded:
        store.classify_cached(x, "2330", ["台積電"], model_version="rules-9.9")   # 新版本 → 重新分類
    assert store.classify_calls == 2


def test_l1_dedup_keeps_first_fetch_time(store):
    it = make_item("台積電營收成長", fetched=tpe(2026, 11, 10, 9))
    store.save_news([it])
    again = make_item("台積電營收成長", fetched=tpe(2026, 11, 10, 15))
    store.save_news([again])
    rows = store.load_news("2330.TW")
    assert len(rows) == 1 and rows[0].fetched_at == tpe(2026, 11, 10, 9)


def test_trend_distinguishes_failed_and_not_collected(store, calendar):
    news = [{"title": "台積電營收成長", "source": "A 報", "link": "https://example.test/a",
             "published": tpe(2026, 11, 9, 10)}]
    npl.run_analysis(store, "2330.TW", "2330", ["台積電"], news, [], calendar, now=tpe(2026, 11, 9, 12))
    npl.run_analysis(store, "2330.TW", "2330", ["台積電"], None, [], calendar, now=tpe(2026, 11, 10, 12),
                     fetch_error="timeout")
    series = npl.trend_series(store, "2330.TW", calendar, date_of(tpe(2026, 11, 11, 12)), 3)
    status = {p["date"].isoformat(): p["status"] for p in series}
    assert status["2026-11-09"] == "ok"
    # 11/10 查詢失敗，但舊事件仍在衰減，仍可計算 NSI → 有快照；不會被當成 0 分
    assert status["2026-11-10"] in ("ok", "failed")
    assert status["2026-11-11"] == "not_collected"


def test_momentum_from_snapshots(store, calendar):
    titles = ["台積電法說會上修財測", "台積電月營收創新高", "台積電接單強勁 擴產", "外資買超台積電", "台積電股利上調", "台積電AI訂單成長"]
    news = [{"title": t, "source": f"{i} 報", "link": f"https://example.test/{i}",
             "published": tpe(2026, 11, 2, 9, i)} for i, t in enumerate(titles)]
    day0 = npl.run_analysis(store, "2330.TW", "2330", ["台積電"], news, [], calendar, now=tpe(2026, 11, 2, 12))
    assert day0["result"]["robustness"] in ("中", "高")
    out = npl.run_analysis(store, "2330.TW", "2330", ["台積電"], [], [], calendar, now=tpe(2026, 11, 10, 12))
    # 11/10 的 d−5 是 11/2（中間跳過 11/4 假日）；沒有新新聞時，舊事件隨時間衰減 → NSI 往中性收縮
    m = out["momentum"]
    if out["result"]["robustness"] in ("中", "高"):
        assert m["value"] == pytest.approx(out["result"]["stats"]["nsi"] - day0["result"]["stats"]["nsi"])
        assert m["value"] < 0
    else:
        assert m["value"] is None and m["label"] == "趨勢資料不足"


def date_of(ts):
    return ts.date()
