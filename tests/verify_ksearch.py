#!/usr/bin/env python3
"""verify_ksearch.py — 金蝶知识服务+kd CLI 一键回归(改服务/CLI 后必跑)
路径全部参数化,双环境自适应:
  KSEARCH_URL  服务基址(默认 http://127.0.0.1:4097)
  KD_PY        kd.py 路径(默认依次找 %USERPROFILE%/.kingdee-kit/bin → %USERPROFILE%/.lingeebuild/bin)
用法: python verify_ksearch.py            # 全量(约 1 分钟;需服务在跑,会消耗真实上游请求)
退出码: 0=全部通过 1=有失败
"""
import json, os, shutil, subprocess, sys, threading, time, urllib.request, urllib.error
from http.server import BaseHTTPRequestHandler, HTTPServer

B = os.environ.get("KSEARCH_URL", "http://127.0.0.1:4097")
HOME = os.environ.get("USERPROFILE") or os.environ.get("HOME") or ""
def _kd_candidates():
    env = os.environ.get("KD_PY")
    if env:
        yield env
    yield os.path.join(HOME, ".kingdee-kit", "bin", "kd.py")
    yield os.path.join(HOME, ".lingeebuild", "bin", "kd.py")
KD_PY = next((p for p in _kd_candidates() if p and os.path.exists(p)), None)
KD_CMD = os.path.splitext(KD_PY)[0] + ".cmd" if KD_PY and os.path.exists(os.path.splitext(KD_PY)[0] + ".cmd") else None
PYTHON = sys.executable

RESULTS = []
SKIPPED = []

def call_http(path, body=None, method=None, timeout=90):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(B + path, data=data,
                                 headers={"Content-Type": "application/json"} if data else {},
                                 method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))

def expect(cond, msg=""):
    if not cond:
        raise AssertionError(msg)
    return msg

class Skipped(Exception):
    """环境不适用(不是失败,也不计入 PASS):该断言在当前平台无意义。"""

def check(name, fn):
    try:
        detail = fn()
        RESULTS.append((name, True, detail))
        print("PASS  %s  %s" % (name, detail or ""))
    except Skipped as e:
        SKIPPED.append((name, str(e)))
        print("SKIP  %s  %s" % (name, e))
    except Exception as e:
        RESULTS.append((name, False, str(e)[:160]))
        print("FAIL  %s  %s" % (name, str(e)[:160]))
    time.sleep(0.3)

# ---- 服务端点 ----
check("health", lambda: expect(call_http("/health").get("anonymous") is True, call_http("/health").get("service")))
def t_manifest():
    m = call_http("/manifest")
    cmds = " | ".join(m["cli"]["commands"])
    expect(len(m["endpoints"]) >= 9, "endpoints=%d" % len(m["endpoints"]))
    expect("kd read" in cmds and "kd ask" in cmds, "命令面在清单中")
    expect("kd ai" not in cmds, "kd ai 已删除(ADR-0008)")
    expect("kd question" not in cmds and "kd article" not in cmds, "旧命令已退役")
    return "%d endpoints" % len(m["endpoints"])
check("manifest", t_manifest)

def t_search():
    d = call_http("/search?text=%s&productId=0&pageSize=10" % urllib.parse.quote("信用额度控制"))
    types = {x["type"] for x in d["results"]}
    expect(d["results"] and types <= {"knowledge", "answer", "article"}, "total=%s types=%s" % (d["total"], sorted(types)))
check("search 全类型", t_search)

check("search type=knowledge", lambda: expect(
    any(x["type"] == "knowledge" for x in call_http(
        "/search?text=%s&productId=0&pageSize=5&type=knowledge" % urllib.parse.quote("信用额度控制"))["results"]),
    "scan 凑满"))

KN_ID = "402990431979506944"; Q_ID = "799346568250934528"; A_ID = "799691682479252480"; AR_ID = "56784392135739905"
check("karticle", lambda: expect(len(call_http("/karticle", {"id": KN_ID})["contentText"]) > 50, "全文非空"))
def t_question():
    d = call_http("/question", {"id": Q_ID})
    expect(d["answers"] and len(d["answers"][0]["contentText"]) > 0, "%d 条回答" % len(d["answers"]))
check("question", t_question)
check("answer", lambda: expect(len(call_http("/answer", {"id": A_ID})["contentText"]) > 0, "正文非空"))
check("article", lambda: expect(len(call_http("/article", {"id": AR_ID})["contentText"]) > 50, "全文非空"))
check("ask(text)", lambda: expect(len(call_http("/ask", {"text": "信用额度控制", "topK": 2})["sources"]) >= 1, "sources≥1"))
check("ask(keywords)", lambda: expect(len(call_http("/ask", {"keywords": ["信用额度", "应收单 信用"], "topK": 2})["sources"]) >= 1, "sources≥1"))
check("share", lambda: expect(call_http("/share", {"link": "https://vip.kingdee.com/link/s/clzcE"})["count"] >= 1, "chats≥1"))

# ---- v4 检索管线(信号重排/同义词RRF/缓存语料库/chunk) ----
def t_v4_search():
    d = call_http("/search", {"text": "MRP运算 需求", "productId": 93, "pageSize": 5,
                              "pipeline": {"rerank": True}})
    expect(d.get("results"), "results空")
    st = d.get("stats") or {}
    expect(st.get("pipeline"), "缺 stats.pipeline")
    expect(len(d.get("queries") or []) >= 1, "缺 queries")
    return "queries=%d upstream=%s" % (len(d["queries"]), st.get("upstreamCalls"))
check("v4 search 管线(重排opt-in)", t_v4_search)

check("v4 search 兼容模式 rerank=0", lambda: expect(
    len(call_http("/search", {"text": "信用额度控制", "productId": 0, "pageSize": 5,
                              "pipeline": {"rerank": False, "synonyms": False}})["results"]) >= 1, "v3.2路径可用"))

def t_v4_ask_cache():
    body = {"text": "BOM正查 需求用量 计算", "topK": 1, "cache": 1}
    d1 = call_http("/ask", body)
    chunks = [len((s.get("detail") or {}).get("chunks") or []) for s in d1.get("sources") or []]
    d2 = call_http("/ask", body)
    st2 = d2.get("stats") or {}
    expect(st2.get("cacheHits", 0) >= 1, "二次未命中缓存 cacheHits=%s" % st2.get("cacheHits"))
    expect(st2.get("upstreamCalls", 9) <= 1, "二次仍打上游 %s 次" % st2.get("upstreamCalls"))
    expect(any(c > 0 for c in chunks), "无 chunk 切片")
    return "chunks=%s 二次upstream=%s cacheHits=%s" % (chunks, st2.get("upstreamCalls"), st2.get("cacheHits"))
check("v4 ask 缓存+chunk", t_v4_ask_cache)

def t_v4_local():
    h = call_http("/health")
    expect("6." in (h.get("service") or ""), h.get("service"))
    expect((h.get("landing") or {}).get("total", 0) >= 1, "landing 未沉淀")
    return "landing=%s" % h["landing"]["total"]
check("v6 落地缓存+健康", t_v4_local)

def t_budget_consistency():
    """预算值是单一事实源(query_routes.json),服务与 CLI 的文字必须与它同步。
    此前四处文档写 16/18 而配置写 32,导致同一份资料包被三套口径描述——用断言钉死。"""
    import re
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg = json.load(open(os.path.join(root, "service", "query_routes.json"), encoding="utf-8"))
    want = int((cfg.get("budget") or {}).get("maxUpstreamPerAsk"))
    h = call_http("/health")
    got = int((h.get("ask") or {}).get("budgetMax"))
    expect(got == want, "/health.ask.budgetMax=%s 与 query_routes.json=%s 不一致" % (got, want))
    # 服务源码的散文说明不得残留其他预算数字
    src = open(os.path.join(root, "service", "kingdee-ksearch-service.py"), encoding="utf-8").read()
    for m in re.finditer(r"预算硬上限[^;\n]*?≤(\d+)", src):
        expect(int(m.group(1)) == want, "服务 docstring 预算值 %s ≠ 配置 %s" % (m.group(1), want))
    kdsrc = open(os.path.join(root, "cli", "kd.py"), encoding="utf-8").read()
    m = re.search(r'--budget.*?默认 (\d+)', kdsrc)
    if m:
        expect(int(m.group(1)) == want, "kd --budget help 默认值 %s ≠ 配置 %s" % (m.group(1), want))
    return "预算=%s(服务/CLI/docstring 三处一致)" % want
check("预算值全链路一致", t_budget_consistency)

# ---- v5 corpus 语料目录(写穿/stub/摄入/deprecate 标注) ----
def t_v5_corpus_written():
    h = call_http("/health")
    c = h.get("corpus") or {}
    expect(c.get("total", 0) >= 3, "corpus 未随前面的 read 沉淀 total=%s" % c.get("total"))
    return "total=%s k/a/a=%s/%s/%s" % (c.get("total"), c.get("knowledge"), c.get("answer"), c.get("article"))
check("v5 corpus 写穿(read 沉淀)", t_v5_corpus_written)

def t_v5_corpus_file():
    cpath = (call_http("/health").get("corpus") or {}).get("path")
    expect(cpath and os.path.isdir(cpath), "corpus 目录不存在 %s" % cpath)
    p = os.path.join(cpath, "knowledge", "%s.md" % KN_ID)
    expect(os.path.exists(p), "knowledge 全文未落盘 %s" % p)
    head = open(p, encoding="utf-8").read(600)
    for field in ("id:", "type: knowledge", "url:", "title:", "discovered_by:"):
        expect(field in head, "front-matter 缺 %s" % field)
    return "front-matter 完整"
check("v5 corpus 文件规范", t_v5_corpus_file)

def t_v5_corpus_ingest():
    r = call_http("/corpus", {"items": [{"type": "article", "id": "verify-ingest-1", "title": "verify stub",
                                         "snippet": "s", "updatedAt": "2026-09-06"}],
                               "discoveredBy": "timesweep"})
    expect(r.get("ok") and r.get("written", 0) + r.get("unchanged", 0) >= 1, str(r)[:120])
    r2 = call_http("/corpus", {"items": [{"type": "article", "id": "verify-ingest-1", "title": "verify stub",
                                          "snippet": "s", "updatedAt": "2026-09-06"}],
                               "discoveredBy": "timesweep"})
    expect(r2.get("unchanged", 0) == 1, "重复摄入未判 unchanged: %s" % r2)
    return "written=%d 复摄入=unchanged" % r.get("written", 0)
check("v5 corpus 摄入+幂等", t_v5_corpus_ingest)

check("v5 deprecate 标注", lambda: expect(
    "deprecated" in str(call_http("/manifest")["pipeline"]["params"].get("local", "")), "local 未标 deprecated"))
def t_v5_fullscan_idempotent():
    """全量快照脚本幂等:假 /search 恒回同一页,/corpus 转发真服务。
    第一轮写入 N 篇;第二轮(断点+去重)必须零新增、请求极少。"""
    import subprocess
    fwd = urllib.request.build_opener()
    class H(BaseHTTPRequestHandler):
        def _send(self, b):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)
        def do_GET(self):
            results = [{"type": "article", "id": "fs-%d" % n, "title": "fullscan sample %d" % n,
                        "snippet": "s", "url": "https://vip.kingdee.com/article/fs-%d" % n} for n in range(3)]
            self._send(json.dumps({"results": results, "totalElements": 6, "totalPages": 2}).encode())
        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(n)
            req = urllib.request.Request(B + self.path, data=body, headers={"Content-Type": "application/json"})
            self._send(fwd.open(req, timeout=30).read())
        def log_message(self, *a):
            pass
    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    fake = "http://127.0.0.1:%d" % srv.server_address[1]
    prog = os.path.join(os.path.dirname(os.path.abspath(__file__)), "__fullscan_test_progress.json")
    if os.path.exists(prog):
        os.remove(prog)
    terms_file = prog.replace("progress", "terms")
    with open(terms_file, "w", encoding="utf-8") as f:
        json.dump({"terms": ["测试词A", "测试词B"]}, f)
    script = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "corpus_fullscan.py")
    def run():
        r = subprocess.run([PYTHON, script, "--terms", terms_file, "--progress", prog,
                            "--max-requests", "10", "--pages", "2"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           env=dict(os.environ, KSEARCH_URL=fake), timeout=120)
        expect(r.returncode == 0, "exit=%s %s" % (r.returncode, r.stderr[:160]))
        return json.loads(r.stdout)
    r1 = run()
    expect(r1["stubsWritten"] >= 3, "首轮未写入 %s" % r1)
    r2 = run()
    expect(r2["stubsWritten"] == 0, "第二轮重复写入 %s" % r2)
    os.remove(prog)
    os.remove(terms_file)
    cpath = (call_http("/health").get("corpus") or {}).get("path")
    for n in range(3):
        f = os.path.join(cpath, "article", "fs-%d.md" % n)
        if os.path.exists(f):
            os.remove(f)
    return "首轮+%d 二轮0" % r1["stubsWritten"]
check("v5 fullscan 幂等(假服务)", t_v5_fullscan_idempotent)


def t_retired():
    try:
        call_http("/rag", {"question": "x"})
        raise AssertionError("/rag 仍存在")
    except urllib.error.HTTPError as e:
        expect(e.code == 404, "/rag 404")
check("退役端点 /rag=404", t_retired)

# ---- kd CLI ----
def t_kd_exists():
    expect(KD_PY, "未找到 kd.py(设 KD_PY 环境变量或先运行安装器)")
check("kd.py 存在", t_kd_exists)

def kd(args, timeout=120):
    if not KD_PY:
        raise AssertionError("KD_PY 未设置")
    r = subprocess.run([PYTHON, KD_PY] + args, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
    expect(r.returncode == 0, "exit=%s stderr=%s" % (r.returncode, r.stderr[:120]))
    return json.loads(r.stdout)

if KD_PY:
    check("kd health", lambda: expect(kd(["health"])["anonymous"] is True, "via kd.py"))
    def t_kd_manifest():
        cmds = " | ".join(kd(["manifest"])["cli"]["commands"])
        expect("kd read" in cmds and "kd ask" in cmds, "命令面")
        expect("kd ai" not in cmds, "kd ai 已删除(ADR-0008)")
        return "endpoints"
    check("kd manifest", t_kd_manifest)
    check("kd search 中文参数", lambda: expect(len(kd(["search", "信用额度控制", "--product", "0", "--size", "3"])["results"]) >= 1, "results≥1"))
    check("kd read knowledge", lambda: expect(len(kd(["read", KN_ID])["contentText"]) > 50, "官方文档全文"))
    check("kd read answer", lambda: expect(len(kd(["read", Q_ID, "--kind", "answer"])["answers"]) >= 1, "问答帖全文"))
    check("kd read article", lambda: expect(len(kd(["read", AR_ID, "--kind", "article"])["contentText"]) > 50, "社区文章全文"))

    def t_kd_bare_name():
        # 回归:agent 在 Git Bash 里敲裸名 `kd` 必须可解析。两个已知坑:
        # ① MSYS2 bash 不把裸名解析到 .cmd → 需要 cli/kd shim;
        # ② Windows 商店 python3.exe/python.exe 别名桩会被 command -v 命中却不可执行 → shim 内逐候选探测。
        bash = shutil.which("bash")
        expect(bash, "未找到 bash,跳过依据缺失")
        un = subprocess.run([bash, "-c", "uname -s"], capture_output=True, text=True, timeout=15).stdout.strip()
        if sys.platform == "win32" and un == "Linux":
            return "WSL bash,环境不同,跳过"
        kd_dir = os.path.dirname(os.path.abspath(KD_PY))
        r = subprocess.run([bash, "-c", "command -v kd >/dev/null && kd health >/dev/null && echo OK"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
                           env=dict(os.environ, PATH=kd_dir + os.pathsep + os.environ.get("PATH", "")))
        expect("OK" in r.stdout, (r.stdout[:80] or r.stderr[:80]) or "exit=%s" % r.returncode)
        return "via %s" % bash
    check("kd 裸名解析(bash shim)", t_kd_bare_name)

# ---- v6.2:合成权在调用方(ADR-0008)——kd ai 已删除,资料包须自带召回信号(ADR-0010) ----
if KD_PY:
    def kd_env(args, env, timeout=240):
        r = subprocess.run([PYTHON, KD_PY] + args, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout, env=env)
        expect(r.returncode == 0, "exit=%s stderr=%s" % (r.returncode, r.stderr[:160]))
        return json.loads(r.stdout)

    def t_ai_removed():
        r = subprocess.run([PYTHON, KD_PY, "ai", "任何问题"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=60,
                           env=dict(os.environ, KSEARCH_URL=B))
        expect(r.returncode == 2, "kd ai 应报用法错误(exit=2),实得 %s" % r.returncode)
        expect("invalid choice" in (r.stderr or ""), "stderr 应为 invalid choice")
    check("kd ai 已移除(ADR-0008)", t_ai_removed)

    def t_ask_brief_and_raw_route():
        # 两个断言组共用一次 ask:同一条上游调用跑两遍纯属浪费(各 300s 超时、各消耗一轮预算)
        d = kd_env(["ask", "BOM 分子分母", "--topk", "2"], dict(os.environ, KSEARCH_URL=B), timeout=300)
        b = d.get("synthesisBrief")
        expect(isinstance(b, dict), "synthesisBrief 缺失")
        # 客观召回信号必须齐:无信号则合成方只能凭正文猜,不算判定(ADR-0010)
        for k in ("sourceCount", "topScores", "routeKinds", "budgetExhausted", "recallHint"):
            expect(k in b, "synthesisBrief 缺字段 %s" % k)
        expect(isinstance(b["topScores"], list), "topScores 应为列表")
        rk = b.get("routeKinds") or []
        expect("raw:question" in rk, "routeKinds 应含原句路(ADR-0009),实得 %s" % rk)
        routes = d.get("routes") or []
        expect(routes and routes[0].get("kind") == "raw:question",
               "原句路应在首位(ADR-0009),实得 %s" % [r.get("kind") for r in routes[:3]])
        expect(int(routes[0].get("sortsType") or 0) == 1, "原句路 sortsType 应为 1")
        return "sources=%s routes=%d 首路=%s(st=%s)" % (b["sourceCount"], len(routes),
                                                       routes[0].get("kind"), routes[0].get("sortsType"))
    check("kd ask 的 synthesisBrief 与原句路首位(单次调用双断言)", t_ask_brief_and_raw_route)

    def t_kd_shim():
        """两个 shim(kd / kd.cmd)是平台分裂的产物,不是冗余:
           - Unix 系(含 Git Bash):裸名 `kd` 走无扩展名 bash shim,MSYS2 不会解析到 .cmd;
           - Windows cmd/PowerShell:不认她bang,只认扩展名,走 kd.cmd。
           本断言按平台只验本平台该走的那条;另一条在其他平台无意义,标 SKIP 而非 FAIL。"""
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if os.name == "nt":
            if not KD_CMD:
                raise Skipped("未安装 kd.cmd(Windows 下正常安装器会装)")
            # 真跑一次:验证探测逻辑挑得出解释器,而不是只判文件在不在
            r = subprocess.run(["cmd", "/c", KD_CMD, "health"], capture_output=True,
                               text=True, encoding="utf-8", errors="replace", timeout=60)
            expect(r.returncode == 0, "kd.cmd health exit=%s stderr=%s" % (r.returncode, (r.stderr or "")[:120]))
            expect('"service"' in (r.stdout or ""), "kd.cmd 未回出 JSON: %s" % (r.stdout or "")[:80])
            # 回归钉子:Python 路径不得再被写死(装 3.11/3.13 会静默失效)
            src = open(os.path.join(root, "cli", "kd.cmd"), encoding="utf-8").read()
            expect("Python312" not in src, "kd.cmd 又出现了硬编码 Python312,应走 py/python 探测")
            return "cmd.exe 真实调用通过,解释器为探测所得"
        shim = os.path.join(root, "cli", "kd")
        if not os.path.exists(shim):
            raise Skipped("cli/kd shim 不存在")
        r = subprocess.run(["bash", shim, "health"], capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=60)
        expect(r.returncode == 0, "cli/kd health exit=%s stderr=%s" % (r.returncode, (r.stderr or "")[:120]))
        expect('"service"' in (r.stdout or ""), "cli/kd 未回出 JSON: %s" % (r.stdout or "")[:80])
        return "bash shim 真实调用通过(kd.cmd 属 Windows,本平台跳过)"
    check("kd shim(本平台真实调用)", t_kd_shim)

# ---- 汇总 ----
fails = [r for r in RESULTS if not r[1]]
print("\n==== %d/%d PASS%s ====" % (len(RESULTS) - len(fails), len(RESULTS),
                                   (",%d SKIP" % len(SKIPPED)) if SKIPPED else ""))
for n, why in SKIPPED:
    print("  SKIP %s: %s" % (n, why))
sys.exit(1 if fails else 0)
