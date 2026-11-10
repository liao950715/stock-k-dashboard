# -*- coding: utf-8 -*-
"""
AI 分析摘要（L4）

觸發機制：自動顯示快取摘要 ＋ 重要資料變化時自動更新 ＋ 手動重新分析（有冷卻時間）
  * 重要資料變化 = 摘要指紋改變（重大事件 / 版本 / 狀態、警示、顯示標籤、模型或 prompt 版本）
  * 摘要超過 24 小時、且有新的有效事件 → 也會更新
  * 純時間衰減造成的 NSI 小幅變化不會重新呼叫 AI

AI 只負責「用中文解釋」程式算好的數字，不能自己計算或修改分數；
輸出會驗證 event_id 與數字，不通過就當作失敗處理。

沒有 API 金鑰時：AI 摘要功能未啟用，畫面顯示規則式摘要（明確標示不是 AI 生成）。
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timedelta, timezone

import news_sentiment as ns

MODEL = "claude-opus-5-5"
SUMMARY_MAX_AGE = timedelta(hours=24)
MANUAL_COOLDOWN = timedelta(seconds=60)

_locks: dict = {}
_locks_guard = threading.Lock()
# 失敗冷卻：同一檔股票、同一份資料（指紋）產生失敗後，30 分鐘內不自動重試，
# 避免每次重新整理頁面都去呼叫 API、一直花錢；手動「重新分析」不受影響
FAILURE_BACKOFF = timedelta(minutes=30)
_failures: dict = {}


def _lock_for(key: str) -> threading.Lock:
    """同一檔股票同時只允許一個摘要在生成，避免多個 rerun / 使用者重複呼叫 API"""
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "overall": {"type": "string"},
        "change": {"type": "string"},
        "positives": {"type": "array", "items": {
            "type": "object",
            "properties": {"text": {"type": "string"}, "event_ids": {"type": "array", "items": {"type": "string"}}},
            "required": ["text", "event_ids"], "additionalProperties": False}},
        "negatives": {"type": "array", "items": {
            "type": "object",
            "properties": {"text": {"type": "string"}, "event_ids": {"type": "array", "items": {"type": "string"}}},
            "required": ["text", "event_ids"], "additionalProperties": False}},
        "risks": {"type": "array", "items": {"type": "string"}},
        "limitations": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["overall", "change", "positives", "negatives", "risks", "limitations"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = (
    "你是股票新聞分析助理。你會收到一份由程式計算好的新聞情緒資料（JSON）。"
    "請用繁體中文、精簡地說明：整體情緒、情緒變化、正面因素、負面因素、重大風險、分析限制。\n"
    "規則：\n"
    "1. 只能使用輸入中提供的數字，不得自行計算、四捨五入成其他數字或創造新的分數。\n"
    "2. 正面 / 負面因素必須引用輸入中存在的 event_id。\n"
    "3. 標示為「未證實（傳聞）」的事件不可寫成已發生的事實。\n"
    "4. 不要做股價預測或投資建議。\n"
    "5. 每一段不超過兩句。\n"
    "6. 讀者是一般投資人：不要寫出程式欄位名稱（例如 K_incl、n_eff、score、null），改用中文說法，"
    "例如「納入計算的事件數」「權重等效事件數」。\n"
    "7. 欄位值是 null 代表資料不足，請寫「資料不足」，不要寫 null。\n"
    "8. 不要引用單一事件的情緒分數（例如 0.5）。\n"
    "9. 權重等效事件數只代表權重集中程度，不是獨立樣本數，不要稱為「有效樣本數」。"
)


class AnthropicSummarizer:
    """使用 Claude API 產生摘要；沒有金鑰或套件時 available = False"""

    model = MODEL
    model_version = MODEL
    display_name = "Claude Opus 5.5"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self.available = False
        self.reason = ""
        if not self.api_key:
            self.reason = "尚未設定 ANTHROPIC_API_KEY"
            return
        try:
            import anthropic  # noqa: F401
        except ImportError:
            self.reason = "未安裝 anthropic 套件"
            return
        self.available = True

    def generate(self, inp: dict) -> dict:
        import anthropic
        client = anthropic.Anthropic(api_key=self.api_key, max_retries=2, timeout=60.0)
        # fallbacks="default"：若主模型因安全分類拒絕回答，伺服器會自動改用合適的模型重試
        response = client.beta.messages.create(
            model=self.model,
            max_tokens=4000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": SUMMARY_SCHEMA}},
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": json.dumps(inp, ensure_ascii=False, default=str)}],
        )
        if response.stop_reason == "refusal":
            raise RuntimeError("模型拒絕產生摘要")
        if response.stop_reason == "max_tokens":
            raise RuntimeError("摘要輸出被截斷")
        text = next((b.text for b in response.content if b.type == "text"), None)
        if not text:
            raise RuntimeError("模型沒有回傳文字")
        return json.loads(text)


class OpenRouterSummarizer:
    """
    透過 OpenRouter（https://openrouter.ai）呼叫 Claude 產生摘要。
    使用 OpenRouter 的 HTTP API（/api/v1/chat/completions），用 Python 標準函式庫送出，不需要額外套件。
    模型 anthropic/claude-opus-5.5，要求回傳符合 SUMMARY_SCHEMA 的 JSON（structured outputs）。
    """

    model = "anthropic/claude-opus-5.5"
    model_version = "openrouter:anthropic/claude-opus-5.5"
    display_name = "Claude Opus 5.5 · OpenRouter"
    url = "https://openrouter.ai/api/v1/chat/completions"

    def __init__(self, api_key: str | None = None):
        self.api_key = (api_key or os.environ.get("OPENROUTER_API_KEY") or "").strip()
        self.available = bool(self.api_key)
        self.reason = "" if self.available else "尚未設定 OPENROUTER_API_KEY"

    def build_request(self, inp: dict) -> dict:
        """組出送給 OpenRouter 的 JSON（單獨拆出來，方便測試）"""
        return {
            "model": self.model,
            "max_tokens": 4000,
            "reasoning": {"effort": "low"},
            "response_format": {"type": "json_schema",
                                "json_schema": {"name": "news_summary", "strict": True, "schema": SUMMARY_SCHEMA}},
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(inp, ensure_ascii=False, default=str)},
            ],
        }

    def parse_response(self, data: dict) -> dict:
        """從 OpenRouter 的回應取出 JSON 摘要；任何異常都丟出錯誤（不偽造結果）"""
        if "error" in data:
            err = data["error"]
            raise RuntimeError(f"OpenRouter 錯誤：{err.get('message', err) if isinstance(err, dict) else err}")
        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError("OpenRouter 沒有回傳結果")
        choice = choices[0]
        finish = choice.get("finish_reason")
        if finish == "length":
            raise RuntimeError("摘要輸出被截斷")
        if finish in ("content_filter", "refusal"):
            raise RuntimeError("模型拒絕產生摘要")
        content = (choice.get("message") or {}).get("content")
        if isinstance(content, list):   # 有些模型會回傳分段的內容
            content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
        if not content:
            raise RuntimeError("模型沒有回傳文字")
        return json.loads(content)

    def generate(self, inp: dict) -> dict:
        import urllib.error
        import urllib.request
        body = json.dumps(self.build_request(inp), ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(self.url, data=body, method="POST", headers={
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "X-Title": "Stock News Sentiment",
        })
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")[:300]
            if e.code == 401:
                raise RuntimeError("OpenRouter 金鑰無效（401），請檢查 Secrets 裡的 OPENROUTER_API_KEY") from None
            if e.code == 402:
                raise RuntimeError("OpenRouter 帳戶餘額不足（402）") from None
            if e.code == 429:
                raise RuntimeError("OpenRouter 請求太頻繁（429），請稍後再試") from None
            raise RuntimeError(f"OpenRouter HTTP {e.code}：{detail}") from None
        except urllib.error.URLError as e:
            raise RuntimeError(f"連不上 OpenRouter：{e.reason}") from None
        return self.parse_response(data)


def pick_summarizer(openrouter_key: str | None = None, anthropic_key: str | None = None):
    """有 OPENROUTER_API_KEY 優先用 OpenRouter；否則用 Anthropic 官方 API；都沒有就回傳未啟用的 provider"""
    orouter = OpenRouterSummarizer(openrouter_key)
    if orouter.available:
        return orouter
    anth = AnthropicSummarizer(anthropic_key)
    if anth.available:
        return anth
    anth.reason = "尚未設定 OPENROUTER_API_KEY 或 ANTHROPIC_API_KEY"
    return anth


def get_summary(store, provider, ticker: str, inp: dict, evset: str, snapshot_id: str,
                data_cutoff: str, rule_summary: dict, force: bool = False, now: datetime | None = None) -> dict:
    """
    取得要顯示的摘要。回傳 dict：
      kind = "ai"（最新 AI 摘要）/ "ai_stale"（AI 失敗，顯示上次成功但已過期的摘要）/ "rule"（規則式）
      content, generated_at, data_cutoff, error, refreshed（這次是否有呼叫 API）
    """
    now = now or datetime.now(timezone.utc)
    if provider is None or not getattr(provider, "available", False):
        reason = getattr(provider, "reason", "AI 摘要功能未啟用")
        return {"kind": "rule", "content": rule_summary, "generated_at": now.isoformat(),
                "data_cutoff": data_cutoff, "error": reason, "refreshed": False}

    fp = ns.summary_fingerprint(inp, provider.model_version)
    cached = store.latest_summary(ticker, provider.model_version, ns.SUMMARY_PROMPT_VERSION)

    def needs_refresh(c):
        if force or c is None or c["fingerprint"] != fp:
            return True
        age = now - datetime.fromisoformat(c["generated_at"])
        new_events = set(e["event_id"] for e in inp["events"]) - set(c["source_event_ids"])
        return age > SUMMARY_MAX_AGE and bool(new_events)

    if not needs_refresh(cached):
        return {"kind": "ai", "content": cached["content"], "generated_at": cached["generated_at"],
                "data_cutoff": cached["data_cutoff"], "error": "", "refreshed": False}

    fail_key = (ticker, provider.model_version, fp)
    last_fail = _failures.get(fail_key)
    if not force and last_fail and now - last_fail[0] < FAILURE_BACKOFF:
        wait = int((FAILURE_BACKOFF - (now - last_fail[0])).total_seconds() // 60) + 1
        err = f"{last_fail[1]}（{wait} 分鐘內不自動重試，可按「重新分析」）"
        if cached:
            return {"kind": "ai_stale", "content": cached["content"], "generated_at": cached["generated_at"],
                    "data_cutoff": cached["data_cutoff"], "error": err, "refreshed": False}
        return {"kind": "rule", "content": rule_summary, "generated_at": now.isoformat(),
                "data_cutoff": data_cutoff, "error": err, "refreshed": False}

    lock = _lock_for(ticker)
    if not lock.acquire(blocking=False):
        # 另一個請求正在生成同一檔股票的摘要：先顯示快取，不重複呼叫
        if cached:
            return {"kind": "ai_stale", "content": cached["content"], "generated_at": cached["generated_at"],
                    "data_cutoff": cached["data_cutoff"], "error": "另一個請求正在更新摘要", "refreshed": False}
        return {"kind": "rule", "content": rule_summary, "generated_at": now.isoformat(),
                "data_cutoff": data_cutoff, "error": "另一個請求正在產生摘要", "refreshed": False}
    try:
        cached = store.latest_summary(ticker, provider.model_version, ns.SUMMARY_PROMPT_VERSION)
        if not needs_refresh(cached):
            return {"kind": "ai", "content": cached["content"], "generated_at": cached["generated_at"],
                    "data_cutoff": cached["data_cutoff"], "error": "", "refreshed": False}
        try:
            out = provider.generate(inp)
            ok, problems = ns.validate_ai_output(out, inp)
            if not ok:
                raise ValueError("AI 輸出未通過驗證：" + "；".join(problems[:3]))
        except Exception as e:  # API 失敗、拒絕、驗證不通過 → 不偽造結果
            _failures[fail_key] = (now, str(e)[:200])
            if cached:
                return {"kind": "ai_stale", "content": cached["content"], "generated_at": cached["generated_at"],
                        "data_cutoff": cached["data_cutoff"], "error": str(e), "refreshed": True}
            return {"kind": "rule", "content": rule_summary, "generated_at": now.isoformat(),
                    "data_cutoff": data_cutoff, "error": str(e), "refreshed": True}
        rec = {"cache_key": ns.sha1(f"{ticker}|{evset}|{snapshot_id}|{provider.model_version}|{ns.SUMMARY_PROMPT_VERSION}|{now.isoformat()}"),
               "ticker": ticker, "event_version_set": evset, "snapshot_id": snapshot_id, "model": provider.model,
               "model_version": provider.model_version, "prompt_version": ns.SUMMARY_PROMPT_VERSION,
               "fingerprint": fp, "generated_at": now.isoformat(), "data_cutoff": data_cutoff,
               "source_event_ids": [e["event_id"] for e in inp["events"]], "content": out}
        store.save_summary(rec)
        _failures.pop(fail_key, None)
        return {"kind": "ai", "content": out, "generated_at": rec["generated_at"], "data_cutoff": data_cutoff,
                "error": "", "refreshed": True}
    finally:
        lock.release()


def manual_allowed(store, ticker: str, session_last: datetime | None, now: datetime | None = None) -> tuple:
    """手動重新分析的冷卻：同一個使用者（session）與同一檔股票（全站）都要間隔 60 秒"""
    now = now or datetime.now(timezone.utc)
    last_global = store.last_manual_run(ticker)
    for last in (session_last, last_global):
        if last and now - last < MANUAL_COOLDOWN:
            wait = int((MANUAL_COOLDOWN - (now - last)).total_seconds()) + 1
            return False, wait
    return True, 0
