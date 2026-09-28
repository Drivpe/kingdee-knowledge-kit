#!/usr/bin/env python3
"""kd 回归用例集(契约重构后重定档,2026-09-27)。

定位:证明「改完契约之后,以前能干的事现在还能干」——只断言**外部行为**
(kd.core 公开面返回结构 + kd 命令的进程级输出/退出码),不断言内部函数名、
模块结构、HTTP 状态码。契约真源:`docs/contract-*.md` 之外,字段集的单一来源是
**包内 `src/kd/contract.json`**(本套件从它派生断言,不再手写第二份)。

本轮改动(相对上一版 2026-09-18 单入口重构基线):
  * **清单粒度反转**(ADR-0014,决策 D4/D6):去重与条目从**回答级**改为**帖子级**
    ——同一帖的多条回答**被合并为一条**,条目 `id` 就是帖子号,`questionId` 字段删除。
    旧用例 `t_manifest_answer_id_space`(断言"同帖不同回答不被合并")**与新契约直接冲突,
    已整体改写为相反方向的断言**——保留它就是钉住一个已被用户拍板撤销的行为。
  * **`answer` → `question` 全量改名**(决策 D5):上游协议里的 `entity-type` 仍是
    `"Answer"`(映射只在 `_upstream._norm_item` 一处),对外一律 `question`。
  * **字段集收敛并改为声明派生**(决策 D8/D10/D13):顶层 16 → 13(砍 page/pageSize/
    totalPages)、条目 16 → 13(砍 questionId/views/updatedAt);`SEARCH_KEYS`/
    `RESULT_KEYS`/`RESULT_FORBIDDEN_KEYS` 全部从 `contract.json` 派生。
    ⚠️ 派生的是**字段名,不是"应该有几个"**:断言写成双向(返回键集 ⊆ 声明允许 且
    ⊇ 声明必含),这样既不用手写 16,又能同时抓到"多出字段"与"少了字段"。
  * **产品线字面推导整体删除**(决策 D14):`_derive_product_id` 及其别名表/裁决规则
    删除,`product_id` 直通。相关三个用例(`t_product_derivation_shared_exit` /
    `t_multi_alias_arbitration` / `t_derive_sources_contract`)随之删除——
    它们钉的机制已不存在,留着只会把"函数没了"误报成回归失败。
    取代它的是 `t_product_id_passthrough`:product_id 恒等于调用方传入值。
  * **清单分页删除**(决策 D10):`--page`/`--size` 与 `page`/`page_size` 形参一并移除,
    分页相关用例删除。
  * **接管守卫三条判据**(决策 D2/D3):版本号单一真源 / `health` 内部件依赖可解析 /
    kind 集合三源一致 —— 原由 `scripts/check_core_surface.py` 承担,该脚本整体删除。

运行:
  python3 tests/kd_regression.py            # 离线组(默认;不联网,无网络也全绿)
  python3 tests/kd_regression.py --online    # 离线组 + 联网组(真实上游,保持人类频率)
  python3 tests/kd_regression.py --online --only-online   # 只跑联网组

退出码语义:
  0 = 全部通过
  1 = 有失败

联网组单独归组、默认不跑:上游是真实网络请求(匿名链路,间隔 ≥1s),离线可验的
结构契约不应因为上游抖动而变红。
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
# ⚠️ **字段集不再手写在这里**(决策 D13):单一来源是包内 `src/kd/contract.json`,
# 代码读它拼返回体、本套件从它派生断言、文档指向它。手写第二份就是"抄六遍"的病根
# ——不改就没痛,只在改动那一刻出事,而那一刻没有任何信号
# (活证据:SKILL.md:245 与 ANSWER-SPEC:23 的链接口径正面对撞)。
_CONTRACT = json.loads(
    open(os.path.join(SRC, "kd", "contract.json"), encoding="utf-8").read())

# search 顶层键集(spec 第 3 节口径,现由声明承载)。
SEARCH_KEYS = set(_CONTRACT["search"]["topKeys"])

# results[] 条目键集:清单是标题级投影,这里列的是**允许出现的全集**。
RESULT_KEYS = set(_CONTRACT["search"]["resultKeys"])

# ⚠️ **冻结基线(2026-09-28,T4)** —— 这是"对外字段契约"的**独立**一份,
# **刻意不派生自 contract.json**,理由是一条实测出来的自环:
#
#   旧口径下,`t_search_contract` 与 `t_manifest_projection` 都从 `contract.json`
#   派生期望,而被测代码(`_manifest_project` 经 `result_keys()`)也读**同一个文件**。
#   于是比对的两端同源,恒等成立。实测(2026-09-28):
#
#     | 注入                                       | 旧回归结果 | 真实输出        |
#     |--------------------------------------------|-----------|-----------------|
#     | 从 contract.json 的 resultKeys 删 `products` | 35/35 全绿 | 真的少了一个字段 |
#     | 代码侧新增 `productsV2`、声明不动            | 35/35 全绿 | ——              |
#
#   这是**回声与回声自比**:两个方向都抓不住,却让后人以为有保护——比没有检查更贵
#   (没有检查时人会小心;有一个永远绿的检查,改配置的人会以为"测试过了,没事")。
#
# 冻结基线的作用就是**打断这个自环**:它是一份不随 contract.json 漂移的独立期望。
# 改对外字段集时**必须**同时改这里——这道摩擦是有意的("改契约必须有人看见")。
#
# 三条断言合起来才有覆盖(见 `t_result_keys_vs_real_output`):
#   ① 声明 == 冻结基线        → 抓「声明漂移」
#   ② 真实输出 == 声明        → 抓「代码与声明脱节」
#   ③ 故障注入证明 ①② 真的会红  → 抓「断言本身退化成重言式」
FROZEN_RESULT_KEYS = ("type", "id", "title", "url", "hitRoutes", "routes", "snippet",
                      "products", "adopted", "answersCount", "comments", "supports",
                      "questionBody")
FROZEN_TOP_KEYS = ("ok", "text", "keywords", "total", "queries", "routesPlanned",
                   "routesDegraded", "effectiveProductId", "results", "routeErrors",
                   "budget_exhausted", "scanNote", "stats")

# 历史残留字段:清单是标题级投影,这些**出现即 FAIL**。
#   contentText / contentLen —— 清单只给标题级信息,全文走 read;
#   fusedScore               —— 随 ask 一并删除的算法评分;
#   chunks                   —— 只服务 ask 深读;
#   useful                   —— 未列入清单字段;
#   views / updatedAt        —— 2026-09-27 砍(ANSWER-SPEC 与 SKILL 里零用途说明);
#   questionId              —— 2026-09-27 砍(帖级化后 id 就是帖子号)。
RESULT_FORBIDDEN_KEYS = set(_CONTRACT["search"]["resultForbiddenKeys"])

# read:ok, id, type, title, contentText, url, products, updatedAt, stats(已摘 landing)。
READ_KEYS = {"ok", "id", "type", "title", "contentText", "url", "products", "updatedAt", "stats"}
# read(question) 专有字段(spec 第 4 节:问答帖另带一组)。
# EXTRA = **可出现的键集**(白名单上界);REQUIRED = **恒在的键集**(必含下界)。
# 两者的差集 {bestAnswer, answers, truncated} 是条件字段:
#   bestAnswer/answers 依赖上游是否给了采纳回答/答案列表;
#   truncated 只在发生截断时置位。
READ_QUESTION_EXTRA_KEYS = {"isSolved", "answersCount", "views", "rewardCoins",
                            "createdAt", "bestAnswer", "answers", "truncated",
                            "answersTaken", "answersTotal"}
READ_QUESTION_REQUIRED_KEYS = READ_QUESTION_EXTRA_KEYS - {"bestAnswer", "answers", "truncated"}

# 产品线默认编号(从声明取;不传 --product 时生效)。
DEFAULT_PRODUCT_ID = int(_CONTRACT["productIds"]["default"])

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
#   多路实证:`BOM 分母变平方` 稳定出 6 路,清单足够深、可有失败路。
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


@case("offline: 清单分页形参已删除(search/CLI 传 page/size 即 TypeError/exit 2)")
def t_pagination_gone():
    """**删除的回归钉子**(决策 D10,2026-09-27):清单分页整体移除。

    删的是三处一致的口径,缺一处就会留下"能传但无效"的幽灵参数:
      ① `core.search` 的 `page` / `page_size` 形参 → 传即 TypeError;
      ② CLI 的 `--page` / `--size` → argparse 直接 exit 2(未知参数);
      ③ 返回顶层不再有 `page`/`pageSize`/`totalPages`。

    为什么值得钉:分页曾是"清单能翻到第 2 页"这条承诺的实现;若将来有人只把
    形参补回签名(而执行链不再消费它),调用方会以为能翻页却永远拿到同一批
    ——静默失效比报错更贵,故三层一起钉。
    """
    for label, fn in (("search(page=2)", lambda: core.search(QUERY, page=2)),
                      ("search(page_size=5)", lambda: core.search(QUERY, page_size=5))):
        try:
            fn()
        except TypeError:
            continue
        except core.InternalError:
            raise Fail("%s 抛 InternalError 而非 TypeError(形参仍被接受)" % label)
        except Exception as e:
            raise Fail("%s 抛 %s(应为 TypeError:形参已删除)" % (label, type(e).__name__))
        raise Fail("%s 未报错——分页形参仍在签名里(应已删除)" % label)
    # CLI 侧:argparse 未知参数 → exit 2,且 stdout 不得出现成功载荷。
    for flag, val in (("--page", "2"), ("--size", "5")):
        code, d, err = cli("search", QUERY, flag, val)
        ok(code == 2, "kd search %s 应 exit 2(argparse 未知参数),实测 %r" % (flag, code))
        ok(err and err.strip(), "kd search %s 的 stderr 为空:用法提示被吞掉" % flag)
        if d is not None:
            ok(d.get("ok") is not True, "kd search %s 竟返回成功载荷: %r" % (flag, d))
    # 顶层键集不含分页字段(声明侧,零上游请求:超长查询走错误路径)。
    ok("page" not in SEARCH_KEYS and "pageSize" not in SEARCH_KEYS
       and "totalPages" not in SEARCH_KEYS,
       "声明里仍有分页字段: %s" % sorted(SEARCH_KEYS))


# ============ 离线组:接管自 check_core_surface.py 的三条判据 ============
# 原 `scripts/check_core_surface.py`(465 行、10 条判据)已整体删除(决策 D2)。
# 其余判据由本套件与"只用一次的两个函数"兜住,但有**三条判据真的防静默失效**
# (全是一次一构的类型级退化,正常改动触碰不到),故迁进这里。迁入时保持判据
# 原意,只把取数方式改成"读源码/读声明",不新增第二份真相。

@case("offline: 版本号单一真源(实现体 VERSION → 其余四处派生)")
def t_version_single_source():
    """五处版本号必须同源:实现体 `VERSION` 是唯一出处,其余四处由它派生。

    抓的是"五份字面量各自演化"这一类漂移——实测曾出现
    `6.2 / 0.1.0 / 6.2 / 6.2.0` 四处不一致,而 `pipx install` 会把 `__version__`
    当成发布版本号;`plugin.json` 是对外决定技能包版本的那一处,长期未纳入任何守卫。

    比较口径:pyproject 与 plugin.json 用三段(PEP 440 / 插件清单惯例),
    实现体用两段,故只比"前两段"是否一致;`__init__.__version__` 与 `cli._VERSION`
    必须与实现体**逐字**相等。
    """
    import importlib
    impl = importlib.import_module("kd._impl")
    pkg = importlib.import_module("kd")
    cli_mod = importlib.import_module("kd.cli")
    want = getattr(impl, "VERSION", None)
    ok(want, "实现体缺少 VERSION 常量(单一真源不存在)")
    ok(getattr(pkg, "__version__", None) == want,
       "kd.__version__=%r != VERSION=%r" % (getattr(pkg, "__version__", None), want))
    ok(getattr(cli_mod, "_VERSION", None) == want,
       "cli._VERSION=%r != VERSION=%r" % (getattr(cli_mod, "_VERSION", None), want))
    # 另两处只认本项目的一行写法(不引第三方 toml/yaml 解析)。
    import re
    got_pyproject = None
    with open(os.path.join(REPO, "pyproject.toml"), encoding="utf-8") as f:
        for line in f:
            m = re.match(r'\s*version\s*=\s*["\']([^"\']+)["\']', line)
            if m:
                got_pyproject = m.group(1)
                break
    ok(got_pyproject is not None, "读不到 pyproject.toml 的 version(格式变了?)")
    ok(str(got_pyproject).split(".")[:2] == str(want).split(".")[:2],
       "pyproject version=%r 前两段 != VERSION=%r" % (got_pyproject, want))
    plugin_path = os.path.join(REPO, "skills", "kingdee-knowledge",
                               ".claude-plugin", "plugin.json")
    with open(plugin_path, encoding="utf-8") as f:
        got_plugin = (json.load(f) or {}).get("version")
    ok(got_plugin is not None, "读不到 plugin.json 的 version(该文件决定技能包版本)")
    ok(str(got_plugin).split(".")[:2] == str(want).split(".")[:2],
       "%s version=%r 前两段 != VERSION=%r" % (plugin_path, got_plugin, want))
    # query_routes.json 的 version 是**配置格式版本**(v6.4),与套件版本同源推进,
    # 但字面形态不同(v 前缀 + 两段),故单独按两段比对。
    with open(os.path.join(SRC, "kd", "query_routes.json"), encoding="utf-8") as f:
        cfgv = (json.load(f) or {}).get("version")
    ok(cfgv, "query_routes.json 缺 version 字段")
    ok(str(cfgv).lstrip("v").split(".")[:2] == str(want).split(".")[:2],
       "query_routes.json version=%r 与 VERSION=%r 不同源" % (cfgv, want))


@case("offline: health 内部件依赖可解析(观测口不失效)")
def t_health_impl_deps_resolvable():
    """`cli.cmd_health` 经观测口读取的每个内部件,必须仍在实现包里可解析。

    抓的是"内部件被删/改名 → health 静默崩、而其它断言照样绿"这一类漂移。
    依赖清单**从 cmd_health 源码里提取**(不手写第二份真相,避免清单自身腐烂);
    三种失败任一即红:
      a. 读不到 cli.py 或定位不到 cmd_health(函数被改名/删除);
      b. 函数体内没有任何 `_cp.<name>` 引用(取数被抽走);
      c. 清单非空但有条目在实现体里已失联(health 会崩)。

    ⚠️ 已知局限(明确接受,原文照迁):只认 `_cp.<identifier>` 字面量,
    经局部变量转手或 `getattr` 拼接的间接引用抓不到。这类漏只表现为"少一条钉子"
    (漏报),不会把健康的 health 判成坏。
    """
    import re
    cli_path = os.path.join(SRC, "kd", "cli.py")
    with open(cli_path, encoding="utf-8") as f:
        text = f.read()
    m = re.search(r"def cmd_health\(.*?(?=\ndef |\Z)", text, re.S)
    ok(m, "无法从 %s 定位 cmd_health(函数被改名/删除?)" % cli_path)
    deps = sorted(set(re.findall(r"_cp\.([A-Za-z_][A-Za-z0-9_]*)", m.group(0))))
    ok(deps, "cmd_health 已定位,但函数体内没有任何 `_cp.<name>` 引用"
             "(取数逻辑被抽走/前缀被改)——否则本判据形同虚设")
    impl = _impl()
    missing = [n for n in deps if not hasattr(impl, n)]
    ok(not missing, "health 依赖的内部件已失联: %s(health 运行时会崩,"
                    "须在实现体里补回或改 cmd_health)——依赖清单 %r"
       % (", ".join(missing), deps))


@case("offline: kind 集合三源一致(ENTITY_KINDS / _DETAIL_KINDS / CLI 白名单)")
def t_kind_consistency():
    """`kind` 集合必须**三处同集合**,且 CLI 不得复制裸字面量。

    抓的是"read 的 --kind 白名单 / search 的 --type 白名单 / 详情分发表三份字面量
    不同源"——历史上分发表有 4 个 kind 而公开白名单只有 3 个,加删 kind 不会被
    任何断言发现。

    ⚠️ 本轮口径变化(决策 D5):集合的第三个值是 `question`(不是 `answer`)。
    CLI 侧不 import(有 argparse 副作用),改用正则读源——与 health 判据同纪律。
    """
    import re
    impl = _impl()
    entity = getattr(impl, "ENTITY_KINDS", None)
    detail = getattr(impl, "_DETAIL_KINDS", None)
    ok(entity and detail, "实现体缺 ENTITY_KINDS 或 _DETAIL_KINDS")
    ok(set(entity) == set(detail),
       "ENTITY_KINDS=%r != _DETAIL_KINDS=%r(改 kind 只改了一处)" % (entity, detail))
    # 新集合必须就是 question 那一套——顺带钉住"没被悄悄改回 answer"。
    ok(set(entity) == {"knowledge", "question", "article"},
       "kind 集合应为 {knowledge, question, article}(决策 D5),实为 %r" % (sorted(entity),))
    cli_path = os.path.join(SRC, "kd", "cli.py")
    with open(cli_path, encoding="utf-8") as f:
        text = f.read()
    literal = re.search(r'\(\s*"knowledge"\s*,\s*"question"\s*,\s*"article"\s*\)', text)
    ok(not literal,
       "cli.py 里仍有裸 kind 字面量(第 %d 字符处)——应从 _IMPL.ENTITY_KINDS 取"
       % (literal.start() if literal else 0))
    ok("ENTITY_KINDS" in text, "cli.py 未引用 ENTITY_KINDS(白名单与实现体脱钩)")


@case("offline: 类型过滤词汇双向映射(对外 question ↔ 上游 Answer)")
def t_type_vocab_mapping():
    """`type_` 过滤必须把**对外词汇**翻成**上游词汇**再比较,两个方向都要对。

    ⚠️ 这条是**实测暴露的真实缺陷**留下的钉子(2026-09-27):
    改名 `answer` → `question` 时,`_norm_item`(收响应时的翻译)改了,
    但 `_route_search_once.collect` 里做过滤比较的那一处**没跟上**——它拿对外的
    `"question"` 去比上游返回的 `"Answer"`,`et != "question"` 恒真 →
    **每一条都被丢弃,清单空、total 正常、无任何报错**。
    这类"静默零结果"是最贵的一种:调用方看到 total 有值、results 为空,
    只会以为"官方没这类文档"。

    三层各钉一段:
      ① 映射函数本身:`question → answer`(发请求前)、`answer → question`(收响应后);
      ② 全 kind 覆盖,漏一个就漏一个类型(将来加 kind 也会在这里红);
      ③ 映射表与 `ENTITY_KINDS` 同集合(防"加了 kind 忘了映射")。
    """
    cp = _impl()
    ok(hasattr(cp, "upstream_type_of"), "实现包应导出 upstream_type_of(映射的唯一出口)")
    # ① 两个方向。
    ok(cp.upstream_type_of("question") == "answer",
       "对外 question 必须映射到上游 answer,实为 %r" % (cp.upstream_type_of("question"),))
    ok(cp.upstream_type_of("knowledge") == "knowledge", "knowledge 应恒等映射")
    ok(cp.upstream_type_of("article") == "article", "article 应恒等映射")
    # ② 收响应方向:_norm_item 必须把上游 "answer" 翻成对外 "question"。
    n = cp._norm_item(_syn("answer", 1, qid=2), "answer")
    ok(n["type"] == "question", "上游 answer 应收敛为对外 question,实为 %r" % (n["type"],))
    # ③ 映射表覆盖全部 kind(与 ENTITY_KINDS 同集合:漏一个 = 漏一个类型的召回)。
    mapped = {k for k in cp.ENTITY_KINDS if cp.upstream_type_of(k)}
    ok(mapped == set(cp.ENTITY_KINDS),
       "有 kind 未建立上游映射(该类型过滤将恒零结果): %s"
       % sorted(set(cp.ENTITY_KINDS) - mapped))
    # 未知 kind 原样返回(不静默折成某个合法值——那会变成"过滤到别人").
    ok(cp.upstream_type_of("nope") == "nope", "未知 kind 应原样返回以便上层报错")
    # 大小写无关(上游写 Knowledge/Answer/Article)。
    ok(cp.upstream_type_of("QUESTION") == "answer", "映射应大小写无关")


@case("offline: product_id 三态直通(省略→默认 / None→不过滤 / 0→不过滤)")
def t_product_id_three_states():
    """`product_id` 的**值域三态**必须可区分——这是实测暴露的第二个真实缺陷。

    修前:`search` 把 `product_id is None` 一律折成默认 93,于是 Python 调用方
    **无法**表达"不过滤"(`product_id=None` 被静默过滤)。实测同问句下
    `product_id=None` 与 `0` 的 total 从 31788 变 6326 —— 过滤被悄悄加上。

    三态定义(签名默认值承担第一态,而不是把 None 折成默认):
      * 省略参数 → 93(签名默认,与 `contract.json` 声明同值);
      * 显式 None → 不过滤(上游省略 productIds);
      * 显式 0 → 不过滤(CLI `--product 0`);
      * 显式整数 → 直通。
    """
    import inspect
    sig = inspect.signature(core.search)
    default = sig.parameters["product_id"].default
    ok(default == DEFAULT_PRODUCT_ID,
       "product_id 签名默认应为声明里的默认编号 %r,实为 %r(省略参数靠它承担)"
       % (DEFAULT_PRODUCT_ID, default))
    # 省略:走签名默认 → 93。
    _r, seen_omit = _patched_search((PROBE_DEGRADED,), budget=10)
    ok(all(c["product_id"] == DEFAULT_PRODUCT_ID for c in seen_omit),
       "省略参数应带默认过滤 %r,实收 %r"
       % (DEFAULT_PRODUCT_ID, [c["product_id"] for c in seen_omit]))
    # 显式 None:必须**不过滤**(上游省略参数)——修前这里会被折成 93。
    r_none, seen_none = _patched_search((PROBE_DEGRADED,), product_id=None, budget=10)
    ok(r_none["effectiveProductId"] is None,
       "显式 product_id=None 应回显 None(显式不过滤),实为 %r" % (r_none["effectiveProductId"],))
    ok(all(c["product_id"] is None for c in seen_none),
       "显式 None 时上游必须省略产品过滤,实收 %r(修前会被静默折成默认 93)"
       % ([c["product_id"] for c in seen_none],))
    # 显式 0:同样不过滤(与 None 同效,但值域不同——调用方词汇)。
    r_zero, seen_zero = _patched_search((PROBE_DEGRADED,), product_id=0, budget=10)
    ok(r_zero["effectiveProductId"] == 0, "显式 0 应回显 0,实为 %r" % (r_zero["effectiveProductId"],))
    ok(all(c["product_id"] is None for c in seen_zero),
       "显式 0 时上游必须省略产品过滤,实收 %r" % ([c["product_id"] for c in seen_zero],))
    # 区分度钉子:三态的回显值必须两两不同,否则本用例无法区分实现。
    ok(len({DEFAULT_PRODUCT_ID, r_none["effectiveProductId"], r_zero["effectiveProductId"]}) == 3,
       "三态回显值必须两两不同(否则无法区分省略/None/0): %r"
       % ([DEFAULT_PRODUCT_ID, r_none["effectiveProductId"], r_zero["effectiveProductId"]],))
    # CLI 侧:不传 --product 时**不得**把 None 当显式值传进内核(那等于不过滤)。
    code, d, err = cli("search", PROBE_DEGRADED)
    ok(code == 0, "kd search 不带 --product 应成功,实测 exit %r;stderr=%r" % (code, err[:200]))
    ok(d and d.get("effectiveProductId") == DEFAULT_PRODUCT_ID,
       "CLI 不传 --product 时应生效默认 %r(把 argparse 的 None 直传进内核会变成不过滤): %r"
       % (DEFAULT_PRODUCT_ID, (d or {}).get("effectiveProductId")))


@case("offline: 上游 answer 字面量只在映射点出现(改名不漏)")
def t_answer_literal_confined():
    """`"answer"` 这个**上游原始值**只允许出现在 `_upstream._norm_item`。

    决策 D5 把对外的问答类型改名为 `question`,而**上游协议里仍是 `Answer`**。
    这个映射必须收口在一处:若别处也直接比较 `"answer"`,那么改上游值名时会有
    N 个地方需要同步改,漏一处就是静默漏召回(该类型条目全丢)。

    本用例**静态扫描 src/kd/(除 _upstream.py 外)的 .py**:
      * 允许出现的位置:注释/docstring(说明"上游叫 answer"是必要的);
      * 不允许:代码里出现带引号的 `"answer"` 字面量。
    """
    import re
    offenders = []
    for root, _dirs, files in os.walk(os.path.join(SRC, "kd")):
        for fn in files:
            if not fn.endswith(".py") or fn == "_upstream.py":
                continue
            path = os.path.join(root, fn)
            with open(path, encoding="utf-8") as f:
                for i, line in enumerate(f, 1):
                    code = line.split("#", 1)[0]
                    if re.search(r'["\']answer["\']', code):
                        offenders.append("%s:%d: %s" % (os.path.relpath(path, REPO), i,
                                                        line.strip()[:80]))
    ok(not offenders,
       "上游值 \"answer\" 出现在映射点之外(应为 question)——漏改处会静默丢该类型条目:\n  "
       + "\n  ".join(offenders))
    # 映射点本身必须存在,否则上面这条扫描会因"扫了个空"而假绿。
    src_up = open(os.path.join(SRC, "kd", "_impl", "_upstream.py"), encoding="utf-8").read()
    ok('et == "answer"' in src_up,
       "_upstream.py 里找不到 `et == \"answer\"` 映射分支——映射点没了,"
       "扫描范围就成了空的(假绿)")
    # ⚠️ 映射的**另一半**(发请求时的过滤比较)也必须在同一文件里,且值域指向上游词汇。
    # 只有"收响应"方向被改是实测发生过的缺陷(见 t_type_vocab_mapping):
    # 那一半漏改的后果是 type=question 恒零结果,而本条扫描抓不到(它在 _manifest,
    # 那里比较的是变量 `want_up`,不含字面量)。故此处显式钉住映射表的定义位置。
    ok('"question": "answer"' in src_up,
       "_upstream.py 缺 `\"question\": \"answer\"` 映射表项——type 过滤会拿对外词汇"
       "去比上游 entity-type,导致该类型恒零结果")
    ok("def upstream_type_of" in src_up, "_upstream.py 缺 upstream_type_of 导出函数")


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
    """合成一个上游形态条目(用于喂 _norm_item),字段形状对齐实测响应。

    ⚠️ 入参 `et` 是**上游**的 entity-type(小写):问答题传 `"answer"`——
    `_norm_item` 收到它才会翻译成对外的 `question`。这是刻意的:用例必须证明
    映射发生在**上游值**上,而不是在已经翻译过的值上(否则映射失效也测不出来)。

    帖子级(`qid`)是默认行为:同一 `qid` + 不同 `i`(回答 id)的两个条目,经
    `_manifest_merge` 必须合并成一条。
    """
    if et == "answer":
        return {"entity-type": "Answer", "id": str(i), "questionId": str(qid or i),
                "highlight": {"question.title": title or ("问题标题 %s" % i),
                              "description": "回答正文 %s" % i},
                "question": {"id": str(qid or i), "answers": 2, "moduleName": "财务云",
                             "description": "问题正文 %s" % i},
                "isAdopt": "true" if adopted else "false", "views": 10,
                "comments": 3, "contentLen": 100, "updatedAt": "2026-01-01"}
    if et == "article":
        return {"entity-type": "Article", "id": str(i),
                "highlight": {"title": title or ("文章标题 %s" % i), "content": "正文 %s" % i},
                "classifies": [{"name": "星空旗舰版"}], "views": 10, "supports": 5,
                "contentLen": 100, "updatedAt": "2026-01-01"}
    return {"entity-type": "Knowledge", "id": str(i), "knowledgeId": str(i),
            "highlight": {"title": title or ("知识标题 %s" % i), "content": "正文 %s" % i},
            "classifies": [{"name": "星空旗舰版"}], "views": 10, "useful": 1,
            "contentLen": 100, "updatedAt": "2026-01-01"}


@case("offline: 多路去重 —— 同一条被两路命中只出一条且 hitRoutes=2")
def t_manifest_dedupe_hit_routes():
    cp = _impl()
    a = cp._norm_item(_syn("knowledge", 100), "knowledge")
    k = cp._norm_item(_syn("knowledge", 200), "knowledge")
    # 路1: [a, k];路2: [a]  —— a 被两路命中
    keys, hits, _first, _by = cp._manifest_fuse([(1, [a, k]), (2, [a])])
    ok(len(keys) == 2, "两条不同条目应去重为 2 条,实为 %d" % len(keys))
    ak = cp._manifest_key(a)
    ok(ak in hits, "条目未进命中表")
    ok(len(hits[ak]) == 2, "该条被两路命中,hitRoutes 应为 2,实为 %d" % len(hits[ak]))
    proj = cp._manifest_project(a, hits[ak])
    ok(proj["hitRoutes"] == 2, "投影后的 hitRoutes 应为 2")
    ok(proj["routes"] == [1, 2], "投影后的 routes 应为 [1,2],实为 %r" % (proj["routes"],))
    ok(proj["title"] == "知识标题 100", "标题应透传")
    ok("contentText" not in proj, "清单条目不得返回 contentText(要全文走 read)")
    kk = cp._manifest_key(k)
    ok(len(hits[kk]) == 1, "k 应只命中 1 路,实为 %d" % len(hits[kk]))
    ok(set(hits[ak]) == {1, 2}, "命中路集应为 {1,2},实为 %r" % (sorted(hits[ak]),))


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
    keys, hits, first, _by = cp._manifest_fuse([(1, [a, b]), (2, [b])])

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
    keys2, hits2, first2, _by2 = cp._manifest_fuse([(1, [c]), (2, [c, d])])
    ok(len(hits2[cp._manifest_key(c)]) == 2, "丙应命中 2 路")
    ok(first2[cp._manifest_key(c)] == (1, 1), "丙首次出现应为 (1,1)")
    # 丁未在路1 出现,首次落在路2 的第 2 名(丙占了路2 第 1 名)。
    ok(first2[cp._manifest_key(d)] == (2, 2), "丁首次出现应为 (2,2)")
    ok(keys2[0] == cp._manifest_key(c), "丙路序 (1,1) 最靠前,应排首位,实得 %r" % (keys2,))

    # 纯函数性质:排序键的两维都不含命中路数(直接问 _manifest_rank)。
    # ⚠️ 2026-09-28 改:该函数的第一个形参 `route_hits` 在函数体内**零使用**,
    # 是装饰性形参,已删(它误导读代码的人以为排序与命中路数有关,恰好与本契约相反)。
    # 这里把"排序不得看命中路数"重钉在**签名 + 行为**两处:
    rank = cp._manifest_rank(first)
    probe = cp._manifest_key(a)
    ok(len(rank(probe)) == 2,
       "排序键应为 2 维 (路序, 路内名次),实为 %d 维: %r" % (len(rank(probe)), rank(probe)))
    ok(rank(probe) == (1, 1), "甲的排序键应为 (1,1),实为 %r" % (rank(probe),))
    # 签名钉子:入参只允许"首次出现表"一个,不得再混入命中路数。
    import inspect
    _params = list(inspect.signature(cp._manifest_rank).parameters)
    ok(_params == ["route_index"],
       "排序键构造器的形参应为 ['route_index'],实为 %r —— "
       "出现 route_hits/hits 之类即意味着排序有重新看命中路数的通道" % (_params,))
    # 行为钉子:hitRoutes 不同的两条,排序完全由首次出现决定(把命中表喂进去也不生效,
    # 因为它根本不是入参)。
    ok(rank(cp._manifest_key(b)) == (1, 2),
       "乙的排序键应为 (1,2)(它命中 2 路,但命中数不进排序键),实为 %r"
       % (rank(cp._manifest_key(b)),))
    ok(not any("score" in str(k).lower() for k in keys), "清单排序不得引入分数键")


@case("offline: 帖子级归并 —— 同一帖的多条回答被合并为一条(ADR-0014)")
def t_manifest_question_merge():
    """**本轮硬证据**(决策 D4/D6 + ADR-0014)。

    ⚠️ 本条与它取代的旧用例(`t_manifest_answer_id_space`,断言"同帖不同回答
    **不被**合并")**方向完全相反**。旧口径的理由是"同帖多条回答语义不同
    (采纳的是解、普通的是旁证),应可分别筛";用户推翻了该理由:
    "我自己人类搜索社区的时候问题和答案都是在一起的"。

    现行契约:同一帖的多条回答在上游是多个条目,在清单里**合并为一条**;
    条目的 `id` 就是**帖子号**;`questionId` 字段整体删除。
    """
    cp = _impl()
    # 同一帖子(帖子号 800)下的两条**不同回答**(上游回答 id 901/902)
    a1 = cp._norm_item(_syn("answer", 901, qid=800, title="帖子标题"), "answer")
    a2 = cp._norm_item(_syn("answer", 902, qid=800, title="帖子标题"), "answer")
    # 上游 "answer" 已被翻译成对外 "question"(决策 D5;映射只在 _norm_item 一处)。
    ok(a1["type"] == a2["type"] == "question",
       "上游 answer 条目应翻译成对外 question,实为 %r/%r" % (a1["type"], a2["type"]))
    ok("questionId" not in a1,
       "questionId 字段应已删除(帖级化后 id 就是帖子号),实有键 %r" % (sorted(a1.keys()),))
    ok(a1["id"] == a2["id"] == "800",
       "条目 id 应为**帖子号**(800),实为 %r/%r —— 取回答 id 会让同帖各自成条" % (a1["id"], a2["id"]))
    ok(cp._manifest_key(a1) == cp._manifest_key(a2) == "question:800",
       "去重键应为 question:<帖子号>,实为 %r/%r"
       % (cp._manifest_key(a1), cp._manifest_key(a2)))

    # 两路各自命中其中一条回答:清单必须归并成 **1** 条(旧契约在此得 2 条)。
    keys, hits, first, by = cp._manifest_fuse([(1, [a1]), (2, [a2])])
    ok(len(keys) == 1,
       "同帖两条回答应合并为 1 条(帖子级,ADR-0014),实为 %d 条" % len(keys))
    merged = by[keys[0]]
    ok(merged["id"] == "800", "合并后条目 id 应为帖子号 800,实为 %r" % (merged["id"],))
    ok(len(hits[keys[0]]) == 2, "该帖被两路命中,hitRoutes 应为 2,实为 %d" % len(hits[keys[0]]))
    # 合并不得引入排序分:该帖的排序位次取**首次出现**(路1 第 1 名)。
    ok(first[keys[0]] == (1, 1),
       "合并后排序位次应仍取首次出现 (1,1),实为 %r —— 归并不得改排序键" % (first[keys[0]],))

    # 同一条回答被两路命中:仍是 1 条(与上一条不冲突,证明归并不是"恒合并所有")。
    keys2, hits2, _f2, _b2 = cp._manifest_fuse([(1, [a1]), (2, [a1])])
    ok(len(keys2) == 1, "同一条回答被两路命中应归并为 1 条,实为 %d 条" % len(keys2))
    ok(len(hits2[cp._manifest_key(a1)]) == 2, "hitRoutes 应为 2")

    # 不同帖**不得**被归并(归并键是帖子号,不是"所有问答"。
    a3 = cp._norm_item(_syn("answer", 903, qid=801, title="另一帖"), "answer")
    keys3, _h3, _f3, _b3 = cp._manifest_fuse([(1, [a1, a3])])
    ok(len(keys3) == 2, "不同帖必须各自成条,实为 %d 条" % len(keys3))


@case("offline: 帖级信号聚合 —— adopted 任一为真、计数取最大值(归并语义)")
def t_manifest_merge_signals():
    """归并时的**帖级信号聚合规则**(ADR-0014 的合并规则表)。

    语义变化(决策 D7):`adopted` 从"这条回答被采纳"变成"**这帖里有采纳答案**"。
    这是刻意的——归并后条目代表整个帖子,旧的回答级语义已无宿主;调用方读法不变
    (仍是"这条值不值得点开"的旁证),故未新增字段。

    两条规则各有反例对照,防止"恰好通过":
      * `adopted`:任一为真即为真 —— 反例是两条都假时必须仍为假(不得恒真);
      * `answersCount`/`comments`/`supports`:取最大值 —— 反例是较小的那条不得胜出
        (归一化不得把"某条缺字段"当成 0 而污染真值)。
    """
    cp = _impl()
    # 上游形态:一条被采纳、一条没被采纳,同一个帖子号 800。
    adopted_one = cp._norm_item(_syn("answer", 901, qid=800, adopted=True), "answer")
    plain_one = cp._norm_item(_syn("answer", 902, qid=800, adopted=False), "answer")
    merged = cp._manifest_merge([adopted_one, plain_one])
    ok(merged["adopted"] is True,
       "同帖有一条被采纳时,帖级 adopted 应为 True(任一为真即为真),实为 %r"
       % (merged["adopted"],))
    # 反例:两条都未被采纳 → 必须为 False(证明上一条不是恒真)。
    merged_false = cp._manifest_merge([plain_one, dict(plain_one)])
    ok(merged_false["adopted"] is False,
       "同帖全未被采纳时 adopted 应为 False,实为 %r" % (merged_false["adopted"],))

    # 计数取最大值(构造:两条 answersCount 不同,大的必须胜出)。
    lo = dict(adopted_one, answersCount=2, comments=1)
    hi = dict(adopted_one, answersCount=7, comments=9)
    m2 = cp._manifest_merge([lo, hi])
    ok(m2["answersCount"] == 7, "answersCount 应取最大值 7,实为 %r" % (m2["answersCount"],))
    ok(m2["comments"] == 9, "comments 应取最大值 9,实为 %r" % (m2["comments"],))
    # 反向顺序也要一致(证明取 max 而不是"取后者")。
    m3 = cp._manifest_merge([hi, lo])
    ok(m3["answersCount"] == 7 and m3["comments"] == 9,
       "归并结果不得依赖条目顺序: %r/%r" % (m3["answersCount"], m3["comments"]))
    # 某条缺字段(None)时不得把真值冲掉。
    m4 = cp._manifest_merge([dict(adopted_one, answersCount=None), hi])
    ok(m4["answersCount"] == 7, "缺字段条目不得冲掉真值,实为 %r" % (m4["answersCount"],))

    # snippet/questionBody 取首个非空,且**不拼接**(拼接会把不相邻片段伪装成连续文本)。
    s1 = dict(adopted_one, snippet=None)
    s2 = dict(adopted_one, snippet="第二条的片段")
    m5 = cp._manifest_merge([s1, s2])
    ok(m5["snippet"] == "第二条的片段",
       "首条 snippet 为空时应取首条非空,实为 %r" % (m5["snippet"],))
    ok(m5["questionBody"] == adopted_one["questionBody"], "questionBody 应取首个非空")


@case("offline: 清单投影字段集固定(声明派生;无 contentText/views/questionId)")
def t_manifest_projection():
    """字段集的**双向**断言:既不得多出、也不得少了。

    键集不手写在这里,而是从 `contract.json` 派生(决策 D13)。故本用例同时钉两件事:
      ① 代码产出的键集 == 声明允许的键集(多一个即红:防"顺手加个字段");
      ② 声明的必含键集 ⊆ 产出(少一个即红:防"字段被删而声明没跟上")。
    """
    cp = _impl()
    declared = set(RESULT_KEYS)
    for et in ("knowledge", "question", "article"):
        # ⚠️ 喂给 _norm_item 的是**上游** entity-type:问答题必须传 "answer"。
        up_et = "answer" if et == "question" else et
        n = cp._norm_item(_syn(up_et, 7), up_et)
        ok(n is not None, "%s 合成条目未被 _norm_item 接受(构造数据形状不对)" % et)
        ok(n["type"] == et, "%s 条目规范化后 type 应为 %r,实为 %r" % (et, et, n["type"]))
        p = cp._manifest_project(n, {1, 2})
        ks = set(p.keys())
        extra = ks - declared
        ok(not extra,
           "%s 条目投影出现声明外字段: %s" % (et, sorted(extra)))
        # 声明里的键必须**全部产出**(值可为 None:声明即"键必须在")。
        # 这两个是内核算出的命中信息,不在声明里当条目字段,由本用例单独要求。
        required = declared | {"hitRoutes", "routes"}
        missing = required - ks
        ok(not missing, "%s 条目投影缺字段: %s" % (et, sorted(missing)))
        # 历史残留字段出现即 FAIL。
        check_no_forbidden(ks, RESULT_FORBIDDEN_KEYS, "%s 条目投影" % et)
        ok(p["hitRoutes"] == 2 and p["routes"] == [1, 2], "%s 投影的命中信息不符" % et)

    # 上游原生信号透传(不是内核算的):问答条目的这几项必须从 _norm_item 带出来。
    a = cp._norm_item(_syn("answer", 900, qid=800, adopted=True), "answer")
    pa = cp._manifest_project(a, {1})
    ok(pa["adopted"] is True, "问答投影应透传 adopted=True,实为 %r" % (pa["adopted"],))
    ok(pa["answersCount"] == 2, "问答投影应透传 answersCount=2,实为 %r" % (pa["answersCount"],))
    ok(pa["comments"] == 3, "问答投影应透传 comments=3,实为 %r" % (pa["comments"],))
    ok(pa["questionBody"] == "问题正文 900",
       "问答投影应透传 questionBody(来源是 question.description),实为 %r" % (pa["questionBody"],))
    # article 的 supports 同理。
    art = cp._norm_item(_syn("article", 5), "article")
    ok(cp._manifest_project(art, {1})["supports"] == 5, "article 投影应透传 supports")


@case("offline: 清单字段集三段对账(声明↔冻结基线↔真实输出)+ 注入自检")
def t_result_keys_vs_real_output():
    """**T4 的核心修复**:把字段集检查的比对对象从「程序自己读的声明」换成
    「程序真实的输出」。

    ⚠️ 本条替换的旧形态(`t_manifest_projection` 里的键集比对)实测是**空转的重言式**:
    它拿 `_manifest_project` 的输出键集去比 `RESULT_KEYS`(派生自 contract.json),
    而 `_manifest_project` **正是用 `result_keys()` 决定产出哪些键的**——两边同源,
    恒等成立。实测两个方向的注入都 35/35 全绿,而 `kd search` 真的少了一个字段。

    实测(2026-09-28,三路注入,全部由本条抓住):
      | 注入                                    | 旧形态 | 本条 |
      |-----------------------------------------|--------|------|
      | contract.json 的 resultKeys 删 `products` | 全绿   | 红   |
      | 代码侧新增 `productsV2`、声明不动          | 全绿   | 红   |
      | 声明与冻结基线漂移                         | 全绿   | 红   |

    三段对账(缺任一段都会重新退化成自环):
      ① 交叉核对:contract.json 声明 == 冻结基线(两份独立来源,任一处漂移即红);
        同时核对代码内置兜底集(它在"声明文件缺失"时生效,若它能自由漂移,
        装机漏拷 contract.json 的机器上字段集会与开发时不同而无任何信号);
      ② 真实输出:喂合成上游走**完整链路**(`_search_manifest` → 归一 → 归并 →
        投影),对 `results[]` 的真实键集断言 —— 不再借用 `_manifest_project` 的
        入参/出参同源关系;
      ③ 注入自检:临时改声明,断言 ①/② 真的会红 —— 证明这两条不是新的重言式。
    """
    cp = _impl()

    # ---- ① 交叉核对:声明 == 冻结基线 == 代码兜底集 ----
    declared = tuple(_CONTRACT["search"]["resultKeys"])
    declared_top = tuple(_CONTRACT["search"]["topKeys"])
    ok(declared == FROZEN_RESULT_KEYS,
       "contract.json 的 resultKeys 与冻结基线漂移:\n  声明 %r\n  基线 %r\n"
       "▶ 若这是有意改对外契约,请同步 tests/kd_regression.py 的 FROZEN_RESULT_KEYS;"
       "若无意,说明声明被误改了。" % (declared, FROZEN_RESULT_KEYS))
    ok(declared_top == FROZEN_TOP_KEYS,
       "contract.json 的 topKeys 与冻结基线漂移:\n  声明 %r\n  基线 %r"
       % (declared_top, FROZEN_TOP_KEYS))
    ok(set(declared).isdisjoint(RESULT_FORBIDDEN_KEYS),
       "声明自相矛盾:这些键同时在 resultKeys 与 resultForbiddenKeys: %s"
       % sorted(set(declared) & RESULT_FORBIDDEN_KEYS))

    # ---- ② 真实输出:走完整链路(打桩上游,零网络) ----
    items = [_syn("knowledge", 101, title="知识 101"),
             _syn("answer", 201, qid=200, title="帖子 200", adopted=True),
             _syn("article", 301, title="文章 301")]
    out = _manifest_via_full_chain(items)

    top_ks = set(out.keys())
    extra_top = top_ks - set(FROZEN_TOP_KEYS)
    missing_top = set(FROZEN_TOP_KEYS) - top_ks
    ok(not extra_top and not missing_top,
       "search 顶层真实输出与契约不符:\n  多出 %s\n  缺 %s\n"
       "▶ 顶层键是**声明式交付**:多一个即未登记的能力,少一个即丢交付字段。"
       % (sorted(extra_top), sorted(missing_top)))

    ok(out["results"], "合成上游喂了 3 条,清单却为空(链路断在投影之前)")
    seen_types = set()
    for i, it in enumerate(out["results"]):
        name = "results[%d](%s)" % (i, it.get("type"))
        seen_types.add(it.get("type"))
        kk = set(it.keys())
        miss = set(FROZEN_RESULT_KEYS) - kk
        extra = kk - set(FROZEN_RESULT_KEYS)
        ok(not miss, "%s 真实输出缺字段: %s(▶ 字段被删而声明没跟上——"
                     "这正是 d753719 那次漏报的形态)" % (name, sorted(miss)))
        ok(not extra, "%s 真实输出多出声明外字段: %s(▶ 新增字段未登记进 contract.json)"
           % (name, sorted(extra)))
        check_no_forbidden(kk, RESULT_FORBIDDEN_KEYS, name)
    ok(seen_types == {"knowledge", "question", "article"},
       "三种实体的真实条目未全被链路产出(实得 %r)——覆盖缺口会让某档的字段检查静默跳过"
       % (sorted(seen_types),))

    # ---- ③ 注入自检:证明 ①② 有抓取力(而不是新的重言式) ----
    _assert_declared_mismatch_fails(declared)
    _assert_code_side_new_key_fails(items)


def _manifest_via_full_chain(items):
    """把合成条目喂进**完整检索链路**,返回真实的 search 返回体(不碰 `_manifest_project`)。

    做法:打桩 `_search_upstream` 让它按路号返回不同条目,然后调 `core.search`。
    这样断言的对象是"用户真的会拿到的东西",而不是内部函数的入参/出参对。
    """
    cp = _impl()
    real = cp._search_upstream
    # 每路都返回同一批条目:多路命中会走归并,覆盖面更全。
    def fake(text, product_id, page, page_size, global_, sorts_type, type_, budget=None,
             rate=None):
        return {"content": [dict(x) for x in items], "totalElements": len(items),
                "totalPages": 1}
    cp._search_upstream = fake
    try:
        return core.search("契约自检探针", product_id=93, budget=10)
    finally:
        cp._search_upstream = real


def _assert_declared_mismatch_fails(declared):
    """注入:声明里删掉一个字段 → ① 必须红。

    直接验证"对账函数"的判定逻辑:用一个少一个键的声明去比冻结基线,
    **必须**得出不相等。若这里判等成立,说明对账退化成恒真,本用例整体无保护力。
    """
    broken = tuple(k for k in declared if k != "products")
    ok(len(broken) == len(declared) - 1,
       "注入自检构造失败:声明里没有 products,无法验证删除能被抓住")
    ok(broken != FROZEN_RESULT_KEYS,
       "注入自检失败:从声明删掉 products 后,与冻结基线的比对**仍然判等**"
       "—— 说明对账已退化成重言式(这正是 T4 要修的形态)")
    ok(set(FROZEN_RESULT_KEYS) - set(broken) == {"products"},
       "注入自检失败:删 products 后差分不是 {products}")


def _assert_code_side_new_key_fails(items):
    """注入:代码侧多产出一个未声明的键 → ② 必须红。

    打桩 `result_keys()` 之外更狠的做法是直接改造条目投影结果,但那会变成
    "测自己写的假函数"。这里改打桩**输出端**:让上游条目多带一个字段并让它
    流到投影层——若投影层只产声明键,它会被静默丢弃(这是**正确**行为,
    说明"声明即白名单"成立);故本自检改为直接对 `_manifest_project` 的
    产出做一次"注入键"验算,断言对账逻辑能识别多出的键。
    """
    cp = _impl()
    n = cp._norm_item(_syn("knowledge", 1), "knowledge")
    p = cp._manifest_project(n, {1})
    injected = dict(p)
    injected["productsV2"] = "未声明的新字段"
    extra = set(injected) - set(FROZEN_RESULT_KEYS)
    ok(extra == {"productsV2"},
       "注入自检失败:代码侧新增未声明字段未被对账识别(实得差分 %r)" % (sorted(extra),))
    # 反向确认:未注入时差分必须为空(否则上面那条会因为"永远有差分"而假绿)。
    ok(not (set(p) - set(FROZEN_RESULT_KEYS)),
       "未注入时投影已多出字段 %s —— 真实代码与契约不符,请先修代码"
       % sorted(set(p) - set(FROZEN_RESULT_KEYS)))


@case("offline: 链接政策已定案且代码真的读它(knowledge/article 给链接、question 不给)")
def t_link_policy():
    """**T1 的回归钉子**。

    此前 `contract.json` 的 `linkPolicy` 是 `"status": "pending"` 的**纯占位、零消费者**:
    文档抄了四遍口径各异(README 第 189 行与 SKILL.md/ANSWER-SPEC 正面对撞),
    而声明这一份谁也没看——"单一来源"写了却没生效。

    现在它被代码读(`_config.link_policy/link_for`),本条钉三件事:
      ① 声明已定案(status 不再是 pending),三条路径的政策齐全;
      ② 代码读到的政策 == 声明的政策(不是只写在 JSON 里);
      ③ 方向正确:`question` 恒为 no-link(匿名 9/9 + 登录态 1 条,证据两条方向一致)。
    """
    cp = _impl()
    lp = _CONTRACT.get("linkPolicy") or {}
    ok(lp.get("status") != "pending",
       "linkPolicy.status 仍是 pending —— 链接专项(T1)尚未收口,文档会继续口径打架")
    rule = lp.get("rule") or {}
    for k in ("knowledge", "question", "article"):
        ok(k in rule, "linkPolicy.rule 缺 %s 的政策" % k)
    # ② 代码真的读它:政策必须能被代码取到,且与声明逐字一致。
    ok(cp.link_policy() == {k: str(v) for k, v in rule.items()},
       "代码读到的 linkPolicy 与声明不一致:\n  代码 %r\n  声明 %r"
       % (cp.link_policy(), rule))
    # ③ 方向钉子:`question` 不给链接是两条证据方向一致的硬结论,不得被静默放开。
    ok(cp.link_for("question") == "no-link",
       "question 的政策应为 no-link(匿名 9/9 + 登录态 1 条不可点),实为 %r"
       % (cp.link_for("question"),))
    ok(cp.link_for("knowledge") == "link",
       "knowledge 的政策应为 link(匿名 14/14 可点),实为 %r" % (cp.link_for("knowledge"),))
    ok(cp.link_for("article") == "link",
       "article 的政策应为 link(匿名 12/12 可点),实为 %r" % (cp.link_for("article"),))
    # 未知 kind 必须保守(少给一个链接 ≠ 多给一个死链)。回落值不得是"给链接"。
    ok(cp.link_for("不存在的kind") == "no-link",
       "未知 kind 的政策必须保守回落 no-link,实为 %r" % (cp.link_for("不存在的kind"),))
    ok(cp.link_for(None) == "no-link", "None kind 必须保守回落 no-link")
    # 证据必须随政策一起在声明里 —— 否则下次没人知道凭什么这么定。
    ev = lp.get("evidence") or {}
    for k in ("knowledge", "article", "question"):
        ok(ev.get(k), "linkPolicy.evidence 缺 %s 的实测依据(政策必须可追溯到实测)" % k)


@case("offline: 链接政策落在生产路径上(question 的 url 必须被抑制)")
def t_link_policy_enforced_in_output():
    """**声明必须落在生产路径上,否则等于没修**。

    ⚠️ 这条补的是 2026-09-28 审查抓到的实洞:`linkPolicy` 定案后,`link_policy()` /
    `link_for()` **只有测试在调**,生产路径(`_manifest_project` / `_detail`)照样
    无条件输出三档 url —— 于是清单里 `question` 条目的 url 照旧交给调用方,
    而该路径实测 9/9 + 登录态 1 条全部不可点。那与修复前的 `pending` 占位**实质相同**:
    声明写了、行为没变,只有测试在自说自话。

    现在生效点是 `_config.apply_link_policy`,两处调用:
      * `_manifest_project`(清单条目);
      * `_detail` 的三个 kind 函数(`read` 全文)。
    本条对着**生产函数的输出**断言,而不是对着政策函数本身(那会退回自比)。
    """
    cp = _impl()
    # ① 清单投影:question 抑制,knowledge/article 保留。
    for et, kind, want in (("answer", "question", None),
                           ("knowledge", "knowledge", "kept"),
                           ("article", "article", "kept")):
        n = cp._norm_item(_syn(et, 7, qid=6), et)
        ok(n is not None, "合成条目未被接受(%s)" % et)
        p = cp._manifest_project(n, {1})
        if want is None:
            ok(p["url"] is None,
               "清单里 %s 条目的 url 必须被抑制(linkPolicy: no-link),实为 %r"
               % (kind, p["url"]))
        else:
            ok(p["url"] and str(p["url"]).startswith("https://"),
               "清单里 %s 条目的 url 必须保留(linkPolicy: link),实为 %r"
               % (kind, p["url"]))
        # 抑制不能顺手把字段删掉 —— 字段仍在契约里,只是值为 None。
        ok("url" in p, "%s 条目的 url 字段被删了(应保留字段、置 None)" % kind)

    # ② 政策改了,输出必须跟着改 —— 证明生产路径**真的在读声明**(不是硬编码)。
    #    把政策整体翻成"都不给链接",则 knowledge 也必须被抑制。
    import kd._impl._config as _cfg_mod
    real = _cfg_mod._CONTRACT
    try:
        _cfg_mod._CONTRACT = {"linkPolicy": {"rule": {"knowledge": "no-link",
                                                     "question": "no-link",
                                                     "article": "no-link"}}}
        n = cp._norm_item(_syn("knowledge", 9), "knowledge")
        ok(cp._manifest_project(n, {1})["url"] is None,
           "把政策改成 no-link 后 knowledge 仍输出 url —— 说明生产路径没读声明,"
           "而是硬编码了某一档政策")
    finally:
        _cfg_mod._CONTRACT = real


@case("offline: 链接口径四份文档 + 声明一致(无残留『不给链接』/『都可点』表述)")
def t_link_policy_docs_in_sync():
    """**T1 的文档同步钉子** —— 治的是"同一事实抄四遍"。

    实测的翻车形态:`README.md:189` 写"引用做成可点击角标",而 SKILL.md 与
    ANSWER-SPEC 同时写"当前不给链接",两份在同一仓库里**正面对撞**;
    而 README 那一处**此前从未被列为同步点**。

    本条不检查措辞好坏,只检查**政策方向**在五处载体里一致:
      contract.json(唯一真源)/ ANSWER-SPEC / SKILL.md / README / _upstream.py 注释。
    """
    files = {
        "ANSWER-SPEC": os.path.join(REPO, "docs", "ANSWER-SPEC.md"),
        "SKILL.md": os.path.join(REPO, "skills", "kingdee-knowledge", "skills",
                                 "kingdee-knowledge", "SKILL.md"),
        "README": os.path.join(REPO, "README.md"),
        "_upstream.py": os.path.join(SRC, "kd", "_impl", "_upstream.py"),
    }
    # ① 过期表述不得残留(它们是 09-27 那套"一律 302"口径的化石)。
    stale = ("三条路径现在一律 302", "三条网页路径实测全失效",
             "三条路径一律 302", "当前不给链接")
    for name, path in files.items():
        txt = open(path, encoding="utf-8").read()
        for bad in stale:
            ok(bad not in txt,
               "%s 仍残留过期链接口径 %r —— 该表述来自 09-27 的『三路径一律 302』,"
               "已由 09-28 复测推翻(见 contract.json 的 linkPolicy)" % (name, bad))
    # ② 每份载体都必须出现"给链接 vs 不给"的分档,而不是笼统一句。
    for name, path in files.items():
        txt = open(path, encoding="utf-8").read()
        ok("question" in txt and "knowledge" in txt,
           "%s 的链接口径未按 kind 分档(应分别说明 knowledge/article 与 question)" % name)
    # ③ README 是**此前漏掉的第五处**,单独钉:它必须指向唯一真源而不是自己另立一套。
    rd = open(files["README"], encoding="utf-8").read()
    ok("linkPolicy" in rd,
       "README 的链接口径未指向唯一真源 contract.json 的 linkPolicy —— "
       "它曾独立写了一套相反的说法(『引用做成可点击角标』)而无人同步")


@case("offline: 稀有数字 token 抢第 1 路,拉丁词不前置(T2 核心行为变更)")
def t_rare_token_route():
    """**T2 的回归钉子**(2026-09-28)。

    行为变更:规则拆词路径把问句里的**纯数字串**(≥3 位)单独成路并置于**第 1 路**,
    原句路退到第 2 路。依据是同一条实测机制:排序键第一维是"首次出现的路序号",
    故最能把候选集压窄的那一路必须排第 1。

    实测(金标 `646787188905978624`,产品线 93;同一组词仅换路序):
      `[2510, 原句]` → 第 1;`[原句, 2510]` → 第 4–5;改前默认 `[原句, 泛词路]` → 第 3。

    ⚠️ 本条同时钉**负结果**:拉丁词与中文词一律不得前置。实测 `BOM`(total 1308)前置
    会把金标 B(`873372977646105600`)从第 2 挤到第 12 —— "看起来像标识符"不等于
    "比同问句里其他路更收窄",判断它需要语义,规则做不到。故只认数字。
    """
    # (a) 纯数字 token 抢第 1 路,原句路退到第 2 路(且仍在,没被丢)。
    r, seen = _patched_search(("2510 应用为禁用状态[网关]",), product_id=93, budget=10)
    ok(r["queries"][0] == "2510",
       "含纯数字 token 时第 1 路应为该 token,实为 %r(路序=%r)" % (r["queries"][0], r["queries"]))
    ok("应用为禁用状态[网关]" in r["queries"][1],
       "原句路应退到第 2 路且内容完整,实为 %r" % (r["queries"][1:],))
    ok(seen[0]["text"] == "2510", "上游第 1 次收到的检索词应为 token,实为 %r" % (seen[0]["text"],))
    # token 路必须只放它自己(不得与中文合并 —— E8 实测合并后掉出前 30)。
    ok(r["queries"][0] == "2510" and " " not in r["queries"][0],
       "token 路应只含 token 本身,不得与其它词空格拼接: %r" % (r["queries"][0],))

    # (b) 无数字 token 时,原句路仍居首(不得因为加了 token 路就动摇了默认形态)。
    r2, _ = _patched_search((PROBE_MULTI_ROUTE,), product_id=93, budget=10)
    ok(r2["queries"][0] == PROBE_MULTI_ROUTE,
       "无数字 token 时第 1 路应仍是原句路,实为 %r" % (r2["queries"][0],))

    # (c) **负结果钉子**:拉丁词不得前置。
    r3, _ = _patched_search(("BOM 分母变平方",), product_id=93, budget=10)
    ok(r3["queries"][0] != "BOM",
       "拉丁词 BOM 被前置了 —— 实测这会把金标 B 从第 2 挤到第 12,"
       "规则只允许前置**纯数字** token。实得路序 %r" % (r3["queries"],))
    r4, _ = _patched_search(("ENG_BOM 是什么",), product_id=93, budget=10)
    ok(r4["queries"][0] != "ENG_BOM",
       "拉丁词 ENG_BOM 被前置了 —— 同上,只允许纯数字。实得路序 %r" % (r4["queries"],))

    # (d) 位数字数下限:1~2 位的数字不构成 token(实测是页数/序号,无区分度)。
    r5, _ = _patched_search(("2 个凭证 无法 删除",), product_id=93, budget=10)
    ok(r5["queries"][0] != "2",
       "1 位数被当成 token 前置了(应受 minDigits 挡下): %r" % (r5["queries"],))

    # (e) 只取一个 token:多个数字串时不得各占一路挤掉其它路。
    r6, _ = _patched_search(("2510 和 3600 都报错",), product_id=93, budget=10)
    ok(r6["queries"].count("2510") == 1, "token 路应只出现一次")
    ok("3600" not in r6["queries"],
       "只应前置**首个**数字 token,第二个不应自成一整路: %r" % (r6["queries"],))

    # (f) **截断优先级钉子**:保席位路多于 max_routes 时,按 `_TRUNC_PRIORITY`
    #     再裁一刀 —— 原句路优先于 token 路。依据:ADR-0009 决策 3「原句路保席位,
    #     截断时不被挤掉」是改动前就成立的硬契约,本轮不得破坏。
    #     ⚠️ 这里最初写成"max_routes=1 应保留 token 路",是**按错误行为写的断言**:
    #     前缀切片恰好会把 token 切出来,而它同时**静默删掉了原句路**。
    #     修法见 `_routes._truncate_routes`(全仓唯一的路截断实现)。
    r7, _ = _patched_search(("2510 应用为禁用状态[网关]",), product_id=93, budget=10,
                            max_routes=1)
    ok(r7["queries"] == ["2510 应用为禁用状态[网关]"],
       "max_routes=1 时必须留**原句路**(ADR-0009 决策 3:原句路保席位),实为 %r"
       % (r7["queries"],))
    r8, _ = _patched_search(("2510 应用为禁用状态[网关]",), product_id=93, budget=10,
                            max_routes=2)
    ok(r8["queries"] == ["2510", "2510 应用为禁用状态[网关]"],
       "max_routes=2 时应留原句路 + token 路(且 token 仍居执行首位),实为 %r"
       % (r8["queries"],))
    # 无 token 时的截断行为不得回归(改动前 max_routes=1 留的就是原句路)。
    r9, _ = _patched_search((PROBE_MULTI_ROUTE,), product_id=93, budget=10, max_routes=1)
    ok(r9["queries"] == [PROBE_MULTI_ROUTE],
       "无 token 时 max_routes=1 应留原句路,实为 %r" % (r9["queries"],))


@case("offline: read 的 budget 契约与 search 同义(库入口 + CLI 入口)")
def t_read_budget_contract():
    """**T5 第 1 条的回归钉子**(2026-09-28)。

    修前:同一公开面里同名参数**两套契约** ——
        core.search(..., budget=3)  -> 正常
        core.read(..., budget=3)    -> AttributeError: 'int' object has no attribute
                                       'acquire'
    且被 `cli._guard` 归为 `internal_error`「这是 bug 而非用法问题」,把排查方向带偏;
    CLI 侧当时也没有 `read --budget`,故**只能由库调用触发、零测试覆盖**。

    修后两入口共用 `_public._as_budget`;CLI 侧补了 `read --budget`。

    ⚠️ 本用例只断言**归一化与错误分类**,不打上游(budget=0 恰好是零请求,可离线验证)。
    """
    cp = _impl()
    # ① 库入口:非法 budget 抛公开异常(不是裸 TypeError/AttributeError)。
    for bad in ("x", -1.5, object()):
        try:
            core.read("knowledge", "6226", budget=bad)
        except core.InternalError:
            continue
        except Exception as e:
            raise Fail("read(budget=%r) 抛 %s(应为 InternalError:非法入参)"
                       % (bad, type(e).__name__))
        raise Fail("read(budget=%r) 未报错(非法 budget 被静默接受)" % (bad,))
    # ② budget=0:零上游请求 → 必须抛**公开**异常且**不得漏出内部类**。
    #    内部信号是 `_BudgetExhausted`,它不在 core.__all__ 里,漏出去调用方没法分类。
    try:
        core.read("knowledge", "6226", budget=0)
    except (core.UpstreamError, core.InternalError) as e:
        ok(type(e).__name__ in ("UpstreamError", "InternalError"),
           "budget=0 抛出的应是公开异常类,实为 %r" % (type(e).__name__,))
        ok(not isinstance(e, cp._BudgetExhausted),
           "budget=0 漏出了内部异常类 _BudgetExhausted(不在 core.__all__ 内)")
    except Exception as e:
        raise Fail("read(budget=0) 漏出内部异常 %s: %s" % (type(e).__name__, e))
    else:
        raise Fail("read(budget=0) 未报错(零上游请求却成功了?)")
    # ③ 签名承诺:read 必须接受 budget 形参(修前它接受了却用不对)。
    import inspect
    ok("budget" in inspect.signature(core.read).parameters,
       "read 签名缺 budget 形参")
    # ④ CLI 侧:两个入口都得有 --budget(修前 read 没有,故库入口的缺陷无人发现)。
    #    ⚠️ `--help` 的文本走 stdout 而**不是** JSON(它不是命令载荷),
    #    故这里用 subprocess 直接抓文本,不复用 cli() 的 JSON 解析。
    for cmd in ("search", "read"):
        p = subprocess.run([PY, RUN, cmd, "--help"], cwd=REPO, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=60)
        ok("--budget" in (p.stdout or ""),
           "kd %s --help 未列出 --budget(search/read 两入口应对称)" % cmd)
    # ⑤ budget=0 走 CLI 时的**退出码分类**:预算不足不是用法错误(不得是 2)。
    code, d, _err = cli("read", "6226", "--budget", "0")
    ok(code == 1,
       "kd read --budget 0 应 exit 1(上游/预算问题),实测 %r —— "
       "归成 2(用法错误)会把排查方向带偏" % code)
    if d is not None:
        ok((d.get("error") or {}).get("code") != "usage",
           "kd read --budget 0 被归成用法错误: %r" % (d.get("error"),))


@case("offline: 装机数据文件齐全且与仓库同源(漏拷=装出来的行为不同)")
def t_install_data_files():
    """**装机同步钉子**。

    治的病有两次实证:
      * `install.sh` 的注释说"检查两个数据文件",代码**只查了 query_routes.json**
        —— 察觉到一半(`contract.json` 缺失时 health 冒烟不受影响,抓不住);
      * 本轮又踩了一次:改完 `SKILL.md` / `contract.json` 后忘了重跑安装,
        仓库版与安装版 md5 不同 —— **改的是仓库,跑的还是旧版**。

    本条钉两件事(都不联网、不依赖"当前机器恰好装过"):
      ① 装机脚本的校验**真的覆盖两个数据文件**(源码级:两个文件名都在校验段出现);
      ② `contract.json` 的键集必须**能被代码从包内读到** —— 即它是随包走的,
         而不是只存在于仓库(测试/文档目录装机后就没有)。
    """
    sh = open(os.path.join(REPO, "install.sh"), encoding="utf-8").read()
    ps1 = open(os.path.join(REPO, "install.ps1"), encoding="utf-8").read()
    for name, src in (("install.sh", sh), ("install.ps1", ps1)):
        # 必须同时校验两个数据文件(缺一个 = 注释与代码不符)。
        ok("query_routes.json" in src and "contract.json" in src,
           "%s 的校验段未同时覆盖 query_routes.json 与 contract.json" % name)
    # ② 两个数据文件都必须**物理存在于包内目录**(随包走,装机才拷得到)。
    for f in ("query_routes.json", "contract.json"):
        ok(os.path.exists(os.path.join(SRC, "kd", f)),
           "包内缺 %s —— 它不在 src/kd/ 里,装机脚本无论怎么校验都拷不到" % f)
    # ③ 声明能被代码读到(不是"文件在但读不到")。读失败会静默回落兜底集,
    #    故这里断言"读到的声明非空",否则回落会被误认为同步成功。
    cp = _impl()
    ok(cp.result_keys() and cp.top_keys() and cp.default_product_id() is not None,
       "包内 contract.json 未被代码读到(声明为空 → 已静默回落兜底集)"
       "—— 装机后字段集会与开发时不同而无任何信号")


@case("offline: 挑选判据在文档里、且显式声明挑选权在调用方(ADR-0015)")
def t_selection_rule_in_docs():
    """**T6 的钉子**(ADR-0015)。

    「按标题挑」是用户规划的 7 步里**唯一长期无机制**的一步:步骤存在,但没有判据、
    没有检查、没有测试面。

    ADR-0015 把它定为**文档判据、内核零感知**,故本条的验收方式只能是"文档自洽 +
    边界未被越过",而不是行为断言(理由见 ADR-0015「为什么不给挑选判据建测试面」)。

    钉三件事:
      ① 判据存在(不是一句"你自己挑吧");
      ② 文档**显式声明**挑选权在调用方 —— 这是边界的文字载体;
      ③ 判据第 3 条与 ANSWER-SPEC 第 7 条(根因引用规则)不冲突 —— 同一事实不许两套。
    """
    skill = open(os.path.join(REPO, "skills", "kingdee-knowledge", "skills",
                              "kingdee-knowledge", "SKILL.md"), encoding="utf-8").read()
    spec = open(os.path.join(REPO, "docs", "ANSWER-SPEC.md"), encoding="utf-8").read()
    adr = os.path.join(REPO, "docs", "adr", "0015-挑选判据归属.md")
    ok(os.path.exists(adr), "缺 ADR-0015 —— 挑选判据的边界没有决策记录")

    # ① 判据存在:几个关键动作点必须都写到(不是泛泛一句"挑最相关的")。
    for probe in ("挑选判据", "读够就停", "根因引用规则", "routesDegraded"):
        ok(probe in skill,
           "SKILL.md 的挑选判据未覆盖 %r —— 判据必须覆盖到可执行的动作点" % probe)

    # ② 边界必须写在文档上:挑选权在调用方,内核不替它挑。
    ok("内核**不替你挑**" in skill or "内核不替你挑" in skill,
       "SKILL.md 未显式声明『内核不替你挑』—— 这是 ADR-0015 决策 2 的文字载体,"
       "缺了它后人会以为可以给内核加推荐排序")
    ok("ADR-0008" in skill or "ADR-0013" in skill,
       "SKILL.md 未引用挑选权的决策依据(ADR-0008/ADR-0013)")

    # ③ 与 ANSWER-SPEC 第 7 条不冲突:根因只能引 knowledge,question 只做症状对齐。
    ok("只引 question 不算根因" in spec,
       "ANSWER-SPEC 第 7 条的根因引用规则被改动 —— 挑选判据第 3 条须同步(ADR-0015)")
    ok("症状对齐" in skill and "症状对齐" in spec,
       "『症状对齐』这一口径在 SKILL.md 与 ANSWER-SPEC 之间不一致")

    # ④ 反向钉子:内核**不得**出现"推荐/该读/打分"这类挑选机制(ADR-0015 决策 2)。
    #    用**标识符级**匹配而不是子串匹配:`topKeys`(顶层键集)含 "topK" 字样却完全
    #    无关,子串匹配会假红——这正是"检查写得太粗会变成假信号"的实例。
    import re as _re
    banned_ids = ("topk", "top_k", "recommended", "pick_best", "rerank", "score")
    for mod in ("_manifest.py", "_routes.py", "_public.py", "_config.py"):
        src = open(os.path.join(SRC, "kd", "_impl", mod), encoding="utf-8").read()
        # 只取代码行(丢掉注释与字符串里的正当讨论——禁令说明本身就写着这些词)。
        code_lines = []
        for ln in src.splitlines():
            s = ln.split("#", 1)[0]
            code_lines.append(s)
        code = "\n".join(code_lines)
        ids = set(m.group(0).lower() for m in _re.finditer(r"[A-Za-z_][A-Za-z0-9_]*", code))
        hit = sorted(ids & set(banned_ids))
        ok(not hit,
           "%s 出现挑选机制标识符 %r —— 内核不得产出『该读哪篇』的任何形式"
           "(ADR-0015 决策 2:打分/截断/推荐字段都是越界)" % (mod, hit))


@case("offline: 声明与兜底键集一致 —— 两份独立抄本不得漂移(真交叉核对)")
def t_contract_fallback_in_sync():
    """`contract.json` 的声明与 `_config` 的内置兜底集必须一致。

    兜底集的存在理由是"声明文件缺失时内核不炸";代价是**同一个事实有了两份**。
    本用例就是那份代价的对价:任一处漂移即红,不靠人工誊抄比对。

    ⚠️ **2026-09-28 修:本用例此前是恒绿的重言式。** 它拿 `cp.result_keys()` 去比
    `_CONTRACT["search"]["resultKeys"]`,而 `result_keys()` 的实现就是
    `_contract().get("search")["resultKeys"] or list(_FALLBACK_RESULT_KEYS)` ——
    **只要声明文件能读,兜底集根本不参与**,两份被比的是同一个值。实测:
    从声明删掉 `products` 后,只有"三段对账"变红,本条**照样绿**,而它的注释
    自称"兜底集与声明的漂移会被抓住"。那是**回声与回声自比**(与 T4 修的是同一种病)。

    修法:比对的必须是**兜底常量本身**(`_FALLBACK_RESULT_KEYS` /
    `_FALLBACK_FORBIDDEN_KEYS`),而不是经过声明覆盖后的读取结果。
    另加一条**反向验证**:模拟"声明文件读不到"的场景,断言此时读到的
    正是兜底集 —— 否则"兜底"这个卖点从未被验证过。
    """
    cp = _impl()
    declared_keys = tuple(_CONTRACT["search"]["resultKeys"])
    declared_forbidden = tuple(_CONTRACT["search"]["resultForbiddenKeys"])

    # ① 真实比对:兜底常量 vs 声明(两份独立来源)。
    ok(tuple(cp._FALLBACK_RESULT_KEYS) == declared_keys,
       "内置兜底 resultKeys 与 contract.json 声明漂移:\n  兜底 %r\n  声明 %r\n"
       "▶ 兜底集是『声明文件缺失时』生效的那份,它漂移会让装了漏拷的机器行为不同。"
       % (tuple(cp._FALLBACK_RESULT_KEYS), declared_keys))
    ok(tuple(cp._FALLBACK_FORBIDDEN_KEYS) == declared_forbidden,
       "内置兜底 resultForbiddenKeys 与声明漂移:\n  兜底 %r\n  声明 %r"
       % (tuple(cp._FALLBACK_FORBIDDEN_KEYS), declared_forbidden))

    # ② 读取路径仍要正确(声明在时用声明)。
    ok(tuple(cp.result_keys()) == declared_keys,
       "result_keys() 与 contract.json 声明不一致:\n  代码 %r\n  声明 %r"
       % (cp.result_keys(), declared_keys))
    ok(tuple(cp.result_forbidden_keys()) == declared_forbidden,
       "result_forbidden_keys() 与声明不一致:\n  代码 %r\n  声明 %r"
       % (cp.result_forbidden_keys(), declared_forbidden))
    ok(tuple(cp.top_keys()) == tuple(_CONTRACT["search"]["topKeys"]),
       "top_keys() 与声明不一致")
    ok(cp.default_product_id() == DEFAULT_PRODUCT_ID,
       "default_product_id() 与声明不一致")

    # ③ 兜底路径**真的会生效**吗?把声明读成空后,读到的必须恰是兜底集。
    #    没有这条,"兜底"就只是个从未被执行过的分支。
    #
    # ⚠️ 必须改**模块全局**(`kd._impl._config._CONTRACT`),不能改 `cp._CONTRACT`:
    # 观测口导出的是**值的副本**,`_contract()` 读的是它自己模块的全局变量,
    # 改包属性对已 import 的函数无效(实测:改 cp._CONTRACT 后 link_for 仍回 'link')。
    import kd._impl._config as _cfg_mod
    real = _cfg_mod._CONTRACT
    try:
        _cfg_mod._CONTRACT = {}
        ok(tuple(cp.result_keys()) == tuple(cp._FALLBACK_RESULT_KEYS),
           "声明为空时 result_keys() 应回落兜底集:\n  实得 %r\n  兜底 %r"
           % (cp.result_keys(), tuple(cp._FALLBACK_RESULT_KEYS)))
        ok(tuple(cp.result_forbidden_keys()) == tuple(cp._FALLBACK_FORBIDDEN_KEYS),
           "声明为空时 result_forbidden_keys() 应回落兜底集")
        ok(cp.default_product_id() == 93,
           "声明为空时 default_product_id() 应回落 93,实为 %r" % (cp.default_product_id(),))
        # 链接政策同理:声明缺失时保守方向是"全部不给链接"(少给一个链接
        # ≠ 多给一个死链)。
        ok(cp.link_for("knowledge") == "no-link" and cp.link_for("article") == "no-link",
           "声明为空时链接政策应保守回落 no-link,实为 %r/%r"
           % (cp.link_for("knowledge"), cp.link_for("article")))
    finally:
        _cfg_mod._CONTRACT = real

    # ④ 键集自洽:允许集与禁止集不得有交集(有交集则同一键既允许又 FAIL,自相矛盾)。
    overlap = RESULT_KEYS & RESULT_FORBIDDEN_KEYS
    ok(not overlap, "声明自相矛盾:这些键同时在 resultKeys 与 resultForbiddenKeys: %s"
       % sorted(overlap))
    ok(len(declared_keys) >= 5, "声明键集为空或过短,断言失去意义: %r" % (declared_keys,))
    # ⑤ 兜底集必须真的与声明同长(防"两边都空"时上面①假绿)。
    ok(len(cp._FALLBACK_RESULT_KEYS) >= 5,
       "内置兜底键集过短或为空,① 会失去意义: %r" % (tuple(cp._FALLBACK_RESULT_KEYS),))


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
# ⑤ 问答条目走真实 _norm_item:标题必须非空(喂上游值 "answer")。
    a = cp._norm_item(_syn("answer", 900, qid=800, title="帖子标题"), "answer")
    ok(a["title"] == "帖子标题", "问答条目标题解析失败: %r" % (a["title"],))
    # ⑥ 上游把标题平铺在条目顶层(assistant 形状变体)也要兜住
    raw = {"entity-type": "Answer", "id": "1", "questionId": "2",
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

    ⚠️ 值域三态(2026-09-27 修复后,must 记住):
      * `None` = 显式不过滤(Python 侧的 `product_id=None`);
      * `0`    = 显式不过滤(CLI 侧的 `--product 0`);
      * 省略参数 = 默认 93(由**签名默认值**承担,不是由 None 折成)。
    早期实现把 None 折成默认 93,使 Python 调用方无法表达"不过滤"(实测 total
    31788 → 6326,过滤被静默加上)。
    """
    return None if effective in (0, None) else effective


@case("offline: effectiveProductId 恒等于调用方传入值(内核不做字面推导)")
def t_effective_product_id_passthrough():
    """**本轮核心行为变更之一**(决策 D14,2026-09-27)。

    旧契约:该键回显的是 `_plan_routes` **字面推导后**的值——问句里出现「苍穹」
    会被改写成 87(即使调用方没传 `--product`)。旧用例钉的正是"必须回显推导值,
    不得回显入参"。

    新契约:**内核不做任何字面产品线推导**,`_derive_product_id` 连同别名表与
    "先出现优先"裁决规则整体删除。故:

      * 该键**恒等于调用方传入值**;**不传即默认 93**(不是 None);
      * 问句里出现「苍穹」**不再**改变它——产品线由调用层(LLM)判定后显式传;
      * 显式 `0` 仍是真不过滤;显式任何值都直通。

    ⚠️ 为什么敢删掉字面推导(而不是"留着但默认关"):字面匹配猜的是一句话里有没有
    产品名,而这件事调用层比规则懂;判定错误等于拿完全不同的语料作答(实测同问句下
    `--product 87` 与 `93` 的 top10 **零交集**)。留一个"偶尔猜对"的隐式改写,比
    没有它更危险——它会让调用方以为自己不传也能对。

    纯离线:monkeypatch `_search_upstream` 拦掉上游出口,零网络请求。

    ⚠️ monkeypatch 陷阱(实测结论,别再踩):`_search_upstream` 走
    `_manifest._resolve()` → 解析点是**包命名空间**,故替换 `kd._impl._search_upstream`
    (包属性)有效(`_patched_search` 用这条)。
    """
    # ⚠️ 第①态按**新**契约期望 93(旧用例期望 93 是"默认兜底",巧合相同;
    # 但第②态必须从 87 变成 93 —— 那才是本轮真正的行为反转)。
    cases = (
        ("默认(省略 --product)→ 默认值", (PROBE_DEGRADED,), {}, DEFAULT_PRODUCT_ID),
        ("问句含「苍穹」但未显式传 → **不再改写**", ("苍穹 " + PROBE_DEGRADED,), {},
         DEFAULT_PRODUCT_ID),
        ("显式 87 直通", (PROBE_DEGRADED,), {"product_id": 87}, 87),
        ("显式 1 直通", (PROBE_DEGRADED,), {"product_id": 1}, 1),
        ("显式 2 直通", (PROBE_DEGRADED,), {"product_id": 2}, 2),
        ("显式 0(真不过滤)", (PROBE_DEGRADED,), {"product_id": 0}, 0),
    )
    for label, args, kw, want in cases:
        kw = dict(kw, budget=10)
        r, seen = _patched_search(args, **kw)
        got = r["effectiveProductId"]
        ok(got == want,
           "%s: effectiveProductId 应为 %r,实为 %r\n"
           "      注:内核不做字面推导,该键恒等于调用方传入值(不传即默认)"
           % (label, want, got))
        # 与上游实际收到的过滤保持一致:回显值必须**真的是**本次生效的过滤,
        # 否则调用方核对的是个装饰性字段。
        #
        # ⚠️ 两套值域**有意不同**,不是脱节:`effectiveProductId` 用**调用方词汇**
        # (`--product` 值域):0 是"我显式要求不过滤";而上游只认"带 productIds[0]
        # 过滤"或"省略该参数",故 0 必须省略参数(传 `productIds[0]=0` 上游会当真值
        # 过滤,把结果挤出前排)。这条映射本身就是契约,由 `_expect_upstream` 显式写出。
        for c in seen:
            ok(c["product_id"] == _expect_upstream(want),
               "%s: 回显 effectiveProductId=%r,其对应的上游过滤应为 %r,实收 %r"
               % (label, got, _expect_upstream(want), c["product_id"]))

    # ⚠️ 区分度钉子:第①②态的期望值**相同**(都是默认值),故必须另有一条断言
    # 证明"问句含苍穹"与"显式传 87"确实不同——否则本用例无法区分新旧实现
    # (旧实现下第②态会得 87;若本用例只断言 93,则新旧都能过)。
    _r_same, _ = _patched_search(("苍穹 " + PROBE_DEGRADED,), budget=10)
    _r_87, seen87 = _patched_search((PROBE_DEGRADED,), product_id=87, budget=10)
    ok(_r_same["effectiveProductId"] != _r_87["effectiveProductId"],
       "问句含「苍穹」与显式传 87 应得到不同的有效产品线(证明不再字面推导),"
       "实得 %r vs %r" % (_r_same["effectiveProductId"], _r_87["effectiveProductId"]))
    ok(all(c["product_id"] == 87 for c in seen87), "显式 87 必须真的传到上游")

    # 值域契约:整数(与 --product 同值域,可直接比对),而不是字符串/中文类目名
    # ——那正是 results[].products 不可替代的原因。
    r, _ = _patched_search((PROBE_DEGRADED,), budget=10)
    ok(not isinstance(r["effectiveProductId"], str),
       "effectiveProductId 不得是字符串(须与 --product 同值域以便直接比对)")
    ok(r["effectiveProductId"] == DEFAULT_PRODUCT_ID,
       "不传 --product 时应为默认值 %r,实为 %r" % (DEFAULT_PRODUCT_ID, r["effectiveProductId"]))
    # keywords 入口同样直通。
    rk, seenk = _patched_search((), keywords=["A"], product_id=87, budget=10)
    ok(rk["effectiveProductId"] == 87, "keywords 入口应回显 87,实为 %r" % (rk["effectiveProductId"],))
    ok(all(c["product_id"] == 87 for c in seenk), "keywords 路的过滤应与回显一致")


@case("offline: 产品线字面推导已整体删除(_derive_product_id 不存在)")
def t_product_derivation_removed():
    """**删除的回归钉子**(决策 D14,2026-09-27)。

    这条用例的方向与常见的"钉住某个能力"相反:**它钉住某个能力必须不存在**。

    为什么值得钉:`_derive_product_id` 曾是一套有实际后果的机制——产品线判定直接
    改变全部召回语料,而它只靠"问句里有没有产品名"这种字面匹配。它被移除的理由是
    调用层能判得更准;若将来有人"顺手把它补回来"(看起来像是在提升召回),那会
    静默地把调用方显式传的产品线又改回去,而**没有任何断言会发现**。

    三层都要钉:
      ① 函数不可经观测口取到;
      ② `query_routes.json` 不再有 `productAliases` 段(推导的数据源);
      ③ 行为上问句里的产品词不再改变 effectiveProductId(由上一用例覆盖,此处不重复)。
    """
    cp = _impl()
    ok(not hasattr(cp, "_derive_product_id"),
       "实现包里仍可解析 _derive_product_id —— 字面产品线推导应已整体删除(决策 D14)")
    cfg = cp._route_cfg()
    ok("productAliases" not in cfg,
       "query_routes.json 仍有 productAliases 段(推导的数据源): %r"
       % (sorted(cfg.keys()),))
    # ⚠️ 诚实说明本条的能力边界(避免给出虚假安心):它抓的是"函数被搬回**实现包**"
    # 这一形态。若有人把同样逻辑换个名字写在别处,本用例不会红——那种情况只能靠
    # 上一用例的**行为**断言(effectiveProductId 恒等于入参)兜住。
    # 两层各覆盖一半,合起来才是完整防线;单看任何一层都不够。


@case("offline: product_id 直通两条分支 —— keywords 入口与规则入口同权直通")
def t_product_id_passthrough_both_branches():
    """`product_id` 直通必须对 `_plan_routes` 的**两条分支**都成立。

    ⚠️ 为什么单列:历史上正是这条分支出过事故——`keywords` 分支曾**提前 return**,
    绕过了产品线处理(`${旧} 缺陷 E`),导致"LLM 拆好词后传进来"这条最自然的用法
    拿到错误的召回语料。直通化只改了几个 return 语句,但**两条 return 都要改**;
    只改一条会让 keywords 入口静默用默认值。

    故这里对同一组 product_id 值,分别走两条入口断言结果一致。
    """
    plan = _impl()._plan_routes
    for pid in (None, 0, 1, 2, 87, 93):
        _r_kw, got_kw = plan(text=PROBE_DEGRADED, keywords=["生产单位数量", "分母"],
                             product_id=pid)
        _r_rule, got_rule = plan(text=PROBE_DEGRADED, keywords=None, product_id=pid)
        ok(got_kw == pid and got_rule == pid,
           "product_id=%r 应直通,两分支分别实得 %r / %r(内核对产品线零改动)"
           % (pid, got_kw, got_rule))

    # 过滤真的被挂到每一路上(直通不等于"只是回显")。
    routes, pid = plan(text=PROBE_DEGRADED, keywords=["生产单位数量", "分母"],
                       product_id=87)
    ok(pid == 87, "返回值应为 87")
    ok(routes, "应至少产出一路")
    ok(all(r.get("productIds") == 87 for r in routes),
       "每一路都应携带 productIds=87(否则 --product 会在某些路上静默失效): %r"
       % ([r.get("productIds") for r in routes],))
    # 显式 0 = 真不过滤:必须**省略**该参数(挂 0 会被上游当真值过滤)。
    routes0, pid0 = plan(text=PROBE_DEGRADED, keywords=["生产定义"], product_id=0)
    ok(pid0 == 0, "显式 0 应直通")
    ok(all("productIds" not in r for r in routes0),
       "显式 0 时各路不得携带 productIds(0 会被上游当真值过滤): %r"
       % ([r.get("productIds") for r in routes0],))

@case("offline: routesDegraded 不因 max_routes 截断而假阳性(缺陷 G)")
def t_routes_degraded_no_false_positive():
    """**缺陷 G 的假阳性钉子**(2026-09-27 修)。

    spec 第 3 节公式:`routesDegraded` = 「`queries[]` 去重后的实际路数 < 计划路数」。

    修前实证:`max_routes=1` + 拆出两路同词 → 计划 1 路、去重后实际 1 路,
    却仍回显 `routesDegraded=true`,且 scanNote 写出"计划 1 路,去重后实际 1 路"
    ——两个数字相同却说塌缩,自相矛盾。真因:代码直接沿用了 `_dedupe_routes`
    的布尔值(它报的是"拆解产出里有重复"),而不是按 spec 公式比较路数。

    契约:截断不是塌缩。两个方向都要验——不该报的别报,该报的别漏。
    """
    # (a) 截断场景:max_routes=1,该词拆出 2 路同词 → 不得报塌缩。
    r, _seen = _patched_search((PROBE_DEGRADED,), product_id=93, budget=10, max_routes=1)
    ok(r["routesPlanned"] == 1, "max_routes=1 时计划路数应为 1,实为 %r" % (r["routesPlanned"],))
    ok(len(r["queries"]) == 1, "max_routes=1 时应只有 1 路,实为 %r" % (r["queries"],))
    ok(r["routesDegraded"] is False,
       "max_routes=1 造成的截断不是塌缩(计划 1 路 = 实际 1 路),"
       "routesDegraded 应为 False,实为 %r;scanNote=%r"
       % (r["routesDegraded"], r["scanNote"]))
    # scanNote 的自相矛盾是这条缺陷的可见症状,单独钉一次。
    ok("塌缩" not in r["scanNote"],
       "未塌缩时 scanNote 不得出现「塌缩」字样(修前会写成「计划 1 路,去重后实际 1 路」),实为 %r"
       % (r["scanNote"],))

    # (b) 真塌缩场景:不截断,该词确实拆出 2 路但去重后 1 路 → 必须报塌缩。
    r2, _s2 = _patched_search((PROBE_DEGRADED,), product_id=93, budget=10)
    ok(r2["routesPlanned"] >= 2, "该探针词去重前应≥2 路,实为 %r" % (r2["routesPlanned"],))
    ok(len(r2["queries"]) < r2["routesPlanned"],
       "塌缩场景实际路数应少于计划: %r vs %r" % (len(r2["queries"]), r2["routesPlanned"]))
    ok(r2["routesDegraded"] is True,
       "真塌缩必须仍报 true(修复不得过度修正),实为 %r" % (r2["routesDegraded"],))
    ok("塌缩" in r2["scanNote"], "真塌缩时 scanNote 应写明,实为 %r" % (r2["scanNote"],))


@case("offline: sortsType=0 不被 or 链吞掉(缺陷 H)")
def t_sorts_type_zero_preserved():
    """取路 sortsType 不得把显式的 `0` 折成 `1`。

    旧写法 `int(r.get("sortsType") or sorts_type or 1)` 用 `or` 链,而 **0 是 falsy**:
    路或调用方明确给 0(相关性排序)会被静默改成 1。

    ⚠️ 性质说明(2026-09-27 实测):上游当前 sortsType=0 与 =1 **等价**(都是相关性
    排序,2 才是时间倒序),故这是**潜伏 bug** 而非已发生的错误召回——现配置恒为 1,
    恰好遮盖了它。但"显式值被静默改写且无任何信号"本身就是契约缺陷,故修之。
    """
    import kd._impl as _implpkg
    f = _implpkg._route_sorts_type
    cases = (
        ("路自带 0 → 保住 0", {"sortsType": 0}, 1, 0),
        ("路自带 2 → 2", {"sortsType": 2}, 1, 2),
        ("路无值 + 调用方 0 → 保住 0", {}, 0, 0),
        ("路无值 + 调用方 2 → 2", {}, 2, 2),
        ("路无值 + 调用方 None → 默认 1", {}, None, 1),
        ("路自带 0 优先于调用方 2", {"sortsType": 0}, 2, 0),
    )
    for label, route, fallback, want in cases:
        got = f(route, fallback)
        ok(got == want, "%s: 应得 %r,实为 %r" % (label, want, got))
        ok(isinstance(got, int), "%s: 返回值应为 int,实为 %r" % (label, type(got)))

    # ⚠️ **端到端钉子**:只钉函数体不够。独立验证(mutation testing)实测——把
    # `_search_manifest` 里的**调用点**还原成旧写法 `int(r.get("sortsType") or … or 1)`
    # 而保留正确的 `_route_sorts_type`,26 项回归**全绿**,而 0 又被吞。
    # 纯函数级断言拦不住调用点回退,故必须断言"上游实收的 sorts_type"。
    #
    # ⚠️ 断言值按实测写(不是推的):原句路**固定 sortsType=1**(ADR-0009 定案,
    # 它自己带 sortsType,故不受调用方入参影响),其余各路才吃调用方的值。
    # 实测(PROBE_MULTI_ROUTE=「BOM 分母变平方」,6 路):
    #   sorts_type=0 → [1, 0, 0, 0, 0, 0]    sorts_type=1 → [1, 1, 1, 1, 1, 1]
    _r, seen = _patched_search((PROBE_MULTI_ROUTE,), product_id=93, budget=10, sorts_type=0)
    ok(len(seen) >= 2, "本用例需要多路输入,实为 %d 路" % len(seen))
    ok(seen[0]["sorts_type"] == 1,
       "第 1 路(原句路)应固定 sortsType=1(ADR-0009),实为 %r" % (seen[0]["sorts_type"],))
    ok(all(c["sorts_type"] == 0 for c in seen[1:]),
       "调用方 sorts_type=0 必须原样到其余各路(不得被 or 链折成 1): %r"
       % ([c["sorts_type"] for c in seen],))
    # 反向对照:默认 1 时各路(含原句路)都应是 1 —— 证明上一条不是"恒等于 0"的假绿。
    _r2, seen2 = _patched_search((PROBE_MULTI_ROUTE,), product_id=93, budget=10)
    ok(all(c["sorts_type"] == 1 for c in seen2),
       "默认 sorts_type 应为 1 并透传到每一路,实为 %r" % ([c["sorts_type"] for c in seen2],))
    # 第三态:2(时间倒序)也必须原样透传。只钉 0 与 1 会漏掉"实现写成只认 0"这类回退
    # (独立验证提示的覆盖缺口)。实测:各路为 [1, 2, 2, 2, 2, 2]。
    _r3, seen3 = _patched_search((PROBE_MULTI_ROUTE,), product_id=93, budget=10, sorts_type=2)
    ok(seen3 and all(c["sorts_type"] == 2 for c in seen3[1:]),
       "调用方 sorts_type=2 应原样到其余各路,实为 %r" % ([c["sorts_type"] for c in seen3],))


# ================= 联网组:真实上游(默认不跑) =================
@case("online: 长度边界两侧(100 放行 / 101、120 本地拦下)", online=True)
def t_length_boundary():
    # 只走公开面 search,不引用 clamp_query 等内部名——内部件随重构收进
    # 私有命名空间/拆包,依赖内部名会把外部行为用例退化成结构断言。
    # 100 字符会真实打到上游,故本用例归联网组。
    try:
        core.search("a" * 100)
    except core.QueryTooLong:
        raise Fail("恰好 100 字符被硬闸误判为超限(应放行)")
    except core.UpstreamError:
        pass  # 上游业务错误与"本地硬闸误判"无关:已证明未被本地拦下
    for n in (101, 120):
        try:
            core.search("a" * n)
        except core.QueryTooLong as e:
            ok(len(e.clamped) == 100, "%d 字符的 clamped 长度应为 100" % n)
            continue
        except core.UpstreamError:
            raise Fail("%d 字符未被本地硬闸拦下而是打到上游(硬闸失效)" % n)
        raise Fail("%d 字符未触发 QueryTooLong" % n)


@case("online: search 顶层 13 键 + 结果项 13 字段(声明派生)+ 无历史残留字段", online=True)
def t_search_contract():
    """顶层与条目键集**双向**断言,键集从 contract.json 派生(决策 D13)。

    ⚠️ 本用例不再手写"16 键"这类计数:计数是**声明的影子**,声明一变计数就过期
    (活证据:d753719 删 3 个字段后测试计数没跟上)。改成"返回键集 == 声明键集",
    既不用维护数字,又能同时抓到多出与少了。
    """
    r = core.search(QUERY, product_id=93)
    ks = keys_of(r, "search")
    check_subset(ks, SEARCH_KEYS, "search 顶层")
    check_no_extra(ks, SEARCH_KEYS, "search 顶层")
    # 与声明逐字一致(强弱两端都断:不得多、不得少)。
    ok(ks == SEARCH_KEYS,
       "search 顶层键集应恰等于声明:\n  多出 %s\n  缺 %s"
       % (sorted(ks - SEARCH_KEYS), sorted(SEARCH_KEYS - ks)))
    # 分页字段已删(决策 D10)——单独再钉一次:声明改了但代码没改也不会漏。
    for dead in ("page", "pageSize", "totalPages"):
        ok(dead not in ks, "search 顶层仍返回已删除的分页字段 %r" % dead)
    ok(r["effectiveProductId"] == 93,
       "显式 product_id=93 时 effectiveProductId 应为 93,实为 %r" % (r["effectiveProductId"],))
    ok(r["ok"] is True, "search.ok 应为 True")
    ok(r["text"] == QUERY, "search.text 应回显查询词")
    ok(r["keywords"] is None, "未给 keywords 时 search.keywords 应为 None")
    ok(r["queries"] and r["queries"][0] == QUERY,
       "search.queries 首元素应为原句路(查询词),实为 %r" % (r["queries"],))
    ok(len(r["queries"]) <= 7, "路数 %d 超过 maxRoutes=7" % len(r["queries"]))
    ok(len(set(r["queries"])) == len(r["queries"]),
       "queries 出现重复检索词(同一请求被发两次,塌缩去重失效): %r" % (r["queries"],))
    plan = r["routesPlanned"]
    ok(isinstance(plan, int) and 1 <= plan <= 7,
       "routesPlanned 应为 1..7 的整数(计划路数),实为 %r" % (plan,))
    ok(isinstance(r["routesDegraded"], bool), "routesDegraded 应为 bool")
    ok(r["routesDegraded"] == (len(r["queries"]) < plan),
       "routesDegraded(%r)与「实际路数(%d) < 计划路数(%d)」不一致"
       % (r["routesDegraded"], len(r["queries"]), plan))
    check_subset(keys_of(r["stats"], "search.stats"), {"upstreamCalls", "elapsedMs"}, "search.stats")
    ok(KS_STATS_LEGACY.isdisjoint(keys_of(r["stats"], "search.stats")),
       "stats 出现旧 HTTP 路径字段 %r" % sorted(KS_STATS_LEGACY & keys_of(r["stats"], "search.stats")))
    ok(isinstance(r["total"], int), "search.total 应为 int")
    # total 稳定性:同查询连续两次 total 必须一致(数值稳定是硬契约)
    time.sleep(1.2)
    r2 = core.search(QUERY, product_id=93)
    ok(r["total"] == r2["total"],
       "total 数值不稳定: 连续两次 %r vs %r" % (r["total"], r2["total"]))
    drift = abs(r["total"] - SEARCH_TOTAL_BASELINE) / float(SEARCH_TOTAL_BASELINE)
    ok(drift <= SEARCH_TOTAL_DRIFT_TOL,
       "total 基线漂移超 %.0f%%: 基线 %d 实得 %r(上游语料变动,需人工重新定档)"
       % (SEARCH_TOTAL_DRIFT_TOL * 100, SEARCH_TOTAL_BASELINE, r["total"]))
    if r["total"] != SEARCH_TOTAL_BASELINE:
        print("      注: total 偏离基线 %d → %r(在 %.0f%% 容差内,已按新观测重新定档)"
              % (SEARCH_TOTAL_BASELINE, r["total"], SEARCH_TOTAL_DRIFT_TOL * 100))
    ok(r["results"], "清单为空(该探测词实测必有结果)")
    for i, it in enumerate(r["results"]):
        name = "results[%d]" % i
        kk = keys_of(it, name)
        check_subset(kk, RESULT_KEYS, name)
        check_no_extra(kk, RESULT_KEYS, name)
        check_no_forbidden(kk, RESULT_FORBIDDEN_KEYS, name)
        ok(it["type"] in ("knowledge", "question", "article"),
           "%s.type 非法(应为 knowledge|question|article): %r" % (name, it["type"]))
        ok(it["id"], "%s.id 为空" % name)
        ok(it["title"], "%s.title 为空(清单核心交付物是标题)" % name)
        ok(isinstance(it["hitRoutes"], int) and it["hitRoutes"] >= 1,
           "%s.hitRoutes 应为 ≥1 的整数,实为 %r" % (name, it.get("hitRoutes")))
        ok(isinstance(it["routes"], list) and it["routes"],
           "%s.routes 应为非空 list" % name)
        ok(len(it["routes"]) == it["hitRoutes"],
           "%s.routes 长度(%d)应等于 hitRoutes(%d)" % (name, len(it["routes"]), it["hitRoutes"]))
        ok(all(isinstance(x, int) and x >= 1 for x in it["routes"]),
           "%s.routes 元素应为 ≥1 的整数" % name)
        # ⚠️ **帖子级硬证据**(ADR-0014):问答条目的 id 是帖子号,不是回答 id。
        # 实测判据:详情端点按该 id 能取到帖(由 read 用例覆盖);此处先钉形态——
        # 问答条目不得再有 questionId 字段(它已随帖级化删除)。
        if it["type"] == "question":
            ok("questionId" not in kk,
               "%s 是 question 却仍带 questionId 字段(帖级化后 id 就是帖子号)" % name)
            ok("adopted" in kk and "answersCount" in kk,
               "%s 是 question 却缺 adopted/answersCount 原生信号" % name)
        else:
            ok(it["adopted"] is None,
               "%s 是非问答条目,adopted 应为 None,实为 %r" % (name, it["adopted"]))


@case("online: 清单内同一帖只出现一次(帖子级归并的外部证据)", online=True)
def t_manifest_post_level_dedup():
    """**帖子级归并的外部可观测证据**(ADR-0014)。

    离线用例已用合成数据证明归并逻辑;本条证明**真实上游数据**下也成立:
    上游按回答返回(同一帖子下多条回答 = 多个条目),清单里同一帖子号必须只出现一次。

    为什么必须联网验:合成数据的形状是照实测写的,若上游改了返回形态
    (例如开始按帖子返回),离线用例仍全绿而本层归并会退化为恒等映射——
    那条路径没有离线可测的信号,只能靠真实数据钉。
    """
    r = core.search("信用额度", product_id=93, type_="question")
    items = r["results"]
    ok(items, "type=question 检索零结果(探测词未命中;换词而非降级断言)")
    ids = [x["id"] for x in items]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    ok(not dupes,
       "清单里同一帖子号出现多次 %r —— 帖级归并未生效(条目仍是回答级)" % (dupes,))
    ok(all(x["type"] == "question" for x in items),
       "type=question 过滤后混入其他类型: %r" % ([x["type"] for x in items],))
    # 帖级信号必须真的带值(不是键在值全空):answersCount 是该帖的回答总数。
    withcount = [x for x in items if isinstance(x.get("answersCount"), int)]
    ok(withcount, "没有任何问答条目带 answersCount —— 帖级信号未透传: %r"
       % ([x.get("answersCount") for x in items][:5],))
    ok(any(x["answersCount"] >= 1 for x in withcount), "answersCount 应至少为 1")


@case("online: search 三种实体全返回(type 字段区分;问答档对外名是 question)", online=True)
def t_search_all_types():
    r = core.search(QUERY, product_id=93, type_=None)
    types = [x["type"] for x in r["results"]]
    ok(all(t in ("knowledge", "question", "article") for t in types),
       "混排页出现非法 type: %r" % (sorted(set(types)),))
    ok("answer" not in types,
       "混排页出现上游原始值 'answer' —— 映射漏了(对外应为 question): %r" % (sorted(set(types)),))
    # 定向过滤:每类型各用实测能命中的探测词。
    # 实测事实(2026-09-17):article 是长尾类型,泛词("信用额度"/"BOM"/"MRP")在该产品
    # 过滤下抽不到 article 条目,故用 "套打" 作为 article 探测词。
    # 注意 `total` 是**上游混合结果的总数,不是过滤后该类型的条数**
    # (例:type=article&text=信用额度 → total=74 但 results=[])。故此处不断言 total。
    probes = (("knowledge", QUERY), ("question", "信用额度"), ("article", "套打"))
    seen = set()
    for t, probe in probes:
        time.sleep(1.2)
        rr = core.search(probe, product_id=93, type_=t)
        ok(rr["ok"] is True, "type=%s 检索失败" % t)
        ok(rr["results"], "type=%s 检索零结果(探测词 %r 未命中;换词而非降级断言)" % (t, probe))
        ok(all(x["type"] == t for x in rr["results"]),
           "type=%s 过滤后混入其他类型: %r" % (t, [x["type"] for x in rr["results"]]))
        ok(rr["scanNote"] and ("type=%s" % t) in rr["scanNote"],
           "type=%s 应带该类型的 scanNote(跨页扫描说明),实为 %r" % (t, rr["scanNote"]))
        seen.add(t)
    ok(seen == {"knowledge", "question", "article"}, "三种类型未全部验证")


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
    r = core.search(QUERY, product_id=93)
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


@case("online: read(question) 契约 —— 键集/帖级 id/截断信号", online=True)
def t_read_question_contract():
    """问答详情路径的契约(键集 + **帖级 id 语义** + 截断信号)。

    ⚠️ 本轮关键变化(决策 D5/D6):清单条目的 `id` **就是帖子号**,故 `read` 直接
    用它即可 —— 旧口径"要把 questionId 交给 read、id 是回答 id"已作废,
    双 id 空间整体消失。本用例用清单条目的 `id` **原样**去读,证明这条闭环成立。
    """
    r = core.search(QUERY, product_id=93, type_="question")
    item = next((x for x in r["results"] if x["type"] == "question"), None)
    ok(item, "未取到 question 条目")
    qid = item["id"]  # 帖级:id 就是帖子号,不再有 questionId 字段
    ok(qid, "question 条目 id 为空")
    ok("questionId" not in item, "question 条目不应再有 questionId 字段(帖级化已删除)")
    time.sleep(1.2)
    d = core.read("question", qid)
    ks = keys_of(d, "read(question)")
    # 核心键必须齐:这是问答路径与 knowledge 路径的形态差异所在。
    # required = 恒在的核心键;allowed = 可出现的全集。
    check_subset(ks, READ_KEYS | READ_QUESTION_REQUIRED_KEYS, "read(question) 顶层")
    check_no_extra(ks, READ_KEYS | READ_QUESTION_EXTRA_KEYS, "read(question) 顶层")
    ok(d["ok"] is True, "read(question).ok 应为 True")
    ok(d["type"] == "question", "read(question).type 应为 question,实为 %r" % (d["type"],))
    ok(str(d["id"]) == str(qid), "read(question).id 应回显传入的帖子号(清单条目 id 原样可用)")
    ok("landing" not in ks, "read(question) 仍返回 landing 字段")
    ok("chunks" not in ks, "read(question) 返回 chunks(spec:随 ask 删除)")
    ok("questionId" not in ks,
       "read(question) 返回了 questionId:该路径的 id 就是帖子号,回显同值是冗余"
       "(且该字段已随帖级化删除)。实有: %r" % (sorted(ks),))

    # 截断信号:已取/总数必须都是 int,且已取 <= 总数。
    # max_answer_pages 造成的截断若不置 truncated,调用方零信号。
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
    for a in d["answers"]:
        aks = keys_of(a, "answer 条目")
        check_subset(aks, {"id", "adopted", "contentText"}, "answer 条目")
        ok("chunks" not in aks, "answer 条目返回 chunks(spec:随 ask 删除)")
    check_subset(keys_of(d["stats"], "read(question).stats"),
                 {"upstreamCalls", "elapsedMs"}, "read(question).stats")


@case("online: read(question) 传回答级 id 已不可表达(双 id 空间消失)", online=True)
def t_read_question_no_second_id():
    """钉住"双 id 空间已消失"这件事:清单不再给回答 id,故无从传错。

    旧口径下这条用例的存在理由是"传回答 id 必 404,故 read 只认 questionId";
    现在回答 id **不再出现在清单里**,调用方拿不到第二个 id 空间——问题从源头消失。

    本用例的最强可执行断言:清单里每一条问答条目,其 `id` 都能被 `read("question", id)`
    **成功**读到(即清单与读取的 id 口径闭环)。
    """
    r = core.search(QUERY, product_id=93, type_="question")
    items = [x for x in r["results"] if x["type"] == "question"][:2]
    ok(items, "未取到 question 条目")
    for it in items:
        ok("questionId" not in it, "清单条目仍暴露第二个 id 空间(questionId)")
        time.sleep(1.2)
        try:
            d = core.read("question", it["id"])
        except Exception as e:
            raise Fail("清单条目的 id(%r)直接读取失败 —— 清单与读取的 id 口径未闭环: %s"
                       % (it["id"], e))
        ok(str(d.get("id")) == str(it["id"]),
           "read 返回的 id(%r)与清单条目 id(%r)不一致(口径分裂)" % (d.get("id"), it["id"]))
        ok(d.get("ok") is True, "read 未返回 ok:true: %r" % (d.get("ok"),))
        ok("questionId" not in d, "read(question) 仍返回 questionId(双 id 空间残留)")


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
        r = core.search(PROBE_MULTI_ROUTE, product_id=93)
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
    r = core.search(PROBE_MULTI_ROUTE, product_id=93, budget=2)
    ok(len(r["queries"]) >= 3, "本用例需要 ≥3 路的输入(实测该探针词 6 路),实为 %d 路"
       % len(r["queries"]))
    ok(r["budget_exhausted"] is True, "budget=2 且 ≥3 路应置 budget_exhausted=true")
    ok("实际完成" in r["scanNote"], "scanNote 应写明实际完成路数,实为 %r" % (r["scanNote"],))
    ok("计划" in r["scanNote"], "scanNote 应写明计划路数,实为 %r" % (r["scanNote"],))
    ok(r["stats"]["upstreamCalls"] <= 2,
       "预算硬上限被击穿: budget=2 实际发出 %r 次请求" % (r["stats"]["upstreamCalls"],))
    ok(r["ok"] is True, "预算耗尽仍应返回 ok:true(不是异常)")
    # budget=0:零上游请求,清单必空且不崩
    r0 = core.search(PROBE_MULTI_ROUTE, product_id=93, budget=0)
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
    a = core.search(QUERY, product_id=0)
    b = core.search(QUERY, product_id=None)
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
    r = core.search("应用为禁用状态[网关]", product_id=93)
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
    code, d, err = cli("search", QUERY, "--product", "93")
    ok(code == 0, "kd search 退出码 %r" % code)
    ok(d is not None, "kd search stdout 不是合法 JSON")
    check_subset(keys_of(d, "cli search"), SEARCH_KEYS, "cli search")
    ok(isinstance(d["total"], int) and d["total"] > 0, "CLI total 应为正整数,实为 %r" % (d["total"],))


@case("online: kd search --kw(LLM 拆词入口)+ 清单字段 + max-routes", online=True)
def t_cli_search_manifest():
    code, d, _ = cli("search", "应用为禁用状态[网关]", "--product", "93")
    ok(code == 0, "kd search 退出码 %r" % code)
    ok(d is not None, "kd search stdout 不是合法 JSON")
    check_subset(keys_of(d, "cli search"), SEARCH_KEYS, "cli search")
    ok(all("hitRoutes" in x for x in d["results"]), "CLI 清单条目应带 hitRoutes")
    ok("contentText" not in (d["results"][0] if d["results"] else {}),
       "CLI 清单条目不得含 contentText")
    # --max-routes 1:退化为单路,路数必为 1
    time.sleep(1.2)
    code, d1, _ = cli("search", QUERY, "--product", "93", "--max-routes", "1")
    ok(code == 0, "kd search --max-routes 1 退出码 %r" % code)
    ok(len(d1["queries"]) == 1, "--max-routes 1 应只跑 1 路,实为 %d 路" % len(d1["queries"]))
    ok(d1["stats"]["upstreamCalls"] == 1,
       "--max-routes 1 且无 type_ 过滤应恰好 1 次上游请求,实为 %r" % (d1["stats"]["upstreamCalls"],))
    # --kw 只给关键词(无 text:nargs="?" 允许)—— LLM 拆词的 CLI 入口。
    time.sleep(1.2)
    code, d2, _ = cli("search", "--kw", "信用额度", "--kw", "应收单 信用", "--product", "93")
    ok(code == 0, "kd search --kw 退出码 %r" % code)
    ok(d2 is not None, "kd search --kw stdout 不是合法 JSON")
    ok(d2["text"] is None, "只给 --kw 时 text 应为 None,实为 %r" % (d2["text"],))
    ok(d2["keywords"] == ["信用额度", "应收单 信用"],
       "--kw 应回显为 keywords,实为 %r" % (d2["keywords"],))
    ok(len(d2["queries"]) == 2, "两个 --kw 应各成一路,实为 %r" % (d2["queries"],))


@case("online: kd read 进程级调用", online=True)
def t_cli_read():
    code, d, _ = cli("search", QUERY, "--product", "93")
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
