#!/usr/bin/env python3
"""run_eval.py — kd 检索管线 A/B 评测(纯 stdlib,打本地服务 HTTP,不直连上游)
评测集: data/eval/evalset.json(27 例:usage=正式金标 13 例[含 v6 首条金标种子] / reference=官方对话参考 14 例)
配置:
  baseline   pipeline={"rerank":0,"synonyms":0}          # v3.2 服务忽略该参数,即现状基线
  full       pipeline={"rerank":1,"synonyms":1}          # v4.0 重排+同义词
  full-cache full 配置 + cache=1                          # 叠加本地缓存(测时延与上游调用)
  rg         离线语料评测:rg 直搜 corpus(零上游、不打本地服务),双口径
             宽口径=任一 query 的 rg 全文结果含 gold 文件(衡量"语料里有没有可搜中的文本",低→定向深读);
             严格口径=标题命中(^title:)优先排序取 top-K(衡量"真实使用能否排进前列",低→查询技巧)
指标: recall@5 / recall@10 / MRR(多路查询 RRF k=60 融合后排序);时延 p50/p95;缓存命中数。
硬指标(v6,ADR-0005,先标金后测管线;hardMetric 标签的用例另走 /ask 资料包,判定语义与 search 层金标互不影响):
  vocab-gap   词汇鸿沟存活率——症状词↔字段名错位用例,至少一个 gold 文档进资料包 sources 即存活;
  root-cause  根因可解释率——sources 引用落到 knowledge 类型且 chunk 文本命中 goldRootCauses[].keywords
              (关键词自 gold 文档【概述】段提取,固化在评测用例内,harness 不做用例外硬编码)。
上游消耗:彻底在线后评测直接消耗上游——响应 stats.upstreamCalls 逐次累加,
  --max-requests 设单轮硬上限(超限即停),--sample N --seed S 从用例池抽样,结束打印实际消耗数。
用法: python scripts/run_eval.py [--url http://127.0.0.1:4097] [--configs baseline,full|rg] [--out docs/eval-report-v4.md]
        [--sample N --seed 42] [--max-requests M] [--json docs/xxx.json] [--no-hard] [--topk 4]
退出码: 0=成功。注意:对上游保持人类频率,请求间默认 sleep 0.4s(基线留档跑请 ≥1s);rg 配置完全离线。
"""
import argparse, json, os, random, re, shutil, statistics, subprocess, sys, time, urllib.request

RRF_K = 60
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 上游请求预算(v6):全轮共享(搜索评测+硬指标),超限即停;实际消耗由各响应 stats.upstreamCalls 累加
UP = {"used": 0, "max": None}

def track_up(d):
    n = int((d.get("stats") or {}).get("upstreamCalls") or 0)
    UP["used"] += n
    return n

def budget_left():
    return UP["max"] is None or UP["used"] < UP["max"]

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
        if not budget_left():
            print("  !! %s 上游预算耗尽,剩余 query 跳过" % case["id"], file=sys.stderr)
            break
        body = {"text": q, "productId": case.get("product", 0), "pageSize": size}
        if pipeline:
            body["pipeline"] = pipeline
        try:
            d, ms = post(url, "/search", body)
        except Exception as e:
            print("  !! %s query=%s error=%s" % (case["id"], q, str(e)[:80]), file=sys.stderr)
            continue
        track_up(d)
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

# ---------- 硬指标(v6 ADR-0005):/ask 资料包口径,只对 hardMetric 标签用例跑 ----------

def _source_keys(s):
    ks = {str(s.get("id") or "")}
    if s.get("questionId"):
        ks.add(str(s["questionId"]))
    return ks

def _chunk_blob(detail):
    """资料包单源的 chunk 文本(无 chunks 退化全文)——引用可落到的最小单位。"""
    texts = [c.get("text") or "" for c in (detail.get("chunks") or [])]
    if not texts:
        texts = [detail.get("contentText") or ""]
    return (detail.get("chunks") or []), "\n".join(texts)

def run_hard_case(url, case, topk):
    """资料包级判定(/ask):词汇鸿沟存活=至少一个 gold 文档进 sources;
    根因可解释=sources 引用落到 knowledge 类型且 chunk 文本命中该 gold 的根因关键词。"""
    body = {"text": case["question"], "productId": case.get("product", 0), "topK": topk}
    try:
        d, ms = post(url, "/ask", body, timeout=180)
    except Exception as e:
        return {"id": case["id"], "tier": case.get("tier"), "tags": case.get("hardMetric") or [],
                "error": str(e)[:120], "survived": False, "explained": False, "goldHit": [],
                "rcGoldHit": [], "kwHits": [], "sources": 0, "upstream": 0, "lat": []}
    up = track_up(d)
    sources = d.get("sources") or []
    gset = gold_ids(case)
    gold_hit = sorted({k for s in sources for k in _source_keys(s) if k in gset})
    kw_hits, rc_hit = [], set()
    for s in sources:
        if s.get("type") != "knowledge":
            continue                      # 根因引用必须落到 knowledge 类型(发版说明 v6 起接入)
        chunks, blob = _chunk_blob(s.get("detail") or {})
        if not blob:
            continue
        for g in case.get("goldRootCauses") or []:
            kws = [k for k in (g.get("keywords") or []) if k and k in blob]
            if not kws:
                continue
            seqs = [c.get("seq") for c in chunks if any(k in (c.get("text") or "") for k in kws)]
            rc_hit.add(str(g.get("id")))
            kw_hits.append({"gold": g.get("id"), "source": s.get("id"), "rank": s.get("rank"),
                            "chunks": seqs, "keywords": kws})
    return {"id": case["id"], "tier": case.get("tier"), "tags": case.get("hardMetric") or [],
            "survived": bool(gold_hit), "explained": bool(rc_hit), "goldHit": gold_hit,
            "rcGoldHit": sorted(rc_hit), "kwHits": kw_hits, "sources": len(sources),
            "upstream": up, "lat": [ms]}

def hard_agg(hrows):
    vg = [r for r in hrows if "vocab-gap" in r["tags"]]
    rc = [r for r in hrows if "root-cause" in r["tags"]]
    return {"vocabGapSurvival": (sum(1 for r in vg if r["survived"]) / len(vg)) if vg else None,
            "vocabGapN": len(vg),
            "vocabGapAlive": sum(1 for r in vg if r["survived"]),
            "rootCauseExplain": (sum(1 for r in rc if r["explained"]) / len(rc)) if rc else None,
            "rootCauseN": len(rc),
            "rootCauseExplained": sum(1 for r in rc if r["explained"]),
            "upstream": sum(r["upstream"] for r in hrows)}

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
    ap.add_argument("--sample", type=int, default=0, help="池化抽样:从用例池随机抽 N 例跑(0=全量);彻底在线后评测消耗上游,抽样控制单轮请求量")
    ap.add_argument("--seed", type=int, default=42, help="抽样随机种子(默认 42,保证可复现)")
    ap.add_argument("--max-requests", type=int, default=0, help="上游请求硬上限(0=不限);超限即停并打印实际消耗")
    ap.add_argument("--topk", type=int, default=4, help="硬指标 /ask 深读 topK(1-8)")
    ap.add_argument("--no-hard", action="store_true", help="跳过硬指标评测(v6 /ask 资料包口径)")
    ap.add_argument("--json", dest="json_out", default=None, help="写结构化结果 JSON 路径(基线留档用)")
    a = ap.parse_args()
    es = json.load(open(a.evalset, encoding="utf-8"))
    pool = es["cases"]
    cases = pool
    if a.sample and 0 < a.sample < len(pool):
        cases = random.Random(a.seed).sample(pool, a.sample)
        print("池化抽样: %d/%d 例 (seed=%d)" % (len(cases), len(pool), a.seed), file=sys.stderr)
    UP["max"] = a.max_requests or None
    results, rg_res, rg_cov, hard = {}, {}, None, None
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
            if not budget_left():
                print("  !! 上游预算(%d)耗尽,剩余用例跳过" % a.max_requests, file=sys.stderr)
                break
            rows.append(run_case(a.url, c, pipe, a.size, a.sleep))
            r = rows[-1]
            print("  %-26s gold=%2d r5=%.2f r10=%.2f mrr=%.3f lat=%sms%s" %
                  (r["id"], r["gold"], r["r5"], r["r10"], r["mrr"],
                   round(statistics.mean(r["lat"]), 1) if r["lat"] else "-",
                   " cached=%d" % r["cached"] if r["cached"] else ""))
        if not rows:
            print("  >> %s: 无结果(上游预算耗尽)" % name, file=sys.stderr)
            break
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
    # 硬指标(v6):/ask 资料包口径,一次(与 configs 无关,基线=服务默认管线)
    hard_cases = [c for c in cases if c.get("hardMetric")]
    if not a.no_hard and hard_cases:
        print("== hard metrics(/ask 资料包口径,topK=%d)" % a.topk, file=sys.stderr)
        hrows = []
        for c in hard_cases:
            if not budget_left():
                print("  !! 上游预算(%d)耗尽,硬指标剩余用例跳过" % a.max_requests, file=sys.stderr)
                break
            r = run_hard_case(a.url, c, a.topk)
            hrows.append(r)
            print("  %-26s sources=%d gold命中=%s 存活=%s 根因可解释=%s 上游=%d lat=%sms" %
                  (r["id"], r["sources"], ",".join(r["goldHit"]) or "-", r["survived"],
                   r["explained"], r["upstream"], round(r["lat"][0]) if r["lat"] else 0))
            for h in r["kwHits"]:
                print("      根因段落: gold=%s <- source=%s(rank %s) chunk%s 关键词=%s" %
                      (h["gold"], h["source"], h["rank"], h["chunks"], "/".join(h["keywords"])))
            time.sleep(max(a.sleep, 0.4))
        if hrows:
            hard = hard_agg(hrows)
            hard["rows"] = hrows
            print("  >> 词汇鸿沟存活率=%.3f(%d/%d例) 根因可解释率=%.3f(%d/%d例) 上游=%d" %
                  (hard["vocabGapSurvival"] or 0, hard["vocabGapAlive"], hard["vocabGapN"],
                   hard["rootCauseExplain"] or 0, hard["rootCauseExplained"], hard["rootCauseN"],
                   hard["upstream"]))
    # 汇总对比
    lines = ["# kd 检索 A/B 评测报告", "", "评测集: %s(池 %d 例,本轮 %d 例;usage=正式金标 %d 例 / reference=官方对话参考 %d 例) | RRF k=%d | 生成时间 %s" %
             (os.path.basename(a.evalset), len(pool), len(cases),
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
    if hard:
        lines += ["", "## 硬指标(v6,ADR-0005;/ask 资料包口径,先标金后测管线)", "",
                  "- **词汇鸿沟存活率**: %.3f(%d/%d 例)——症状词↔字段名错位用例,至少一个 gold 文档进资料包 sources 即存活;" %
                  (hard["vocabGapSurvival"] or 0, hard["vocabGapAlive"], hard["vocabGapN"]),
                  "- **根因可解释率**: %.3f(%d/%d 例)——sources 引用落到 knowledge 类型且 chunk 文本命中 gold 根因关键词(自 gold 文档【概述】段提取,固化在评测用例内);" %
                  (hard["rootCauseExplain"] or 0, hard["rootCauseExplained"], hard["rootCauseN"]),
                  "- 判定为资料包级(sources 引用素材);最终回答级引用判定待 kd ai 管线评测,不影响 search 层金标口径。", ""]
        lines += ["| 用例 | gold命中(sources) | 存活 | 根因可解释 | 根因关键词命中 | 上游请求 |",
                  "|---|---|---|---|---|---|"]
        for r in hard["rows"]:
            kws = "; ".join("%s<-%s:%s" % (h["gold"], h["source"], "/".join(h["keywords"]))
                            for h in r["kwHits"]) or "-"
            lines.append("| %s | %s | %s | %s | %s | %d |" %
                         (r["id"], ",".join(r["goldHit"]) or "-", r["survived"], r["explained"], kws, r["upstream"]))
    lines += ["", "上游请求实际消耗: %d(上限 %s)| 请求间 sleep=%.1fs | 频率:对上游保持人类节奏" %
              (UP["used"], UP["max"] if UP["max"] else "不限", a.sleep)]
    text = "\n".join(lines) + "\n"
    print("\n" + text)
    if a.out:
        os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
        with open(a.out, "w", encoding="utf-8") as fh:
            fh.write(text)
        print("report ->", a.out, file=sys.stderr)
    if a.json_out:
        os.makedirs(os.path.dirname(os.path.abspath(a.json_out)), exist_ok=True)
        out = {"generatedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
               "evalset": {"file": os.path.basename(a.evalset), "version": es.get("version"),
                           "pool": len(pool), "sampled": len(cases),
                           "seed": a.seed if (a.sample and 0 < a.sample < len(pool)) else None},
               "args": {"configs": a.configs, "size": a.size, "sleep": a.sleep, "topk": a.topk,
                        "sample": a.sample, "seed": a.seed, "maxRequests": a.max_requests,
                        "hardMetrics": not a.no_hard},
               "upstreamCalls": {"used": UP["used"], "cap": a.max_requests or None},
               "results": results, "hard": {k: v for k, v in (hard or {}).items() if k != "rows"},
               "hardRows": (hard or {}).get("rows")}
        with open(a.json_out, "w", encoding="utf-8") as fh:
            json.dump(out, fh, ensure_ascii=False, indent=1)
        print("json ->", a.json_out, file=sys.stderr)

if __name__ == "__main__":
    main()
