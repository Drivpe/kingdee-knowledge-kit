#!/usr/bin/env python3
"""corpus_fullscan.py — corpus 全量快照(交接文档 17 执行记录 / issue #1,一次性,手动触发)
范围:星空旗舰版(93)+ 苍穹(87),global=false + productIds 过滤(实测 global=true 时过滤被上游忽略)。
方式:宽词表 × sortsType=2 时间倒序 × pageSize=50,逐页抓到「整页无新 ID」或 50 页上限,POST /corpus 落 stub。
去重:内存 id 集 + corpus updatedAt 幂等(重跑=unchanged);进度文件支持断点续跑。

红线(2026-09-06 用户豁免一次):1 请求/秒,单轮上限默认 7500;下次全量刷新需重新拍板。
日常增量仍走 discovery_sweep.py(≤200 请求/轮)。

用法:
  python scripts/corpus_fullscan.py                        # 全词表,断点续跑
  python scripts/corpus_fullscan.py --max-requests 50 --dry-run
退出码 0=完成。进度/汇总走 stderr,最终 JSON 汇总走 stdout。
"""
import argparse, json, os, sys, time, urllib.parse, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = os.environ.get("KSEARCH_URL", "http://127.0.0.1:4097")
PROGRESS = os.path.join(ROOT, "data", "fullscan-progress.json")
PRODUCTS = ["93", "87"]  # 星空旗舰版(=AI星空) + 苍穹

def http(path, body=None, timeout=60):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(BASE + path, data=data,
                                 headers={"Content-Type": "application/json"} if data else {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))

def load_progress(path):
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception:
        return {"done": {}, "seen": []}

def save_progress(path, p):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(p, f, ensure_ascii=False)

def main():
    ap = argparse.ArgumentParser(description="corpus 全量快照:93+87 × 宽词表 × 时间倒序(一次性,手动触发)")
    ap.add_argument("--terms", default=os.path.join(ROOT, "data", "fullscan-terms.json"))
    ap.add_argument("--pages", type=int, default=50, help="每词最多页数(默认 50=上游钳制上限)")
    ap.add_argument("--size", type=int, default=50)
    ap.add_argument("--sleep", type=float, default=1.0, help="请求间隔秒(红线豁免值:1)")
    ap.add_argument("--max-requests", type=int, default=7500, help="单轮请求上限(豁免记录:7500)")
    ap.add_argument("--progress", default=PROGRESS, help="断点进度文件(verify 用临时文件隔离)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    terms = json.load(open(a.terms, encoding="utf-8"))
    terms = terms.get("terms") if isinstance(terms, dict) else terms
    terms = [t for t in terms if t and t.strip()]
    if not terms:
        sys.exit("词表为空: %s" % a.terms)

    prog = load_progress(a.progress)
    seen = {(t, i) for t, i in prog.get("seen") or []}  # JSON 往返把元组变列表,这里转回元组
    done = prog.get("done") or {}
    print("词表 %d 词 | 已见 %d 篇 | 断点 %d 词 | 上限 %d 请求%s" %
          (len(terms), len(seen), len(done), a.max_requests, "(dry-run)" if a.dry_run else ""), file=sys.stderr)

    req_n, added = 0, 0
    t0 = time.time()
    stop = False
    for term in terms:
        if stop:
            break
        start_page = done.get(term, 0) + 1
        for page in range(start_page, a.pages + 1):
            if req_n >= a.max_requests:
                print("!! 达到单轮请求上限 %d,断点保存后收工(重跑继续)" % a.max_requests, file=sys.stderr)
                stop = True
                break
            qs = urllib.parse.urlencode({"text": term, "page": page, "pageSize": a.size,
                                         "sortsType": 2, "cache": 0,
                                         "productIds[0]": PRODUCTS[0], "productIds[1]": PRODUCTS[1]})
            if a.dry_run:
                print("DRY /search %s p%d" % (term, page), file=sys.stderr)
                req_n += 1
                continue
            try:
                d = http("/search?" + qs)
            except Exception as e:
                print("!! search fail %r p%d: %s(断点保存后收工)" % (term, page, str(e)[:100]), file=sys.stderr)
                stop = True
                break
            req_n += 1
            items = d.get("results") or []
            fresh = [x for x in items if (x.get("type"), str(x.get("questionId") or x.get("id"))) not in seen]
            if fresh:
                r = http("/corpus", {"items": [{"type": x.get("type"), "id": x.get("id"),
                                                "questionId": x.get("questionId"),
                                                "title": x.get("title"), "snippet": x.get("snippet"),
                                                "url": x.get("url"), "updatedAt": x.get("updatedAt")}
                                               for x in fresh],
                                     "discoveredBy": "fullscan"})
                added += r.get("written", 0)
                for x in fresh:
                    seen.add((x.get("type"), str(x.get("questionId") or x.get("id"))))
                prog["done"], prog["seen"] = {**done, **{term: page}}, list(seen)
            else:
                prog["done"] = {**done, **{term: page}}  # 整页无新:该词抓完(去重完),记录断点防重复
            done = prog["done"]
            if not items or len(fresh) < len(items) or page >= (d.get("totalPages") or 1):
                # 整页无新 ID = 更深页面只会更旧且已重复,提前收词
                if not items or len(fresh) == 0:
                    done[term] = a.pages  # 标记该词完成
                    break
            save_progress(a.progress, prog)
            if page % 10 == 0:
                print("== %r p%d/%s | 本页新 %d | 累计新增 %d | req %d" %
                      (term, page, d.get("totalPages"), len(fresh), added, req_n), file=sys.stderr)
            time.sleep(a.sleep)
        else:
            done[term] = a.pages
        prog["done"] = done
        save_progress(a.progress, prog)
    out = {"ok": True, "terms": len(terms), "uniqueDocs": len(seen), "stubsWritten": added,
           "upstreamRequests": req_n, "elapsedSec": round(time.time() - t0, 1),
           "dryRun": a.dry_run, "products": PRODUCTS}
    print(json.dumps(out, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
