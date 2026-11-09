# -*- coding: utf-8 -*-
"""13.1 數學測試"""
import math

import pytest

import news_sentiment as ns
from conftest import make_item, tpe


def test_zero_events():
    st = ns.nsi_stats([], [])
    assert st["valid"] is False and st["nsi"] is None and st["omega"] is None


def test_all_weights_zero():
    st = ns.nsi_stats([0, 0, 0], [1, -1, 0.5])
    assert st["valid"] is False


def test_one_event_shrinkage():
    # W = 3, s = +1 → NSI_raw = 1，NSI_shrunk = 3 / (3 + 2) = 0.6 → 顯示 60
    st = ns.nsi_stats([3.0], [1.0])
    assert st["nsi_raw"] == pytest.approx(1.0)
    assert st["nsi_shrunk"] == pytest.approx(0.6)
    assert st["nsi"] == pytest.approx(60.0)
    assert st["n_eff"] == pytest.approx(1.0)


def test_two_events():
    st = ns.nsi_stats([1.0, 2.0], [0.5, -1.0])
    # Σws = 0.5 − 2 = −1.5；ΣW = 3 → shrunk = −1.5 / 5 = −0.3
    assert st["nsi"] == pytest.approx(-30.0)
    assert st["A"] == pytest.approx(2.5 / 3)
    assert st["omega"] == pytest.approx(1 - 1.5 / 2.5)


def test_all_neutral_no_division_by_zero():
    st = ns.nsi_stats([1, 2, 3], [0, 0, 0])
    assert st["valid"] and st["nsi"] == 0
    assert st["A"] == 0 and st["omega"] is None and st["no_direction"] is True


def test_all_positive_and_all_negative():
    pos = ns.nsi_stats([1, 1, 1], [1, 0.5, 1])
    neg = ns.nsi_stats([1, 1, 1], [-1, -0.5, -1])
    assert pos["omega"] == pytest.approx(0) and neg["omega"] == pytest.approx(0)
    assert pos["nsi"] == pytest.approx(-neg["nsi"])
    assert pos["nsi"] > 0


def test_full_cancellation():
    st = ns.nsi_stats([1, 1], [1, -1])
    assert st["nsi"] == pytest.approx(0)
    assert st["omega"] == pytest.approx(1)
    assert st["A"] == pytest.approx(1)


def test_n_eff():
    assert ns.nsi_stats([1, 1, 1, 1], [0, 0, 0, 0])["n_eff"] == pytest.approx(4)
    assert ns.nsi_stats([3, 1], [1, 1])["n_eff"] == pytest.approx(16 / 10)


def test_cap_not_applied_below_three_events():
    w, capped, feasible = ns.cap_weights([10, 1])
    assert w == [10, 1] and not capped and feasible


def test_cap_single_dominant_event():
    w, capped, feasible = ns.cap_weights([10, 1, 1, 1])
    assert capped and feasible
    assert max(w) / sum(w) == pytest.approx(0.4)
    assert w[1:] == [1, 1, 1]          # 沒被壓的事件權重不變


def test_cap_multiple_events_capped():
    w, capped, feasible = ns.cap_weights([10, 9, 1, 1, 1])
    assert capped and feasible
    shares = [x / sum(w) for x in w]
    assert max(shares) <= 0.4 + 1e-9
    assert shares[0] == pytest.approx(0.4) and shares[1] == pytest.approx(0.4)


def test_cap_infeasible_reports_and_terminates():
    # 3 個相同權重、上限 20%：數學上不可能，必須回報而不是無限迴圈
    w, capped, feasible = ns.cap_weights([1, 1, 1], cap=0.2)
    assert feasible is False and capped is False and w == [1, 1, 1]


def test_cap_no_change_when_within_limit():
    w, capped, feasible = ns.cap_weights([1, 1, 1, 1])
    assert not capped and feasible and w == [1, 1, 1, 1]


@pytest.mark.parametrize("level,h", [(4, 6), (3, 3), (2, 2), (1, 1)])
def test_time_decay_boundaries(level, h):
    assert ns.time_decay(0, level) == 1
    assert ns.time_decay(h, level) == pytest.approx(0.5)
    assert ns.time_decay(3 * h, level) == pytest.approx(0.125)      # t = 3H 仍保留
    assert ns.time_decay(3 * h + 1, level) == 0                     # t > 3H 排除


def test_relevance_threshold():
    assert ns.relevance_weight(0.29) == 0
    assert ns.relevance_weight(0.3) == 0.5
    assert ns.relevance_weight(0.69) == 0.5
    assert ns.relevance_weight(0.7) == 1.0


def test_labels_and_hysteresis():
    assert ns.label_from_score(40) == "強烈偏多"
    assert ns.label_from_score(15) == "偏多"
    assert ns.label_from_score(14.9) == "中性"
    assert ns.label_from_score(-15) == "偏空"
    assert ns.label_from_score(-40) == "強烈偏空"
    # 從「中性」剛跨過 +15 一點點：顯示標籤維持中性；超過 +20 才切換
    assert ns.display_label(16, "中性") == "中性"
    assert ns.display_label(21, "中性") == "偏多"
    # 正式標籤不受遲滯影響
    assert ns.label_from_score(16) == "偏多"


def test_momentum_rules():
    ok = {"nsi": 30, "robustness": "中", "formula_version": ns.FORMULA_VERSION}
    past = {"nsi": 10, "robustness": "高", "formula_version": ns.FORMULA_VERSION}
    m = ns.momentum(ok, past)
    assert m["value"] == 20 and m["label"] == "改善"
    assert ns.momentum(ok, {**past, "robustness": "低"})["value"] is None
    assert ns.momentum(ok, {**past, "formula_version": "old"})["value"] is None
    assert ns.momentum(ok, None)["label"] == "趨勢資料不足"
    assert ns.momentum({**ok, "nsi": 15}, past)["label"] == "大致持平"


def test_analyze_end_to_end_and_sensitivity(calendar):
    items = [
        make_item("台積電月營收創新高 年增三成", published=tpe(2026, 11, 10, 9, 30)),
        make_item("台積電法說會上修全年財測", source="B 報", published=tpe(2026, 11, 10, 14, 0)),
        make_item("外資賣超台積電 股價下跌", source="C 報", published=tpe(2026, 11, 9, 10, 0)),
        make_item("台積電 AI 訂單強勁 受惠擴產", source="D 報", published=tpe(2026, 11, 6, 10, 0)),
        make_item("台積電今天開股東會", source="E 報", published=tpe(2026, 11, 6, 11, 0)),
    ]
    events = ns.build_events(items)
    res = ns.analyze(events, calendar, tpe(2026, 11, 11, 10, 0))
    st = res["stats"]
    assert res["K_incl"] == 5 and st["valid"]
    # 用最終權重手算一次 NSI，必須和程式一致
    ws = [r["W"] for r in res["included"]]
    ss = [r["event"].score for r in res["included"]]
    assert st["nsi"] == pytest.approx(100 * sum(w * s for w, s in zip(ws, ss)) / (sum(ws) + 2))
    assert set(res["sensitivity"]) == {"正式權重（V2.1）", "等權重", "不使用時間衰減", "替代重要性權重"}
    assert res["robustness"] in ("高", "中", "低")


def test_robustness_low_with_few_events(calendar):
    events = ns.build_events([make_item("台積電月營收創新高", published=tpe(2026, 11, 10, 9, 30))])
    res = ns.analyze(events, calendar, tpe(2026, 11, 10, 12, 0))
    assert res["K_incl"] == 1 and res["robustness"] == "低"
    assert any("事件數不足" in n for n in res["notes"])
    assert not res["capped"]
