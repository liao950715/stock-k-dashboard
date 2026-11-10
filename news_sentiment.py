# -*- coding: utf-8 -*-
"""
新聞情緒量化分析 V2.1（純計算模組，不依賴 Streamlit，方便單元測試）

處理流程：
  原始新聞 → 規則式分類（情緒 / 事件類型 / 相關性 / 傳聞與更正旗標）
          → 交易日時間對齊
          → 事件分群（去重）與版本管理
          → 消息確認狀態
          → 權重 W = I × R' × T × U（含 40% 集中度限制）
          → 收縮 NSI、n_eff、A、Ω、敏感度分析、分析穩健程度
          → 重大事件警示、規則式摘要

重要說明：
  * 目前沒有 AI 分類模型，情緒 / 事件類型 / 相關性都是「規則式（關鍵字 + 標題結構）」判斷，
    版本記為 CLASSIFIER_VERSION，未經準確率驗證。
  * 所有權重、門檻都是「初始設計參數」，集中在 PARAMS，修改時要一起更新 PARAMS_VERSION。
  * NSI 是新聞情緒的描述性統計，不是股價預測。
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time as dtime, timedelta
from difflib import SequenceMatcher
from typing import Iterable
from zoneinfo import ZoneInfo

# ---------------------------------------------------------------------
# 一、版本與參數（全部集中管理）
# ---------------------------------------------------------------------
FORMULA_VERSION = "nsi-2.1.0"
PARAMS_VERSION = "params-2.1.0"
CLASSIFIER_MODEL = "rule-based"
CLASSIFIER_VERSION = "rules-2.1.2"
CLASSIFIER_PROMPT_VERSION = "none"      # 規則式分類沒有 prompt；改用 AI 分類時才會有版本

PARAMS = {
    # 五級情緒分數
    "scores": {"強烈偏多": 1.0, "偏多": 0.5, "中性": 0.0, "偏空": -0.5, "強烈偏空": -1.0},
    # 重要性等級 → 權重 I
    "importance_weight": {1: 1.0, 2: 1.5, 3: 2.0, 4: 3.0},
    # 敏感度分析用的替代重要性權重
    "alt_importance_weight": {1: 1.0, 2: 1.25, 3: 1.5, 4: 2.0},
    # 半衰期（交易日）
    "half_life": {4: 6, 3: 3, 2: 2, 1: 1},
    "expire_multiple": 3,               # t > 3H 時排除
    # 相關性門檻
    "relevance_exclude": 0.3,
    "relevance_direct": 0.7,
    "relevance_indirect_weight": 0.5,
    # 收縮先驗強度
    "w0": 2.0,
    # 集中度限制
    "cap_share": 0.4,
    "cap_min_events": 3,
    # 事件去重
    "dedup_similarity": 0.80,
    "dedup_window_hours": 48,
    "version_similarity": 0.5,          # 更正 / 否認新聞對應舊事件的最低相似度
    "version_window_days": 7,
    "syndication_similarity": 0.95,     # 不同來源的標題幾乎相同 → 視為轉載，不算獨立來源
    # 五級標籤門檻與遲滯緩衝
    "label_strong": 40.0,
    "label_mild": 15.0,
    "hysteresis_buffer": 5.0,
    # 動能
    "momentum_window": 5,
    "momentum_threshold": 10.0,
    # 多空高度分歧警示
    "divergence_A": 0.4,
    "divergence_omega": 0.6,
}

LABELS = ["強烈偏空", "偏空", "中性", "偏多", "強烈偏多"]   # 由空到多排序（遲滯判斷用）

# ---------------------------------------------------------------------
# 二、規則式分類用的關鍵字
# ---------------------------------------------------------------------
STRONG_POS = ["漲停", "大漲", "暴漲", "飆漲", "創新高", "創高", "史上新高", "噴出", "大幅成長", "倍增", "雙位數成長"]
POS = ["上漲", "成長", "增長", "優於預期", "調升", "上修", "買超", "獲利", "利多", "看好", "強勢", "突破",
       "反彈", "回升", "攀升", "加碼", "雙增", "新高", "受惠", "擴產", "大單", "接單", "旺", "V轉", "V型反轉", "V形反轉"]
STRONG_NEG = ["跌停", "大跌", "暴跌", "重挫", "崩跌", "崩盤", "虧損擴大", "大幅衰退", "腰斬", "創新低"]
NEG = ["下跌", "下滑", "衰退", "減少", "不如預期", "調降", "下修", "賣超", "虧損", "利空", "看淡", "疲弱",
       "跌破", "走弱", "減碼", "雙減", "警示", "示警", "砍單", "停工", "裁員", "罰款"]
# 不放單獨的「跌」：像「最大跌點後 V 轉」這種標題其實是反彈，單字太容易誤判
NEUTRALIZERS = ["漲跌", "止跌", "跌深反彈", "最大跌點"]   # 先移除，避免誤判方向（「最大跌點」含有「大跌」字樣）

LEVEL4 = ["財報", "季報", "年報", "財測", "EPS", "每股盈餘", "每股純益", "重大訊息", "重訊", "股利", "配息", "減資", "增資"]
LEVEL3 = ["月營收", "營收", "法說", "訂單", "接單", "併購", "合併", "擴產", "資本支出"]
LEVEL2 = ["外資", "投信", "自營商", "法人", "買超", "賣超", "融資", "融券", "籌碼", "產業", "半導體", "供應鏈",
          "總經", "央行", "利率", "通膨", "匯率", "關稅", "美股", "費半", "AI", "類股"]
EVENT_TYPE = {4: "財報 / 財測 / 重大訊息", 3: "月營收 / 法說 / 訂單", 2: "產業 / 總經 / 籌碼", 1: "一般新聞 / 評論"}

# 注意：不放單獨的「爆料」，因為有媒體名稱叫「股市爆料同學會」，會被誤判成傳聞
RUMOR = ["傳出", "傳聞", "傳言", "據傳", "市場傳", "消息指出", "據悉", "傳將", "傳為", "傳已", "爆料指出", "網傳", "傳 "]
DENY = ["否認", "闢謠", "不實", "澄清非事實"]
CORRECT = ["更正", "澄清", "修正"]
WITHDRAW = ["撤回", "撤銷", "取消"]
LISTY = ["TOP", "排行", "名單", "一覽", "懶人包", "盤後", "盤中", "類股", "個股", "成交量", "漲幅榜", "跌幅榜"]

STATUS_OFFICIAL = "官方確認"
STATUS_MULTI = "多個獨立來源支持（未官方確認）"
STATUS_SINGLE = "單一來源"
STATUS_RUMOR = "未證實（傳聞）"
STATUS_CONFLICT = "來源互相矛盾"
STATUS_DENIED = "已否認"
STATUS_SUPERSEDED = "已更正或取代"


# ---------------------------------------------------------------------
# 三、資料結構
# ---------------------------------------------------------------------
@dataclass
class NewsItem:
    """一則原始新聞（L1 資料）加上規則式分類結果（L2）"""
    news_id: str
    ticker: str
    title: str
    source: str
    source_url: str
    published_at: datetime | None          # 原始發布時間（含時區）
    fetched_at: datetime                   # 系統第一次取得的時間（含時區）
    content_hash: str
    official: bool = False                 # 是否來自官方公告（證交所 / 櫃買中心重大訊息）
    # 分類結果
    label: str = "中性"
    score: float = 0.0
    importance: int = 1
    event_type: str = EVENT_TYPE[1]
    relevance: float = 0.0
    reason: str = ""
    rumor: bool = False
    flag: str = ""                         # "denied" / "corrected" / "withdrawn" / ""
    period_tokens: tuple = ()              # 期別（季、月、年），用來避免不同期財報被合併


@dataclass
class Event:
    event_id: str
    ticker: str
    items: list                            # 目前版本的新聞
    version: int = 1
    history: list = field(default_factory=list)     # [{version, status, news_ids, at}]
    status: str = STATUS_SINGLE
    superseded_items: list = field(default_factory=list)

    # --- 衍生欄位 ---
    @property
    def title(self):
        return self.items[0].title if self.items else ""

    @property
    def importance(self):
        return max((i.importance for i in self.items), default=1)

    @property
    def event_type(self):
        return EVENT_TYPE[self.importance]

    @property
    def relevance(self):
        return max((i.relevance for i in self.items), default=0.0)

    @property
    def score(self):
        """同一事件多篇報導：情緒分數取平均"""
        vals = [i.score for i in self.items]
        return sum(vals) / len(vals) if vals else 0.0

    @property
    def divergence(self):
        """來源之間的分歧：最高分 − 最低分"""
        vals = [i.score for i in self.items]
        return (max(vals) - min(vals)) if vals else 0.0

    @property
    def first_published(self):
        times = [i.published_at for i in self.items if i.published_at]
        return min(times) if times else None

    @property
    def last_updated(self):
        times = [i.published_at for i in self.items if i.published_at]
        return max(times) if times else None

    @property
    def sources(self):
        return sorted({i.source for i in self.items})

    @property
    def rumor(self):
        return any(i.rumor for i in self.items) and not any(i.official for i in self.items)


# ---------------------------------------------------------------------
# 四、工具：雜湊、標題正規化、相似度
# ---------------------------------------------------------------------
def sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def normalize_title(title: str) -> str:
    """去掉空白、標點、括號內的來源標記，只留下中英數字，用來比較相似度"""
    t = re.sub(r"[｜|【】\[\]（）()《》「」『』,，。.!！?？:：;；\-—–_~～/\\\s]", "", title or "")
    return t.lower()


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, normalize_title(a), normalize_title(b)).ratio()


def make_news_id(source_url: str, source: str, title: str) -> str:
    return sha1(source_url) if source_url else sha1(f"{source}|{title}")


def make_content_hash(title: str, source: str) -> str:
    return sha1(f"{normalize_title(title)}|{source}")


PERIOD_PATTERNS = [
    r"Q[1-4]", r"第[一二三四1-4]季", r"[上下]半年", r"\d{2,4}年", r"\d{1,2}月(?!\d)", r"\d{1,2}/\d{1,2}",
]


def extract_periods(title: str) -> tuple:
    """抓出標題中的期別字樣（季度、月份、年份、日期），不同期別的新聞不能合併成同一事件"""
    found = []
    for p in PERIOD_PATTERNS:
        found += re.findall(p, title or "")
    return tuple(sorted(set(found)))


# ---------------------------------------------------------------------
# 五、規則式分類
# ---------------------------------------------------------------------
def _hits(text, words):
    return [w for w in words if w in text]


def classify_sentiment(title: str):
    """
    五級情緒：
      強烈字（漲停、重挫…）算 2 分，一般字（成長、下滑…）算 1 分；
      多方總分 > 空方總分 → 偏多，只有多方強烈字、且沒有空方字 → 強烈偏多；空方對稱。
    回傳 (標籤, 分數, 判斷依據)
    """
    text = title or ""
    for n in NEUTRALIZERS:
        text = text.replace(n, "")
    sp, p = _hits(text, STRONG_POS), _hits(text, POS)
    sn, n = _hits(text, STRONG_NEG), _hits(text, NEG)
    # 強烈字通常也包含一般字（例如「大漲」含「漲」），已經算過強烈字的就不重複算一般字
    p = [w for w in p if not any(w in s for s in sp)]
    n = [w for w in n if not any(w in s for s in sn)]
    pos_pts, neg_pts = 2 * len(sp) + len(p), 2 * len(sn) + len(n)
    if pos_pts == neg_pts:
        label = "中性"
    elif pos_pts > neg_pts:
        label = "強烈偏多" if sp and neg_pts == 0 else "偏多"
    else:
        label = "強烈偏空" if sn and pos_pts == 0 else "偏空"
    words = []
    if sp or p:
        words.append("偏多字詞：" + "、".join(sp + p))
    if sn or n:
        words.append("偏空字詞：" + "、".join(sn + n))
    reason = "；".join(words) if words else "標題沒有明確的偏多或偏空字詞"
    return label, PARAMS["scores"][label], reason


def classify_importance(title: str, official: bool = False):
    """事件重要性等級（1～4）與事件類型；官方重大訊息一律為等級 4"""
    if official:
        return 4, EVENT_TYPE[4]
    text = title or ""
    for level, words in ((4, LEVEL4), (3, LEVEL3), (2, LEVEL2)):
        if _hits(text, words):
            return level, EVENT_TYPE[level]
    return 1, EVENT_TYPE[1]


def classify_relevance(title: str, code: str, names: Iterable[str]):
    """
    個股相關性 R（0～1），規則：
      * 標題直接提到這家公司（代碼 / 名稱 / 別名），而且不是多檔股票的清單、排行 → 0.85（直接相關）
      * 有提到，但同時列了很多檔股票或是排行榜類標題                         → 0.5（間接相關）
      * 沒提到公司，但有產業、供應鏈等關鍵字                                → 0.35（間接相關）
      * 都沒有                                                              → 0.15（排除）
    不會只因為標題出現股票名稱就判定高度相關。
    """
    text = title or ""
    names = [n for n in names if n]
    mentioned = (code and code in text) or any(n in text for n in names)
    other_codes = [c for c in re.findall(r"(?<!\d)(\d{4,6})(?!\d)", text) if c != code]
    listy = bool(_hits(text.upper(), LISTY)) or len(other_codes) >= 2 or text.count("、") >= 3
    if mentioned and not listy:
        return 0.85, "標題直接提到這家公司"
    if mentioned:
        return 0.5, "標題提到這家公司，但同時列了多檔股票或屬於排行 / 盤勢整理"
    if _hits(text, LEVEL2):
        return 0.35, "沒有直接提到公司，只有產業 / 總經關聯"
    return 0.15, "標題與這家公司沒有明確關聯"


def classify_flags(title: str):
    """傳聞、否認、更正、撤回旗標"""
    text = title or ""
    if _hits(text, DENY):
        flag = "denied"
    elif _hits(text, WITHDRAW):
        flag = "withdrawn"
    elif _hits(text, CORRECT):
        flag = "corrected"
    else:
        flag = ""
    # 「傳台積電將…」這種以「傳」開頭（或接在標點後面）的標題也算傳聞
    rumor = bool(_hits(text, RUMOR)) or bool(re.search(r"(^|[，、：:】\]\s])傳(?![統承輸真送播])", text))
    return rumor, flag


def classify_item(item: NewsItem, code: str, names: Iterable[str]) -> NewsItem:
    """對一則新聞做完整的規則式分類（就地更新並回傳）"""
    item.label, item.score, item.reason = classify_sentiment(item.title)
    item.importance, item.event_type = classify_importance(item.title, item.official)
    item.relevance, rel_reason = classify_relevance(item.title, code, names)
    if item.official:
        item.relevance = max(item.relevance, 0.9)
        rel_reason = "公司官方公告"
    item.rumor, item.flag = classify_flags(item.title)
    item.period_tokens = extract_periods(item.title)
    item.reason = f"{item.reason}；相關性：{rel_reason}"
    return item


def relevance_weight(r: float) -> float:
    """相關性門檻化：R < 0.3 排除；0.3～0.7 → 0.5；≥ 0.7 → 1.0"""
    if r < PARAMS["relevance_exclude"]:
        return 0.0
    if r < PARAMS["relevance_direct"]:
        return PARAMS["relevance_indirect_weight"]
    return 1.0


# ---------------------------------------------------------------------
# 六、交易日時間對齊
# ---------------------------------------------------------------------
@dataclass
class MarketCalendar:
    """
    交易日曆：
      * known_days：從實際行情資料得到的交易日（過去的日期以這個為準，最可靠）
      * holidays  ：交易所公告的休市日（用來判斷「還沒有行情資料」的未來日期）
      * 其他未來日期：週一到週五視為交易日
    """
    tz: str = "Asia/Taipei"
    open_time: dtime = dtime(9, 0)
    close_time: dtime = dtime(13, 30)
    known_days: frozenset = frozenset()
    holidays: frozenset = frozenset()

    @property
    def last_known(self):
        return max(self.known_days) if self.known_days else None

    def is_trading_day(self, d: date) -> bool:
        if self.known_days and d <= self.last_known and d >= min(self.known_days):
            return d in self.known_days
        return d.weekday() < 5 and d not in self.holidays

    def next_trading_day(self, d: date) -> date:
        d = d + timedelta(days=1)
        while not self.is_trading_day(d):
            d += timedelta(days=1)
        return d

    def prev_or_same_trading_day(self, d: date) -> date:
        while not self.is_trading_day(d):
            d -= timedelta(days=1)
        return d

    def availability_session(self, ts: datetime) -> date:
        """
        新聞「可以被市場用到」的交易日：
          * 交易日收盤前發布（盤前、盤中）→ 當天
          * 收盤後、週末、假日發布        → 下一個交易日
        """
        local = ts.astimezone(ZoneInfo(self.tz))
        d = local.date()
        if self.is_trading_day(d) and local.time() < self.close_time:
            return d
        return self.next_trading_day(d)

    def as_of_session(self, ts: datetime) -> date:
        """分析時間點對應的交易日：交易日開盤後算當天，其餘時間算前一個（含當天以前最近的）交易日"""
        local = ts.astimezone(ZoneInfo(self.tz))
        d = local.date()
        if self.is_trading_day(d) and local.time() >= self.open_time:
            return d
        return self.prev_or_same_trading_day(d - timedelta(days=1))

    def trading_days_between(self, start: date, end: date) -> int:
        """start（不含）到 end（含）之間有幾個交易日；end 早於 start 時回傳 0"""
        if end <= start:
            return 0
        n, d = 0, start
        while d < end:
            d = self.next_trading_day(d)
            if d <= end:
                n += 1
        return n

    def age(self, published: datetime, as_of: datetime) -> int:
        """
        統一的時間尺度：新聞從「可用交易日」到「分析交易日」經過幾個交易日（t，整數）。
        例：週五收盤後發布 → 可用日是下週一；週一盤前分析 → 分析日是週五 → t = 0。
        """
        return self.trading_days_between(self.availability_session(published), self.as_of_session(as_of))


def time_decay(t: float, level: int) -> float:
    """T = 2^(−t / H)；t > 3H 時回傳 0（排除）"""
    h = PARAMS["half_life"][level]
    if t > PARAMS["expire_multiple"] * h:
        return 0.0
    return 2 ** (-t / h)


# ---------------------------------------------------------------------
# 七、事件分群（去重）與版本管理
# ---------------------------------------------------------------------
def _periods_compatible(a: NewsItem, b: NewsItem) -> bool:
    """兩則新聞都有期別字樣時，期別必須相同（避免把不同季的財報合併）"""
    if a.period_tokens and b.period_tokens:
        return set(a.period_tokens) == set(b.period_tokens)
    return True


def _same_event(item: NewsItem, ev: Event) -> bool:
    if not ev.items or not item.published_at:
        return False
    if item.ticker != ev.ticker:
        return False
    window = timedelta(hours=PARAMS["dedup_window_hours"])
    for other in ev.items:
        if other.published_at and abs(item.published_at - other.published_at) <= window:
            if (similarity(item.title, other.title) >= PARAMS["dedup_similarity"]
                    and _periods_compatible(item, other)
                    and item.importance == other.importance):
                return True
    return False


def _related_for_version(item: NewsItem, ev: Event) -> bool:
    """更正 / 否認 / 撤回的新聞，要找到它在講的那個舊事件"""
    if not ev.items or not item.published_at or ev.ticker != item.ticker:
        return False
    window = timedelta(days=PARAMS["version_window_days"])
    for other in ev.items:
        if other.published_at and timedelta(0) <= item.published_at - other.published_at <= window:
            if similarity(item.title, other.title) >= PARAMS["version_similarity"] and _periods_compatible(item, other):
                return True
    return False


def _independent_sources(items: list) -> int:
    """
    獨立來源數：
      * 同一家媒體的多篇只算 1
      * 不同媒體但標題幾乎一樣（≥ 0.95，通常是轉載同一篇稿）也只算 1
    """
    groups = []
    for it in items:
        placed = False
        for g in groups:
            if it.source in {x.source for x in g} or any(
                    similarity(it.title, x.title) >= PARAMS["syndication_similarity"] for x in g):
                g.append(it)
                placed = True
                break
        if not placed:
            groups.append([it])
    return len(groups)


def _status_of(ev: Event) -> str:
    flags = {i.flag for i in ev.items}
    if "denied" in flags:
        return STATUS_DENIED
    if "withdrawn" in flags:
        return STATUS_SUPERSEDED
    if any(i.official for i in ev.items):
        return STATUS_OFFICIAL
    scores = [i.score for i in ev.items]
    if any(s > 0 for s in scores) and any(s < 0 for s in scores):
        return STATUS_CONFLICT
    if _independent_sources(ev.items) >= 2:
        return STATUS_MULTI
    if ev.rumor:
        return STATUS_RUMOR
    return STATUS_SINGLE


def build_events(items: list) -> list:
    """
    依發布時間排序後逐則處理：
      * 更正 / 否認 / 撤回的新聞 → 找到對應的舊事件，建立新版本（舊版本的新聞不再計票）
      * 與既有事件相似（≥ 0.80、48 小時內、期別與事件等級相同）→ 併入同一事件
      * 否則 → 新事件
    """
    events: list[Event] = []
    ordered = sorted([i for i in items if i.published_at], key=lambda i: i.published_at)
    for it in ordered:
        target = None
        if it.flag:
            for ev in reversed(events):
                if _related_for_version(it, ev):
                    target = ev
                    break
            if target is not None:
                target.history.append({"version": target.version, "status": target.status,
                                       "news_ids": [x.news_id for x in target.items],
                                       "at": target.last_updated.isoformat() if target.last_updated else None})
                target.superseded_items += target.items
                target.items = [it]
                target.version += 1
                target.status = _status_of(target)
                continue
        for ev in reversed(events):
            if _same_event(it, ev):
                target = ev
                break
        if target is not None:
            target.items.append(it)
            target.status = _status_of(target)
        else:
            ev = Event(event_id="ev-" + sha1(f"{it.ticker}|{it.news_id}"), ticker=it.ticker, items=[it])
            ev.status = _status_of(ev)
            events.append(ev)
    for ev in events:
        ev.history.append({"version": ev.version, "status": ev.status,
                           "news_ids": [x.news_id for x in ev.items],
                           "at": ev.last_updated.isoformat() if ev.last_updated else None})
    return events


# ---------------------------------------------------------------------
# 八、權重與集中度限制
# ---------------------------------------------------------------------
def cap_weights(weights: list, cap: float = None, min_events: int = None):
    """
    單一事件權重占比上限（預設 40%）：
      * 事件數 < 3：不做硬性上限（數學上不可行），回傳原權重
      * 事件數 ≥ 3：找出最少要被「壓到上限」的事件數 m，
        使被壓的事件權重 = cap × 調整後總和，且所有未被壓的事件都 ≤ 這個值。
        這是一次求出的封閉解（依序嘗試 m = 0, 1, 2…），一定會停止；找不到可行解時回報 infeasible。
    回傳 (調整後權重, 是否觸發上限, 是否可行)
    """
    cap = PARAMS["cap_share"] if cap is None else cap
    min_events = PARAMS["cap_min_events"] if min_events is None else min_events
    w = [max(0.0, float(x)) for x in weights]
    total = sum(w)
    k = sum(1 for x in w if x > 0)
    if total <= 0 or k < min_events:
        return w, False, True
    if max(w) <= cap * total + 1e-12:
        return w, False, True
    order = sorted(range(len(w)), key=lambda i: w[i], reverse=True)
    for m in range(1, len(w)):
        if m * cap >= 1:
            break
        rest = sum(w[i] for i in order[m:])
        if rest <= 0:
            break
        capped_value = cap * rest / (1 - m * cap)
        largest_rest = w[order[m]]
        smallest_capped = w[order[m - 1]]
        if largest_rest <= capped_value + 1e-12 and capped_value <= smallest_capped + 1e-12:
            out = list(w)
            for i in order[:m]:
                out[i] = capped_value
            return out, True, True
    return w, False, False


# ---------------------------------------------------------------------
# 九、NSI 與統計量
# ---------------------------------------------------------------------
def nsi_stats(weights: list, scores: list, w0: float = None) -> dict:
    """
    NSI_raw     = Σ(W·s) / ΣW
    NSI_shrunk  = Σ(W·s) / (ΣW + W0)        （W0 = 2，中性先驗）
    NSI_display = 100 × NSI_shrunk
    n_eff       = (ΣW)² / Σ(W²)
    A           = Σ(W·|s|) / ΣW              平均情緒強度
    Ω           = 1 − |Σ(W·s)| / Σ(W·|s|)     情緒抵銷率（分母為 0 時不計算）
    """
    w0 = PARAMS["w0"] if w0 is None else w0
    sw = sum(weights)
    sws = sum(w * s for w, s in zip(weights, scores))
    swabs = sum(w * abs(s) for w, s in zip(weights, scores))
    sw2 = sum(w * w for w in weights)
    if sw <= 0:
        return {"valid": False, "sum_w": 0.0, "nsi_raw": None, "nsi_shrunk": None, "nsi": None,
                "n_eff": 0.0, "A": None, "omega": None, "no_direction": True}
    nsi_shrunk = sws / (sw + w0)
    return {
        "valid": True,
        "sum_w": sw,
        "nsi_raw": sws / sw,
        "nsi_shrunk": nsi_shrunk,
        "nsi": 100 * nsi_shrunk,
        "n_eff": sw * sw / sw2 if sw2 > 0 else 0.0,
        "A": swabs / sw,
        "omega": (1 - abs(sws) / swabs) if swabs > 0 else None,
        "no_direction": swabs == 0,
    }


def label_from_score(nsi: float | None) -> str | None:
    """五級標籤（≥ +40 強烈偏多；+15～+40 偏多；−15～+15 中性；−40～−15 偏空；≤ −40 強烈偏空）"""
    if nsi is None:
        return None
    s, m = PARAMS["label_strong"], PARAMS["label_mild"]
    if nsi >= s:
        return "強烈偏多"
    if nsi >= m:
        return "偏多"
    if nsi > -m:
        return "中性"
    if nsi > -s:
        return "偏空"
    return "強烈偏空"


def display_label(nsi: float | None, previous: str | None) -> str | None:
    """
    UI 顯示用的標籤（遲滯機制）：
      正式 NSI 與正式標籤不受影響；只有「畫面上的標籤」要多越過 5 分緩衝才切換，
      避免分數在門檻附近來回跳動時，標籤與 AI 摘要一直重算。
    """
    raw = label_from_score(nsi)
    if raw is None or previous not in LABELS or raw == previous:
        return raw
    direction = 1 if LABELS.index(raw) > LABELS.index(previous) else -1
    if label_from_score(nsi - direction * PARAMS["hysteresis_buffer"]) == previous:
        return previous
    return raw


# ---------------------------------------------------------------------
# 十、完整計算：從事件到 NSI、敏感度、穩健程度
# ---------------------------------------------------------------------
def eligibility(ev: Event) -> tuple:
    """事件能不能納入正式 NSI：(可以?, 原因)"""
    if relevance_weight(ev.relevance) == 0:
        return False, "相關性不足（R < 0.3）"
    if ev.status == STATUS_DENIED:
        return False, "已被否認，不再以原方向計分"
    if ev.status == STATUS_SUPERSEDED:
        return False, "已撤回或被取代"
    if ev.status == STATUS_RUMOR:
        return False, "未證實傳聞，只列入警示通道"
    return True, ""


def _event_weight(ev: Event, t: int, importance_table=None, use_decay=True) -> float:
    importance_table = importance_table or PARAMS["importance_weight"]
    i = importance_table[ev.importance]
    r = relevance_weight(ev.relevance)
    tt = time_decay(t, ev.importance) if use_decay else (1.0 if t <= PARAMS["expire_multiple"] * PARAMS["half_life"][ev.importance] else 0.0)
    u = 1.0   # P0：分類穩定性因子固定為 1
    return i * r * tt * u


def analyze(events: list, calendar: MarketCalendar, as_of: datetime) -> dict:
    """
    對一組事件做完整的 V2.1 計算（只使用 as_of 之前已發布、已取得的新聞，不使用未來資訊）。
    """
    rows = []
    for ev in events:
        pub = ev.last_updated
        if pub is None or pub > as_of or any(i.fetched_at > as_of for i in ev.items):
            continue
        t = calendar.age(pub, as_of)
        ok, why = eligibility(ev)
        decay = time_decay(t, ev.importance)
        if ok and decay == 0:
            ok, why = False, f"已超過 3 倍半衰期（t = {t} 交易日）"
        rows.append({"event": ev, "t": t, "eligible": ok, "excluded_reason": why,
                     "I": PARAMS["importance_weight"][ev.importance], "R": ev.relevance,
                     "R_prime": relevance_weight(ev.relevance), "T": decay, "U": 1.0})
    incl = [r for r in rows if r["eligible"]]
    raw_w = [r["I"] * r["R_prime"] * r["T"] * r["U"] for r in incl]
    scores = [r["event"].score for r in incl]
    final_w, capped, feasible = cap_weights(raw_w)
    for r, w0_, w1 in zip(incl, raw_w, final_w):
        r["W_raw"], r["W"] = w0_, w1
    stats_raw = nsi_stats(raw_w, scores)
    stats = nsi_stats(final_w, scores)
    k_incl = sum(1 for w in final_w if w > 0)
    share = (max(final_w) / sum(final_w)) if final_w and sum(final_w) > 0 else None

    # --- 敏感度分析 ---
    def variant(weights):
        st = nsi_stats(weights, scores)
        return {"nsi": st["nsi"], "label": label_from_score(st["nsi"])}

    sens = {
        "正式權重（V2.1）": {"nsi": stats["nsi"], "label": label_from_score(stats["nsi"])},
        "等權重": variant([1.0 if w > 0 else 0.0 for w in raw_w]),
        "不使用時間衰減": variant(cap_weights([_event_weight(r["event"], r["t"], use_decay=False) for r in incl])[0]),
        "替代重要性權重": variant(cap_weights([_event_weight(r["event"], r["t"], PARAMS["alt_importance_weight"]) for r in incl])[0]),
    }
    vals = [v["nsi"] for v in sens.values() if v["nsi"] is not None]
    labels = {v["label"] for v in sens.values() if v["label"]}
    signs = {(1 if v > 5 else (-1 if v < -5 else 0)) for v in vals}
    consistent = bool(vals) and (len(labels) == 1 or ((max(vals) - min(vals)) < 15 and not ({1, -1} <= signs)))

    # --- 分析穩健程度 ---
    conflict_share = 0.0
    if final_w and sum(final_w) > 0:
        conflict_share = sum(w for r, w in zip(incl, final_w) if r["event"].status == STATUS_CONFLICT) / sum(final_w)
    issues = []
    if not feasible:
        issues.append("權重集中度限制不可行")
    if conflict_share >= 0.25:
        issues.append("有相互矛盾的來源占比偏高")
    if not stats["valid"]:
        robustness = "無"
    elif k_incl >= 5 and stats["n_eff"] >= 3 and consistent and not issues:
        robustness = "高"
    elif k_incl >= 3 and stats["n_eff"] >= 2 and conflict_share < 0.4 and feasible:
        robustness = "中"
    else:
        robustness = "低"
    notes = []
    if k_incl < PARAMS["cap_min_events"] and stats["valid"]:
        notes.append(f"事件數不足（{k_incl} 個），未套用 40% 集中度上限，分數已向中性收縮")
    if not consistent and stats["valid"]:
        notes.append("不同權重設定的結果不一致")
    return {
        "rows": rows, "included": incl, "stats": stats, "stats_before_cap": stats_raw,
        "K_incl": k_incl, "capped": capped, "cap_feasible": feasible, "max_share": share,
        "nsi_cap_diff": (stats["nsi"] - stats_raw["nsi"]) if stats["valid"] and stats_raw["valid"] else 0.0,
        "label": label_from_score(stats["nsi"]), "sensitivity": sens, "sensitivity_consistent": consistent,
        "robustness": robustness, "issues": issues, "notes": notes,
        "formula_version": FORMULA_VERSION, "params_version": PARAMS_VERSION,
    }


# ---------------------------------------------------------------------
# 十一、動能
# ---------------------------------------------------------------------
def momentum(today: dict | None, past: dict | None) -> dict:
    """
    M5 = NSI_d − NSI_{d−5}；兩個快照都要有效、穩健程度至少為「中」、公式版本相同，否則不計算。
    today / past 是快照 dict（含 nsi、robustness、formula_version）。
    """
    if not today or not past:
        return {"value": None, "label": "趨勢資料不足", "reason": "缺少 5 個交易日前的有效快照"}
    if today.get("nsi") is None or past.get("nsi") is None:
        return {"value": None, "label": "趨勢資料不足", "reason": "快照沒有有效 NSI"}
    if today.get("formula_version") != past.get("formula_version"):
        return {"value": None, "label": "趨勢資料不足", "reason": "兩個快照的公式版本不同"}
    if today.get("robustness") not in ("中", "高") or past.get("robustness") not in ("中", "高"):
        return {"value": None, "label": "趨勢資料不足", "reason": "快照的分析穩健程度低於「中」"}
    m = today["nsi"] - past["nsi"]
    th = PARAMS["momentum_threshold"]
    lab = "改善" if m >= th else ("惡化" if m <= -th else "大致持平")
    return {"value": m, "label": lab, "reason": ""}


# ---------------------------------------------------------------------
# 十二、重大事件警示
# ---------------------------------------------------------------------
def build_alerts(result: dict) -> list:
    """
    重大事件警示與 NSI 分開計算：
      重大利多 / 重大利空：重要性 ≥ 3、方向明確、可計分、仍在有效期間內
      未確認重大消息     ：傳聞，且重要性 ≥ 3 或情緒強烈
      來源矛盾           ：同一事件不同來源方向相反（重要性 ≥ 2）
      已否認 / 已更正    ：狀態改變的事件（仍在有效期間內才顯示，過期自動解除）
      多空高度分歧       ：整體 A ≥ 0.4 且 Ω ≥ 0.6
    """
    alerts = []
    for r in result["rows"]:
        ev, t = r["event"], r["t"]
        alive = time_decay(t, ev.importance) > 0
        if not alive or relevance_weight(ev.relevance) == 0:
            continue
        base = {"event_id": ev.event_id, "title": ev.title, "score": ev.score, "importance": ev.importance,
                "event_type": ev.event_type, "status": ev.status, "sources": ev.sources,
                "published_at": ev.first_published, "updated_at": ev.last_updated,
                "reason": ev.items[0].reason if ev.items else ""}
        if ev.status == STATUS_RUMOR and (ev.importance >= 3 or abs(ev.score) >= 1.0):
            alerts.append({**base, "kind": "未確認重大消息"})
        elif ev.status == STATUS_DENIED:
            alerts.append({**base, "kind": "已否認"})
        elif ev.status == STATUS_SUPERSEDED or (ev.version > 1 and ev.status != STATUS_DENIED):
            alerts.append({**base, "kind": "已更正或撤回"})
        elif ev.status == STATUS_CONFLICT and ev.importance >= 2:
            alerts.append({**base, "kind": "來源矛盾"})
        elif r["eligible"] and ev.importance >= 3 and ev.score >= 0.5:
            alerts.append({**base, "kind": "重大利多"})
        elif r["eligible"] and ev.importance >= 3 and ev.score <= -0.5:
            alerts.append({**base, "kind": "重大利空"})
    st = result["stats"]
    if (st["valid"] and result["K_incl"] >= 2 and st["A"] is not None and st["omega"] is not None
            and st["A"] >= PARAMS["divergence_A"] and st["omega"] >= PARAMS["divergence_omega"]):
        alerts.append({"event_id": None, "kind": "多空高度分歧", "title": "利多與利空事件同時存在且互相抵銷",
                       "score": None, "importance": None, "status": "", "sources": [],
                       "reason": f"平均情緒強度 A = {st['A']:.2f}、抵銷率 Ω = {st['omega']:.2f}"})
    return alerts


# ---------------------------------------------------------------------
# 十三、規則式摘要（沒有 AI 或 AI 失敗時使用；必須標示為規則式，不能當成 AI 生成）
# ---------------------------------------------------------------------
def rule_based_summary(result: dict, mom: dict, alerts: list) -> dict:
    st = result["stats"]
    if not st["valid"]:
        return {"overall": "目前沒有可納入計算的有效新聞事件，無法判斷新聞情緒。",
                "change": mom["label"], "positives": [], "negatives": [], "risks": [],
                "limitations": ["有效事件數為 0"]}
    pos = [r["event"] for r in result["included"] if r["event"].score > 0]
    neg = [r["event"] for r in result["included"] if r["event"].score < 0]
    pos.sort(key=lambda e: -e.importance)
    neg.sort(key=lambda e: -e.importance)
    overall = (f"新聞情緒指數 NSI 為 {st['nsi']:+.0f}，判讀為「{result['label']}」，"
               f"共納入 {result['K_incl']} 個事件，分析穩健程度為「{result['robustness']}」。")
    change = (f"與 5 個交易日前相比 {mom['value']:+.0f}（{mom['label']}）。" if mom["value"] is not None
              else f"情緒動能：{mom['label']}（{mom['reason']}）。")
    risks = [f"{a['kind']}：{a['title']}" for a in alerts if a["kind"] in ("重大利空", "未確認重大消息", "來源矛盾", "已否認", "多空高度分歧")]
    limits = ["情緒、事件類型與相關性為規則式（關鍵字）判斷，未經準確率驗證", "只依據新聞標題分析"]
    limits += result["notes"]
    return {"overall": overall, "change": change,
            "positives": [{"text": e.title, "event_ids": [e.event_id]} for e in pos[:3]],
            "negatives": [{"text": e.title, "event_ids": [e.event_id]} for e in neg[:3]],
            "risks": risks[:4], "limitations": limits}


# ---------------------------------------------------------------------
# 十四、AI 摘要：輸入資料、觸發指紋、輸出驗證
# ---------------------------------------------------------------------
SUMMARY_PROMPT_VERSION = "summary-prompt-1.0"


def summary_input(ticker: str, name: str, result: dict, mom: dict, alerts: list, shown_label: str) -> dict:
    """給 AI 的結構化輸入：所有數字都由程式提供，AI 只負責用中文解釋"""
    st = result["stats"]
    major = [r["event"] for r in result["included"] if r["event"].importance >= 3]
    return {
        "ticker": ticker, "name": name,
        "nsi": None if st["nsi"] is None else round(st["nsi"]),
        "label": shown_label, "momentum": None if mom["value"] is None else round(mom["value"]),
        "momentum_label": mom["label"], "robustness": result["robustness"],
        "A": None if st["A"] is None else round(st["A"], 2),
        "omega": None if st["omega"] is None else round(st["omega"], 2),
        "K_incl": result["K_incl"], "n_eff": round(st["n_eff"], 1),
        "events": [{"event_id": r["event"].event_id, "title": r["event"].title, "score": r["event"].score,
                    "importance": r["event"].importance, "status": r["event"].status,
                    "sources": r["event"].sources} for r in result["included"]][:25],
        "major_event_ids": [e.event_id for e in major],
        "alerts": [{"kind": a["kind"], "event_id": a["event_id"], "title": a["title"]} for a in alerts],
        "notes": result["notes"],
    }


def summary_fingerprint(inp: dict, model_version: str) -> str:
    """
    摘要要不要重算，看這個指紋：重大事件（含版本 / 狀態）、警示、顯示標籤、模型與 prompt 版本。
    時間衰減造成的 NSI 小幅變動不在指紋裡，所以不會每次都重新呼叫 AI。
    """
    major = sorted((e["event_id"], e["status"]) for e in inp["events"] if e["importance"] >= 3)
    al = sorted((a["kind"], a["event_id"] or "") for a in inp["alerts"])
    key = repr((inp["ticker"], major, al, inp["label"], model_version, SUMMARY_PROMPT_VERSION))
    return sha1(key)


def validate_ai_output(out: dict, inp: dict) -> tuple:
    """
    驗證 AI 輸出：
      * 只能引用輸入裡存在的 event_id
      * 文字裡出現的數字必須是輸入有提供的數字（避免 AI 自己算分數或編造數字）
    回傳 (通過?, 問題清單)
    """
    problems = []
    valid_ids = {e["event_id"] for e in inp["events"]} | {a["event_id"] for a in inp["alerts"] if a["event_id"]}
    # 允許的數字 = 輸入資料裡出現過的所有數字（NSI、動能、A、Ω、事件分數、重要性、新聞標題裡的數字…）
    # 加上公式說明可能用到的常數。用數值比較，所以 0.5 和 0.50 視為同一個數字。
    import json as _json
    source_text = _json.dumps(inp, ensure_ascii=False, default=str)
    for eid in valid_ids:                       # event_id 是雜湊碼，裡面的數字不算「數字」
        source_text = source_text.replace(eid, " ")
    allowed = {float(x) for x in re.findall(r"\d+(?:\.\d+)?", source_text)}
    allowed |= {0.0, 1.0, 2.0, 3.0, 5.0, 15.0, 40.0, 100.0}

    def check_text(txt):
        txt = txt or ""
        for eid in valid_ids:                   # 文字裡引用 event_id 時，id 本身的數字不檢查
            txt = txt.replace(eid, " ")
        for num in re.findall(r"\d+(?:\.\d+)?", txt):
            if float(num) not in allowed:
                problems.append(f"出現輸入沒有提供的數字：{num}")

    for key in ("overall", "change"):
        check_text(out.get(key, ""))
    for key in ("positives", "negatives"):
        for p in out.get(key, []) or []:
            check_text(p.get("text", ""))
            for eid in p.get("event_ids", []) or []:
                if eid not in valid_ids:
                    problems.append(f"引用了不存在的事件：{eid}")
    for key in ("risks", "limitations"):
        for txt in out.get(key, []) or []:
            check_text(txt)
    return (not problems), problems
