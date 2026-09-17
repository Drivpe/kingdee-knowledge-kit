#!/usr/bin/env python3
"""kd 回归用例集(工单 #21,去服务化重构后的回归基线)。

定位:证明「拆掉 HTTP 服务之后,以前能干的事现在还能干」——只断言**外部行为**
(kd.core 公开面返回结构 + kd 命令的进程级输出/退出码),不断言内部函数名、
模块结构、HTTP 状态码。

运行:
  python3 tests/kd_regression.py            # 离线组(默认;不联网,无网络也全绿)
  python3 tests/kd_regression.py --online    # 离线组 + 联网组(真实上游,保持人类频率)
  python3 tests/kd_regression.py --online --only-online   # 只跑联网组

退出码语义:
  0 = 全部通过
  1 = 有失败

历史上本套件区分过「已知 src 缺陷」与「真实回归失败」两类,并提供一个
`--allow-known-defect` 开关放行前者。该机制已随其唯一服务对象被移除:它记载的
`_Budget.require()`/`spend()` 两步非原子竞态已由工单 #29 修复(现为单个持锁的
`acquire()`,见 src/kd/_core_impl.py `_Budget`)。机制比缺陷活得久,会让退出码语义
继续宣告一个不存在的问题类别,故整体删除——每个失败都返回 1。

联网组单独归组、默认不跑:上游是真实网络请求(匿名链路,间隔 ≥1s),离线可验的
结构契约不应因为上游抖动而变红。

不碰 tests/verify_ksearch.py(旧套件保留到 #22)。
"""
import json
import os
import subprocess
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(REPO, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from kd import core  # noqa: E402

PY = sys.executable
RUN = os.path.join(SRC, "kd_run.py")

# ---------------------------------------------------------------- 契约基线
# ask 顶层实测 12 键 = 基线 11 键(ASK_KEYS)+ 新内核新增 synthesisBrief
# (新增字段不算等价失败,故不并入 ASK_KEYS,只在 check_no_extra 白名单里放行)。
#
# ⚠️ 实测不符:基线列的旧键表里 `semanticRerank` 在**新内核中不存在**。
# 该字段来自旧 HTTP 服务的 semantic_rerank.py(随去服务化整体删除,
# 见 docs/specs/2026-09-16-去服务化重构.md 决策 4)。故此处把
# `semanticRerank` 从"必须存在"降为"允许存在但不应出现"——它是**旧版残留字段**,
# 不是等价性要求。同理 `stats.pipeline` 也只在旧 HTTP v4 路径存在过。
ASK_KEYS = {"ok", "text", "total", "effectiveProductId", "routes", "queries", "sources",
            "budget", "budget_exhausted", "note", "stats"}
ASK_LEGACY_KEYS = {"semanticRerank"}   # 旧服务字段:新内核不产出,出现即异常
ASK_NEW_KEYS = {"synthesisBrief"}
BRIEF_KEYS = {"sourceCount", "topScores", "routeKinds", "routeCount", "budgetExhausted",
              "upstreamUsed", "upstreamMax", "recallHint"}
# routes[] 基线称 4 字段(kind/terms/why/sortsType)。实测只有原句路(raw:question)固定
# 携带 sortsType,其余路靠下游 `.get("sortsType") or 1` 兜底,字段**不存在**。
# 故 sortsType 定为条件字段,判据放在 productIds 同档;kind/terms/why 才是硬契约。
ROUTE_KEYS = {"kind", "terms", "why"}
ROUTE_OPT_KEYS = {"sortsType", "productIds"}
# sources[] 10 字段
SOURCE_KEYS = {"rank", "type", "id", "questionId", "title", "url", "snippet",
               "fusedScore", "products", "detail"}
# sources[].detail 16 字段(旧版 17,已摘 landing)。detail 是各 kind 的并集:
# 预算是"字段名必须落在白名单内 + 必含核心字段",因为 answer/knowledge 天然字段集不同。
DETAIL_BASE_KEYS = {"ok", "id", "type", "title", "contentText", "url", "products", "updatedAt"}
DETAIL_ALLOWED_KEYS = DETAIL_BASE_KEYS | {
    "chunks", "isSolved", "answersCount", "views", "rewardCoins", "createdAt",
    "bestAnswer", "answers", "truncated", "supports", "questionId", "adopted",
    "usefuls", "comments", "error"}
CHUNK_KEYS = {"seq", "heading", "text"}

# search 顶层 10 键;结果项 10 字段(并集:answer 条目额外带 questionId/questionBody/
# adopted/answersCount/comments,knowledge 带 useful,article 带 supports)
SEARCH_KEYS = {"ok", "text", "total", "queries", "page", "pageSize", "totalPages",
               "results", "scanNote", "stats"}
RESULT_BASE_KEYS = {"type", "id", "url", "title", "snippet", "products", "views",
                    "useful", "contentLen", "updatedAt"}
RESULT_ALLOWED_KEYS = (RESULT_BASE_KEYS - {"useful"}) | {
    "useful", "supports", "questionId", "questionBody", "adopted", "answersCount", "comments"}

# read:ok, id, type, title, contentText, url, products, updatedAt, stats(已摘 landing)
READ_KEYS = {"ok", "id", "type", "title", "contentText", "url", "products", "updatedAt", "stats"}

# stats 只断"旧 HTTP 路径字段不得回归"(pipeline);upstreamCalls/elapsedMs/rateProfile
# 由各用例就地断言。原 STATS_KEYS 常量定义了却从未被引用,属死常量,已删除。
KS_STATS_LEGACY = {"pipeline"}  # 旧 HTTP v4 路径字段,新内核不产出
BUDGET_KEYS = {"max", "used", "upstreamCalls"}

QUERY = "信用额度控制"
# 基线(工单 #21)记录 total=6199;本次实测上游已漂到 6198 且三次复现稳定。
# 定档为"稳定可复现 + 落在合理量级",不钉死绝对值——钉死会把上游语料变动
# 误报成回归失败。偏离超过阈值才需人工重新定档。
SEARCH_TOTAL_BASELINE = 6199
SEARCH_TOTAL_OBSERVED = 6198
SEARCH_TOTAL_DRIFT_TOL = 0.02  # 2%:语料增删的常态波动区间


# ---------------------------------------------------------------- 迷你断言框架
class Fail(Exception):
    pass


def ok(cond, msg):
    if not cond:
        raise Fail(msg)


def keys_of(d, name):
    ok(isinstance(d, dict), "%s 不是 dict: %r" % (name, type(d)))
    return set(d.keys())


def check_subset(actual, required, name):
    """required 必须全在 actual 里(允许 actual 有额外键:新增字段不算等价失败)。"""
    missing = set(required) - set(actual)
    ok(not missing, "%s 缺字段: %s (实有 %s)" % (name, sorted(missing), sorted(actual)))


def check_no_extra(actual, allowed, name):
    extra = set(actual) - set(allowed)
    ok(not extra, "%s 出现白名单外字段: %s" % (name, sorted(extra)))


def cli(*args, **kw):
    """进程级调用 kd 命令:返回 (exit_code, stdout_json_or_None, stderr_text)。"""
    p = subprocess.run([PY, RUN, *args], cwd=REPO, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=kw.get("timeout", 120))
    try:
        data = json.loads(p.stdout)
    except Exception:
        data = None
    return p.returncode, data, p.stderr


# ---------------------------------------------------------------- 用例注册
CASES = []


def case(name, online=False):
    def deco(fn):
        CASES.append((name, fn, online))
        return fn
    return deco


# ================= 离线组:结构契约(import 级) =================
@case("offline: 公开面恰为 ask/search/read + 三个异常类")
def t_public_surface():
    for n in ("ask", "search", "read", "QueryTooLong", "UpstreamError", "InternalError"):
        ok(hasattr(core, n), "kd.core 缺公开名 %s" % n)
    for n in ("ask", "search", "read"):
        ok(callable(getattr(core, n)), "%s 不可调用" % n)
    for n in ("QueryTooLong", "UpstreamError", "InternalError"):
        ok(isinstance(getattr(core, n), type) and issubclass(getattr(core, n), BaseException),
           "%s 不是异常类" % n)


@case("offline: 100 字硬闸 raise QueryTooLong(带 original/clamped/limit)")
def t_query_too_long_attrs():
    long_text = "超" * 120
    try:
        core.ask(long_text)
    except core.QueryTooLong as e:
        ok(len(e.original) == 120, "original 长度应为 120,实为 %d" % len(e.original))
        ok(len(e.clamped) == 100, "clamped 长度应为 100,实为 %d" % len(e.clamped))
        ok(int(e.limit) == 100, "limit 应为 100,实为 %r" % (e.limit,))
        ok(e.clamped == long_text[:100], "clamped 不是前 100 字符的压回值")
        return
    raise Fail("120 字符问句没有 raise QueryTooLong(旧服务是静默压回,本次重构是显式报错)")


@case("offline: 100 字硬闸同样作用于 search/显式 keywords")
def t_query_too_long_every_entry():
    long_text = "额" * 101
    for label, fn in (("search", lambda: core.search(long_text)),
                      ("ask.keywords", lambda: core.ask(keywords=[long_text]))):
        try:
            fn()
        except core.QueryTooLong as e:
            ok(len(e.clamped) == 100, "%s clamped 长度应为 100" % label)
            continue
        raise Fail("%s 未对 101 字符输入 raise QueryTooLong" % label)


@case("online: 长度边界两侧(100 放行 / 101、120 本地拦下)", online=True)
def t_length_boundary():
    # 只走公开面 ask/search,不引用 clamp_query 等内部名——内部件随重构收进
    # 私有命名空间,依赖内部名会把外部行为用例退化成结构断言。
    # 100 字符会真实打到上游,故本用例归联网组。
    try:
        core.search("a" * 100, page=1, page_size=1)
    except core.QueryTooLong:
        raise Fail("恰好 100 字符被硬闸误判为超限(应放行)")
    except core.UpstreamError:
        pass  # 上游业务错误与"本地硬闸误判"无关:已证明未被本地拦下
    for n in (101, 120):
        try:
            core.search("a" * n, page=1, page_size=1)
        except core.QueryTooLong as e:
            ok(len(e.clamped) == 100, "%d 字符的 clamped 长度应为 100" % n)
            continue
        except core.UpstreamError:
            raise Fail("%d 字符未被本地硬闸拦下而是打到上游(硬闸失效)" % n)
        raise Fail("%d 字符未触发 QueryTooLong" % n)


@case("offline: 空 text 与非法入参 raise InternalError(不是崩溃)")
def t_internal_error():
    for label, fn in (("search('')", lambda: core.search("")),
                      ("search(None)", lambda: core.search(None)),
                      ("ask()", lambda: core.ask()),
                      ("ask(keywords=[])", lambda: core.ask(keywords=[])),
                      ("search(bad type)", lambda: core.search(QUERY, type_="nope")),
                      ("read(bad kind)", lambda: core.read("nope", "1")),
                      ("read(no id)", lambda: core.read("knowledge", ""))):
        try:
            fn()
        except core.InternalError:
            continue
        except Exception as e:
            raise Fail("%s 抛的是 %s(应为 InternalError)" % (label, type(e).__name__))
        raise Fail("%s 未抛 InternalError" % label)


# ================= 离线组:CLI 进程级 =================
@case("offline: kd health 库模式自检(无服务/无端口)")
def t_cli_health():
    code, d, err = cli("health")
    ok(code == 0, "kd health 退出码 %r(应 0)" % code)
    ok(d is not None, "kd health stdout 不是合法 JSON")
    ks = keys_of(d, "health")
    check_subset(ks, {"ok", "service", "anonymous", "http", "noDiskWrite", "python",
                      "executable", "coreApi", "missingApi", "routesCfg", "routesCfgLoaded",
                      "maxRoutes", "deepReadTopK", "budgetMax", "rateProfile", "rerank",
                      "textMax", "commands", "note"}, "health 顶层")
    ok(d["ok"] is True, "health.ok 应为 True")
    ok(d["http"] is False, "去服务化后 http 必须为 False")
    ok(d["noDiskWrite"] is True, "内核应声明不落盘")
    ok(d["missingApi"] == [], "coreApi 有缺失: %r" % (d["missingApi"],))
    check_subset(set(d["coreApi"]), {"ask", "search", "read"}, "health.coreApi")
    ok(set(d["commands"]) == {"search", "read", "ask", "health"},
       "命令面应为 search/read/ask/health,实为 %r" % (d["commands"],))
    ok("share" not in d["commands"], "share 应已删除")
    ok(int(d["textMax"]) == 100, "health.textMax 应为 100")
    ok("4097" not in (d.get("note") or "") or "不依赖" in (d.get("note") or ""),
       "health.note 不应再承诺端口语义")


@case("offline: kd --help 列出四条子命令、share 不存在")
def t_cli_help():
    p = subprocess.run([PY, RUN, "--help"], cwd=REPO, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=60)
    ok(p.returncode == 0, "--help 退出码 %r" % p.returncode)
    for c in ("search", "read", "ask", "health"):
        ok(c in p.stdout, "--help 未列出子命令 %s" % c)
    ok("share" not in p.stdout, "--help 仍列出已删除的 share")
    code, d, _ = cli("share")
    ok(code != 0, "share 应已删除,实测退出码 %r" % code)


@case("offline: 100 字符硬闸 → exit 1 + error.code=query_too_long")
def t_cli_query_too_long():
    code, d, err = cli("ask", "超" * 120)
    ok(code == 1, "超长查询退出码 %r(应 1)" % code)
    ok(d is not None, "超长查询 stdout 不是合法 JSON")
    e = d.get("error")
    ok(isinstance(e, dict), "错误体缺 error 对象: %r" % (d,))
    ok(e.get("code") == "query_too_long", "错误 code 应为 query_too_long,实为 %r" % (e.get("code"),))
    ok(e.get("limit") == 100, "错误体 limit 应为 100,实为 %r" % (e.get("limit"),))
    ok(e.get("length") == 120, "错误体 length 应为 120,实为 %r" % (e.get("length"),))
    ok(len(e.get("clamped") or "") == 100, "错误体 clamped 长度应为 100")
    ok("hint" in e, "错误体缺 hint(既有契约:错误是带 hint 的 JSON)")


@case("offline: 用法错误 → exit 2(argparse),非法入参 → exit 1")
def t_cli_exit_codes():
    code, _, _ = cli("nosuchcmd")
    ok(code == 2, "未知子命令应 exit 2,实测 %r" % code)
    code, _, _ = cli("search")  # 缺必填 text
    ok(code == 2, "缺必填参数应 exit 2,实测 %r" % code)
    code, d, _ = cli("read", "1", "--kind", "nope")
    ok(code == 2, "非法 --kind 应 exit 2(argparse choices),实测 %r" % code)


@case("offline: stdout 只出 JSON,日志走 stderr")
def t_cli_stream_split():
    code, d, err = cli("health")
    ok(d is not None, "stdout 不是纯 JSON(混入了日志?)")
    ok(code == 0, "health 退出码 %r" % code)


@case("offline: 内核不做仓外落盘(无 landing 字段、无日志文件写盘)")
def t_no_landing_field():
    # 行为断言而非源码扫描:源码扫描会随内部重构(如命名空间隔离)假红。
    # landing 是旧服务的落地缓存字段,工单 #20 应已从对外返回结构中消失。
    import tempfile
    probe = os.path.join(tempfile.gettempdir(), "kd_reg_probe_%d" % os.getpid())
    before = set(os.listdir(tempfile.gettempdir()))
    # 用超长查询触发错误路径(零上游请求):内核不应留下任何落盘产物
    try:
        core.ask("超" * 120)
    except core.QueryTooLong:
        pass
    after = set(os.listdir(tempfile.gettempdir()))
    new = {x for x in (after - before) if not x.startswith("kd_reg_probe_")}
    ok(not new, "内核在临时目录留下落盘产物: %r" % (sorted(new)[:5],))
    ok(not os.path.exists(probe), "探测文件被创建(不应发生)")


# ================= 联网组:真实上游(默认不跑) =================
@case("online: search 顶层 10 键 + 结果项字段 + total 稳定(基线 6199,容差 2%)", online=True)
def t_search_contract():
    r = core.search(QUERY, product_id=93, page=1, page_size=5)
    ks = keys_of(r, "search")
    check_subset(ks, SEARCH_KEYS, "search 顶层")
    check_no_extra(ks, SEARCH_KEYS | {"text"}, "search 顶层")
    ok(r["ok"] is True, "search.ok 应为 True")
    ok(r["text"] == QUERY, "search.text 应回显查询词")
    ok(r["queries"] == [QUERY], "search.queries 应为 [查询词],实为 %r" % (r["queries"],))
    ok(r["page"] == 1 and r["pageSize"] == 5, "分页回显不符: %r/%r" % (r["page"], r["pageSize"]))
    check_subset(keys_of(r["stats"], "search.stats"), {"upstreamCalls", "elapsedMs"}, "search.stats")
    ok(isinstance(r["total"], int), "search.total 应为 int")
    # total 稳定性:同查询连续两次 total 必须一致(数值稳定是硬契约)
    r2 = core.search(QUERY, product_id=93, page=1, page_size=5)
    ok(r["total"] == r2["total"],
       "total 数值不稳定: 连续两次 %r vs %r" % (r["total"], r2["total"]))
    drift = abs(r["total"] - SEARCH_TOTAL_BASELINE) / float(SEARCH_TOTAL_BASELINE)
    ok(drift <= SEARCH_TOTAL_DRIFT_TOL,
       "total 基线漂移超 %.0f%%: 基线 %d 实得 %r(上游语料变动,需人工重新定档)"
       % (SEARCH_TOTAL_DRIFT_TOL * 100, SEARCH_TOTAL_BASELINE, r["total"]))
    if r["total"] != SEARCH_TOTAL_BASELINE:
        print("      注: total 偏离基线 %d → %r(在 %.0f%% 容差内,已按新观测重新定档)"
              % (SEARCH_TOTAL_BASELINE, r["total"], SEARCH_TOTAL_DRIFT_TOL * 100))
    ok(len(r["results"]) == 5, "page_size=5 应返回 5 条,实为 %d" % len(r["results"]))
    for i, it in enumerate(r["results"]):
        check_subset(keys_of(it, "results[%d]" % i), RESULT_BASE_KEYS - {"useful"},
                     "results[%d]" % i)
        check_no_extra(keys_of(it, "results[%d]" % i), RESULT_ALLOWED_KEYS,
                       "results[%d]" % i)
        ok(it["type"] in ("knowledge", "answer", "article"),
           "results[%d].type 非法: %r" % (i, it["type"]))
        ok(it["id"], "results[%d].id 为空" % i)


@case("online: search 三种实体全返回(type 字段区分)", online=True)
def t_search_all_types():
    seen = set()
    # 混排页:不保证三类型齐全,只做"混排可见合法类型"的弱断言
    r = core.search(QUERY, product_id=93, page=1, page_size=25)
    types = [x["type"] for x in r["results"]]
    ok(all(t in ("knowledge", "answer", "article") for t in types),
       "混排页出现非法 type: %r" % (sorted(set(types)),))
    # 定向过滤:每类型各用实测能命中的探测词。
    # 实测事实(2026-09-17):article 是长尾类型,泛词("信用额度"/"BOM"/"MRP")在该
    # 产品过滤下抽不到 article 条目,故用 "套打" 作为 article 探测词。
    # 注意 `total` 是**上游混合结果的总数,不是过滤后该类型的条数**
    # (例:type=article&text=信用额度 → total=74 但 results=[])。故此处不断言 total。
    probes = (("knowledge", QUERY), ("answer", "信用额度"), ("article", "套打"))
    for t, probe in probes:
        rr = core.search(probe, product_id=93, page=1, page_size=5, type_=t)
        ok(rr["ok"] is True, "type=%s 检索失败" % t)
        ok(rr["results"], "type=%s 检索零结果(探测词 %r 未命中;换词而非降级断言)" % (t, probe))
        ok(all(x["type"] == t for x in rr["results"]),
           "type=%s 过滤后混入其他类型: %r" % (t, [x["type"] for x in rr["results"]]))
        ok(rr["scanNote"] and ("type=%s" % t) in rr["scanNote"],
           "type=%s 应带该类型的 scanNote(跨页扫描说明),实为 %r" % (t, rr["scanNote"]))
        seen.add(t)
    ok(seen == {"knowledge", "answer", "article"}, "三种类型未全部验证")


@case("online: 空结果为 ok:true + total:0 + 空数组(绝非异常)", online=True)
def t_empty_result():
    r = core.search("zzqqxx不存在的词xyzzy777", product_id=93)
    ks = keys_of(r, "search")
    check_subset(ks, SEARCH_KEYS, "search 顶层")
    ok(r["ok"] is True, "空结果 ok 应为 True(不是异常)")
    ok(isinstance(r["total"], int), "空结果 total 应为 int")
    ok(isinstance(r["results"], list), "results 应为 list")
    ok(r["results"] == [] if r["total"] == 0 else True, "total=0 时 results 应为空数组")


@case("online: read 契约(9 键,已摘 landing)", online=True)
def t_read_contract():
    r = core.search(QUERY, product_id=93, page=1, page_size=5)
    kid = next((x["id"] for x in r["results"] if x["type"] == "knowledge"), None)
    ok(kid, "未取到 knowledge 条目 id")
    d = core.read("knowledge", kid)
    ks = keys_of(d, "read")
    check_subset(ks, READ_KEYS, "read 顶层")
    check_no_extra(ks, READ_KEYS | {"chunks", "views", "supports", "createdAt",
                                    "isSolved", "answersCount", "rewardCoins",
                                    "bestAnswer", "answers", "truncated", "questionId",
                                    "adopted", "usefuls", "comments"}, "read 顶层")
    ok("landing" not in ks, "read 仍返回 landing 字段(工单 #20 应已摘除)")
    ok(d["ok"] is True, "read.ok 应为 True")
    ok(d["type"] == "knowledge", "read.type 应为 knowledge")
    ok(str(d["id"]) == str(kid), "read.id 应回显请求 id")
    ok(isinstance(d["contentText"], str) and len(d["contentText"]) > 0,
       "contentText 为空(应含正文本)")
    ok(str(d["url"]).startswith("https://"), "read.url 不合规: %r" % (d["url"],))
    check_subset(keys_of(d["stats"], "read.stats"), {"upstreamCalls", "elapsedMs"}, "read.stats")


@case("online: ask 顶层 12 键 + synthesisBrief 8 字段", online=True)
def t_ask_contract():
    p = core.ask(QUERY, product_id=93, top_k=3)
    ks = keys_of(p, "ask")
    check_subset(ks, ASK_KEYS, "ask 顶层")
    check_no_extra(ks, ASK_KEYS | ASK_NEW_KEYS, "ask 顶层")
    # 旧服务残留字段:semanticRerank 随 semantic_rerank.py 一起删除,不应复活
    ok(not (ks & ASK_LEGACY_KEYS),
       "ask 出现旧服务字段 %r(去服务化决策 4 已删除 semantic_rerank)" % sorted(ks & ASK_LEGACY_KEYS))
    ok(p["ok"] is True, "ask.ok 应为 True")
    check_subset(keys_of(p["budget"], "ask.budget"), BUDGET_KEYS, "ask.budget")
    check_subset(keys_of(p["stats"], "ask.stats"), {"upstreamCalls", "elapsedMs"}, "ask.stats")
    ok(KS_STATS_LEGACY.isdisjoint(keys_of(p["stats"], "ask.stats")),
       "stats 出现旧 HTTP 路径字段 %r" % sorted(KS_STATS_LEGACY & keys_of(p["stats"], "ask.stats")))
    # 新内核新增字段:不算等价失败,但结构必须完整
    br = p.get("synthesisBrief")
    ok(isinstance(br, dict), "ask 缺 synthesisBrief")
    check_subset(keys_of(br, "synthesisBrief"), BRIEF_KEYS, "synthesisBrief")
    check_no_extra(keys_of(br, "synthesisBrief"), BRIEF_KEYS, "synthesisBrief")
    ok(isinstance(br["topScores"], list), "synthesisBrief.topScores 应为 list")
    ok(isinstance(br["routeKinds"], list), "synthesisBrief.routeKinds 应为 list")
    ok(br["sourceCount"] == len(p["sources"]),
       "synthesisBrief.sourceCount 应与 sources 数一致")
    ok(br["routeCount"] == len(p["routes"]), "synthesisBrief.routeCount 应与 routes 数一致")
    ok(br["upstreamUsed"] == p["budget"]["used"], "upstreamUsed 应与 budget.used 一致")
    ok(br["upstreamMax"] == p["budget"]["max"], "upstreamMax 应与 budget.max 一致")


@case("online: ask routes[] 硬契约 3 字段 + ≤7 路", online=True)
def t_ask_routes():
    p = core.ask(QUERY, product_id=93, top_k=2)
    ok(p["routes"], "ask 未产出任何路由")
    ok(len(p["routes"]) <= 7, "routes 路数 %d 超过 maxRoutes=7" % len(p["routes"]))
    for i, r in enumerate(p["routes"]):
        name = "routes[%d](kind=%s)" % (i, r.get("kind"))
        ks = keys_of(r, name)
        check_subset(ks, ROUTE_KEYS, name)              # kind/terms/why 是硬契约
        check_no_extra(ks, ROUTE_KEYS | ROUTE_OPT_KEYS, name)
        ok(str(r["terms"]).strip(), "%s.terms 为空" % name)
        if "sortsType" in ks:                            # 条件字段:出现则值必须合法
            ok(int(r["sortsType"]) in (0, 1, 2, 3), "%s.sortsType 非法" % name)
    # 原句路固定携带 sortsType=1(ADR-0009:相关性排序,保席位不被截断)
    raw = [r for r in p["routes"] if str(r.get("kind", "")).startswith("raw")]
    ok(raw, "未产出原句路(raw:question / raw)")
    ok(raw[0].get("sortsType") == 1,
       "原句路 sortsType 应为 1(固定相关性排序),实为 %r" % (raw[0].get("sortsType"),))
    # 显式关键词模式:每词一路,kind=explicit
    pk = core.ask(keywords=["信用额度控制", "应收单 信用"], product_id=93, top_k=1)
    ok([r["kind"] for r in pk["routes"]] == ["explicit", "explicit"],
       "显式关键词应各成一路,实为 %r" % ([r["kind"] for r in pk["routes"]],))


@case("online: ask sources[] 10 字段 + detail 16 字段 + chunks 3 字段(降级态另判)", online=True)
def t_ask_sources():
    p = core.ask(QUERY, product_id=93, top_k=3)
    ok(p["sources"], "ask 未产出任何 sources")
    ok(len(p["sources"]) <= 3, "top_k=3 应 ≤3 条 sources,实为 %d" % len(p["sources"]))
    for i, s in enumerate(p["sources"]):
        name = "sources[%d]" % i
        ks = keys_of(s, name)
        check_subset(ks, SOURCE_KEYS, name)
        check_no_extra(ks, SOURCE_KEYS, name)
        ok(s["rank"] == i + 1, "%s.rank 应为 %d,实为 %r" % (name, i + 1, s["rank"]))
        ok(s["type"] in ("knowledge", "answer", "article"), "%s.type 非法" % name)
        ok(isinstance(s["fusedScore"], (int, float)), "%s.fusedScore 应为数值" % name)
        d = s["detail"]
        dk = keys_of(d, "%s.detail" % name)
        if d.get("ok") is False:
            # 深读降级(预算耗尽或上游异常):_fetch_for_item 有意返回
            # {"ok": False, "error": ...} 两键字典,不是契约破坏。
            # 降级时只有"降级形态正确"可断言,其余契约字段本就不存在。
            ok("error" in dk, "%s.detail 降级却未带 error 字段" % name)
            check_no_extra(dk, {"ok", "error"}, "%s.detail(降级)" % name)
            continue
        check_subset(dk, DETAIL_BASE_KEYS, "%s.detail" % name)
        check_no_extra(dk, DETAIL_ALLOWED_KEYS, "%s.detail" % name)
        ok("landing" not in dk, "%s.detail 仍含 landing(工单 #20 应已摘除)" % name)
        if dk & (DETAIL_ALLOWED_KEYS - DETAIL_BASE_KEYS):
            pass  # kind 专属字段不构成失败
        for j, c in enumerate(d.get("chunks") or []):
            cn = "%s.detail.chunks[%d]" % (name, j)
            check_subset(keys_of(c, cn), CHUNK_KEYS, cn)
            check_no_extra(keys_of(c, cn), CHUNK_KEYS, cn)
            ok(isinstance(c["seq"], int) and c["seq"] >= 1, "%s.seq 应为 ≥1 的整数" % cn)
    # 展示排序:answer 优先、knowledge 紧随
    order = [s["type"] for s in p["sources"]]
    rank_of = {t: i for i, t in enumerate(order)}
    ok(order == sorted(order, key=lambda t: {"answer": 0, "knowledge": 1, "article": 2}.get(t, 9)),
       "sources 展示排序不符(answer 优先、knowledge 紧随): %r" % (order,))


@case("online: ask budget 契约(max 回显/used 与 upstreamCalls 一致/budget=0 零上游)", online=True)
def t_ask_budget():
    p = core.ask(QUERY, product_id=93, top_k=8, budget=4)
    b = p["budget"]
    ok(b["max"] == 4, "budget.max 应回显为 4,实为 %r" % (b["max"],))
    ok(b["upstreamCalls"] == b["used"], "budget.upstreamCalls 应与 used 一致")
    ok(isinstance(p["budget_exhausted"], bool), "budget_exhausted 应为 bool")
    if b["used"] >= b["max"]:
        ok(p["budget_exhausted"] is True, "用尽预算却未标 budget_exhausted")
        ok(p["synthesisBrief"]["budgetExhausted"] is True, "synthesisBrief 未同步预算状态")
    # 预算为 0:不得发起上游,且必须优雅返回(不是崩溃)
    p0 = core.ask(QUERY, product_id=93, top_k=1, budget=0)
    ok(p0["sources"] == [], "budget=0 不应产出 sources,实为 %d 条" % len(p0["sources"]))
    ok(p0["budget"]["used"] == 0, "budget=0 却消耗了上游请求")


@case("online: ask budget 并发正确性(max=2/3/4 深读下 used 不越限)", online=True)
def t_ask_budget_concurrency():
    """期望行为:`ask(top_k=8, budget=N)` 的 budget.used 恒不超过 max。

    覆盖场景:深读路径经 `ThreadPoolExecutor(max_workers=4)` 并发进入上游取数,
    多个线程同时竞争同一份预算名额。这里的断言是"并发下 max 仍是硬上限"。

    --- 历史注记(2026-09-17,工单 #29)---
    本用例原名 `[已知缺陷] … 被击穿`,当时断言的是同一个期望行为,但实现不满足:
    实测矩阵 max=1→used=1, max=2→used=2, max=3→used=6, max=4→used=6, max=6→used=9。
    根因是 `_Budget.require()`(检查 used>=max)与 `_Budget.spend()`(used+=1)为两步
    非原子操作且无锁,4 个线程可同时通过检查再各自自增,实际请求数溢出 (并发度-1) 次。

    修复手法:两者合并为单个持锁的 `acquire()`(检查+占用在同一临界区内完成,
    见 src/kd/_core_impl.py `_Budget`),配合编排层经 `remaining()`/`snapshot()`
    持锁读取,消除 check-then-act 竞态。修复后本用例转绿。

    保留注记的理由:用例名与断言现在描述"应该怎样",历史缺陷记录在案但不进入
    用例标识——否则已修复的问题会继续以用例名的形式对外宣告自己健在。
    """
    for maxv in (2, 3, 4):
        p = core.ask(QUERY, product_id=93, top_k=8, budget=maxv)
        used = p["budget"]["used"]
        ok(used <= maxv,
           "budget 硬上限被击穿: max=%d 实际 used=%d(超出 %d 次上游请求);"
           "深读路径经 ThreadPoolExecutor(max_workers=4) 并发取名额,"
           "预算领取必须是原子的(见 _Budget.acquire)"
           % (maxv, used, used - maxv))


@case("online: ask productId=0 与省略参数 —— 结果集等价、回显不等价", online=True)
def t_product_id_zero():
    # 结果集层:等价(上游必须省略 productIds[0] 参数,传 0 会被当真值过滤)
    a = core.search(QUERY, product_id=0, page=1, page_size=5)
    b = core.search(QUERY, product_id=None, page=1, page_size=5)
    ok(a["total"] == b["total"],
       "product_id=0 与省略的 total 不等价: %r vs %r" % (a["total"], b["total"]))
    ok([x["id"] for x in a["results"]] == [x["id"] for x in b["results"]],
       "product_id=0 与省略的结果 id 序列不等价")
    # 回显层:不等价,显式定档 —— 0 回显 0,省略回显 None
    p0 = core.ask(QUERY, product_id=0, top_k=1)
    pn = core.ask(QUERY, top_k=1)
    ok(p0["effectiveProductId"] == 0,
       "product_id=0 应回显 0(显式不过滤),实为 %r" % (p0["effectiveProductId"],))
    ok(pn["effectiveProductId"] is None,
       "省略 product_id 应回显 None(未指定),实为 %r" % (pn["effectiveProductId"],))
    # 未指定时若问句含产品别名,会自动推导(此处不带别名 → None)
    ok(a["total"] == b["total"], "结果集等价性在第二次对照中不成立")


@case("online: v4 管线(rerank=1)与兼容路径(rerank=0)都可跑通", online=True)
def t_rerank_paths():
    r0 = core.search(QUERY, product_id=93, page=1, page_size=5, rerank=False)
    ok(r0["ok"] is True, "rerank=0 失败")
    ok(r0["scanNote"] is None, "rerank=0 为 v3.2 兼容路径,scanNote 应为 None")
    r1 = core.search(QUERY, product_id=93, page=1, page_size=5, rerank=True)
    ok(r1["ok"] is True, "rerank=1(v4 管线)失败")
    ok(r1["scanNote"] and "v4" in r1["scanNote"].lower(),
       "rerank=1 应带 v4 管线 scanNote,实为 %r" % (r1["scanNote"],))
    check_subset(keys_of(r1, "search"), SEARCH_KEYS, "search 顶层(v4)")
    ok(r1["results"], "v4 管线零结果")


@case("online: kd search 进程级调用(退出码 0 + JSON 契约)", online=True)
def t_cli_search():
    code, d, err = cli("search", QUERY, "--product", "93", "--size", "5")
    ok(code == 0, "kd search 退出码 %r" % code)
    ok(d is not None, "kd search stdout 不是合法 JSON")
    check_subset(keys_of(d, "cli search"), SEARCH_KEYS, "cli search")
    ok(isinstance(d["total"], int) and d["total"] > 0, "CLI total 应为正整数,实为 %r" % (d["total"],))


@case("online: kd read 进程级调用", online=True)
def t_cli_read():
    code, d, _ = cli("search", QUERY, "--product", "93", "--size", "5")
    assert d is not None
    kid = next((x["id"] for x in d["results"] if x["type"] == "knowledge"), None)
    ok(kid, "CLI search 未取到 knowledge id")
    time.sleep(1.2)
    code, d2, _ = cli("read", kid)
    ok(code == 0, "kd read 退出码 %r" % code)
    ok(d2 is not None, "kd read stdout 不是合法 JSON")
    check_subset(keys_of(d2, "cli read"), READ_KEYS, "cli read")
    ok("landing" not in d2, "kd read 仍输出 landing")


@case("online: kd ask 进程级调用(资料包 + synthesisBrief)", online=True)
def t_cli_ask():
    code, d, err = cli("ask", QUERY, "--product", "93", "--topk", "2")
    ok(code == 0, "kd ask 退出码 %r" % code)
    ok(d is not None, "kd ask stdout 不是合法 JSON")
    ks = keys_of(d, "cli ask")
    check_subset(ks, ASK_KEYS, "cli ask")
    ok(not (ks & ASK_LEGACY_KEYS),
       "kd ask 输出旧服务字段 %r" % sorted(ks & ASK_LEGACY_KEYS))
    check_subset(keys_of(d.get("synthesisBrief") or {}, "cli ask.synthesisBrief"),
                 BRIEF_KEYS, "cli ask.synthesisBrief")
    ok(d["sources"], "kd ask 未产出 sources")


@case("online: kd ask --kw 显式关键词(跳过自动拆解)", online=True)
def t_cli_ask_kw():
    code, d, _ = cli("ask", "--kw", "信用额度控制", "--kw", "应收单 信用",
                     "--product", "93", "--topk", "1")
    ok(code == 0, "kd ask --kw 退出码 %r" % code)
    ok(d is not None, "kd ask --kw stdout 不是合法 JSON")
    ok([r["kind"] for r in d["routes"]] == ["explicit", "explicit"],
       "kd ask --kw 路由应为两条 explicit,实为 %r" % ([r["kind"] for r in d["routes"]],))


@case("online: kd ask 恰好 100 字符正常跑通", online=True)
def t_cli_exactly_100():
    # 上游 text 上限 100 原始字符:100 必须可跑,101 必须报错
    q100 = ("信用额度控制" * 17)[:100]
    ok(len(q100) == 100, "构造的 100 字符查询长度错误")
    code, d, _ = cli("ask", q100, "--topk", "1", "--budget", "3")
    ok(code == 0, "恰好 100 字符应跑通,实测退出码 %r / %r" % (code, d))
    ok(d is not None and d.get("ok") is True, "100 字符未返回正常资料包")


# ---------------------------------------------------------------- 执行器
def main(argv):
    import urllib.error
    online = "--online" in argv
    only_online = "--only-online" in argv
    chosen = [(n, f, on) for (n, f, on) in CASES if (on if only_online else (online or not on))]

    print("kd 回归用例集(工单 #21)")
    print("仓库: %s" % REPO)
    print("模式: %s" % ("联网组 %d 项(含离线)" % sum(1 for _, _, o in chosen if o)
                        if online else "离线组 %d 项(不联网)" % len(chosen)))
    print("-" * 72)

    passed, failed = [], []
    for name, fn, is_online in chosen:
        if is_online:
            time.sleep(1.2)  # 保持人类频率:联网用例之间 ≥1s
        t0 = time.time()
        try:
            fn()
        except Fail as e:
            failed.append((name, str(e)))
            print("FAIL  %s\n      %s" % (name, e))
        except urllib.error.URLError as e:
            failed.append((name, "上游不可达: %s" % e))
            print("FAIL  %s\n      上游不可达: %s" % (name, e))
        except core.UpstreamError as e:
            failed.append((name, "上游业务错误: %s" % e))
            print("FAIL  %s\n      上游业务错误: %s" % (name, e))
        except Exception as e:
            failed.append((name, "%s: %s" % (type(e).__name__, e)))
            print("ERROR %s\n      %s: %s" % (name, type(e).__name__, e))
        else:
            passed.append(name)
            print("ok    %s  (%.1fs)" % (name, time.time() - t0))

    print("-" * 72)
    print("通过 %d / 失败 %d" % (len(passed), len(failed)))
    if failed:
        print("失败用例:")
        for n, m in failed:
            print("  x %s: %s" % (n, m))
    # 任何失败都返回非零。曾有一个 --allow-known-defect 开关放行"已记录的 src 缺陷",
    # 它服务的那条缺陷(工单 #29 的预算竞态)已修复,故开关与 known/real 分类一并删除
    # ——保留会继续宣告一个不存在的问题类别,见本文件头部退出码说明。
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
