#!/usr/bin/env python3
"""run_eval.py — kd 检索管线 A/B 评测(纯 stdlib,打本地服务 HTTP,不直连上游)
评测集: data/eval/evalset.json(23 例:官方分享对话金标 + 专家金标)
配置:
  baseline   pipeline={"rerank":0,"synonyms":0}          # v3.2 服务忽略该参数,即现状基线
  full       pipeline={"rerank":1,"synonyms":1}          # v4.0 重排+同义词
  full-cache full 配置 + cache=1                          # 叠加本地缓存(测时延与上游调用)
指标: recall@5 / recall@10 / MRR(多路查询 RRF k=60 融合后排序);时延 p50/p95;缓存命中数。
用法: python scripts/run_eval.py [--url http://127.0.0.1:4097] [--configs baseline,full] [--out docs/eval-report-v4.md]
退出码: 0=成功。注意:对上游保持人类频率,请求间默认 sleep 0.4s。
"""
import argparse, json, os, statistics, sys, time, urllib.request

RRF_K = 60
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def post(url, path, body, timeout=60):
    req = urllib.request.Request(url.rstrip("/") + path, data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.loads(r.read().decode("utf-8", "replace"))
    return d, (time.time() - t0) * 1000

def item_key(x):
    # answer 条目按 questionId 归并(同问题多回答只算一个文档位)
    if x.get("type") == "answer" and x.get("questionId"):
        return "answer:" + str(x["questionId"])
    return str(x.get("type", "?")) + ":" + str(x.get("id"))

def gold_ids(case):
    return {str(g["id"]) for g in case["gold"]}

def _tier_of(case_id, cases):
    for c in cases:
        if c["id"] == case_id:
            return c.get("tier", "usage" if c.get("source") == "expert" else "reference")
    return "usage"

def run_case(url, case, pipeline, size, sleep):
    lists, lat, cached = [], [], 0
    for q in case["queries"]:
        body = {"text": q, "productId": case.get("product", 0), "pageSize": size}
        if pipeline:
            body["pipeline"] = pipeline
        try:
            d, ms = post(url, "/search", body)
        except Exception as e:
            print("  !! %s query=%s error=%s" % (case["id"], q, str(e)[:80]), file=sys.stderr)
            continue
        lat.append(ms)
        cached += int((d.get("stats") or {}).get("cacheHits") or 0)
        lists.append(d.get("results") or [])
        time.sleep(sleep)
    # RRF 融合
    scores, items = {}, {}
    for lst in lists:
        for rank, x in enumerate(lst, 1):
            k = item_key(x)
            scores[k] = scores.get(k, 0.0) + 1.0 / (RRF_K + rank)
            if k not in items:
                items[k] = x
    ranked = sorted(items.values(), key=lambda x: -scores[item_key(x)])
    gset = gold_ids(case)
    hits_at = {k: 0 for k in (5, 10)}
    rr = 0.0
    for rank, x in enumerate(ranked, 1):
        ids = {str(x.get("id") or ""), str(x.get("questionId") or "")}
        if ids & gset:
            if rank <= 10:
                rr = max(rr, 1.0 / rank)
            for k in hits_at:
                if rank <= k:
                    hits_at[k] += 1
    return {"id": case["id"], "source": case.get("source"),
            "gold": len(gset), "returned": len(ranked),
            "r5": hits_at[5] / len(gset) if gset else 0.0,
            "r10": hits_at[10] / len(gset) if gset else 0.0,
            "mrr": rr, "lat": lat, "cached": cached,
            "stats": [x.get("stats") for x in ranked[:1]]}

def agg(cases):
    lat = sorted(m for c in cases for m in c["lat"])
    def p(ps):
        return round(lat[min(len(lat) - 1, int(len(lat) * ps / 100))], 1) if lat else 0
    return {"r5": sum(c["r5"] for c in cases) / len(cases),
            "r10": sum(c["r10"] for c in cases) / len(cases),
            "mrr": sum(c["mrr"] for c in cases) / len(cases),
            "p50": p(50), "p95": p(95),
            "cached": sum(c["cached"] for c in cases)}

CONFIGS = {
    "baseline": {"rerank": 0, "cache": 0},     # 冷态:纯上游(不读写缓存)
    "rerank": {"rerank": 1, "cache": 0},       # opt-in 信号重排实验(评测:recall@10 -11%,不推荐常开)
    "cache": {"rerank": 0, "cache": 1},        # 本地缓存语料库(首跑=回源预热,二跑=纯暖读)
}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=os.environ.get("KSEARCH_URL", "http://127.0.0.1:4097"))
    ap.add_argument("--evalset", default=os.path.join(ROOT, "data", "eval", "evalset.json"))
    ap.add_argument("--configs", default="baseline,full")
    ap.add_argument("--size", type=int, default=10)
    ap.add_argument("--sleep", type=float, default=0.4)
    ap.add_argument("--out", default=None, help="写 markdown 报告路径")
    a = ap.parse_args()
    es = json.load(open(a.evalset, encoding="utf-8"))
    cases = es["cases"]
    results = {}
    for name in a.configs.split(","):
        name = name.strip()
        pipe = CONFIGS.get(name)
        if pipe is None:
            print("unknown config", name, file=sys.stderr); sys.exit(2)
        print("== config %s pipeline=%s" % (name, pipe), file=sys.stderr)
        rows = []
        for c in cases:
            rows.append(run_case(a.url, c, pipe, a.size, a.sleep))
            r = rows[-1]
            print("  %-26s gold=%2d r5=%.2f r10=%.2f mrr=%.3f lat=%sms%s" %
                  (r["id"], r["gold"], r["r5"], r["r10"], r["mrr"],
                   round(statistics.mean(r["lat"]), 1) if r["lat"] else "-",
                   " cached=%d" % r["cached"] if r["cached"] else ""))
        results[name] = {"rows": rows, "agg": agg(rows)}
        results[name]["byTier"] = {}
        for tier in ("usage", "reference"):
            tr = [r for r in rows if _tier_of(r["id"], cases) == tier]
            if tr:
                results[name]["byTier"][tier] = {"agg": agg(tr), "n": len(tr)}
        g = results[name]["agg"]
        print("  >> %s: recall@5=%.3f recall@10=%.3f MRR=%.3f lat p50=%sms p95=%sms cached=%d" %
              (name, g["r5"], g["r10"], g["mrr"], g["p50"], g["p95"], g["cached"]))
        for tier, t in results[name]["byTier"].items():
            ta = t["agg"]
            print("     %s(%d例): recall@5=%.3f recall@10=%.3f MRR=%.3f p50=%sms" %
                  (tier, t["n"], ta["r5"], ta["r10"], ta["mrr"], ta["p50"]))
    # 汇总对比
    lines = ["# kd 检索 A/B 评测报告", "", "评测集: %s(%d 例,usage=正式金标 %d 例 / reference=官方对话参考 %d 例) | RRF k=%d | 生成时间 %s" %
             (os.path.basename(a.evalset), len(cases),
              sum(1 for c in cases if c.get("tier") == "usage"),
              sum(1 for c in cases if c.get("tier") == "reference"),
              RRF_K, time.strftime("%Y-%m-%d %H:%M:%S")),
             "", "≥ 正式达标口径=usage 层;reference(官方对话金标)仅作参考,不作达标依据。", ""]
    lines += ["| 配置 | recall@5 | recall@10 | MRR | 时延p50(ms) | 时延p95(ms) | 缓存命中 |",
              "|---|---|---|---|---|---|---|"]
    for name, r in results.items():
        g = r["agg"]
        lines.append("| %s | %.3f | %.3f | %.3f | %s | %s | %d |" %
                     (name, g["r5"], g["r10"], g["mrr"], g["p50"], g["p95"], g["cached"]))
    lines += ["", "### 分层(正式口径 = usage 层)", ""]
    for name, r in results.items():
        for tier in ("usage", "reference"):
            t = r["byTier"].get(tier)
            if t:
                ta = t["agg"]
                lines.append("- **%s · %s(%d例)**: recall@5=%.3f recall@10=%.3f MRR=%.3f p50=%sms" %
                             (name, tier, t["n"], ta["r5"], ta["r10"], ta["mrr"], ta["p50"]))
    if "baseline" in results and "rerank" in results:
        b, f = results["baseline"]["agg"], results["rerank"]["agg"]
        lines += ["", "rerank 相对 baseline:recall@5 %+.1f%%,recall@10 %+.1f%%,MRR %+.1f%%(正数=更好)" %
                  ((f["r5"]/b["r5"]-1)*100 if b["r5"] else 0,
                   (f["r10"]/b["r10"]-1)*100 if b["r10"] else 0,
                   (f["mrr"]/b["mrr"]-1)*100 if b["mrr"] else 0)]
    if "baseline" in results and "cache" in results:
        b, f = results["baseline"]["agg"], results["cache"]["agg"]
        lines += ["", "cache 相对 baseline:p50 %+.0f%%,p95 %+.0f%%(负数=更快),缓存命中 %d 次检索" %
                  ((f["p50"]/b["p50"]-1)*100 if b["p50"] else 0,
                   (f["p95"]/b["p95"]-1)*100 if b["p95"] else 0, f["cached"])]
    text = "\n".join(lines) + "\n"
    print("\n" + text)
    if a.out:
        os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
        with open(a.out, "w", encoding="utf-8") as fh:
            fh.write(text)
        print("report ->", a.out, file=sys.stderr)

if __name__ == "__main__":
    main()
