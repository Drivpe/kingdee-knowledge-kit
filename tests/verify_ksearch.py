#!/usr/bin/env python3
"""verify_ksearch.py — 金蝶知识服务+kd CLI 一键回归(改服务/CLI 后必跑)
路径全部参数化,双环境自适应:
  KSEARCH_URL  服务基址(默认 http://127.0.0.1:4097)
  KD_PY        kd.py 路径(默认依次找 %USERPROFILE%/.kingdee-kit/bin → %USERPROFILE%/.lingeebuild/bin)
用法: python verify_ksearch.py            # 全量(约 1 分钟)
退出码: 0=全部通过 1=有失败
"""
import json, os, subprocess, sys, time, urllib.request, urllib.error

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

def call_http(path, body=None, method=None, timeout=90):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(B + path, data=data,
                                 headers={"Content-Type": "application/json"} if data else {},
                                 method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))

def check(name, fn):
    try:
        detail = fn()
        RESULTS.append((name, True, detail))
        print("PASS  %s  %s" % (name, detail or ""))
    except Exception as e:
        RESULTS.append((name, False, str(e)[:160]))
        print("FAIL  %s  %s" % (name, str(e)[:160]))
    time.sleep(0.3)

def expect(cond, msg=""):
    if not cond:
        raise AssertionError(msg)
    return msg

# ---- 服务端点 ----
check("health", lambda: expect(call_http("/health").get("anonymous") is True, call_http("/health").get("service")))
check("manifest", lambda: expect(len(call_http("/manifest")["endpoints"]) >= 9
                                 and call_http("/manifest")["cli"]["commands"], "%d endpoints" % len(call_http("/manifest")["endpoints"])))

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
    check("kd manifest", lambda: expect(len(kd(["manifest"])["endpoints"]) >= 9, "endpoints"))
    check("kd search 中文参数", lambda: expect(len(kd(["search", "信用额度控制", "--product", "0", "--size", "3"])["results"]) >= 1, "results≥1"))
    check("kd question", lambda: expect(len(kd(["question", Q_ID])["answers"]) >= 1, "answers≥1"))
    if KD_CMD:
        check("kd.cmd 包装", lambda: expect(subprocess.run(["cmd", "/c", KD_CMD, "health"], capture_output=True,
              text=True, encoding="utf-8", errors="replace", timeout=60).returncode == 0, "exit=0"))

# ---- 汇总 ----
fails = [r for r in RESULTS if not r[1]]
print("\n==== %d/%d PASS ====" % (len(RESULTS) - len(fails), len(RESULTS)))
sys.exit(1 if fails else 0)
