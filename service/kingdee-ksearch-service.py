#!/usr/bin/env python3
"""金蝶知识检索服务 v5.0 —— 匿名检索 + corpus 语料目录(grep 语料)+ 检索管线(本地缓存缩编为纯上游缓存)
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
  图游走(recommendArray)v5.1 剔除:匿名不可达(ADR-0004 增补)。
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
  /ask       {"text"} 或 {"keywords":[..]} + {"productId"?,"topK"=4,"pipeline"?}
             → 一站式问答包:多路检索(RRF+重排)→并行深读 topK 全文→附 top 相关 chunk,
               调用方 AI 拿包即合成带引用回答(官方问答效果的无登录等价)
  /share     {"link"} → 官方 AI 分享对话全文(评测集素材)
  /health
"""
import json, math, os, re, sqlite3, sys, threading, time, urllib.request, urllib.parse, hashlib
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

VIP = "https://vip.kingdee.com"
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 4097
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(_ROOT, "logs", "ksearch-service.log")
DB_PATH = os.environ.get("KSEARCH_DB", os.path.join(_ROOT, "data", "ksearch.db"))
CORPUS_DIR = os.environ.get("KSEARCH_CORPUS", os.path.join(_ROOT, "corpus"))
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/152.0.0.0"
HDRS = {"User-Agent": UA, "Accept": "application/json"}
# 管线默认值(环境变量;单请求可用 rerank/cache/refresh/local 参数覆盖)
# 评测结论(data/eval,交接文档 16):信号重排默认关——手工信号权重会压过上游相关度排序
# (recall@10 -11%),仅作 opt-in 实验保留;同义词变体已按评测移除(无召回增益,时延×2)。
RERANK_DEFAULT = os.environ.get("KSEARCH_RERANK", "0").lower() in ("1", "true", "on")
INDEX_DEFAULT = os.environ.get("KSEARCH_INDEX", "0").lower() in ("1", "true", "on")
SEARCH_TTL = 7 * 86400  # 搜索缓存 7 天;明细缓存永久(知识文档基本不可变,refresh=1 强制回源)
RRF_K = 60

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

def _get_json(url):
    _up_inc()
    req = urllib.request.Request(url, headers=HDRS)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8", "replace"))

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

def _corpus_read_fm(path):
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read(4096)
    except Exception:
        return {}
    if not text.startswith("---"):
        return {}
    fm = {}
    for ln in text.split("\n", 40)[1:]:
        if ln.strip() == "---":
            break
        m = re.match(r"([A-Za-z_]+):\s*(.*)", ln)
        if m:
            fm[m.group(1)] = m.group(2).strip()
    return fm

def _corpus_write(type_, oid, title, body, updated_at=None, summary=None,
                  discovered_by="usage", stub=False, url=None):
    """同步写穿(本地毫秒级,不阻塞回答)。返回 written|unchanged|error。"""
    type_ = str(type_ or "").lower()
    oid = str(oid or "").strip()
    if type_ not in _CORPUS_URL or not oid:
        return "error"
    path = os.path.join(CORPUS_DIR, type_, "%s.md" % oid)
    fm_old = _corpus_read_fm(path)
    if fm_old:
        upgraded = fm_old.get("stub") == "true" and body  # stub → 全文:无条件升级
        if not upgraded and fm_old.get("updatedAt") == (updated_at or ""):
            return "unchanged"
    lines = ["---", "id: %s" % oid, "type: %s" % type_,
             "url: %s" % (url or (_CORPUS_URL[type_] % oid)),
             "title: %s" % re.sub(r"\s+", " ", title or "")]
    if updated_at:
        lines.append("updatedAt: %s" % updated_at)
    lines.append("discovered_by: %s" % discovered_by)
    if summary:
        lines.append("summary: %s" % re.sub(r"\s+", " ", summary)[:300])
    if stub:
        lines.append("stub: true")
    try:
        with _CORPUS_LOCK:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n---\n\n" + (body or ""))
        return "written"
    except Exception as e:
        log("corpus write fail:", str(e)[:100])
        return "error"

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

def _search_upstream_cached(text, product_id, page, page_size, global_, sorts_type, type_, cache_on):
    def fetch(pg):
        params = {"text": text, "page": pg, "pageSize": page_size,
                  "global": "true" if global_ else "false", "sortsType": sorts_type}
        if product_id and int(product_id) != 0:  # 0=不过滤:必须省略参数,传 0 上游会当真值过滤(实测把 Knowledge 挤出前排)
            params["productIds[0]"] = int(product_id)
        return _get_json(VIP + "/api/search?" + urllib.parse.urlencode(params))

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
                     max_scan_pages=5, rerank=False, cache_on=False):
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
            d = _search_upstream_cached(text, product_id, page, page_size, global_, sorts_type, type_, cache_on)
            total, total_pages = d.get("totalElements"), d.get("totalPages")
            hits += 1 if d.get("_cached") else 0
            collect(d)
            pg = 2
            while len(items) < page * page_size and pg <= max_scan_pages and pg <= (total_pages or 1):
                dd = _search_upstream_cached(text, product_id, pg, page_size, global_, sorts_type, type_, cache_on)
                hits += 1 if dd.get("_cached") else 0
                collect(dd)
                pg += 1
            items = items[(page - 1) * page_size: page * page_size]
            scan_note = f"type={type_} 过滤:跨上游 {pg - 1} 页扫描(混排结果按相关度抽取该类型)"
        else:
            d = _search_upstream_cached(text, product_id, page, page_size, global_, sorts_type, type_, cache_on)
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
        d = _search_upstream_cached(q, product_id, 1, up_size, global_, sorts_type, type_, cache_on)
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
def knowledge_article(kid):
    d = _get_json(VIP + "/knowledgeapi/knowledge/" + str(kid))
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

def question_detail(qid, with_answers=True, max_answer_pages=3, max_detail=5):
    d = _get_json(VIP + "/api/questions/" + str(qid))
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
        answers, page = [], 1
        while page <= max_answer_pages:
            ad = _get_json(VIP + "/api/questions/%s/answers?page=%d&pageSize=20" % (qid, page))
            for a in ad.get("content") or []:
                answers.append(_answer_brief(a))
            if page >= (ad.get("totalPages") or 1):
                break
            page += 1
        answers.sort(key=lambda a: (not a["adopted"]))
        for a in answers[:max(max_detail, 0)]:
            try:
                det = _answer_brief(_get_json(VIP + "/api/answers/" + a["id"]))
                if len(det.get("contentText") or "") > len(a.get("contentText") or ""):
                    a["contentText"] = det["contentText"]
                if det.get("discussion"):
                    a["discussion"] = det["discussion"]
            except Exception:
                pass
        out["answers"] = answers
    return out

def answer_detail(aid):
    d = _get_json(VIP + "/api/answers/" + str(aid))
    q = d.get("question") or {}
    qid = str(d.get("questionId") or q.get("id") or "")
    return {"ok": True, "id": str(aid), "questionId": qid,
            "title": q.get("title"), "contentText": html2text(d.get("description")),
            "adopted": _is_true(d.get("isAdopt")), "usefuls": d.get("usefuls"),
            "url": f"{VIP}/question/{qid}" if qid else None,
            "updatedAt": d.get("updatedAt")}

def article_detail(aid):
    d = _get_json(VIP + "/api/articles/" + str(aid))
    classes = [c.get("name") for c in (d.get("classifies") or []) if c.get("name")]
    return {"ok": True, "id": str(aid), "type": "article", "title": d.get("title"),
            "contentText": html2text(d.get("content")),
            "url": f"{VIP}/article/{aid}",
            "products": classes[:3], "supports": d.get("supports"), "views": d.get("views"),
            "updatedAt": d.get("updatedAt")}

_DETAIL_FN = {"knowledge": knowledge_article, "answer": question_detail,
              "article": article_detail, "answer_detail": answer_detail}

def detail_cached(kind, oid, cache_on=False, refresh=False, stats=None):
    """详情统一入口:cache_on 时优先本地(明细永久),refresh=1 强制回源;corpus 写穿与缓存开关无关(常开)。"""
    key = "%s:%s" % (kind, oid)
    if cache_on and not refresh:
        hit = _cache_get("detail_cache", key)
        if hit:
            if stats is not None:
                stats["cacheHits"] = stats.get("cacheHits", 0) + 1
            d = json.loads(hit)
            d["fromCache"] = True
            _corpus_sync(kind, d)
            return d
    d = _DETAIL_FN[kind](oid)
    if cache_on:
        _cache_put("detail_cache", key, d)
        _store_chunks(key, d.get("contentText") or "")
    _corpus_sync(kind, d)
    return d

# ---------- /ask 一站式问答包(多路检索 + 并行深读 + chunk) ----------
def _fetch_for_item(item, cache_on, refresh):
    try:
        if item["type"] == "knowledge":
            d = detail_cached("knowledge", item["id"], cache_on, refresh)
        elif item["type"] == "answer":
            d = detail_cached("answer", item.get("questionId") or item["id"], cache_on, refresh)
        elif item["type"] == "article":
            d = detail_cached("article", item["id"], cache_on, refresh)
        else:
            d = None
    except Exception as e:
        d = {"ok": False, "error": str(e)[:150]}
    return d

def ask_bundle(text=None, keywords=None, product_id=None, top_k=4,
               rerank=False, cache_on=False, refresh=False):
    top_k = max(1, min(int(top_k or 4), 8))
    base = [str(k).strip() for k in keywords if str(k).strip()][:4] if keywords else ([text] if text else [])
    queries = list(base)
    # 多路检索:每路走 v4 管线(内部 RRF+重排),路间再 RRF 融合
    lists, total = [], 0
    for q in queries:
        try:
            r = knowledge_search(q, product_id=product_id, page=1, page_size=15,
                                 rerank=rerank, cache_on=cache_on)
        except Exception:
            continue
        total = max(total, r.get("total") or 0)
        lists.append(r["results"])
        time.sleep(0.05)
    scores, items = _rrf_fuse(lists)
    ranked = sorted(items.values(), key=lambda x: -scores[
        "answer:" + str(x.get("questionId")) if x.get("type") == "answer" and x.get("questionId")
        else str(x.get("type", "?")) + ":" + str(x.get("id"))])
    picked, seen, sources = 0, set(), []
    sel = []
    for item in ranked:
        if picked >= top_k:
            break
        key = item.get("questionId") if item["type"] == "answer" else item.get("id")
        if not key or key in seen:
            continue
        seen.add(key)
        sel.append(item)
        picked += 1
    # 并行深读(≤4 并发;缓存命中零开销)
    results_map = {}
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = [(ex.submit(_fetch_for_item, it, cache_on, refresh), it) for it in sel]
        for f, it in futs:
            results_map[id(it)] = f.result()
    terms = _terms(" ".join(queries))
    for item in sel:
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
                        "detail": d})
    return {"ok": True, "text": text or " / ".join(base), "total": total, "queries": queries,
            "sources": sources, "_cacheHits": sum(1 for s in sources if s.get("fromCache")),
            "note": "sources[].detail 已含全文(回答采纳项排前);knowledge/article 附 chunks(标题感知切片,"
                    "top3 相关段);调用方 AI 据此合成带引用回答,可引用 [chunk#seq]"}

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
        "service": "kingdee-ksearch", "version": "5.0", "anonymous": True,
        "description": "金蝶官方知识库匿名检索/全文/问答包(逆向官方社区后端,零账号零点数);v5:corpus 语料目录(rg 直接检索,read/ask 写穿全文+发现层 stub)+检索管线;本地 sqlite 缩编为纯上游缓存(FTS5/chunks/local 冻结开发,ADR-0004)",
        "corpus": {"dir": CORPUS_DIR, "write": "read/ask 深读同步写穿全文;POST /corpus 摄入发现层 stub(全量快照/时间网格);share 引用自动落盘",
                   "search": "agent 用 rg 在 corpus 目录直接检索(rg 优先,零上游);SKILL.md 有检索策略",
                   "discovered_by": ["usage", "timesweep", "share", "fullscan"]},
        "pipeline": {"params": {"rerank": "信号重排(opt-in 实验,评测:recall@10 -11%),默认关",
                                 "cache": "本地sqlite上游缓存,默认关(KSEARCH_INDEX=on 开);v5 缩编为纯缓存,不再扩展",
                                 "refresh": "强制回源,默认关", "local": "只查本地语料(FTS5)【deprecated:冻结开发,检索角色由 corpus+rg 接管】"},
                      "env": {"KSEARCH_RERANK": "默认0(实验)", "KSEARCH_INDEX": "默认0",
                              "KSEARCH_DB": "默认 <根>/data/ksearch.db", "KSEARCH_CORPUS": "默认 <根>/corpus"},
                      "data": ["<corpus>/ 语料目录(一文档一 md,rg 可搜)",
                               "data/ksearch.db 上游缓存(纯缓存,永久)",
                               "data/eval/evalset.json 评测集", "scripts/run_eval.py A/B评测",
                               "scripts/discovery_sweep.py 时间网格发现(手动触发)"]},
        "cli": {"path": os.path.join(_ROOT, "bin", "kd.cmd" if os.name == "nt" else "kd"),
                "commands": ["kd search \"<关键词>\" [--product 93] [--type knowledge|answer|article] [--size 10]",
                             "kd read <id> [--kind knowledge|answer|article]  # kind 照抄 search 结果的 type",
                             "kd ask \"<问题>\" [--topk 4] / --kw \"词1\" --kw \"词2\"",
                             "kd ai \"<问题>\" [--topk 4]  # 需模型通道 KAI_BASE/KAI_MODEL,不可用自动降级资料包",
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
            "GET|POST /ask": {"params": {"text": "问题(与 keywords 二选一)", "keywords": "关键词数组(多词+同义词变体,上限6路)",
                                         "productId": "int", "topK": "1-8,默认4", "pipeline": "可选覆盖"},
                              "returns": "sources[] 深读全文资料包(knowledge/article 附 top3 相关 chunk),调用方 AI 合成带引用回答"},
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
        log(name.upper() + ":", i, "| len", len(res.get("contentText") or ""), "| cached", bool(res.get("fromCache")))
        return self._reply(200, self._stats_wrap(res, n0, t0, p))

    def _ask(self, qs, body=None):
        body = body or {}
        text = (qs.get("text") or [""])[0].strip() or body.get("text") or ""
        keywords = body.get("keywords")
        if not text and not keywords:
            return self._reply(400, {"error": "text or keywords required",
                                     "example": "/ask?text=信用额度控制&topK=4 或 POST {\"keywords\":[\"信用额度\",\"应收单 信用\"],\"topK\":4}"})
        p = self._pipe(qs, body)
        n0, t0 = _up_now(), time.time()
        res = ask_bundle(text=text or None, keywords=keywords,
                         product_id=(qs.get("productId") or [None])[0] or body.get("productId"),
                         top_k=(qs.get("topK") or ["4"])[0],
                         rerank=p["rerank"], cache_on=p["cache"], refresh=p["refresh"])
        log("ASK:", (text or "kw×%d" % len(keywords))[:50], "| total", res["total"], "| sources", len(res["sources"]),
            "| queries", len(res.get("queries") or []))
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
            return self._reply(200, {"service": "kingdee-ksearch v5.0", "anonymous": True,
                                     "pipeline": {"rerank": RERANK_DEFAULT, "cache": INDEX_DEFAULT},
                                     "db": db_stats(), "corpus": corpus_stats(),
                                     "endpoints": ["/manifest", "/search", "/karticle", "/question", "/answer", "/article", "/ask", "/share", "/corpus", "/health"],
                                     "note": "v5:corpus 语料目录(read/ask 写穿+发现层 stub,rg 直接检索);sqlite 缩编为纯上游缓存(FTS5/local 冻结开发);官方 ai-search 管线需登录,不用"})
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
    log(f"kingdee-ksearch v5.0 listening on {PORT} (rerank={RERANK_DEFAULT} cache={INDEX_DEFAULT} corpus={CORPUS_DIR})")
    print(f"kingdee-ksearch v5.0 on :{PORT} (pipeline rerank={RERANK_DEFAULT} cache={INDEX_DEFAULT} corpus={CORPUS_DIR})")
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
