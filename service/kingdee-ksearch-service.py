#!/usr/bin/env python3
"""金蝶知识检索服务 v3.2 —— 纯匿名全类型检索 + 全文闭环 + 问答包 + 自描述清单
逆向自金蝶云社区官方后端(2026-09-05,详见交接文档 11/12/13/14),零账号/零点数/零凭据/零外部依赖。
匿名接口铁证(均实测 HTTP 200,无 cookie):
  检索    GET https://vip.kingdee.com/api/search?text=&page=&pageSize=&global=&sortsType=&productIds[0]=
          → content[] 混合三种实体:Knowledge / Answer(问答) / Article
  知识全文 GET https://vip.kingdee.com/knowledgeapi/knowledge/{knowledgeId}
  问题详情 GET https://vip.kingdee.com/api/questions/{questionId}
  全部回答 GET https://vip.kingdee.com/api/questions/{questionId}/answers?page=&pageSize=
  回答全文 GET https://vip.kingdee.com/api/answers/{answerId}
  文章全文 GET https://vip.kingdee.com/api/articles/{articleId}
  分享对话 GET https://vip.kingdee.com/aisapi/ai-search/sharing-chats/{chatId}
          (chatId 来自分享短链落地页 /searchchats/{chatId}?sharingId=..;匿名可读)
注意:网页 /question/{id} 等有登录门(302),但底层 API 不校验——只走 API。
★ 不含任何 RAG/AI 问答/cookie/浏览器逻辑(官方 ai-search 管线匿名实测 answerType:3 未授权,需登录+身份认证,不用;
  旧 RAG 层与 MCP 适配器均已退役,不在本仓库)

端点(POST/GET 双形态):
  /          或 /manifest → 机器可读能力清单(端点/参数/实体/CLI 路径/版本),agent 自发现入口
  /search    {"text","productId":93,"page":1,"pageSize":10,"global":false,"sortsType":1,"type":"answer|knowledge|article"}
             → 三种实体全返回,type 字段区分;Answer 条目内联 questionId/questionTitle/questionBody/adopted
  /karticle  {"id":"<knowledgeId>"}          → 知识库全文
  /question  {"id":"<questionId>"}           → 问题详情 + 全部回答(含正文/采纳/作者/追问链)
  /answer    {"id":"<answerId>"}             → 单条回答全文
  /article   {"id":"<articleId>"}            → 社区文章全文
  /ask       {"text"} 或 {"keywords":[..]} + {"productId"?,"topK"=4}
             → 一站式问答包:检索(keywords 模式多词合并去重)+按序深读 topK 全文,
               调用方 AI 拿包即合成带引用回答(官方问答效果的无登录等价)
  /health
"""
import json, os, re, sys, time, urllib.request, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

VIP = "https://vip.kingdee.com"
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 4097
LOG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs", "ksearch-service.log")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/152.0.0.0"
HDRS = {"User-Agent": UA, "Accept": "application/json"}

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

def _get_json(url):
    req = urllib.request.Request(url, headers=HDRS)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8", "replace"))

def _is_true(v):
    return str(v or "").lower() == "true"

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

def knowledge_search(text, product_id=None, page=1, page_size=10, global_=False, sorts_type=1, type_=None, max_scan_pages=5):
    def fetch(pg):
        params = {"text": text, "page": pg, "pageSize": page_size,
                  "global": "true" if global_ else "false", "sortsType": sorts_type}
        if product_id and int(product_id) != 0:  # 0=不过滤:必须省略参数,传 0 上游会当真值过滤(实测把 Knowledge 挤出前排)
            params["productIds[0]"] = int(product_id)
        return _get_json(VIP + "/api/search?" + urllib.parse.urlencode(params))

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
    if type_:  # 过滤模式下上游按相关度混排,目标类型可能排在深页 → 从第 1 页起跨页扫描凑满
        d = fetch(1)
        total, total_pages = d.get("totalElements"), d.get("totalPages")
        collect(d)
        pg = 2
        while len(items) < page * page_size and pg <= max_scan_pages and pg <= (total_pages or 1):
            collect(fetch(pg))
            pg += 1
        items = items[(page - 1) * page_size: page * page_size]
        scan_note = f"type={type_} 过滤:跨上游 {pg - 1} 页扫描(混排结果按相关度抽取该类型)"
    else:
        d = fetch(page)
        total, total_pages = d.get("totalElements"), d.get("totalPages")
        collect(d)
    return {"ok": True, "text": text, "total": total,
            "page": page, "pageSize": page_size, "totalPages": total_pages,
            "results": items, "scanNote": scan_note}

# ---------- 详情:知识 / 问答 / 文章 ----------
def knowledge_article(kid):
    d = _get_json(VIP + "/knowledgeapi/knowledge/" + str(kid))
    return {"ok": True, "id": str(kid), "title": d.get("title"),
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
    out = {"ok": True, "id": str(qid), "title": d.get("title"),
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
        # 列表条目正文是 summary(可能截断);前 max_detail 条拉详情补全(详情 description 更全且含追问链)
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
    return {"ok": True, "id": str(aid), "title": d.get("title"),
            "contentText": html2text(d.get("content")),
            "url": f"{VIP}/article/{aid}",
            "products": classes[:3], "supports": d.get("supports"), "views": d.get("views"),
            "updatedAt": d.get("updatedAt")}

# ---------- /ask 一站式问答包 ----------
def ask_bundle(text=None, keywords=None, product_id=None, top_k=4):
    top_k = max(1, min(int(top_k or 4), 8))
    if keywords:  # 多关键词模式:逐词检索合并去重(每词取前 8)
        merged, seen, total = [], set(), 0
        for kw in [str(k).strip() for k in keywords if str(k).strip()][:4]:
            try:
                r = knowledge_search(kw, product_id=product_id, page=1, page_size=8)
            except Exception:
                continue
            total = max(total, r.get("total") or 0)
            for item in r["results"]:
                key = (item["type"], item.get("questionId") if item["type"] == "answer" else item.get("id"))
                if key in seen:
                    continue
                seen.add(key)
                merged.append(item)
        res = {"total": total, "results": merged}
        text = text or " / ".join(str(k) for k in keywords[:4])
    else:
        res = knowledge_search(text, product_id=product_id, page=1, page_size=15)
    sources, seen, picked = [], set(), 0
    for item in res["results"]:
        if picked >= top_k:
            break
        key = item.get("questionId") if item["type"] == "answer" else item.get("id")
        if not key or key in seen:
            continue
        try:
            if item["type"] == "knowledge":
                detail = knowledge_article(item["id"])
            elif item["type"] == "answer":
                detail = question_detail(key, max_detail=0)  # /ask 轻量:回答正文用 summary,控制请求数
            elif item["type"] == "article":
                detail = article_detail(item["id"])
            else:
                continue
        except Exception as e:
            detail = {"ok": False, "error": str(e)[:150]}
        seen.add(key)
        picked += 1
        sources.append({"rank": picked, "type": item["type"], "id": item.get("id"),
                        "questionId": item.get("questionId"),
                        "title": item.get("title"), "url": item.get("url"),
                        "snippet": item.get("snippet"), "detail": detail})
    return {"ok": True, "text": text, "total": res["total"], "sources": sources,
            "note": "sources[].detail 已含全文(回答采纳项排前);调用方 AI 据此合成带引用回答"}

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
    for c in d.get("chats") or []:
        refs = [{"title": x.get("title"), "url": x.get("url"),
                 "summary": (x.get("summary") or "")[:200],
                 "entityType": x.get("entityType"), "entityId": x.get("entityId")}
                for x in (c.get("recallDocuments") or [])]
        chats.append({"question": c.get("searchText"), "answer": c.get("content"),
                      "answerType": c.get("answerType"), "refs": refs})
    return {"ok": True, "chatId": chat_id, "count": len(chats), "chats": chats}

# ---------- /manifest 机器可读能力清单 ----------
def _manifest():
    return {
        "service": "kingdee-ksearch", "version": "3.2", "anonymous": True,
        "description": "金蝶官方知识库匿名检索/全文/问答包(逆向官方社区后端,零账号零点数)",
        "cli": {"path": os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin",
                                     "kd.cmd" if os.name == "nt" else "kd"),
                "commands": ["kd search \"<关键词>\" [--product 93] [--type knowledge|answer|article] [--size 10]",
                             "kd question <questionId>", "kd answer <answerId>",
                             "kd article <id> [--kind article]", "kd ask \"<问题>\" [--topk 4]",
                             "kd share <分享短链|chatId>", "kd manifest", "kd health"]},
        "endpoints": {
            "GET|POST /search": {"params": {"text": "string,必填,关键词", "productId": "int,93=星空旗舰版/87=苍穹/1=企业版标准版/0=不过滤",
                                            "page": "int", "pageSize": "int<=50", "global": "bool",
                                            "sortsType": "int,1=综合", "type": "knowledge|answer|article 可选过滤"},
                                 "returns": "results[] 三种实体混合,type 字段区分;type 过滤时跨上游页扫描(scanNote)"},
            "GET|POST /karticle": {"params": {"id": "knowledge 条目的 id"}, "returns": "知识库文档全文 contentText"},
            "GET|POST /question": {"params": {"id": "answer 条目的 questionId"}, "returns": "问题正文+全部回答(采纳优先,前5条拉详情)+追问链 discussion"},
            "GET|POST /answer": {"params": {"id": "answer 条目的 id"}, "returns": "单条回答全文"},
            "GET|POST /article": {"params": {"id": "article 条目的 id"}, "returns": "社区文章全文"},
            "GET|POST /ask": {"params": {"text": "问题(与 keywords 二选一)", "keywords": "关键词数组(多词合并检索)",
                                         "productId": "int", "topK": "1-8,默认4"},
                              "returns": "sources[] 深读全文资料包,调用方 AI 合成带引用回答"},
            "GET|POST /share": {"params": {"link": "官方分享短链 /searchchats/{id} 页面链接或纯数字 chatId"},
                                "returns": "chats[] 官方 AI 分享对话全文(问题/Markdown 回答/引用 recallDocuments)"},
            "GET /manifest": {"returns": "本清单"},
            "GET /health": {"returns": "存活与版本"},
        },
        "entities": {"knowledge": {"read": "/karticle", "note": "官方文档,权威优先"},
                     "answer": {"read": "/question?id=<questionId>", "note": "社区问答帖,条目内联 questionId/questionBody/adopted;网页有登录门但 API 匿名"},
                     "article": {"read": "/article", "note": "社区文章"}},
        "notes": ["纯匿名:零账号/零点数/无 LLM 生成", "保持人类调用频率", "上游接口铁证见交接文档 13/14"],
    }

class H(BaseHTTPRequestHandler):
    def _reply(self, code, obj):
        b = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _search(self, qs):
        text = (qs.get("text") or [""])[0].strip()
        if not text:
            return self._reply(400, {"error": "text required. 例: /search?text=信用额度&productId=93"})
        res = knowledge_search(text,
                               product_id=(qs.get("productId") or [None])[0],
                               page=int((qs.get("page") or ["1"])[0]),
                               page_size=int((qs.get("pageSize") or ["10"])[0]),
                               global_=((qs.get("global") or ["false"])[0] == "true"),
                               sorts_type=int((qs.get("sortsType") or ["1"])[0]),
                               type_=(qs.get("type") or [None])[0])
        log("SEARCH:", text[:50], "| total", res["total"], "| returned", len(res["results"]))
        return self._reply(200, res)

    def _by_id(self, qs, fn, name):
        i = (qs.get("id") or [""])[0].strip()
        if not i:
            return self._reply(400, {"error": "id required. 例: /%s?id=<id>" % name})
        res = fn(i)
        log(name.upper() + ":", i, "| len", len(res.get("contentText") or ""))
        return self._reply(200, res)

    def _ask(self, qs, body=None):
        text = (qs.get("text") or [""])[0].strip() or (body or {}).get("text") or ""
        keywords = (body or {}).get("keywords")
        if not text and not keywords:
            return self._reply(400, {"error": "text or keywords required",
                                     "example": "/ask?text=信用额度控制&topK=4 或 POST {\"keywords\":[\"信用额度\",\"应收单 信用\"],\"topK\":4}"})
        res = ask_bundle(text=text or None, keywords=keywords,
                         product_id=(qs.get("productId") or [None])[0],
                         top_k=(qs.get("topK") or ["4"])[0])
        log("ASK:", (text or "kw×%d" % len(keywords))[:50], "| total", res["total"], "| sources", len(res["sources"]))
        return self._reply(200, res)

    def _share(self, qs, body=None):
        link = (qs.get("link") or [""])[0].strip() or (body or {}).get("link") or ""
        if not link:
            return self._reply(400, {"error": "link required", "example": "POST {\"link\":\"https://vip.kingdee.com/link/s/xxxx\"}"})
        res = share_read(link)
        log("SHARE:", link[-40:], "| chats", res.get("count"))
        return self._reply(200, res)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(u.query)
        if u.path in ("/", "/manifest"):
            return self._reply(200, _manifest())
        if u.path == "/health":
            return self._reply(200, {"service": "kingdee-ksearch v3.2", "anonymous": True,
                                     "endpoints": ["/manifest", "/search", "/karticle", "/question", "/answer", "/article", "/ask", "/share", "/health"],
                                     "note": "全类型检索+全文闭环+自描述清单;官方 ai-search 管线需登录+身份认证,不用(文档 13/14)"})
        if u.path == "/search":
            return self._search(qs)
        if u.path == "/karticle":
            return self._by_id(qs, knowledge_article, "karticle")
        if u.path == "/question":
            return self._by_id(qs, question_detail, "question")
        if u.path == "/answer":
            return self._by_id(qs, answer_detail, "answer")
        if u.path == "/article":
            return self._by_id(qs, article_detail, "article")
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
        qs = {k: [str(v)] for k, v in body.items() if v is not None}
        if self.path == "/search":
            return self._search(qs)
        if self.path == "/karticle":
            return self._by_id(qs, knowledge_article, "karticle")
        if self.path == "/question":
            return self._by_id(qs, question_detail, "question")
        if self.path == "/answer":
            return self._by_id(qs, answer_detail, "answer")
        if self.path == "/article":
            return self._by_id(qs, article_detail, "article")
        if self.path == "/ask":
            return self._ask(qs, body=body)
        if self.path == "/share":
            return self._share(qs, body=body)
        return self._reply(404, {"error": "POST /search | /karticle | /question | /answer | /article | /ask"})

    def log_message(self, *a):
        pass

if __name__ == "__main__":
    log(f"kingdee-ksearch v3.2 listening on {PORT} (anonymous-only, all entity types, self-describing)")
    print(f"kingdee-ksearch v3.2 on :{PORT} (anonymous-only, all entity types, self-describing)")
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
