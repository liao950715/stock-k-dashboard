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


@pytest.fixture(autouse=True)
def _reset_failure_memory():
    """失敗冷卻記在模組層級，每個測試前後都清空，避免測試互相影響"""
    ai_summary._failures.clear()
    yield
    ai_summary._failures.clear()


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


def test_no_provider_is_labelled_rule_based(store, calendar, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
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


# ---------------------------------------------------------------- OpenRouter
import io
import json
import urllib.error


def test_openrouter_preferred_over_anthropic(monkeypatch):
    # 測試不能受電腦上真的環境變數影響
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    p = ai_summary.pick_summarizer("sk-or-test", "sk-ant-test")
    assert isinstance(p, ai_summary.OpenRouterSummarizer) and p.available
    assert isinstance(ai_summary.pick_summarizer(None, "sk-ant-test"), ai_summary.AnthropicSummarizer)
    none = ai_summary.pick_summarizer(None, None)
    assert none.available is False and "OPENROUTER_API_KEY" in none.reason


def test_openrouter_request_uses_structured_output():
    req = ai_summary.OpenRouterSummarizer("sk-or-test").build_request({"nsi": 30, "events": []})
    assert req["model"] == "anthropic/claude-opus-5.5"
    assert req["response_format"]["type"] == "json_schema"
    assert req["response_format"]["json_schema"]["schema"] == ai_summary.SUMMARY_SCHEMA
    assert req["messages"][0]["role"] == "system" and req["messages"][1]["role"] == "user"


def test_openrouter_parse_response_cases():
    p = ai_summary.OpenRouterSummarizer("sk-or-test")
    good = {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps({"overall": "x"})}}]}
    assert p.parse_response(good) == {"overall": "x"}
    parts = {"choices": [{"finish_reason": "stop", "message": {"content": [{"type": "text", "text": '{"a": 1}'}]}}]}
    assert p.parse_response(parts) == {"a": 1}
    for bad, msg in [({"error": {"message": "boom"}}, "boom"),
                     ({"choices": []}, "沒有回傳"),
                     ({"choices": [{"finish_reason": "length", "message": {"content": "{"}}]}, "截斷"),
                     ({"choices": [{"finish_reason": "content_filter", "message": {"content": ""}}]}, "拒絕")]:
        with pytest.raises(RuntimeError, match=msg):
            p.parse_response(bad)


class _FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_openrouter_full_summary_flow_with_mocked_http(store, calendar, monkeypatch):
    _, _, _, inp, events = _analysis(calendar)
    ids = [e["event_id"] for e in inp["events"]]
    content = {"overall": f"新聞情緒指數為 {inp['nsi']}", "change": "趨勢資料不足",
               "positives": [{"text": "營收創新高", "event_ids": ids[:1]}], "negatives": [], "risks": [],
               "limitations": ["只依據標題"]}
    sent = {}

    def fake_urlopen(req, timeout=0):
        sent["url"], sent["auth"] = req.full_url, req.headers.get("Authorization")
        sent["body"] = json.loads(req.data.decode("utf-8"))
        return _FakeResp(json.dumps({"choices": [{"finish_reason": "stop",
                                                  "message": {"content": json.dumps(content, ensure_ascii=False)}}]}).encode())

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    p = ai_summary.OpenRouterSummarizer("sk-or-test")
    out = _get(store, p, inp, events)
    assert out["kind"] == "ai" and out["content"] == content
    assert sent["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert sent["auth"] == "Bearer sk-or-test"
    assert sent["body"]["model"] == "anthropic/claude-opus-5.5"
    # 第二次（rerun）不再呼叫
    sent.clear()
    assert _get(store, p, inp, events)["refreshed"] is False and not sent


def test_openrouter_http_errors_fall_back(store, calendar, monkeypatch):
    _, _, _, inp, events = _analysis(calendar)

    def raise_401(req, timeout=0):
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, io.BytesIO(b'{"error":"bad key"}'))

    monkeypatch.setattr("urllib.request.urlopen", raise_401)
    out = _get(store, ai_summary.OpenRouterSummarizer("sk-or-bad"), inp, events)
    assert out["kind"] == "rule" and "401" in out["error"]


@pytest.mark.network
def test_openrouter_real_endpoint_rejects_fake_key():
    """真的連到 OpenRouter，用假金鑰：預期回 401（不會產生費用），確認網址與錯誤處理正確"""
    p = ai_summary.OpenRouterSummarizer("sk-or-v1-this-is-not-a-real-key")
    with pytest.raises(RuntimeError, match="401|金鑰無效"):
        p.generate({"nsi": 0, "events": [], "alerts": []})


def test_failure_backoff_prevents_repeated_api_calls(store, calendar):
    _, _, _, inp, events = _analysis(calendar)
    ai_summary._failures.clear()
    p = FakeProvider(fail=True)
    now = datetime(2026, 11, 10, 4, tzinfo=timezone.utc)
    _get(store, p, inp, events, now=now)
    for _ in range(5):                                        # 重新整理 5 次
        out = _get(store, p, inp, events, now=now + timedelta(minutes=5))
    assert p.calls == 1 and "不自動重試" in out["error"]
    _get(store, p, inp, events, now=now + timedelta(minutes=31))   # 冷卻結束才再試
    assert p.calls == 2
    _get(store, p, inp, events, force=True, now=now + timedelta(minutes=32))   # 手動重新分析不受限制
    assert p.calls == 3
    ai_summary._failures.clear()


def test_validation_accepts_numbers_from_input_and_event_ids():
    inp = {"nsi": 41, "momentum": None, "K_incl": 21, "n_eff": 13.5, "A": 0.42, "omega": 0.0,
           "events": [{"event_id": "ev-ab0226cd", "title": "台積電10月營收5,868億元", "score": 0.5,
                       "importance": 3, "status": "單一來源", "sources": []}],
           "alerts": [], "notes": []}
    good = {"overall": "NSI 為 +41，事件分數 0.50，營收 5,868 億元", "change": "",
            "positives": [{"text": "營收創新高（ev-ab0226cd）", "event_ids": ["ev-ab0226cd"]}],
            "negatives": [], "risks": [], "limitations": []}
    assert ns.validate_ai_output(good, inp)[0]
    bad = dict(good, overall="NSI 為 +87")
    ok, problems = ns.validate_ai_output(bad, inp)
    assert not ok and "87" in problems[0]
