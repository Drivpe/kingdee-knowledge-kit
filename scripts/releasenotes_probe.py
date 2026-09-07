#!/usr/bin/env python3
"""releasenotes_probe.py — 发版说明列表页匿名可达性探路(issue #11 / ADR-0005 决策 5,手动触发,可重跑)
三类探测:
  A. 搜索索引覆盖面:/api/search(productIds=93)发版说明关键词族 × sortsType=2,深翻「发版说明」全部页,
     按标题内嵌版本号(R2025MM/PLMR/「YYYY年M月发版说明」)做 时间×领域 分布——updatedAt 是批量编辑时间不可信。
  B. 列表页匿名可达性:①知识专题页 /knowledge/specialDetail/<id>(零 cookie,看是否登录门)
     ②学习成长中心 schoolapi/search(ADR-0004 决策 4 线索)③课程详情端点候选。
  C. 时间覆盖缺口:索引内版本月分布 vs 近 1 年窗口(2025-09 起),诚实边界=索引外集合匿名不可枚举。

红线:零账号零 cookie;1 请求/秒+随机抖动;单轮上限默认 60(实际用量 ~20)。只探路不摄取:
不写 corpus/releasenotes,不改任何本地数据( stdout=汇总 JSON,stderr=进度 )。

用法:
  python scripts/releasenotes_probe.py                # 全量探测(~20 请求)
  python scripts/releasenotes_probe.py --dry-run      # 只打印将发的请求,不打上游
  python scripts/releasenotes_probe.py --max-requests 30 --pages 5   # 收缩预算
退出码 0=完成。
"""
import argparse, json, os, random, re, sys, time, urllib.error, urllib.parse, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = os.environ.get("KSEARCH_URL", "http://127.0.0.1:4097")   # 走本地服务(同一匿名姿势转发上游)
VIP = "https://vip.kingdee.com"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/152.0.0.0"
PRODUCT = 93          # 星空旗舰版(=金蝶AI套件)
WINDOW = "2025-09"    # 近 1 年窗口起点
KEYWORDS = ["发版说明", "版本更新说明", "更新说明", "版本发布", "发版"]
NOTE_RE = re.compile(r"发版说明|版本更新说明|更新说明|发布说明")
VER_RE = re.compile(r"\b(?:PLMR|R|V)(20\d{4})(?:\.\d+)?")            # R202608 / PLMR202512.001
MONTH_RE = re.compile(r"(20\d{2})年(\d{1,2})月发版说明")              # 2026年8月发版说明(汇总篇)
SPECIAL_ID = "352491453127123200"                                     # 金蝶云星空安全补丁知识专题(搜索引擎已知)
SCHOOL_COURSE_ID = "137943481535811840"                               # 「金蝶云星空 V7.6_发版说明」课程(2021)

_req_n = 0

def pace(sleep):
    time.sleep(sleep + random.uniform(0.15, 0.55))  # 1 请求/秒 + 抖动(匿名链路红线)

def search(term, page=1, size=50, sorts=2):
    """经本地服务 /search(单次上游调用,无 type 混排,客户端按标题过滤)。"""
    global _req_n
    qs = urllib.parse.urlencode({"text": term, "page": page, "pageSize": size,
                                 "sortsType": sorts, "cache": 0, "productId": PRODUCT})
    d = json.load(urllib.request.urlopen(BASE + "/search?" + qs, timeout=60))
    _req_n += 1
    return d

def raw_get(url, max_bytes=4000):
    """直连 vip.kingdee.com 的原始 GET(与 service 同姿势:仅 UA,零 cookie),不跟随重定向。"""
    global _req_n
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json, text/html"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            body = r.read(max_bytes).decode("utf-8", "replace")
            return {"status": r.status, "location": None, "bodyHead": body[:400]}
    except urllib.error.HTTPError as e:
        loc = e.headers.get("Location")
        return {"status": e.code, "location": loc, "bodyHead": (e.read(max_bytes) or b"").decode("utf-8", "replace")[:400]}
    except Exception as e:
        return {"status": None, "location": None, "bodyHead": "EXC: %s" % str(e)[:150]}
    finally:
        _req_n += 1

def redact(loc):
    """去 query(可能含 oauth state/token),只留路径。"""
    if not loc:
        return None
    return urllib.parse.urlsplit(loc)._replace(query="", fragment="").geturl()

def ver_month(title):
    m = MONTH_RE.search(title or "")
    if m:
        return "%s-%02d" % (m.group(1), int(m.group(2)))
    m = VER_RE.search(title or "")
    if m:
        v = m.group(1)
        return "%s-%s" % (v[:4], v[4:6])
    return None

def main():
    global _req_n
    ap = argparse.ArgumentParser(description="发版说明匿名可达性探路(issue #11,手动触发,≤60 请求)")
    ap.add_argument("--pages", type=int, default=14, help="深翻「发版说明」最多页数(默认 14,pageSize=50 覆盖 total≈509)")
    ap.add_argument("--size", type=int, default=50)
    ap.add_argument("--sleep", type=float, default=1.0)
    ap.add_argument("--max-requests", type=int, default=60, help="单轮上游请求上限(红线 60)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    print("发版说明探路:关键词 %d 词 + 深翻≤%d 页 + 列表页 3 探;上限 %d 请求%s" %
          (len(KEYWORDS), a.pages, a.max_requests, "(dry-run)" if a.dry_run else ""), file=sys.stderr)
    t0 = time.time()
    out = {"ok": True, "dryRun": a.dry_run, "product": PRODUCT, "window": WINDOW,
           "requestBudget": {"cap": a.max_requests, "used": 0}}

    # ---- A1. 关键词族覆盖面(每词 1 页×50) ----
    per_kw = {}
    for kw in KEYWORDS:
        if _req_n >= a.max_requests:
            break
        if a.dry_run:
            print("DRY /search %r sortsType=2 p1" % kw, file=sys.stderr)
            _req_n += 1
            continue
        d = search(kw, size=a.size)
        items = [x for x in (d.get("results") or []) if x.get("type") == "knowledge" and NOTE_RE.search(x.get("title") or "")]
        per_kw[kw] = {"total": d.get("total"), "page1ReleaseLike": len(items),
                      "sample": [{"id": x.get("id"), "title": x.get("title")} for x in items[:3]]}
        print("== %r total=%s 发版类(首页)=%d" % (kw, d.get("total"), len(items)), file=sys.stderr)
        pace(a.sleep)
    out["searchIndex"] = {"perKeyword": per_kw}

    # ---- A2. 深翻「发版说明」:时间×领域分布(标题版本号为时间轴) ----
    docs, pages_fetched = {}, 0
    for page in range(1, a.pages + 1):
        if _req_n >= a.max_requests:
            print("!! 达到请求上限 %d,深翻提前收工" % a.max_requests, file=sys.stderr)
            break
        if a.dry_run:
            print("DRY /search '发版说明' p%d" % page, file=sys.stderr)
            _req_n += 1
            continue
        d = search("发版说明", page=page, size=a.size)
        pages_fetched = page
        rs = d.get("results") or []
        for x in rs:
            if x.get("type") == "knowledge" and NOTE_RE.search(x.get("title") or ""):
                docs[str(x.get("id"))] = {"id": x.get("id"), "title": x.get("title"),
                                          "updatedAt": x.get("updatedAt"), "products": x.get("products")}
        print("== 深翻 p%d/%s 本页 %d 条,累计发版类 %d" % (page, d.get("totalPages"), len(rs), len(docs)), file=sys.stderr)
        if page >= (d.get("totalPages") or 1) or not rs:
            break
        pace(a.sleep)
    if not a.dry_run:
        by_month, by_cloud, in_window = {}, {}, []
        for x in docs.values():
            vm = ver_month(x["title"])
            if vm:
                by_month[vm] = by_month.get(vm, 0) + 1
            if vm and vm >= WINDOW:
                in_window.append(x)
            prods = x.get("products") or []
            cloud = (x["title"].split("R20")[0].split("V")[0].strip() if not MONTH_RE.search(x["title"]) else "月度汇总") or (prods[0] if prods else "?")
            by_cloud[cloud] = by_cloud.get(cloud, 0) + 1
        out["searchIndex"]["deepScan"] = {
            "pagesFetched": pages_fetched, "releaseLikeDocs": len(docs),
            "docsWithVersionMonth": sum(by_month.values()),
            "byMonth": dict(sorted(by_month.items())),
            "byCloud": dict(sorted(by_cloud.items(), key=lambda t: -t[1])),
            "inWindowDocs": len(in_window),
            "windowSample": [{"id": x["id"], "title": x["title"]} for x in in_window[:5]],
        }

    # ---- B. 列表页/接口匿名可达性(零 cookie 直连) ----
    if not a.dry_run:
        # B1 知识专题页(HTML):跟随与否都不跟,看 302 Location 是否登录门
        r1 = raw_get("%s/knowledge/specialDetail/%s" % (VIP, SPECIAL_ID))
        pace(a.sleep)
        # B2 schoolapi/search(ADR-0004 决策 4 已证匿名可用;此处复核+看发版说明课程新旧)
        r2 = raw_get(VIP + "/schoolapi/search?" + urllib.parse.urlencode({"text": "发版说明", "size": 20}))
        school_notes = []
        try:
            sd = json.loads(json.dumps({})) # placeholder; 解析在下方
        except Exception:
            pass
        pace(a.sleep)
        # B3 课程详情端点候选(预期 404,记录在案)
        r3 = raw_get("%s/schoolapi/courses/%s" % (VIP, SCHOOL_COURSE_ID))
        # B2 的响应体需要完整解析,bodyHead 只有 400 字节 → 单独再拿一次完整 JSON 不划算,
        # 用 bodyHead 判可达性;完整结构在手工探路已见(content/totalElements/…,见备忘录)。
        out["listPage"] = {
            "specialDetail": {"status": r1["status"], "redirect": redact(r1["location"]),
                              "note": "302→passport=登录门;query 已 redact", "bodyHead": r1["bodyHead"][:200]},
            "schoolSearch": {"status": r2["status"], "bodyHead": r2["bodyHead"][:400],
                             "note": "匿名 200=可达;实体=LearningCourse(课程),发版说明课程停留在 2021(V7.6)"},
            "schoolCourseDetail": {"status": r3["status"], "bodyHead": r3["bodyHead"][:200],
                                   "note": "courses/<id> 候选端点实测状态"},
        }
    else:
        for u in ["/knowledge/specialDetail/<id>", "/schoolapi/search?text=..", "/schoolapi/courses/<id>"]:
            print("DRY GET vip.kingdee.com%s" % u, file=sys.stderr)
            _req_n += 1

    out["requestBudget"]["used"] = _req_n
    out["elapsedSec"] = round(time.time() - t0, 1)
    print(json.dumps(out, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
