#!/usr/bin/env python3
"""kd 回归用例集(单入口检索重构后重定档,2026-09-18)。

定位:证明「收敛成一条检索链路之后,以前能干的事现在还能干」——只断言**外部行为**
(kd.core 公开面返回结构 + kd 命令的进程级输出/退出码),不断言内部函数名、
模块结构、HTTP 状态码。唯一契约真源:
`docs/specs/2026-09-18-单入口检索重构.md`(尤其第 2、3、9 节)。

本轮改动(相对上一版 工单 #21 基线):
  * `ask` 及全部 `t_ask_*` 用例删除(用户 2026-09-18 拍板:留 search,ask 全删);
    连带删除 ASK_KEYS / ASK_NEW_KEYS / BRIEF_KEYS / SOURCE_KEYS / DETAIL_* /
    CHUNK_KEYS 等 ask 专属契约常量。
  * `search` 契约按 spec 第 3 节重写:顶层 **16 键**(新增 keywords / routesPlanned /
    routesDegraded;`effectiveProductId` 系执行期裁决补入,见 spec 第 3.3 节)、
    `results[]` 16 字段(新增 adopted / answersCount / comments / supports /
    questionBody);`contentText` / `fusedScore` / `chunks` / `contentLen` /
    `useful` **出现即 FAIL**。
  * 排序契约反转:`hitRoutes` 从"排序主键"**降级为纯信息字段**。内核只去重,
    顺序 = (首次出现的路序号, 该路内上游名次)。旧的 `t_search_manifest_order`
    (断言 hitRoutes 单调降序)与新契约直接冲突,已删除并替换为路序断言。
  * 新增硬证据用例:keywords 入口 / routesDegraded 塌缩 / `--global` 透传 /
    排序键不含命中路数 / `kd ask` 进程级 exit 2。
  * 离线内部件用例改走新包路径:实现体由模块 `kd._core_impl` 变为包 `kd._impl`,
    但观测口口径不变(`core._impl()` 仍返回承载实现的对象,内部件由其再导出)。

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
`acquire()`,见 src/kd/_impl/_config.py `_Budget`)。机制比缺陷活得久,会让退出码
语义继续宣告一个不存在的问题类别,故整体删除——每个失败都返回 1。

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
# 全部常量按 spec(2026-09-18 单入口检索重构)第 3 节冻结。任何一条与 spec 冲突
# 的改动都必须在 spec 里先落字,再改这里——不得只改测试。

# search 顶层 **16 键**(spec 第 3 节逐字段冻结)。
# 排序键 (首次路序, 路内名次) 使 `routesPlanned`/`routesDegraded` 成为新字段:
# 前者是"计划路数"(受 max_routes 截断后),后者是"路数塌缩"的事实表达(缺陷 C)。
# `effectiveProductId` 系**执行期裁决补入**(spec 第 3.3 节,2026-09-18):
# 本源设计漏列该键,走查发现它是真实功能回退——该键原为 ask 独有,ask 删除后
# 调用方失去唯一**可执行**的产品线判据(ANSWER-SPEC 第 8 条依赖它;它与 --product
# 同值域的整数,可直接比对;results[].products 是中文类目名,值域不可比、不能替代)。
SEARCH_KEYS = {"ok", "text", "keywords", "total", "queries", "routesPlanned",
               "routesDegraded", "effectiveProductId", "page", "pageSize", "totalPages",
               "results", "routeErrors", "budget_exhausted", "scanNote", "stats"}

# results[] 条目 16 字段(spec 第 3.1 节逐字段冻结)。
# `adopted`/`answersCount`/`comments`/`supports`/`questionBody` 是**上游原生信号**
# (由 `_norm_item` 算出,投影时透传),不是内核算的分——它们的存在不违反"零算法排序"。
RESULT_KEYS = {"type", "id", "title", "url", "hitRoutes", "routes", "questionId",
               "snippet", "products", "views", "updatedAt", "adopted",
               "answersCount", "comments", "supports", "questionBody"}

# 历史残留字段:清单是标题级投影,这些**出现即 FAIL**(spec 第 3.1 节末)。
#   contentText / contentLen —— 清单只给标题级信息,全文走 read;
#   fusedScore               —— 随 ask 一并删除的算法评分;
#   chunks                   —— 只服务 ask 深读;
#   useful                   —— 未列入清单字段。
RESULT_FORBIDDEN_KEYS = {"contentText", "fusedScore", "chunks", "contentLen", "useful"}

# read:ok, id, type, title, contentText, url, products, updatedAt, stats(已摘 landing)。
READ_KEYS = {"ok", "id", "type", "title", "contentText", "url", "products", "updatedAt", "stats"}
# read(answer) 专有字段(spec 第 4 节:answer 另带一组)。
# EXTRA = **可出现的键集**(白名单上界);REQUIRED = **恒在的键集**(必含下界)。
# 两者的差集 {questionId, bestAnswer, answers, truncated} 是条件字段:
#   bestAnswer/answers 依赖上游是否给了采纳回答/答案列表;
#   truncated 只在发生截断时置位;questionId 该路径从不返回(见用例内说明)。
READ_ANSWER_EXTRA_KEYS = {"questionId", "isSolved", "answersCount", "views", "rewardCoins",
                          "createdAt", "bestAnswer", "answers", "truncated",
                          "answersTaken", "answersTotal"}
READ_ANSWER_REQUIRED_KEYS = READ_ANSWER_EXTRA_KEYS - {"questionId", "bestAnswer",
                                                      "answers", "truncated"}

# stats 只断"旧 HTTP 路径字段不得回归"(pipeline);upstreamCalls/elapsedMs
# 由各用例就地断言。
KS_STATS_LEGACY = {"pipeline"}  # 旧 HTTP v4 路径字段,新内核不产出

QUERY = "信用额度控制"
# 基线(工单 #21)记录 total=6199;2026-09-18 单入口重构后实测 6326 且多次复现稳定。
# 定档为"稳定可复现 + 落在合理量级",不钉死绝对值——钉死会把上游语料变动
# 误报成回归失败。偏离超过阈值才需人工重新定档。
SEARCH_TOTAL_BASELINE = 6326
SEARCH_TOTAL_DRIFT_TOL = 0.02  # 2%:语料增删的常态波动区间

# 实证探针词(各用例共用,避免散落的魔法字符串):
#   塌缩实证:`信用额度控制` 的原句路与产品路拆出**逐字相同**的 terms(缺陷 C);
#   多路实证:`BOM 分母变平方` 稳定出 6 路,清单足够深、可分页、可有失败路。
PROBE_DEGRADED = "信用额度控制"
PROBE_MULTI_ROUTE = "BOM 分母变平方"


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


def check_no_forbidden(actual, forbidden, name):
    hit = set(actual) & set(forbidden)
    ok(not hit, "%s 出现已删除的历史残留字段 %s(spec 第 3.1 节:出现即 FAIL)"
       % (name, sorted(hit)))


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
@case("offline: 公开面恰为 search/read + 三个异常类,ask 不可属性访问")
def t_public_surface():
    for n in ("search", "read", "QueryTooLong", "UpstreamError", "InternalError"):
        ok(hasattr(core, n), "kd.core 缺公开名 %s" % n)
    for n in ("search", "read"):
        ok(callable(getattr(core, n)), "%s 不可调用" % n)
    for n in ("QueryTooLong", "UpstreamError", "InternalError"):
        ok(isinstance(getattr(core, n), type) and issubclass(getattr(core, n), BaseException),
           "%s 不是异常类" % n)
    # 本轮硬证据(spec 第 2.1 / 第 10 节):ask 从白名单移除后**必须不可属性访问**。
    # 这是回归钉子——防止将来把 ask 当成"顺手补回来"的入口重新挂上公开面。
    ok(hasattr(core, "ask") is False,
       "kd.core 仍可属性访问 ask(spec 第 2.1 节:ask 必须不可见)")
    ok("ask" not in list(getattr(core, "__all__", [])),
       "kd.core.__all__ 仍含 ask: %r" % (core.__all__,))
    ok(set(core.__all__) == {"search", "read", "QueryTooLong", "UpstreamError", "InternalError"},
       "kd.core.__all__ 应为 5 个公开名,实为 %r" % (core.__all__,))


@case("offline: 三个死形参已删除(rerank/routes/refresh 传即 TypeError)")
def t_dead_params_gone():
    """钉住本轮删掉的死形参:它们不是被忽略,而是**不存在**。

    旧 search 的 `rerank` / `routes` 与旧 read 的 `refresh` 都是收下后从不生效的
    幽灵参数(spec 第 2.2 节:多路清单路径下无任何行为 / 内核恒在线)。
    留着它们会让调用方以为传了有用——静默失效比报错更贵。
    注意 `routes`**改名**为 `max_routes`(旧名与返回结构的 results[].routes[]
    同名不同义),故旧名也必须 TypeError。
    """
    probes = (
        ("search(rerank=True)", lambda: core.search(QUERY, rerank=True)),
        ("search(routes=1)", lambda: core.search(QUERY, routes=1)),
        ("read(refresh=True)", lambda: core.read("knowledge", "1", refresh=True)),
    )
    for label, fn in probes:
        try:
            fn()
        except TypeError:
            continue
        except core.InternalError:
            raise Fail("%s 抛 InternalError 而非 TypeError(形参仍被接受)" % label)
        except Exception as e:
            raise Fail("%s 抛 %s(应为 TypeError:形参已删除)" % (label, type(e).__name__))
        raise Fail("%s 未报错——死形参仍在签名里(应已删除)" % label)


@case("offline: 100 字硬闸 raise QueryTooLong(带 original/clamped/limit)")
def t_query_too_long_attrs():
    long_text = "超" * 120
    try:
        core.search(long_text)
    except core.QueryTooLong as e:
        ok(len(e.original) == 120, "original 长度应为 120,实为 %d" % len(e.original))
        ok(len(e.clamped) == 100, "clamped 长度应为 100,实为 %d" % len(e.clamped))
        ok(int(e.limit) == 100, "limit 应为 100,实为 %r" % (e.limit,))
        ok(e.clamped == long_text[:100], "clamped 不是前 100 字符的压回值")
        return
    raise Fail("120 字符问句没有 raise QueryTooLong(旧服务是静默压回,本次重构是显式报错)")


@case("offline: 100 字硬闸同样作用于 search 的 text 与显式 keywords 两个入口")
def t_query_too_long_every_entry():
    long_text = "额" * 101
    for label, fn in (("search(text)", lambda: core.search(long_text)),
                      ("search(keywords=[…])", lambda: core.search(keywords=[long_text]))):
        try:
            fn()
        except core.QueryTooLong as e:
            ok(len(e.clamped) == 100, "%s clamped 长度应为 100" % label)
            continue
        raise Fail("%s 未对 101 字符输入 raise QueryTooLong" % label)


@case("offline: 空 text/非法入参 raise InternalError(不是崩溃)")
def t_internal_error():
    # `ask` 的两条入参分支已随 ask 删除;`search` 现为唯一入口,故空入参检查落在它身上。
    for label, fn in (("search('')", lambda: core.search("")),
                      ("search(None)", lambda: core.search(None)),
                      ("search('', keywords=[])", lambda: core.search("", keywords=[])),
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
                      "maxRoutes", "budgetMax", "rateProfile", "textMax",
                      "commands", "note"}, "health 顶层")
    ok(d["ok"] is True, "health.ok 应为 True")
    ok(d["http"] is False, "去服务化后 http 必须为 False")
    ok(d["noDiskWrite"] is True, "内核应声明不落盘")
    ok(d["missingApi"] == [], "coreApi 有缺失: %r" % (d["missingApi"],))
    check_subset(set(d["coreApi"]), {"search", "read"}, "health.coreApi")
    ok("ask" not in d["coreApi"], "health.coreApi 仍含 ask: %r" % (d["coreApi"],))
    # spec 第 5 节:health 的 commands 字段 → ["search", "read", "health"]。
    ok(set(d["commands"]) == {"search", "read", "health"},
       "命令面应为 search/read/health,实为 %r" % (d["commands"],))
    ok("ask" not in d["commands"], "ask 应已删除,health.commands 仍列出它")
    ok("share" not in d["commands"], "share 应已删除")
    ok(int(d["textMax"]) == 100, "health.textMax 应为 100")
    ok("4097" not in (d.get("note") or "") or "不依赖" in (d.get("note") or ""),
       "health.note 不应再承诺端口语义")


@case("offline: kd --help 列出三条子命令、ask 与 share 都不存在")
def t_cli_help():
    p = subprocess.run([PY, RUN, "--help"], cwd=REPO, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=60)
    ok(p.returncode == 0, "--help 退出码 %r" % p.returncode)
    for c in ("search", "read", "health"):
        ok(c in p.stdout, "--help 未列出子命令 %s" % c)
    ok("share" not in p.stdout, "--help 仍列出已删除的 share")
    # ask 已整条删除(不留 "unsupported" 占位)。子命令列表里不得出现它。
    ok("ask" not in p.stdout, "--help 仍列出已删除的 ask")
    code, d, _ = cli("share")
    ok(code != 0, "share 应已删除,实测退出码 %r" % code)


@case("offline: kd ask 进程级调用 → exit 2(argparse:子命令不存在)")
def t_cli_ask_gone():
    """本轮硬证据(spec 第 5 / 第 10 节):`ask` 子命令**真删除**,不留占位。

    退出码语义(spec 第 5 节:0/1/2 不变):
      2 = 用法错误。argparse 对"未知子命令"在**解析期**就退出,故走 2 而非 1。

    实测口径备注(与 spec 文本的差异,已记录不自行调和):argparse 的用法错误直接
    写 stderr,stdout 为空 —— 本项目"错误是带 hint 的 JSON"这条只覆盖**运行期**
    错误(由 cli._fail/_usage_error 产出)。故此处断言的是:
      ① 退出码恒为 2;② stdout **不得**出现任何成功载荷(ok:true / results);
      ③ stderr 有内容(用法提示不会静默)。
    `kd nosuchcmd` 走同一路径,一并断言两者等价,证明 ask 是真的不存在、
    而不是被特判成了一个"看起来像错误"的分支。
    """
    code, d, err = cli("ask", QUERY)
    ok(code == 2, "kd ask 退出码应为 2(argparse 未知子命令),实测 %r" % code)
    ok(err and err.strip(), "kd ask 的 stderr 为空:用法提示被吞掉了")
    if d is not None:
        ok(d.get("ok") is not True, "kd ask 竟然返回了成功载荷: %r" % (d,))
        ok(not d.get("results"), "kd ask 竟然返回了结果清单")
    code2, d2, _ = cli("nosuchcmd")
    ok(code2 == code, "kd ask 的退出码(%r)应与未知子命令(%r)一致——ask 未被特判" % (code, code2))
    # 子命令确实不在 parser 的可选集里(直接读 --help 的用法行,不猜)。
    p = subprocess.run([PY, RUN, "ask"], cwd=REPO, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=60)
    ok("invalid choice" in p.stderr or "invalid choice" in p.stdout,
       "kd ask 未报 invalid choice(说明 ask 仍被 parser 接受): %r" % (p.stderr[:200],))


@case("offline: 100 字符硬闸 → exit 1 + error.code=query_too_long")
def t_cli_query_too_long():
    code, d, err = cli("search", "超" * 120)
    ok(code == 1, "超长查询退出码 %r(应 1)" % code)
    ok(d is not None, "超长查询 stdout 不是合法 JSON")
    e = d.get("error")
    ok(isinstance(e, dict), "错误体缺 error 对象: %r" % (d,))
    ok(e.get("code") == "query_too_long", "错误 code 应为 query_too_long,实为 %r" % (e.get("code"),))
    ok(e.get("limit") == 100, "错误体 limit 应为 100,实为 %r" % (e.get("limit"),))
    ok(e.get("length") == 120, "错误体 length 应为 120,实为 %r" % (e.get("length"),))
    ok(len(e.get("clamped") or "") == 100, "错误体 clamped 长度应为 100")
    ok("hint" in e, "错误体缺 hint(既有契约:错误是带 hint 的 JSON)")


@case("offline: 用法错误 → exit 2(argparse + 内核入参);非法入参 → exit 1")
def t_cli_exit_codes():
    code, _, _ = cli("nosuchcmd")
    ok(code == 2, "未知子命令应 exit 2,实测 %r" % code)
    # search 的 text 现为 nargs="?"(只给 --kw 时合法),故"缺 text"不再由 argparse 拦,
    # 而由内核 raise InternalError → cli._usage_error 映射为 exit 2(spec 第 5 节)。
    code, _, _ = cli("search")
    ok(code == 2, "既无 text 又无 --kw 应 exit 2,实测 %r" % code)
    code, d, _ = cli("read", "1", "--kind", "nope")
    ok(code == 2, "非法 --kind 应 exit 2(argparse choices),实测 %r" % code)


@case("offline: stdout 只出 JSON,日志走 stderr")
def t_cli_stream_split():
    code, d, err = cli("health")
    ok(d is not None, "stdout 不是纯 JSON(混入了日志?)")
    ok(code == 0, "health 退出码 %r" % code)


@case("offline: 内核不做仓外落盘(无 landing 字段、无日志文件写盘)")
def t_no_landing_field():
    # 行为断言而非源码扫描:源码扫描会随内部重构(如命名空间隔离/拆包)假红。
    # landing 是旧服务的落地缓存字段,工单 #20 应已从对外返回结构中消失。
    import tempfile
    probe = os.path.join(tempfile.gettempdir(), "kd_reg_probe_%d" % os.getpid())
    before = set(os.listdir(tempfile.gettempdir()))
    # 用超长查询触发错误路径(零上游请求):内核不应留下任何落盘产物
    try:
        core.search("超" * 120)
    except core.QueryTooLong:
        pass
    after = set(os.listdir(tempfile.gettempdir()))
    new = {x for x in (after - before) if not x.startswith("kd_reg_probe_")}
    ok(not new, "内核在临时目录留下落盘产物: %r" % (sorted(new)[:5],))
    ok(not os.path.exists(probe), "探测文件被创建(不应发生)")


# ================= 离线组:多路清单去重与排序(合成条目,不打上游) =================
def _impl():
    """内部观测口:实现包的内部件(去重键/排序/投影/拆词)。

    这些件是**有意的**内部件(不在 kd.core 公开面),用观测口取而非从 kd.core
    摸私有属性——与 cli.cmd_health 取数的口径一致。

    包路径变更(2026-09-18):实现体由模块 `kd._core_impl` 拆成包 `kd._impl`,
    但**口径不变**——`core._impl()` 仍返回承载实现的对象(现为包),内部件由其
    再导出。故本套件不 import `kd._impl.*` 子模块,只走观测口。
    """
    return core._impl()


def _syn(et, i, qid=None, title=None, adopted=False):
    """合成一个上游形态条目(用于喂 _norm_item),字段形状对齐实测响应。"""
    if et == "answer":
        return {"entity-type": "answer", "id": str(i), "questionId": str(qid or i),
                "highlight": {"question.title": title or ("问题标题 %s" % i),
                              "description": "回答正文 %s" % i},
                "question": {"id": str(qid or i), "answers": 2, "moduleName": "财务云",
                             "description": "问题正文 %s" % i},
                "isAdopt": "true" if adopted else "false", "views": 10,
                "comments": 3, "contentLen": 100, "updatedAt": "2026-01-01"}
    if et == "article":
        return {"entity-type": "article", "id": str(i),
                "highlight": {"title": title or ("文章标题 %s" % i), "content": "正文 %s" % i},
                "classifies": [{"name": "星空旗舰版"}], "views": 10, "supports": 5,
                "contentLen": 100, "updatedAt": "2026-01-01"}
    return {"entity-type": "knowledge", "id": str(i), "knowledgeId": str(i),
            "highlight": {"title": title or ("知识标题 %s" % i), "content": "正文 %s" % i},
            "classifies": [{"name": "星空旗舰版"}], "views": 10, "useful": 1,
            "contentLen": 100, "updatedAt": "2026-01-01"}


@case("offline: 多路去重 —— 同一 answer 被两路命中只出一条且 hitRoutes=2")
def t_manifest_dedupe_hit_routes():
    cp = _impl()
    a = cp._norm_item(_syn("answer", 900, qid=800, title="同一帖回答"), "answer")
    k = cp._norm_item(_syn("knowledge", 100), "knowledge")
    # 路1: [answer, knowledge];路2: [answer]  —— answer 被两路命中
    keys, hits, _first = cp._manifest_fuse([(1, [a, k]), (2, [a])])
    ok(len(keys) == 2, "两条不同条目应去重为 2 条,实为 %d" % len(keys))
    ak = cp._manifest_key(a)
    ok(ak in hits, "answer 条目未进命中表")
    ok(len(hits[ak]) == 2, "answer 被两路命中,hitRoutes 应为 2,实为 %d" % len(hits[ak]))
    proj = cp._manifest_project(a, hits[ak])
    ok(proj["hitRoutes"] == 2, "投影后的 hitRoutes 应为 2")
    ok(proj["routes"] == [1, 2], "投影后的 routes 应为 [1,2],实为 %r" % (proj["routes"],))
    ok(proj["title"] == "同一帖回答", "标题应透传")
    ok("contentText" not in proj, "清单条目不得返回 contentText(要全文走 read)")
    # knowledge 只命中 1 路
    kk = cp._manifest_key(k)
    ok(len(hits[kk]) == 1, "knowledge 应只命中 1 路,实为 %d" % len(hits[kk]))
    # 排序:本条不再是"命中路数多者优先"(见下条用例),此处只钉两种命中态都被记下。
    ok(set(hits[ak]) == {1, 2}, "answer 的命中路集应为 {1,2},实为 %r" % (sorted(hits[ak]),))


@case("offline: 排序键不含命中路数 —— 路序靠前者恒在前(本轮核心行为变更)")
def t_manifest_order_route_first():
    """**本轮硬证据**(spec 第 3.2 节第 4 条 + 第 10 节)。

    排序键只有 `(首次出现的路序号, 该路内上游名次)` 两维,**第一维不是命中路数**。

    之所以必须反转:`hitRoutes` 是本内核**算出来的量**,按它排序等于在官方综合排序
    之上再叠一层我们自己的权重。用户 2026-09-18 拍板「不使用算法排分,只去重」,
    故命中路数降级为纯信息字段。

    构造(两条对照,方向相反,防止"恰好通过"):
      * 甲:命中 1 路、但**路序靠前**(路1 第 1 名);
      * 乙:命中 2 路、但**首次出现路序靠后**(路1 第 2 名,路2 第 1 名)。
    新契约下甲的排序键 (1,1) < 乙的 (1,2) → 甲在前。旧契约(-len(hits) 优先)
    会判乙在前——故本用例能区分新旧两种实现。
    """
    cp = _impl()
    a = cp._norm_item(_syn("knowledge", 11, title="甲:单路命中但路序靠前"), "knowledge")
    b = cp._norm_item(_syn("knowledge", 22, title="乙:双路命中但路序靠后"), "knowledge")
    keys, hits, first = cp._manifest_fuse([(1, [a, b]), (2, [b])])

    ka, kb = cp._manifest_key(a), cp._manifest_key(b)
    ok(len(hits[ka]) == 1, "甲应命中 1 路,实为 %d" % len(hits[ka]))
    ok(len(hits[kb]) == 2, "乙应命中 2 路,实为 %d" % len(hits[kb]))
    ok(first[ka] == (1, 1), "甲首次出现应为路1第1名,实为 %r" % (first[ka],))
    ok(first[kb] == (1, 2), "乙首次出现应为路1第2名,实为 %r" % (first[kb],))
    ok(keys[0] == ka,
       "路序靠前者应排在前:命中 1 路的甲 (1,1) 却排在命中 2 路的乙 (1,2) 之后——"
       "排序键仍在按命中路数打分(spec 第 3.2 节:第一维必须不是 len(hits))。实得顺序 %r"
       % (keys,))

    # 排序键的第二条构造:命中路数多的那个**路序确实更靠前**,此时它理应在最前
    # ——证明本用例不是"永远让命中少者在前",而是真的按路序。
    c = cp._norm_item(_syn("knowledge", 33, title="丙:双路命中且路序最靠前"), "knowledge")
    d = cp._norm_item(_syn("knowledge", 44, title="丁:单路命中但路序靠后"), "knowledge")
    keys2, hits2, first2 = cp._manifest_fuse([(1, [c]), (2, [c, d])])
    ok(len(hits2[cp._manifest_key(c)]) == 2, "丙应命中 2 路")
    ok(first2[cp._manifest_key(c)] == (1, 1), "丙首次出现应为 (1,1)")
    # 丁未在路1 出现,首次落在路2 的第 2 名(丙占了路2 第 1 名)。
    ok(first2[cp._manifest_key(d)] == (2, 2), "丁首次出现应为 (2,2)")
    ok(keys2[0] == cp._manifest_key(c), "丙路序 (1,1) 最靠前,应排首位,实得 %r" % (keys2,))

    # 纯函数性质:排序键的两维都不含命中路数(直接问 _manifest_rank)。
    rank = cp._manifest_rank(hits, first)
    probe = cp._manifest_key(a)
    ok(len(rank(probe)) == 2,
       "排序键应为 2 维 (路序, 路内名次),实为 %d 维: %r" % (len(rank(probe)), rank(probe)))
    ok(rank(probe) == (1, 1), "甲的排序键应为 (1,1),实为 %r" % (rank(probe),))
    ok(not any("score" in str(k).lower() for k in keys), "清单排序不得引入分数键")


@case("offline: answer 双 id 空间 —— 同一帖的不同回答不被合并")
def t_manifest_answer_id_space():
    cp = _impl()
    # 同一 questionId(800)下的两条**不同回答**(id 901/902)
    a1 = cp._norm_item(_syn("answer", 901, qid=800, title="帖子标题"), "answer")
    a2 = cp._norm_item(_syn("answer", 902, qid=800, title="帖子标题"), "answer")
    ok(a1["questionId"] == a2["questionId"] == "800", "构造数据应同帖")
    ok(cp._manifest_key(a1) != cp._manifest_key(a2),
       "去重键必须按回答 id 区分:两条不同回答得到同一个键 %r" % (cp._manifest_key(a1),))
    ok(cp._manifest_key(a1) == "answer:901", "answer 去重键应为 answer:<回答id>,实为 %r" % (cp._manifest_key(a1),))
    # 两路各自命中其中一条:清单必须是 2 条,而不是被并成 1 条
    keys, hits, _first = cp._manifest_fuse([(1, [a1]), (2, [a2])])
    ok(len(keys) == 2, "同帖不同回答应各自成条(2 条),实为 %d 条" % len(keys))
    # 同一条被两路命中:仍只 1 条
    keys2, hits2, _f2 = cp._manifest_fuse([(1, [a1]), (2, [a1])])
    ok(len(keys2) == 1, "同一回答被两路命中应合并为 1 条,实为 %d 条" % len(keys2))
    ok(len(hits2[cp._manifest_key(a1)]) == 2, "hitRoutes 应为 2")


@case("offline: 清单投影字段集固定(spec 16 字段;无 contentText/fusedScore/chunks)")
def t_manifest_projection():
    cp = _impl()
    for et in ("knowledge", "answer", "article"):
        n = cp._norm_item(_syn(et, 7), et)
        ok(n is not None, "%s 合成条目未被 _norm_item 接受(构造数据形状不对)" % et)
        p = cp._manifest_project(n, {1, 2})
        ks = set(p.keys())
        # 16 字段**逐个**要求存在(spec 第 3.1 节逐字段冻结:不是"抽查几个")。
        missing = RESULT_KEYS - ks
        ok(not missing, "%s 条目投影缺字段 spec 要求: %s" % (et, sorted(missing)))
        # 且不得多出白名单外的键。
        extra = ks - RESULT_KEYS
        ok(not extra, "%s 条目投影出现白名单外字段: %s" % (et, sorted(extra)))
        # 历史残留字段出现即 FAIL。
        check_no_forbidden(ks, RESULT_FORBIDDEN_KEYS, "%s 条目投影" % et)
        ok(p["hitRoutes"] == 2 and p["routes"] == [1, 2], "%s 投影的命中信息不符" % et)
    # 上游原生信号透传(不是内核算的):answer 的这三项必须从 _norm_item 带出来。
    a = cp._norm_item(_syn("answer", 900, qid=800, adopted=True), "answer")
    pa = cp._manifest_project(a, {1})
    ok(pa["adopted"] is True, "answer 投影应透传 adopted=True,实为 %r" % (pa["adopted"],))
    ok(pa["answersCount"] == 2, "answer 投影应透传 answersCount=2,实为 %r" % (pa["answersCount"],))
    ok(pa["comments"] == 3, "answer 投影应透传 comments=3,实为 %r" % (pa["comments"],))
    ok(pa["questionBody"] == "问题正文 900",
       "answer 投影应透传 questionBody(来源是 question.description),实为 %r" % (pa["questionBody"],))
    # article 的 supports 同理。
    art = cp._norm_item(_syn("article", 5), "article")
    ok(cp._manifest_project(art, {1})["supports"] == 5, "article 投影应透传 supports")


@case("offline: 标题解析降级链 —— 纯高亮壳/空缺/点号键都不产生静默空标题")
def t_title_of_fallbacks():
    cp = _impl()
    # ① 点号同层键(上游 answer 的真实形态)优先
    ok(cp._title_of("预算模板使用<em>状态</em>显示禁用", None) == "预算模板使用状态显示禁用",
       "点号键应被解析并剥掉高亮标签")
    # ② 首候选为"存在但空串"时,不得挡住后面有货的候选(silent None 的根源)
    ok(cp._title_of("", "真标题") == "真标题", "空串候选应被跳过而非短路")
    ok(cp._title_of(None, "真标题") == "真标题", "None 候选应被跳过")
    # ③ 纯高亮标签壳:html2text 剥标签后仍有文本 → 非空
    ok(cp._title_of("<em>禁用</em>") == "禁用", "纯标签壳应剥出文本")
    # ④ 全空 → None(不是空串:契约里字段可为 None,但不得是"" 这种"看起来有值"的形态)
    ok(cp._title_of(None, "", "   ") is None, "全空候选应返回 None")
    # ⑤ answer 条目走真实 _norm_item:标题必须非空
    a = cp._norm_item(_syn("answer", 900, qid=800, title="帖子标题"), "answer")
    ok(a["title"] == "帖子标题", "answer 条目标题解析失败: %r" % (a["title"],))
    # ⑥ 上游把标题平铺在条目顶层(assistant 形状变体)也要兜住
    raw = {"entity-type": "answer", "id": "1", "questionId": "2",
           "highlight": {}, "question": {"id": "2"}, "title": "平铺标题"}
    ok(cp._norm_item(raw, "answer")["title"] == "平铺标题", "顶层平铺标题未被兜住")


# ---------- 离线组:本轮新增的入口/事实字段(打桩上游,不联网) ----------
def _patched_search(monkey_args, **kw):
    """把 `_search_upstream` 换成记录器后调 `core.search`,返回 (结果, 收到的实参表)。

    注入点:`kd._impl._search_upstream`(所有路最终都经它)。
    为什么能打桩:包内的跨模块调用经包命名空间间接解析(见 _impl/_manifest._resolve),
    故替换包属性对已 import 的调用方同样生效——这与拆分前的单文件行为等价。
    """
    cp = _impl()
    real = cp._search_upstream
    seen = []

    def fake(text, product_id, page, page_size, global_, sorts_type, type_, budget=None, rate=None):
        seen.append({"text": text, "product_id": product_id, "page": page,
                     "page_size": page_size, "global_": global_, "sorts_type": sorts_type,
                     "type_": type_})
        return {"content": [], "totalElements": 0, "totalPages": 0}

    cp._search_upstream = fake
    try:
        return core.search(*monkey_args, **kw), seen
    finally:
        cp._search_upstream = real


@case("offline: keywords 入口 —— 每词一路、text 为 None、queries 回显关键词")
def t_keywords_entry():
    """spec 第 2.2 节新增 `keywords` 的理由:打通「LLM 拆词」缺口。

    调用方在**调用层**把问题拆成关键词后传进来,内核永不含 LLM 调用。
    契约:每词一路 → `queries` 逐词回显;`text` 为 None;`keywords` 回显原列表。
    """
    r, seen = _patched_search((), keywords=["A", "B"], product_id=93, budget=10)
    ok(r["ok"] is True, "keywords 入口失败")
    ok(r["text"] is None, "只给 keywords 时 search.text 应为 None,实为 %r" % (r["text"],))
    ok(r["keywords"] == ["A", "B"], "search.keywords 应回显调用方给的关键词,实为 %r" % (r["keywords"],))
    ok(r["queries"] == ["A", "B"],
       "每个关键词应各成一路且顺序保持,实为 %r" % (r["queries"],))
    ok(r["routesPlanned"] == 2, "计划路数应为 2,实为 %r" % (r["routesPlanned"],))
    ok(r["routesDegraded"] is False, "两词不同,不应塌缩")
    ok([c["text"] for c in seen] == ["A", "B"],
       "上游实际收到的检索词应与 keywords 一致,实为 %r" % ([c["text"] for c in seen],))
    ok(all(c["product_id"] == 93 for c in seen),
       "显式关键词路也必须携带 productIds(否则 --kw 会绕过 --product 造成串线): %r"
       % ([c["product_id"] for c in seen],))

    # keywords 与 text 并用:text 回显、keywords 也回显(入参契约允许并用)。
    r2, _ = _patched_search((QUERY,), keywords=["X"], product_id=93, budget=10)
    ok(r2["text"] == QUERY, "text+keywords 并用时 text 应回显,实为 %r" % (r2["text"],))
    ok(r2["keywords"] == ["X"], "text+keywords 并用时 keywords 应回显,实为 %r" % (r2["keywords"],))

    # 未给 keywords 时该字段为 None(不是空数组——空数组会被读成"给了空关键词")。
    r3, _ = _patched_search((QUERY,), product_id=93, budget=10)
    ok(r3["keywords"] is None, "未给 keywords 时 search.keywords 应为 None,实为 %r" % (r3["keywords"],))


@case("offline: --kw 模式原句路恒常补入(用户 2026-09-27 拍板:默认就是要原句的)")
def t_keywords_raw_route():
    """`--kw` 分支此前**整条原句路消失**(2026-09-18 记录,2026-09-27 修)。

    旧:`_plan_routes(keywords=[…])` 直接把 text 丢掉,只留回显——于是
    "我给整句 + 我同时给拆好的词"这种最自然的用法反而丢了原句路,而原句路恰是
    ADR-0009 实测最值钱的一路。用户裁决:**默认就是要原句的**,不留开关。

    新契约(两条分支各验一次):
      * `text` 非空 → text 充任第 1 路(原句路),其余关键词依次成 explicit 路;
      * `text` 为空 → **第 1 个关键词**充任原句路(故不再另占 explicit 路:
        路数不变、queries 逐字不变,只有第 1 路的 kind/sortsType 变精确)。
    """
    # (a) 只给 keywords:第 1 个关键词升格为原句路,路数与 queries 都不变。
    r, seen = _patched_search((), keywords=["甲", "乙", "丙"], product_id=93, budget=10)
    ok(r["queries"] == ["甲", "乙", "丙"],
       "只给 keywords 时 queries 应逐词保持原序(第 1 词改充原句路,不增不减): %r"
       % (r["queries"],))
    ok(len(seen) == 3, "只给 3 个 keywords 应恰好 3 路(不因补原句路而多出一路): %r"
       % ([c["text"] for c in seen],))
    ok(seen[0]["sorts_type"] == 1,
       "第 1 个关键词充任原句路后必须用 sortsType=1(相关性),实为 %r"
       % (seen[0]["sorts_type"],))

    # (b) text + keywords 并用:text 充任原句路(第 1 路),keywords 随后。
    r2, seen2 = _patched_search(("整句报错串",), keywords=["甲", "乙"], product_id=93, budget=10)
    ok(r2["queries"] == ["整句报错串", "甲", "乙"],
       "text+keywords 并用时第 1 路必须是 text(原句路),实为 %r" % (r2["queries"],))
    ok(len(seen2) == 3, "text+2 个 keywords 应恰 3 路,实为 %r"
       % ([c["text"] for c in seen2],))
    ok(seen2[0]["sorts_type"] == 1,
       "原句路必须固定 sortsType=1,实为 %r" % (seen2[0]["sorts_type"],))
    ok(r2["text"] == "整句报错串", "text 仍应回显,实为 %r" % (r2["text"],))
    ok(r2["keywords"] == ["甲", "乙"], "keywords 仍应回显原列表,实为 %r" % (r2["keywords"],))

    # (c) text 与某关键词逐字相同时不得产生两条同词路(去重兜住)。
    r3, seen3 = _patched_search(("整句报错串",), keywords=["整句报错串", "乙"],
                                product_id=93, budget=10)
    ok(len(set(r3["queries"])) == len(r3["queries"]),
       "text 与关键词重复时不得出现同词两路(塌缩去重必须兜住): %r" % (r3["queries"],))


@case("offline: --global 透传 —— _search_upstream 收到的 global_ 与调用方一致")
def t_global_passthrough():
    """**本轮硬证据**(spec 第 2.3 节缺陷 A,真 bug 的回归钉子)。

    旧实现里 `_search_manifest(global_=…)` 收到该参数后**从未下传**,
    `_route_search_once` 内部把 `global_` 硬编码为 `False`。后果:CLI 的
    `--global`(跨全部产品)**静默无效**——传了等于没传,且没有任何报错。

    本用例用 monkeypatch 抓住上游出口实际收到的 `global_` 实参,断言与调用方一致。
    单路与多路都要验:多路下"每路都透传"才是修好的口径。
    """
    # global_=True,单路(信用额度控制 去重后 1 路)
    r, seen = _patched_search((PROBE_DEGRADED,), product_id=93, global_=True, budget=10)
    ok(seen, "上游未被调用,无法校验 global_ 透传")
    ok(all(c["global_"] is True for c in seen),
       "global_=True 未透传到上游(缺陷 A 复发): %r" % ([c["global_"] for c in seen],))

    # global_=True,多路(BOM 分母变平方 稳定 6 路)——每一路都必须带上
    r2, seen2 = _patched_search((PROBE_MULTI_ROUTE,), product_id=93, global_=True, budget=10)
    ok(len(seen2) >= 2, "本用例需要多路输入(该探针词实测 ≥2 路),实为 %d 路" % len(seen2))
    ok(all(c["global_"] is True for c in seen2),
       "多路下 global_ 未逐路透传: %r" % ([c["global_"] for c in seen2],))

    # 默认 False 与显式 False 都必须透传成假值(不得被写成字符串 "false"/None)。
    _r3, seen3 = _patched_search((PROBE_DEGRADED,), product_id=93, budget=10)
    ok(all(c["global_"] is False for c in seen3),
       "默认 global_ 应为假值,实为 %r" % ([c["global_"] for c in seen3],))

    # CLI 侧同一条链:--global 必须真的到上游(端到端,不打桩)。
    # 这里只查 CLI 有没有把 flag 接上(离线可判:patch 后跑 cli 的进程内路径太绕,
    # 故用 parser 级断言 + 上面的核心断言组合)。
    import kd.cli as _cli
    a = _cli.build_parser().parse_args(["search", QUERY, "--global"])
    ok(a.global_ is True, "cli --global 未绑定到 global_ 属性(实为 %r)" % (a.global_,))


@case("offline: routesDegraded —— 拆出同一串词时置 true 并写明塌缩")
def t_routes_degraded():
    """**本轮硬证据**(spec 第 2.3 节缺陷 C)。

    实证:`信用额度控制` 的原句路与产品/上下文词路拆出的 terms **逐字相同**,
    不在执行链去重就是对同一 query 发两次上游请求——既浪费预算,又会让该词条目的
    `hitRoutes` 虚高为 2(它其实只被一路的检索意图覆盖)。

    契约:去重后实际路数 < 计划路数 → `routesDegraded is True`,且 `scanNote`
    写明「路数塌缩 N→M」。**这是表达事实,不是评分**。
    """
    r, seen = _patched_search((PROBE_DEGRADED,), product_id=93, budget=10)
    ok(r["routesPlanned"] >= 2,
       "本用例需要该探针词拆出 ≥2 路(实测 2 路),实为 %r" % (r["routesPlanned"],))
    ok(r["routesDegraded"] is True,
       "`%s` 的原句路与产品路拆出同一串词,去重后路数 < 计划路数,"
       "routesDegraded 应置 true,实为 %r" % (PROBE_DEGRADED, r["routesDegraded"]))
    ok(len(r["queries"]) < r["routesPlanned"],
       "塌缩时实际路数(%d)应小于计划路数(%d)" % (len(r["queries"]), r["routesPlanned"]))
    ok(len(seen) == len(r["queries"]),
       "实际上游请求数(%d)应等于去重后路数(%d)——塌缩路不得真的发请求"
       % (len(seen), len(r["queries"])))
    ok("塌缩" in r["scanNote"],
       "scanNote 应写明路数塌缩事实,实为 %r" % (r["scanNote"],))

    # 反向对照:多路探针词各路的 terms 互不相同 → 不塌缩。
    r2, _ = _patched_search((PROBE_MULTI_ROUTE,), product_id=93, budget=10)
    ok(r2["routesDegraded"] is False,
       "`%s` 各路 terms 互不相同,不应报塌缩,实为 %r" % (PROBE_MULTI_ROUTE, r2["routesDegraded"]))
    ok(r2["routesPlanned"] == len(r2["queries"]),
       "不塌缩时计划路数应与实际路数相等: %r vs %r"
       % (r2["routesPlanned"], len(r2["queries"])))


def _expect_upstream(effective):
    """`effectiveProductId`(调用方词汇)→ 上游实际收到的 product_id。

    唯一映射规则:0 与 None **都**表示"不带产品过滤",故上游一律省略该参数(None)。
    传 `productIds[0]=0` 会被上游当真值过滤(实测把 Knowledge 挤出前排)——
    这正是"不过滤"与"过滤到 0 号产品"的语义分界。
    """
    return None if effective in (0, None) else effective


@case("offline: effectiveProductId 回显**推导后**实际生效值(默认93/苍穹87/显式0)")
def t_effective_product_id_derived():
    """**本轮第 6 条硬证据**(spec 第 3.3 节)。

    `effectiveProductId` 经裁决补回顶层(spec 第 3.3 节),它存在的全部理由就是
    ANSWER-SPEC 第 8 条的那句「引用与合成前核对产品线」。因此它的语义是硬约束:

      **必须回显「推导后实际生效」的值,不得回显调用方传入的原始值。**

    为什么这条断言值得单列:`_plan_routes` 会在问句里出现产品别名(「苍穹」等)时
    **覆盖**默认 93。若实现图省事写成"原样回显入参",三态里的第二态(87)就会退化成
    93 —— 调用方拿它去核对产品线会**得到错误的安心感**,而这正是 2026-09-06 旗舰版
    串线事故的形态。这种回归在联网用例里也难发现(默认态 93 恰好正确),故用离线
    三态钉死。

    三态实证(2026-09-18 实测):
      ① 默认(不带 product_id)     → 93(旗舰版,默认值)
      ② 问句含「苍穹」             → 87(别名**覆盖**默认)
      ③ 显式 product_id=0          → 0(真不过滤;唯一能拿到不过滤的方式)
    另补第 ④ 态:显式 None → None(未带任何过滤),与 0 值域区分。

    纯离线:monkeypatch `_search_upstream` 拦掉上游出口,零网络请求。

    ⚠️ monkeypatch 陷阱(实测结论,别再踩):本实现里有**两种**解析方式,
    换错地方会静默失效(负控跑出假绿):
      · `_search_upstream` 走 `_manifest._resolve()` → 解析点是**包命名空间**,
        故替换 `kd._impl._search_upstream`(包属性)**有效**(`_patched_search` 用这条);
      · `_search_manifest` 在 `_public` 里是 **import 期局部绑定**,
        故替换包属性或 `_manifest` 子模块属性**都无效**,必须改 `kd._impl._public`
        的同名全局才能让负控生效。
    本用例只 patch 前者。已用负控验证区分度:把 `effectiveProductId` 改成
    "原样回显入参"后,第②态(苍穹→87)立刻红并报出实得 93。
    """
    # 注:第①态是"省略 product_id 参数"(而非省略 text)—— text 与 keywords
    # 二选一是另一条契约(search 至少要有一个检索词,否则 InternalError)。
    cases = (
        ("默认(省略 --product)", (PROBE_DEGRADED,), {}, 93),
        ("问句含「苍穹」(别名覆盖默认)", ("苍穹 " + PROBE_DEGRADED,), {}, 87),
        ("显式 product_id=0(真不过滤)", (PROBE_DEGRADED,), {"product_id": 0}, 0),
        ("显式 product_id=None(未带过滤)", (PROBE_DEGRADED,), {"product_id": None}, None),
    )
    for label, args, kw, want in cases:
        kw = dict(kw, budget=10)
        r, seen = _patched_search(args, **kw)
        got = r["effectiveProductId"]
        ok(got == want,
           "%s: effectiveProductId 应为推导后实际生效值 %r,实为 %r\n"
           "      注:回显调用方入参而非推导结果会让「别名覆盖默认」不可见——"
           "该键存在的唯一理由就是让调用方核对产品线(spec 第 3.3 节)"
           % (label, want, got))
        # 与上游实际收到的过滤保持一致:回显值必须**真的是**本次生效的过滤,
        # 否则调用方核对的是个装饰性字段。
        #
        # ⚠️ 两套值域**有意不同**,不是脱节(2026-09-18 实测确认):
        #   `effectiveProductId` 用**调用方词汇**(--product 值域):0 是"我显式要求不过滤";
        #   而上游只认"带 productIds[0] 过滤"或"省略该参数",故 0 与 None 都必须
        #   省略参数(传 `productIds[0]=0` 上游会当真值过滤,把结果挤出前排)。
        # 故映射是 `0 → 省略(None)`,而非逐字相等。这条映射本身就是契约,
        # 由下面的 `_expect_upstream` 显式写出,避免用"相等"这个错误口径去测它。
        for c in seen:
            ok(c["product_id"] == _expect_upstream(want),
               "%s: 回显 effectiveProductId=%r,其对应的上游过滤应为 %r,实收 %r"
               % (label, got, _expect_upstream(want), c["product_id"]))

    # 反向钉子:别名推导不得只是"巧合等于默认"——两个产品线的值必须不同,
    # 否则上表第 ①②态本就会同时通过,断言失去区分度。
    ok(cases[0][3] != cases[1][3], "本用例需要「默认」与「别名」两个值不同,否则无区分度")
    # 值域契约:整数或 None(与 --product 同值域,可直接比对),
    # 而不是字符串/中文类目名——那正是 results[].products 不可替代的原因。
    r, _ = _patched_search((PROBE_DEGRADED,), budget=10)
    ok(not isinstance(r["effectiveProductId"], str),
       "effectiveProductId 不得是字符串(须与 --product 同值域以便直接比对)")
    # keywords 入口同样带回推导值(显式关键词路径与规则路径同权携带 productIds)。
    rk, seenk = _patched_search((), keywords=["A"], product_id=87, budget=10)
    ok(rk["effectiveProductId"] == 87, "keywords 入口应回显 87,实为 %r" % (rk["effectiveProductId"],))
    ok(all(c["product_id"] == 87 for c in seenk), "keywords 路的过滤应与回显一致")


# ================= 联网组:真实上游(默认不跑) =================
@case("online: 长度边界两侧(100 放行 / 101、120 本地拦下)", online=True)
def t_length_boundary():
    # 只走公开面 search,不引用 clamp_query 等内部名——内部件随重构收进
    # 私有命名空间/拆包,依赖内部名会把外部行为用例退化成结构断言。
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


@case("online: search 顶层 16 键(含 effectiveProductId)+ 结果项 16 字段 + 无历史残留字段", online=True)
def t_search_contract():
    r = core.search(QUERY, product_id=93, page=1, page_size=5)
    ks = keys_of(r, "search")
    # 顶层键集按 spec 第 3 节**逐字段冻结**,故这里严格到"不得多出一个键"。
    # 原"三方冲突"告示已于 2026-09-18 撤销:`effectiveProductId` 经裁决**保留**
    # (spec 第 3.3 节),故它已并入 SEARCH_KEYS,不再是白名单外字段。
    check_subset(ks, SEARCH_KEYS, "search 顶层")
    check_no_extra(ks, SEARCH_KEYS, "search 顶层")
    ok(len(ks) == 16,
       "search 顶层应为 16 键(spec 第 3 节),实为 %d 键: %s" % (len(ks), sorted(ks)))
    ok(isinstance(r["effectiveProductId"], (int, type(None))), 
       "effectiveProductId 应为 int 或 None(与 --product 同值域),实为 %r" 
       % (r["effectiveProductId"],))
    ok(r["effectiveProductId"] == 93,
       "默认(不带 --product)时 effectiveProductId 应回显 93(旗舰版),实为 %r"
       % (r["effectiveProductId"],))
    ok(r["ok"] is True, "search.ok 应为 True")
    ok(r["text"] == QUERY, "search.text 应回显查询词")
    ok(r["keywords"] is None, "未给 keywords 时 search.keywords 应为 None")
    ok(r["queries"] and r["queries"][0] == QUERY,
       "search.queries 首元素应为原句路(查询词),实为 %r" % (r["queries"],))
    ok(len(r["queries"]) <= 7, "路数 %d 超过 maxRoutes=7" % len(r["queries"]))
    ok(len(set(r["queries"])) == len(r["queries"]),
       "queries 出现重复检索词(同一请求被发两次,塌缩去重失效): %r" % (r["queries"],))
    # 本轮新增的两个路数字段(spec 第 3 节)。
    plan = r["routesPlanned"]
    ok(isinstance(plan, int) and 1 <= plan <= 7,
       "routesPlanned 应为 1..7 的整数(计划路数),实为 %r" % (plan,))
    ok(isinstance(r["routesDegraded"], bool), "routesDegraded 应为 bool")
    # 塌缩的定义式:实际路数 < 计划路数 ↔ routesDegraded 为真(双向都要成立)。
    ok(r["routesDegraded"] == (len(r["queries"]) < plan),
       "routesDegraded(%r)与「实际路数(%d) < 计划路数(%d)」不一致"
       % (r["routesDegraded"], len(r["queries"]), plan))
    ok(r["page"] == 1 and r["pageSize"] == 5, "分页回显不符: %r/%r" % (r["page"], r["pageSize"]))
    ok(isinstance(r["totalPages"], int), "totalPages 应为 int(清单总页数),实为 %r" % (r["totalPages"],))
    check_subset(keys_of(r["stats"], "search.stats"), {"upstreamCalls", "elapsedMs"}, "search.stats")
    ok(KS_STATS_LEGACY.isdisjoint(keys_of(r["stats"], "search.stats")),
       "stats 出现旧 HTTP 路径字段 %r" % sorted(KS_STATS_LEGACY & keys_of(r["stats"], "search.stats")))
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
        name = "results[%d]" % i
        kk = keys_of(it, name)
        # 16 字段:必含硬契约字段(views/snippet 可为 None 但键必须在——spec 逐字段冻结)。
        check_subset(kk, RESULT_KEYS, name)
        check_no_extra(kk, RESULT_KEYS, name)
        check_no_forbidden(kk, RESULT_FORBIDDEN_KEYS, name)
        ok(it["type"] in ("knowledge", "answer", "article"),
           "%s.type 非法: %r" % (i, it["type"]))
        ok(it["id"], "%s.id 为空" % i)
        ok(it["title"], "%s.title 为空(清单核心交付物是标题)" % name)
        ok(isinstance(it["hitRoutes"], int) and it["hitRoutes"] >= 1,
           "%s.hitRoutes 应为 ≥1 的整数,实为 %r" % (name, it.get("hitRoutes")))
        ok(isinstance(it["routes"], list) and it["routes"],
           "%s.routes 应为非空 list" % name)
        ok(len(it["routes"]) == it["hitRoutes"],
           "%s.routes 长度(%d)应等于 hitRoutes(%d)" % (name, len(it["routes"]), it["hitRoutes"]))
        ok(all(isinstance(x, int) and x >= 1 for x in it["routes"]),
           "%s.routes 元素应为 ≥1 的整数" % name)
        if it["type"] == "answer":
            ok(it["questionId"], "%s 是 answer 却缺 questionId(无法定位问题帖)" % name)
            # answer 专有原生信号:键必须存在(answer 帖必有回答数/采纳标记)。
            ok("adopted" in kk and "answersCount" in kk,
               "%s 是 answer 却缺 adopted/answersCount 原生信号" % name)
        else:
            # 非 answer 条目没有这些字段的来源,值应为 None(键在、值为 None)。
            ok(it["adopted"] is None and it["questionId"] is None,
               "%s 是非 answer 条目,adopted/questionId 应为 None" % name)


@case("online: 清单顺序按路序 —— 首次出现的路序号单调不减(排序键第一维)", online=True)
def t_search_manifest_order_by_route():
    """**替换掉旧的 `t_search_manifest_order`**(旧断言 hitRoutes 单调降序)。

    旧断言与本轮契约直接冲突:`hitRoutes` 已从排序主键**降级为纯信息字段**,
    清单不再按命中路数排。保留它就是钉住一个已被用户拍板撤销的行为。

    新断言把一个**可观测的量**钉住:`min(routes)` 就是"首次出现的路序号"
    (`routes` 是升序的路号集,首元素即该条首次被哪一路召回)。排序键第一维是它,
    故清单里它必须单调不减。这条断言对实现的约束是实打实的:任何"按分数/命中路数
    重排"的改动都会打乱它。
    """
    r = core.search(PROBE_MULTI_ROUTE, product_id=93, page=1, page_size=30)
    ok(len(r["queries"]) >= 3,
       "本用例需要多路输入(实测该探针词 ≥3 路),实为 %d 路" % len(r["queries"]))
    firsts = [min(x["routes"]) for x in r["results"]]
    ok(firsts == sorted(firsts),
       "清单未按「首次出现的路序号」升序(排序键第一维): %r\n"
       "      注:排序键应为 (首次路序, 路内名次),不含 hitRoutes 长度" % (firsts,))
    # 首条必须来自第 1 路(原句路恒为第 1 路,路序最靠前)。
    ok(firsts and firsts[0] == 1, "清单首条应来自第 1 路,实为 %r" % (firsts[:1],))
    # 说明:**不断言** hitRoutes 的单调形态。上游数据里"恰好逆序"可以合法出现
    # (排序键是路序,不是命中路数;两者无需一致),拿一个统计巧合当判据会假红。
    # "hitRoutes 不是排序主键"由离线合成用例
    # (`排序键不含命中路数 —— 路序靠前者恒在前`)确定性证明,并已用负控验证
    # (把旧实现 -len(hits) 塞回去 → 该用例立刻变红)。


@case("online: 清单分页是清单口径 —— 每路上游固定 10 条,page_size 切清单", online=True)
def t_search_manifest_paging():
    # 用稳定产出多路的探针词:`信用额度控制` 去重后只剩 1 路(原句路与产品路同词),
    # 清单天然只有 10 条,深页无意义。`BOM 分母变平方` 稳定出 6 路,清单足够深。
    r1 = core.search(PROBE_MULTI_ROUTE, product_id=93, page=1, page_size=5)
    ok(len(r1["results"]) <= 5, "page_size=5 应 ≤5 条,实为 %d" % len(r1["results"]))
    ok(len(r1["queries"]) >= 2, "本用例需要多路输入(实测该探针词 ≥2 路),实为 %d 路"
       % len(r1["queries"]))
    r2 = core.search(PROBE_MULTI_ROUTE, product_id=93, page=2, page_size=5)
    ids1 = [x["id"] for x in r1["results"]]
    ids2 = [x["id"] for x in r2["results"]]
    ok(not (set(ids1) & set(ids2)),
       "清单第 1/2 页出现重复条目: %r" % (sorted(set(ids1) & set(ids2)),))


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
        time.sleep(1.2)
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


@case("online: read 契约(9 键,已摘 landing;无 refresh)", online=True)
def t_read_contract():
    r = core.search(QUERY, product_id=93, page=1, page_size=5)
    kid = next((x["id"] for x in r["results"] if x["type"] == "knowledge"), None)
    ok(kid, "未取到 knowledge 条目 id")
    time.sleep(1.2)
    d = core.read("knowledge", kid)
    ks = keys_of(d, "read")
    check_subset(ks, READ_KEYS, "read 顶层")
    check_no_extra(ks, READ_KEYS, "read 顶层")
    ok("landing" not in ks, "read 仍返回 landing 字段(工单 #20 应已摘除)")
    ok("chunks" not in ks, "read 返回 chunks(spec:随 ask 删除)")
    ok("refresh" not in ks, "read 返回 refresh(spec:该形参已删除)")
    ok(d["ok"] is True, "read.ok 应为 True")
    ok(d["type"] == "knowledge", "read.type 应为 knowledge")
    ok(str(d["id"]) == str(kid), "read.id 应回显请求 id")
    ok(isinstance(d["contentText"], str) and len(d["contentText"]) > 0,
       "contentText 为空(应含正文本)")
    ok(str(d["url"]).startswith("https://"), "read.url 不合规: %r" % (d["url"],))
    check_subset(keys_of(d["stats"], "read.stats"), {"upstreamCalls", "elapsedMs"}, "read.stats")


@case("online: read(answer) 契约 —— 键集/id 语义/截断信号", online=True)
def t_read_answer_contract():
    """answer 路径此前零覆盖(t_read_contract 与 t_cli_read 都只测 knowledge),
    而它的返回形态与 knowledge 差异最大:多 answers/bestAnswer/isSolved 等,
    且带 answersTaken/answersTotal 截断信号。基线从未描述过它,上游改字段没有
    任何断言会发现。

    同时钉住 id 语义:answer 只认 questionId(search 条目的 `id` 是回答 id,
    拿去请求 /api/questions/{id} 必 404),故显式用 questionId 读取。
    """
    r = core.search(QUERY, product_id=93, page=1, page_size=10)
    item = next((x for x in r["results"] if x["type"] == "answer"), None)
    ok(item, "未取到 answer 条目")
    qid = item.get("questionId")
    ok(qid, "answer 条目缺 questionId(无法定位问题帖)")
    time.sleep(1.2)
    d = core.read("answer", qid)
    ks = keys_of(d, "read(answer)")
    # 核心键必须齐:这是 answer 路径与 knowledge 路径的形态差异所在。
    # ⚠️ spec 第 4 节把 answer 的附加字段列成一串(questionId/isSolved/…/truncated),
    # 但那是**可出现的键集**,不是"必须全在"的键集——实证两条(spec 说 read「不变」,
    # 故口径应与拆分前逐字一致):
    #   * `questionId`:**该路径从不返回**。answer 详情的 id 就是入参 questionId,
    #     用它自己的 id 再回显一个 questionId 是同值重复;拆分前亦无此键。
    #   * `truncated`:**条件字段**,只在发生截断时置位(_question_detail 里三处
    #     `truncated = True`)。未截断就不该出现——正因如此它才有信号价值。
    # 故 required = 恒在的核心键;allowed = 可出现的全集。
    check_subset(ks, READ_KEYS | READ_ANSWER_REQUIRED_KEYS, "read(answer) 顶层")
    check_no_extra(ks, READ_KEYS | READ_ANSWER_EXTRA_KEYS, "read(answer) 顶层")
    ok(d["ok"] is True, "read(answer).ok 应为 True")
    ok(d["type"] == "answer", "read(answer).type 应为 answer")
    ok(str(d["id"]) == str(qid), "read(answer).id 应回显传入的 questionId")
    ok("landing" not in ks, "read(answer) 仍返回 landing 字段")
    ok("chunks" not in ks, "read(answer) 返回 chunks(spec:随 ask 删除)")
    ok("questionId" not in ks,
       "read(answer) 返回了 questionId:该路径的 id 就是 questionId,回显同值是冗余"
       "(拆分前亦无此键;spec 第 4 节列的是可出现键集,不是必含键集)。实有: %r" % (sorted(ks),))

    # 截断信号:已取/总数必须都是 int,且已取 <= 总数。
    # 此前 max_answer_pages 造成的截断走正常退出、不置 truncated,调用方零信号。
    ok(isinstance(d.get("answersTaken"), int), "answersTaken 应为 int,实为 %r" % (d.get("answersTaken"),))
    ok(isinstance(d.get("answersTotal"), int) or d.get("answersTotal") is None,
       "answersTotal 应为 int 或 None,实为 %r" % (d.get("answersTotal"),))
    taken = d.get("answersTaken")
    total = d.get("answersTotal")
    if isinstance(total, int) and total >= 0:
        ok(taken <= total, "answersTaken(%r) 不应大于 answersTotal(%r)" % (taken, total))
    ok(isinstance(d.get("answers"), list), "answers 应为 list")
    ok(len(d["answers"]) == taken, "answers 长度(%d)应等于 answersTaken(%d)"
       % (len(d["answers"]), taken))
    # 逐个回答条目:硬契约字段
    for a in d["answers"]:
        aks = keys_of(a, "answer 条目")
        check_subset(aks, {"id", "adopted", "contentText"}, "answer 条目")
        ok("chunks" not in aks, "answer 条目返回 chunks(spec:随 ask 删除)")
    check_subset(keys_of(d["stats"], "read(answer).stats"),
                 {"upstreamCalls", "elapsedMs"}, "read(answer).stats")


@case("online: read 的 answer 路径只认 questionId(传回答 id 应失败)", online=True)
def t_read_answer_id_semantics():
    """钉住 id 语义单一口径:search 条目的 `id` 是回答 id,不是问题 id。

    此前 `_fetch_for_item` 有 `questionId or id` 兜底,公开的 read 路径没有,
    同一输入两处行为相反。兜底已移除,这里守住"传回答 id 必须失败而不是静默读错帖"。
    """
    r = core.search(QUERY, product_id=93, page=1, page_size=10)
    item = next((x for x in r["results"] if x["type"] == "answer"), None)
    ok(item, "未取到 answer 条目")
    aid, qid = item.get("id"), item.get("questionId")
    ok(aid and qid and str(aid) != str(qid),
       "本用例需要 id != questionId 的条目(实得 id=%r qid=%r)" % (aid, qid))
    time.sleep(1.2)
    try:
        d = core.read("answer", aid)
    except Exception:
        return   # 上游对错误 id 报错 = 期望行为
    # 若不报错,则必须证明它没有把回答 id 当成问题 id 读出一个"别的帖子"
    ok(str(d.get("id")) != str(aid) or d.get("ok") is False,
       "read(answer, 回答id) 静默成功了:read 的 id 口径已分裂")


@case("online: routeErrors 契约 —— 失败路被记录、其余路结果仍返回", online=True)
def t_route_errors_recorded():
    """注入单路上游故障,断言:该路被记录进 routeErrors,且**其余路结果照常返回**。

    注入点选在 `_search_upstream`(每路检索的唯一出口),用 monkeypatch 让第 1 路抛
    UpstreamError(HTTP 200 带 errorCode 的形态)。这是本票的核心风险点:若不暴露失败路,
    调用方会把"某路被上游拒绝"读成"官方没这类文档"。
    """
    cp = _impl()
    real = cp._search_upstream
    state = {"n": 0}

    def fake(text, product_id, page, page_size, global_, sorts_type, type_, budget=None, rate=None):
        state["n"] += 1
        if state["n"] == 1:
            raise core.UpstreamError(409, "text too long(injected)")
        return real(text, product_id, page, page_size, global_, sorts_type, type_, budget, rate)

    # 探针词必须稳定产出 ≥2 路,否则"其余路"不存在(单路时首路即全部)
    cp._search_upstream = fake
    try:
        r = core.search(PROBE_MULTI_ROUTE, product_id=93, page=1, page_size=10)
    finally:
        cp._search_upstream = real
    ok(r["ok"] is True, "单路失败不应让整轮失败")
    ok(len(r["queries"]) >= 2, "本用例需要多路输入(实测该探针词 ≥2 路)")
    ok(len(r["routeErrors"]) == 1, "应记录 1 条 routeErrors,实为 %d" % len(r["routeErrors"]))
    e = r["routeErrors"][0]
    ok(e.get("route") == 1, "失败路序号应为 1,实为 %r" % (e.get("route"),))
    ok(e.get("error") == "upstream_error", "error 类型应为 upstream_error,实为 %r" % (e.get("error"),))
    ok(e.get("code") == 409, "应保留上游 errorCode,实为 %r" % (e.get("code"),))
    ok(e.get("terms"), "routeErrors 应带该路检索词,便于定位")
    ok(r["results"], "其余路的结果仍应返回,实为空清单")
    ok("routeErrors" in r["scanNote"] or "失败" in r["scanNote"], "scanNote 应提示存在失败路")


@case("online: 预算耗尽 —— budget_exhausted=true 且 scanNote 反映实际完成路数", online=True)
def t_budget_exhausted_search():
    """多路下预算不足:断言耗尽被置位,且 scanNote 写明'实际完成 N 路 / 计划 M 路'。

    同时断言**没有静默**:调用方能从 scanNote 读出清单不完整。

    budget 取值依据:该探针词实测 6 路,每路 1 页 = 6 次基础请求。
    budget=2 只够跑完 1 路、第 2 路缺口,必然触发耗尽(旧断言用 budget=1,
    单路查询时恰好跑完、不会耗尽——那是测试假设错,不是产品缺陷)。
    """
    r = core.search(PROBE_MULTI_ROUTE, product_id=93, budget=2, page=1, page_size=10)
    ok(len(r["queries"]) >= 3, "本用例需要 ≥3 路的输入(实测该探针词 6 路),实为 %d 路"
       % len(r["queries"]))
    ok(r["budget_exhausted"] is True, "budget=2 且 ≥3 路应置 budget_exhausted=true")
    ok("实际完成" in r["scanNote"], "scanNote 应写明实际完成路数,实为 %r" % (r["scanNote"],))
    ok("计划" in r["scanNote"], "scanNote 应写明计划路数,实为 %r" % (r["scanNote"],))
    ok(r["stats"]["upstreamCalls"] <= 2,
       "预算硬上限被击穿: budget=2 实际发出 %r 次请求" % (r["stats"]["upstreamCalls"],))
    ok(r["ok"] is True, "预算耗尽仍应返回 ok:true(不是异常)")
    # budget=0:零上游请求,清单必空且不崩
    r0 = core.search(PROBE_MULTI_ROUTE, product_id=93, budget=0, page=1, page_size=10)
    ok(r0["ok"] is True, "budget=0 应优雅返回")
    ok(r0["results"] == [], "budget=0 不应产出清单,实为 %d 条" % len(r0["results"]))
    ok(r0["stats"]["upstreamCalls"] == 0, "budget=0 却发生了上游请求")


@case("online: product_id 0 与省略等价(上游必须省略 productIds[0])", online=True)
def t_product_id_zero():
    """结果集层:product_id=0 与 None 等价(上游必须省略 productIds[0] 参数,传 0 会被当真值过滤)。

    ⚠️ 回显层已随 ask 删除:`effectiveProductId` 原是 **ask 返回体的顶层键**
    (票 #18 为「产品线粘性」规则提供落点),ask 删除后它在 spec 第 3 节的
    search 返回结构里**不存在**,故本用例不再断言任何产品线回显——
    这是 spec 冻结的事实,不是被弱化的断言。
    """
    a = core.search(QUERY, product_id=0, page=1, page_size=5)
    b = core.search(QUERY, product_id=None, page=1, page_size=5)
    ok(a["total"] == b["total"],
       "product_id=0 与省略的 total 不等价: %r vs %r" % (a["total"], b["total"]))
    # ⚠️ 断言口径(2026-09-18 实测修正):**只比集合,不比序列**。
    # 根因不是内核,是上游对"同分条目"的返回顺序本身有抖动:
    #   实测交替调 0/None 各 6 轮,1 轮里两条**同分相邻条目**互换位置
    #   (其余 5 轮完全一致);而两次请求打到上游的 query string 逐字节相同
    #   (已用 monkeypatch 抓 `_search_upstream` 实参验证):
    #     pid=0    -> ('信用额度控制', None, 1, 10, False, 1, None)
    #     pid=None -> ('信用额度控制', None, 1, 10, False, 1, None)
    #   即"0 与 None 等价"这条契约内核侧完全成立(0 被折成 None、productIds[0]
    #   被省略),抖动发生在内核之外。钉住序列等于把上游抖动误报成回归失败。
    # 真正要挡的回归是"过滤被静默丢弃、召回集合变大/变小",故比集合。
    ok(set(x["id"] for x in a["results"]) == set(x["id"] for x in b["results"]),
       "product_id=0 与省略的召回集合不等价(过滤可能被静默丢弃):\n  A=%r\n  B=%r"
       % ([x["id"] for x in a["results"]], [x["id"] for x in b["results"]]))


@case("online: 固定实证 —— 「应用为禁用状态[网关]」/93 必须召回 646787188905978624", online=True)
def t_gold_error_code_case():
    """**本票存在的唯一理由**:报错原文检索时,目标文档必须出现在清单里。

    背景(设计定稿实测):融合 RRF 把《金蝶AIOpenAPI错误码说明》压到第 8 位、
    落在 ask --topk 4 之外;而它恰是"单路精确命中"的典型(词法鸿沟:用户拿的是
    上游报错原文,官方文档标题是产品术语)。清单化后该文档不再被"多路都提到的泛文"
    淹没——本轮进一步删掉「按命中路数排序」(那同样是一种算法评分)后,
    顺序完全等于上游原生序,该文档必须仍可见。
    """
    gold = "646787188905978624"
    r = core.search("应用为禁用状态[网关]", product_id=93, page=1, page_size=30)
    ok(r["results"], "实证用例召回为零,清单化失去意义")
    ids = [x["id"] for x in r["results"]]
    ok(gold in ids,
       "目标文档《金蝶AIOpenAPI错误码说明》(id %s) 未出现在清单前 30 条中;"
       "实得 id=%r" % (gold, ids))
    hit = next(x for x in r["results"] if x["id"] == gold)
    ok(hit["title"], "目标文档命中但标题为空(清单核心交付物是标题)")
    ok("错误码" in (hit["title"] or ""), "目标文档标题异常: %r" % (hit["title"],))


@case("online: kd search 进程级调用(退出码 0 + JSON 契约)", online=True)
def t_cli_search():
    code, d, err = cli("search", QUERY, "--product", "93", "--size", "5")
    ok(code == 0, "kd search 退出码 %r" % code)
    ok(d is not None, "kd search stdout 不是合法 JSON")
    check_subset(keys_of(d, "cli search"), SEARCH_KEYS, "cli search")
    ok(isinstance(d["total"], int) and d["total"] > 0, "CLI total 应为正整数,实为 %r" % (d["total"],))


@case("online: kd search --kw(LLM 拆词入口)+ 清单字段 + max-routes", online=True)
def t_cli_search_manifest():
    code, d, _ = cli("search", "应用为禁用状态[网关]", "--product", "93", "--size", "30")
    ok(code == 0, "kd search 退出码 %r" % code)
    ok(d is not None, "kd search stdout 不是合法 JSON")
    check_subset(keys_of(d, "cli search"), SEARCH_KEYS, "cli search")
    ok(all("hitRoutes" in x for x in d["results"]), "CLI 清单条目应带 hitRoutes")
    ok("contentText" not in (d["results"][0] if d["results"] else {}),
       "CLI 清单条目不得含 contentText")
    # --max-routes 1:退化为单路,路数必为 1
    time.sleep(1.2)
    code, d1, _ = cli("search", QUERY, "--product", "93", "--max-routes", "1", "--size", "5")
    ok(code == 0, "kd search --max-routes 1 退出码 %r" % code)
    ok(len(d1["queries"]) == 1, "--max-routes 1 应只跑 1 路,实为 %d 路" % len(d1["queries"]))
    ok(d1["stats"]["upstreamCalls"] == 1,
       "--max-routes 1 且无 type_ 过滤应恰好 1 次上游请求,实为 %r" % (d1["stats"]["upstreamCalls"],))
    # --kw 只给关键词(无 text:nargs="?" 允许)—— LLM 拆词的 CLI 入口。
    time.sleep(1.2)
    code, d2, _ = cli("search", "--kw", "信用额度", "--kw", "应收单 信用", "--product", "93", "--size", "5")
    ok(code == 0, "kd search --kw 退出码 %r" % code)
    ok(d2 is not None, "kd search --kw stdout 不是合法 JSON")
    ok(d2["text"] is None, "只给 --kw 时 text 应为 None,实为 %r" % (d2["text"],))
    ok(d2["keywords"] == ["信用额度", "应收单 信用"],
       "--kw 应回显为 keywords,实为 %r" % (d2["keywords"],))
    ok(len(d2["queries"]) == 2, "两个 --kw 应各成一路,实为 %r" % (d2["queries"],))


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


# ---------------------------------------------------------------- 执行器
def main(argv):
    import urllib.error
    online = "--online" in argv
    only_online = "--only-online" in argv
    chosen = [(n, f, on) for (n, f, on) in CASES if (on if only_online else (online or not on))]

    print("kd 回归用例集(单入口检索重构,2026-09-18)")
    print("契约真源: docs/specs/2026-09-18-单入口检索重构.md")
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
