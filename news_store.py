# -*- coding: utf-8 -*-
"""
新聞情緒的持久化儲存（SQLite）與四層快取

  L1 news_raw         原始新聞：依 news_id + content_hash 保存，同一內容不重複儲存
  L2 classifications  分類結果：依 news_id + content_hash + 模型 + 模型版本 + prompt 版本快取
  L3 quant_cache      量化計算：依股票 + 分析交易日 + 公式版本 + 參數版本 + 事件版本集合快取
     events           事件與版本紀錄（可追溯）
     snapshots        每日 NSI 快照（趨勢與動能用）
     fetch_log        每次新聞查詢成功 / 失敗紀錄（區分「沒有新事件」與「查詢失敗」）
  L4 summaries        AI 摘要：依股票 + 事件版本集合 + 快照 + 模型版本 + 摘要 prompt 版本

注意：Streamlit Community Cloud 的檔案系統在重新部署或重啟時會被清空，
      所以雲端上的歷史資料只會從最近一次部署後開始累積。
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone

import news_sentiment as ns

DEFAULT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "news_history.sqlite")

SCHEMA = """
CREATE TABLE IF NOT EXISTS news_raw (
    news_id TEXT, content_hash TEXT, ticker TEXT, title TEXT, source TEXT, source_url TEXT,
    published_at TEXT, fetched_at TEXT, last_seen_at TEXT, official INTEGER DEFAULT 0,
    PRIMARY KEY (news_id, content_hash)
);
CREATE INDEX IF NOT EXISTS ix_news_ticker ON news_raw (ticker, published_at);
CREATE TABLE IF NOT EXISTS classifications (
    news_id TEXT, content_hash TEXT, model TEXT, model_version TEXT, prompt_version TEXT,
    label TEXT, score REAL, importance INTEGER, event_type TEXT, relevance REAL, reason TEXT,
    rumor INTEGER, flag TEXT, created_at TEXT,
    PRIMARY KEY (news_id, content_hash, model, model_version, prompt_version)
);
CREATE TABLE IF NOT EXISTS events (
    event_id TEXT, version INTEGER, ticker TEXT, status TEXT, title TEXT, news_ids TEXT,
    history TEXT, updated_at TEXT, PRIMARY KEY (event_id, version)
);
CREATE TABLE IF NOT EXISTS quant_cache (
    cache_key TEXT PRIMARY KEY, ticker TEXT, as_of_session TEXT, formula_version TEXT,
    params_version TEXT, event_version_set TEXT, result TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS snapshots (
    ticker TEXT, trading_date TEXT, formula_version TEXT, nsi REAL, label TEXT, display_label TEXT,
    k_incl INTEGER, n_eff REAL, a REAL, omega REAL, robustness TEXT, major_events TEXT,
    n_raw INTEGER, n_rel INTEGER, k_events INTEGER, data_cutoff TEXT, created_at TEXT,
    PRIMARY KEY (ticker, trading_date, formula_version)
);
CREATE TABLE IF NOT EXISTS fetch_log (
    ticker TEXT, trading_date TEXT, run_at TEXT, status TEXT, n_raw INTEGER, error TEXT,
    runs INTEGER DEFAULT 1, PRIMARY KEY (ticker, trading_date, status)
);
CREATE TABLE IF NOT EXISTS summaries (
    cache_key TEXT PRIMARY KEY, ticker TEXT, event_version_set TEXT, snapshot_id TEXT, model TEXT,
    model_version TEXT, prompt_version TEXT, fingerprint TEXT, generated_at TEXT, data_cutoff TEXT,
    source_event_ids TEXT, content TEXT
);
CREATE TABLE IF NOT EXISTS manual_runs (ticker TEXT, run_at TEXT);
"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt) -> str | None:
    return dt.isoformat() if dt else None


def _parse(s):
    return datetime.fromisoformat(s) if s else None


class NewsStore:
    """SQLite 包裝；同一個物件可以在多個 Streamlit session 之間共用（以鎖保護寫入）"""

    def __init__(self, path: str | None = None):
        # 路徑在建立時才決定：可以用環境變數 NEWS_DB_PATH 改到別的位置（測試時用暫存檔）
        path = path or os.environ.get("NEWS_DB_PATH") or DEFAULT_PATH
        self.path = path
        if path != ":memory:":
            os.makedirs(os.path.dirname(path), exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.lock = threading.RLock()
        self.classify_calls = 0          # 測試用：實際執行分類的次數（快取命中不會增加）
        with self.lock:
            self.conn.executescript(SCHEMA)
            self.conn.commit()

    # ------------------------------------------------------------- L1
    def save_news(self, items: list) -> list:
        """
        存原始新聞。同一 news_id + content_hash 只存一次（fetched_at 保留第一次取得的時間），
        之後只更新 last_seen_at。回傳帶有「第一次取得時間」的 NewsItem 清單。
        """
        out = []
        now = _iso(_now())
        with self.lock:
            for it in items:
                row = self.conn.execute(
                    "SELECT fetched_at FROM news_raw WHERE news_id=? AND content_hash=?",
                    (it.news_id, it.content_hash)).fetchone()
                if row:
                    it.fetched_at = _parse(row[0])
                    self.conn.execute("UPDATE news_raw SET last_seen_at=? WHERE news_id=? AND content_hash=?",
                                      (now, it.news_id, it.content_hash))
                else:
                    self.conn.execute(
                        "INSERT INTO news_raw VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (it.news_id, it.content_hash, it.ticker, it.title, it.source, it.source_url,
                         _iso(it.published_at), _iso(it.fetched_at), now, int(it.official)))
                out.append(it)
            self.conn.commit()
        return out

    def load_news(self, ticker: str, since: datetime | None = None, until: datetime | None = None) -> list:
        """讀回某檔股票所有歷史新聞（不只畫面上的 20 則），只取到 until 為止已取得的資料（避免未來資訊）"""
        q = "SELECT news_id, content_hash, ticker, title, source, source_url, published_at, fetched_at, official FROM news_raw WHERE ticker=?"
        args = [ticker]
        if since:
            q += " AND published_at >= ?"
            args.append(_iso(since))
        rows = self.conn.execute(q, args).fetchall()
        items = []
        for r in rows:
            fetched = _parse(r[7])
            if until and fetched and fetched > until:
                continue
            items.append(ns.NewsItem(news_id=r[0], content_hash=r[1], ticker=r[2], title=r[3], source=r[4],
                                     source_url=r[5], published_at=_parse(r[6]), fetched_at=fetched,
                                     official=bool(r[8])))
        return items

    # ------------------------------------------------------------- L2
    def classify_cached(self, it, code: str, names, model=ns.CLASSIFIER_MODEL,
                        model_version=ns.CLASSIFIER_VERSION, prompt_version=ns.CLASSIFIER_PROMPT_VERSION,
                        classifier=None):
        """
        分類結果快取：同一則新聞內容、同一分類模型與版本，只分類一次。
        classifier 預設是規則式 ns.classify_item；換成 AI 分類器時只要傳入另一個函式並更新版本。
        """
        classifier = classifier or ns.classify_item
        key = (it.news_id, it.content_hash, model, model_version, prompt_version)
        row = self.conn.execute(
            "SELECT label, score, importance, event_type, relevance, reason, rumor, flag FROM classifications "
            "WHERE news_id=? AND content_hash=? AND model=? AND model_version=? AND prompt_version=?", key).fetchone()
        if row:
            it.label, it.score, it.importance, it.event_type, it.relevance, it.reason = row[:6]
            it.rumor, it.flag = bool(row[6]), row[7]
            it.period_tokens = ns.extract_periods(it.title)
            return it
        classifier(it, code, names)
        self.classify_calls += 1
        with self.lock:
            self.conn.execute("INSERT OR REPLACE INTO classifications VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                              key + (it.label, it.score, it.importance, it.event_type, it.relevance, it.reason,
                                     int(it.rumor), it.flag, _iso(_now())))
            self.conn.commit()
        return it

    # ------------------------------------------------------------- 事件
    def save_events(self, events: list):
        with self.lock:
            for ev in events:
                self.conn.execute(
                    "INSERT OR REPLACE INTO events VALUES (?,?,?,?,?,?,?,?)",
                    (ev.event_id, ev.version, ev.ticker, ev.status, ev.title,
                     json.dumps([i.news_id for i in ev.items]), json.dumps(ev.history, ensure_ascii=False),
                     _iso(ev.last_updated)))
            self.conn.commit()

    @staticmethod
    def event_version_set(events: list) -> str:
        """目前事件版本集合的穩定雜湊（快取鍵用）"""
        return ns.sha1(repr(sorted((e.event_id, e.version, e.status, len(e.items)) for e in events)))

    # ------------------------------------------------------------- L3
    def quant_cache_get(self, key: str):
        row = self.conn.execute("SELECT result FROM quant_cache WHERE cache_key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def quant_cache_put(self, key: str, ticker: str, as_of_session: str, evset: str, result: dict):
        with self.lock:
            self.conn.execute("INSERT OR REPLACE INTO quant_cache VALUES (?,?,?,?,?,?,?,?)",
                              (key, ticker, as_of_session, ns.FORMULA_VERSION, ns.PARAMS_VERSION, evset,
                               json.dumps(result, ensure_ascii=False, default=str), _iso(_now())))
            self.conn.commit()

    # ------------------------------------------------------------- 快照 / 查詢紀錄
    def log_fetch(self, ticker: str, trading_date: str, status: str, n_raw: int = 0, error: str = ""):
        with self.lock:
            # 每個交易日、每種狀態只留一筆（記錄最後一次時間與執行次數），畫面重新整理不會讓紀錄一直變多
            self.conn.execute(
                "INSERT INTO fetch_log VALUES (?,?,?,?,?,?,1) ON CONFLICT(ticker, trading_date, status) "
                "DO UPDATE SET run_at=excluded.run_at, n_raw=excluded.n_raw, error=excluded.error, runs=runs+1",
                (ticker, trading_date, _iso(_now()), status, n_raw, error[:300]))
            self.conn.commit()

    def save_snapshot(self, ticker: str, trading_date: str, snap: dict):
        """每個交易日一筆（同一天重跑會用最新資料覆蓋，data_cutoff 會更新）"""
        with self.lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (ticker, trading_date, snap["formula_version"], snap["nsi"], snap["label"], snap["display_label"],
                 snap["K_incl"], snap["n_eff"], snap["A"], snap["omega"], snap["robustness"],
                 json.dumps(snap.get("major_events", []), ensure_ascii=False), snap.get("n_raw"), snap.get("n_rel"),
                 snap.get("k_events"), snap["data_cutoff"], _iso(_now())))
            self.conn.commit()

    def snapshots(self, ticker: str, formula_version: str = ns.FORMULA_VERSION) -> dict:
        rows = self.conn.execute(
            "SELECT trading_date, nsi, label, display_label, k_incl, n_eff, a, omega, robustness, data_cutoff "
            "FROM snapshots WHERE ticker=? AND formula_version=? ORDER BY trading_date", (ticker, formula_version)).fetchall()
        keys = ["trading_date", "nsi", "label", "display_label", "K_incl", "n_eff", "A", "omega", "robustness", "data_cutoff"]
        return {r[0]: {**dict(zip(keys, r)), "formula_version": formula_version} for r in rows}

    def fetch_status_by_day(self, ticker: str) -> dict:
        """每個交易日的查詢狀態：只要當天有一次成功就算 ok，否則 failed"""
        out = {}
        for d, status in self.conn.execute("SELECT trading_date, status FROM fetch_log WHERE ticker=?", (ticker,)):
            if out.get(d) != "ok":
                out[d] = status
        return out

    def last_display_label(self, ticker: str, before: str) -> str | None:
        row = self.conn.execute(
            "SELECT display_label FROM snapshots WHERE ticker=? AND trading_date<? AND display_label IS NOT NULL "
            "ORDER BY trading_date DESC LIMIT 1", (ticker, before)).fetchone()
        return row[0] if row else None

    # ------------------------------------------------------------- L4
    def save_summary(self, rec: dict):
        with self.lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO summaries VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (rec["cache_key"], rec["ticker"], rec["event_version_set"], rec["snapshot_id"], rec["model"],
                 rec["model_version"], rec["prompt_version"], rec["fingerprint"], rec["generated_at"],
                 rec["data_cutoff"], json.dumps(rec["source_event_ids"]), json.dumps(rec["content"], ensure_ascii=False)))
            self.conn.commit()

    def latest_summary(self, ticker: str, model_version: str, prompt_version: str) -> dict | None:
        row = self.conn.execute(
            "SELECT cache_key, fingerprint, generated_at, data_cutoff, source_event_ids, content, model "
            "FROM summaries WHERE ticker=? AND model_version=? AND prompt_version=? ORDER BY generated_at DESC LIMIT 1",
            (ticker, model_version, prompt_version)).fetchone()
        if not row:
            return None
        return {"cache_key": row[0], "fingerprint": row[1], "generated_at": row[2], "data_cutoff": row[3],
                "source_event_ids": json.loads(row[4]), "content": json.loads(row[5]), "model": row[6]}

    def record_manual_run(self, ticker: str):
        with self.lock:
            self.conn.execute("INSERT INTO manual_runs VALUES (?,?)", (ticker, _iso(_now())))
            self.conn.commit()

    def last_manual_run(self, ticker: str) -> datetime | None:
        row = self.conn.execute("SELECT MAX(run_at) FROM manual_runs WHERE ticker=?", (ticker,)).fetchone()
        return _parse(row[0]) if row and row[0] else None
