#!/usr/bin/env python3
"""kd._core_impl —— kd.core 的私有实现体(工单 #26)。

本模块**不是**公开面的一部分:它是 kd.core 的内部实现载体。之所以单独成文件,
是因为模块级定义无法在"同一文件内"避免被注册进 kd.core 的命名空间——
前导下划线只是君子协定(`kd.core._rrf_fuse` 仍可属性访问)。把实现搬进本模块后,
kd.core 的命名空间里只剩 ask / search / read / 三个异常类。

约定:
  * 只有 kd/core.py 允许 import 本模块;
  * 本模块的 `__all__` 为空——它不是给外部 `import *` 用的;
  * 内部件可直接 `from kd._core_impl import _rrf_fuse` 用于测试观测,
    但这属于内部行为,不构成对外契约。

上游接口、纪律与设计说明见 kd/core.py 的 docstring。
"""

import hashlib
import json
import math
import os
import random
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

VIP = "https://vip.kingdee.com"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/152.0.0.0"
HDRS = {"User-Agent": UA, "Accept": "application/json"}

# 包内数据文件:拆解规则/预算/限速档(语料可配置;上游迁移时随包走)。
_ROUTE_CFG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "query_routes.json")
_ROUTE_CFG = None

# 上游 text 参数硬上限:100 原始字符(含标点/空格/换行,均计 1)。
# 超限返回 HTTP 200 + {"errorCode":409,...},body 无 totalElements ——
# 不识别就会把"查询超限"静默降级成"无匹配结果"(2026-09-16 实测)。
UPSTREAM_TEXT_MAX = 100

RRF_K = 60

# 管线默认值(环境变量;函数入参可逐次覆盖)
RERANK_DEFAULT = os.environ.get("KSEARCH_RERANK", "0").lower() in ("1", "true", "on")
# 注:原 INDEX_DEFAULT(KSEARCH_INDEX)已随本地落盘缓存体系取消而删除(ADR-0011 决策 3)——
# 它自 v6 起即无消费方(检索面早已由 corpus+rg 接管),去服务化后全仓无 sqlite3 导入。

# 内核不落盘(工单 #20 / ADR-0011 决策 3):落地缓存与包内日志写盘**整体摘除**。
# 摘除理由(两条各自独立成立):
#   1) ADR-0011 决策 3 已取消本地落盘缓存;去服务化后每次调用都是新进程,
#      "写穿即复用"的收益本就消失,保留只会留下无人读的写盘面。
#   2) ~/.kd/ 是姊妹项目 ly CLI 的凭据目录(../Lingya/src/ly/config.py 读
#      ~/.kd/config.json)。内核若往该目录写 core.log,两套件目录即耦合,
#      且往存凭据处写非凭据文件是明确要避免的形态。
# log() 保留为**空实现**而非删除:它是 core 内部通用观测点(约 10 处调用),
# 删函数会把"日志"这个关注点炸进每个调用点;改为写 stderr 才是正确落点。

_ONE_LINE = re.compile(r"\s+")


def log(*a):
    """观测日志 → stderr(不落盘、不写 ~/.kd/)。

    契约:stdout 只出 JSON,进度与日志走 stderr。失败静默——日志不能影响检索主链路。
    """
    try:
        sys.stderr.write("[kd] " + " ".join(str(x) for x in a) + "\n")
    except Exception:
        pass


# ---------- 错误类型(对外可见,便于调用方分类) ----------
class UpstreamError(Exception):
    """上游业务错误(HTTP 200 但 body 带 errorCode)。"""

    def __init__(self, code, message):
        self.code = code
        self.message = message
        super().__init__("upstream %s: %s" % (code, message))


class QueryTooLong(ValueError):
    """检索词超过上游 100 原始字符硬闸。压回上限内确实能跑通,但静默截断会把
    "查询被上游拒绝"伪装成"官方没这类文档"——故显式报错,由调用方决定是否
    用 clamped 字段里的压回值重试。"""

    def __init__(self, original, clamped, limit):
        self.original = original
        self.clamped = clamped
        self.limit = limit
        super().__init__("query too long: %d > %d upstream chars" % (len(original), limit))


class InternalError(Exception):
    """参数/内部使用错误(CLI 映射为用法错误退出码)。"""


# ---------- 限速配置与预算 ----------
def _route_cfg():
    """拆解规则/预算/限速配置(数据文件在包内,沉淀词表只改 query_routes.json)。"""
    global _ROUTE_CFG
    if _ROUTE_CFG is None:
        try:
            with open(_ROUTE_CFG_PATH, encoding="utf-8") as f:
                _ROUTE_CFG = json.load(f)
        except Exception as e:
            log("query_routes.json load fail:", str(e)[:120])
            _ROUTE_CFG = {}
    return _ROUTE_CFG


def _cfg_budget_max():
    v = os.environ.get("KSEARCH_ASK_BUDGET")
    if v and str(v).isdigit():
        return int(v)
    cfg = _route_cfg()
    b = (cfg.get("budget") or {}).get("maxUpstreamPerAsk")
    if b:
        return int(b)
    # 兜底:路数 + topK 深读 + 回答展开余量(仅在配置读不到时生效,有意保守)。
    routes = int(cfg.get("maxRoutes") or 7)
    topk = int((cfg.get("deepRead") or {}).get("topK") or 4)
    return routes + topk * 3


class _Budget:
    """单次 ask 的上游请求硬上限(默认 64,可配置)。只对真实上游调用计数——    领取点都在 _get_json 入口,本地缓存命中不经 _get_json,天然不计。

    并发纪律(工单 #29):本类**全部**状态读写都在 `self._lock` 下进行。
    `acquire()` 把"检查余量"与"占用名额"合并为一次原子操作,消除
    check-then-act 竞态——深读路径经 ThreadPoolExecutor(max_workers=4)
    并发进入,若不原子则 4 个线程可同时通过检查再各自自增,实际请求数
    溢出 (并发度-1) 次,`max` 就不再是硬上限。
    `used` / `exhausted` 是裸属性,外部(含返回结构组装)只在本类方法内读写;
    读取也要持锁,否则会读到撕裂或滞后的值。"""

    def __init__(self, max_upstream):
        self._lock = threading.Lock()
        # budget=0 是合法入参,语义为"零上游请求"(不是"未设限")。原写法
        # `int(x or 0) or None` 把 0 折成 None,等于把"禁网"读成"无限"——既是
        # 参数语义错,也直接击穿"budget 是硬上限"的对外承诺(见工单 #29)。
        # 只有 None / 空值才视为未设限。
        self.max = None if max_upstream is None else int(max_upstream)
        self.used = 0
        self.exhausted = False

    def acquire(self):
        """原子领取一个上游名额:有余量则占用并返回 True;耗尽则标记并抛 _BudgetExhausted。

        检查与自增在同一临界区内完成,不可分割——这是本工单的核心修复点。
        """
        with self._lock:
            if self.max is not None and self.used >= self.max:
                self.exhausted = True
                raise _BudgetExhausted()
            self.used += 1
            return True

    def remaining(self):
        """持锁读余量(max 为 None 时视为无限)。用于编排层的"还剩多少"判断。"""
        with self._lock:
            if self.max is None:
                return None
            return max(0, self.max - self.used)

    def mark_exhausted(self):
        """持锁标记耗尽(供编排层在读余量后补记,保证标记不丢)。"""
        with self._lock:
            self.exhausted = True

    def snapshot(self):
        """持锁取 (used, max, exhausted) 一致性快照。"""
        with self._lock:
            return self.used, self.max, self.exhausted


class _BudgetExhausted(Exception):
    """预算耗尽信号:停止发起上游请求,返回已获资料。"""


class _RateLimiter:
    """上游限速两档(匿名链路):interactive=交互会话短突发 2-3 请求/秒+随机抖动
    (默认档,/ask 用);background=后台/摄取任务 1 请求/秒。令牌桶实现,只对真实上游
    请求生效(_get_json 入口),本地缓存命中不计;触发节流写日志+stderr,限速可观测。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._next = 0.0
        self._profile = None  # 请求级切档;None=环境/配置默认

    def profile(self, name=None):
        cfg = _route_cfg().get("rate") or {}
        name = str(name or self._profile or os.environ.get("KSEARCH_RATE") or "interactive").lower()
        p = cfg.get(name)
        if not isinstance(p, dict):  # 配置缺失时的安全默认
            p = {"burst": 1, "rps": 1.0, "jitterMs": [0, 120]} if name == "background" \
                else {"burst": 3, "rps": 2.5, "jitterMs": [40, 220]}
        return name, p

    def set_profile(self, name):
        self._profile = name

    def wait(self, name=None):
        pname, p = self.profile(name)
        rps = float(p.get("rps") or 2.5)
        burst = max(1, int(p.get("burst") or 1))
        j = p.get("jitterMs") or [0, 0]
        interval = 1.0 / rps
        with self._lock:
            now = time.monotonic()
            t = max(self._next, now - burst * interval)  # 短突发:允许 burst 个请求立即通过
            delay = t - now
            self._next = t + interval
        if delay > 0:
            log("RATE[%s] 节流 %.2fs (burst=%d rps=%.1f)" % (pname, delay, burst, rps))
            try:
                sys.stderr.write("[rate] %s throttle %.2fs\n" % (pname, delay))
            except Exception:
                pass
        time.sleep(max(delay, 0.0) + random.uniform(float(j[0]), float(j[1])) / 1000.0)


_RATE = _RateLimiter()


def _rate_profile(name=None):
    """读/切当前限速档。后台/摄取任务开始时切 background。"""
    if name is not None:
        _RATE.set_profile(name)
    return _RATE.profile()[0]


# ---------- 上游调用计数(每次调用取前后差值,本地缓存命中不计) ----------
_UP_LOCK = threading.Lock()
_UP_N = 0


def _up_inc():
    global _UP_N
    with _UP_LOCK:
        _UP_N += 1


def _up_now():
    with _UP_LOCK:
        return _UP_N


# ---------- 检索词硬闸 ----------
def clamp_query(text, limit=None, strict=False):
    """把检索词压到上限内。默认 limit=UPSTREAM_TEXT_MAX(100 原始字符)。

    上游是硬闸而不是软截断:超限返回 HTTP 200 + errorCode:409 空壳。因此
    strict=True 时对超限输入显式 raise QueryTooLong(附 clamped 压回值),
    绝不静默截断——静默会把"查询超限"伪装成"官方没这类文档"。
    strict=False(内部路径)按上限压回,保证上游请求永远合法。
    """
    t = str(text or "")
    n = int(limit or UPSTREAM_TEXT_MAX)
    clamped = t[:n]
    if strict and len(t) > n:
        raise QueryTooLong(t, clamped, n)
    return clamped


# ---------- 文本清洗 ----------
def html2text(h):
    if not h:
        return ""
    h = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", h, flags=re.S | re.I)
    h = re.sub(r"<br\s*/?>", "\n", h, flags=re.I)
    h = re.sub(r"</(p|div|tr|h[1-6]|li|table)>", "\n", h, flags=re.I)
    h = re.sub(r"</t[dh]>", "\t", h, flags=re.I)
    h = re.sub(r"<[^>]+>", "", h)
    h = (h.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<")
          .replace("&gt;", ">").replace("&quot;", '"').replace("&#39;", "'"))
    lines = [re.sub(r"[ \t]+\n", "\n", re.sub(r"[ \t]{2,}", " ", ln)).strip() for ln in h.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(ln for ln in lines if ln))


def _is_true(v):
    return str(v or "").lower() == "true"


def _get_json(url, budget=None, rate=None):
    if budget is not None:
        # 工单 #29:原子领取名额(检查+占用一步完成),取代原 require()+spend() 两步。
        # 领取必须在 _RATE.wait() **之前**——限速等待期间持有名额,才能保证
        # "同时最多 max 个线程在飞",而不是"同时最多 max 个线程通过了检查"。
        budget.acquire()
    _RATE.wait(rate)      # 限速只卡真实上游请求;本地缓存命中不走这里,不计
    _up_inc()
    req = urllib.request.Request(url, headers=HDRS)
    with urllib.request.urlopen(req, timeout=20) as r:
        d = json.loads(r.read().decode("utf-8", "replace"))
    # 上游"假 200":HTTP 200 但 body 是错误壳(如 text 超 100 字符的 errorCode:409)。
    # 不识别会把"查询超限"静默降级成"无匹配结果"。
    if isinstance(d, dict) and d.get("errorCode"):
        raise UpstreamError(int(d["errorCode"]), str(d.get("message") or "")[:200])
    return d


# ---------- chunk 切片(标题感知,对齐金蝶文档【】结构) ----------
_HEADING_RE = re.compile(r"^(【[^】]{1,30}】|#{1,6}\s*\S.*|\d+[\.、．]\s*\S.{0,40})\s*$")


def _chunk_text(text, size=500, max_len=700):
    if not text:
        return []
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks, cur, head = [], [], ""
    for p in paras:
        lines = p.split("\n")
        if lines and _HEADING_RE.match(lines[0].strip()):
            head = lines[0].strip()[:40]
        cur.append(p)
        if sum(len(x) for x in cur) >= size:
            chunks.append({"heading": head, "text": "\n".join(cur)[:max_len]})
            cur = []
    if cur:
        chunks.append({"heading": head, "text": "\n".join(cur)[:max_len]})
    return [{"seq": i, **c} for i, c in enumerate(chunks, 1)]


def _top_chunks(chunks, terms, k=3):
    if len(chunks) <= k:
        return chunks

    def sc(c):
        return sum((1.5 if t in (c.get("heading") or "") else 0) +
                   (1.0 if t in (c.get("text") or "") else 0) for t in terms)

    pick = sorted(chunks, key=lambda c: -sc(c))[:k]
    return sorted(pick, key=lambda c: c["seq"])


# ---------- 查询词项 ----------
def _terms(text):
    return [t for t in re.split(r"[\s,，、;；/()（）]+", str(text or "")) if len(t) >= 2]


# ---------- 详情深读(不再落盘:工单 #20 摘除 landing 写穿) ----------
_URL_OF = {"knowledge": VIP + "/knowledge/%s", "answer": VIP + "/question/%s",
           "article": VIP + "/article/%s"}


# ---------- 信号重排(默认关:评测 recall@10 -11%,仅作 opt-in 实验) ----------
def _fresh_bonus(updated):
    try:
        y = int(str(updated)[:4])
        return 0.6 if y >= time.gmtime().tm_year - 1 else 0.3 if y >= time.gmtime().tm_year - 2 else 0.0
    except Exception:
        return 0.0


def _rerank_bonus(item, terms, fulltext):
    title = item.get("title") or ""
    snippet = item.get("snippet") or ""
    s = 0.0
    if fulltext and len(fulltext) >= 2 and fulltext in title:
        s += 2.0
    for t in terms:
        if t in title:
            s += 1.2
        if t in snippet:
            s += 0.4
    if item.get("type") == "answer":
        s += 2.0 if item.get("adopted") else 0.2
    elif item.get("type") == "knowledge":
        s += 1.0
    else:
        s += 0.3
    s += min(1.0, 0.4 * math.log10(1 + (item.get("useful") or item.get("supports") or 0)))
    s += min(0.6, 0.15 * math.log10(1 + (item.get("views") or 0)))
    if item.get("products"):
        s += 0.2
    return s + _fresh_bonus(item.get("updatedAt"))


def _rrf_fuse(lists):
    """多路结果 RRF 融合(k=60),answer 条目按 questionId 归并。"""
    scores, items = {}, {}
    for lst in lists:
        for rank, x in enumerate(lst, 1):
            if x.get("type") == "answer" and x.get("questionId"):
                k = "answer:" + str(x["questionId"])
            else:
                k = str(x.get("type", "?")) + ":" + str(x.get("id"))
            scores[k] = scores.get(k, 0.0) + 1.0 / (RRF_K + rank)
            items.setdefault(k, x)
    return scores, items


# ---------- 检索:三种实体全返回 ----------
def _norm_item(x, et):
    hl = x.get("highlight") or {}
    classes = [c.get("name") for c in (x.get("classifies") or []) if c.get("name")]
    if et == "knowledge":
        kid = str(x.get("knowledgeId") or x.get("id") or "")
        return {"type": "knowledge", "id": kid,
                "url": f"{VIP}/knowledge/{kid}" if kid else None,
                "title": html2text(hl.get("title") or x.get("title") or "") or None,
                "snippet": html2text(hl.get("content") or x.get("summary") or "")[:400] or None,
                "products": classes[:3],
                "views": x.get("views"), "useful": x.get("useful"),
                "contentLen": x.get("contentLen"), "updatedAt": x.get("updatedAt")}
    if et == "answer":
        q = x.get("question") or {}
        qid = str(x.get("questionId") or q.get("id") or "")
        return {"type": "answer", "id": str(x.get("id") or ""), "questionId": qid,
                "url": f"{VIP}/question/{qid}" if qid else None,
                "title": html2text(hl.get("question.title") or q.get("title") or "") or None,
                "questionBody": html2text(q.get("description") or "")[:500] or None,
                "snippet": html2text(hl.get("description") or x.get("summary") or "")[:400] or None,
                "adopted": _is_true(x.get("isAdopt")),
                "answersCount": q.get("answers"),
                "products": classes[:3] or ([q.get("moduleName")] if q.get("moduleName") else []),
                "views": x.get("views"), "comments": x.get("comments"),
                "contentLen": x.get("contentLen"), "updatedAt": x.get("updatedAt")}
    if et == "article":
        arid = str(x.get("id") or "")
        return {"type": "article", "id": arid,
                "url": f"{VIP}/article/{arid}" if arid else None,
                "title": html2text(hl.get("title") or x.get("title") or "") or None,
                "snippet": html2text(hl.get("content") or x.get("summary") or "")[:400] or None,
                "products": classes[:3],
                "views": x.get("views"), "supports": x.get("supports"),
                "contentLen": x.get("contentLen"), "updatedAt": x.get("updatedAt")}
    return None


def _search_upstream(text, product_id, page, page_size, global_, sorts_type, type_, budget=None,
                     rate=None):
    text = clamp_query(text, UPSTREAM_TEXT_MAX)  # 入口压回:上游 100 字硬闸
    params = {"text": text, "page": page, "pageSize": page_size,
              "global": "true" if global_ else "false", "sortsType": sorts_type}
    if product_id and int(product_id) != 0:
        # 0=不过滤:必须省略参数,传 productIds[0]=0 上游会当真值过滤(实测把 Knowledge 挤出前排)
        params["productIds[0]"] = int(product_id)
    return _get_json(VIP + "/api/search?" + urllib.parse.urlencode(params), budget, rate)


def _knowledge_search(text, product_id=None, page=1, page_size=10, global_=False, sorts_type=1,
                      type_=None, max_scan_pages=5, rerank=False, budget=None, rate=None):
    """rerank=False 时与 v3.2 行为逐字兼容(分页/扫描/scanNote 不变)。
    rerank=True(opt-in 实验):上游深扫描(page1 size≥25)→RRF+信号重排→按页切片。"""
    if not rerank:
        items, seen = [], set()

        def collect(dd):
            for x in dd.get("content") or []:
                et = (x.get("entity-type") or "").lower()
                if type_ and et != type_.lower():
                    continue
                key = (et, str(x.get("id") or ""))
                if key in seen:
                    continue
                seen.add(key)
                n = _norm_item(x, et)
                if n:
                    items.append(n)

        scan_note = None
        hits = 0
        if type_:
            d = _search_upstream(text, product_id, page, page_size, global_, sorts_type, type_, budget, rate)
            total, total_pages = d.get("totalElements"), d.get("totalPages")
            collect(d)
            pg = 2
            while len(items) < page * page_size and pg <= max_scan_pages and pg <= (total_pages or 1):
                dd = _search_upstream(text, product_id, pg, page_size, global_, sorts_type, type_, budget, rate)
                collect(dd)
                pg += 1
            items = items[(page - 1) * page_size: page * page_size]
            scan_note = f"type={type_} 过滤:跨上游 {pg - 1} 页扫描(混排结果按相关度抽取该类型)"
        else:
            d = _search_upstream(text, product_id, page, page_size, global_, sorts_type, type_, budget, rate)
            total, total_pages = d.get("totalElements"), d.get("totalPages")
            collect(d)
        return {"ok": True, "text": text, "total": total, "queries": [text],
                "page": page, "pageSize": page_size, "totalPages": total_pages,
                "results": items, "scanNote": scan_note, "_cacheHits": hits}

    # ---- v4 管线路径:深扫描 + RRF + 信号重排 ----
    queries = [text]
    up_size = max(page_size, 25)
    lists, total, total_pages = [], 0, 0
    for q in queries:
        d = _search_upstream(q, product_id, 1, up_size, global_, sorts_type, type_, budget, rate)
        total = max(total, d.get("totalElements") or 0)
        total_pages = max(total_pages, d.get("totalPages") or 0)
        lists.append(d.get("content") or [])
    scores, raw_items = _rrf_fuse(lists)
    terms = _terms(text)
    scored = []
    for k, x in raw_items.items():
        n = _norm_item(x, (x.get("entity-type") or "").lower())
        if not n:
            continue
        if type_ and n["type"] != type_.lower():
            continue
        scored.append((scores[k] + _rerank_bonus(n, terms, text), n))
    scored.sort(key=lambda t: -t[0])
    items = [n for _, n in scored][(page - 1) * page_size: page * page_size]
    return {"ok": True, "text": text, "total": total,
            "page": page, "pageSize": page_size, "totalPages": total_pages,
            "results": items, "_cacheHits": 0,
            "scanNote": "v4管线:上游深扫描%d条×%d路%s,RRF(k=%d)+信号重排" % (
                up_size, len(queries), "+同义词变体" if len(queries) > 1 else "", RRF_K),
            "queries": queries}


# ---------- 详情:知识 / 问答 / 文章 ----------
def _knowledge_article(kid, budget=None, rate=None):
    d = _get_json(VIP + "/knowledgeapi/knowledge/" + str(kid), budget, rate)
    return {"ok": True, "id": str(kid), "type": "knowledge", "title": d.get("title"),
            "contentText": html2text(d.get("content")),
            "url": f"{VIP}/knowledge/{kid}",
            "products": [p.get("name") for p in (d.get("products") or [])][:3],
            "updatedAt": d.get("updatedAt")}


def _answer_brief(a):
    disc = []
    for e in (a.get("appendQuestionsAndAnswers") or []):
        if isinstance(e, dict):
            t = html2text(e.get("content") or e.get("plainTextContent") or "")
            if t:
                disc.append({"creator": (e.get("creator") or {}).get("name")
                             if isinstance(e.get("creator"), dict) else None,
                             "contentText": t})
    return {"id": str(a.get("id") or ""),
            "contentText": html2text(a.get("description") or a.get("summary") or a.get("content") or ""),
            "adopted": _is_true(a.get("isAdopt")),
            "usefuls": a.get("usefuls"), "comments": a.get("comments"),
            "creator": (a.get("creator") or {}).get("name"),
            "createdAt": a.get("createdAt"),
            "discussion": disc or None}


def _q_products(d):
    mod = d.get("module") or d.get("domain") or {}
    pn = mod.get("pathName")
    return pn.split("/") if pn else []


def _question_detail(qid, with_answers=True, max_answer_pages=3, max_detail=5, budget=None, rate=None):
    d = _get_json(VIP + "/api/questions/" + str(qid), budget, rate)
    out = {"ok": True, "id": str(qid), "type": "answer", "title": d.get("title"),
           "contentText": html2text(d.get("description")),
           "url": f"{VIP}/question/{qid}",
           "isSolved": d.get("isSolved"), "answersCount": d.get("answers"),
           "views": d.get("views"), "rewardCoins": d.get("rewardCoins"),
           "products": _q_products(d),
           "createdAt": d.get("createdAt"), "updatedAt": d.get("updatedAt")}
    best = d.get("bestAnswer")
    if isinstance(best, list) and best:
        out["bestAnswer"] = _answer_brief(best[0])
    if with_answers:
        # 预算硬上限:回答展开(翻页+逐条详情)是深读里最贵的请求,超限即停,
        # 保留已获部分并标 truncated——截断结果不写穿落盘,避免幂等把残缺全文钉死。
        truncated = False
        answers, page = [], 1
        try:
            while page <= max_answer_pages:
                ad = _get_json(VIP + "/api/questions/%s/answers?page=%d&pageSize=20" % (qid, page),
                               budget, rate)
                for a in ad.get("content") or []:
                    answers.append(_answer_brief(a))
                if page >= (ad.get("totalPages") or 1):
                    break
                page += 1
            answers.sort(key=lambda a: (not a["adopted"]))
            for a in answers[:max(max_detail, 0)]:
                det = _answer_brief(_get_json(VIP + "/api/answers/" + a["id"], budget, rate))
                if len(det.get("contentText") or "") > len(a.get("contentText") or ""):
                    a["contentText"] = det["contentText"]
                if det.get("discussion"):
                    a["discussion"] = det["discussion"]
        except _BudgetExhausted:
            truncated = True
        except Exception:
            pass
        if truncated:
            out["truncated"] = True
        out["answers"] = answers
    return out


def _answer_detail(aid, budget=None, rate=None):
    d = _get_json(VIP + "/api/answers/" + str(aid), budget, rate)
    q = d.get("question") or {}
    qid = str(d.get("questionId") or q.get("id") or "")
    return {"ok": True, "id": str(aid), "questionId": qid,
            "title": q.get("title"), "contentText": html2text(d.get("description")),
            "adopted": _is_true(d.get("isAdopt")), "usefuls": d.get("usefuls"),
            "url": f"{VIP}/question/{qid}" if qid else None,
            "updatedAt": d.get("updatedAt")}


def _article_detail(aid, budget=None, rate=None):
    d = _get_json(VIP + "/api/articles/" + str(aid), budget, rate)
    classes = [c.get("name") for c in (d.get("classifies") or []) if c.get("name")]
    return {"ok": True, "id": str(aid), "type": "article", "title": d.get("title"),
            "contentText": html2text(d.get("content")),
            "url": f"{VIP}/article/{aid}",
            "products": classes[:3], "supports": d.get("supports"), "views": d.get("views"),
            "updatedAt": d.get("updatedAt")}


_DETAIL_FN = {"knowledge": _knowledge_article, "answer": _question_detail,
              "article": _article_detail, "answer_detail": _answer_detail}
_DETAIL_KINDS = tuple(_DETAIL_FN)


def _detail(kind, oid, refresh=False, budget=None, rate=None):
    """详情统一入口(纯在线:不再写穿落地缓存)。

    摘除写穿后本函数的语义只剩"取详情 + URL 补齐",保留它是因为 4 个 kind 的
    分发点只应有一处;调用方(_fetch_for_item / read)无需知道分发表存在。
    预算:require/spend 在 _get_json。
    """
    d = _DETAIL_FN[kind](oid, budget=budget, rate=rate)
    if not d.get("url") and d.get("id"):
        t = str(d.get("type") or kind).lower()
        if t in _URL_OF:
            d["url"] = _URL_OF[t] % d["id"]
    return d


# ---------- 多路关键词编排 ----------
def _fetch_for_item(item, refresh, budget=None, rate=None):
    try:
        if item["type"] == "knowledge":
            d = _detail("knowledge", item["id"], refresh, budget=budget, rate=rate)
        elif item["type"] == "answer":
            d = _detail("answer", item.get("questionId") or item["id"], refresh, budget=budget, rate=rate)
        elif item["type"] == "article":
            d = _detail("article", item["id"], refresh, budget=budget, rate=rate)
        else:
            d = None
    except _BudgetExhausted:
        d = {"ok": False, "error": "budget_exhausted"}
    except Exception as e:
        d = {"ok": False, "error": str(e)[:150]}
    return d


def _salient_chunks(text, stopwords):
    """CJK 连续段去掉停用词/功能词后切成候选症状词(≥2 字);拉丁/数字 token 由调用方单独提取。"""
    sw = [re.escape(s) for s in (stopwords or []) if s]
    out = []
    for seg in re.findall(r"[\u4e00-\u9fff]+", str(text or "")):
        parts = re.split("(?:%s)" % "|".join(sw), seg) if sw else [seg]
        out += [p for p in parts if len(p) >= 2]
    return out


def _plan_routes(text=None, keywords=None, product_id=None):
    """一句自然语言问题 → ≤maxRoutes 路关键词(原句路+症状词路+字段/实体名词路+产品词路)。
    拆解规则全部在包内 query_routes.json。返回 (routes, product_id)。"""
    cfg = _route_cfg()
    max_routes = int(cfg.get("maxRoutes") or 7)
    raw_cfg = cfg.get("rawRoute") or {}
    raw_on = raw_cfg.get("enabled", True)
    raw_sorts = int(raw_cfg.get("sortsType", 1))
    raw_max = int(raw_cfg.get("maxChars") or UPSTREAM_TEXT_MAX)
    if keywords:  # 调用方显式关键词:每词一路(保持 v5 兼容语义)
        routes, seen = [], set()
        for k in keywords:
            k = str(k).strip()
            if k and k not in seen:
                seen.add(k)
                routes.append({"kind": "explicit", "terms": k, "why": "调用方显式关键词"})
        routes = routes[:max_routes]
        # 显式关键词路与主路径同权:产品过滤必须统一携带,否则 --kw 绕过 --product 会串线
        if product_id and int(product_id) != 0:
            for r in routes:
                r["productIds"] = int(product_id)
        return routes, product_id
    text = str(text or "")
    aliases = ((cfg.get("productAliases") or {}).get("alias") or {})
    if not product_id:
        for name, pid in aliases.items():
            if str(name) in text:
                product_id = pid
                break
    latin = []  # 扫描提取(中英混写无空格:"MRP运算""分母显示27000"里的 MRP/27000 也要拿到)
    for t in re.findall(r"[A-Za-z0-9][A-Za-z0-9.%/_:-]*", text):
        if t not in latin:
            latin.append(t)
    cjk = _salient_chunks(text, cfg.get("stopwords"))
    routes, used = [], set()

    # 0) 原句路(ADR-0009):先占席位,固定 sortsType=1,不受截断挤压
    raw_route = None
    if raw_on and text.strip():
        raw_route = {"kind": "raw:question", "terms": clamp_query(text.strip(), raw_max),
                     "why": "原句路(相关性排序;片段路的词汇鸿沟无法覆盖时由此救回)",
                     "sortsType": raw_sorts}

    def take(term):
        used.add(term)
        return term

    # 1) 症状词路:按类别归拢(命中模式词/包含模式词的原文片段)
    numeric = [t for t in latin if re.fullmatch(r"\d+", t)]
    cats = cfg.get("symptomCategories")
    cats = cats.get("categories", []) if isinstance(cats, dict) else (cats or [])
    for i, cat in enumerate(cats):
        terms = []
        for p in (cat.get("patterns") or []):
            p = str(p)
            if p.lower() in [t.lower() for t in latin]:
                terms.append(take(next(t for t in latin if t.lower() == p.lower())))
            else:
                hit = next((c for c in cjk if p in c and c not in used), None)
                if hit:
                    terms.append(take(hit))
        if i == 0 and terms:
            terms += [t for t in numeric if t not in used][:2]  # 纯数字量词归第一症状路
        if terms:
            routes.append({"kind": "symptom:" + str(cat.get("name") or "symptom"),
                           "terms": " ".join(terms[:4]), "why": cat.get("why")})
    leftover_cjk = [c for c in cjk if c not in used]
    leftover_lat = [t for t in latin if t not in used and not re.fullmatch(r"\d+", t)]
    # 2) 字段/实体名词路:症状反推领域术语(词汇鸿沟的桥)
    ent = cfg.get("entityRules")
    ent = ent.get("rules", []) if isinstance(ent, dict) else (ent or [])
    for rule in ent:
        when = rule.get("whenAny") or []
        if when and not any(str(w) in text for w in when):
            continue
        rgx = rule.get("whenRegex")
        if rgx:
            try:
                if not re.search(rgx, text):
                    continue
            except re.error:
                pass
        for r in (rule.get("routes") or []):
            terms = r.get("terms") if isinstance(r, dict) else r
            if terms:
                routes.append({"kind": "entity:" + str(rule.get("name") or "?"), "terms": str(terms),
                               "why": (r.get("why") if isinstance(r, dict) else None) or rule.get("why")})
    # 3) 产品/上下文词路:剩余 token(产品名/BOM 等)+productIds 过滤
    if leftover_lat or leftover_cjk:
        routes.append({"kind": "product", "terms": " ".join((leftover_lat + leftover_cjk)[:3]),
                       "why": "产品/上下文词路(携带 productIds 过滤)"})
    # 兜底:拆解一路未出(原句路关闭或文本为空)→ 原句一路
    if not routes and not raw_route and text.strip():
        routes.append({"kind": "raw", "terms": clamp_query(text.strip(), raw_max),
                       "why": "拆解无命中,原句检索"})
    if raw_route:
        routes.insert(0, raw_route)
    if product_id and int(product_id) != 0:
        for r in routes:
            r["productIds"] = int(product_id)
    # 上限截断:产品词路与原句路保席位(两者携带的信息维度不可被片段路挤掉)
    if len(routes) > max_routes:
        pinned_kinds = ("product", "raw:question", "raw")
        pinned = [r for r in routes if r["kind"] in pinned_kinds]
        rest = [r for r in routes if r["kind"] not in pinned_kinds]
        routes = rest[:max(0, max_routes - len(pinned))] + pinned
        routes.sort(key=lambda r: (0 if r["kind"] == "raw:question" else
                                   2 if r["kind"] == "product" else 1))
    return routes[:max_routes], product_id


def _fused_key(x):
    return "answer:" + str(x.get("questionId")) if x.get("type") == "answer" and x.get("questionId") \
        else str(x.get("type", "?")) + ":" + str(x.get("id"))


def _select_top(ranked, top_k):
    """融合排名选 topK 深读;并保证资料包两种来源覆盖(knowledge 补根因/answer 对齐症状):
    若选出的全是单一类型且候选池另一类型存在,用融合排名最低的席位换另一类型最佳候选。"""
    sel, seen = [], set()
    for item in ranked:
        k = item.get("questionId") if item["type"] == "answer" else item.get("id")
        if not k or k in seen:
            continue
        seen.add(k)
        sel.append(item)
        if len(sel) >= top_k:
            break
    if top_k >= 2:
        have = {it["type"] for it in sel}
        for want in ("knowledge", "answer"):
            if want in have or not sel:
                continue
            cand = next((it for it in ranked if it["type"] == want and
                         (it.get("questionId") if want == "answer" else it.get("id")) not in seen), None)
            if cand is None:
                continue
            seen.add(cand.get("questionId") if want == "answer" else cand.get("id"))
            sel[-1] = cand
            have.add(want)
    return sel


def _ask_bundle(text=None, keywords=None, product_id=None, top_k=None,
                rerank=None, refresh=False, budget=None, rate=None):
    cfg = _route_cfg()
    top_k = max(1, min(int(top_k or (cfg.get("deepRead") or {}).get("topK") or 4), 8))
    routes, product_id = _plan_routes(text=text, keywords=keywords, product_id=product_id)
    page_size = int(cfg.get("searchPageSize") or 25)
    rerank = RERANK_DEFAULT if rerank is None else bool(rerank)
    # 多路检索:每路一次上游搜索(混排 answer+knowledge,产品过滤统一携带),路间 RRF 融合
    lists, total = [], 0
    for r in routes:
        # 工单 #29:经 remaining() 持锁读余量,取代原 `budget.used >= budget.max` 裸读。
        # 裸读与并发深读的自增竞争,会读到滞后值;此处只做"提前止损"的优化判断,
        # 真正的硬闸在 _get_json 的 acquire() 里——两者共用同一把锁,语义一致。
        if budget is not None and budget.remaining() == 0:
            budget.mark_exhausted()
            break
        try:
            res = _knowledge_search(r["terms"], product_id=r.get("productIds"), page=1,
                                    page_size=page_size, rerank=rerank, budget=budget, rate=rate,
                                    sorts_type=int(r.get("sortsType") or 1))
        except _BudgetExhausted:
            break
        except UpstreamError as e:
            # 上游业务错误(HTTP 200 但 body 带 errorCode,如 text 超 100 字符的 409)。
            # 不能与"该路无结果"混为一谈——显式记日志,否则调用方会把"查询被上游拒绝"读成"官方没这类文档"。
            log("UPSTREAM_ERR:", "route=%s code=%s msg=%s" % (r.get("kind"), e.code, e.message))
            continue
        except Exception as e:
            log("ROUTE_ERR:", "route=%s terms=%s %s: %s"
                % (r.get("kind"), str(r.get("terms"))[:40], type(e).__name__, str(e)[:120]))
            continue
        total = max(total, res.get("total") or 0)
        lists.append(res["results"])
    scores, items = _rrf_fuse(lists)
    ranked = sorted(items.values(), key=lambda x: -scores[_fused_key(x)])
    sel = _select_top(ranked, top_k)
    # 深读顺序:knowledge(根因,每源 1 请求)优先消耗预算,answer(展开可能多请求)殿后;
    # 展示排序另行按 displayOrder(answer 优先)重排。预算只卡真实上游。
    fetch_order = sorted(sel, key=lambda x: ({"knowledge": 0, "article": 1}.get(x["type"], 2),
                                             -scores[_fused_key(x)]))
    results_map = {}
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = [(ex.submit(_fetch_for_item, it, refresh, budget, rate), it) for it in fetch_order]
        for f, it in futs:
            results_map[id(it)] = f.result()
    terms = _terms(" ".join(r["terms"] for r in routes))
    order = {t: i for i, t in enumerate(cfg.get("displayOrder") or ["answer", "knowledge", "article"])}
    sources = []
    for item in sorted(sel, key=lambda x: (order.get(x["type"], 9), -scores[_fused_key(x)])):
        d = results_map.get(id(item))
        if d is None:
            continue
        if isinstance(d, dict) and d.get("ok"):
            if item["type"] == "answer":  # 问答帖正文常空,合成读的是回答 → chunk 素材对齐
                atext = "\n\n".join(p for p in
                                    [d.get("contentText") or ""] +
                                    [("[采纳] " if a.get("adopted") else "") + (a.get("contentText") or "")
                                     for a in (d.get("answers") or [])[:3]] if p)
            else:
                atext = d.get("contentText") or ""
            ch = _top_chunks(_chunk_text(atext), terms)
            if ch:
                d["chunks"] = ch
        sources.append({"rank": len(sources) + 1, "type": item["type"], "id": item.get("id"),
                        "questionId": item.get("questionId"),
                        "title": item.get("title"), "url": item.get("url"),
                        "snippet": item.get("snippet"),
                        "fromCache": d.get("fromCache") if d else None,
                        "fusedScore": round(scores[_fused_key(item)], 5),
                        "products": d.get("products") or item.get("products") or [],
                        "detail": d})
    # 工单 #29:经 snapshot() 持锁取一致性快照,避免读到撕裂/滞后的 used。
    _used, _max, _exhausted = budget.snapshot() if budget else (None, None, False)
    return {"ok": True, "text": text or " / ".join(r["terms"] for r in routes) or None,
            "total": total,
            "effectiveProductId": product_id,
            "routes": routes,
            "queries": [r["terms"] for r in routes],
            "sources": sources,
            "budget": {"max": _max, "used": _used, "upstreamCalls": _used},
            "budget_exhausted": bool(_exhausted),
            "_cacheHits": sum(1 for s in sources if s.get("fromCache")),
            "note": "v6.2 原句路+多路关键词编排(规则在 kd/query_routes.json,语料可配置);"
                    "sources 展示排序 answer 优先(症状对齐)、knowledge 紧随(根因);"
                    "sources[].detail 已含全文并写穿落地缓存;knowledge/article 附 chunks"
                    "(标题感知切片,top3 相关段);budget=上游请求硬上限,超限即停并标 budget_exhausted;"
                    "effectiveProductId=本次实际生效的产品过滤"}


def _synthesis_brief(pack):
    """把客观召回信号显式抬到顶层,供调用方判定召回置信度(ADR-0010)。
    只做搬运与去冗,不产生任何新判断。"""
    src = pack.get("sources") or []
    b = pack.get("budget") or {}
    routes = pack.get("routes") or []
    kinds = []
    for r in routes:
        k = str(r.get("kind") or "")
        if k and k not in kinds:
            kinds.append(k)
    scores = [s.get("fusedScore") for s in src if isinstance(s.get("fusedScore"), (int, float))]
    return {
        "sourceCount": len(src),
        "topScores": [round(x, 5) for x in sorted(scores, reverse=True)[:5]],
        "routeKinds": kinds,
        "routeCount": len(routes),
        "budgetExhausted": bool(pack.get("budget_exhausted")),
        "upstreamUsed": b.get("used"),
        "upstreamMax": b.get("max"),
        "recallHint": ("检索未命中任何文档" if not src else
                       "上游预算耗尽,本轮召回不完整" if pack.get("budget_exhausted") else
                       "召回正常"),
    }


# ---------- 分享对话(匿名) ----------
def _share_read(link_or_id, rate=None):
    s = str(link_or_id).strip()
    chat_id = None
    m = re.search(r"/searchchats/(\d+)", s)
    if m:
        chat_id = m.group(1)
    elif s.isdigit():
        chat_id = s
    elif "/link/s/" in s:
        url = s if s.startswith("http") else VIP + s
        for _ in range(5):  # 沿重定向找 /searchchats/{chatId}
            class _NR(urllib.request.HTTPRedirectHandler):
                def redirect_request(self, *a, **k):
                    return None

            req = urllib.request.Request(url, headers={"User-Agent": UA})
            try:
                urllib.request.build_opener(_NR).open(req, timeout=20)
                break  # 无重定向了
            except urllib.error.HTTPError as e:
                loc = e.headers.get("Location") or ""
                if not loc:
                    break
                if loc.startswith("/"):
                    loc = VIP + loc
                m2 = re.search(r"/searchchats/(\d+)", loc)
                if m2:
                    chat_id = m2.group(1)
                    break
                url = loc
            except Exception:
                break
    if not chat_id:
        raise InternalError("cannot resolve chatId(传分享短链、/searchchats/{id} 页面链接或纯数字 chatId)")
    d = _get_json(VIP + "/aisapi/ai-search/sharing-chats/" + chat_id, None, rate)
    chats = []
    for c in d.get("chats") or []:
        refs = [{"title": x.get("title"), "url": x.get("url"),
                 "summary": (x.get("summary") or "")[:200],
                 "entityType": x.get("entityType"), "entityId": x.get("entityId")}
                for x in (c.get("recallDocuments") or [])]
        chats.append({"question": c.get("searchText"), "answer": c.get("content"),
                      "answerType": c.get("answerType"), "refs": refs})
    return {"ok": True, "chatId": chat_id, "count": len(chats), "chats": chats}


# ================= 公开面:ask / search / read =================
def search(text, product_id=None, page=1, page_size=10, global_=False, sorts_type=1,
           type_=None, rerank=None, budget=None, rate=None):
    """检索原语:三种实体全返回(知识/问答/文章),type 字段区分。

    text       检索词(str)。超过上游 100 原始字符 → raise QueryTooLong(不静默截断)。
    product_id 93=星空旗舰版 / 87=苍穹 / 1=企业版标准版;None 或 0 = 不过滤(省略参数)。
    page/page_size/sorts_type/global_ 直通上游。
    type_      可选过滤 knowledge|answer|article(过滤时跨页扫描补齐该类型)。
    rerank     信号重排(opt-in 实验,默认取 KSEARCH_RERANK,默认关)。
    budget     Budget 实例(可复用计数);rate 限速档名。
    返回:上游条目 + stats{upstreamCalls,elapsedMs}。非法入参 raise InternalError。
    """
    if not str(text or "").strip():
        raise InternalError("text required: 传具体功能名/业务名词/报错词")
    if type_ and str(type_).lower() not in ("knowledge", "answer", "article"):
        raise InternalError("bad type: %s(knowledge|answer|article)" % type_)
    clamp_query(str(text), UPSTREAM_TEXT_MAX, strict=True)
    n0, t0 = _up_now(), time.time()
    res = _knowledge_search(text, product_id=product_id, page=int(page), page_size=int(page_size),
                            global_=bool(global_), sorts_type=int(sorts_type), type_=type_,
                            rerank=RERANK_DEFAULT if rerank is None else bool(rerank),
                            budget=budget, rate=rate)
    res["stats"] = {"upstreamCalls": _up_now() - n0,
                    "cacheHits": res.get("_cacheHits", 0),
                    "elapsedMs": round((time.time() - t0) * 1000, 1)}
    log("SEARCH:", str(text)[:50], "| total", res.get("total"), "| returned", len(res.get("results") or []))
    return res


def read(kind, oid, refresh=False, budget=None, rate=None):
    """按类型读全文:kind ∈ knowledge | answer(问答帖全文,传 questionId) | article。

    深读结果直接返回,不再写穿落地缓存(工单 #20:内核不落盘)。
    返回:上游全文包 + stats{upstreamCalls,elapsedMs}。kind/id 非法 raise InternalError。
    """
    kind = str(kind or "knowledge").lower()
    if kind not in _DETAIL_KINDS:
        raise InternalError("bad kind: %s(%s)" % (kind, "|".join(_DETAIL_KINDS)))
    if oid is None or str(oid).strip() == "":
        raise InternalError("id required: 传 search 结果条目的 id")
    n0, t0 = _up_now(), time.time()
    d = _detail(kind, oid, refresh=bool(refresh), budget=budget, rate=rate)
    d["stats"] = {"upstreamCalls": _up_now() - n0, "elapsedMs": round((time.time() - t0) * 1000, 1)}
    log("READ[%s]:" % kind, oid, "| len", len(d.get("contentText") or ""))
    return d


def ask(text=None, keywords=None, product_id=None, top_k=None, budget=None,
        rerank=None, refresh=False, rate=None):
    """一站式资料包:多路关键词拆解 → RRF 融合 → 深读 topK 全文 → 附相关 chunk。

    text        自然语言问题(与 keywords 二选一)。超 100 原始字符 raise QueryTooLong
                (上游是硬闸;压回值在异常的 clamped 字段里,是否重试由调用方决定)。
    keywords    显式关键词列表(跳过自动拆解,每词一路)。
    product_id  产品过滤;未指定时按问句里的产品词自动推导(所有路统一携带)。
    top_k       深读条数 1-8(默认取 query_routes.json 的 deepRead.topK=4)。
    budget      上游请求硬上限(int);默认取 KSEARCH_ASK_BUDGET / query_routes.json(64)。
    rerank      opt-in 信号重排;默认取 KSEARCH_RERANK(默认关)。
    refresh     强制回源(当前实现恒在线,保留参数语义)。
    rate        限速档 interactive(默认)|background(1 req/s)。

    返回资料包(与走 HTTP 时同构):
      routes[] 拆解明细(kind/terms/why/productIds/sortsType)、
      sources[]{rank,type,id,questionId,title,url,snippet,fromCache,fusedScore,products,
                detail{…,chunks[]}},
      budget{max,used,upstreamCalls}、budget_exhausted、effectiveProductId、
      synthesisBrief(召回信号:来源数/融合高分段/命中路数/预算状态/召回提示)、
      stats{upstreamCalls,elapsedMs}。
    超长查询报 QueryTooLong 前只发生一次路由拆解、零上游请求。
    """
    if not (str(text or "").strip() or keywords):
        raise InternalError('text or keywords required(例: ask("信用额度控制"))')
    if text:
        clamp_query(str(text), UPSTREAM_TEXT_MAX, strict=True)
    for kw in (keywords or []):
        clamp_query(str(kw), UPSTREAM_TEXT_MAX, strict=True)
    bv = budget if budget is not None else _cfg_budget_max()
    if not isinstance(bv, _Budget):
        if not (isinstance(bv, int) or (isinstance(bv, str) and str(bv).isdigit())):
            raise InternalError("bad budget: %r(应为正整数或 Budget 实例)" % (budget,))
        bv = _Budget(bv)
    rate_used = _rate_profile(str(rate) if rate else None)
    n0, t0 = _up_now(), time.time()
    res = _ask_bundle(text=text or None, keywords=keywords, product_id=product_id,
                      top_k=top_k if top_k is not None else (bv.max and None),
                      rerank=RERANK_DEFAULT if rerank is None else bool(rerank),
                      refresh=bool(refresh), budget=bv, rate=rate_used)
    res["stats"] = {"upstreamCalls": _up_now() - n0,
                    "elapsedMs": round((time.time() - t0) * 1000, 1),
                    "rateProfile": rate_used}
    res["synthesisBrief"] = _synthesis_brief(res)
    log("ASK:", str(text or "kw×%d" % len(keywords or []))[:50],
        "| routes", len(res.get("routes") or []), "| sources", len(res["sources"]),
        "| upstream", res["budget"]["used"], "/", res["budget"]["max"],
        "| rate", rate_used, "| exhausted", res["budget_exhausted"])
    return res


# 未收编的旧服务能力(share 端点):下一张票决定其去留,本张不对外暴露。
_read_share = _share_read
