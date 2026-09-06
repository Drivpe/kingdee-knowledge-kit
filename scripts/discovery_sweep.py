#!/usr/bin/env python3
"""discovery_sweep.py — 时间网格发现(交接文档 17 §3.3,手动触发,无常驻/无定时)
词表 data/discovery-terms.json × sortsType=2(时间倒序)逐词扫 → 命中条目 POST /corpus 落 stub
(标题+摘要+url,discovered_by=timesweep;正文按需 kd read 写穿覆盖)。

红线:1 请求/秒;单轮请求计数上限默认 200(词数×页数超限时按序截断);纯追加写,失败重跑即可。
正式检索入口仍是上游关键词搜索;本脚本只补发现层(索引外新文档/更新文档的落盘)。

用法:
  python scripts/discovery_sweep.py                     # 全词表,每词前 3 页(pageSize=25)
  python scripts/discovery_sweep.py --pages 2 --groups usage,domain
  python scripts/discovery_sweep.py --dry-run           # 只打印将发的请求,不打上游
退出码 0=完成。请求明细走 stderr,汇总 JSON 走 stdout。
"""
import argparse, json, os, sys, time, urllib.parse, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = os.environ.get("KSEARCH_URL", "http://127.0.0.1:4097")

def http(path, body=None, timeout=60):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(BASE + path, data=data,
                                 headers={"Content-Type": "application/json"} if data else {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))

def main():
    ap = argparse.ArgumentParser(description="时间网格发现:词表×sortsType=2 → corpus stub(手动触发)")
    ap.add_argument("--terms", default=os.path.join(ROOT, "data", "discovery-terms.json"))
    ap.add_argument("--groups", default="usage,eval,domain", help="逗号分隔的词表组")
    ap.add_argument("--pages", type=int, default=3, help="每词扫前 N 页(默认 3,pageSize=25)")
    ap.add_argument("--size", type=int, default=25)
    ap.add_argument("--sleep", type=float, default=1.0, help="检索请求间隔秒(默认 1,人类频率红线)")
    ap.add_argument("--max-requests", type=int, default=200, help="单轮上游请求上限(默认 200)")
    ap.add_argument("--product", type=int, default=0, help="0=不过滤(默认);93=星空旗舰版 等")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    tl = json.load(open(a.terms, encoding="utf-8"))
    terms = []
    for g in [g.strip() for g in a.groups.split(",") if g.strip()]:
        for t in tl.get("groups", {}).get(g) or []:
            if t not in terms:
                terms.append(t)
    if not terms:
        sys.exit("词表为空: %s groups=%s" % (a.terms, a.groups))

    plan = terms[:max(0, a.max_requests // max(1, a.pages))]  # 每词固定 pages 页,超上限的词截断
    print("词表 %d 组 %d 词 × %d 页;本轮上限 %d 请求(%d 词)%s" %
          (len(tl.get("groups") or {}), len(terms), a.pages, a.max_requests, len(plan),
           "(dry-run)" if a.dry_run else ""), file=sys.stderr)

    req_n, totals, stubs_all = 0, {}, 0
    t0 = time.time()
    for term in plan:
        items = []
        for page in range(1, a.pages + 1):
            if req_n >= a.max_requests:
                break
            if a.dry_run:
                print("DRY /search text=%r sortsType=2 page=%d" % (term, page), file=sys.stderr)
                req_n += 1
                continue
            qs = "text=%s&page=%d&pageSize=%d&global=false&sortsType=2&cache=0" % (
                urllib.parse.quote(term), page, a.size)
            if a.product:
                qs += "&productId=%d" % a.product
            try:
                d = http("/search?" + qs)
            except Exception as e:
                print("!! search fail %r p%d: %s" % (term, page, str(e)[:100]), file=sys.stderr)
                break
            req_n += 1
            results = d.get("results") or []
            items += results
            total_pages = d.get("totalPages") or 1
            if page >= total_pages:
                break
            time.sleep(a.sleep)
        if items:
            r = http("/corpus", {"items": [{"type": x.get("type"),
                                            "id": x.get("id"), "questionId": x.get("questionId"),
                                            "title": x.get("title"), "snippet": x.get("snippet"),
                                            "url": x.get("url"), "updatedAt": x.get("updatedAt")}
                                           for x in items],
                                "discoveredBy": "timesweep"})
            stubs_all += r.get("written", 0)
            totals[term] = {"hits": len(items), "written": r.get("written", 0),
                            "unchanged": r.get("unchanged", 0)}
            print("== %r hits=%d written=%d unchanged=%d (req=%d)" %
                  (term, len(items), r.get("written", 0), r.get("unchanged", 0), req_n), file=sys.stderr)
        if req_n >= a.max_requests:
            print("!! 达到单轮请求上限 %d,提前收工" % a.max_requests, file=sys.stderr)
            break
        time.sleep(a.sleep)
    out = {"ok": True, "termsPlanned": len(plan), "termsHit": len(totals),
           "upstreamRequests": req_n, "stubsWritten": stubs_all,
           "elapsedSec": round(time.time() - t0, 1), "dryRun": a.dry_run, "perTerm": totals}
    print(json.dumps(out, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
