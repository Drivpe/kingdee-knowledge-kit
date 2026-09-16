#!/usr/bin/env python3
"""金蝶知识检索服务 v6.2 —— 匿名检索 + /ask 多路关键词编排(ADR-0005 彻底在线)+ 落地缓存写穿
逆向自金蝶云社区官方后端(2026-09-05,详见交接文档 11/12/13/14/16),零账号/零点数/零凭据/零外部依赖。
匿名接口铁证(均实测 HTTP 200,无 cookie):
  检索    GET https://vip.kingdee.com/api/search?text=&page=&pageSize=&global=&sortsType=&productIds[0]=
          → content[] 混合三种实体:Knowledge / Answer(问答) / Article
  知识全文 GET https://vip.kingdee.com/knowledgeapi/knowledge/{knowledgeId}
  问题详情 GET https://vip.kingdee.com/api/questions/{questionId}
  全部回答 GET https://vip.kingdee.com/api/questions/{questionId}/answers?page=&pageSize=
  回答全文 GET https://vip.kingdee.com/api/answers/{answerId}
  文章全文 GET https://vip.kingdee.com/api/articles/{articleId}
  分享对话 GET https://vip.kingdee.com/aisapi/ai-search/sharing-chats/{chatId}
★ 官方 ai-search 管线(语义RAG)需登录+身份认证,匿名实测未授权,不用(文档 13/14)。
★ v5.0 = corpus 语料目录(~/.lingeebuild/corpus,一文档一 md + front-matter,id/type/url/title/updatedAt/
  discovered_by):read/ask 深读同步写穿全文,发现层(全量快照/时间网格/share 引用)写 stub,agent 用 rg
  直接检索(grep verdict,交接文档 17 / ADR-0004)。机器缓存(sqlite)缩编为纯上游缓存:FTS5/chunks/
  local=1 冻结开发(deprecate),检索角色由 corpus+rg 接管;向量 BLOB 列冻结待墙。
★ v6 landing(issue #10)= 落地缓存(~/.lingeebuild/landing):深读全文独立写穿,常开、与缓存开关解耦、
  不阻塞回答;updatedAt 幂等(没变不重写,变了覆盖);discovered_by=query;rg 做字段名/报错原文精查。
  md+front-matter 落盘/幂等唯一实现在 docstore.py(发版说明库 #15 复用同一套)。
  图游走(recommendArray)v5.1 剔除:匿名不可达(ADR-0004 增补)。
★ v6.2 原句路 + 多路编排(ADR-0009,/ask 服务端硬行为):一句自然语言问题自动拆解为
  原句路+≤7 路关键词(原句路/症状词路/字段、实体名词路/产品词路,拆解规则在 query_routes.json 语料可配置)分别检索,
  RRF 融合 → topK 深读写穿落地缓存;预算硬上限单次 ask ≤32 上游请求(超限即停,budget_exhausted 可见);
  限速两档(CONTEXT v6):交互短突发 2-3 请求/秒+抖动(/ask 默认档)/后台摄取 1 请求/秒,只卡真实上游请求,
  本地缓存命中不计。资料包展示排序:answer 优先(症状对齐),knowledge 紧随(根因)。
★ v4.0 管线 = 查询侧×排序侧×存储侧;评测结论:信号重排默认关(recall@10 -11%),同义词已移除。

检索管线(参数可按请求覆盖,默认值来自环境变量):
  rerank    信号重排:标题/摘要命中×采纳×实体类型×有用/浏览×新鲜度 加权,上游深扫描(pageSize≥25)
            默认开(KSEARCH_RERANK=0 关)
  cache     本地缓存语料库(sqlite+FTS5 trigram,永久存储,search 7天/详情永久,refresh=1 强制回源)
            默认关(KSEARCH_INDEX=on 开);开启后明细自动切 chunk 入库,支持 local=1 纯本地检索
  其余参数:refresh=1 强制回源;local=1 只查本地语料【v5 deprecated:冻结开发,检索角色由 corpus+rg 接管】;
  所有响应带 stats{upstreamCalls,cacheHits,elapsedMs}

端点(POST/GET 双形态):
  /          或 /manifest → 机器可读能力清单(端点/参数/实体/CLI 路径/版本),agent 自发现入口
  /corpus    POST {"items":[{type,id,questionId?,title,snippet,url,updatedAt,contentText?}],"discoveredBy"}
             → 语料摄入(全文或 stub 落盘;时间网格/发现层脚本统一走这里,零上游调用)
  /search    {"text","productId":93,"page":1,"pageSize":10,"global":false,"sortsType":1,"type":"answer|knowledge|article",
              "pipeline":{"rerank","cache"},"refresh","local"}
             → 三种实体全返回,type 字段区分;Answer 条目内联 questionId/questionTitle/questionBody/adopted
  /karticle  {"id":"<knowledgeId>"}          → 知识库全文
  /question  {"id":"<questionId>"}           → 问题详情 + 全部回答(含正文/采纳/作者/追问链)
  /answer    {"id":"<answerId>"}             → 单条回答全文
  /article   {"id":"<articleId>"}            → 社区文章全文
  /ask       {"text"} 或 {"keywords":[..]} + {"productId"?,"topK"=4,"budget"?,"rate"?,"pipeline"?}
             → 一站式问答包:原句路+多路关键词拆解(≤7 路,规则在
               service/query_routes.json)→ RRF 融合 → 深读 topK 全文(写穿落地缓存)→附 top 相关 chunk;
               预算硬上限 ≤32 上游请求(超限即停,budget_exhausted);展示排序 answer 优先/knowledge 紧随,
               调用方 AI 拿包即合成带引用回答(官方问答效果的无登录等价)
  /share     {"link"} → 官方 AI 分享对话全文(评测集素材)
  /health
"""
import json, math, os, random, re, sqlite3, sys, threading, time, urllib.request, urllib.parse, hashlib
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import docstore  # md+front-matter 落盘/幂等唯一实现(本服务与发版说明库 #15 共用,issue #10 prefactor)
import semantic_rerank  # 票 #21:查询内语义重排打分器(本地 bge-small ONNX,默认 OFF,失败静默降级)

VIP = "https://vip.kingdee.com"
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 4097
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(_ROOT, "logs", "ksearch-service.log")
DB_PATH = os.environ.get("KSEARCH_DB", os.path.join(_ROOT, "data", "ksearch.db"))
CORPUS_DIR = os.environ.get("KSEARCH_CORPUS", os.path.join(_ROOT, "corpus"))
# 落地缓存(v6 landing,issue #10):与 corpus 互相独立的目录(corpus 删除是票 #13 的事)。
# 默认 ~/.lingeebuild/landing,KSEARCH_LANDING 可覆盖;深读写穿常开,与 KSEARCH_INDEX 无关。
LANDING_DIR = os.environ.get("KSEARCH_LANDING",
                             os.path.join(os.path.expanduser("~"), ".lingeebuild", "landing"))
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/152.0.0.0"
HDRS = {"User-Agent": UA, "Accept": "application/json"}
# 管线默认值(环境变量;单请求可用 rerank/cache/refresh/local 参数覆盖)
# 评测结论(data/eval,交接文档 16):信号重排默认关——手工信号权重会压过上游相关度排序
# (recall@10 -11%),仅作 opt-in 实验保留;同义词变体已按评测移除(无召回增益,时延×2)。
RERANK_DEFAULT = os.environ.get("KSEARCH_RERANK", "0").lower() in ("1", "true", "on")
INDEX_DEFAULT = os.environ.get("KSEARCH_INDEX", "0").lower() in ("1", "true", "on")
SEARCH_TTL = 7 * 86400  # 搜索缓存 7 天;明细缓存永久(知识文档基本不可变,refresh=1 强制回源)
RRF_K = 60

# ---------- 多路编排配置(数据文件:拆解规则/预算/限速档) ----------
_ROUTE_CFG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "query_routes.json")
_ROUTE_CFG = None

def route_cfg():
    """拆解规则/预算/限速配置(语料可配置:沉淀词表只改 query_routes.json,不改代码)。"""
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
    cfg = route_cfg()
    b = (cfg.get("budget") or {}).get("maxUpstreamPerAsk")
    if b:
        return int(b)
    # 兜底:路数 + topK 深读 + 回答展开余量(实测 answer 详情翻页可吃 3-5 次/源)
    # 仅在 query_routes.json 缺 budget.maxUpstreamPerAsk 时生效,故不追求与配置值相等——
    # 配置是真源,本公式只是"配置读不到时的最小可用粗估"(7+12=19,有意保守)。
    routes = int(cfg.get("maxRoutes") or 7)
    topk = int((cfg.get("deepRead") or {}).get("topK") or 4)
    return routes + topk * 3

# 上游对 text 参数的硬上限:100 原始字符(含标点/空格/换行,均计 1)。
# 超限返回 HTTP 200 + {"errorCode":409,"message":"搜索内容的长度不能超过100个字符"},
# body 无 totalElements —— 不识别就会把"查询超限"静默降级成"无匹配结果"(2026-09-16 实测)。
UPSTREAM_TEXT_MAX = 100

def clamp_query(text, limit):
    """把检索词压到上限内(默认传 UPSTREAM_TEXT_MAX)。超限时按上限硬截(上游是硬闸,不是软截断)。"""
    t = str(text or "")
    n = int(limit or UPSTREAM_TEXT_MAX)
    return t[:n]

class BudgetExhausted(Exception):
    """预算耗尽信号:停止发起上游请求,返回已获资料。"""

class Budget:
    """单次 ask 的上游请求硬上限(默认 32,可配置)。只对真实上游调用计数——
    require/spend 都在 _get_json 入口,本地缓存命中不经 _get_json,天然不计。"""
    def __init__(self, max_upstream):
        self.max = int(max_upstream or 0) or None
        self.used = 0
        self.exhausted = False

    def require(self):
        if self.max is not None and self.used >= self.max:
            self.exhausted = True
            raise BudgetExhausted()

    def spend(self):
        self.used += 1

class RateLimiter:
    """上游限速两档(CONTEXT v6 匿名链路):interactive=交互会话短突发 2-3 请求/秒+随机抖动
    (默认档,/ask 用);background=后台/摄取任务 1 请求/秒。令牌桶实现,只对真实上游请求生效
    (_get_json 入口),本地缓存命中不计;触发节流写日志+stderr,限速可观测。"""
    def __init__(self):
        self._lock = threading.Lock()
        self._next = 0.0
        self._profile = None  # 请求级切档(后台任务调用 rate_profile("background"));None=环境/配置默认

    def profile(self, name=None):
        cfg = route_cfg().get("rate") or {}
        name = str(name or self._profile or os.environ.get("KSEARCH_RATE") or "interactive").lower()
        p = cfg.get(name)
        if not isinstance(p, dict):  # 配置缺失时的安全默认(与 CONTEXT 档位一致)
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

_RATE = RateLimiter()

def rate_profile(name=None):
    """读/切当前限速档。/ask 请求入口按参数切;后台/摄取任务(#15)开始时切 background。"""
    if name is not None:
        _RATE.set_profile(name)
    return _RATE.profile()[0]

def log(*a):
    line = f"[{time.strftime('%m-%d %H:%M:%S')}] " + " ".join(str(x) for x in a)
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

def html2text(h):
    if not h:
        return ""
    h = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", h, flags=re.S | re.I)
    h = re.sub(r"<br\s*/?>", "\n", h, flags=re.I)
    h = re.sub(r"</(p|div|tr|h[1-6]|li|table)>", "\n", h, flags=re.I)
    h = re.sub(r"</t[dh]>", "\t", h, flags=re.I)
    h = re.sub(r"<[^>]+>", "", h)
    h = h.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"').replace("&#39;", "'")
    lines = [re.sub(r"[ \t]+\n", "\n", re.sub(r"[ \t]{2,}", " ", ln)).strip() for ln in h.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(ln for ln in lines if ln))

# ---------- 上游调用计数(每请求取前后差值,缓存命中不计) ----------
_UP_LOCK = threading.Lock()
_UP_N = 0

def _up_inc():
    global _UP_N
    with _UP_LOCK:
        _UP_N += 1

def _up_now():
    with _UP_LOCK:
        return _UP_N

class UpstreamError(Exception):
    """上游业务错误(HTTP 200 但 body 带 errorCode)。"""
    def __init__(self, code, message):
        self.code = code
        self.message = message
        super().__init__("upstream %s: %s" % (code, message))

def _get_json(url, budget=None):
    if budget is not None:
        budget.require()  # 预算硬上限:超限不再发起请求(issue #12)
    _RATE.wait()          # 限速只卡真实上游请求;缓存命中不走这里,不计
    _up_inc()
    if budget is not None:
        budget.spend()
    req = urllib.request.Request(url, headers=HDRS)
    with urllib.request.urlopen(req, timeout=20) as r:
        d = json.loads(r.read().decode("utf-8", "replace"))
    # 上游"假 200":HTTP 200 但 body 是错误壳(如 text 超 100 字符的 errorCode:409)。
    # 不识别会把"查询超限"静默降级成"无匹配结果"——调用方据此会误判官方没这类文档。
    if isinstance(d, dict) and d.get("errorCode"):
        raise UpstreamError(int(d["errorCode"]), str(d.get("message") or "")[:200])
    return d

def _is_true(v):
    return str(v or "").lower() == "true"

# ---------- 本地缓存语料库(sqlite,永久存储;表结构预留向量列) ----------
_DB = None
_DB_LOCK = threading.Lock()

def _db():
    global _DB
    if _DB is None:
        with _DB_LOCK:
            if _DB is None:
                os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
                conn = sqlite3.connect(DB_PATH, check_same_thread=False)
                conn.executescript("""
                CREATE TABLE IF NOT EXISTS search_cache(k TEXT PRIMARY KEY, resp TEXT, fetched_at REAL);
                CREATE TABLE IF NOT EXISTS detail_cache(k TEXT PRIMARY KEY, resp TEXT, fetched_at REAL);
                CREATE TABLE IF NOT EXISTS chunks(doc_key TEXT, seq INTEGER, heading TEXT, text TEXT, embedding BLOB,
                    PRIMARY KEY(doc_key, seq));
                CREATE VIRTUAL TABLE IF NOT EXISTS fts_chunks USING fts5(text, heading, doc_key UNINDEXED, seq UNINDEXED, tokenize='trigram');
                """)
                _DB = conn
    return _DB

def _cache_get(table, key):
    try:
        row = _db().execute("SELECT resp, fetched_at FROM %s WHERE k=?" % table, (key,)).fetchone()
        if not row:
            return None
        if table == "search_cache" and time.time() - row[1] > SEARCH_TTL:
            return None
        return row[0]
    except Exception:
        return None

def _cache_put(table, key, obj):
    try:
        with _DB_LOCK:
            _db().execute("INSERT OR REPLACE INTO %s(k, resp, fetched_at) VALUES(?,?,?)" % table,
                          (key, json.dumps(obj, ensure_ascii=False), time.time()))
            _db().commit()
    except Exception as e:
        log("cache_put fail:", str(e)[:100])

def _store_chunks(doc_key, text):
    if not text:
        return
    try:
        cs = chunk_text(text)
        with _DB_LOCK:
            db = _db()
            db.execute("DELETE FROM chunks WHERE doc_key=?", (doc_key,))
            db.execute("DELETE FROM fts_chunks WHERE doc_key=?", (doc_key,))
            for c in cs:
                db.execute("INSERT OR REPLACE INTO chunks(doc_key, seq, heading, text, embedding) VALUES(?,?,?,?,NULL)",
                           (doc_key, c["seq"], c["heading"], c["text"]))
                db.execute("INSERT INTO fts_chunks(text, heading, doc_key, seq) VALUES(?,?,?,?)",
                           (c["text"], c["heading"], doc_key, c["seq"]))
            db.commit()
    except Exception as e:
        log("store_chunks fail:", str(e)[:100])

def local_search(text, n=10):
    """纯本地语料检索(FTS5 trigram,毫秒级,不打上游);仅 cache 沉淀过的文档可见。"""
    terms = [t for t in _terms(text) if len(t) >= 3]  # trigram 最小 3 字符
    if not terms:
        return []
    q = " OR ".join('"%s"' % t.replace('"', "") for t in terms[:8])
    try:
        rows = _db().execute(
            "SELECT doc_key, seq, heading, text FROM fts_chunks WHERE fts_chunks MATCH ? LIMIT ?",
            (q, n)).fetchall()
        return [{"docKey": r[0], "seq": r[1], "heading": r[2], "text": r[3]} for r in rows]
    except Exception:
        return []

def db_stats():
    try:
        db = _db()
        d = db.execute("SELECT COUNT(*) FROM detail_cache").fetchone()[0]
        s = db.execute("SELECT COUNT(*) FROM search_cache").fetchone()[0]
        c = db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        return {"details": d, "searches": s, "chunks": c, "path": DB_PATH}
    except Exception:
        return {"details": 0, "searches": 0, "chunks": 0, "path": DB_PATH}

# ---------- 查询词项 ----------
def _terms(text):
    return [t for t in re.split(r"[\s,，、;；/()（）]+", str(text or "")) if len(t) >= 2]

# ---------- corpus 语料目录(v5:语料给 agent 和人,rg 直接搜;sqlite 只给服务) ----------
# 文件规范(~/.lingeebuild/corpus/<type>/<id>.md):front-matter(id/type/url/title/updatedAt/
# discovered_by/stub?/summary?)+ 正文全文。stub=邻域/时间网格发现的标题+摘要版,正文按需深读后写穿覆盖。
# 不变量:上游被请求,语料才更新;重复发现按 updatedAt 比对,变了才覆盖(stub→全文无条件升级)。
_CORPUS_LOCK = threading.Lock()
_CORPUS_URL = {"knowledge": VIP + "/knowledge/%s", "answer": VIP + "/question/%s", "article": VIP + "/article/%s"}

def _corpus_write(type_, oid, title, body, updated_at=None, summary=None,
                  discovered_by="usage", stub=False, url=None):
    """同步写穿(本地毫秒级,不阻塞回答)。返回 written|unchanged|error。
    落盘/幂等实现在 docstore(issue #10 抽出的复用模块,落地缓存/发版说明库同用一套约定)。"""
    type_ = str(type_ or "").lower()
    oid = str(oid or "").strip()
    if type_ not in _CORPUS_URL or not oid:
        return "error"
    fields = {"id": oid, "type": type_,
              "url": url or (_CORPUS_URL[type_] % oid),
              "title": title, "updatedAt": updated_at,
              "discovered_by": discovered_by, "summary": summary}
    if stub:
        fields["stub"] = "true"
    return docstore.write_doc(os.path.join(CORPUS_DIR, type_, "%s.md" % oid), fields, body,
                              lock=_CORPUS_LOCK, log=log)

def _corpus_body(kind, d):
    """corpus 正文:knowledge/article=全文;answer=问题正文+全部回答(采纳优先标记)+追问链。"""
    if kind != "answer":
        return d.get("contentText") or ""
    parts = [d.get("contentText") or ""]
    for a in (d.get("answers") or []):
        head = "## [采纳] 回答" if a.get("adopted") else "## 回答"
        who = " · %s · %s" % (a.get("creator") or "匿名", a.get("createdAt") or "") if (a.get("creator") or a.get("createdAt")) else ""
        parts.append("\n%s%s\n\n%s" % (head, who, a.get("contentText") or ""))
        for disc in (a.get("discussion") or []):
            parts.append("> 追问(%s):%s" % (disc.get("creator") or "", disc.get("contentText") or ""))
    return "\n\n".join(p for p in parts if p and p.strip())

def _corpus_sync(kind, d):
    """详情→corpus 写穿(路径①命中文档全文;②share 引用 stub 由 share_read 负责)。
    图游走(recommendArray)已于 v5.1 剔除:匿名不可达,见 docs/adr/0004 增补。"""
    try:
        if kind == "answer_detail":
            return  # 单条回答并入其问题全文文件,不单独落盘
        _corpus_write(d.get("type") or kind, d.get("id"), d.get("title"), _corpus_body(kind, d),
                      updated_at=d.get("updatedAt"), discovered_by="usage")
    except Exception as e:
        log("corpus sync fail:", str(e)[:100])

def _corpus_ingest(items, discovered_by):
    """发现层摄入(时间网格/图游走脚本统一入口):只写本地文件,零上游调用。"""
    n = {"written": 0, "unchanged": 0, "error": 0}
    for it in items or []:
        if not isinstance(it, dict):
            continue
        r = _corpus_write(it.get("type"), it.get("questionId") or it.get("id"),
                          it.get("title"), it.get("contentText") or "",
                          updated_at=it.get("updatedAt"),
                          summary=it.get("snippet") or it.get("summary"),
                          discovered_by=discovered_by or it.get("discovered_by") or "timesweep",
                          url=it.get("url"), stub=not (it.get("contentText") or "").strip())
        n[r] = n.get(r, 0) + 1
    return n

def corpus_stats():
    out = {"path": CORPUS_DIR}
    total = 0
    for t in ("knowledge", "answer", "article", "usage"):
        dd = os.path.join(CORPUS_DIR, t)
        c = sum(1 for f in os.listdir(dd) if f.endswith(".md")) if os.path.isdir(dd) else 0
        out[t] = c
        total += c
    out["total"] = total
    return out

# ---------- 落地缓存(v6 landing,issue #10):查询路径深读的本地沉淀 ----------
# 与 corpus(语料目录,删除是票 #13 的事)互相独立:一文档一 md + front-matter,discovered_by=query。
# 写穿常开、与 sqlite 上游缓存(KSEARCH_INDEX)完全解耦;本地毫秒级写,docstore 吞异常,绝不阻塞回答。
# 用途:rg 对它做字段名/报错原文的全文精查(ADR-0005 彻底在线:查过的毫秒级复用,没查过的不存在)。
_LANDING_TYPES = ("knowledge", "answer", "article")

def _landing_sync(kind, d):
    """深读详情 → 落地缓存写穿(discovered_by=query)。幂等/格式由 docstore 统一保证:
    重复深读按 updatedAt 比对,没变不重写(返回 unchanged),变了整文件覆盖。"""
    try:
        if kind == "answer_detail":
            return  # 单条回答并入其问题全文文件,不单独落盘(与 corpus 同约定)
        t = str(d.get("type") or kind).lower()
        if t not in _LANDING_TYPES:
            return
        oid = d.get("id")
        if not oid:
            return
        fields = {"id": oid, "type": t,
                  "url": d.get("url") or (_CORPUS_URL[t] % oid if t in _CORPUS_URL else None),
                  "title": d.get("title"), "updatedAt": d.get("updatedAt"),
                  "discovered_by": "query"}
        r = docstore.write_doc(docstore.doc_path(LANDING_DIR, t, oid), fields,
                               _corpus_body(kind, d), log=log)
        if r != "error":
            d["landing"] = r  # written|unchanged,响应可见的幂等证据(测试/调用方观察)
    except Exception as e:
        log("landing sync fail:", str(e)[:100])

def landing_stats():
    out = {"path": LANDING_DIR}
    total = 0
    for t in _LANDING_TYPES:
        dd = os.path.join(LANDING_DIR, t)
        c = sum(1 for f in os.listdir(dd) if f.endswith(".md")) if os.path.isdir(dd) else 0
        out[t] = c
        total += c
    out["total"] = total
    return out

# ---------- 信号重排 ----------
def _fresh_bonus(updated):
    try:
        y = int(str(updated)[:4])
        return 0.6 if y >= time.gmtime().tm_year - 1 else 0.3 if y >= time.gmtime().tm_year - 2 else 0.0
    except Exception:
        return 0.0

def rerank_bonus(item, terms, fulltext):
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

# ---------- 检索:三种实体全返回(可叠加管线) ----------
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

def _search_upstream_cached(text, product_id, page, page_size, global_, sorts_type, type_, cache_on, budget=None):
    text = clamp_query(text, UPSTREAM_TEXT_MAX)  # 上游 100 字硬闸:超限返回 errorCode:409 空壳,先在入口压回上限内
    def fetch(pg):
        params = {"text": text, "page": pg, "pageSize": page_size,
                  "global": "true" if global_ else "false", "sortsType": sorts_type}
        if product_id and int(product_id) != 0:  # 0=不过滤:必须省略参数,传 0 上游会当真值过滤(实测把 Knowledge 挤出前排)
            params["productIds[0]"] = int(product_id)
        return _get_json(VIP + "/api/search?" + urllib.parse.urlencode(params), budget)

    key = hashlib.sha1(json.dumps([text, product_id, page, page_size, global_, sorts_type, type_],
                                  ensure_ascii=False).encode()).hexdigest()
    if cache_on:
        hit = _cache_get("search_cache", key)
        if hit:
            d = json.loads(hit)
            d["_cached"] = True
            return d
    d = fetch(page)
    if cache_on:
        _cache_put("search_cache", key, d)
    return d

def knowledge_search(text, product_id=None, page=1, page_size=10, global_=False, sorts_type=1, type_=None,
                     max_scan_pages=5, rerank=False, cache_on=False, budget=None):
    """rerank=False 时与 v3.2 行为逐字节兼容(分页/扫描/scanNote 不变)。
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
            d = _search_upstream_cached(text, product_id, page, page_size, global_, sorts_type, type_, cache_on, budget)
            total, total_pages = d.get("totalElements"), d.get("totalPages")
            hits += 1 if d.get("_cached") else 0
            collect(d)
            pg = 2
            while len(items) < page * page_size and pg <= max_scan_pages and pg <= (total_pages or 1):
                dd = _search_upstream_cached(text, product_id, pg, page_size, global_, sorts_type, type_, cache_on, budget)
                hits += 1 if dd.get("_cached") else 0
                collect(dd)
                pg += 1
            items = items[(page - 1) * page_size: page * page_size]
            scan_note = f"type={type_} 过滤:跨上游 {pg - 1} 页扫描(混排结果按相关度抽取该类型)"
        else:
            d = _search_upstream_cached(text, product_id, page, page_size, global_, sorts_type, type_, cache_on, budget)
            total, total_pages = d.get("totalElements"), d.get("totalPages")
            hits += 1 if d.get("_cached") else 0
            collect(d)
        return {"ok": True, "text": text, "total": total, "queries": [text],
                "page": page, "pageSize": page_size, "totalPages": total_pages,
                "results": items, "scanNote": scan_note, "_cacheHits": hits}

    # ---- v4 管线路径:多路 + RRF + 信号重排 ----
    queries = [text]
    up_size = max(page_size, 25)
    lists, up_resps, total, total_pages = [], [], 0, 0
    for i, q in enumerate(queries):
        d = _search_upstream_cached(q, product_id, 1, up_size, global_, sorts_type, type_, cache_on, budget)
        total, total_pages = max(total, d.get("totalElements") or 0), max(total_pages, d.get("totalPages") or 0)
        lists.append(d.get("content") or [])
        up_resps.append(d)
    scores, raw_items = _rrf_fuse(lists)
    terms = _terms(text)
    cached_n = sum(1 for d in up_resps if d.get("_cached"))
    scored = []
    for k, x in raw_items.items():
        n = _norm_item(x, (x.get("entity-type") or "").lower())
        if not n:
            continue
        if type_ and n["type"] != type_.lower():
            continue
        scored.append((scores[k] + rerank_bonus(n, terms, text), n))
    scored.sort(key=lambda t: -t[0])
    items = [n for _, n in scored][(page - 1) * page_size: page * page_size]
    return {"ok": True, "text": text, "total": total,
            "page": page, "pageSize": page_size, "totalPages": total_pages,
            "results": items, "_cacheHits": cached_n,
            "scanNote": "v4管线:上游深扫描%d条×%d路%s,RRF(k=%d)+信号重排" % (
                up_size, len(queries), "+同义词变体" if len(queries) > 1 else "", RRF_K),
            "queries": queries}

# ---------- chunk 切片(标题感知,对齐金蝶文档【】结构) ----------
_HEADING_RE = re.compile(r"^(【[^】]{1,30}】|#{1,6}\s*\S.*|\d+[\.、．]\s*\S.{0,40})\s*$")

def chunk_text(text, size=500, max_len=700):
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

def top_chunks(chunks, terms, k=3):
    if len(chunks) <= k:
        return chunks
    def sc(c):
        return sum((1.5 if t in (c.get("heading") or "") else 0) + (1.0 if t in (c.get("text") or "") else 0) for t in terms)
    pick = sorted(chunks, key=lambda c: -sc(c))[:k]
    return sorted(pick, key=lambda c: c["seq"])

# ---------- 详情:知识 / 问答 / 文章 ----------
def knowledge_article(kid, budget=None):
    d = _get_json(VIP + "/knowledgeapi/knowledge/" + str(kid), budget)
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

def question_detail(qid, with_answers=True, max_answer_pages=3, max_detail=5, budget=None):
    d = _get_json(VIP + "/api/questions/" + str(qid), budget)
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
        # 预算硬上限(issue #12):回答展开(翻页+逐条详情)是深读里最贵的请求,超限即停,
        # 保留已获部分并标 truncated——截断结果不写缓存/不写穿落盘,避免 updatedAt 幂等把残缺全文钉死在落盘文件里。
        truncated = False
        answers, page = [], 1
        try:
            while page <= max_answer_pages:
                ad = _get_json(VIP + "/api/questions/%s/answers?page=%d&pageSize=20" % (qid, page), budget)
                for a in ad.get("content") or []:
                    answers.append(_answer_brief(a))
                if page >= (ad.get("totalPages") or 1):
                    break
                page += 1
            answers.sort(key=lambda a: (not a["adopted"]))
            for a in answers[:max(max_detail, 0)]:
                det = _answer_brief(_get_json(VIP + "/api/answers/" + a["id"], budget))
                if len(det.get("contentText") or "") > len(a.get("contentText") or ""):
                    a["contentText"] = det["contentText"]
                if det.get("discussion"):
                    a["discussion"] = det["discussion"]
        except BudgetExhausted:
            truncated = True
        except Exception:
            pass
        if truncated:
            out["truncated"] = True
        out["answers"] = answers
    return out

def answer_detail(aid, budget=None):
    d = _get_json(VIP + "/api/answers/" + str(aid), budget)
    q = d.get("question") or {}
    qid = str(d.get("questionId") or q.get("id") or "")
    return {"ok": True, "id": str(aid), "questionId": qid,
            "title": q.get("title"), "contentText": html2text(d.get("description")),
            "adopted": _is_true(d.get("isAdopt")), "usefuls": d.get("usefuls"),
            "url": f"{VIP}/question/{qid}" if qid else None,
            "updatedAt": d.get("updatedAt")}

def article_detail(aid, budget=None):
    d = _get_json(VIP + "/api/articles/" + str(aid), budget)
    classes = [c.get("name") for c in (d.get("classifies") or []) if c.get("name")]
    return {"ok": True, "id": str(aid), "type": "article", "title": d.get("title"),
            "contentText": html2text(d.get("content")),
            "url": f"{VIP}/article/{aid}",
            "products": classes[:3], "supports": d.get("supports"), "views": d.get("views"),
            "updatedAt": d.get("updatedAt")}

_DETAIL_FN = {"knowledge": knowledge_article, "answer": question_detail,
              "article": article_detail, "answer_detail": answer_detail}

def detail_cached(kind, oid, cache_on=False, refresh=False, stats=None, budget=None):
    """详情统一入口:cache_on 时优先本地(明细永久),refresh=1 强制回源;
    corpus 写穿与落地缓存写穿(v6 landing)都与缓存开关无关、常开,本地毫秒级不阻塞回答。
    预算(issue #12):require/spend 在 _get_json,缓存命中零预算消耗;truncated(预算截断)
    的结果不写缓存、不写穿落盘,避免 updatedAt 幂等把残缺全文钉死在落盘文件里。"""
    key = "%s:%s" % (kind, oid)
    if cache_on and not refresh:
        hit = _cache_get("detail_cache", key)
        if hit:
            if stats is not None:
                stats["cacheHits"] = stats.get("cacheHits", 0) + 1
            d = json.loads(hit)
            d["fromCache"] = True
            _corpus_sync(kind, d)
            _landing_sync(kind, d)
            return d
    d = _DETAIL_FN[kind](oid, budget=budget)
    truncated = bool(d.get("truncated"))
    if cache_on and not truncated:
        _cache_put("detail_cache", key, d)
        _store_chunks(key, d.get("contentText") or "")
    if not truncated:
        _corpus_sync(kind, d)
        _landing_sync(kind, d)
    return d

# ---------- /ask 一站式问答包(多路关键词编排 + RRF 融合 + 深读写穿 + chunk) ----------
def _fetch_for_item(item, cache_on, refresh, budget=None):
    try:
        if item["type"] == "knowledge":
            d = detail_cached("knowledge", item["id"], cache_on, refresh, budget=budget)
        elif item["type"] == "answer":
            d = detail_cached("answer", item.get("questionId") or item["id"], cache_on, refresh, budget=budget)
        elif item["type"] == "article":
            d = detail_cached("article", item["id"], cache_on, refresh, budget=budget)
        else:
            d = None
    except BudgetExhausted:
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

def plan_routes(text=None, keywords=None, product_id=None):
    """一句自然语言问题 → ≤maxRoutes 路关键词(症状词路+字段/实体名词路+产品词路+原句路)。
    拆解规则全部在 query_routes.json(语料可配置,沉淀词表只改数据文件,issue #12):
      1) 产品词:productAliases 命中问句且调用方未指定 productId 时推导,随后所有路统一携带
         productIds 过滤(实测无产品过滤时字段名路被全产品噪声淹没,金标文档挤出 25 条外);
      2) 症状词路:问句停用词剥离后的 CJK 片段+拉丁/数字 token,按 symptomCategories 归类成路;
      3) 字段/实体名词路:entityRules 按 whenAny/whenRegex 触发,产出领域术语变体(跨词汇鸿沟的桥);
      4) 产品/上下文词路:剩余未归类 token(产品名/BOM 等上下文词)成一路,携带 productIds;
      5) 原句路(ADR-0009):完整问句作为独立一路,sortsType=1(相关性排序)。
         它不是兜底——实测同一文档在原句+sortsType=1 下排第 1,而片段路由 RRF 融合后掉到第 23,
         系统取 top4 时金牌出局。原句与片段信息独立,故恒常参与融合且保席位。
         kind 取 "raw:question"(区别于兜底路 "raw":后者仅在什么都拆不出时触发且带 60 字截断语义)。
    返回 (routes, product_id),route={kind,terms,why,productIds?,sortsType?}。"""
    cfg = route_cfg()
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
        # 显式关键词路与主路径同权:产品过滤必须统一携带,
        # 否则 --kw 绕过 --product 造成跨产品线串线(票 #17)
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
    for i, cat in enumerate(cfg.get("symptomCategories", {}).get("categories", [])
                            if isinstance(cfg.get("symptomCategories"), dict) else (cfg.get("symptomCategories") or [])):
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
            terms += [t for t in numeric if t not in used][:2]  # 纯数字量词归第一症状路(量化症状)
        if terms:
            routes.append({"kind": "symptom:" + str(cat.get("name") or "symptom"),
                           "terms": " ".join(terms[:4]), "why": cat.get("why")})
    leftover_cjk = [c for c in cjk if c not in used]
    leftover_lat = [t for t in latin if t not in used and not re.fullmatch(r"\d+", t)]
    # 2) 字段/实体名词路:症状反推领域术语(词汇鸿沟的桥)
    for rule in ((cfg.get("entityRules") or {}).get("rules", [])
                 if isinstance(cfg.get("entityRules"), dict) else (cfg.get("entityRules") or [])):
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
    # 原句路并入候选列表(在最前),统一走下面的保席位截断
    if raw_route:
        routes.insert(0, raw_route)
    # 产品过滤:所有路统一携带(问句/调用方给出的产品上下文)
    if product_id and int(product_id) != 0:
        for r in routes:
            r["productIds"] = int(product_id)
    # 上限截断:产品词路与原句路保席位(ADR-0009——两者携带的信息维度不可被片段路挤掉)
    if len(routes) > max_routes:
        pinned_kinds = ("product", "raw:question", "raw")
        pinned = [r for r in routes if r["kind"] in pinned_kinds]
        rest = [r for r in routes if r["kind"] not in pinned_kinds]
        routes = rest[:max(0, max_routes - len(pinned))] + pinned
        # 保席位后按原句→片段→产品的稳定顺序回排(原句路恒在首位)
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

def ask_bundle(text=None, keywords=None, product_id=None, top_k=4,
               rerank=False, cache_on=False, refresh=False, budget=None):
    cfg = route_cfg()
    top_k = max(1, min(int(top_k or (cfg.get("deepRead") or {}).get("topK") or 4), 8))
    routes, product_id = plan_routes(text=text, keywords=keywords, product_id=product_id)
    page_size = int(cfg.get("searchPageSize") or 25)
    # 多路检索:每路一次上游搜索(混排 answer+knowledge,产品过滤统一携带),路间 RRF 融合
    lists, total = [], 0
    for r in routes:
        if budget is not None and budget.used >= (budget.max or 0):
            budget.exhausted = True
            break
        try:
            res = knowledge_search(r["terms"], product_id=r.get("productIds"), page=1,
                                   page_size=page_size, rerank=rerank, cache_on=cache_on, budget=budget,
                                   sorts_type=int(r.get("sortsType") or 1))
        except BudgetExhausted:
            break
        except UpstreamError as e:
            # 上游业务错误(HTTP 200 但 body 带 errorCode,如 text 超 100 字符的 409)。
            # 不能与"该路无结果"混为一谈——显式记日志,否则静默吞掉会把"查询被上游拒绝"
            # 伪装成"官方没这类文档",调用方据此得出错误结论。
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
    # ---- 票 #21:查询内语义重排(默认 OFF)----
    # 插入点:深读 topK 选择(_select_top)**之前**对候选按 query-doc 语义分重排,
    # 决定哪些文档被深读(ask 链路的咽喉);展示排序(下方 sources 的三层路由规则)完全不动。
    # 模型缺失/未装/异常/超时(>5s)一律静默降级为原排序,semanticRerank.reason 可见。
    def _sr_err(msg):
        log("SEMANTIC-RERANK:", msg)
        try:
            sys.stderr.write("[semantic-rerank] %s\n" % msg)
        except Exception:
            pass
    try:
        ranked, semantic_rerank_info = semantic_rerank.rerank_candidates(
            text or " ".join(r["terms"] for r in routes), ranked, err_fn=_sr_err)
    except Exception as e:  # 兜底:任何重排故障都不影响回答可用性
        _sr_err("unexpected fail: %s" % str(e)[:120])
        ranked_fallback = sorted(items.values(), key=lambda x: -scores[_fused_key(x)])
        ranked, semantic_rerank_info = ranked_fallback, {
            "enabled": semantic_rerank.ENABLED_DEFAULT, "reordered": False,
            "reason": "unexpected fail: %s" % str(e)[:120], "candidates": 0, "scores": [],
            "elapsedMs": None, "model": "bge-small-zh-v1.5"}
    sel = _select_top(ranked, top_k)
    # 深读顺序:knowledge(根因,每源 1 请求)优先消耗预算,answer(展开可能多请求)殿后;
    # 展示排序另行按 displayOrder(answer 优先)重排。缓存命中零开销,预算只卡真实上游。
    fetch_order = sorted(sel, key=lambda x: ({"knowledge": 0, "article": 1}.get(x["type"], 2), -scores[_fused_key(x)]))
    results_map = {}
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = [(ex.submit(_fetch_for_item, it, cache_on, refresh, budget), it) for it in fetch_order]
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
            ch = top_chunks(chunk_text(atext), terms)
            if ch:
                d["chunks"] = ch
        sources.append({"rank": len(sources) + 1, "type": item["type"], "id": item.get("id"),
                        "questionId": item.get("questionId"),
                        "title": item.get("title"), "url": item.get("url"),
                        "snippet": item.get("snippet"), "fromCache": d.get("fromCache") if d else None,
                        "fusedScore": round(scores[_fused_key(item)], 5),
                        # 票 #18:产品线上浮到 source 顶层(detail 优先,缺省回退检索条目),免翻 detail 甄别产品线
                        "products": d.get("products") or item.get("products") or [],
                        "detail": d})
    return {"ok": True, "text": text or " / ".join(r["terms"] for r in routes) or None,
            "total": total,
            "effectiveProductId": product_id,  # 票 #18:回显本次 ask 实际生效的产品过滤(plan_routes 返回)
            "routes": routes,  # 多路拆解明细(kind/terms/why/productIds/sortsType)
            "queries": [r["terms"] for r in routes],  # 兼容 v5 字段
            "sources": sources,
            "budget": {"max": budget.max if budget else None, "used": budget.used if budget else None,
                       "upstreamCalls": budget.used if budget else None},
            "budget_exhausted": bool(budget and budget.exhausted),
            # 票 #21:语义重排可见性(enabled/reason/reordered/candidates/scores/elapsedMs;失败降级时 reason 说明)
            "semanticRerank": semantic_rerank_info,
            "_cacheHits": sum(1 for s in sources if s.get("fromCache")),
                    "note": "v6.2 原句路+多路关键词编排:routes[] 为拆解明细(规则在 service/query_routes.json,语料可配置);"
                    "sources 展示排序 answer 优先(症状对齐)、knowledge 紧随(根因);sources[].detail 已含全文并写穿"
                    "落地缓存;knowledge/article 附 chunks(标题感知切片,top3 相关段);调用方 AI 据此合成带引用回答,"
                    "可引用 [chunk#seq];budget=上游请求硬上限,超限即停并标 budget_exhausted;"
                    "effectiveProductId=本次实际生效的产品过滤,sources[].products=各源产品线(顶层直读,票 #18)"}

# ---------- /share 官方分享对话(匿名) ----------
def share_read(link_or_id):
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
        return {"ok": False, "error": "cannot resolve chatId", "hint": "传分享短链、/searchchats/{id} 页面链接或纯数字 chatId"}
    d = _get_json(VIP + "/aisapi/ai-search/sharing-chats/" + chat_id)
    chats = []
    seen = set()
    for c in d.get("chats") or []:
        refs = [{"title": x.get("title"), "url": x.get("url"),
                 "summary": (x.get("summary") or "")[:200],
                 "entityType": x.get("entityType"), "entityId": x.get("entityId")}
                for x in (c.get("recallDocuments") or [])]
        chats.append({"question": c.get("searchText"), "answer": c.get("content"),
                      "answerType": c.get("answerType"), "refs": refs})
        for ref in refs:  # 官方 AI 引用文档 → corpus stub(discovered_by=share,零额外上游请求)
            _corpus_write((ref.get("entityType") or "").lower(), ref.get("entityId"),
                          ref.get("title") or "", "", summary=ref.get("summary"),
                          discovered_by="share", stub=True, url=ref.get("url"))
    return {"ok": True, "chatId": chat_id, "count": len(chats), "chats": chats}

# ---------- /manifest 机器可读能力清单 ----------
def _manifest():
    return {
        "service": "kingdee-ksearch", "version": "6.2", "anonymous": True,
        "description": "金蝶官方知识库匿名检索/全文/资料包(逆向官方社区后端,零账号零点数,零模型依赖);v6.2:/ask 内置原句路+多路关键词编排(≤7 路 RRF 融合,规则在 service/query_routes.json)+上游预算硬上限+两档限速(交互短突发 2-3 req/s+抖动/后台 1 req/s);上游 text 有 100 字符硬上限(超限 errorCode:409,已自动压回并显式报错);v6 彻底在线(ADR-0005):查询时检索+落地缓存写穿",
        "corpus": {"dir": CORPUS_DIR, "write": "read/ask 深读同步写穿全文;POST /corpus 摄入 stub;share 引用自动落盘;"
                   "v6 起 corpus 检索面废除(ADR-0005),本地检索面在 landing+releasenotes(rg 精查),usage/ 子目录承载会话收尾金标沉淀",
                   "discovered_by": ["usage", "share"]},
        "landing": {"dir": LANDING_DIR,
                    "write": "kd read/ask 深读全文自动写穿(常开,不阻塞回答,与 cache 开关无关);discovered_by=query;updatedAt 幂等(没变不重写,变了覆盖);格式与 corpus 同约定(docstore 模块,发版说明库复用)",
                    "search": "agent 用 rg 对落地缓存做字段名/报错原文全文精查(ADR-0005:查过的毫秒级复用,没查过的不存在;rg 不是在线发现层入口,常规检索走 kd ask)"},
        "releasenotes": {"dir": os.path.join(os.path.expanduser("~"), ".lingeebuild", "releasenotes"),
                         "search": "agent 用 rg 对发版说明库(93 旗舰版近 1 年,一版本一 md,官方「模块-问题-修复」结构)做修复项关键词精查;「官方已修复」类问题的终审依据,前两层解释不了根因时的第三跳(ADR-0005)"},
        "pipeline": {"params": {"rerank": "信号重排(opt-in 实验,评测:recall@10 -11%),默认关",
                                 "cache": "本地sqlite上游缓存,默认关(KSEARCH_INDEX=on 开);v5 缩编为纯缓存,不再扩展",
                                 "refresh": "强制回源,默认关", "local": "只查本地语料(FTS5)【deprecated:冻结开发,检索角色由 corpus+rg 接管】"},
                      "env": {"KSEARCH_RERANK": "默认0(实验)", "KSEARCH_INDEX": "默认0",
                              "KSEARCH_RERANK_SEMANTIC": "默认off(票 #21 查询内语义重排,本地 bge-small ONNX,只作用深读 topK 选择;on 开)",
                              "KSEARCH_DB": "默认 <根>/data/ksearch.db", "KSEARCH_CORPUS": "默认 <根>/corpus"},
                      "data": ["<corpus>/usage/ 会话收尾金标沉淀(usage 层)",
                               "data/ksearch.db 上游缓存(纯缓存,永久)",
                               "data/eval/evalset.json 评测集", "scripts/run_eval.py A/B评测",
                               "scripts/releasenotes_ingest.py 发版说明摄取(唯一预囤发现腿,手动触发)"]},
        "cli": {"path": os.path.join(_ROOT, "bin", "kd.cmd" if os.name == "nt" else "kd"),
                "commands": ["kd ask \"<问题>\" [--topk 4]  # 唯一常规入口:原句路+多路关键词拆解(≤7路 RRF)+深读写穿落地缓存+上游预算+synthesisBrief 召回信号;--kw 显式关键词跳过拆解",
                             "kd search \"<关键词>\" [--product 93] [--type knowledge|answer|article] [--size 10]  # 手动细粒度调试命令",
                             "kd read <id> [--kind knowledge|answer|article]  # 手动细粒度调试命令;kind 照抄 search 结果的 type",
                             "kd read <chunkId> --chunk  # 按官方 AI 引用 chunkId 匿名还原块全文(ADR-0007)",
                             "kd share <分享短链|chatId>", "kd manifest", "kd health"]},
        "endpoints": {
            "GET|POST /search": {"params": {"text": "string,必填,关键词", "productId": "int,93=星空旗舰版/87=苍穹/1=企业版标准版/0=不过滤",
                                            "page": "int", "pageSize": "int<=50", "global": "bool",
                                            "sortsType": "int,1=综合", "type": "knowledge|answer|article 可选过滤",
                                            "pipeline": "{rerank,cache} 可按请求覆盖,默认见 pipeline.env"},
                                 "returns": "results[] 三种实体混合,type 字段区分;带 stats{upstreamCalls,cacheHits,elapsedMs}"},
            "GET|POST /karticle": {"params": {"id": "knowledge 条目的 id", "cache/refresh": "可选"}, "returns": "知识库全文 contentText"},
            "GET|POST /question": {"params": {"id": "answer 条目的 questionId"}, "returns": "问题正文+全部回答(采纳优先,前5条拉详情)+追问链 discussion"},
            "GET|POST /answer": {"params": {"id": "answer 条目的 id"}, "returns": "单条回答全文"},
            "GET|POST /article": {"params": {"id": "article 条目的 id"}, "returns": "社区文章全文"},
            "GET|POST /ask": {"params": {"text": "自然语言问题(与 keywords 二选一);原句路+多路关键词拆解(≤7 路 RRF 融合,规则在 service/query_routes.json);注意上游 text 上限 100 字符,原句路自动截断",
                                         "keywords": "显式关键词数组(跳过自动拆解,每词一路,上限6路)",
                                         "productId": "int,93=星空旗舰版/87=苍穹/1=企业版标准版/0=不过滤(未指定时问句产品词自动推导,所有路统一携带)",
                                         "topK": "深读条数 1-8,默认4", "budget": "上游请求硬上限覆盖(默认 32,超限即停)",
                                         "rate": "限速档 interactive(默认,短突发+抖动)|background(1 req/s)",
                                         "pipeline": "可选覆盖"},
                              "returns": "routes[] 拆解明细 + sources[] 深读全文资料包(展示排序 answer 优先/knowledge 紧随,"
                                         "knowledge/article 附 top3 相关 chunk)+ budget{max,used} 与 budget_exhausted;"
                                         "深读写穿落地缓存;调用方 AI 合成带引用回答"},
            "GET|POST /share": {"params": {"link": "官方分享短链 /searchchats/{id} 页面链接或纯数字 chatId"},
                                "returns": "chats[] 官方 AI 分享对话全文(问题/Markdown 回答/引用 recallDocuments);引用文档自动落 corpus stub"},
            "POST /corpus": {"params": {"items": "数组[{type,id|questionId,title,snippet?,url?,updatedAt?,contentText?}]",
                                        "discoveredBy": "usage|graph|timesweep|share(默认 timesweep)"},
                             "returns": "{written,unchanged,error} 计数;纯本地落盘零上游调用"},
            "GET /manifest": {"returns": "本清单"},
            "GET /health": {"returns": "存活与版本"},
        },
        "entities": {"knowledge": {"read": "/karticle", "note": "官方文档,权威优先"},
                     "answer": {"read": "/question?id=<questionId>", "note": "社区问答帖,条目内联 questionId/questionBody/adopted;网页有登录门但 API 匿名"},
                     "article": {"read": "/article", "note": "社区文章"}},
        "notes": ["纯匿名:零账号/零点数/无 LLM 生成", "保持人类调用频率", "上游接口铁证见交接文档 13/14;管线设计见交接文档 16"]
    }

class H(BaseHTTPRequestHandler):
    def _reply(self, code, obj):
        b = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _pipe(self, qs, body):
        """解析管线参数:query/body 顶层 bool 或 body.pipeline{} 覆盖环境默认。"""
        pv = dict(body.get("pipeline") or {}) if isinstance(body, dict) else {}

        def val(name, default):
            v = pv.get(name)
            if v is None:
                if name in qs:
                    v = qs[name][0]
                elif isinstance(body, dict) and name in body:
                    v = body[name]
            if v is None:
                return default
            if isinstance(v, bool):
                return v
            return str(v).lower() in ("1", "true", "on", "yes")
        return {"rerank": val("rerank", RERANK_DEFAULT),
                "cache": val("cache", INDEX_DEFAULT),
                "refresh": val("refresh", False),
                "local": val("local", False)}

    def _stats_wrap(self, res, n0, t0, p):
        res["stats"] = {"upstreamCalls": _up_now() - n0,
                        "cacheHits": res.get("_cacheHits", 1 if res.get("fromCache") else 0),
                        "elapsedMs": round((time.time() - t0) * 1000, 1),
                        "pipeline": {k: v for k, v in p.items()}}
        return res

    def _search(self, qs, body=None):
        text = (qs.get("text") or [""])[0].strip() or (body or {}).get("text", "")
        if not text:
            return self._reply(400, {"error": "text required. 例: /search?text=信用额度&productId=93"})
        p = self._pipe(qs, body or {})
        n0, t0 = _up_now(), time.time()
        if p["local"]:  # 纯本地语料检索(不打上游)
            res = {"ok": True, "text": text, "local": True, "results": local_search(text)}
            return self._reply(200, self._stats_wrap(res, n0, t0, p))
        res = knowledge_search(text,
                               product_id=(qs.get("productId") or [None])[0] or (body or {}).get("productId"),
                               page=int((qs.get("page") or ["1"])[0]),
                               page_size=int((qs.get("pageSize") or ["10"])[0]),
                               global_=((qs.get("global") or ["false"])[0] == "true"),
                               sorts_type=int((qs.get("sortsType") or ["1"])[0]),
                               type_=(qs.get("type") or [None])[0],
                               rerank=p["rerank"], cache_on=p["cache"])
        log("SEARCH:", text[:50], "| total", res["total"], "| returned", len(res["results"]),
            "| pipeline", {k: v for k, v in p.items() if v})
        return self._reply(200, self._stats_wrap(res, n0, t0, p))

    def _by_id(self, qs, kind, name):
        i = (qs.get("id") or [""])[0].strip()
        if not i:
            return self._reply(400, {"error": "id required. 例: /%s?id=<id>" % name})
        p = self._pipe(qs, {})
        n0, t0 = _up_now(), time.time()
        res = detail_cached(kind, i, cache_on=p["cache"], refresh=p["refresh"])
        log(name.upper() + ":", i, "| len", len(res.get("contentText") or ""), "| cached", bool(res.get("fromCache")),
            "| landing", res.get("landing"))
        return self._reply(200, self._stats_wrap(res, n0, t0, p))

    def _ask(self, qs, body=None):
        body = body or {}
        text = (qs.get("text") or [""])[0].strip() or body.get("text") or ""
        keywords = body.get("keywords")
        if not text and not keywords:
            return self._reply(400, {"error": "text or keywords required",
                                     "example": "/ask?text=信用额度控制&topK=4 或 POST {\"keywords\":[\"信用额度\",\"应收单 信用\"],\"topK\":4}"})
        p = self._pipe(qs, body)
        # 限速档:/ask 默认交互档(短突发+抖动);请求参数 rate=background 可切后台档(摄取类调用)
        rate_param = body.get("rate") or (qs.get("rate") or [None])[0]
        rate_used = rate_profile(str(rate_param) if rate_param else None)
        # 预算硬上限:请求参数 budget > 环境变量 KSEARCH_ASK_BUDGET > query_routes.json(默认 32)
        bv = body.get("budget") or (qs.get("budget") or [None])[0]
        max_up = int(bv) if bv and str(bv).isdigit() else _cfg_budget_max()
        budget = Budget(max_up)
        n0, t0 = _up_now(), time.time()
        res = ask_bundle(text=text or None, keywords=keywords,
                         product_id=(qs.get("productId") or [None])[0] or body.get("productId"),
                         top_k=(qs.get("topK") or ["4"])[0],
                         rerank=p["rerank"], cache_on=p["cache"], refresh=p["refresh"], budget=budget)
        log("ASK:", (text or "kw×%d" % len(keywords))[:50], "| routes", len(res.get("routes") or []),
            "| sources", len(res["sources"]), "| upstream", res["budget"]["used"], "/", res["budget"]["max"],
            "| rate", rate_used, "| exhausted", res["budget_exhausted"])
        return self._reply(200, self._stats_wrap(res, n0, t0, p))

    def _share(self, qs, body=None):
        link = (qs.get("link") or [""])[0].strip() or (body or {}).get("link") or ""
        if not link:
            return self._reply(400, {"error": "link required", "example": "POST {\"link\":\"https://vip.kingdee.com/link/s/xxxx\"}"})
        res = share_read(link)
        log("SHARE:", link[-40:], "| chats", res.get("count"))
        return self._reply(200, res)

    def _corpus_ep(self, body=None):
        body = body or {}
        items = body.get("items")
        if not isinstance(items, list) or not items:
            return self._reply(400, {"error": "items required",
                                     "example": "POST /corpus {\"items\":[{\"type\":\"article\",\"id\":\"642448594288545024\",\"title\":\"..\",\"snippet\":\"..\",\"url\":\"..\"}],\"discoveredBy\":\"timesweep\"}"})
        n = _corpus_ingest(items, body.get("discoveredBy"))
        log("CORPUS:", "written=%d unchanged=%d error=%d" % (n.get("written", 0), n.get("unchanged", 0), n.get("error", 0)),
            "| by", body.get("discoveredBy") or "timesweep")
        return self._reply(200, {"ok": True, **n})

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(u.query)
        if u.path in ("/", "/manifest"):
            return self._reply(200, _manifest())
        if u.path == "/health":
            return self._reply(200, {"service": "kingdee-ksearch v6.2", "anonymous": True,
                                     "pipeline": {"rerank": RERANK_DEFAULT, "cache": INDEX_DEFAULT},
                                     "ask": {"multiRoute": True, "routesCfg": _ROUTE_CFG_PATH,
                                             "budgetMax": _cfg_budget_max(), "rateProfile": rate_profile()},
                                     "rerankSemantic": semantic_rerank.status(),  # 票 #21:开关+模型加载状态
                                     "db": db_stats(), "corpus": corpus_stats(), "landing": landing_stats(),
                                     "endpoints": ["/manifest", "/search", "/karticle", "/question", "/answer", "/article", "/ask", "/share", "/corpus", "/health"],
                                     "note": "v6.2:/ask 内置原句路+多路关键词编排(≤7 路 RRF,规则 service/query_routes.json)+"
                                             "上游预算硬上限(默认 32,超限即停 budget_exhausted)+两档限速(交互短突发+抖动/后台 1req/s,只卡真实上游);"
                                             "v6 落地缓存 landing:深读写穿独立落盘(常开,updatedAt 幂等,discovered_by=query,rg 精查);"
                                             "sqlite 缩编为纯上游缓存;官方 ai-search 管线需登录,不用"})
        if u.path == "/corpus":
            return self._reply(200, {"ok": True, **corpus_stats()})
        if u.path == "/search":
            return self._search(qs)
        if u.path == "/karticle":
            return self._by_id(qs, "knowledge", "karticle")
        if u.path == "/question":
            return self._by_id(qs, "answer", "question")
        if u.path == "/answer":
            return self._by_id(qs, "answer_detail", "answer")
        if u.path == "/article":
            return self._by_id(qs, "article", "article")
        if u.path == "/ask":
            return self._ask(qs)
        if u.path == "/share":
            return self._share(qs)
        return self._reply(404, {"error": "GET /search?text=.. | /karticle?id=.. | /question?id=.. | /answer?id=.. | /article?id=.. | /ask?text=.. | /health"})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return self._reply(400, {"error": "bad json"})
        qs = {k: [str(v)] for k, v in body.items() if v is not None and k != "pipeline"}
        if self.path == "/search":
            return self._search(qs, body)
        if self.path == "/karticle":
            return self._by_id(qs, "knowledge", "karticle")
        if self.path == "/question":
            return self._by_id(qs, "answer", "question")
        if self.path == "/answer":
            return self._by_id(qs, "answer_detail", "answer")
        if self.path == "/article":
            return self._by_id(qs, "article", "article")
        if self.path == "/ask":
            return self._ask(qs, body)
        if self.path == "/share":
            return self._share(qs, body)
        if self.path == "/corpus":
            return self._corpus_ep(body)
        return self._reply(404, {"error": "POST /search | /karticle | /question | /answer | /article | /ask | /corpus"})

    def log_message(self, *a):
        pass

if __name__ == "__main__":
    log(f"kingdee-ksearch v6.2 listening on {PORT} (rerank={RERANK_DEFAULT} cache={INDEX_DEFAULT} corpus={CORPUS_DIR} landing={LANDING_DIR} budget={_cfg_budget_max()} rate={rate_profile()})")
    print(f"kingdee-ksearch v6.2 on :{PORT} (multi-route ask budget={_cfg_budget_max()} rate={rate_profile()} rerank={RERANK_DEFAULT} cache={INDEX_DEFAULT} landing={LANDING_DIR})")
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
