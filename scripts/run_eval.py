#!/usr/bin/env python3
"""run_eval.py — kd 检索管线 A/B 评测(纯 stdlib,打本地服务 HTTP,不直连上游)
评测集: data/eval/evalset.json(26 例:usage=正式金标 12 例 / reference=官方对话参考 14 例)
配置:
  baseline   pipeline={"rerank":0,"synonyms":0}          # v3.2 服务忽略该参数,即现状基线
  full       pipeline={"rerank":1,"synonyms":1}          # v4.0 重排+同义词
  full-cache full 配置 + cache=1                          # 叠加本地缓存(测时延与上游调用)
  rg         离线语料评测:rg 直搜 corpus(零上游、不打本地服务),双口径
             宽口径=任一 query 的 rg 全文结果含 gold 文件(衡量"语料里有没有可搜中的文本",低→定向深读);
             严格口径=标题命中(^title:)优先排序取 top-K(衡量"真实使用能否排进前列",低→查询技巧)
指标: recall@5 / recall@10 / MRR(多路查询 RRF k=60 融合后排序);时延 p50/p95;缓存命中数。
用法: python scripts/run_eval.py [--url http://127.0.0.1:4097] [--configs baseline,full|rg] [--out docs/eval-report-v4.md]
退出码: 0=成功。注意:对上游保持人类频率,请求间默认 sleep 0.4s;rg 配置完全离线。
"""
import argparse, json, os, re, shutil, statistics, subprocess, sys, time, urllib.request

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

# ---------- rg 离线语料评测(配置名 "rg",不碰 HTTP) ----------

RG_EXCLUDE = ("-g", "!usage/**")   # usage 沉淀目录不算语料检索面

def default_corpus():
    p = os.path.expanduser("~/.lingeebuild/corpus")
    if not os.path.isdir(p):
        print("corpus 目录不存在: %s(kd health 的 corpus.path 为准,可用 --corpus 覆盖)" % p, file=sys.stderr)
        sys.exit(2)
    return p

def rg_files(corpus, terms, extra=RG_EXCLUDE):
    """多词必须 -e 重复(OR 语义)——与 SKILL.md 的姿势修正同一口径;返回命中的 .md 全路径集合"""
    cmd = ["rg", "--no-messages", "-il", *extra]
    for t in terms:
        cmd += ["-e", t]
    cmd.append(corpus)
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return {l.strip() for l in p.stdout.splitlines() if l.strip()}

def stem(path):
    return os.path.basename(path)[:-3] if path.endswith(".md") else os.path.basename(path)

def gold_file_map(cases, corpus):
    """gold id → corpus 文件 stem 集合。主路径=文件名即 id;answer 类 gold 可能是 questionId,
    用单次 rg 全文搜所有 id 兜底(搜到的文件读 front-matter 区确认归属)。"""
    ids = sorted({str(g["id"]) for c in cases for g in c["gold"]})
    m = {i: {i} for i in ids}
    hits = rg_files(corpus, [re.escape(i) for i in ids], extra=())
    for path in hits:
        try:
            head = open(path, encoding="utf-8", errors="replace").read(2000)
        except OSError:
            continue
        for i in ids:
            if i in head:
                m[i].add(stem(path))
    return m

def rg_case(corpus, case, gmap):
    gset = gold_ids(case)
    gfiles = [gmap.get(g, {g}) for g in gset]
    stem2gold = {}
    for i, gf in enumerate(gfiles):
        for s in gf:
            stem2gold.setdefault(s, set()).add(i)
    wide_seen, scores, lat = set(), {}, []
    for q in case["queries"]:
        terms = [t for t in re.split(r"\s+", q.strip()) if t]
        t0 = time.time()
        title = {stem(p) for p in rg_files(corpus, ["^title:.*" + re.escape(t) for t in terms])}
        body = {stem(p) for p in rg_files(corpus, [re.escape(t) for t in terms])}
        lat.append((time.time() - t0) * 1000)
        wide_seen |= body | title
        ranked = sorted(title) + sorted(body - title)   # 严格口径:标题命中优先
        for rank, s in enumerate(ranked, 1):
            scores[s] = scores.get(s, 0.0) + 1.0 / (RRF_K + rank)
    wide = sum(1 for gf in gfiles if gf & wide_seen)
    best = {}
    for s in sorted(scores, key=lambda x: -scores[x]):
        rank = len(best) + 1
        for i in stem2gold.get(s, ()):
            best.setdefault(i, rank)
    hits_at = {k: sum(1 for r in best.values() if r <= k) for k in (5, 10)}
    rr = min(best.values()) if best else 0
    return {"id": case["id"], "source": case.get("source"), "gold": len(gset),
            "wide": wide / len(gset) if gset else 0.0,
            "r5": hits_at[5] / len(gset) if gset else 0.0,
            "r10": hits_at[10] / len(gset) if gset else 0.0,
            "mrr": 1.0 / rr if rr else 0.0, "lat": lat}

def rg_agg(rows):
    lat = sorted(m for r in rows for m in r["lat"])
    def p(ps):
        return round(lat[min(len(lat) - 1, int(len(lat) * ps / 100))], 1) if lat else 0
    n = len(rows) or 1
    return {"wide": sum(r["wide"] for r in rows) / n,
            "r5": sum(r["r5"] for r in rows) / n,
            "r10": sum(r["r10"] for r in rows) / n,
            "mrr": sum(r["mrr"] for r in rows) / n,
            "p50": p(50), "p95": p(95)}

def run_rg_cases(corpus, cases):
    if shutil.which("rg") is None:
        print("rg 未找到(需要 ripgrep 14+,确认它在 PATH)", file=sys.stderr); sys.exit(2)
    print("  构建 gold→corpus 文件映射(单次 rg 全文搜 id)...", file=sys.stderr)
    gmap = gold_file_map(cases, corpus)
    # corpus 对金标的文件覆盖率(宽口径的地板:文件不在语料里,搜得再好也是 0)
    have = sum(1 for c in cases for g in c["gold"]
               if os.path.isfile(os.path.join(corpus, g["type"], str(g["id"]) + ".md"))
               or gmap.get(str(g["id"]), set()) - {str(g["id"])})
    total = sum(len(c["gold"]) for c in cases)
    rows = []
    for c in cases:
        rows.append(rg_case(corpus, c, gmap))
        r = rows[-1]
        print("  %-26s gold=%2d 宽=%.2f r5=%.2f r10=%.2f mrr=%.3f rg=%.0fms" %
              (r["id"], r["gold"], r["wide"], r["r5"], r["r10"], r["mrr"],
               statistics.mean(r["lat"]) if r["lat"] else 0))
    cov = have / total if total else 0.0
    return rows, cov

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=os.environ.get("KSEARCH_URL", "http://127.0.0.1:4097"))
    ap.add_argument("--evalset", default=os.path.join(ROOT, "data", "eval", "evalset.json"))
    ap.add_argument("--configs", default="baseline,full")
    ap.add_argument("--size", type=int, default=10)
    ap.add_argument("--sleep", type=float, default=0.4)
    ap.add_argument("--out", default=None, help="写 markdown 报告路径")
    ap.add_argument("--corpus", default=None, help="rg 离线评测的语料目录(默认 ~/.lingeebuild/corpus)")
    a = ap.parse_args()
    es = json.load(open(a.evalset, encoding="utf-8"))
    cases = es["cases"]
    results, rg_res, rg_cov = {}, {}, None
    for name in a.configs.split(","):
        name = name.strip()
        if name == "rg":
            rows, rg_cov = run_rg_cases(a.corpus or default_corpus(), cases)
            rg_res[name] = {"rows": rows, "agg": rg_agg(rows), "byTier": {}}
            for tier in ("usage", "reference"):
                tr = [r for r in rows if _tier_of(r["id"], cases) == tier]
                if tr:
                    rg_res[name]["byTier"][tier] = {"agg": rg_agg(tr), "n": len(tr)}
            g = rg_res[name]["agg"]
            print("  >> rg: 宽口径recall=%.3f 严格recall@5=%.3f @10=%.3f MRR=%.3f rg时延 p50=%sms p95=%sms" %
                  (g["wide"], g["r5"], g["r10"], g["mrr"], g["p50"], g["p95"]))
            for tier, t in rg_res[name]["byTier"].items():
                ta = t["agg"]
                print("     %s(%d例): 宽=%.3f r5=%.3f r10=%.3f mrr=%.3f" %
                      (tier, t["n"], ta["wide"], ta["r5"], ta["r10"], ta["mrr"]))
            continue
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
    if rg_res:
        lines += ["", "## rg 离线语料评测(corpus 直搜,零上游)", "",
                  "- **宽口径**:任一 query 的 rg 全文结果含 gold 文件即命中——衡量\"语料里有没有可搜中的文本\";低 → 语料缺全文,指向定向深读写穿;",
                  "- **严格口径**:标题命中(`^title:`)优先排序取 top-K(RRF 融合)——衡量\"真实使用能否排进前列\";低 → 查询技巧/排序问题;",
                  "- usage/ 目录不计入检索面;corpus 未按 product 过滤(目录无产品维度)。",
                  ""]
        lines += ["| 配置 | 宽口径recall | 严格recall@5 | 严格recall@10 | 严格MRR | rg时延p50(ms) |",
                  "|---|---|---|---|---|---|"]
        for name, r in rg_res.items():
            g = r["agg"]
            lines.append("| %s | %.3f | %.3f | %.3f | %.3f | %s |" %
                         (name, g["wide"], g["r5"], g["r10"], g["mrr"], g["p50"]))
        lines += ["", "### 分层(正式口径 = usage 层)", ""]
        for name, r in rg_res.items():
            for tier in ("usage", "reference"):
                t = r["byTier"].get(tier)
                if t:
                    ta = t["agg"]
                    lines.append("- **%s · %s(%d例)**: 宽=%.3f 严格r5=%.3f r10=%.3f MRR=%.3f" %
                                 (name, tier, t["n"], ta["wide"], ta["r5"], ta["r10"], ta["mrr"]))
        lines += ["", "语料对金标的文件覆盖率(宽口径的地板):%.1f%%——低于它的 recall 差距只能靠补语料(深读写穿/发现层)弥合。" % (rg_cov * 100)]
    text = "\n".join(lines) + "\n"
    print("\n" + text)
    if a.out:
        os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
        with open(a.out, "w", encoding="utf-8") as fh:
            fh.write(text)
        print("report ->", a.out, file=sys.stderr)

if __name__ == "__main__":
    main()
