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
import socket
import subprocess
import sys
import time
import urllib.error

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(REPO, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from kd import core  # noqa: E402
import kd.cli as _kd_cli  # noqa: E402,F401  # 登记 sys.modules["kd.cli"],供 _cli_mod() 取
# ⚠️ **直接 import 语义已可用**(2026-09-29,工单 #33 项三 3.2 收口):
# `kd._impl` 的唯一真遮蔽(`from ._detail import _detail`)已改名 `_detail_fn`,
# 故下面两个 import **拿到的就是模块对象本身**,不再是同名函数。
# 实测(`import kd._impl._upstream as m; isinstance(m, ModuleType)` → True),
# 且与 `sys.modules[...]` 是**同一对象**,故打桩等价。
import kd._impl._detail as _detail_mod_obj  # noqa: E402
import kd._impl._upstream as _upstream_mod_obj  # noqa: E402

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

# results[] 条目键集:**按类型分三份**(ADR-0016 决策 6,v6.6)。
# 公共段三类恒有;类型专属段只有该类型才有。故"允许出现的全集"= 公共 ∪ 全部专属,
# 而"某类型**应当**出现的键集"= 公共 ∪ 该类型专属。
RESULT_KEYS_COMMON = set(_CONTRACT["search"]["resultKeysCommon"])
RESULT_KEYS_BY_TYPE = {str(k): set(v)
                       for k, v in (_CONTRACT["search"]["resultKeysByType"] or {}).items()}


def result_keys_of(kind):
    """某类型条目**应当**出现的键集(公共 + 该类型专属)。

    ⚠️ 这是"类型不适用的键**不出现**"这条契约的断言来源:article 不该有 `adopted`,
    knowledge 不该有 `supports`——前提是"没出现",而不是"值为 None"。
    """
    return RESULT_KEYS_COMMON | RESULT_KEYS_BY_TYPE.get(str(kind), set())


# 全集(公共 ∪ 全部类型专属):用于"出现白名单外字段"这类**上界**断言。
RESULT_KEYS = RESULT_KEYS_COMMON | set().union(*RESULT_KEYS_BY_TYPE.values()) \
    if RESULT_KEYS_BY_TYPE else set(RESULT_KEYS_COMMON)

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
# ⚠️ v6.6(ADR-0016 决策 6)改成**按类型三份**:公共段 + 各类型专属段。
FROZEN_RESULT_KEYS_COMMON = ("type", "id", "title", "url", "snippet", "products",
                             "comments", "hitRoutes", "routes")
FROZEN_RESULT_KEYS_BY_TYPE = {
    "knowledge": (),
    "question": ("adopted", "answersCount", "questionBody"),
    "article": ("supports",),
    # ⚠️ 2026-09-29(工单 #32):`other` 档收容罕见上游类型。
    # `upstreamType` 必须保留 —— 对外只有一个 other,丢掉上游原值就等于
    # 把"认得出但不当一类"又变回"看不出这是什么"。
    "other": ("upstreamType", "resourceType"),
}
# ⚠️ v6.6:顶层键 `text` 删除(位置参数删除后无来源),新增 `keywordsDropped`
# (ADR-0016 决策 4:收词超限必须写明丢了几条),`budget_exhausted` 删除(预算机制整套删除)。
FROZEN_TOP_KEYS = ("ok", "keywords", "total", "queries", "routesPlanned",
                   "effectiveProductId", "results", "otherSkipped",
                   "routeErrors", "keywordsDropped", "scanNote", "contractCfgLoaded",
                   "stats")

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


def code_only(path):
    """读一个 .py 文件,**剥掉注释与字符串字面量**后返回可扫描的代码文本。

    为什么需要它:本项目多处用"源码扫描"做钉子(禁止清单、字面量收口),而这些
    源码里**大量 docstring 在解释被禁形态的历史**(例如 `_routes.py` 的模块 docstring
    逐条记着 `_rare_token` 为什么被删)。用纯文本扫描会把这些"留证"判成违规,
    于是断言被逼着要么误报、要么把判据放宽到没抓取力——两种都是坏结果。

    判据应当是 **"代码里不得出现",而不是"任何地方不得提及"**:
      * 注释与字符串是**文档**,记录被删形态是必要的(留证以免重复踩);
      * 标识符、属性访问、字典键若出现在**代码**里,那就是机制回来了。

    用标准库 `tokenize` 精确剥离(不用正则——正则会漏掉多行字符串/转义引号,
    那类漏洞会让判据悄悄失效)。解析失败(语法错误)时退回原始文本并**不静默**:
    调用方的断言会因此变严(更容易红),这是安全方向。
    """
    import io
    import tokenize
    with open(path, encoding="utf-8") as f:
        src = f.read()
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(src).readline))
    except Exception:
        return src
    drop = {tokenize.COMMENT, tokenize.STRING, tokenize.NL, tokenize.NEWLINE,
            tokenize.INDENT, tokenize.DEDENT}
    return "".join(t.string + "\n" for t in toks if t.type not in drop)


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


@case("offline: 已删除形参/开关传即失效(rerank/routes/refresh/page/size/text/type_/max_routes/budget)")
def t_dead_params_gone():
    """钉住历次删除的形参与开关:它们不是被忽略,而是**不存在**。

    删的是"能传但无效"的幽灵参数——静默失效比报错更贵(留着一个收下就扔的形参,
    调用方会以为传了有用)。历次删除累计如下:

      | 形参 | 删除于 | 原因 |
      |---|---|---|
      | `rerank` / `routes` | 2026-09-18 | 多路清单路径下无任何行为 |
      | `read(refresh=)` | 2026-09-18 | 内核恒在线 |
      | `page` / `page_size` | 2026-09-27(决策 D10) | 清单分页整体删除 |
      | `text`(位置参数) | **v6.6(ADR-0016)** | 内核不生成检索词,`--kw` 是唯一入口 |
      | `type_` | **v6.6** | 调用方从清单条目的 `type` 字段自己筛 |
      | `max_routes` | **v6.6** | 上限只在声明里一个值(`limits.maxKeywords`) |
      | `budget`(search/read) | **v6.6** | 每路恒 1 次请求,预算永不可触发 |
      | `--chunk` | **v6.6** | 解析后立刻报错的空参数,连帮助文本一并删 |

    前三行原由 `t_pagination_gone` 单独承担,本轮与本条**合并**(同一类钉子,
    分开写只增加维护面)。
    """
    probes = (
        ("search(rerank=True)", lambda: core.search(["甲"], rerank=True)),
        ("search(routes=1)", lambda: core.search(["甲"], routes=1)),
        ("search(page=2)", lambda: core.search(["甲"], page=2)),
        ("search(page_size=5)", lambda: core.search(["甲"], page_size=5)),
        # ⚠️ v6.6 新增:`text` 作为**关键字**传入也必须 TypeError。
        # 它是位置参数,而位置参数在 Python 里不接受关键字绑定(`def search(keywords=...)`)。
        ("search(text='整句')", lambda: core.search(text=QUERY)),
        ("search(type_='question')", lambda: core.search(["甲"], type_="question")),
        ("search(max_routes=1)", lambda: core.search(["甲"], max_routes=1)),
        ("search(budget=5)", lambda: core.search(["甲"], budget=5)),
        ("read(refresh=True)", lambda: core.read("knowledge", "1", refresh=True)),
        ("read(budget=5)", lambda: core.read("knowledge", "1", budget=5)),
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
        raise Fail("%s 未报错——已删形参仍在签名里(应已删除)" % label)
    # ⚠️ **位置实参的静默失效防堵**(v6.6 施工裁定):`core.search("整句")` 现在会把
    # 字符串绑到 `keywords` 上并按**字符**拆成多路——返回体结构完全合法,故它不是
    # TypeError 能拦住的。内核必须显式拒绝 str 入参(否则"一句话"被静默发成 N 个字的
    # N 路请求,零信号)。这是"宁可报错也不静默"在多路场景的落地。
    for label, bad in (('search("整句")', QUERY), ("search(b'x')", b"x")):
        try:
            core.search(bad)
        except core.InternalError as e:
            ok("列表" in str(e) or "list" in str(e).lower(),
               "%s 的报错应指明正确写法是词列表,实为: %s" % (label, str(e)[:120]))
            continue
        except Exception as e:
            raise Fail("%s 抛 %s(应为 InternalError:字符串入参被显式拒绝)"
                       % (label, type(e).__name__))
        raise Fail("%s 未报错——字符串被静默当作词列表(按字符拆成多路,零信号)" % label)
    # ⚠️ **dict 与生成器**(v6.6 审查补):两者同为"可迭代但语义不对"的入参——
    # dict 遍历的是**键**(`{"甲":1}` 静默变成 `["甲"]`),生成器只能消费一次
    # (校验遍历两遍会让它第二遍为空,有效词被误报成"全是空白"、hint 指错方向)。
    try:
        core.search({"甲": 1, "乙": 2})
    except core.InternalError as e:
        ok("dict" in str(e), "dict 入参的报错应点名 dict,实为: %s" % str(e)[:120])
    except Exception as e:
        raise Fail("search(dict) 抛 %s(应为 InternalError:映射被静默当词列表)"
                   % type(e).__name__)
    else:
        raise Fail("search(dict) 未报错——映射的**键**被静默当成检索词")
    # ⚠️ 下面两条会**真正走到上游调用**,故必须先把网络出口换掉(离线组不联网)。
    mod = _upstream_mod_obj
    real = mod._search_upstream
    mod._search_upstream = lambda *a, **k: {"content": [], "totalElements": 0,
                                            "totalPages": 0}
    try:
        _gen = core.search((k for k in ["甲", "乙"]))  # 不得因只能消费一次而误报
        ok(_gen.get("ok") is True and len(_gen.get("queries") or []) == 2,
           "生成器入参被误处理(应物化成 2 路;实得 %r 路)——校验遍历两遍会把它耗空"
           % len(_gen.get("queries") or []))
        # ⚠️ **部分空串的丢弃必须可见**(v6.6 审查补,规格 §A4「违规的是不说」):
        # `["甲","",""]` 只发 1 路,若返回体只字不提,调用方会把自己的输入错误读成
        # "内核只召回了 1 路"。故 scanNote 必须点名丢弃了 2 条空白词。
        _b = core.search(["甲", "", "  "])
        ok(_b.get("scanNote") and "空白" in _b["scanNote"],
           "部分空串被静默丢弃(scanNote 未提): %r —— 路数变少而调用方找不到原因"
           % (_b.get("scanNote"),))
        ok(len(_b.get("queries") or []) == 1,
           "空串不应产生检索路(实得 %r 路)" % len(_b.get("queries") or []))
    finally:
        mod._search_upstream = real
    # CLI 侧:argparse 未知参数 → exit 2,且 stdout 不得出现成功载荷。
    # ⚠️ 本轮新增三个已删开关;`--page`/`--size` 是 D10 的遗留钉子。
    for flag, val in (("--page", "2"), ("--size", "5"), ("--type", "question"),
                      ("--max-routes", "1"), ("--budget", "5"), ("--chunk", None)):
        args = ["search", "--kw", QUERY, flag] + ([val] if val else [])
        code, d, err = cli(*args)
        ok(code == 2, "kd search %s 应 exit 2(argparse 未知参数),实测 %r" % (flag, code))
        ok(err and err.strip(), "kd search %s 的 stderr 为空:用法提示被吞掉" % flag)
        if d is not None:
            ok(d.get("ok") is not True, "kd search %s 竟返回成功载荷: %r" % (flag, d))
    # 顶层键集不含分页字段与 `text`(声明侧,零上游请求:超长查询走错误路径)。
    ok("page" not in SEARCH_KEYS and "pageSize" not in SEARCH_KEYS
       and "totalPages" not in SEARCH_KEYS,
       "声明里仍有分页字段: %s" % sorted(SEARCH_KEYS))
    ok("text" not in SEARCH_KEYS,
       "声明顶层仍有 `text`(位置参数删除后它无来源,契约不挂恒为 null 的坑): %s"
       % sorted(SEARCH_KEYS))
    ok("budget_exhausted" not in SEARCH_KEYS,
       "声明顶层仍有 budget_exhausted(预算机制已整体删除): %s" % sorted(SEARCH_KEYS))


@case("offline: 实现包无命名空间遮蔽(直接 import 拿到模块,不再需要绕道)")
def t_impl_no_namespace_shadowing():
    """**工单 #33 项三 3.2 的钉子**(2026-09-29 收口时补)。

    治的病:`kd._impl/__init__.py` 里 `from ._detail import _detail` 让包属性
    `_detail` 变成**函数**,于是 `import kd._impl._detail as m` 拿到函数而非模块
    (`m._get_json` → AttributeError)。这不是理论风险:回归套件为此专门写了
    `_upstream_mod()` / `_detail_mod()` 两个 helper **绕道 `sys.modules`**,
    并注明"不能写 `import kd._impl._detail as m`" —— **代价已经真实发生**。

    本条钉两个层面,缺一即红:
      ① **语义**:包 `kd._impl` 的每个子模块属性必须**真的是模块对象**
         (`isinstance(..., ModuleType)`)—— 这条会抓住任何新引入的同名遮蔽;
      ② **等价性**:直接 import 拿到的对象与 `sys.modules` 里的是**同一个** ——
         否则测试里的打桩会打在副本上(这正是本仓"打桩必须打在真实注入点"的纪律)。

    ⚠️ 本仓纪律:`_net_mod()` / `_config_mod()` / `_cli_mod()` 三个 helper **保留**
    (`sys.modules` 写法),它们的理由是**独立的**(那三个模块是注入点且要被当作
    单一真源取用),与"遮蔽"无关。本条**不**要求删除它们。
    """
    import importlib
    import types
    import sys as _sys

    pkg = importlib.import_module("kd._impl")
    submods = ("_config", "_detail", "_errors", "_manifest", "_net",
               "_public", "_routes", "_text", "_upstream")
    for name in submods:
        attr = getattr(pkg, name, None)
        ok(isinstance(attr, types.ModuleType),
           "kd._impl.%s 的包属性不是模块对象(实为 %s)—— 说明包命名空间里该名字"
           "**被同名函数/变量遮蔽**了,`import kd._impl.%s as m` 会拿到非模块对象。"
           "这正是工单 #33 项三 3.2 修掉的那类问题(改法:导入时给函数改个名字,"
           "如 `from ._detail import _detail as _detail_fn`)"
           % (name, type(attr).__name__, name))
        # ② 与 sys.modules 同一对象(打桩等价性)。
        real = _sys.modules.get("kd._impl." + name)
        ok(attr is real,
           "kd._impl.%s 的包属性与 sys.modules 里的**不是同一对象** —— "
           "测试对包属性打桩会打在副本上,而生产代码读的是另一个:%s vs %s"
           % (name, attr, real))

    # ③ 反向验证:"绕道 helper"真的删干净了(否则票面验收标准不算达成)。
    #    ⚠️ 判据**用 AST 查定义与调用节点**,不做字符串匹配 —— 本条的说明文字里
    #    自然会出现那两个 helper 的名字,字符串判定会**误伤自己的注释**
    #    (补钉子时实测踩到:第一版用字面串检查就红了)。
    import ast as _ast
    tree = _ast.parse(open(os.path.abspath(__file__), encoding="utf-8").read())
    dead_names = {"_upstream_mod", "_detail_mod"}
    found = []
    for node in _ast.walk(tree):
        if isinstance(node, _ast.FunctionDef) and node.name in dead_names:
            found.append("定义在第 %d 行" % node.lineno)
        elif isinstance(node, _ast.Call) and isinstance(node.func, _ast.Name) \
                and node.func.id in dead_names:
            found.append("调用在第 %d 行" % node.lineno)
    ok(not found,
       "回归套件里仍有绕道 helper 的节点(%s)—— 工单 #33 项三 3.2 的验收标准是"
       "「两个绕道 helper 删除」。实现侧已修好(见上两条断言),消费端不该再留。"
       "改法:顶部 `import kd._impl._upstream as _upstream_mod_obj` 直接用模块对象。"
       % ", ".join(sorted(found)))


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
    # ⚠️ v6.6:原这里比的是 query_routes.json 的 version(配置格式版本,v 前缀两段)。
    # 该文件已随拆词器与预算机制整体删除(ADR-0016),数据文件收敛为 contract.json
    # ——故改比它的 version(pipx/pip 安装与插件清单之外,包内还剩这一处版本声明)。
    with open(os.path.join(SRC, "kd", "contract.json"), encoding="utf-8") as f:
        cfgv = (json.load(f) or {}).get("version")
    ok(cfgv, "contract.json 缺 version 字段(它是包内唯一数据文件的版本声明)")
    ok(str(cfgv).lstrip("v").split(".")[:2] == str(want).split(".")[:2],
       "contract.json version=%r 与 VERSION=%r 不同源" % (cfgv, want))


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

    ⚠️ **2026-09-29 改判(工单 #32)**:`ENTITY_KINDS` 多了第四个值 `other`
    (收容罕见上游类型),而 `_DETAIL_KINDS` **仍只有三档** —— 这不是"改一处漏一处",
    而是**刻意的语义区分**:

        `ENTITY_KINDS`  = 合法的实体类型(清单里会出现这些 type)
        `_DETAIL_KINDS` = **真的有全文端点**的那几档(read 能取全文)

    `other` 合法但无端点(课程/路径/专题各类型端点形状不一,部分连端点都没有)。
    故本条判据从"两集合相等"收紧为"`_DETAIL_KINDS` ⊆ `ENTITY_KINDS`,
    且差集**恰好**是 known-无法读的那些" —— 后者才是能抓住"漏同步"的形态。
    """
    import re
    impl = _impl()
    entity = getattr(impl, "ENTITY_KINDS", None)
    detail = getattr(impl, "_DETAIL_KINDS", None)
    ok(entity and detail, "实现体缺 ENTITY_KINDS 或 _DETAIL_KINDS")
    # ① 可读档必须都是合法档(方向一:_DETAIL_KINDS ⊆ ENTITY_KINDS)。
    ok(set(detail) <= set(entity),
       "_DETAIL_KINDS 里有 ENTITY_KINDS 之外的值(能读却不合法?): %r"
       % (sorted(set(detail) - set(entity)),))
    # ② 差集必须**恰好**是刻意的那些(方向二:漏同步会在这里红)。
    #    当前唯一合法的"不可读档"是 `other`。将来若给 other 接了端点,
    #    把它加进 _DETAIL_FN 即可 —— 那时本条要求同步改这里(而不是静默放宽)。
    ok(set(entity) - set(detail) == {"other"},
       "ENTITY_KINDS 与 _DETAIL_KINDS 的差集应为 {'other'}(合法但无全文端点);"
       "实为 %r —— 要么漏同步了分发表,要么新增了未声明的不可读档"
       % (sorted(set(entity) - set(detail)),))
    # 新集合必须就是那四个——顺带钉住"没被悄悄改回 answer"。
    ok(set(entity) == {"knowledge", "question", "article", "other"},
       "kind 集合应为 {knowledge, question, article, other}(决策 D5 + 工单 #32),"
       "实为 %r" % (sorted(entity),))
    # ③ `other` **不得**混进分发表:它没有端点,混进去会在 read 时 KeyError/发错请求。
    ok("other" not in set(detail),
       "_DETAIL_KINDS 含 other —— 它没有全文端点,不该出现在分发表里")
    cli_path = os.path.join(SRC, "kd", "cli.py")
    with open(cli_path, encoding="utf-8") as f:
        text = f.read()
    literal = re.search(r'\(\s*"knowledge"\s*,\s*"question"\s*,\s*"article"\s*\)', text)
    ok(not literal,
       "cli.py 里仍有裸 kind 字面量(第 %d 字符处)——应从 _IMPL.ENTITY_KINDS 取"
       % (literal.start() if literal else 0))
    ok("ENTITY_KINDS" in text, "cli.py 未引用 ENTITY_KINDS(白名单与实现体脱钩)")


@case("offline: 上游 answer → 对外 question 的映射仍在(`--type` 已删,收响应方向不变)")
def t_type_vocab_mapping():
    """`answer` → `question` 的**收响应方向**映射必须保留且正确。

    ⚠️ v6.6 变化(ADR-0016 决策 3):`--type` 参数删除,**发请求方向**的映射
    (`upstream_type_of`,把对外词汇翻成上游 `entity-type` 再比较)随之整体删除
    —— 它服务的"收响应后按类型过滤"这条通路失去唯一入口(清单不再按类型过滤)。
    那一半的历史缺陷仍值得记:改名时只改了 `_norm_item` 而未改过滤比较点,
    导致 `type=question` **恒零结果**(清单空、total 正常、无任何报错)——最贵的
    "静默零结果"。该缺陷形态随过滤删除而**不可能复现**,故不再重建断言。

    **保留的这一半(本用例的断言对象)**:上游响应里的 `entity-type` 是 `Answer`,
    而对外一律是 `question`;这条翻译只许在 `_norm_item` 一处发生,漏改就是
    全部问答条目的类型字段错(调用方 `read(kind=...)` 会照着错的 type 传)。
    """
    cp = _impl()
    # 收响应方向:_norm_item 必须把上游 "answer" 翻成对外 "question"。
    n = cp._norm_item(_syn("answer", 1, qid=2), "answer")
    ok(n["type"] == "question", "上游 answer 应收敛为对外 question,实为 %r" % (n["type"],))
    # 另两个 kind 恒等(不得被顺手改名)。
    for et in ("knowledge", "article"):
        ok(cp._norm_item(_syn(et, 1), et)["type"] == et,
           "%s 条目的对外 type 应恒等,不得被改写" % et)
    # 发请求方向的映射函数必须**不存在**了(它随 --type 删除;留着是死代码)。
    ok(not hasattr(cp, "upstream_type_of"),
       "实现包仍导出 upstream_type_of —— 它随 --type 删除后零消费者(ADR-0016)")
    # ⚠️ 映射表与 ENTITY_KINDS 的"同集合"关系一并消失:`ENTITY_KINDS` 现在只服务
    # `read --kind` 白名单,不再需要"每个 kind 都有上游映射"这条约束。
    ok(set(cp.ENTITY_KINDS) == {"knowledge", "question", "article", "other"},
       "kind 集合应为 {knowledge, question, article, other}(decision D5 + 工单 #32),"
       "实为 %r" % (sorted(cp.ENTITY_KINDS),))

@case("offline: product_id 两态(不传/None→默认;0→不过滤)")
def t_product_id_three_states():
    """`product_id` 的**两态**(ADR-0016 决策 3,v6.6;原为三态)。

    历史(值得记):三态时代修过一个真缺陷——`search` 把 `product_id is None` 一律
    折成默认 93,于是 Python 调用方**无法**表达"不过滤"(实测同问句下 total
    31788 → 6326,过滤被静默加上)。修法是把 None 定义为"显式不过滤"。

    用户随后裁定**收两态**:`None` 与"不传"同义(都是默认),不过滤只由显式 `0` 表达。
    理由:`None` 与 `0` 当时**同效而值域不同**(都是不过滤却要调用方记两个写法),
    而 CLI 侧从来没暴露过 `None` 这一态。收两态后 `effectiveProductId` 不再出现
    `null`,调用方与回显逐字可比对。

    本用例钉住两态各自的行为**与**那条"区分度"要求(0 与默认必须是两个不同结果)。
    """
    import inspect
    sig = inspect.signature(core.search)
    default = sig.parameters["product_id"].default
    ok(default == DEFAULT_PRODUCT_ID,
       "product_id 签名默认应为声明里的默认编号 %r,实为 %r(省略参数靠它承担)"
       % (DEFAULT_PRODUCT_ID, default))

    # 省略:走签名默认 → 默认编号。
    _r, seen_omit = _patched_search((), keywords=["甲"])
    ok(all(c["product_id"] == DEFAULT_PRODUCT_ID for c in seen_omit),
       "省略参数应带默认过滤 %r,实收 %r"
       % (DEFAULT_PRODUCT_ID, [c["product_id"] for c in seen_omit]))

    # 显式 None:与不传**同义**(v6.6 两态)——必须按默认线过滤,而不是不过滤。
    r_none, seen_none = _patched_search((), keywords=["甲"], product_id=None)
    ok(r_none["effectiveProductId"] == DEFAULT_PRODUCT_ID,
       "显式 None 应与不传同义(取默认 %r),实为 %r" % (DEFAULT_PRODUCT_ID,
                                                       r_none["effectiveProductId"]))
    ok(all(c["product_id"] == DEFAULT_PRODUCT_ID for c in seen_none),
       "显式 None 时上游应按默认线过滤(旧三态下它会退化成不过滤),实收 %r"
       % ([c["product_id"] for c in seen_none],))

    # 显式 0:真不过滤(与默认**不同**)。
    r_zero, seen_zero = _patched_search((), keywords=["甲"], product_id=0)
    ok(r_zero["effectiveProductId"] == 0, "显式 0 应回显 0,实为 %r" % (r_zero["effectiveProductId"],))
    ok(all(c["product_id"] is None for c in seen_zero),
       "显式 0 时上游必须省略产品过滤,实收 %r" % ([c["product_id"] for c in seen_zero],))

    # ⚠️ 区分度钉子:两态的回显值必须不同,否则本用例无法区分实现
    # (若实现把 0 也折成默认,或把默认也当不过滤,这条会红)。
    ok(r_zero["effectiveProductId"] != r_none["effectiveProductId"],
       "显式 0(不过滤)与 None(默认线)必须是两个不同结果: %r vs %r"
       % (r_zero["effectiveProductId"], r_none["effectiveProductId"]))
    # 回显值不得出现 null(两态的核心可观测后果)。
    for label, rr in (("省略", _r), ("显式 None", r_none), ("显式 0", r_zero)):
        ok(rr["effectiveProductId"] is not None,
           "%s 时 effectiveProductId 不得为 null(v6.6 两态): %r" % (label, rr["effectiveProductId"]))

    # CLI 侧:不传 --product 时**不得**把 None 当显式值传进内核(那等于不过滤)。
    # ⚠️ 2026-09-29 出口统一:此处**原先是 `cli("search", ...)`(起子进程)**,
    # 而 `cli()` 用 `subprocess.run` 起的**新进程不继承进程内桩** —— 于是这条用例
    # 绕过全部打桩点真发一次上游请求(实测抓着:48/48 全绿而漏出 1 次真实请求)。
    # 改成进程内 `_patched_search` 后,本用例断言的东西**完全一样**
    # (effectiveProductId 的口径 + 不用显式 None),但零网络。
    _r_cli, seen_cli = _patched_search((), keywords=[PROBE_DEGRADED])
    ok(_r_cli["effectiveProductId"] == DEFAULT_PRODUCT_ID,
       "CLI 不传 --product 时应生效默认 %r: %r"
       % (DEFAULT_PRODUCT_ID, _r_cli.get("effectiveProductId")))
    ok(all(c["product_id"] == DEFAULT_PRODUCT_ID for c in seen_cli),
       "不传 --product 时上游应收到默认线 %r(不得把 None 当显式值传成不过滤),实收 %r"
       % (DEFAULT_PRODUCT_ID, [c["product_id"] for c in seen_cli]))
    # CLI 侧无 --kw 时必须是用法错误(不能静默空跑)。
    # ⚠️ 这条**必须**留在子进程:`clamp_query(strict=True)` 的用法错误由 argparse /
    # `_guard` 在进程边界上表现为 exit code 2,进程内断言测不到退出码。
    # 它**不发上游请求**(argparse/内核校验在触网之前就拦住了),故不构成泄漏。
    code2, _d2, _e2 = cli("search")
    ok(code2 == 2, "kd search 无 --kw 应 exit 2(内核 raise InternalError → 用法错误),实测 %r" % code2)

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
    # ⚠️ v6.6:**发请求方向的映射表已随 `--type` 删除**(ADR-0016 决策 3),
    # 故原先"映射的另一半必须在同一文件里"那条断言随之移除——它守的是
    # "对外词汇 vs 上游词汇"的过滤比较,而清单已不再按类型过滤。
    # 那条断裂的历史缺陷(改了收响应一半、漏改过滤一半 → 恒零结果)记在
    # `t_type_vocab_mapping` 的 docstring 里,不需要一条守已删机制的断言。
    ok("_UPSTREAM_TYPE_OF" not in code_only(os.path.join(SRC, "kd", "_impl", "_upstream.py")),
       "_upstream.py 代码里仍有 _UPSTREAM_TYPE_OF —— 该映射表随 --type 删除后零消费者")


@case("offline: 100 字硬闸 raise QueryTooLong(带 original/clamped/limit)")
def t_query_too_long_attrs():
    long_text = "超" * 120
    try:
        # ⚠️ v6.6:必须以**词列表**传入(位置参数删除;字符串入参被显式拒绝)。
        # 本用例验的是"单个词超长"这条闸门,故是 [long_text] 而非 long_text。
        core.search([long_text])
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
    # ⚠️ v6.6:只剩一个入口(keywords 列表),故两路探针都走列表形式:
    # 一路是**唯一元素**超长,一路是**多词中夹一个**超长(逐词都要过闸)。
    for label, fn in (("search([超长])", lambda: core.search([long_text])),
                      ("search([短, 超长])", lambda: core.search(["甲", long_text]))):
        try:
            fn()
        except core.QueryTooLong as e:
            ok(len(e.clamped) == 100, "%s clamped 长度应为 100" % label)
            continue
        raise Fail("%s 未对 101 字符输入 raise QueryTooLong" % label)


@case("offline: 空/非法入参 raise InternalError(不是崩溃)")
def t_internal_error():
    """唯一入口 `search(keywords=…)` 的入参校验,加上 `read` 的两条。

    ⚠️ v6.6 口径变化(ADR-0016 决策 3):`text` 形参删除,故"空 text"这类探针
    改为"空 keywords";而**字符串入参**现在是**显式拒绝**的非法形态(见
    `t_dead_params_gone` 的静默失效防堵)——它必须得到 InternalError 而不是
    被按字符拆成多路。
    """
    for label, fn in (("search(None)", lambda: core.search(None)),
                      ("search([])", lambda: core.search([])),
                      ("search([''])", lambda: core.search([""])),
                      ("search(str)", lambda: core.search(QUERY)),
                      ("read(bad kind)", lambda: core.read("nope", "1")),
                      ("read(no id)", lambda: core.read("knowledge", ""))):
        try:
            fn()
        except core.InternalError:
            continue
        except Exception as e:
            raise Fail("%s 抛的是 %s(应为 InternalError)" % (label, type(e).__name__))
        raise Fail("%s 未抛 InternalError" % label)
    # ⚠️ `type_` 形参已删除,故"非法 type"这条探针改为 TypeError(形参不存在),
    # 而不是 InternalError——两件事不能混:前者是"没有这个参数",后者是"参数值非法"。
    try:
        core.search(["甲"], type_="nope")
    except TypeError:
        pass
    except Exception as e:
        raise Fail("search(type_='nope') 抛的是 %s(应为 TypeError:形参已删除)"
                   % type(e).__name__)
    else:
        raise Fail("search(type_='nope') 未报错——type_ 形参应已删除")


# ================= 离线组:CLI 进程级 =================
@case("offline: kd health 库模式自检(无服务/无端口)")
def t_cli_health():
    code, d, err = cli("health")
    ok(code == 0, "kd health 退出码 %r(应 0)" % code)
    ok(d is not None, "kd health stdout 不是合法 JSON")
    ks = keys_of(d, "health")
    # ⚠️ v6.6:数据文件收敛为一个(ADR-0016)——原 routesCfg/routesCfgLoaded/maxRoutes/
    # budgetMax 四个字段随 query_routes.json 与预算机制删除,改为 contractCfg/
    # contractCfgLoaded/maxKeywords。报已删能力等于承诺不存在的东西。
    check_subset(ks, {"ok", "service", "anonymous", "http", "noDiskWrite", "python",
                      "executable", "coreApi", "missingApi", "contractCfg",
                      "contractCfgLoaded", "maxKeywords", "rateProfile", "textMax",
                      "commands", "note"}, "health 顶层")
    for gone in ("routesCfg", "routesCfgLoaded", "maxRoutes", "budgetMax"):
        ok(gone not in ks,
           "health 仍报已删字段 %s(query_routes.json 与预算机制已整体删除)" % gone)
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
    # ⚠️ v6.6:唯一入口是 --kw(位置参数删除)。
    code, d, err = cli("search", "--kw", "超" * 120)
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


@case("offline: read 的错误提示必须是 read 语境(cli._guard 的 op 有钉子)")
def t_cli_guard_op_for_read():
    """**工单 #24 修复的守护**(2026-09-29 补,工单 #26)。

    治的病:`read` 遇上游 404 时不再误报"text 超 100 字符"这套 **search 语境**的提示,
    靠的是调用点**手写一行** `op="read"`。删掉它 —— **48 条离线用例全绿**,
    即这条修复此前**没有任何守护**。

    变异实测(本用例正是为此而写):
        原版    $ kd read 123456789012345678 → hint = 按 id 取全文时上游报错…
        变异版  $ kd read 123456789012345678 → hint = 上游以 HTTP 200 返回错误壳
                                              (常见于检索词超 100 字符…)   ← search 语境

    ⚠️ **两条实现纪律(踩过才知道)**:
      * 必须走 `cli.main([...])` 的**真调用路径**。直接调 `_guard(op="read")`
        在 `op` 被删后**仍会通过**(参数是显式传的),测不到回归;
      * 必须打桩 `cli._out`,**不能**用 `redirect_stdout` —— 后者内部调
        `sys.stdout.reconfigure`,而 `io.StringIO` **没有该方法**,会把用例炸成 ERROR
        而不是 FAIL(那是测试自身的缺陷,不是被测行为的信号)。

    注入点:`_net._get_json`(唯一网络出口,2026-09-29 出口统一)——抛 `UpstreamError`
    即模拟"上游以错误壳回应",不触网。
    """
    import io as _io
    cli_mod = _cli_mod()
    net_mod = _net_mod()
    real_get, real_out = net_mod._get_json, cli_mod._out

    def boom(url, rate=None):
        # 上游"假 200":HTTP 200 但 body 是错误壳 —— 内核识别为 UpstreamError。
        raise core.UpstreamError(404, "not found")

    captured = {}

    def grab(obj):
        captured.clear()
        captured.update(obj if isinstance(obj, dict) else {})

    net_mod._get_json = boom
    cli_mod._out = grab
    try:
        # 真调用路径:走 argparse → cmd_read → _guard(op=...)。
        try:
            cli_mod.main(["read", "123456789012345678", "--kind", "question"])
        except SystemExit:
            pass
    finally:
        net_mod._get_json = real_get
        cli_mod._out = real_out

    err = (captured or {}).get("error") or {}
    ok(err, "read 遇上游错误应输出带 hint 的 JSON 错误体,实收 %r" % (captured,))
    hint = str(err.get("hint") or "")
    example = str(err.get("example") or "")
    ok(hint, "read 的错误体缺 hint(既有契约:错误是带 hint 的 JSON): %r" % (err,))

    # ① hint 必须是 **read 语境**:不得把排查方向带去 search。
    ok("id" in hint or "全文" in hint,
       "read 的错误 hint 未指向 read 语境(应按 id 取全文): %r" % hint)
    ok("100" not in hint,
       "read 的错误 hint 出现『100 字符』—— 那是 **search** 语境的上游 text 硬闸,"
       "与 read 毫无关系,会把排查方向直接带偏(工单 #24 记录的误导项): %r" % hint)
    ok("检索词" not in hint,
       "read 的错误 hint 出现『检索词』—— 同因,read 不传检索词: %r" % hint)

    # ② example 也必须是 read 的(它是给调用方照抄的下一步动作)。
    ok(example.startswith("kd read"),
       "read 的错误 example 应以 `kd read` 开头(照抄即能复现),实为 %r" % example)

    # ③ 反向对照:同一错误在 **search 语境**下必须给出**不同**的 hint/example ——
    #    否则本用例没有区分度(若两档本就相同,上面对 read 的断言等于什么都没钉)。
    #    ⚠️ 这里**不能**用 `cli.main(["search", ...])` 走真路径来对照: `search`
    #    的编排层把每路的 `UpstreamError` 吞进 `routeErrors` 并继续(单路失败不拖垮
    #    整轮,见 `_search_manifest`),于是错误体根本不会出现 —— 反向对照会测到
    #    一个空对象而误判。故直接调 `_guard`(默认 op="search"),只借它产出
    #    search 语境的措辞来比。主钉子(read)仍是真调用路径。
    captured.clear()
    cli_mod._out = grab
    try:
        try:
            cli_mod._guard(lambda: (_ for _ in ()).throw(core.UpstreamError(404, "not found")))
        except SystemExit:
            pass
    finally:
        cli_mod._out = real_out
    s_err = (captured or {}).get("error") or {}
    s_hint = str(s_err.get("hint") or "")
    s_example = str(s_err.get("example") or "")
    ok(s_example.startswith("kd search"),
       "search 语境的错误 example 应以 `kd search` 开头,实为 %r" % s_example)
    ok(s_hint != hint or s_example != example,
       "search 与 read 的同一错误必须给出**不同**语境提示 —— 二者相同说明 `op`"
       "没有真正分流(read: hint=%r example=%r / search: hint=%r example=%r)"
       % (hint, example, s_hint, s_example))


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
    # ⚠️ v6.6:以词列表传入(位置参数删除)。
    try:
        core.search(["超" * 120])
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


def _syn(et, i, qid=None, title=None, adopted=False, comments=3):
    """合成一个上游形态条目(用于喂 _norm_item),字段形状对齐实测响应。

    ⚠️ 入参 `et` 是**上游**的 entity-type(小写):问答题传 `"answer"`——
    `_norm_item` 收到它才会翻译成对外的 `question`。这是刻意的:用例必须证明
    映射发生在**上游值**上,而不是在已经翻译过的值上(否则映射失效也测不出来)。

    帖子级(`qid`)是默认行为:同一 `qid` + 不同 `i`(回答 id)的两个条目,经
    `_manifest_merge` 必须合并成一条。

    `comments` 三类都给(实测:同一响应内 0/2/3)——它是 D2 的钉子来源,
    故可传 `comments=None` 构造"上游没给该键"的形态(`knowledge`/`article` 分支
    会真的把该键丢掉,`answer` 分支保留 None)。
    ⚠️ article/knowledge 的真实形态**本来就没有** `adopted` 键——这正是 D1
    (合成假 `False`)的构造前提,不需要额外开关。
    """
    if et == "answer":
        return {"entity-type": "Answer", "id": str(i), "questionId": str(qid or i),
                "highlight": {"question.title": title or ("问题标题 %s" % i),
                              "description": "回答正文 %s" % i},
                "question": {"id": str(qid or i), "answers": 2, "moduleName": "财务云",
                             "description": "问题正文 %s" % i},
                "isAdopt": "true" if adopted else "false", "views": 10,
                "comments": comments, "contentLen": 100, "updatedAt": "2026-01-01"}
    if et == "article":
        d = {"entity-type": "Article", "id": str(i),
             "highlight": {"title": title or ("文章标题 %s" % i), "content": "正文 %s" % i},
             "classifies": [{"name": "星空旗舰版"}], "views": 10, "supports": 5,
             "comments": comments, "contentLen": 100, "updatedAt": "2026-01-01"}
        if comments is None:
            d.pop("comments", None)
        return d
    d = {"entity-type": "Knowledge", "id": str(i), "knowledgeId": str(i),
         "highlight": {"title": title or ("知识标题 %s" % i), "content": "正文 %s" % i},
         "classifies": [{"name": "星空旗舰版"}], "views": 10, "useful": 1,
         "comments": comments, "contentLen": 100, "updatedAt": "2026-01-01"}
    if comments is None:
        d.pop("comments", None)
    return d


@case("offline: 超限丢词必须在返回体里写明(keywordsDropped)")
def t_keywords_dropped_reported():
    """**ADR-0016 决策 4 的钉子**(规格 §4 新增用例)。

    N = 7(实测:7 词 = 7 次请求 = 3.33 秒)。收词超过 N → **按调用方给的顺序
    取前 N 个**,并在返回体里写明丢了几条。

    ⚠️ 「按顺序取前 N」**不违反**"内核不分轻重"——路序已逐字等于调用方给的顺序,
    内核没有做选择;违规的是**不说**。这条是既有纪律(`clamp_query` 的"宁可报错
    也不静默截断")在多路场景的落地。

    两个方向都验:超限必须报(且数字准确),未超限必须是 0(不得恒报)。
    """
    # (a) 给 9 个词 → 取前 7、dropped=2、scanNote 写明。
    nine = ["词%d" % i for i in range(1, 10)]
    r, seen = _patched_search((), keywords=nine, product_id=93)
    ok(r["keywordsDropped"] == 2, "9 个词应报 dropped=2,实为 %r" % (r["keywordsDropped"],))
    ok(len(r["queries"]) == 7, "应只发前 7 个词,实为 %r" % (r["queries"],))
    ok(r["queries"] == nine[:7],
       "必须是**按顺序取前 N**(不是挑重要的/不是取后 N): %r" % (r["queries"],))
    ok(len(seen) == 7, "上游实际应收到 7 次请求,实为 %d" % len(seen))
    ok("丢" in r["scanNote"] or "上限" in r["scanNote"],
       "scanNote 必须有一句人读的丢词说明(不得只写在数字字段里): %r" % (r["scanNote"],))
    ok("2" in r["scanNote"],
       "scanNote 应写明丢了几条(便于人读日志),实为 %r" % (r["scanNote"],))

    # (b) 恰好等于上限 → dropped 必须为 0(边界不得误报)。
    r2, _ = _patched_search((), keywords=nine[:7], product_id=93)
    ok(r2["keywordsDropped"] == 0,
       "恰好 7 个词不应报丢词(边界误报): %r" % (r2["keywordsDropped"],))
    ok(len(r2["queries"]) == 7, "7 个词应全部发出")
    ok("丢" not in r2["scanNote"],
       "未丢词时 scanNote 不得出现丢词说明: %r" % (r2["scanNote"],))

    # (c) 远少于上限 → 0,且不得出现丢词字样(反例对照)。
    r3, _ = _patched_search((), keywords=["甲", "乙"], product_id=93)
    ok(r3["keywordsDropped"] == 0 and "丢" not in r3["scanNote"],
       "2 个词不应报丢词: dropped=%r note=%r" % (r3["keywordsDropped"], r3["scanNote"]))

    # (d) 上限值本身来自声明(不是代码里的字面量):改声明必须改行为。
    import kd._impl._config as _cfg_mod
    real = _cfg_mod._CONTRACT
    try:
        broken = json.loads(json.dumps(real))
        broken["limits"]["maxKeywords"] = 3
        _cfg_mod._CONTRACT = broken
        r4, seen4 = _patched_search((), keywords=nine, product_id=93)
        ok(len(r4["queries"]) == 3 and r4["keywordsDropped"] == 6,
           "上限改为 3 后应发 3 个词、丢 6 个,实为 %r/dropped=%r"
           % (r4["queries"], r4["keywordsDropped"]))
        ok(len(seen4) == 3, "上限改为 3 后上游应只收到 3 次请求,实为 %d" % len(seen4))
    finally:
        _cfg_mod._CONTRACT = real
    ok(len(_patched_search((), keywords=nine, product_id=93)[0]["queries"]) == 7,
       "注入后未恢复声明(上限仍是 3)")


@case("offline: adopted 不被合成假 False(D1 的钉子)")
def t_adopted_not_falsified():
    """**D1 缺陷的钉子**(规格 §4 新增用例)。

    缺陷:合并两条同 key 条目时,`out["adopted"] = bool(out.get("adopted")) or
    bool(n.get("adopted"))` 在 `for n in ns[1:]` 里**只要同 key ≥2 条就无条件执行**,
    于是把**缺失键的 `None`** 折成 `False`——上游从没说过这条文章未被采纳,
    是我们替它说的。

    实测影响:那条"非问答条目 adopted 应为 None"的在线断言在真实数据上会红,
    只因合成数据的 id 互异而从未触发合并。

    契约(v6.6):**只在任一侧有值时取或**;两侧都无值 → 保持缺失。而 article 的
    `adopted` 按类型分档干脆**不该出现在条目上**(B1 顺带解决)。
    """
    cp = _impl()
    # ① 两条都**没有** adopted 键的 article 合并 → 不得出现 adopted(尤其是不得为 False)。
    a1 = cp._norm_item(_syn("article", 501), "article")
    a2 = cp._norm_item(_syn("article", 501), "article")
    ok("adopted" not in a1, "article 的 _norm_item 不该产出 adopted 键")
    merged = cp._manifest_merge([a1, a2])
    ok(merged.get("adopted") is None,
       "两条都无 adopted 键时合并结果不得被折成假 False(D1):实为 %r"
       % (merged.get("adopted"),))
    ok("adopted" not in cp._manifest_project(merged, {1}),
       "article 条目投影不得出现 adopted(类型分档 + D1 顺带解决)")

    # ② 一侧有值 → 取该值(真值传染)。
    q1 = cp._norm_item(_syn("answer", 601, qid=600, adopted=False), "answer")
    q2 = cp._norm_item(_syn("answer", 602, qid=600, adopted=True), "answer")
    ok(cp._manifest_merge([q1, q2])["adopted"] is True,
       "同帖有一条被采纳 → 帖级 adopted 应为 True(任一为真即为真)")
    # ③ 两侧都 False → False(不得被"缺失即不产出"的规则误伤)。
    q3 = cp._norm_item(_syn("answer", 603, qid=600, adopted=False), "answer")
    q4 = cp._norm_item(_syn("answer", 604, qid=600, adopted=False), "answer")
    ok(cp._manifest_merge([q3, q4])["adopted"] is False,
       "同帖两条都未被采纳 → 应为 False(不是 None):证明 ③ 不是'恒不产出'")
    # ④ 一侧无值、另一侧 False → False(有值的那侧胜出)。
    q5 = {"type": "question", "id": "700", "adopted": None}
    q6 = {"type": "question", "id": "700", "adopted": False}
    ok(cp._manifest_merge([q5, q6])["adopted"] is False,
       "一侧缺值、一侧 False 时应取 False")


@case("offline: comments 三类都从上游取(D2 的钉子)")
def t_comments_all_kinds():
    """**D2 缺陷的钉子**(规格 §4 新增用例)。

    缺陷:`_upstream._norm_item` 只在 `et == "answer"` 分支取 `x["comments"]`,
    knowledge 与 article 分支**根本没用该字段** —— 而上游**三类都给**
    (实测同一响应内 0/2/3)。这是"归一化白丢":上游给了、我们扔了,无任何信号。

    契约(v6.6):三类分支都取 `comments`;而声明侧它进**公共键集**(三类恒可达),
    否则 D2 会在投影层**再次被丢掉**(半修)。
    """
    cp = _impl()
    for et in ("knowledge", "answer", "article"):
        n = cp._norm_item(_syn(et, 801, comments=7), et)
        ok(n is not None, "%s 合成条目未被接受" % et)
        ok(n.get("comments") == 7,
           "%s 条目的 comments 应从上游取到(实为 %r)—— D2 白丢复发" % (et, n.get("comments")))
        p = cp._manifest_project(n, {1})
        ok("comments" in p,
           "%s 条目投影必须含 comments(它进公共键集,三类恒可达)" % et)
        ok(p["comments"] == 7, "%s 投影的 comments 应为 7,实为 %r" % (et, p["comments"]))
    # 上游**没给**该键时值为 None(而不是键消失)——"上游没给值"与"结构性不适用"
    # 是两件事:`comments` 对本类型**适用**,只是这次没值(None)。
    n_none = cp._norm_item({"entity-type": "Knowledge", "id": "99"},
                           "knowledge")
    ok("comments" in n_none and n_none["comments"] is None,
       "上游没给 comments 时应为 None(键仍在,因为该字段对本类型适用): %r" % (n_none,))
    ok(cp._manifest_project(n_none, {1})["comments"] is None,
       "上游没给值时投影应为 None(不得与'键不出现'混同)")
    # 声明侧的归属钉子:`comments` 必须在公共段(否则 knowledge 的取值会被投影层丢掉)。
    ok("comments" in RESULT_KEYS_COMMON,
       "comments 应在 resultKeysCommon(三类恒可达);若只在 question/article 专属段,"
       "knowledge 的取值会在投影时被静默丢掉(D2 半修)")
    ok("comments" not in RESULT_KEYS_BY_TYPE.get("knowledge", set()),
       "comments 不该重复出现在 knowledge 专属段(公共段已覆盖)")


@case("offline: read(question) 截断改字符串枚举(B3 的钉子)")
def t_truncated_enum():
    """**B3 的钉子**(规格 §4 新增用例)。

    原实现是布尔 `truncated: true`,把两种成因压成同一个值:
      * `"answer_limit"` —— 帖子太长没读完(翻页上限 / 详情只展开前 N 条);
      * `"upstream_error"` —— 上游报错(**该重试**)。

    为什么必须分开:`truncated` 是召回置信度判定(ADR-0010)的输入之一。
    把"上游坏了"与"资料就这么多"压成同一个值,会让判定**在最该保守的时候给出
    乐观结论**——上游故障时调用方会以为"这帖只有这些答案"。

    ⚠️ 规格已确认 `page_limit` 与 `budget` 两档**不存在**(清单侧不再截断),
    故只验枚举里的两个值。注入点在**唯一网络出口**(`_net._get_json`)。

    ⚠️ **2026-09-29 工单 #30**:`max_answer_pages` 形参已删除(翻页上限永不可触发);本条原先靠 `totalPages=9` 触发"翻页上限截断",现该路径不存在,故 ② 改为验证**同一条截断语义的另一来源**(条数超 `max_detail`,见 ③),并把"翻页上限已删"单独钉一条(见 `t_read_depth_in_contract`)。

    ⚠️ **2026-09-29 出口统一**:本用例原先打桩 `_detail._get_json` —— 那是 import 期
    快照,`_detail` 里已不存在这个名字。改打 `_net._get_json`(唯一真出口)后,
    **注入成为全内核生效**:它同时证明"替换网络出口即拦住深读侧全部请求"这条
    出口统一的验收标准。
    """
    # 注入点在**唯一网络出口**(`_net._get_json`)——深读侧 5 个调用点都经它。
    net_mod = _net_mod()
    real = net_mod._get_json
    import contextlib

    @contextlib.contextmanager
    def inject(fn):
        net_mod._get_json = fn
        try:
            yield
        finally:
            net_mod._get_json = real

    # ① 正常读完(页数与条数都在上限内)→ **不得**有 truncated 键(不是 false)。
    def fake_ok(url, rate=None):
        if "/api/questions/900/answers" in url:
            return {"content": [{"id": "a1", "description": "答案一"}], "totalPages": 1}
        if "/api/answers/" in url:
            return {"description": "答案一展开"}
        return {"title": "Q", "description": "问题正文", "answers": 1}
    with inject(fake_ok):
        r = _detail_mod_obj._question_detail("900", max_detail=5)
    ok("truncated" not in r,
       "未截断时不该出现 truncated 键(旧形态会产出布尔 false): 实有 %r"
       % (r.get("truncated"),))

    # ② answer_limit(成因一):**条数超过详情展开上限** —— 翻页上限删除后
    #    这是唯一剩下的"没读完"来源,故它必须仍能产出 `answer_limit`。
    def fake_limit(url, rate=None):
        if "/api/questions/901/answers" in url:
            return {"content": [{"id": "a%d" % i, "description": "答案 %d" % i}
                                for i in range(1, 8)], "totalPages": 1}
        if "/api/answers/" in url:
            return {"description": "答案一展开"}
        return {"title": "Q", "description": "问题正文", "answers": 30}
    with inject(fake_limit):
        r2 = _detail_mod_obj._question_detail("901", max_detail=5)
    ok(r2.get("truncated") == "answer_limit",
       "条数超详情展开上限应产出 'answer_limit',实为 %r" % (r2.get("truncated"),))
    ok(isinstance(r2.get("truncated"), str),
       "truncated 必须是字符串枚举(旧形态是布尔): %r" % (type(r2.get("truncated")).__name__,))
    # ⚠️ **2026-09-29(工单 #30)**:翻页**不再截断** —— 有限页必须全部取回。
    #    原实现 max_answer_pages=3 会把 9 页的帖子只读 3 页;现在有多少取多少。
    pages_hit = []
    with inject(fake_limit):
        def counting(url, rate=None):
            if "/answers" in url and "/api/answers/" not in url:
                pages_hit.append(url)
            return fake_limit(url, rate)
        net_mod._get_json = counting
        r_pages = _detail_mod_obj._question_detail("901", max_detail=0)
    ok(len(pages_hit) == 1,
       "统一 totalPages=1 时应只取 1 页,实取 %d 次" % len(pages_hit))
    ok(r_pages.get("answersTaken") == 7,
       "totalPages=1 时 7 条回答应全部取回(翻页上限已删),实得 %r"
       % (r_pages.get("answersTaken"),))

    # ②b 多页帖子:页数**不再被上限截断**,且按页号归位(完成先后不得决定顺序)。
    def fake_pages(url, rate=None):
        if "/api/answers/" in url:
            return {"description": "展开"}
        if "/answers" in url:
            p = int(url.split("page=")[1].split("&")[0])
            return {"content": [{"id": "p%d" % p, "description": "第%d页" % p}],
                    "totalPages": 6}          # 6 页 > 旧上限 3
        return {"title": "Q", "description": "问题正文", "answers": 6}
    with inject(fake_pages):
        # ⚠️ `max_detail` 要显式放到 6 以上,否则触发的是**另一条**截断
        # (逐条详情上限),本段专测"翻页不再截断",两个变量必须隔离。
        r6 = _detail_mod_obj._question_detail("906", max_detail=10)
    got_pages = [a["id"] for a in r6["answers"]]
    ok(got_pages == ["p1", "p2", "p3", "p4", "p5", "p6"],
       "6 页的帖子必须**全部**取回且按页号有序(翻页上限已删;并发不得打乱页序):"
       "实为 %r" % (got_pages,))
    ok(r6.get("truncated") is None,
       "翻页不再是截断来源,6 页全取回且 max_detail 足够时不应有 truncated: %r"
       % (r6.get("truncated"),))

    # ③ answer_limit:条数超出详情展开上限(另一条成因,同一枚举值)。
    def fake_many(url, rate=None):
        if "/api/questions/902/answers" in url:
            return {"content": [{"id": "a%d" % i, "description": "答案 %d" % i}
                                for i in range(1, 11)], "totalPages": 1}
        if "/api/answers/" in url:
            return {"description": "展开"}
        return {"title": "Q", "description": "问题正文", "answers": 10}
    with inject(fake_many):
        r3 = _detail_mod_obj._question_detail("902", max_detail=2)
    ok(r3.get("truncated") == "answer_limit",
       "详情展开上限截断应产出 'answer_limit'(与上一条同一档:都是'没读完'),"
       "实为 %r" % (r3.get("truncated"),))

    # ④ upstream_error:上游报错 → **另一档**(该重试),不得与"没读完"混同。
    def fake_err(url, rate=None):
        if "/api/questions/903/answers" in url:
            raise core.UpstreamError(500, "boom")
        return {"title": "Q", "description": "问题正文", "answers": 30}
    with inject(fake_err):
        r4 = _detail_mod_obj._question_detail("903", max_detail=5)
    ok(r4.get("truncated") == "upstream_error",
       "上游报错应产出 'upstream_error'(与 answer_limit 区分开),实为 %r"
       % (r4.get("truncated"),))

    # ④b **部分失败**:首页成功、后续页报错 —— 已取回的回答**不得被丢弃**(2026-09-29 补)。
    # 治的病:原写法把 `answers = pages` 放在并发翻页块**之后**,任一路抛错即穿透到外层
    # `except`,`answers` 停在 `[]` —— 把"上游抖一次"伪装成"这帖没回答"。
    # ⚠️ 上一条 ④ 测不到这条路径(它首页就炸,没有"已取回"的部分可丢)。
    def fake_partial(url, rate=None):
        if "/api/questions/904/answers?page=1" in url:
            return {"content": [{"id": "a%d" % i, "description": "答案 %d" % i}
                                for i in range(1, 6)], "totalPages": 3}
        if "/api/questions/904/answers" in url:
            raise core.UpstreamError(500, "part-fail")
        return {"title": "Q", "description": "问题正文", "answers": 25}
    with inject(fake_partial):
        r4b = _detail_mod_obj._question_detail("904", max_detail=5)
    ok(r4b.get("truncated") == "upstream_error",
       "翻页部分失败应仍报 'upstream_error',实为 %r" % (r4b.get("truncated"),))
    ok(r4b.get("answersTaken") == 5,
       "**首页已成功取回的 5 条回答不得被翻页失败丢弃**(截断标记可给,数据不可吞):"
       "实测 answersTaken=%r(期望 5),ids=%r"
       % (r4b.get("answersTaken"), [a.get("id") for a in r4b.get("answers") or []]))
    ok([a.get("id") for a in r4b.get("answers") or []] == ["a1", "a2", "a3", "a4", "a5"],
       "部分失败时保住的必须是首页那批原始条目,实得 %r"
       % ([a.get("id") for a in r4b.get("answers") or []],))
    # ④c **并发块内「已完成未归位」的页不得丢**(2026-09-29 二次修正补的钉子)。
    # 治的病:④b 的桩里 page≥2 **全部**失败,结构上**不存在**"某页已成功返回、
    # 另一页抛错"这一状态,故它抓不到下面这个真回归:
    #   首版把各页结果先收进局部 `rest[p]`,等全部 future 成功后才 `pages.extend(...)`;
    #   且 `for fut in as_completed(futs): ad = fut.result()` 在**某页抛错的瞬间**
    #   就向外交棒 —— 其余已 in-flight 返回的页根本没被收集。
    #   实测(page1=5、page2=3、page3 抛错且**最先完成**):首版得 5 条,
    #   而 HEAD 的串行版在同一构造下保住 **8** 条 —— 是真回归,不是"范围问题"。
    # 构造要点:让**失败页最先完成**,才能确保"已完成的成功页"确实处于未归位状态;
    # 用 `time.sleep` 拉开 300ms/10ms 的差,使先后顺序确定而非碰运气。
    import time as _t
    def fake_race(url, rate=None):
        if "/api/questions/905/answers?page=1" in url:
            return {"content": [{"id": "a%d" % i} for i in range(1, 6)], "totalPages": 3}
        if "page=2" in url:
            _t.sleep(0.30)                      # 慢:确保 p3 先完成
            return {"content": [{"id": "b%d" % i} for i in range(1, 4)], "totalPages": 3}
        if "page=3" in url:
            _t.sleep(0.01)                      # 最快:先抛错,打断收集循环
            raise core.UpstreamError(500, "page3-fail")
        return {"title": "Q", "description": "问题正文", "answers": 25}
    with inject(fake_race):
        r4c = _detail_mod_obj._question_detail("905", max_detail=5)
    ids4c = [a.get("id") for a in r4c.get("answers") or []]
    ok(r4c.get("truncated") == "upstream_error",
       "④c:失败页报错时仍须置 'upstream_error',实为 %r" % (r4c.get("truncated"),))
    ok(r4c.get("answersTaken") == 8,
       "④c:**并发块内已成功返回的页不得因另一页失败而丢弃** —— 首页 5 条 + page2 的 3 条"
       "共 8 条必须全保住(实测 answersTaken=%r;若为 5 说明收集循环被异常打断,"
       "其余已完成页随栈帧丢弃 —— 与 HEAD 串行版对照即知是真回归)" % (r4c.get("answersTaken"),))
    ok(ids4c == ["a1", "a2", "a3", "a4", "a5", "b1", "b2", "b3"],
       "④c:保住的条目必须**按页号归位**(不得让完成先后决定回答顺序),实得 %r" % (ids4c,))
    # ④d **真实上游故障形态**(不只 `UpstreamError`)下同样不得丢已取页(2026-09-29 补)。
    # 治的病(code review High-1):④b/④c 用的都是**受控的 `UpstreamError`** ——
    # 而 `_net._get_json` 只把"HTTP 200 带 errorCode"包装成它;真实的
    # `URLError`(不可达)/`socket.timeout`(20s 超时)/`JSONDecodeError`(非 JSON 响应)
    # **直接穿透**,原先深读侧只 catch `UpstreamError` → 这些形态下已取回的 8 条
    # **整包丢弃**且无日志、返 `internal_error`(实测 8→0,比 ④b 治的 0→5 更差)。
    # ⚠️ 判据要点:**受控异常与真实网络异常必须同结果** —— 这正是"两侧不对称"的抓取点。
    import http.client as _http_client

    def _mk_race(exc):
        def _f(url, rate=None):
            if "?page=1" in url:
                return {"content": [{"id": "a%d" % i} for i in range(1, 6)], "totalPages": 3}
            if "page=2" in url:
                _t.sleep(0.20)
                return {"content": [{"id": "b%d" % i} for i in range(1, 4)], "totalPages": 3}
            if "page=3" in url:
                _t.sleep(0.01)
                raise exc
            return {"title": "Q", "description": "问题正文", "answers": 25}
        return _f
    for _label, _exc in (
            ("urllib.error.URLError(不可达)", urllib.error.URLError("network down")),
            ("socket.timeout(超时)", socket.timeout("timed out")),
            ("json.JSONDecodeError(响应非 JSON)", json.JSONDecodeError("bad", "doc", 0)),
            # ⚠️ **HTTP 协议层故障不是 `OSError` 子类**(2026-09-29 补,分类边界):
            # 实测 `isinstance(http.client.IncompleteRead(b"x"), OSError)` → **False**。
            # 它在 `urlopen` + `r.read()` 路径上会真实发生(响应体读到一半连接断),
            # 若只收 `OSError` 就会漏掉 —— 故此条是**分类完整性的钉子**。
            ("http.client.IncompleteRead(响应体截断)", _http_client.IncompleteRead(b"x")),
            ("http.client.BadStatusLine(状态行非法)", _http_client.BadStatusLine("bad"))):
        with inject(_mk_race(_exc)):
            _r = _detail_mod_obj._question_detail("907", max_detail=0)
        ok(_r.get("truncated") == "upstream_error",
           "④d:%s 属**上游故障**,应置 'upstream_error'(与 answer_limit 区分),实为 %r"
           % (_label, _r.get("truncated")))
        ok(_r.get("answersTaken") == 8,
           "④d:**%s 下已取回的 8 条不得被丢弃** —— 真实网络故障与受控 "
           "`UpstreamError` 必须同等对待(原先深读侧只 catch `UpstreamError`,"
           "这类形态会把已取页整包丢掉并返 internal_error)。实测 answersTaken=%r,ids=%r"
           % (_label, _r.get("answersTaken"),
              [a.get("id") for a in _r.get("answers") or []]))
    # ⚠️ **反面**:程序缺陷**必须**继续穿透 —— 不得被"上游故障"分类吞掉。
    # 若有人把 `except _net.UPSTREAM_FAILURES` 放宽成裸 `except Exception`,
    # 本条会红:那等于把"我们写错了"伪装成"上游抖了",与本仓禁忌同型。
    with inject(_mk_race(AttributeError("our own bug"))):
        _raised = None
        try:
            _detail_mod_obj._question_detail("907", max_detail=0)
        except AttributeError as _e:
            _raised = _e
        except Exception as _e:                                  # noqa: BLE001
            _raised = _e
        ok(isinstance(_raised, AttributeError),
           "④d:程序缺陷(AttributeError)必须**响亮穿透**成 internal_error,"
           "不得被上游故障分类吞成 'upstream_error' —— 否则"
           "「我们写错了」会被伪装成「上游抖了」,实得 %r" % (_raised,))
    # 区分度钉子:两档必须是两个不同的值(否则本用例无法区分实现)。
    # ④e **失败页号不得取决于线程调度**(2026-09-29 补,code review M-1)。
    # 治的病:原先只留"第一个异常",而"第一个"取的是 `as_completed` 的**完成序**
    # —— 同一输入下哪一页被报出去会随线程调度变化,等于**让完成先后泄漏进结果**
    # (本仓明令禁止:见 `_manifest` 的路序纪律)。现改为按页号记 `failed_pages`
    # 并报 `min(failed_pages)`,与调度无关。
    # ⚠️ 判据做法:让**大页号先失败、小页号后失败**(完成序与页号相反),
    # 断言最终报出的仍是**最小页号**那个 —— 若实现退回"留第一个完成者",本断言会红。
    def _mk_two_fail():
        def _f(url, rate=None):
            if "?page=1" in url:
                return {"content": [{"id": "a%d" % i} for i in range(1, 6)], "totalPages": 4}
            if "page=2" in url:          # 小页号,**后**失败(慢)
                _t.sleep(0.30)
                raise core.UpstreamError(500, "page2-late")
            if "page=3" in url:          # 大页号,**先**失败(快)
                _t.sleep(0.01)
                raise core.UpstreamError(429, "page3-early")
            return {"title": "Q", "description": "问题正文", "answers": 25}
        return _f
    with inject(_mk_two_fail()):
        _r4e = _detail_mod_obj._question_detail("908", max_detail=0)
    ok(_r4e.get("truncated") == "upstream_error",
       "④e:多页失败时仍须置 'upstream_error',实为 %r" % (_r4e.get("truncated"),))
    ok(_r4e.get("answersTaken") == 5,
       "④e:两页失败时只剩首页 5 条(page2/3 均失败),实为 %r" % (_r4e.get("answersTaken"),))
    # 关键:失败页号必须是**确定的最小页号**,与完成序无关。
    # ⚠️ 判据打在**日志**上(`log()` 写 stderr):返回体里本来就没有"哪页失败"这个
    # 字段(那需要改契约,本轮不做),故真正可观测的不变量是日志里的 `failedPages`。
    # 期望:`failedPages=[2, 3]` —— 完成序是 3 先 2 后,而报告的是完整的页号集合。
    import contextlib as _ctx
    import io as _io
    _buf = _io.StringIO()
    with inject(_mk_two_fail()):
        with _ctx.redirect_stderr(_buf):
            _r4e2 = _detail_mod_obj._question_detail("908", max_detail=0)
    _log_txt = _buf.getvalue()
    ok("failedPages=[2, 3]" in _log_txt,
       "④e:日志必须报出**完整的失败页号集合**(按页号排序,与完成先后无关)。"
       "完成序是 page3 先、page2 后,故这里若出现 `[3]` 或 `[3, 2]` 都说明"
       "完成先后泄漏进了结果。实测日志:%r" % (_log_txt.strip()[:300],))
    ok(_r4e2.get("answersTaken") == 5,
       "④e:两页失败时只剩首页 5 条,实为 %r" % (_r4e2.get("answersTaken"),))
    # 区分度钉子:两档必须是两个不同的值(否则本用例无法区分实现)。
    ok(r2.get("truncated") != r4.get("truncated"),
       "两种截断成因必须是不同的枚举值(旧布尔形态下二者同形,判定会失真)")
    # 幂等性:恢复注入后必须回到正常路径(否则污染后续用例)。
    with inject(fake_ok):
        r5 = _detail_mod_obj._question_detail("900", max_detail=5)
        ok("truncated" not in r5, "注入恢复失败:截断标记仍在")


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


@case("offline: 清单保序 —— 顺序 = (给词顺序, 路内名次),且不产生任何排序分")
def t_manifest_keep_order():
    """**ADR-0013 零算法排序 + ADR-0016 决策 2 的钉子**(2026-09-29 改写,工单 #33)。

    ⚠️ **本条原先是"排序键不含命中路数",问的是 `_manifest_rank`** ——
    那个函数**已被删除**(它是恒等函数:200 组随机构造下
    `sorted(order, key=rank) == order` 恒成立,排完等于没排)。
    故判据从"排序键的形状"改为**顺序本身**,后者才是真正的契约。

    现在由**一个隐式不变量**承载顺序:`_manifest_fuse` 里
    `order.append(k)` 与 `first_seen[k] = (route_no, pos)` **同处写入**
    (只在 `k not in route_hits` 分支各发生一次),故 `order` 的次序
    逐字等于 `first_seen` 的(路序, 名次)次序。本条把它验成**行为断言**。

    钉两条:
      ① **路序优先**:命中路数少的条目,只要路序靠前,就排在前 —— 反向构造,
         若实现按"命中路数"排序则必红;
      ② `first_seen` 与产物顺序**一致**(不变量可观测)。
    """
    cp = _impl()
    # 构造:条目 X 只被第 1 路命中(路序靠前);条目 Y 被第 2、3 路命中(命中路数更多)。
    # 若排序掺入"命中路数",Y 会排到 X 前面 —— 那正是本条要防的。
    X = cp._norm_item(_syn("knowledge", 11, title="X-靠前但只中1路"), "knowledge")
    Y = cp._norm_item(_syn("knowledge", 22, title="Y-靠后但中2路"), "knowledge")
    assert X and Y, "合成条目未被接受"

    # 路 1 只出 X;路 2、3 都出 Y(于是 Y 的 hitRoutes=2,路序 2;X 的路序 1)。
    keys, hits, first, by = cp._manifest_fuse([(1, [X]), (2, [Y]), (3, [Y])])
    want = [cp._manifest_key(X), cp._manifest_key(Y)]
    ok(keys == want,
       "清单顺序必须是 (首次路序, 路内名次) —— X 首次出现于第 1 路,故 X 在前;"
       "若 Y 排到前面,说明排序掺入了『命中路数』(命中路数是由本内核算出来的量,"
       "按它排等于在官方综合排序之上再叠一层我们自己的权重,ADR-0013 明令禁止)。"
       "实得 %r" % (keys,))
    ok(hits[want[1]] == {2, 3},
       "Y 应被第 2、3 两路命中(hitRoutes=2): %r" % (hits[want[1]],))
    # ② 不变量可观测:顺序与 first_seen 的(路序, 名次)次序逐字一致。
    ok(keys == sorted(first, key=lambda k: first[k]),
       "产物顺序与 first_seen 的(路序, 名次)次序不一致 —— 保序不变量被破坏:"
       "\n  keys  %r\n  first %r" % (keys, sorted(first, key=lambda k: first[k])))
    # ③ 反向确认:`first_seen` 必须是**单射**(无 tie-break),否则"保序"会依赖字典序。
    vals = list(first.values())
    ok(len(set(vals)) == len(vals),
       "first_seen 不是单射(存在同(路序,名次)的两条)—— 顺序会退化成不确定: %r" % (vals,))
    # ④ `_manifest_rank` 这个恒等函数必须**真的删掉了**(不得留兼容层)。
    ok(not hasattr(cp, "_manifest_rank"),
       "实现包仍导出 _manifest_rank —— 它是恒等函数(排完等于没排),已按用户裁定删除")


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


@case("offline: 清单投影字段集按类型固定(不适用键不出现;无 contentText/views/questionId)")
def t_manifest_projection():
    """字段集的**双向**断言:既不得多出、也不得少了 —— 而且**按类型分别断言**。

    ⚠️ 别名:施工规格 §4 把这条新增用例命名为 `t_field_set_by_type`。实际实现落在
    本用例(②③ 正是"三类型各自的键集"与"不适用键不出现"两条断言),故未另立一个
    同义用例 —— 同一批断言分两处写只会增加维护面。按规格名查找时请到这里。

    键集不手写在这里,而是从 `contract.json` 派生(决策 D13)。本用例钉三件事:
      ① 代码产出的键集 == **该类型**应当有的键集(多一个即红:防"顺手加个字段");
      ② 声明的必含键集 ⊆ 产出(少一个即红:防"字段被删而声明没跟上");
      ③ **类型不适用的键直接不出现**(ADR-0016 决策 6,v6.6 新增):
         article 不得有 `adopted`,knowledge 不得有 `supports` —— 前提是"没出现",
         而**不是"值为 None"**。"结构性不适用"与"上游没给值"是两件事,
         混成 null 会让调用方分不清"这个字段对本类型无意义"与"这次没取到"。
    """
    cp = _impl()
    for et in ("knowledge", "question", "article"):
        # ⚠️ 喂给 _norm_item 的是**上游** entity-type:问答题必须传 "answer"。
        up_et = "answer" if et == "question" else et
        n = cp._norm_item(_syn(up_et, 7), up_et)
        ok(n is not None, "%s 合成条目未被 _norm_item 接受(构造数据形状不对)" % et)
        ok(n["type"] == et, "%s 条目规范化后 type 应为 %r,实为 %r" % (et, et, n["type"]))
        p = cp._manifest_project(n, {1, 2})
        ks = set(p.keys())
        # ① 上界:不得出现该类**允许集**之外的键(hitRoutes/routes 是内核算出的命中信息,
        #    不在声明里当条目字段,故并入允许集)。
        allowed = result_keys_of(et) | {"hitRoutes", "routes"}
        extra = ks - allowed
        ok(not extra, "%s 条目投影出现允许集外字段: %s" % (et, sorted(extra)))
        # ② 下界:该类型应当有的键必须**全部产出**。
        missing = allowed - ks
        ok(not missing, "%s 条目投影缺字段: %s" % (et, sorted(missing)))
        # ③ 类型专属键的**排他性**:别的类型的专属键一个都不许出现。
        for other in ("knowledge", "question", "article"):
            if other == et:
                continue
            intruders = (RESULT_KEYS_BY_TYPE.get(other, set())) & ks
            ok(not intruders,
               "%s 条目出现了 %s 的专属键 %s —— 类型不适用的键必须**不出现**"
               "(而非填 null,ADR-0016 决策 6)" % (et, other, sorted(intruders)))
        # ⚠️ v6.6:此处原有一句 `check_no_forbidden(ks, RESULT_FORBIDDEN_KEYS, …)`,
        # 已删。它断言"白名单投影的产物里不含禁止键" —— **恒绿**(见 CONTEXT.md
        # 「禁止键名单是文档记录,不是防线」:清单字段是白名单拼的,白名单外根本产不出键)。
        # 施工规格 §B4 也点名删掉这套冗余断言。禁止键名单仍留在 contract.json 里作为
        # **文档记录**(记着"这些键是刻意删掉的",防后人加回),但不作断言对象。
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
    # ⚠️ article 的 `adopted` 必须**不出现**(D1 的出口侧钉子):同 key 两条 article
    # 合并时曾把缺失键的 None 折成假 False("上游没说过它未被采纳,是我们替它说的"),
    # 而按类型投影后这个键根本不该存在。
    ok("adopted" not in cp._manifest_project(art, {1}),
       "article 条目投影出现了 adopted(它只对 question 有意义,且 D1 曾在此合成假 False)")


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

    # ---- ① 交叉核对:声明 == 冻结基线 == 代码兜底集(按类型三份,v6.6) ----
    declared_common = tuple(_CONTRACT["search"]["resultKeysCommon"])
    declared_by = {str(k): tuple(v)
                   for k, v in (_CONTRACT["search"]["resultKeysByType"] or {}).items()}
    declared_top = tuple(_CONTRACT["search"]["topKeys"])
    ok(declared_common == FROZEN_RESULT_KEYS_COMMON,
       "contract.json 的 resultKeysCommon 与冻结基线漂移:\n  声明 %r\n  基线 %r\n"
       "▶ 若这是有意改对外契约,请同步 tests/kd_regression.py 的 FROZEN_RESULT_KEYS_COMMON;"
       "若无意,说明声明被误改了。" % (declared_common, FROZEN_RESULT_KEYS_COMMON))
    # 类型专属段:三份都要对账(漏一份 = 漏一个类型的字段保护)。
    ok(set(declared_by) == set(FROZEN_RESULT_KEYS_BY_TYPE),
       "contract.json 的 resultKeysByType 类型集与冻结基线不符: %r vs %r"
       % (sorted(declared_by), sorted(FROZEN_RESULT_KEYS_BY_TYPE)))
    for kind in sorted(FROZEN_RESULT_KEYS_BY_TYPE):
        ok(tuple(declared_by.get(kind, ())) == tuple(FROZEN_RESULT_KEYS_BY_TYPE[kind]),
           "contract.json 的 resultKeysByType[%s] 与冻结基线漂移:\n  声明 %r\n  基线 %r"
           % (kind, declared_by.get(kind), FROZEN_RESULT_KEYS_BY_TYPE[kind]))
    ok(declared_top == FROZEN_TOP_KEYS,
       "contract.json 的 topKeys 与冻结基线漂移:\n  声明 %r\n  基线 %r"
       % (declared_top, FROZEN_TOP_KEYS))
    # 公共段与专属段不得重叠(否则同一个键有两种归属,分档失去意义)。
    for kind, extra in declared_by.items():
        dup = set(declared_common) & set(extra)
        ok(not dup, "公共段与 %s 专属段重叠: %s" % (kind, sorted(dup)))
    # 代码内置兜底集也必须与声明同形(声明文件缺失时生效,能自由漂移就无信号)。
    ok(tuple(cp._FALLBACK_COMMON_KEYS) == declared_common,
       "代码兜底公共键集与声明不一致:\n  兜底 %r\n  声明 %r"
       % (tuple(cp._FALLBACK_COMMON_KEYS), declared_common))
    for kind in sorted(declared_by):
        ok(tuple(cp._FALLBACK_BY_TYPE_KEYS.get(kind, ())) == declared_by[kind],
           "代码兜底 %s 专属键集与声明不一致: %r vs %r"
           % (kind, cp._FALLBACK_BY_TYPE_KEYS.get(kind), declared_by[kind]))
    ok(set(declared_common).isdisjoint(RESULT_FORBIDDEN_KEYS),
       "声明自相矛盾:这些键同时在公共段与 resultForbiddenKeys: %s"
       % sorted(set(declared_common) & RESULT_FORBIDDEN_KEYS))

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
        # ⚠️ v6.6:按**该条目的类型**取期望键集(公共 + 专属),不再是一份全集。
        want = set(_frozen_keys_of(it.get("type"))) | {"hitRoutes", "routes"}
        miss = want - kk
        extra = kk - want
        ok(not miss, "%s 真实输出缺字段: %s(▶ 字段被删而声明没跟上——"
                     "这正是 d753719 那次漏报的形态)" % (name, sorted(miss)))
        ok(not extra, "%s 真实输出多出该类型不该有的字段: %s"
                      "(▶ 新增字段未登记进 contract.json,或类型分档写错了)"
           % (name, sorted(extra)))
        # (v6.6 删:此处原有的 check_no_forbidden 恒绿 —— `extra = kk - want`
        #  已经覆盖"多出任何白名单外键",而 want 里不含禁止键,故它恒真。见 CONTEXT.md:36。)
    ok(seen_types == {"knowledge", "question", "article"},
       "三种实体的真实条目未全被链路产出(实得 %r)——覆盖缺口会让某档的字段检查静默跳过"
       % (sorted(seen_types),))

    # ---- ③ 注入自检:证明 ①② 有抓取力(而不是新的重言式) ----
    _assert_declared_mismatch_fails(declared_common)
    _assert_code_side_new_key_fails(items)
    _assert_type_partition_has_teeth()


def _manifest_via_full_chain(items):
    """把合成条目喂进**完整检索链路**,返回真实的 search 返回体(不碰 `_manifest_project`)。

    做法:打桩 `_upstream._search_upstream`,再调 `core.search`。
    这样断言的对象是"用户真的会拿到的东西",而不是内部函数的入参/出参对。

    ⚠️ 注入点 v6.6 变更(A6 / ADR-0016):原实现替换包属性
    `kd._impl._search_upstream`(靠 `_manifest._resolve()` 的字符串查表生效)。
    `_resolve` 与拆词器一同删除后,注入点改为**模块级入口** `_upstream._search_upstream`
    ——`_manifest._route_search_once` 逐次经它调用,故替换模块属性即生效,
    不再依赖"把名字挂到包命名空间"这种间接层。
    """
    mod = _upstream_mod_obj
    real = mod._search_upstream

    def fake(text, product_id, page, page_size, global_, sorts_type, rate=None):
        return {"content": [dict(x) for x in items], "totalElements": len(items),
                "totalPages": 1}

    mod._search_upstream = fake
    try:
        return core.search(keywords=["契约自检探针"], product_id=93)
    finally:
        mod._search_upstream = real


def _frozen_keys_of(kind):
    """某类型条目的**冻结期望键集**(公共 + 专属;独立于 contract.json)。

    与 `FROZEN_RESULT_KEYS_COMMON` / `FROZEN_RESULT_KEYS_BY_TYPE` 同一份真相,
    只是按 kind 拼起来用。独立于声明是刻意的(打断"回声与自回声比",见基线注释)。
    """
    return set(FROZEN_RESULT_KEYS_COMMON) | set(FROZEN_RESULT_KEYS_BY_TYPE.get(str(kind), ()))


def _assert_type_partition_has_teeth():
    """注入:类型分档若退化成"三份一样",② 必须能红。

    这是 v6.6 新增断言的**自检**——"按类型分档"本身可能是个摆设:
    若实现改成"所有类型都产全集"(即回到旧的统一填充),那么
      * article 会出现 `adopted`(本不该有);
      * 而断言若只检查"缺字段",就完全抓不住。
    故此处直接验算:拿 article 的全集版键集去比该类型的期望,差分**必须非空**
    ——证明"多出该类型不该有的字段"这条判据真的有抓取力。
    """
    art_full = set(FROZEN_RESULT_KEYS_COMMON) | set().union(*FROZEN_RESULT_KEYS_BY_TYPE.values())
    want = _frozen_keys_of("article")
    extra = art_full - want
    # ⚠️ 2026-09-29(工单 #32):新增 `other` 档后,"全集"多出它的两个专属键。
    # 本自检的**意图**是"多出的字段确实存在且分档非退化",故这里要与
    # `FROZEN_RESULT_KEYS_BY_TYPE` 保持**同源**地列出所有非 article 的专属键,
    # 而不是写死三个 —— 写死会让下次加档时在此处假红(摩擦该在契约变更处,
    # 不在自检的措辞里)。
    expect_extra = set()
    for k, v in FROZEN_RESULT_KEYS_BY_TYPE.items():
        if k != "article":
            expect_extra |= set(v)
    ok(extra == expect_extra,
       "类型分档自检失败:article 的'全集版'与期望的差分应为**其它所有档**的专属键 %r,"
       "实为 %r —— 差分变了说明分档结构动了,请核对本自检"
       % (sorted(expect_extra), sorted(extra)))
    ok(extra,
       "类型分档自检失败:差分是空的 —— 说明所有类型键集相同,分档退化成摆设")
    ok(_frozen_keys_of("knowledge") != _frozen_keys_of("question"),
       "类型分档自检失败:knowledge 与 question 的期望键集相同 —— 分档退化成摆设")
    ok(_frozen_keys_of("article") != _frozen_keys_of("knowledge"),
       "类型分档自检失败:article 与 knowledge 的期望键集相同 —— 分档退化成摆设")


def _assert_declared_mismatch_fails(declared):
    """注入:声明里删掉一个字段 → ① 必须红。

    直接验证"对账函数"的判定逻辑:用一个少一个键的声明去比冻结基线,
    **必须**得出不相等。若这里判等成立,说明对账退化成恒真,本用例整体无保护力。
    """
    broken = tuple(k for k in declared if k != "products")
    ok(len(broken) == len(declared) - 1,
       "注入自检构造失败:声明里没有 products,无法验证删除能被抓住")
    ok(broken != FROZEN_RESULT_KEYS_COMMON,
       "注入自检失败:从声明删掉 products 后,与冻结基线的比对**仍然判等**"
       "—— 说明对账已退化成重言式(这正是 T4 要修的形态)")
    ok(set(FROZEN_RESULT_KEYS_COMMON) - set(broken) == {"products"},
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
    extra = set(injected) - _frozen_keys_of("knowledge")
    ok(extra == {"productsV2"},
       "注入自检失败:代码侧新增未声明字段未被对账识别(实得差分 %r)" % (sorted(extra),))
    # 反向确认:未注入时差分必须为空(否则上面那条会因为"永远有差分"而假绿)。
    ok(not (set(p) - _frozen_keys_of("knowledge")),
       "未注入时投影已多出字段 %s —— 真实代码与契约不符,请先修代码"
       % sorted(set(p) - _frozen_keys_of("knowledge")))


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
    # ③ 方向钉子(**2026-09-29 改判**):三档之间曾有的差异是"question 不给链接",
    #    其依据是"问答恒不可点"—— 该结论**已被推翻**(死的是 URL 形式,不是这一档)。
    #    现行口径:三档都给。故判据改为"question 必须是 link",
    #    并**单独钉住它为什么变**(对应的 URL 形式必须是长形式,见下一条用例)。
    ok(cp.link_for("question") == "link",
       "question 的政策应为 link(2026-09-29 改判:长形式 /questions/<qid>/answers/<aid> "
       "实测 22/22 可点;旧 no-link 依据的『问答恒不可点』测的是单数短形式,已被推翻):"
       "实为 %r" % (cp.link_for("question"),))
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


@case("offline: 链接政策落在生产路径上(三档都给 url;问答必须是长形式)")
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

    ⚠️ **2026-09-29 改判**:三档现在都是 `link`,故本条的判据从"question 必须被抑制"
    改为**"问答的 url 必须是长形式"** —— 后者才是改判后真正会出错的地方:
    政策说给链接,而内核若仍拼单数短形式,给出去的就是一个恒死的地址
    (比"不给"更糟:读者以为资料不存在)。
    """
    cp = _impl()
    # ① 清单投影:三档都给 url(2026-09-29 起 question 也给了)。
    for et, kind in (("answer", "question"), ("knowledge", "knowledge"),
                     ("article", "article")):
        n = cp._norm_item(_syn(et, 7, qid=6), et)
        ok(n is not None, "合成条目未被接受(%s)" % et)
        p = cp._manifest_project(n, {1})
        ok(p["url"] and str(p["url"]).startswith("https://"),
           "清单里 %s 条目的 url 必须保留(linkPolicy: link),实为 %r"
           % (kind, p["url"]))
        # 抑制不能顺手把字段删掉 —— 字段仍在契约里,只是值由政策决定。
        ok("url" in p, "%s 条目的 url 字段被删了(应保留字段)" % kind)

    # ①b **问答必须拼长形式**(`/questions/<帖子号>/answers/<回答号>`)——
    #     短形式 `/question/<qid>` 实测 22/22 死、复数无 aid 段 4/4 死。
    qn = cp._norm_item(_syn("answer", 7, qid=6), "answer")
    qurl = str(qn.get("url") or "")
    ok(qurl.startswith("https://vip.kingdee.com/questions/"),
       "问答 url 必须是**复数** `/questions/...`(单数 `/question/` 实测恒死),实为 %r"
       % qurl)
    ok("/answers/" in qurl,
       "问答 url 必须带 `/answers/<回答号>` 段(复数但无 aid 段实测 4/4 死),实为 %r"
       % qurl)

    # ①c **回答号缺失时不得回落短形式** —— 那是死链。正确答案是"不给链接"。
    bare = {"entity-type": "Answer", "questionId": "6", "id": "", "title": "T"}
    bn = cp._norm_item(bare, "answer")
    ok(bn.get("url") is None,
       "回答号缺失时必须**给不出链接**(不得回落短形式 —— 那 22/22 不可点),实为 %r"
       % (bn.get("url"),))

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


@case("offline: 链接口径五份文档 + 声明一致(无残留『不给链接』/『都可点』表述)")
def t_link_policy_docs_in_sync():
    """**T1 的文档同步钉子** —— 治的是"同一事实抄四遍"。

    实测的翻车形态:`README.md:189` 写"引用做成可点击角标",而 SKILL.md 与
    ANSWER-SPEC 同时写"当前不给链接",两份在同一仓库里**正面对撞**;
    而 README 那一处**此前从未被列为同步点**。

    ⚠️ **2026-09-29 补 `CONTEXT.md`(工单 #33 项三欠账)** —— 本条 docstring 自称
    "五处载体"而 `files` 只有**四处**,`CONTEXT.md` 是那个长期缺席的第五处。
    **后果已经实际发生一次**:`CONTEXT.md` 里曾断言「`linkPolicy.question` 仍为
    `no-link`」—— 与声明事实**直接相反**(实际已是 `link`),却完全逃过本钉子;
    agent 照它执行就不给问答链接,**直接抵消工单 #29 的对外效果**。
    故本次把 `CONTEXT.md` 纳入 `files`,并补上它当时逃逸所依赖的那句 stale 表述。

    本条不检查措辞好坏,只检查**政策方向**在五处载体里一致:
      contract.json(唯一真源)/ ANSWER-SPEC / SKILL.md / README / CONTEXT.md / _upstream.py 注释。
    """
    files = {
        "ANSWER-SPEC": os.path.join(REPO, "docs", "ANSWER-SPEC.md"),
        "SKILL.md": os.path.join(REPO, "skills", "kingdee-knowledge", "skills",
                                 "kingdee-knowledge", "SKILL.md"),
        "README": os.path.join(REPO, "README.md"),
        # ⚠️ 第五处:术语表。它曾因缺席而漂移出与声明相反的口径(见 docstring)。
        "CONTEXT.md": os.path.join(REPO, "CONTEXT.md"),
        "_upstream.py": os.path.join(SRC, "kd", "_impl", "_upstream.py"),
    }
    # ① 过期表述不得残留(它们是 09-27 那套"一律 302"口径的化石)。
    #    ⚠️ **但"被引用来说明它已废止"是合法的**(如 `_Avoid_` 行:
    #    `"三条路径一律 302"(已推翻的实测口径)`)—— 那不是化石,是把错误口径
    #    钉在案上以免再犯。故判据是"**未被废止标注地裸着出现**":
    #    该串所在的那一行里若含"已推翻/已废止/已过期/当时"等字样,即放过。
    stale = ("三条路径现在一律 302", "三条网页路径实测全失效",
             "三条路径一律 302", "当前不给链接",
             # ⚠️ 2026-09-29 补:`CONTEXT.md` 逃逸本钉子时所依赖的正是下面这句
             # ("只给标题与出处"= question 不给链接的旧口径)。加它才真正堵住该形态。
             "question 只给标题与出处",
             "linkPolicy.question 仍为 `no-link`")
    retired_marks = ("已推翻", "已废止", "已过期", "当时", "旧口径", "化石")
    for name, path in files.items():
        txt = open(path, encoding="utf-8").read()
        for bad in stale:
            for lineno, line in enumerate(txt.split("\n"), 1):
                if bad not in line:
                    continue
                ok(any(m in line for m in retired_marks),
                   "%s:%d 残留过期链接口径 %r 且**未标废止** —— 该表述来自 09-27 的"
                   "『三路径一律 302』,已由 09-28/09-29 复测推翻(见 contract.json 的 "
                   "linkPolicy)。若此处是刻意引用来说明它已废止,请在该行写明"
                   "「已推翻/已废止/当时」等字样,以免读者当成现行口径。"
                   "原文:%s" % (name, lineno, bad, line.strip()[:120]))
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
    # ④ CONTEXT.md 同样必须指向唯一真源(它曾断言与真源**相反**的结论)。
    cx = open(files["CONTEXT.md"], encoding="utf-8").read()
    ok("linkPolicy" in cx,
       "CONTEXT.md 的链接口径未指向唯一真源 contract.json 的 linkPolicy —— "
       "它曾断言『linkPolicy.question 仍为 no-link』而与声明事实相反,让 agent "
       "照旧口径执行、抵消工单 #29 的对外效果")


@case("offline: 检索诊断标记不得写进答案正文(ANSWER-SPEC 措辞有钉子)")
def t_answer_spec_diagnostics_wording():
    """**ANSWER-SPEC「诊断标记」措辞的守护**(2026-09-29 补,工单 #26)。

    治的病:v6.6 引入了 `truncated` 字符串枚举(`"answer_limit"` / `"upstream_error"`),
    它是**给调用方看的诊断**,**不得**出现在给用户的答案正文里。这条口径此前只写在
    ANSWER-SPEC 的正文里、**没有任何回归守护** —— 措辞被改掉或被删掉都不会有任何信号。

    与 `t_link_policy_docs_in_sync` 同型:不检查措辞好坏,只检查**该说的那句话还在**,
    且**列全了**当前实际存在的诊断标记(漏一个就是"某个标记可以合法泄漏进正文")。

    ⚠️ 判据必须与**真实存在的标记**对齐,不能只抄文档里那一串 —— 否则将来新增标记时
    文档没跟上,本条照样绿(回声与回声自比)。
    """
    spec = os.path.join(REPO, "docs", "ANSWER-SPEC.md")
    ok(os.path.exists(spec), "ANSWER-SPEC 不存在: %s" % spec)
    txt = open(spec, encoding="utf-8").read()

    # ① 必须有一段明确说"这些是给你看的诊断,不要写进正文"。
    ok("诊断" in txt,
       "ANSWER-SPEC 未出现『诊断』这一分类 —— 检索诊断标记的边界说明被删了?")
    ok("正文" in txt,
       "ANSWER-SPEC 未出现『正文』一词 —— 无法说明诊断标记不得进入答案正文")

    # ② 当前**真实存在**的顶层/深读诊断标记,必须在文档里被点到。
    #    search 侧:keywordsDropped / routeErrors / hitRoutes;
    #    read  侧:truncated(枚举两值)。
    cp = _impl()
    real_top = set(cp.top_keys())
    must_cover = [k for k in ("keywordsDropped", "routeErrors", "hitRoutes", "truncated")
                  if k in real_top or k == "truncated"]
    ok(must_cover, "顶层声明里一个诊断标记都没有?声明可能没读到: %r" % sorted(real_top))
    missing = [k for k in must_cover if k not in txt]
    ok(not missing,
       "ANSWER-SPEC 未点到这些**实际存在**的诊断标记 %s —— 漏一个就意味着" 
       "那个标记可以合法地被写进答案正文" % missing)

    # ③ `truncated` 的两个枚举值都必须在文档里出现(否则调用方不知道哪个该重试)。
    for val in ("answer_limit", "upstream_error"):
        ok(val in txt,
           "ANSWER-SPEC 未出现 truncated 的枚举值 %r —— 该标记的语义没交代" % val)

    # ④ 反向确认扫描有效:文档里确有这句话(而不是因为整个文件为空而"通过")。
    ok(len(txt) > 1000,
       "ANSWER-SPEC 内容过短(%d 字节),本条断言可能是『扫了个空』的假绿" % len(txt))


@case("offline: 内核不得生成任何检索词(ADR-0016 禁止清单源码扫描)")
def t_no_decomposer_symbols():
    """**ADR-0016 禁止清单的钉子**(规格 §4 新增用例,v6.6)。

    内核**不得**出现"自行从问句生成检索词"的任何形态。三层扫描,任一层命中即红:

      ① **已删符号不得回来**:`_rare_token`(纯数字抢位)、`_salient_chunks`(CJK 切片)、
         `_EXEC_ORDER` / `_TRUNC_PRIORITY`(两张顺序表)、`_truncate_routes`(按重要性
         选路截断)、`symptomCategories` / `entityRules` / `stopwords`(三张词表),
         以及载体文件 `query_routes.json`;
      ② **不得出现"按重要性/优先级排序"的词汇**:它们是本轮删掉的越界形态;
      ③ **反向确认扫描有效**:若把扫描范围缩到一个真不含这些词的文件,它必须**不报**
         ——否则 ① 可能是"扫了个空"的假绿。

    为什么值得钉:这三处都是**有实测负结果**的机制(拉丁词前置把金标从第 2 挤到第 12、
    泛词拆分掉出前 30、双截断实现并存),而它们的形态看起来都像"提升召回"。
    若将来有人顺手补回来(几乎必然是以"优化搜索质量"的名义),必须立刻红。

    ⚠️ 与 `t_answer_literal_confined` 同型:扫描的**范围与判据**都明写在这里,
    并带一条"扫空即假绿"的反向确认。
    """
    banned = ("_rare_token", "_salient_chunks", "_EXEC_ORDER", "_TRUNC_PRIORITY",
              "_truncate_routes", "symptomCategories", "entityRules", "stopwords",
              "query_routes")
    offenders = []
    for root, _dirs, files in os.walk(os.path.join(SRC, "kd")):
        for fn in sorted(files):
            if not fn.endswith(".py"):
                continue
            path = os.path.join(root, fn)
            # ⚠️ 只扫**代码**(剥掉注释与字符串):模块 docstring 里逐条记着这些符号
            # 为什么被删——那是必要的留证,不该判成违规。反之,标识符/属性/字典键若
            # 出现在代码里,就是机制回来了(见 `code_only` 的论证)。
            for i, line in enumerate(code_only(path).split("\n"), 1):
                for sym in banned:
                    if sym in line:
                        offenders.append("%s:%d: %s" % (os.path.relpath(path, REPO), i,
                                                        line.strip()[:90]))
                        break
    ok(not offenders,
       "内核**代码**里出现 ADR-0016 禁止清单的形态(拆词器/顺序表/截断优先级/词表/旧配置载体)"
       "(注释与 docstring 里的历史留证不算):\n  " + "\n  ".join(offenders))
    # 已删文件不得回来。
    ok(not os.path.exists(os.path.join(SRC, "kd", "query_routes.json")),
       "query_routes.json 回来了 —— 它是拆词规则/预算的载体,已随 ADR-0016 整体删除")
    # 已删导出不得回来(观测口仍可解析 = 机制没删干净)。
    cp = _impl()
    for sym in ("_rare_token", "_salient_chunks", "_truncate_routes", "_route_sorts_type",
                "_route_cfg", "_ROUTE_CFG_PATH", "_Budget", "_BudgetExhausted",
                "_cfg_budget_search_max", "upstream_type_of"):
        ok(not hasattr(cp, sym),
           "实现包仍导出已删符号 %s —— 机制没删干净(ADR-0016)" % sym)
    # 反向确认:扫描逻辑真的能抓到东西(拿一个已知含禁用词的历史文档做阳性对照,
    # 再拿一个干净字符串做阴性对照)——证明 ① 不是"扫了个空"。
    probe = "def _f():\n    return _rare_token(x)\n"
    ok(any(s in probe for s in banned),
       "反向确认失败:构造的阳性样本未被判据识别(判据失效,① 是假绿)")
    ok(not any(s in "def _clean():\n    return plan_routes(kws)\n" for s in banned),
       "反向确认失败:干净样本被判成违规(判据过宽,会误报)")
@case("offline: 预算机制已整体删除(形参/CLI/声明/导出四面俱净)")
def t_budget_mechanism_removed():
    """**删除的回归钉子**(ADR-0016 决策 3,v6.6)。

    预算机制(`_Budget` / `_BudgetExhausted` / `_cfg_budget_search_max` /
    `KSEARCH_SEARCH_BUDGET` / 顶层 `budget_exhausted`)整体删除。理由:跨页扫描删除后
    **每路恒发 1 次请求**(实测 7 词 = 7 次),预算**永不可触发** —— 留着是一套
    "承诺了一个不存在的行为"的死机制。

    四面都要净,缺一面就留下"能传但无效"的幽灵:
      ① **库入口**:`search(..., budget=)` / `read(..., budget=)` 传即 TypeError;
      ② **CLI**:两个子命令都不再有 `--budget`(且 `kd read --budget 0` 走 argparse
         未知参数 → exit 2);
      ③ **声明**:顶层键集不含 `budget_exhausted`;
      ④ **实现包**:观测口取不到 `_Budget` / `_BudgetExhausted` / `_cfg_budget_search_max`。

    ⚠️ 本条取代的旧用例(`t_read_budget_contract`)钉的是"两个入口共用同一套预算
    归一化"——被钉的机制本身已不存在,留着它只会把"函数没了"误报成回归失败。
    但它抓出的**真实缺陷**记得留证:旧 `read(budget=3)` 会把裸 int 递进 `_Budget`
    并抛 `AttributeError`,被 CLI 归成 `internal_error`「这是 bug 而非用法问题」——
    该形态随机制删除而**不可能复现**(没有再收 int 的地方),故无需在新用例里重建断言。
    """
    import kd.cli as _cli
    for label, fn in (
            ("search(budget=5)", lambda: core.search(["甲"], budget=5)),
            ("read(budget=5)", lambda: core.read("knowledge", "1", budget=5)),
            ("read(budget=0)", lambda: core.read("knowledge", "1", budget=0))):
        try:
            fn()
        except TypeError:
            continue
        except core.InternalError:
            raise Fail("%s 抛 InternalError 而非 TypeError(形参仍被接受)" % label)
        except Exception as e:
            raise Fail("%s 抛 %s(应为 TypeError:形参已删除)" % (label, type(e).__name__))
        raise Fail("%s 未报错——budget 形参仍在签名里(机制未删净)" % label)

    # ② CLI:两个子命令都没有 --budget。
    for cmd, extra in (("search", ["--kw", "甲"]), ("read", ["6226"])):
        p = subprocess.run([PY, RUN, cmd, "--help"], cwd=REPO, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=60)
        ok("--budget" not in (p.stdout or ""),
           "kd %s --help 仍列出 --budget(预算机制已删除)" % cmd)
        code, d, err = cli(cmd, *extra, "--budget", "0")
        ok(code == 2,
           "kd %s --budget 0 应 exit 2(argparse 未知参数),实测 %r" % (cmd, code))
        ok(err and err.strip(), "kd %s --budget 的 stderr 为空:用法提示被吞掉" % cmd)

    # ③ 声明:顶层不再有 budget_exhausted。
    ok("budget_exhausted" not in SEARCH_KEYS,
       "声明顶层仍有 budget_exhausted(预算机制已整体删除): %s" % sorted(SEARCH_KEYS))
    # ④ 实现包:已删符号取不到。
    cp = _impl()
    for sym in ("_Budget", "_BudgetExhausted", "_cfg_budget_search_max"):
        ok(not hasattr(cp, sym), "实现包仍导出 %s(预算机制未删净)" % sym)
    # 环境变量通路也必须死掉(否则"预设预算"仍是一条看不见的输入)。
    # ⚠️ 只扫**代码**(剥掉注释与 docstring):模块 docstring 里记着该变量"已删除"是
    # 必要的留证,不该判成违规(见 `code_only` 的论证)。
    src_cfg = code_only(os.path.join(SRC, "kd", "_impl", "_config.py"))
    ok("KSEARCH_SEARCH_BUDGET" not in src_cfg,
       "实现体**代码**里仍读 KSEARCH_SEARCH_BUDGET —— 预算开关应随机制一并删除")
    # 全仓代码(含 CLI)都不得再引用它。
    for fn in ("cli.py", os.path.join("_impl", "_public.py"),
               os.path.join("_impl", "_manifest.py")):
        body = code_only(os.path.join(SRC, "kd", fn))
        ok("KSEARCH" not in body, "%s 代码里仍有 KSEARCH_* 引用(预算通路未删净)" % fn)



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
        # ⚠️ v6.6:数据文件**收敛为一个**(contract.json)。原校验覆盖两个
        # (query_routes.json + contract.json),前者已随拆词器与预算机制整体删除
        # (ADR-0016),故改为断言"只校验 contract.json 且不再提 query_routes.json"
        # ——后者若还留着,说明装机脚本与包内容脱节(装机必失败)。
        ok("contract.json" in src,
           "%s 的校验段未覆盖 contract.json(它是包内唯一数据文件)" % name)
        # ⚠️ 判据落在**可执行的校验语句**上,而不是"文件里有没有这个词":
        # 注释里记着"原 query_routes.json 已删除"是必要的留证。用 shell/pwsh 的
        # 校验行本身做判据(它长这样:for f in query_routes.json ... / @("a","b"))。
        # 判据:校验语句里出现的文件名集合,必须恰为 {contract.json}。
        import re as _re
        if name.endswith(".sh"):
            stmt = _re.findall(r"for f in ([^;]+);", src)
        else:
            stmt = _re.findall(r"foreach \(\$f in @\(([^)]+)\)\)", src)
        ok(stmt, "%s 里定位不到数据文件校验语句(校验被删了?)" % name)
        names = set()
        for chunk in stmt:
            for tok in _re.findall(r"[A-Za-z0-9_./\\-]+\.json", chunk):
                names.add(os.path.basename(tok))
        ok(names == {"contract.json"},
           "%s 的校验语句覆盖的数据文件应为 {contract.json}(v6.6 只剩一个),实为 %r"
           " —— 若含 query_routes.json,装机将必然失败(该文件已删除)"
           % (name, sorted(names)))
    # ② 数据文件必须**物理存在于包内目录**(随包走,装机才拷得到);
    #    且已删的那个不得回来。
    ok(os.path.exists(os.path.join(SRC, "kd", "contract.json")),
       "包内缺 contract.json —— 它不在 src/kd/ 里,装机脚本无论怎么校验都拷不到")
    ok(not os.path.exists(os.path.join(SRC, "kd", "query_routes.json")),
       "包内仍有 query_routes.json —— 已整体删除(ADR-0016)")
    # pyproject 的 package-data 也必须同步(否则 pip 安装会漏拷声明)。
    # ⚠️ 只判 **package-data 的实际声明行**(注释里的历史留证不算)。
    pj = open(os.path.join(REPO, "pyproject.toml"), encoding="utf-8").read()
    import re as _re2
    pd = _re2.search(r"\[tool\.setuptools\.package-data\](.*?)(?=\n\[|\Z)", pj, _re2.S)
    ok(pd, "pyproject.toml 里定位不到 package-data 段")
    declared_files = set(_re2.findall(r'"([^"]+\.json)"', pd.group(1)))
    ok(declared_files == {"contract.json"},
       "pyproject.toml 的 package-data 应恰为 {contract.json},实为 %r"
       "(含 query_routes.json 会让 pip 安装因缺文件报错)" % (sorted(declared_files),))
    # ③ 声明能被代码读到(不是"文件在但读不到")。读失败会静默回落兜底集,
    #    故这里断言"读到的声明非空",否则回落会被误认为同步成功。
    cp = _impl()
    ok(cp.result_keys() and cp.top_keys() and cp.default_product_id() is not None,
       "包内 contract.json 未被代码读到(声明为空 → 已静默回落兜底集)"
       "—— 装机后字段集会与开发时不同而无任何信号")
    # ④ limits 段(原 query_routes.json 的两个仍有效值)必须真被读到。
    ok(cp.max_keywords() == 7,
       "limits.maxKeywords 未从声明读到(实为 %r)—— 路数上限回落了内置兜底值" % (cp.max_keywords(),))
    ok(cp._rate_profile() == "interactive",
       "limits.rate 未从声明读到(实为 %r)" % (cp._rate_profile(),))
    # ⑤ **install.ps1 必须带 UTF-8 BOM**(v6.6 审查补,边界钉子)。
    #    治的病:本轮编辑该文件时 BOM 被静默丢掉(它有 2877 个非 ASCII 字节,
    #    全是中文注释与装机提示)。Windows PowerShell 5.1 对**无 BOM** 的 .ps1
    #    按系统 ANSI 代码页解码 —— 中文全乱码,而脚本仍会"成功执行",
    #    无任何报错。这是典型的静默回归:改动者看不见、用户看得见。
    #    判据是**首三字节**(不是"文件里有没有中文"),因为乱码的成因就是 BOM 缺失。
    with open(os.path.join(REPO, "install.ps1"), "rb") as f:
        head = f.read(3)
    ok(head == b"\xef\xbb\xbf",
       "install.ps1 缺 UTF-8 BOM(首三字节 %r)——它含大量中文字面量,"
       "PowerShell 5.1 会按 ANSI 解码导致乱码,且不报错" % (head,))


@case("offline: 上游故障分类单一来源(边界够宽且不吞程序缺陷)")
def t_upstream_failure_taxonomy():
    """**`_net.UPSTREAM_FAILURES` 的分类边界钉子**(2026-09-29 补,code review High-1)。

    治的病:深读侧原先只 `except UpstreamError`,而 `_net._get_json` 只把
    "HTTP 200 带 errorCode"包装成它 —— 真实的网络故障形态**全部穿透**,
    把已取回的回答整包丢弃并返 `internal_error`(实测 8 条 → 0 条)。

    本条钉两件**方向相反**的事,缺一即红:
      ① **够宽**:四类真实上游故障形态必须都被归入 ——
         `UpstreamError`(上游假 200)、`OSError`(URLError/超时/连接重置/SSL)、
         `json.JSONDecodeError`(响应非 JSON)、
         `http.client.HTTPException`(**读响应体阶段**的协议故障)。
         ⚠️ 第四类**不是 `OSError` 子类**(实测 `isinstance(IncompleteRead(...), OSError)`
         → False),故必须单列 —— 漏了它,"连接中途断"这种最常见的抖动形态就会穿透。
      ② **不吞程序缺陷**:`AttributeError`/`TypeError`/`KeyError`/`MemoryError`
         必须**不在**元组里 —— 它们穿透成 `internal_error`("这是 bug 而非用法问题"),
         不得伪装成"上游抖动"。若有人把元组放宽成裸 `Exception`,本条必红。
    """
    import http.client as _http_client
    import ssl
    net_mod = _net_mod()
    tax = net_mod.UPSTREAM_FAILURES

    # ① 够宽:真实上游故障形态逐一必须在列。
    must_catch = [
        ("UpstreamError(上游假 200)", core.UpstreamError(500, "shell")),
        ("urllib.error.URLError(不可达)", urllib.error.URLError("x")),
        ("urllib.error.HTTPError(4xx/5xx)", urllib.error.HTTPError("u", 500, "m", None, None)),
        ("socket.timeout(超时)", socket.timeout("t")),
        ("ConnectionResetError(连接重置)", ConnectionResetError("r")),
        ("ssl.SSLError(TLS 失败)", ssl.SSLError("s")),
        ("http.client.IncompleteRead(响应体截断)", _http_client.IncompleteRead(b"x")),
        ("http.client.BadStatusLine(状态行非法)", _http_client.BadStatusLine("bad")),
        ("json.JSONDecodeError(响应非 JSON)", json.JSONDecodeError("b", "d", 0)),
    ]
    for label, exc in must_catch:
        ok(isinstance(exc, tax),
           "① 上游故障分类漏了 %s —— 该形态会穿透并丢掉已取页(High-1 的成因)。"
           "实测 `isinstance(%s, UPSTREAM_FAILURES)` → False" % (label, type(exc).__name__))

    # ② 不吞程序缺陷:这些必须穿透。
    must_pass = [
        ("AttributeError", AttributeError("bug")),
        ("TypeError", TypeError("bug")),
        ("KeyError", KeyError("bug")),
        ("MemoryError", MemoryError()),
        ("RuntimeError", RuntimeError("bug")),
    ]
    for label, exc in must_pass:
        ok(not isinstance(exc, tax),
           "② %s 属**程序缺陷**,不得被上游故障分类吞掉 —— 否则「我们写错了」会被"
           "伪装成「上游抖了」(与本仓禁忌同型)。实测已被吞" % label)

    # ③ 分类元组必须是**具名单一来源**,且被两处 catch 共用(不是又抄两份)。
    src_detail = open(os.path.join(SRC, "kd", "_impl", "_detail.py"), encoding="utf-8").read()
    n_use = src_detail.count("_net.UPSTREAM_FAILURES")
    ok(n_use >= 2,
       "③ 深读侧只有 %d 处引用 `_net.UPSTREAM_FAILURES`(应 ≥2:页级 catch + 外层 catch)"
       "—— 少于 2 处说明又出现「某层认识这种失败、另一层不认识」的不对称" % n_use)
    ok("except UpstreamError" not in src_detail,
       "③ 深读侧仍残留只认 `UpstreamError` 的 catch —— 那会让真实网络故障穿透,"
       "正是 High-1 的原始形态")
    # ④ 元组里不得出现裸 `Exception`/`BaseException`(会吞程序缺陷)。
    ok(not any(x in (Exception, BaseException) for x in tax),
       "④ 分类元组里出现了裸 `Exception`/`BaseException`: %r —— 那会把程序缺陷"
       "一并吞成「上游故障」" % (tax,))


@case("offline: 兜底 burst 有共享消费者且两条路径恒等(无静默分叉)")
def t_fallback_burst_has_consumer_and_no_fork():
    """**`_FALLBACK_BURST` 的消费者钉子**(2026-09-29 补,交接欠账④-1)。

    治的病:该常量此前**只在一条路径上生效**,而另一条路径悄悄用别的值 ——
      声明缺失时 `profile()` 回落 `{"burst": 1, ...}`,故 `_burst()` 里的
      `or _FALLBACK_BURST` 恒不触发(实测返回 1);
      但 `_CONTRACT = {"limits": {"rate": {"interactive": {}}}}`(档位存在但为空
      dict)时 `profile()` 给回 `{}` —— 此时 `_burst()` = **7** 而 `wait()` = **1**。
    **即同一个档位 dict 在"翻页并发上界"与"限速器突发量"上得出不同答案**,
    而本仓纪律是「声明必须有消费者,否则'单一来源'是假的」;该常量当时**零用例引用**
    (对比 `_FALLBACK_MAX_KEYWORDS` 有交叉核对钉子)。

    钉两层:
      ① **恒等**:同一个档位 dict 喂给 `wait()` 与 `_burst()` 必须得出同一上界
         (扫 5 种配置形态,含"档位空 dict"这个真正会分叉的形态);
      ② **消费者真实存在**:`_burst_of` 必须被**两处**引用(限速器 + 翻页并发上界),
         否则它退回"只有定义没有消费者"的死常量。
    """
    cp = _impl()
    cfg_mod = _config_mod()
    real = cfg_mod._CONTRACT

    # ① 恒等:两路径对同一 prof 必须给出同一个数(空 dict 是历史分叉点)。
    cases = [
        ("档位空 dict(历史分叉点)", {}),
        ("档位缺 burst 键", {"rps": 5}),
        ("档位显式给 burst", {"burst": 3, "rps": 5}),
        ("档位给非法值 0", {"burst": 0, "rps": 5}),
    ]
    for label, prof in cases:
        ok(cfg_mod._burst_of(prof) == max(1, int(prof.get("burst") or cp._FALLBACK_BURST)),
           "%s: `_burst_of` 的取值不是单一来源(应与 _FALLBACK_BURST=%r 一致)"
           % (label, cp._FALLBACK_BURST))
    # 关键一条:空档位必须吃兜底值,而不是悄悄变 1。
    ok(cfg_mod._burst_of({}) == cp._FALLBACK_BURST,
       "空档位时 `_burst_of({})` 应回落到 `_FALLBACK_BURST`=%r(否则该常量在"
       "限速器那条路径上永不生效,正是历史分叉),实为 %r"
       % (cp._FALLBACK_BURST, cfg_mod._burst_of({})))

    # 真实配置态下,两条路径必须给出同一个数(端到端恒等,不只比辅助函数)。
    try:
        for label, contract in (
                ("整份声明读不到", {}),
                ("rate.interactive 为空 dict", {"limits": {"rate": {"interactive": {}}}}),
                ("正常声明", real)):
            cfg_mod._CONTRACT = contract
            pname, prof = cfg_mod._RATE.profile()
            got_two = (cfg_mod._burst(), cfg_mod._burst_of(prof))
            ok(got_two[0] == got_two[1],
               "%s 时两条路径分叉:`_burst()`=%r 而限速器用 %r —— "
               "限速器按一个数限流、翻页却按另一个数放并发,是静默的名实不符"
               % (label, got_two[0], got_two[1]))
    finally:
        cfg_mod._CONTRACT = real

    # ② 消费者真实存在:源级扫描(两处引用才叫"共享单一来源")。
    src_txt = open(os.path.join(SRC, "kd", "_impl", "_config.py"), encoding="utf-8").read()
    n_ref = src_txt.count("_burst_of(")
    ok(n_ref >= 3,
       "`_burst_of` 的引用点只有 %d 处(应 ≥3:定义 1 + 限速器 wait() 1 + `_burst()` 1)"
       "—— 引用不足说明它退回死常量" % n_ref)

    # ②b **真实适用范围**(2026-09-29 补,code review M-2):上面 ① 只证明"档位 dict 层面
    #     两路恒等",**不足以**宣称 `_FALLBACK_BURST` 在所有形态下都被消费 ——
    #     精确边界是:**整份声明读不到**时走的是 `_CONSERVATIVE_RATE`(burst=1),
    #     `_FALLBACK_BURST` 在那条路径上**零消费**。此处把两条路径的边界钉成事实,
    #     免得后人(或注释)把它写成覆盖一切的通则 —— 本仓把"自称覆盖、实际不覆盖"
    #     当作与"注释与代码相反"同级的病。
    cfg_mod._CONTRACT = {}                                # 整份声明读不到
    try:
        _pname, _prof = cfg_mod._RATE.profile()
        ok(_prof.get("burst") == 1,
           "声明整份读不到时应走**保守默认** burst=1(不知道红线 → 最保守),实为 %r"
           % (_prof.get("burst"),))
        ok(cfg_mod._burst_of(_prof) == 1,
           "该形态下 `_burst_of` 应返回保守默认 1 —— 若返回 7,说明 `_FALLBACK_BURST` "
           "被误当成覆盖一切的通则(它只覆盖'档位在但缺键'),实为 %r"
           % (cfg_mod._burst_of(_prof),))
        ok(cfg_mod._burst() == 1,
           "该形态下 `_burst()` 也应为 1(两条路径口径一致),实为 %r" % (cfg_mod._burst(),))
    finally:
        cfg_mod._CONTRACT = real
    # 而"档位在但缺 burst 键"才是 `_FALLBACK_BURST` 的适用形态(与上一条互补)。
    ok(cfg_mod._burst_of({}) == cp._FALLBACK_BURST,
       "`_FALLBACK_BURST` 的适用形态是'档位 dict 在、burst 键缺',此时应返回 %r"
       % (cp._FALLBACK_BURST,))
    # ②c **非数值 burst 不得炸主链路**(2026-09-29 补,code review L1):`int("x")` 会抛
    #     `ValueError`;若 `wait()` 那条路径不兜,一个坏声明值会**炸掉整个 search/read**,
    #     而 `_burst()` 却静默回落 —— 又是两副面孔。
    ok(cfg_mod._burst_of({"burst": "not-a-number"}) == cp._FALLBACK_BURST,
       "档位里 burst 是非数值时 `_burst_of` 应就地回落到 %r(不得抛 ValueError "
       "炸掉检索主链路),实为 %r"
       % (cp._FALLBACK_BURST, cfg_mod._burst_of({"burst": "not-a-number"})))

    # ③ 兜底值本身不能被顺手改小(它与 maxKeywords 对齐是刻意的)。
    ok(cp._FALLBACK_BURST == 7,
       "`_FALLBACK_BURST` 应为 7(与 `maxKeywords` 对齐,ADR-0017 决策 2),实为 %r"
       % (cp._FALLBACK_BURST,))


@case("offline: 红线数值被钉住(burst=7 / rps=5;ADR-0017 决策 2)")
def t_rate_limit_values():
    """**ADR-0017 决策 2 的钉子**(2026-09-29 补,工单 #27)。

    治的病:红线数值上调后**没有任何用例引用它**。全仓只断言档名
    `_rate_profile() == "interactive"` —— 于是改 `burst` / `rps` 不触及任何测试,
    **这正是它当初变成"匿名常量"的原因**。而 README 曾声明了约束却不写数字,
    `CONTEXT.md` 的契约声明条点名批评过这种"名实不符"。

    钉三层:
      ① 声明里的**具体数值**(7 / 5);
      ② 数值**真的被限速器读到**(不是只在 JSON 里躺着);
      ③ ADR-0017 的核心不变量:**3 路及以下 `delay` 恒为 0**(放宽只对 ≥4 路生效)。

    ⚠️ 第 ③ 层是本条最容易被误伤的部分:若将来有人"顺手"把 burst 调小,
    典型工作流(3 路)会开始排队而**没有任何信号**——那一层断言就是为此存在。
    """
    cp = _impl()
    lim = cp._limits()
    rate = (lim.get("rate") or {})
    inter = rate.get("interactive") or {}
    ok(inter, "limits.rate.interactive 读不到(声明缺失或段名改了): %r" % (rate,))

    # ① 具体数值(不是档名)。
    ok(inter.get("burst") == 7,
       "burst 应为 7(ADR-0017 决策 2:对齐单次操作最大请求数 maxKeywords=7),"
       "实为 %r —— 若这是有意调整,请先改 ADR-0017 再改本断言" % (inter.get("burst"),))
    ok(inter.get("rps") == 5,
       "rps 应为 5(ADR-0017 决策 2),实为 %r —— 同上,先改 ADR 再改断言"
       % (inter.get("rps"),))
    # jitterMs 未在 ADR-0017 决策 2 的调整范围内,钉住以防顺手改动。
    ok(inter.get("jitterMs") == [40, 220],
       "jitterMs 未在上调范围内(ADR-0017 决策 2 只动 burst/rps),实为 %r"
       % (inter.get("jitterMs"),))

    # ② 数值真的被限速器读到 —— 否则上面对 JSON 的断言只是"文件里写着"。
    pname, prof = cp._RATE.profile()
    ok(pname == "interactive", "限速档名应为 interactive,实为 %r" % (pname,))
    ok(prof.get("burst") == 7 and prof.get("rps") == 5,
       "限速器读到的档位与声明不一致(burst=%r rps=%r)—— 声明没进生产路径"
       % (prof.get("burst"), prof.get("rps")))

    # ③ 核心不变量:3 路及以下 delay 恒为 0(放宽只对 ≥4 路生效)。
    #    ⚠️ **本层是纯算法复刻,不实例化 `_RateLimiter`**(2026-09-29 清理):
    #    原写法 `rl = type(cp._RATE)()` 建了实例却**从不引用它**(下面的 `nxt`/`delays`
    #    是局部重算),属"伪读点" —— 与本仓批评的 M-1 同型(留一个看似被用到的对象)。
    #    ⚠️ **抓取力边界(如实声明,勿夸大)**:本层复刻的是 `wait()` 的算法文本,
    #    故它守护的是**声明数值下的这个不变量**;若有人改了 `wait()` 的实现而
    #    不改此处,本层**不会红**。真调生产 `wait()` 的那条钉子在
    #    `t_concurrency_contracts` ③(它才是防"回声与回声自比"的那一层)。
    import math
    import time as _time
    burst = int(prof["burst"])
    rps = float(prof["rps"])
    interval = 1.0 / rps
    now = _time.monotonic()
    # 复刻 `wait()` 的算法(不真 sleep):t = max(_next, now - burst*interval)
    nxt, delays = 0.0, []
    for _ in range(burst):
        t = max(nxt, now - burst * interval)
        delays.append(max(0.0, t - now))
        nxt = t + interval
    ok(all(abs(d) < 1e-9 for d in delays),
       "burst 个请求的 delay 必须全为 0(短突发允许立即通过),实测 %r" % (delays,))
    ok(burst == 7,
       "burst 必须覆盖典型工作流(3 路 search + 若干 read),实为 %r" % burst)
    ok(math.isclose(interval, 0.2, rel_tol=1e-6),
       "rps=5 对应 interval 应为 200ms,实为 %r" % (interval * 1000,))


@case("offline: 并发改造不破坏契约(路序 / 失败隔离 / 限速器线程安全)")
def t_concurrency_contracts():
    """**ADR-0017 决策 4 的前置与验收钉子**(2026-09-29 补,工单 #28)。

    路由循环由串行改并发池后,有三条契约**可能被静默破坏**,各钉一条:

      ① **路序**:清单顺序必须由"调用方给词的顺序"决定,**不得**让完成先后泄漏进去。
         实现上靠"按下标就地写回 + 最后按路号排序"。做法:让各路的**耗时与给词顺序
         相反**(第一路最慢),再断言清单顺序仍是给词顺序 —— 串行版天然通过,
         错误的并发实现(谁先回来谁先 append)必红。
      ② **失败隔离**:单路失败只进 `routeErrors`,不拖垮整轮(与串行版逐字相同)。
      ③ **`_RateLimiter` 的并发安全**:ADR-0017 决策 4 明确要求纳入回归 ——
         该实现 `wait()` 的锁只包住 `_next` 的计算与更新、sleep 在锁外,
         **从未在并发场景下被真正使用过**。钉住 `_next` 单调、无异常。
    """
    import threading
    import time as _time

    cp = _impl()
    umod = _upstream_mod_obj
    real_up = umod._search_upstream

    # ---- ① 路序:第 1 路最慢(与给词顺序相反),清单顺序仍须是给词顺序 ----
    def fake_slow_first(text, product_id, page, page_size, global_, sorts_type, rate=None):
        # 第一路睡最久 → 若实现是"谁先回来谁先排",顺序会整体倒过来。
        _time.sleep(0.03 if text == "甲" else 0.001)
        return {"content": [{"entity-type": "Knowledge", "knowledgeId": "id-" + text,
                             "title": "标题-" + text}],
                "totalElements": 1, "totalPages": 1}

    umod._search_upstream = fake_slow_first
    try:
        r = core.search(keywords=["甲", "乙", "丙"], product_id=93)
    finally:
        umod._search_upstream = real_up
    got = [it["title"] for it in r["results"]]
    ok(got == ["标题-甲", "标题-乙", "标题-丙"],
       "并发后清单顺序被完成先后污染 —— 契约是『按调用方给词的顺序』(ADR-0016 决策 2):"
       "应为 ['标题-甲','标题-乙','标题-丙'],实为 %r" % (got,))

    # ---- ② 失败隔离:中间一路炸,另外两路照常出条目 ----
    def fake_mid_fail(text, product_id, page, page_size, global_, sorts_type, rate=None):
        if text == "乙":
            raise RuntimeError("boom")
        return {"content": [{"entity-type": "Knowledge", "knowledgeId": "id-" + text,
                             "title": "标题-" + text}],
                "totalElements": 1, "totalPages": 1}

    umod._search_upstream = fake_mid_fail
    try:
        r2 = core.search(keywords=["甲", "乙", "丙"], product_id=93)
    finally:
        umod._search_upstream = real_up
    titles = [it["title"] for it in r2["results"]]
    ok(titles == ["标题-甲", "标题-丙"],
       "单路失败应只丢那一路(失败隔离),实得 %r" % (titles,))
    ok(len(r2["routeErrors"]) == 1 and r2["routeErrors"][0]["route"] == 2,
       "失败路必须进 routeErrors 且路号正确(应为 2): %r" % (r2["routeErrors"],))
    ok("失败路 1 条" in r2["scanNote"],
       "scanNote 应通报失败路数(不得静默): %r" % (r2["scanNote"],))

    # ---- ③ _RateLimiter 并发安全 ----
    # 用一个**独立实例**(不污染全局 _RATE 的 _next 状态),多线程并发抢。
    #
    # ⚠️ **必须调用生产的 `rl.wait()`**(2026-09-29 修假绿):原实现**从不调用 `wait()`**,
    # 而是在 `worker()` 里**手抄了一遍临界区**(`with rl._lock: tt = max(rl._next, ...)`)——
    # 于是它验证的是副本的线程安全性,**不是生产实现**。变异已实证其零抓取力:
    # 把 `wait()` 的 `with self._lock` 整段删掉,57/57 **仍全绿**。
    # 那与本仓自己批评过的「回声与回声自比」同型:被测对象与断言对象是两份独立文本。
    #
    # ⚠️ **本用例的抓取力边界(诚实声明,勿夸大)**:断言打在**生产 `wait()`** 上,
    # 抓的是"**该实例的调用总次数被完整计入时间片**"这一不变量(总量守恒)。
    # ⚠️ 2026-09-29 独立核实后**措辞下调**:原写"每次调用各占一个时间片"**强于断言**
    # —— 固定时钟下它化简为 `84 × interval`,多调一次/少调一次相抵仍绿,
    # 故只能宣称"总量守恒 / 恰好消费 12×7 次"。实测界定(3 组对照):
    #   · 删掉 `with self._lock`(临界区裸奔)→ **测不出** —— CPython 的 GIL 使
    #     `max()`/赋值这类字节码级操作在实践中不交错,**该变异在本环境无法变红**。
    #     故本用例**不宣称**能守护"锁被删"这一形态;
    #   · `wait()` 不推进时间片(`_next = t`)→ **红**(实测差值恰为 84 个时间片 = 16.8s);
    #   · 手抄副本(原写法)→ 上述变异**全绿**,故改为真调 `wait()`。
    rl = type(cp._RATE)()
    seen, errs = [], []
    lock = threading.Lock()
    real_sleep = _time.sleep
    real_monotonic = _time.monotonic
    cfg = _config_mod()                      # 真模块:`wait()` 读的就是它的 time
    real_cfg_monotonic = cfg.time.monotonic
    N_EACH, N_THREAD = 12, 7

    # 真调 wait() 会 sleep(节流 + jitter),12×7 次会拖慢离线组 → 打桩掉 sleep,
    # **但不动临界区**:被测对象仍是生产的 `with self._lock:` 那段。
    # 时钟也打桩成固定值:这样"应消费的时间片总数"可精确算出,差异一律来自实现缺陷。
    _time.sleep = lambda *_a, **_k: None
    cfg.time.monotonic = lambda: 100.0

    def worker():
        try:
            for _ in range(N_EACH):
                rl.wait()          # ← 生产实现,不再是手抄副本
            with lock:
                seen.append(1)
        except Exception as e:
            with lock:
                errs.append("%s: %s" % (type(e).__name__, e))

    try:
        threads = [threading.Thread(target=worker) for _ in range(N_THREAD)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
    finally:
        _time.sleep = real_sleep
        cfg.time.monotonic = real_cfg_monotonic
    ok(not errs, "_RateLimiter 并发下抛异常(非线程安全): %r" % (errs[:3],))
    ok(len(seen) == N_THREAD,
       "并发线程应全部走完并记录,实得 %d(期望 %d)" % (len(seen), N_THREAD))
    # 时间片守恒:burst 次立即放行后,每次调用**恰好**推进一个 interval。
    # ⚠️ **措辞下调(2026-09-29,独立核实后校正)**:本条**不宣称**能守护
    # "每次调用各占一个时间片" —— 断言在固定时钟(_next 初值 0、now=100.0)下
    # 化简为 `84 × interval`,它约束的是**总时间片守恒**,等价于
    # "该实例恰好被 `wait()` 消费 12×7 次"。某线程多调一次、另一线程少调一次
    # 总数不变,**仍会绿**。真实的抓取力边界即此,如实写下来。
    prof = rl.profile()[1]
    interval = 1.0 / float(prof.get("rps") or 5)
    burst = max(1, int(prof.get("burst") or 7))
    expect = 100.0 - burst * interval + N_EACH * N_THREAD * interval
    ok(abs(rl._next - expect) < 1e-6,
       "_RateLimiter 的时间片**总量守恒**:固定时钟下 %d 次并发调用后 _next 应为 "
       "%.4f,实为 %.4f(差 %.4f)—— 推进量不足说明有调用没被计入"
       "(变异实证:`_next = t` 不推进即报此错)"
       % (N_EACH * N_THREAD, expect, rl._next, expect - rl._next))


@case("offline: read 深度写在声明里(limits.maxDetail=5;翻页上限已删)")
def t_read_depth_in_contract():
    """**工单 #30 的钉子**(2026-09-29)。

    治的病:`max_detail` 与 `max_answer_pages` 原先是 `_question_detail` 的两个
    默认形参,而**全仓无任何调用方传值**、也不在任何声明里 —— 两个"画上去的旋钮"。

    钉四层:
      ① 声明里有 `limits.maxDetail` 且**代码真的读它**(有消费者);
      ② 未显式传参时,**深度来自声明**而不是代码里的字面量;
      ③ 翻页上限**真的删掉了**(形参不存在 + "有多少页取多少页");
      ④ 公开签名与 CLI 参数面**不变**(不新增可用旋钮)。

    ⚠️ 第 ② 层是重点:只断言"声明里写着 5"会退化成"文件里有这行字",
    必须证明**改声明会改行为**。
    """
    cp = _impl()
    lim = cp._limits()
    ok("maxDetail" in lim,
       "limits 段缺 maxDetail —— read 的深读深度又变成代码里的魔数了")
    ok(lim.get("maxDetail") == 5,
       "limits.maxDetail 应为 5(实测 25 条帖子回答数最大 = 5),实为 %r —— "
       "若有意调整,请先改声明与 ADR 依据" % (lim.get("maxDetail"),))

    # ① 有消费者:声明进了生产取值函数。
    ok(cp.max_detail_knowledge() == 5,
       "max_detail_knowledge() 未从声明取到 5(实为 %r)—— 声明没有消费者"
       % (cp.max_detail_knowledge(),))

    # ② 改声明必须改行为(证明真的读声明,而不是恰好都等于 5)。
    import kd._impl._config as _cfg_mod
    net_mod = _net_mod()
    real_contract = _cfg_mod._CONTRACT
    real_get = net_mod._get_json

    def fake(url, rate=None):
        if "/api/answers/" in url:
            return {"description": "展开"}
        if "/answers" in url:
            return {"content": [{"id": "a%d" % i, "description": "答案 %d" % i}
                                for i in range(1, 6)], "totalPages": 1}
        return {"title": "Q", "description": "问题正文", "answers": 5}

    try:
        # 声明改成 2 → 5 条回答里只有前 2 条被展开 → 必然截断。
        _cfg_mod._CONTRACT = {"limits": {"maxDetail": 2}, "linkPolicy":
                              {"rule": {"question": "link"}}}
        net_mod._get_json = fake
        r = _detail_mod_obj._question_detail("910")
        ok(r.get("truncated") == "answer_limit",
           "把声明 maxDetail 改成 2 后,5 条回答必须只剩 2 条被展开并报截断 —— "
           "未报说明深度没读声明。实为 %r(answersTaken=%r)"
           % (r.get("truncated"), r.get("answersTaken")))
    finally:
        _cfg_mod._CONTRACT = real_contract
        net_mod._get_json = real_get

    # ③ 翻页上限真的删了:形参不存在,且有限页会被全部取回。
    import inspect
    sig = inspect.signature(_detail_mod_obj._question_detail)
    ok("max_answer_pages" not in sig.parameters,
       "max_answer_pages 形参回来了 —— 该上限实测永不可触发(10 帖全 totalPages=1),"
       "留着是画上去的旋钮: %r" % (list(sig.parameters),))

    # ④ 公开签名与 CLI 参数面不变(不新增可用旋钮)。
    rsig = inspect.signature(core.read)
    ok(list(rsig.parameters) == ["kind", "oid", "rate"],
       "core.read 公开签名必须不变(仍 kind/oid/rate),实为 %r" % (list(rsig.parameters),))
    p = subprocess.run([PY, RUN, "read", "--help"], cwd=REPO, capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=60)
    for bad in ("--max-detail", "--maxdetail", "--max-detail-pages", "--depth"):
        ok(bad not in (p.stdout or ""),
           "kd read --help 出现了 %r —— 按 ADR-0016 决策 3 删 --budget 的先例,"
           "没有调用方可用它就不该加参数" % bad)


@case("offline: entity-type 宽容层(strip/尾换行/全角/NBSP 不再静默丢弃)")
def t_entity_type_tolerance():
    """**工单 #31 的钉子**(2026-09-29)。

    治的病:`_manifest._norm` 原先只做 ASCII `.lower()`,`_norm_item` 内部**零归一**、
    直接字面量比较 —— 于是带空白/尾换行/全角的 `entity-type` 被**整条静默丢弃**:

        '  Knowledge  '  -> None(丢弃)
        'Knowledge\\n'    -> None(丢弃)
        'Ｋｎｏｗｌｅｄｇｅ'  -> None(丢弃)

    后果与 `other` 档同型:**总条数对不上而没有任何信号**(调用方只见"结果少了")。

    ⚠️ 本条**必须走完整链路**(`core.search`),不能直调 `_norm_item` ——
    后者绕过了归一那一层,正是这个缺陷长期测不出来的原因(直调用例自己传小写)。
    """
    umod = _upstream_mod_obj
    real_up = umod._search_upstream

    variants = ["  Knowledge  ", "Knowledge\n", "Ｋｎｏｗｌｅｄｇｅ",
                "Knowledge\xa0", " Knowledge\r\n ", "KNOWLEDGE"]

    def fake(text, product_id, page, page_size, global_, sorts_type, rate=None):
        return {"content": [{"entity-type": v, "knowledgeId": "k%d" % i,
                             "title": "标题%d" % i}
                            for i, v in enumerate(variants)],
                "totalElements": len(variants), "totalPages": 1}

    umod._search_upstream = fake
    try:
        r = core.search(keywords=["宽容层探针"], product_id=93)
    finally:
        umod._search_upstream = real_up

    got = len(r["results"])
    ok(got == len(variants),
       "这 %d 种 entity-type 变体**全部**应被归一接受(宽容层:NFKC + strip + lower),"
       "实得 %d 条 —— 被丢的那些是静默丢弃(调用方零信号)。漏掉的变体: %r"
       % (len(variants), got,
          [v for v in variants if v.lower().strip() != "knowledge"]))

    # 反向确认:真正不认识的值**仍须**不被当成 knowledge(宽容层不得宽到失效)。
    def fake_bad(text, product_id, page, page_size, global_, sorts_type, rate=None):
        return {"content": [{"entity-type": "Kknowledge", "knowledgeId": "kb",
                             "title": "错拼"}],
                "totalElements": 1, "totalPages": 1}

    umod._search_upstream = fake_bad
    try:
        r2 = core.search(keywords=["宽容层反向探针"], product_id=93)
    finally:
        umod._search_upstream = real_up
    ok(len(r2["results"]) == 0,
       "错拼的 'Kknowledge' 不得被容错成 knowledge(宽容层只管空白/大小写/全角,"
       "不做模糊匹配),实得 %d 条" % len(r2["results"]))


@case("offline: other 档收容罕见类型(默认隐藏 + 通报跳过数 + 真实响应 fixture)")
def t_other_tier():
    """**工单 #32 的钉子**:上游罕见 entity-type 不再静默丢弃。

    这是本轮最重要的缺陷,端到端形态(真实上游响应实测):

        搜「微课」→ total: 212 而 results 只 2 条、routeErrors: []、
                   scanNote 写"1/1 路完成"
        → 调用 LLM 会告诉用户"官方没这类资料",而库里其实有 8 条课程 + 2 篇文章

    **三处矛盾同时出现**,且**丢弃无任何信号** —— 这是本项目最忌讳的失效形态:
    **把"我们没读懂"伪装成"上游没有"**。

    ⚠️ **本条用真实上游响应 fixture**(`tests/fixtures/upstream-search-培训课程.json`,
    2026-09-29 采自 `/api/search?text=培训课程`,10 条里 8 条 `LearningCourse`)。
    这是仓库的**第一条真实响应 fixture** —— 此前回归样本全是合成的、只造
    `Answer`/`Article`/`Knowledge` 三个值,**这正是本缺陷长期未被发现的原因**。
    """
    cp = _impl()
    net_mod = _net_mod()
    real_get = net_mod._get_json

    fx = os.path.join(REPO, "tests", "fixtures", "upstream-search-培训课程.json")
    ok(os.path.exists(fx),
       "缺真实上游响应 fixture: %s —— 没有它,本缺陷会再次复现而无人发现" % fx)
    with open(fx, encoding="utf-8") as f:
        data = json.load(f)
    ets = [x.get("entity-type") for x in (data.get("content") or [])]
    ok("LearningCourse" in ets,
       "fixture 里没有 LearningCourse —— 它抓不住本缺陷(需重新采集)")
    n_course = ets.count("LearningCourse")

    net_mod._get_json = lambda url, rate=None: data
    try:
        # ① 默认:**隐藏** other 档,但**必须通报跳过了多少条**。
        r = cp.search(keywords=["培训课程"], product_id=93)
        types = {it["type"] for it in r["results"]}
        ok("other" not in types,
           "other 档默认必须**隐藏**(用户裁定:这些类型质量低,得与前三档区分开),"
           "实得类型 %r" % (sorted(types),))
        ok(r.get("otherSkipped") == n_course,
           "隐藏时必须**通报跳过了多少条**(otherSkipped)—— 否则就是用一处静默"
           "换另一处静默。本 fixture 有 %d 条 LearningCourse,实报 %r"
           % (n_course, r.get("otherSkipped")))
        ok("隐藏" in r["scanNote"] and "条" in r["scanNote"],
           "scanNote 必须有一句人读的跳过说明(『total 大而 results 小』的差额"
           "必须有解释): %r" % (r["scanNote"],))
        # ② total 与 results 的差额现在**有解释**了 —— 这正是本缺陷的原始形态。
        ok(r["total"] > len(r["results"]),
           "本 fixture 的 total 应远大于 results(否则这条用例构造不出该场景)")

        # ③ 开关打开:other 条目出现,且带 `upstreamType`(信息不丢)。
        r2 = cp.search(keywords=["培训课程"], product_id=93, include_other=True)
        oth = [it for it in r2["results"] if it["type"] == "other"]
        ok(len(oth) == n_course,
           "打开 include_other 后 %d 条 LearningCourse 应全部出现,实得 %d 条"
           % (n_course, len(oth)))
        ok(r2.get("otherSkipped") == 0,
           "开关打开时 otherSkipped 应为 0,实为 %r" % (r2.get("otherSkipped"),))
        for it in oth:
            ok(it.get("upstreamType") == "LearningCourse",
               "other 条目必须带 `upstreamType`(上游原始类型名)—— 对外只有一个 other,"
               "丢掉原值就等于把『认得出但不当一类』又变回『看不出这是什么』: %r" % (it,))
            # ④ 链接政策:other 恒不给链接(实测无可点网页形式)。
            ok(it.get("url") is None,
               "other 档必须**不给链接**(实测无任何可点的网页形式;见 contract.json "
               "的 linkPolicy 与 research/2026-09-29-额外类型链接形式复验.md): %r"
               % (it.get("url"),))
            ok(it.get("title"),
               "other 条目应有 title(至少有标题级信息,调用方才知道存在这类资料): %r"
               % (it,))
    finally:
        net_mod._get_json = real_get

    # ⑤ 三处同步:漏一处就是"用一处静默换另一处静默"。
    #    ⚠️ **2026-09-29 由"四处"改为"三处"**:原第 ④ 项断言 `_URL_OF` 必须含
    #    `other` 模板 —— 实测那个模板是**生产死键**(见 `_URL_OF` 处注释的哨兵实验),
    #    唯一读点就是这条断言本身。给死键留一条"存在性断言"等于把死键钉成契约,
    #    故本项改为**反向断言**:`_URL_OF` 必须**不含** other(表里只放恒有链接的三档)。
    ok("other" in cp.ENTITY_KINDS, "① 实现体 ENTITY_KINDS 缺 other")
    ok("other" in (cp._contract().get("search") or {}).get("resultKeysByType", {}),
       "② contract.json 的 resultKeysByType 缺 other")
    ok(cp.link_for("other") == "no-link",
       "③ linkPolicy 缺 other 或政策不对(应为 no-link),实为 %r" % (cp.link_for("other"),))
    ok("other" not in cp._URL_OF,
       "④ `_URL_OF` 不该给 other 设模板 —— other 恒 no-link,模板存在即**生产死键**"
       "(实测:哨兵替换后从未出现在输出里;删键后 results 逐字段相同)。"
       "表里只放恒有链接的三档:%r" % (sorted(cp._URL_OF),))
    # ⑥ `read(kind="other")` 的提示必须**准确**:说清"合法但无端点",
    #    而不是让调用方以为传错了 kind。
    try:
        core.read("other", "6898")
        ok(False, "read(kind='other') 不该成功 —— 它没有全文端点")
    except core.InternalError as e:
        msg = str(e)
        ok("合法" in msg or "没有" in msg and "端点" in msg,
           "read(kind='other') 的提示必须说清『合法类型但没有全文端点』,实为 %r" % msg)
        ok("bad kind" not in msg,
           "read(kind='other') 不得报 `bad kind` —— 那样会把『这一档没有端点』"
           "引向『我是不是传错了 kind』,而调用方其实是照抄清单 type 的正确用法: %r"
           % msg)
    # ⑦ 真·非法 kind 仍须报 bad kind(两个分支必须能区分)。
    try:
        core.read("nosuchkind", "1")
        ok(False, "read(真非法 kind) 应报错")
    except core.InternalError as e:
        ok("bad kind" in str(e),
           "真·非法 kind 必须报 `bad kind`(与 other 的『合法但不可读』区分开): %r" % (e,))


@case("offline: other 档去重键并入上游类型(跨类型不得互相吃掉)")
def t_other_dedupe_key():
    """**code review C1 的钉子**(2026-09-29)。

    治的病:`other` 是**跨类型收容桶** —— 它把 `LearningCourse` / `LearningPath` /
    `KnowledgeSpecial` / `LearningBroadcast` 压成同一个 `type="other"`,
    而它们的 **id 空间互不相干、形状相同**(实测课程与学习路径都用雪花串,
    课程还有 `6898` 这种小整数)。若去重键仍是 `f"{type}:{id}"`,两条不同实体会被
    折成一条 —— 端到端实测:`total: 4` 而 `results: 2`、`routeErrors: []`、无任何提示。

    ⚠️ **这正是本轮要治的原始缺陷在 `other` 档内部重演**(「把'我们没读懂'伪装成
    '上游没有'」)。原用例的 fixture 是**单一类型**(10 条里 8 条都是 LearningCourse),
    **结构上抓不到**跨类型撞车 —— 故本条单独构造多类型 + id 同形/缺失的场景。
    """
    cp = _impl()
    net_mod = _net_mod()
    real_get = net_mod._get_json

    # 4 条**不同实体**,但 id 两两相同(模拟真实 id 空间撞车)。
    items = [
        {"entity-type": "LearningPath", "id": "6898", "xId": "LearningPath-6898",
         "highlight": {"title": "学习路径-6898"}},
        {"entity-type": "LearningCourse", "id": "6898", "xId": "LearningCourse-6898",
         "highlight": {"title": "课程-6898"}, "resourceType": "video"},
        {"entity-type": "LearningBroadcast", "id": "797227405852501248",
         "xId": "LearningBroadcast-797227405852501248",
         "highlight": {"title": "直播-A"}, "url": "http://live.vhall.com/1"},
        {"entity-type": "KnowledgeSpecial", "id": "797227405852501248",
         "xId": "KnowledgeSpecial-797227405852501248",
         "highlight": {"title": "专题-A"}},
    ]
    net_mod._get_json = lambda url, rate=None: {
        "content": items, "totalElements": len(items), "totalPages": 1}
    try:
        r = cp.search(keywords=["撞车探针"], product_id=93, include_other=True)
    finally:
        net_mod._get_json = real_get

    ok(len(r["results"]) == len(items),
       "不同实体**不得**被去重键折成一条:输入 %d 条,实得 %d 条。"
       "other 档各类型 id 空间互不相干,键必须并入 `upstreamType`"
       "(否则就是『用一处静默换另一处静默』): %r"
       % (len(items), len(r["results"]),
          [(x.get("id"), x.get("upstreamType")) for x in r["results"]]))
    # 每条都保住自己的上游类型(合并若发生会"自称是首条的类型")。
    kinds_got = sorted(str(x.get("upstreamType")) for x in r["results"])
    kinds_want = sorted(x["entity-type"] for x in items)
    ok(kinds_got == kinds_want,
       "每条 other 条目必须保住**自己的** upstreamType(合并会让条目自称首条的类型):\n"
       "  得到 %r\n  期望 %r" % (kinds_got, kinds_want))

    # 反向确认:**同一**类型同一 id 仍必须去重(不得为了修 C1 而把去重废掉)。
    dup = [
        {"entity-type": "LearningCourse", "id": "6898", "xId": "LearningCourse-6898",
         "highlight": {"title": "课程"}},
        {"entity-type": "LearningCourse", "id": "6898", "xId": "LearningCourse-6898",
         "highlight": {"title": "课程"}},
    ]
    net_mod._get_json = lambda url, rate=None: {
        "content": dup, "totalElements": 2, "totalPages": 1}
    try:
        r2 = cp.search(keywords=["同类型重复探针"], product_id=93, include_other=True)
    finally:
        net_mod._get_json = real_get
    ok(len(r2["results"]) == 1,
       "同一类型的同一条实体仍必须去重(修 C1 不得把去重一起废掉):实得 %d 条"
       % len(r2["results"]))

    # 键的构造本身也要可断言(不依赖端到端路径)。
    k1 = cp._manifest_key({"type": "other", "id": "6898",
                           "upstreamType": "LearningCourse"})
    k2 = cp._manifest_key({"type": "other", "id": "6898",
                           "upstreamType": "LearningPath"})
    ok(k1 != k2,
       "other 档的去重键必须含 upstreamType(同 id 不同类型不得同键): %r vs %r"
       % (k1, k2))
    ok(cp._manifest_key({"type": "knowledge", "id": "1"}) == "knowledge:1",
       "已知三档的键形状**不得**改动(它们 id 空间本就唯一): %r"
       % (cp._manifest_key({"type": "knowledge", "id": "1"}),))


@case("offline: other 的链接政策有真实消费者(改政策即改输出)")
def t_other_link_policy_has_consumer():
    """**code review H2 的钉子**(2026-09-29)。

    治的病:`other` 的 url 原先在 `_norm_item` 里**硬编码 `None`**,于是
    `linkPolicy.other = "no-link"` **永远读不到** —— 声明既无生产者也无消费者。
    这直接违反本仓硬纪律(「**声明必须有消费者,否则'单一来源'是假的**」),
    而且正是本轮反复批评的同型形态。

    判据:把政策翻成 `link` 后,`other` 条目的 url **必须跟着出现**;
    翻回 `no-link` 后必须被抑制。两条都成立才叫"有消费者"。
    """
    cp = _impl()
    import kd._impl._config as _cfg
    # 带 url 的真实形状(LearningBroadcast 的 url 指向第三方域,实测存在)。
    item = {"entity-type": "LearningBroadcast", "id": "797227405852501248",
            "xId": "LearningBroadcast-797227405852501248",
            "highlight": {"title": "直播"}, "url": "http://live.vhall.com/542261023"}
    n = cp._norm_item(item, "learningbroadcast")
    ok(n and n.get("url"),
       "归一化必须**保留上游原值 url**(不是硬编码 None,否则政策无消费者): %r" % (n,))

    real = _cfg._CONTRACT
    try:
        # 政策 = no-link(现行):抑制。
        _cfg._CONTRACT = {"linkPolicy": {"rule": {"other": "no-link"}}}
        ok(cp._manifest_project(n, {1}).get("url") is None,
           "政策 no-link 时 other 的 url 必须被抑制")
        # 政策 = link:必须放行 —— 这一条证明"政策真的被读到"。
        _cfg._CONTRACT = {"linkPolicy": {"rule": {"other": "link"}}}
        got = cp._manifest_project(n, {1}).get("url")
        ok(got == item["url"],
           "把政策改成 link 后 other 的 url 必须跟着出现(否则声明零消费者): %r" % (got,))
    finally:
        _cfg._CONTRACT = real
    # 现行声明必须是 no-link(实测无任何可点的网页形式)。
    ok(cp.link_for("other") == "no-link",
       "other 的现行政策应为 no-link(实测无可点形式),实为 %r" % (cp.link_for("other"),))


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
    # ⚠️ 原第 4 项是 `routesDegraded`(已删键)—— 那会让本钉子变成"要求文档必须提一个
    # 已删的键",判据方向反转:真正的挑选判据被删掉时,只要留证式提及还在它也不红
    # (2026-09-29 修正,code review M7)。现改为钉**实际存在的**诊断信号。
    for probe in ("挑选判据", "读够就停", "根因引用规则", "otherSkipped"):
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


@case("offline: 文档不得残留已删参数/机制(ADR-0016 文档同步钉子)")
def t_docs_no_dead_mechanisms():
    """**v6.6 文档同步钉子**(规格 §5,ADR-0016)。

    本轮是**破坏性变更**:位置参数与 `--type`/`--max-routes`/`--budget`/`--chunk` 全部删除,
    预算机制与拆词器整体移除。而**调用方 agent 的行为完全由文档驱动**——它读 SKILL.md 决定
    怎么调 `kd`。文档里若留着已删参数,agent 就会照着传,**每一次都以 argparse 退出码 2 失败**
    (且 `kd search "整句"` 这类写法在新内核里会报错,而旧文档通篇在用)。

    这正是本项目反复吃过的亏:链接口径曾在四份文档里各写一套而互相矛盾(见 README 的实例)。
    故本轮给文档同步加一道钉子 —— **只扫"可执行示例"形态**,不扫**变更说明**:
      * 允许:`——`、`⚠️`、`已删除`/`v6.6 删`、迁移说明(它们**必须**提到旧参数);
      * 禁止:看起来像"现在可以这样用"的命令行示例与参数速查条目。

    判据是**段落级**的:一段里出现 `kd search "…"`(位置参数形态)、或出现已删开关且该段
    **不含**变更标记词,即红。

    ⚠️ **文件集必须含安装脚本与包文档**(v6.6 收尾补):初版只扫 4 份文档,于是
    `install.sh:161` 的 `kd search "信用额度控制"` 与 `install.ps1:223`、
    `__init__.py` 的 `query_routes.json` 残留全部漏网——而安装脚本末尾的"试一试"
    是**用户装机后第一眼看到的用法**,照抄必撞 argparse 退出码 2。判据的面必须覆盖
    **所有会被照抄的地方**,只扫主文档不算同步。
    """
    files = {
        "SKILL.md": os.path.join(REPO, "skills", "kingdee-knowledge", "skills",
                                 "kingdee-knowledge", "SKILL.md"),
        "README.md": os.path.join(REPO, "README.md"),
        "ANSWER-SPEC.md": os.path.join(REPO, "docs", "ANSWER-SPEC.md"),
        "CONTEXT.md": os.path.join(REPO, "CONTEXT.md"),
        "install.sh": os.path.join(REPO, "install.sh"),
        "install.ps1": os.path.join(REPO, "install.ps1"),
        "__init__.py": os.path.join(REPO, "src", "kd", "__init__.py"),
        "core.py": os.path.join(REPO, "src", "kd", "core.py"),
    }
    # 变更标记词:含这些词的行是在**说明变更**,不是在教用法。
    change_marks = ("已删", "删除", "废除", "废止", "v6.6", "ADR-0016", "迁移",
                    "不再", "旧版本", "改传", "废弃", "~~", "已退出", "不得", "别再")
    # 已删开关:出现即须有变更标记。
    dead_flags = ("--max-routes", "--budget", "--chunk", "--type ")
    # 已删文件/机制:同上(agent 见文档提它就会去找、去传、去解释)。
    dead_terms = ("query_routes.json",)
    offenders = []
    for label, path in files.items():
        ok(os.path.exists(path), "缺文档 %s(规格 §5 的同步点)" % label)
        with open(path, encoding="utf-8") as f:
            lines = f.read().split("\n")
        # ⚠️ **段落级**判据(不是行级):变更说明常写成多行——标题行带「已删除」,
        # 续行只列参数名(如 "`--type`(…)、`--max-routes`(…)、"),行级判据会把
        # 续行误判成"在教用法"。故按**空行分隔的段落**判断:段内任一行含变更标记,
        # 整段视为说明性文字而跳过。这不削弱抓取力——真正"教用法"的段落里
        # 不会出现「已删除/v6.6/迁移」这类词(那正是本轮加钉子要防的漂移)。
        for para in _paragraphs(lines):
            if any(any(m in ln for m in change_marks) for _i, ln in para):
                continue
            for i, line in para:
                # ① 位置参数形态:`kd search "…"`(新契约必须带 --kw)。
                if _re_search_pos_arg(line):
                    offenders.append("%s:%d 位置参数形态: %s" % (label, i, line.strip()[:80]))
                # ② 已删开关的"可用"写法。
                for flag in dead_flags:
                    if flag in line:
                        offenders.append("%s:%d 已删开关 %s: %s"
                                         % (label, i, flag.strip(), line.strip()[:80]))
                # ③ 已删数据文件的"仍然存在"写法。
                for term in dead_terms:
                    if term in line:
                        offenders.append("%s:%d 已删文件 %s: %s"
                                         % (label, i, term, line.strip()[:80]))
    ok(not offenders,
       "文档里残留已删参数(agent 会照抄,每次都失败)—— 若该行确实是在说明变更,"
       "请带上「已删除/v6.6/迁移」这类标记词:\n  " + "\n  ".join(offenders))

    # 反向确认:判据真的能抓到(否则上面可能扫了个空)。
    ok(_re_search_pos_arg('  kd search "整句" --product 93'),
       "反向确认失败:位置参数形态未被判据识别(判据失效)")
    ok(not _re_search_pos_arg('  kd search --kw "甲" --product 93'),
       "反向确认失败:合法写法被误判为位置参数(判据过宽)")


def _paragraphs(lines):
    """把文件按**空行**切成段落,产出 [(行号, 行内容), …] 的列表(行号 1-based)。"""
    para, buf = [], []
    for i, ln in enumerate(lines, 1):
        if ln.strip():
            buf.append((i, ln))
        elif buf:
            para.append(buf)
            buf = []
    if buf:
        para.append(buf)
    return para


def _re_search_pos_arg(line):
    """该行是否是"把参数直接跟在 search 后面"的旧写法(`kd search "…"`)。

    ⚠️ 只认 `search` 紧跟一个**引号开头**的实参;`--kw`/`--product`/`--global` 后跟值都不算。
    """
    import re as _re
    return bool(_re.search(r"kd\s+search\s+['\"]", line))


@case("offline: 声明与兜底键集一致 —— 两份独立抄本不得漂移(真交叉核对;v6.6 按类型)")
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

    修法:比对的必须是**兜底常量本身**(`_FALLBACK_COMMON_KEYS` /
    `_FALLBACK_BY_TYPE_KEYS` / `_FALLBACK_FORBIDDEN_KEYS`),而不是经过声明覆盖后
    的读取结果。另加一条**反向验证**:模拟"声明文件读不到"的场景,断言此时读到的
    正是兜底集 —— 否则"兜底"这个卖点从未被验证过。

    ⚠️ v6.6(ADR-0016 决策 6):键集从一份改为**按类型三份**,故本用例相应扩展为
    公共段 + 三个类型专属段逐段对账。
    """
    cp = _impl()
    declared_common = tuple(_CONTRACT["search"]["resultKeysCommon"])
    declared_by = {str(k): tuple(v)
                   for k, v in (_CONTRACT["search"]["resultKeysByType"] or {}).items()}
    declared_forbidden = tuple(_CONTRACT["search"]["resultForbiddenKeys"])

    # ① 真实比对:兜底常量 vs 声明(两份独立来源)。
    ok(tuple(cp._FALLBACK_COMMON_KEYS) == declared_common,
       "内置兜底公共键集与 contract.json 声明漂移:\n  兜底 %r\n  声明 %r\n"
       "▶ 兜底集是『声明文件缺失时』生效的那份,它漂移会让装了漏拷的机器行为不同。"
       % (tuple(cp._FALLBACK_COMMON_KEYS), declared_common))
    for kind in sorted(set(declared_by) | set(cp._FALLBACK_BY_TYPE_KEYS)):
        ok(tuple(cp._FALLBACK_BY_TYPE_KEYS.get(kind, ())) == declared_by.get(kind, ()),
           "内置兜底 %s 专属键集与声明漂移:\n  兜底 %r\n  声明 %r"
           % (kind, cp._FALLBACK_BY_TYPE_KEYS.get(kind), declared_by.get(kind)))
    ok(tuple(cp._FALLBACK_FORBIDDEN_KEYS) == declared_forbidden,
       "内置兜底 resultForbiddenKeys 与声明漂移:\n  兜底 %r\n  声明 %r"
       % (tuple(cp._FALLBACK_FORBIDDEN_KEYS), declared_forbidden))

    # ② 读取路径仍要正确(声明在时用声明)。
    ok(tuple(cp.result_keys()) == declared_common,
       "result_keys() 应为公共键集并与声明一致:\n  代码 %r\n  声明 %r"
       % (cp.result_keys(), declared_common))
    for kind in sorted(declared_by):
        want = declared_common + declared_by[kind]
        ok(tuple(cp.result_keys(kind)) == want,
           "result_keys(%r) 应为公共 + 专属:\n  代码 %r\n  期望 %r"
           % (kind, cp.result_keys(kind), want))
    ok(tuple(cp.result_forbidden_keys()) == declared_forbidden,
       "result_forbidden_keys() 与声明不一致")
    ok(tuple(cp.top_keys()) == tuple(_CONTRACT["search"]["topKeys"]),
       "top_keys() 与声明不一致")
    ok(cp.default_product_id() == DEFAULT_PRODUCT_ID,
       "default_product_id() 与声明不一致")
    ok(cp.max_keywords() == int(_CONTRACT["limits"]["maxKeywords"]),
       "max_keywords() 与声明 limits.maxKeywords 不一致")

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
        ok(tuple(cp.result_keys()) == tuple(cp._FALLBACK_COMMON_KEYS),
           "声明为空时 result_keys() 应回落兜底公共集:\n  实得 %r\n  兜底 %r"
           % (cp.result_keys(), tuple(cp._FALLBACK_COMMON_KEYS)))
        ok(tuple(cp.result_keys("question")) ==
           tuple(cp._FALLBACK_COMMON_KEYS) + tuple(cp._FALLBACK_BY_TYPE_KEYS["question"]),
           "声明为空时 result_keys('question') 应回落兜底集合")
        ok(tuple(cp.result_forbidden_keys()) == tuple(cp._FALLBACK_FORBIDDEN_KEYS),
           "声明为空时 result_forbidden_keys() 应回落兜底集")
        ok(cp.default_product_id() == 93,
           "声明为空时 default_product_id() 应回落 93,实为 %r" % (cp.default_product_id(),))
        ok(cp.max_keywords() == 7,
           "声明为空时 max_keywords() 应回落 7,实为 %r" % (cp.max_keywords(),))
        ok(tuple(cp.top_keys()) == tuple(cp._FALLBACK_TOP_KEYS),
           "声明为空时 top_keys() 应回落兜底顶层键集")
        # 链接政策同理:声明缺失时保守方向是"全部不给链接"(少给一个链接
        # ≠ 多给一个死链)。
        ok(cp.link_for("knowledge") == "no-link" and cp.link_for("article") == "no-link",
           "声明为空时链接政策应保守回落 no-link,实为 %r/%r"
           % (cp.link_for("knowledge"), cp.link_for("article")))
        # ⚠️ ADR-0016 决策 7(v6.6 审查补):声明读失败必须**在返回体里可见**。
        # 只回落不标注,调用方拿到的是"字段齐全、url 全空"的合法清单 ——
        # 无法区分"官方这些条目没链接"与"内核没读到声明"(后者会让全部 url 静默变 null)。
        # 这里 `_CONTRACT_OK` 已被置为"读过且失败",故必须报 False。
        _cfg_mod._CONTRACT_OK = False
        ok(cp.contract_loaded() is False,
           "声明读失败时 contract_loaded() 仍报 True —— 顶层 contractCfgLoaded 失去意义")
        ok("contractCfgLoaded" in tuple(cp.top_keys()),
           "契约来源标注未进声明 topKeys(它有消费者才有意义)")
        _cfg_mod._CONTRACT_OK = None
        _cfg_mod._CONTRACT = real
        ok(cp.contract_loaded() is True,
           "恢复声明后 contract_loaded() 应回到 True,实为 %r" % (cp.contract_loaded(),))
    finally:
        _cfg_mod._CONTRACT = real
        _cfg_mod._CONTRACT_OK = None

    # ④ 键集自洽:公共/专属/禁止三集两两不得有交集(有交集则同一键既允许又 FAIL)。
    overlap = RESULT_KEYS & RESULT_FORBIDDEN_KEYS
    ok(not overlap, "声明自相矛盾:这些键同时在允许集与禁止集: %s" % sorted(overlap))
    ok(len(declared_common) >= 5, "公共键集为空或过短,断言失去意义: %r" % (declared_common,))
    # ⑤ 兜底集必须真的非空(防"两边都空"时上面①假绿)。
    ok(len(cp._FALLBACK_COMMON_KEYS) >= 5,
       "内置兜底公共键集过短或为空,① 会失去意义: %r" % (tuple(cp._FALLBACK_COMMON_KEYS),))
    ok(cp._FALLBACK_MAX_KEYWORDS == int(_CONTRACT["limits"]["maxKeywords"]),
       "兜底 maxKeywords 与声明漂移: %r vs %r"
       % (cp._FALLBACK_MAX_KEYWORDS, _CONTRACT["limits"]["maxKeywords"]))



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
def _config_mod():
    """取 `kd._impl._config` **模块对象**(限速器 / 声明读取所在)。

    用途:`t_concurrency_contracts` ③ 要把 `wait()` 读的那个 `time` 打桩成固定时钟,
    故必须拿到真模块而不是包属性快照 —— 打桩 `sys.modules` 里的对象与
    `wait()` 内部 `time.monotonic()` 解析到的是同一个。
    """
    import sys as _sys
    return _sys.modules["kd._impl._config"]


def _net_mod():
    """取 `kd._impl._net` **模块对象**(全内核唯一的网络出口,2026-09-29 出口统一)。

    这是**唯一**能拦住全部上游请求的注入点:替换它的 `_get_json`,
    检索侧(`_upstream._search_upstream`)与深读侧(`_detail` 的 5 个调用点)
    **一并**被拦住。出口统一之前,`_upstream` / `_detail` 各持一份 import 期快照,
    替换本模块拦不住它们 —— 那正是离线组"有网就真发"的成因。

    ⚠️ **2026-09-29 更正**:原文此处写「本仓已记录 `kd._impl` 的命名空间遮蔽问题
    (见 `_upstream_mod`),统一走 `sys.modules` 可避免再次踩到」—— 那条理由**已失效**:
    唯一真遮蔽(`_detail`)已随工单 #33 项三 3.2 改名 `_detail_fn`,`_upstream`/`_detail`
    的绕道 helper 也已删除。本 helper 保留 `sys.modules` 写法**与遮蔽无关**,
    其独立理由只有一条:**它是全内核唯一网络出口**,`wait()`/`_get_json` 的注入
    必须打到**生产实现解析到的同一个对象**上;经 `sys.modules` 取值与直接 import
    等价(已实测同一对象),此处固定写法只为避免"将来某次重构把 import 形式改坏"。
    """
    import sys as _sys
    return _sys.modules["kd._impl._net"]


def _cli_mod():
    """取 `kd.cli` **模块对象**(命令面:argparse、`_guard`、`_out`)。

    为什么必须经模块对象:`t_cli_guard_op_for_read` 要**打桩 `cli._out`** 并走
    `cli.main` 真调用路径。`cli` 不在 `kd._impl` 里,`_impl()` 观测口取不到它,
    故直接经 `sys.modules` 拿 —— 它在 `import kd.cli` 时已由本模块顶部注册。
    """
    import sys as _sys
    return _sys.modules["kd.cli"]


def _patched_search(monkey_args, **kw):
    """把 `_search_upstream` 换成记录器后调 `core.search`,返回 (结果, 收到的实参表)。

    注入点:`kd._impl._upstream._search_upstream`(每路检索的唯一出口,A6)。
    ⚠️ v6.6 起不再是"替换包属性"——`_manifest._resolve()` 字符串查表已随拆词器删除,
    网络出口改为**模块级单一入口**,故替换模块属性即生效。
    `budget` / `type_` 两个形参已随机制删除,记录表里不再有它们。
    """
    mod = _upstream_mod_obj
    real = mod._search_upstream
    seen = []

    def fake(text, product_id, page, page_size, global_, sorts_type, rate=None):
        seen.append({"text": text, "product_id": product_id, "page": page,
                     "page_size": page_size, "global_": global_,
                     "sorts_type": sorts_type})
        return {"content": [], "totalElements": 0, "totalPages": 0}

    mod._search_upstream = fake
    try:
        return core.search(*monkey_args, **kw), seen
    finally:
        mod._search_upstream = real


@case("offline: keywords 唯一入口 —— 每词一路、逐字逐序、queries 回显关键词")
def t_keywords_entry():
    """`keywords` 是**唯一**入口(ADR-0016 决策 1/3,v6.6)。

    调用方在**调用层**把问题拆成关键词后传进来,内核永不含 LLM 调用、也永不自行拆词。
    契约:每词一路 → `queries` **逐字、逐序**回显;`keywords` 回显原列表;
    顶层**不再有 `text`**(位置参数删除后它无来源,契约不挂恒为 null 的坑)。
    """
    r, seen = _patched_search((), keywords=["A", "B"], product_id=93)
    ok(r["ok"] is True, "keywords 入口失败")
    ok("text" not in r,
       "顶层不应再有 `text` 键(位置参数已删,ADR-0016 决策 3):实有 %r" % (sorted(r),))
    ok(r["keywords"] == ["A", "B"], "search.keywords 应回显调用方给的关键词,实为 %r" % (r["keywords"],))
    ok(r["queries"] == ["A", "B"],
       "每个关键词应各成一路且顺序保持,实为 %r" % (r["queries"],))
    ok(r["routesPlanned"] == 2, "计划路数应为 2,实为 %r" % (r["routesPlanned"],))
    # ⚠️ `routesDegraded` 已整体删除(2026-09-29,工单 #33,用户裁定:
    # 「重复的就不要提示了,非重复的超上限的提示」)—— 故此处不再断言它。
    ok([c["text"] for c in seen] == ["A", "B"],
       "上游实际收到的检索词应与 keywords 一致,实为 %r" % ([c["text"] for c in seen],))
    ok(all(c["product_id"] == 93 for c in seen),
       "显式关键词路也必须携带 productIds(否则 --kw 会绕过 --product 造成串线): %r"
       % ([c["product_id"] for c in seen],))

    # ⚠️ **逐字逐序**是本轮的核心契约(ADR-0016 决策 1/2),别只测两个词的顺序:
    # 构造"乱序 + 含重复 + 含首尾空白"的输入,断言内核**一个字都没动**。
    messy = [" 乙 ", "甲", "乙", "丙"]
    r2, seen2 = _patched_search((), keywords=messy, product_id=93)
    # ⚠️ **2026-09-29 并发改造后改判据**(ADR-0017 决策 4):原先断言的是
    # `seen2` 的**物理调用顺序** == 调用方给词顺序。路由循环改并发池后,
    # 物理调用顺序**不再是确定的**,也不再是被承诺的东西 —— 它成了实现细节。
    # 契约(外部行为)是这两条,改为断它们:
    #   ① `queries[]` 逐字逐序等于调用方给词的顺序(在进并发池**之前**定好,恒成立);
    #   ② 实际发出的词**集合**恰好是去重后的那批(不多不少、不重复发)。
    # 「路序」的真实载体是 ① 与清单顺序(见下方专用探针),不是线程调度。
    ok(sorted(c["text"] for c in seen2) == sorted(["乙", "甲", "丙"]),
       "内核必须原样按序发送(仅 strip 首尾空白 + 去重):发出的词集合应恰为"
       "['乙','甲','丙'],实为 %r" % (sorted(c["text"] for c in seen2),))
    ok(len(seen2) == 3,
       "去重后应只发 3 路(乙 出现两次不得发两次请求),实发 %d 次" % len(seen2))
    ok(r2["queries"] == ["乙", "甲", "丙"],
       "queries 必须逐字逐序等于调用方给词的顺序(内核不得重排),实为 %r" % (r2["queries"],))
    # ⚠️ 重复词的提示已撤(用户口径:重复的**不要**提示)—— 但**去重本身保留**,
    # 见上面 `len(seen2) == 3` 的断言(不给同一 query 发两次请求)。

    # 路序即召回顺序:第一维是"你给的第几个词"。乱序输入必须按**给的顺序**归并
    # —— 这是并发改造后仍必须保住的核心契约,故用**专用探针**直接验清单顺序:
    # 让每一路回一条可区分的条目,再看清单里谁在前。
    umod = _upstream_mod_obj
    real_up = umod._search_upstream
    order_seen = []

    def fake_by_word(text, product_id, page, page_size, global_, sorts_type, rate=None):
        order_seen.append(text)
        # 同一条 id 前缀编码该词,便于断言"哪一路的条目排在前面"。
        return {"content": [{"entity-type": "Knowledge", "knowledgeId": "id-" + text,
                             "title": "标题-" + text}],
                "totalElements": 1, "totalPages": 1}

    umod._search_upstream = fake_by_word
    try:
        r3 = core.search(keywords=messy, product_id=93)
    finally:
        umod._search_upstream = real_up
    got = [it["title"] for it in r3["results"]]
    ok(got == ["标题-乙", "标题-甲", "标题-丙"],
       "清单顺序必须由**调用方给词的顺序**决定(并发不得让完成先后泄漏进清单):"
       "应为 ['标题-乙','标题-甲','标题-丙'],实为 %r" % (got,))


@case("offline: 原句路已整体废止(ADR-0009 废止;整句不再有特殊地位)")
def t_raw_route_abolished():
    """**ADR-0009 整体废止的钉子**(ADR-0016)。

    原句路当初存在的理由是"程序切分全是词搜索导致结果过宽"——即它是**对抗内核自身
    拆词缺陷的补丁**。拆词一旦移交调用方,这个补丁的对象消失。故:

      * 整句**不再有任何特殊性**:它就是调用方给的一个词,与其余词同权同序;
      * `queries[]` **恰好**等于调用方给的词列表——不允许多出"自动补入的原句路";
      * 不再有 `raw:question` 这个路种,也没有固定的 `sortsType=1` 特殊路。

    反例对照(证明"不多不少"):给 3 个词就必须恰好 3 次请求——旧实现在只给
    keywords 时也会补原句路,路数会多出来。
    """
    cp = _impl()
    r, seen = _patched_search((), keywords=["甲", "乙", "丙"], product_id=93)
    ok(r["queries"] == ["甲", "乙", "丙"],
       "queries 必须恰好等于调用方给的词(不得自动补入原句路): %r" % (r["queries"],))
    ok(len(seen) == 3,
       "给 3 个词必须恰好 3 次上游请求(旧实现补原句路会多出一路): %r"
       % ([c["text"] for c in seen],))
    # 整句作为**普通词**传入时同样只占一路,且按它所在的位置执行(不再被前置/后置)。
    r2, seen2 = _patched_search((), keywords=["整句报错串", "甲"], product_id=93)
    ok(r2["queries"] == ["整句报错串", "甲"],
       "整句就是普通一路,按调用方给的顺序: %r" % (r2["queries"],))
    # 路定义里不得再有原句路的路种(静态:源码不得出现 raw:question)。
    src_routes = open(os.path.join(SRC, "kd", "_impl", "_routes.py"), encoding="utf-8").read()
    ok("raw:question" not in src_routes,
       "_routes.py 仍出现 `raw:question` 路种——原句路未整体废止")


@case("offline: --global 透传 —— _search_upstream 收到的 global_ 与调用方一致")
def t_global_passthrough():
    """**缺陷 A 的回归钉子**(spec 第 2.3 节,真 bug)。

    旧实现里 `_search_manifest(global_=…)` 收到该参数后**从未下传**,
    `_route_search_once` 内部把 `global_` 硬编码为 `False`。后果:CLI 的
    `--global`(跨全部产品)**静默无效**——传了等于没传,且没有任何报错。

    本用例用 monkeypatch 抓住上游出口实际收到的 `global_` 实参,断言与调用方一致。
    多路下"每路都透传"才是修好的口径。
    """
    r, seen = _patched_search((), keywords=["甲", "乙"], product_id=93, global_=True)
    ok(seen, "上游未被调用,无法校验 global_ 透传")
    ok(len(seen) >= 2, "本用例需要多路输入,实为 %d 路" % len(seen))
    ok(all(c["global_"] is True for c in seen),
       "global_=True 未逐路透传到上游(缺陷 A 复发): %r" % ([c["global_"] for c in seen],))

    # 默认 False 必须透传成假值(不得被写成字符串 "false"/None)。
    _r3, seen3 = _patched_search((), keywords=["甲"], product_id=93)
    ok(all(c["global_"] is False for c in seen3),
       "默认 global_ 应为假值,实为 %r" % ([c["global_"] for c in seen3],))

    # CLI 侧同一条链:--global 必须真的绑定到 global_(parser 级断言)。
    import kd.cli as _cli
    a = _cli.build_parser().parse_args(["search", "--kw", QUERY, "--global"])
    ok(a.global_ is True, "cli --global 未绑定到 global_ 属性(实为 %r)" % (a.global_,))


@case("offline: 重复词不提示、去重仍生效(用户口径:重复的不提示)")
def t_dedupe_silent_no_prompt():
    """**工单 #33 的钉子**(2026-09-29,执行用户裁定)。

    用户原话:「**重复的就不要提示了,非重复的超上限的提示**」。

    这条**推翻了**原先 `routesDegraded` 的行为。原实现的口径是
    "塌缩是事实,必须能被调用方看见",而实测它**报得自相矛盾**:

        `A B A`(3 词 1 重复)  → 报「路数塌缩:3→2 路(调用方给了重复词)」
        `A…G A`(8 词 1 重复)  → **只字不提**

    同一原因时而报时而不报 —— 这才是真问题。用户据此裁定撤掉这一支提示,
    而 `keywordsDropped`(去重后词数 − 上限)的算法**恰好符合**该口径,保留。

    ⚠️ **去重必须仍然生效**:不得对同一 query 发两次上游请求(浪费请求数、
    且会让该词条目的 hitRoutes 虚高)。
    """
    # ① 重复词:不产生任何提示,且不发两次请求。
    r, seen = _patched_search((), keywords=["甲", "乙", "甲", "乙", "丙"], product_id=93)
    ok("routesDegraded" not in r,
       "routesDegraded 应已整体删除(用户裁定:重复的不提示;留恒 false 的键是死机制),"
       "实有 %r" % (sorted(r),))
    ok("塌缩" not in r["scanNote"],
       "重复词**不得**出现在 scanNote 里(用户口径:重复的就不要提示了): %r"
       % (r["scanNote"],))
    ok(len(seen) == 3,
       "去重必须仍生效:5 个词(2 个重复)只应发 3 次上游请求,实发 %d 次" % len(seen))
    ok(r["queries"] == ["甲", "乙", "丙"],
       "重复词去重后 queries 应保序留唯一: %r" % (r["queries"],))
    ok(r["routesPlanned"] == 5,
       "routesPlanned 口径不变(去重前、受上限截断后的计划路数),实为 %r"
       % (r["routesPlanned"],))

    # ② 反向对照:非重复的超上限**仍然要提示**(不得连带撤掉)。
    r2, seen2 = _patched_search((), keywords=["词%d" % i for i in range(1, 10)],
                                product_id=93)
    ok(r2["keywordsDropped"] == 2,
       "非重复词超上限仍须提示丢了几条(用户口径的后半句『非重复的超上限的提示』),"
       "实为 %r" % (r2["keywordsDropped"],))
    ok("上限" in r2["scanNote"] or "丢" in r2["scanNote"],
       "超上限时 scanNote 应有一句人读说明,实为 %r" % (r2["scanNote"],))
    ok("塌缩" not in r2["scanNote"],
       "截断不是塌缩,scanNote 不得出现『塌缩』: %r" % (r2["scanNote"],))

    # ③ 常规输入:既不丢词也不提示。
    r3, _s3 = _patched_search((), keywords=["甲", "乙", "丙"], product_id=93)
    ok(r3["keywordsDropped"] == 0 and "塌缩" not in r3["scanNote"],
       "常规输入不应有任何丢弃/塌缩提示: %r" % (r3["scanNote"],))


@case("offline: 顶层字段照声明拼(删声明一键 → 真实输出跟着少)")
def t_context_keys_from_declaration():
    """**`topKeys` 有消费者的钉子**(ADR-0016 决策 8 / B4,规格 §4 新增用例)。

    治的病:`topKeys` 在生产路径**零调用**(实测 `rg` 仅剩定义与导出),与 CONTEXT.md
    的纪律直接冲突 —— **「声明必须有消费者,否则'单一来源'是假的」**。声明写了而
    没人读,等于文档多抄一份:改声明不会有任何行为变化,也不会有人发现。

    本用例用**注入法**证明它真有消费者(照 T4 的三段对账模式):
      ① 基线:真实输出顶层键集 == 声明;
      ② 注入:临时把声明的 `topKeys` 删掉一个键 → **真实输出必须跟着少一个**
         (证明输出真的经过声明投影,而不是各写一份硬拼);
      ③ 反向:注入后若输出**没变**,说明声明是装饰性的(本用例必须红)。

    注入点必须改**模块全局** `_CONTRACT`(见 `t_contract_fallback_in_sync` 的论证:
    改包属性对已读取的函数无效)。
    """
    import kd._impl._config as _cfg_mod
    cp = _impl()
    real = _cfg_mod._CONTRACT

    # ① 基线。
    out = _manifest_via_full_chain([_syn("knowledge", 101, title="知识 101")])
    ok(set(out.keys()) == set(FROZEN_TOP_KEYS),
       "基线不合:真实输出顶层键集与声明不符:\n  输出 %r\n  声明 %r"
       % (sorted(out), sorted(FROZEN_TOP_KEYS)))

    # ② 注入:删掉 `scanNote` 这个顶层键。真实输出必须跟着少它。
    try:
        broken = json.loads(json.dumps(real))
        removed = "scanNote"
        ok(removed in broken["search"]["topKeys"],
           "注入自检构造失败:声明里没有 %r,无法验证删除会被抓住" % removed)
        broken["search"]["topKeys"] = [k for k in broken["search"]["topKeys"]
                                       if k != removed]
        _cfg_mod._CONTRACT = broken
        out2 = _manifest_via_full_chain([_syn("knowledge", 101, title="知识 101")])
        ok(removed not in out2,
           "从声明删掉 %r 后真实输出**仍然有它** —— 顶层字段不是照声明拼的,"
           "`topKeys` 仍是装饰性声明(ADR-0016 决策 8 未落地)。实有 %r"
           % (removed, sorted(out2)))
        ok(set(out2.keys()) == set(FROZEN_TOP_KEYS) - {removed},
           "注入后输出应恰为声明键集减 %r:\n  输出 %r" % (removed, sorted(out2)))

        # ③ 反向对照(证明 ② 不是"永远少一个键"的假绿):删**另一个**键,
        #    少的必须是那一个,而不是固定的 scanNote。
        broken2 = json.loads(json.dumps(real))
        removed2 = "routeErrors"
        broken2["search"]["topKeys"] = [k for k in broken2["search"]["topKeys"]
                                        if k != removed2]
        _cfg_mod._CONTRACT = broken2
        out3 = _manifest_via_full_chain([_syn("knowledge", 101, title="知识 101")])
        ok(removed2 not in out3 and removed in out3,
           "反向对照失败:删 %r 后应少它、而 %r 仍在。实有 %r"
           % (removed2, removed, sorted(out3)))

        # ④ **`stats` 的专属负路径**(v6.6 审查补):`stats` 是**注入在投影之前、
        #    但日志在投影之后才读**的唯一顶层键(`_public.search` 注入 + 打日志)。
        #    故它此前有一段很窄的崩溃路径:从声明删 `stats` → 日志行读
        #    `res["stats"]["upstreamCalls"]` 直接 KeyError(不是"输出少一个键",
        #    而是**抛异常**)。上面 ② ③ 只注入了 `scanNote`/`routeErrors`,它们不参与
        #    日志行,所以恰好没踩到这条路径 —— 这正是"注入样本要覆盖到边的"的教训。
        #    判据:删 `stats` 后链路仍应正常返回且输出不含 `stats` ——
        #    "声明即闸门"不得退化成"声明即崩溃"。
        broken3 = json.loads(json.dumps(real))
        ok("stats" in broken3["search"]["topKeys"],
           "注入自检构造失败:声明里没有 stats,无法验证该负路径")
        broken3["search"]["topKeys"] = [k for k in broken3["search"]["topKeys"]
                                        if k != "stats"]
        _cfg_mod._CONTRACT = broken3
        try:
            out4 = _manifest_via_full_chain([_syn("knowledge", 101, title="知识 101")])
        except Exception as e:
            raise Fail("从声明删掉 `stats` 后检索**抛异常**(%s: %s)—— `_public.search` "
                       "的日志行在投影之后读 res['stats']。声明投影的目的正是允许删键,"
                       "删键不得变成崩溃" % (type(e).__name__, e))
        ok("stats" not in out4,
           "从声明删掉 `stats` 后真实输出仍有它: %r" % (sorted(out4)))
        ok(out4.get("ok") is True,
           "删 `stats` 后返回体不再成功: %r" % (out4.get("ok"),))
    finally:
        _cfg_mod._CONTRACT = real
    # 恢复确认:注入结束后读取路径必须回到声明(否则本用例会污染后续用例)。
    ok(tuple(cp.top_keys()) == tuple(FROZEN_TOP_KEYS),
       "注入后未恢复声明: top_keys()=%r" % (cp.top_keys(),))



def _expect_upstream(effective):
    """`effectiveProductId`(调用方词汇)→ 上游实际收到的 product_id。

    唯一映射规则:`0` 与 `None` **都**表示"不带产品过滤",故上游一律省略该参数(None)。
    传 `productIds[0]=0` 会被上游当真值过滤(实测把 Knowledge 挤出前排)——
    这正是"不过滤"与"过滤到 0 号产品"的语义分界。

    ⚠️ 值域 **v6.6 起收两态**(ADR-0016 决策 3):
      * 不传 / 传 `None` → **同义**,都取声明默认(93);
      * 显式 `0` → 不过滤(上游省略 productIds)。
    故 `effectiveProductId` **不再出现 `null`**——三态时它会出现 None,调用方要与
    "默认值"再比对一次才能确认本轮到底用了哪条线。收两态后回显值恒为整数。
    """
    return None if effective in (0, None) else effective


@case("offline: effectiveProductId 恒等于调用方传入值(内核不做字面推导;v6.6 两态)")
def t_effective_product_id_passthrough():
    """**核心行为变更之一**(决策 D14,2026-09-27;v6.6 收两态)。

    旧契约:该键回显的是 `_plan_routes` **字面推导后**的值——问句里出现「苍穹」
    会被改写成 87(即使调用方没传 `--product`)。

    新契约:**内核不做任何字面产品线推导**,`_derive_product_id` 连同别名表与
    "先出现优先"裁决规则整体删除。故:

      * 该键**恒等于调用方传入值**;不传与传 `None` 同义 → 都取声明默认 93;
      * 问句里出现「苍穹」**不再**改变它——产品线由调用层(LLM)判定后显式传;
      * 显式 `0` 仍是真不过滤;显式任何值都直通;
      * **回显值不再出现 `None`**(v6.6 两态,ADR-0016 决策 3)。

    ⚠️ 为什么敢删掉字面推导(而不是"留着但默认关"):字面匹配猜的是一句话里有没有
    产品名,而这件事调用层比规则懂;判定错误等于拿完全不同的语料作答(实测同问句下
    `--product 87` 与 `93` 的 top10 **零交集**)。留一个"偶尔猜对"的隐式改写,比
    没有它更危险——它会让调用方以为自己不传也能对。

    纯离线:monkeypatch 上游出口,零网络请求。

    ⚠️ 注入点 v6.6 变更(A6):不再是"替换包属性 + `_resolve` 字符串查表",
    而是替换模块级网络出口 `_upstream._search_upstream`(`_patched_search` 用这条)。
    """
    cases = (
        ("默认(省略 --product)→ 默认值", ["信用额度控制"], {}, DEFAULT_PRODUCT_ID),
        ("问句含「苍穹」但未显式传 → **不再改写**", ["苍穹", "信用额度控制"], {},
         DEFAULT_PRODUCT_ID),
        ("显式 87 直通", ["信用额度控制"], {"product_id": 87}, 87),
        ("显式 1 直通", ["信用额度控制"], {"product_id": 1}, 1),
        ("显式 2 直通", ["信用额度控制"], {"product_id": 2}, 2),
        ("显式 0(真不过滤)", ["信用额度控制"], {"product_id": 0}, 0),
    )
    for label, kws, kw, want in cases:
        r, seen = _patched_search((), keywords=kws, **kw)
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

    # ⚠️ **两态钉子**(v6.6):显式 `None` 与"不传"必须**同义**(都是默认),
    # 且回显值不得是 `None` —— 旧三态下 None 表示"不过滤",与本轮语义直接冲突。
    r_none, seen_none = _patched_search((), keywords=["信用额度控制"], product_id=None)
    ok(r_none["effectiveProductId"] == DEFAULT_PRODUCT_ID,
       "显式 None 应与不传同义(都取默认 %r),实为 %r —— v6.6 起两态"
       % (DEFAULT_PRODUCT_ID, r_none["effectiveProductId"]))
    ok(r_none["effectiveProductId"] is not None,
       "effectiveProductId 不得出现 null(v6.6 两态:唯一特殊值是显式 0)")
    ok(all(c["product_id"] == DEFAULT_PRODUCT_ID for c in seen_none),
       "显式 None 应真的按默认线过滤(旧三态下它会退化成不过滤): %r"
       % ([c["product_id"] for c in seen_none],))
    # 区分度钉子:"不过滤(0)"与"默认"必须是两个不同的结果,否则本用例无法区分实现。
    r_zero, seen_zero = _patched_search((), keywords=["信用额度控制"], product_id=0)
    ok(r_zero["effectiveProductId"] == 0, "显式 0 应回显 0,实为 %r" % (r_zero["effectiveProductId"],))
    ok(all(c["product_id"] is None for c in seen_zero),
       "显式 0 时上游必须省略产品过滤,实收 %r" % ([c["product_id"] for c in seen_zero],))
    ok(r_zero["effectiveProductId"] != r_none["effectiveProductId"],
       "0(不过滤)与 None(默认线)必须是两个不同结果(证明语义未混同)")

    # ⚠️ 区分度钉子:第①②态的期望值**相同**(都是默认值),故必须另有一条断言
    # 证明"问句含苍穹"与"显式传 87"确实不同——否则本用例无法区分新旧实现
    # (旧实现下第②态会得 87;若本用例只断言 93,则新旧都能过)。
    _r_same, _ = _patched_search((), keywords=["苍穹", "信用额度控制"])
    _r_87, seen87 = _patched_search((), keywords=["信用额度控制"], product_id=87)
    ok(_r_same["effectiveProductId"] != _r_87["effectiveProductId"],
       "问句含「苍穹」与显式传 87 应得到不同的有效产品线(证明不再字面推导),"
       "实得 %r vs %r" % (_r_same["effectiveProductId"], _r_87["effectiveProductId"]))
    ok(all(c["product_id"] == 87 for c in seen87), "显式 87 必须真的传到上游")

    # 值域契约:整数(与 --product 同值域,可直接比对),而不是字符串/中文类目名
    # ——那正是 results[].products 不可替代的原因。
    r, _ = _patched_search((), keywords=["信用额度控制"])
    ok(not isinstance(r["effectiveProductId"], str),
       "effectiveProductId 不得是字符串(须与 --product 同值域以便直接比对)")
    ok(isinstance(r["effectiveProductId"], int),
       "effectiveProductId 应为整数,实为 %r(%r)" % (type(r["effectiveProductId"]).__name__, r["effectiveProductId"]))
    ok(r["effectiveProductId"] == DEFAULT_PRODUCT_ID,
       "不传 --product 时应为默认值 %r,实为 %r" % (DEFAULT_PRODUCT_ID, r["effectiveProductId"]))
    # keywords 入口同样直通。
    rk, seenk = _patched_search((), keywords=["A"], product_id=87)
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
      ② 声明文件里没有推导数据源(v6.6 起原 `productAliases` 所在的
         `query_routes.json` 已整体删除,故改为断言该文件不存在);
      ③ 行为上问句里的产品词不再改变 effectiveProductId(由上一用例覆盖,此处不重复)。
    """
    cp = _impl()
    ok(not hasattr(cp, "_derive_product_id"),
       "实现包里仍可解析 _derive_product_id —— 字面产品线推导应已整体删除(决策 D14)")
    # ⚠️ v6.6:原断言读 `query_routes.json` 的 `productAliases` 段。该文件已随拆词器
    # 与预算机制整体删除(ADR-0016),故改为断言**它不存在**——这比"文件在但没那个段"
    # 更强(连放推导规则的地方都没了)。
    ok(not os.path.exists(os.path.join(SRC, "kd", "query_routes.json")),
       "query_routes.json 仍存在 —— 它应随拆词器/预算机制整体删除(ADR-0016),"
       "其仍有效的两个值已并入 contract.json 的 limits 段")
    # 产品线编号表在 contract.json 里,且**只是编号表**(不得长出推导字段)。
    pid = _CONTRACT.get("productIds") or {}
    ok("aliases" not in pid and "productAliases" not in pid,
       "contract.json 的 productIds 段出现别名/推导字段(它应只是编号表): %r"
       % (sorted(pid.keys()),))
    # ⚠️ 诚实说明本条的能力边界(避免给出虚假安心):它抓的是"函数被搬回**实现包**"
    # 这一形态。若有人把同样逻辑换个名字写在别处,本用例不会红——那种情况只能靠
    # 上一用例的**行为**断言(effectiveProductId 恒等于入参)兜住。
    # 两层各覆盖一半,合起来才是完整防线;单看任何一层都不够。


@case("offline: product_id 直通(单入口;产品过滤挂在每一路上)")
def t_product_id_passthrough_both_branches():
    """`product_id` 直通必须对**唯一入口**成立,且过滤真的挂到每一路。

    ⚠️ 历史沿革(为什么这条用例还在):`_plan_routes` 曾有**两条**分支
    (keywords 显式入口 / 规则拆解入口),而 keywords 分支曾**提前 return**,
    绕过了产品线处理(缺陷 E)——"LLM 拆好词后传进来"这条最自然的用法拿到错误语料。
    当时本用例对同一组 product_id 分别走两条入口断言结果一致。

    v6.6 规则入口随拆词器整体删除(ADR-0016),只剩一条入口,故"两条分支一致"
    的前提消失;**但"每一路都带上过滤"这条实质断言必须保留**——它是缺陷 E 真正
    守住的东西(直通不等于"只是回显")。
    """
    plan = _impl()._plan_routes
    for pid in (None, 0, 1, 2, 87, 93):
        _routes, got = plan(keywords=["生产单位数量", "分母"], product_id=pid)
        ok(got == pid,
           "product_id=%r 应直通,实得 %r(内核对产品线零改动)" % (pid, got))

    # 过滤真的被挂到每一路上(直通不等于"只是回显")。
    routes, pid = plan(keywords=["生产单位数量", "分母"], product_id=87)
    ok(pid == 87, "返回值应为 87")
    ok(routes, "应至少产出一路")
    ok(all(r.get("productIds") == 87 for r in routes),
       "每一路都应携带 productIds=87(否则 --product 会在某些路上静默失效): %r"
       % ([r.get("productIds") for r in routes],))
    # 显式 0 = 真不过滤:必须**省略**该参数(挂 0 会被上游当真值过滤)。
    routes0, pid0 = plan(keywords=["生产定义"], product_id=0)
    ok(pid0 == 0, "显式 0 应直通")
    ok(all("productIds" not in r for r in routes0),
       "显式 0 时各路不得携带 productIds(0 会被上游当真值过滤): %r"
       % ([r.get("productIds") for r in routes0],))


@case("offline: 超限按顺序取前 N + 绝不静默(截断不是塌缩;缺陷 G 口径保留)")
def t_routes_degraded_no_false_positive():
    """**两条相邻但不同的事**必须各归各位(缺陷 G 的口径,v6.6 换触发条件)。

    spec 第 3 节公式:`routesDegraded` = 「`queries[]` 去重后的实际路数 < 计划路数」,
    即它报的是**塌缩**(调用方给了重复词),不是**截断**(词数超上限)。

    修前实证:截断造成的"计划 1 路、实际 1 路"却回显 `routesDegraded=true`,
    scanNote 写出"计划 1 路,去重后实际 1 路"——两个数字相同却说塌缩,自相矛盾。
    真因:代码直接沿用了 `_dedupe_routes` 的布尔值(它报的是"产出里有重复"),
    而不是按 spec 公式比较路数。

    ⚠️ v6.6 触发条件变了(`max_routes` 形参删除,截断改由声明上限触发),但口径
    **一字未改**:截断不是塌缩。故本条改用"9 个词超过上限"来构造截断场景,
    并**同时**钉住 ADR-0016 决策 4 的通报要求(丢词必须写明 `keywordsDropped`)。
    """
    # (a) 截断场景:9 个词 > 上限 7 → 取前 7,**不得**报塌缩(计划 7 = 实际 7)。
    r, seen = _patched_search((), keywords=["词%d" % i for i in range(1, 10)], product_id=93)
    ok(r["routesPlanned"] == 7,
       "9 个词应只计划 7 路(声明上限),实为 %r" % (r["routesPlanned"],))
    ok(len(r["queries"]) == 7, "超限时应只发前 7 个词,实为 %r" % (r["queries"],))
    ok(r["queries"] == ["词%d" % i for i in range(1, 8)],
       "必须按调用方给的顺序取前 N(不得重排/挑选): %r" % (r["queries"],))
    ok(r["keywordsDropped"] == 2,
       "丢词必须在返回体里写明(ADR-0016 决策 4): 应为 2,实为 %r" % (r["keywordsDropped"],))
    ok("上限" in r["scanNote"] or "丢" in r["scanNote"],
       "scanNote 应有一句人读的丢词说明,实为 %r" % (r["scanNote"],))
    # ⚠️ `routesDegraded` 已整体删除(工单 #33);此处改为钉**它不得回来**。
    ok("routesDegraded" not in r,
       "routesDegraded 应已删除(留恒 false 的键是死机制): %r" % (sorted(r),))
    ok("塌缩" not in r["scanNote"],
       "截断不是塌缩,scanNote 不得出现「塌缩」字样,实为 %r" % (r["scanNote"],))
    ok(len(seen) == 7,
       "实际上游请求数应为 7(截断既不发请求也不发多余请求),实为 %d" % len(seen))

    # (b) 真塌缩场景:同词重复(不超上限)→ 必须报塌缩,且 keywordsDropped 为 0。
    r2, _s2 = _patched_search((), keywords=["甲", "乙", "甲", "乙", "丙"], product_id=93)
    ok(r2["routesPlanned"] == 5, "去重前应计划 5 路,实为 %r" % (r2["routesPlanned"],))
    ok(len(r2["queries"]) < r2["routesPlanned"],
       "塌缩场景实际路数应少于计划: %r vs %r" % (len(r2["queries"]), r2["routesPlanned"]))
    ok("塌缩" not in r2["scanNote"],
       "重复词不再提示(用户口径),scanNote 不得出现「塌缩」: %r" % (r2["scanNote"],))
    ok(r2["keywordsDropped"] == 0,
       "未超上限时 keywordsDropped 必须为 0(不得把塌缩混报成丢词),实为 %r"
       % (r2["keywordsDropped"],))

    # (c) 反向对照:词数未超限且互不相同 → 既不塌缩也不丢词。
    r3, _s3 = _patched_search((), keywords=["甲", "乙", "丙"], product_id=93)
    ok(r3["keywordsDropped"] == 0,
       "常规输入不应丢词: dropped=%r" % (r3["keywordsDropped"],))
    ok("塌缩" not in r3["scanNote"] and "上限" not in r3["scanNote"],
       "常规输入的 scanNote 不得出现塌缩/上限字样: %r" % (r3["scanNote"],))


@case("offline: sortsType 直通每一路(0 不被 or 链吞掉;原句路特殊值已废止)")
def t_sorts_type_zero_preserved():
    """调用方的 `sorts_type` 必须**原样**到每一路,且显式的 `0` 不得被折成 `1`。

    旧写法 `int(r.get("sortsType") or sorts_type or 1)` 用 `or` 链,而 **0 是 falsy**:
    路或调用方明确给 0(相关性排序)会被静默改成 1。

    ⚠️ 性质说明(2026-09-27 实测):上游当前 sortsType=0 与 =1 **等价**(都是相关性
    排序,2 才是时间倒序),故这是**潜伏 bug** 而非已发生的错误召回。但"显式值被
    静默改写且无任何信号"本身就是契约缺陷,故修之。

    ⚠️ v6.6 变化(ADR-0016):原句路废止后**不再有"某一路自带 sortsType"这回事**
    ——所有路都吃调用方的值,故旧用例里"第 1 路固定为 1、其余才是 0"的分裂期望
    消失,期望变成**全部等于调用方给的值**。这同时意味着 `_route_sorts_type` 那套
    "路自带值优先"的取值逻辑整体删除(零消费者),`or` 链的风险点不复存在。
    """
    # ⚠️ **端到端钉子**(比纯函数断言更强):直接断言"上游实收的 sorts_type"。
    # 独立验证实测过——把调用点还原成旧写法而保留正确的取值函数,回归会**全绿**
    # 而 0 又被吞。故必须断言上游收到的东西。
    _r, seen = _patched_search((), keywords=["甲", "乙", "丙"], product_id=93, sorts_type=0)
    ok(len(seen) >= 2, "本用例需要多路输入,实为 %d 路" % len(seen))
    ok(all(c["sorts_type"] == 0 for c in seen),
       "调用方 sorts_type=0 必须原样到每一路(不得被 or 链折成 1): %r"
       % ([c["sorts_type"] for c in seen],))
    # 反向对照:默认 1 时各路都应是 1 —— 证明上一条不是"恒等于 0"的假绿。
    _r2, seen2 = _patched_search((), keywords=["甲", "乙", "丙"], product_id=93)
    ok(all(c["sorts_type"] == 1 for c in seen2),
       "默认 sorts_type 应为 1 并透传到每一路,实为 %r" % ([c["sorts_type"] for c in seen2],))
    # 第三态:2(时间倒序)也必须原样透传。只钉 0 与 1 会漏掉"实现写成只认 0"这类回退。
    _r3, seen3 = _patched_search((), keywords=["甲", "乙", "丙"], product_id=93, sorts_type=2)
    ok(seen3 and all(c["sorts_type"] == 2 for c in seen3),
       "调用方 sorts_type=2 应原样到每一路,实为 %r" % ([c["sorts_type"] for c in seen3],))
    # 已删符号的钉子:取值间接层随原句路废止一并删除。
    cp = _impl()
    ok(not hasattr(cp, "_route_sorts_type"),
       "实现包仍导出 _route_sorts_type —— 它服务的是'路自带 sortsType'这条已废止的路径")


# ================= 联网组:真实上游(默认不跑) =================
@case("online: 长度边界两侧(100 放行 / 101、120 本地拦下)", online=True)
def t_length_boundary():
    # 只走公开面 search,不引用 clamp_query 等内部名——内部件随重构收进
    # 私有命名空间/拆包,依赖内部名会把外部行为用例退化成结构断言。
    # 100 字符会真实打到上游,故本用例归联网组。
    # ⚠️ v6.6:唯一入口是 keywords 列表(位置参数删除)。
    try:
        core.search(["a" * 100])
    except core.QueryTooLong:
        raise Fail("恰好 100 字符被硬闸误判为超限(应放行)")
    except core.UpstreamError:
        pass  # 上游业务错误与"本地硬闸误判"无关:已证明未被本地拦下
    for n in (101, 120):
        try:
            core.search(["a" * n])
        except core.QueryTooLong as e:
            ok(len(e.clamped) == 100, "%d 字符的 clamped 长度应为 100" % n)
            continue
        except core.UpstreamError:
            raise Fail("%d 字符未被本地硬闸拦下而是打到上游(硬闸失效)" % n)
        raise Fail("%d 字符未触发 QueryTooLong" % n)


@case("online: search 顶层键与结果项字段集按类型(声明派生)+ 无历史残留字段", online=True)
def t_search_contract():
    """顶层与条目键集**双向**断言,键集从 contract.json 派生(决策 D13)。

    ⚠️ 本用例不再手写"16 键"这类计数:计数是**声明的影子**,声明一变计数就过期
    (活证据:d753719 删 3 个字段后测试计数没跟上)。改成"返回键集 == 声明键集",
    既不用维护数字,又能同时抓到多出与少了。
    """
    r = core.search([QUERY], product_id=93)
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
    # ⚠️ v6.6:顶层不再有 `text`;`keywords` 恒回显调用方给的列表(唯一入口)。
    ok("text" not in ks, "search 顶层仍有 `text`(位置参数已删)")
    ok(r["keywords"] == [QUERY], "search.keywords 应回显调用方给的词列表,实为 %r"
       % (r["keywords"],))
    ok(r["queries"] and r["queries"][0] == QUERY,
       "search.queries 首元素应为你给的第 1 个词,实为 %r" % (r["queries"],))
    ok(r["keywordsDropped"] == 0 and len(r["queries"]) == 1,
       "给 1 个词应发 1 路且不丢词: queries=%r dropped=%r"
       % (r["queries"], r["keywordsDropped"]))
    ok(len(r["queries"]) <= 7, "路数 %d 超过声明上限 7" % len(r["queries"]))
    ok(len(set(r["queries"])) == len(r["queries"]),
       "queries 出现重复检索词(同一请求被发两次,塌缩去重失效): %r" % (r["queries"],))
    plan = r["routesPlanned"]
    ok(isinstance(plan, int) and 1 <= plan <= 7,
       "routesPlanned 应为 1..7 的整数(计划路数),实为 %r" % (plan,))
    ok(isinstance(r["keywordsDropped"], int) and r["keywordsDropped"] >= 0,
       "keywordsDropped 应为非负整数,实为 %r" % (r["keywordsDropped"],))
    ok("routesDegraded" not in r,
       "routesDegraded 应已删除(工单 #33): %r" % (sorted(r),))
    check_subset(keys_of(r["stats"], "search.stats"), {"upstreamCalls", "elapsedMs"}, "search.stats")
    ok(KS_STATS_LEGACY.isdisjoint(keys_of(r["stats"], "search.stats")),
       "stats 出现旧 HTTP 路径字段 %r" % sorted(KS_STATS_LEGACY & keys_of(r["stats"], "search.stats")))
    ok(isinstance(r["total"], int), "search.total 应为 int")
    # total 稳定性:同查询连续两次 total 必须一致(数值稳定是硬契约)
    time.sleep(1.2)
    r2 = core.search([QUERY], product_id=93)
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
        name = "results[%d](%s)" % (i, it.get("type"))
        kk = keys_of(it, name)
        # ⚠️ v6.6:按**该条目的类型**取期望键集(公共 + 专属),不再是一份全集。
        want = result_keys_of(it.get("type"))
        # hitRoutes/routes 是内核算出的命中信息,不在声明的条目字段里。
        check_subset(kk, want | {"hitRoutes", "routes"}, name)
        check_no_extra(kk, want | {"hitRoutes", "routes"}, name)
        # (v6.6 删:此处原有的 check_no_forbidden 恒绿,理由同上。) 
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
            # ⚠️ v6.6:非问答条目**不该有** adopted 键(类型分档)——而不是"值为 None"。
            # 旧口径把缺失键填 None、还曾合成假 False(D1);现行口径是"键不出现"。
            ok("adopted" not in kk,
               "%s 是非问答条目,按类型分档不得出现 adopted 键(D1 + 决策 6),实有 %r"
               % (name, sorted(kk)))
        # comments 三类恒可达(公共键)——D2 修好后 knowledge/article 也应有真值。
        ok("comments" in kk, "%s 缺公共键 comments(D2:三类都从上游取)" % name)


@case("online: 清单内同一帖只出现一次(帖子级归并的外部证据)", online=True)
def t_manifest_post_level_dedup():
    """**帖子级归并的外部可观测证据**(ADR-0014)。

    离线用例已用合成数据证明归并逻辑;本条证明**真实上游数据**下也成立:
    上游按回答返回(同一帖子下多条回答 = 多个条目),清单里同一帖子号必须只出现一次。

    为什么必须联网验:合成数据的形状是照实测写的,若上游改了返回形态
    (例如开始按帖子返回),离线用例仍全绿而本层归并会退化为恒等映射——
    那条路径没有离线可测的信号,只能靠真实数据钉。
    """
    # ⚠️ v6.6:`type_=` 参数已删除(ADR-0016 决策 3)——类型筛选改由**调用方从
    # 清单条目的 `type` 字段自己筛**(这正是"内核不猜语义"的落地)。故本用例改为
    # 先取混排清单,再自行筛出 question 条目来验帖级归并。
    r = core.search(["信用额度"], product_id=93)
    items = [x for x in r["results"] if x.get("type") == "question"]
    ok(items, "混排清单里没有 question 条目(探测词未命中;换词而非降级断言): %r"
       % ([x.get("type") for x in r["results"]],))
    ids = [x["id"] for x in items]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    ok(not dupes,
       "清单里同一帖子号出现多次 %r —— 帖级归并未生效(条目仍是回答级)" % (dupes,))
    # 帖级信号必须真的带值(不是键在值全空):answersCount 是该帖的回答总数。
    withcount = [x for x in items if isinstance(x.get("answersCount"), int)]
    ok(withcount, "没有任何问答条目带 answersCount —— 帖级信号未透传: %r"
       % ([x.get("answersCount") for x in items][:5],))
    ok(any(x["answersCount"] >= 1 for x in withcount), "answersCount 应至少为 1")


@case("online: search 三种实体全返回(type 字段区分;问答档对外名是 question)", online=True)
def t_search_all_types():
    r = core.search([QUERY], product_id=93)
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
    # ⚠️ v6.6:`type_=` 已删除——"只要某类型"由**调用方从清单筛**表达。
    # 本用例改为验证那三种类型在真实数据里**都出现过**(各用实测能命中的探测词),
    # 并断言混排清单里三种都能被筛出来(= 调用方真的能做这件事)。
    probes = ("信用额度", "套打", "BOM")
    seen = set()
    for probe in probes:
        time.sleep(1.2)
        rr = core.search([probe], product_id=93)
        ok(rr["ok"] is True, "%r 检索失败" % probe)
        found = {x["type"] for x in rr["results"]}
        ok(found, "%r 检索零结果(探测词未命中;换词而非降级断言)" % probe)
        seen |= found
    ok(seen == {"knowledge", "question", "article"},
       "三种实体未在真实数据里全部出现(实得 %r)——调用方无法靠清单 type 字段完成筛选"
       % (sorted(seen),))
    ok("answer" not in seen,
       "混排页出现上游原始值 'answer' —— 映射漏了(对外应为 question): %r" % (sorted(seen),))


@case("online: 空结果为 ok:true + total:0 + 空数组(绝非异常)", online=True)
def t_empty_result():
    r = core.search(["zzqqxx不存在的词xyzzy777"], product_id=93)
    ks = keys_of(r, "search")
    check_subset(ks, SEARCH_KEYS, "search 顶层")
    ok(r["ok"] is True, "空结果 ok 应为 True(不是异常)")
    ok(isinstance(r["total"], int), "空结果 total 应为 int")
    ok(isinstance(r["results"], list), "results 应为 list")
    ok(r["results"] == [] if r["total"] == 0 else True, "total=0 时 results 应为空数组")


@case("online: read 契约(9 键,已摘 landing;无 refresh)", online=True)
def t_read_contract():
    r = core.search([QUERY], product_id=93)
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
    # ⚠️ v6.6:type_ 已删——由调用方从清单筛出 question 条目。
    r = core.search([QUERY], product_id=93)
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
    # ⚠️ 2026-09-29(工单 #30):翻页上限已删除,故截断现在只可能来自
    # `limits.maxDetail`(逐条详情展开只覆盖前 N 条)。若这条截断不置 truncated,
    # 调用方对该帖"还有回答没展开"零信号 —— 这正是本断言守护的东西。
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
    r = core.search([QUERY, "信用额度"], product_id=93)
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
    mod = _upstream_mod_obj
    real = mod._search_upstream
    state = {"n": 0}

    def fake(text, product_id, page, page_size, global_, sorts_type, rate=None):
        state["n"] += 1
        if state["n"] == 1:
            raise core.UpstreamError(409, "text too long(injected)")
        return real(text, product_id, page, page_size, global_, sorts_type, rate)

    # ⚠️ v6.6:路数由**调用方给的词数**决定(内核不拆词),故这里直接给 3 个词
    # 来保证"其余路"存在(单路时首路即全部,本用例无意义)。
    mod._search_upstream = fake
    try:
        r = core.search(["BOM", "分母", "变平方"], product_id=93)
    finally:
        mod._search_upstream = real
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


@case("online: 每路恒 1 次请求(跨页扫描与预算机制均已删除)", online=True)
def t_one_request_per_route():
    """**替代已删的"预算耗尽"用例**(ADR-0016 决策 3/5,v6.6)。

    旧用例断言 `budget_exhausted=true` 与"实际完成 N 路 / 计划 M 路";预算机制整体
    删除后该断言无对象。取而代之的核心事实是:**每路恒发 1 次请求**(实测 7 词 = 7 次
    = 3.33 秒),因为跨页扫描(`_MAX_SCAN_PAGES` 的 while 循环)**永不执行**
    ——它原本为 `--type` 补齐服务,而 `--type` 已删。

    为什么值得联网验:请求数是唯一能证明"没有偷偷多翻页"的外部可观测量。
    离线打桩能看到调用次数,但看不到**真实上游在第一页的返回是否够用**。
    """
    kws = ["信用额度控制", "应收单 信用", "信用额度"]
    r = core.search(kws, product_id=93)
    ok(r["ok"] is True, "多路检索失败")
    ok(r["queries"] == kws,
       "queries 必须逐字逐序等于调用方给的词(内核不生成、不重排): %r" % (r["queries"],))
    ok(r["stats"]["upstreamCalls"] == len(kws),
       "每路应恒发 1 次请求: %d 个词应 = %d 次,实为 %r"
       % (len(kws), len(kws), r["stats"]["upstreamCalls"]))
    # 第一页就够(实测):不带 type 过滤时上游第一页返回 10 条 = 每路目标数。
    ok(r["total"] > 0, "该组探测词实测必有结果(换词而非降级断言)")
    # 已删字段不得回来。
    ks = keys_of(r, "search")
    for dead in ("budget_exhausted",):
        ok(dead not in ks, "search 顶层仍返回已删除的预算字段 %r(机制已整体删除)" % dead)


@case("online: 产品线两态(both 0 与不传/None;默认线 = 93)", online=True)
def t_product_id_zero():
    """结果集层的产品线两态(v6.6,ADR-0016 决策 3)。

    两态口径:
      * **显式 `0`** = 真不过滤(上游省略 `productIds[0]`);
      * **不传 / 显式 `None`** = 同义,都取声明默认线(93)。

    断言口径分两层,**上层是确定性的、下层才碰网络**:

      **第一层(确定性,零网络)**:三种写法发出的上游 URL **逐字节相同**。
      这是两态契约的**真正载体** —— 参数层不同才算语义不同。做法:spy 掉
      `_net._get_json`(唯一网络出口),记录 URL 后中止,不发请求。

      **第二层(联网)**:结果集比对,但**必须容忍 `pageSize` 截断边界的抖动**。

    ⚠️ **为什么第二层要容差(2026-09-29 实测,已独立复现)**:上游对**近分相邻条目**
    的返回顺序不稳 —— 实测争议两条是上游第 10、11 名,`score` 差 **0.37%**
    (1390.67 vs 1385.47),而 `pageSize=10` 只取前 10,**它们争夺同一个席位**。
    用**逐字节相同的 URL** 绕过内核直连上游连测,结果自发在两者间摆动。

    **容差 2 是实测出来的,不是拍的**:同一 URL 连测 6 次,每次都返回 10 条,
    但相对首次的对称差**恒为 2**(交集恒为 9),6 次的并集是 11 条 ——
    即"两个席位在 3 条候选间轮换"。`total` 恒为 6330 不变。
    故容差取 **2**(实测上界),而不是 1(会假红,本轮实测就是这么发现选错了)。

    即:**内核零参与也会抖**。若不加容差,这条用例会以约 50% 概率假红
    (实测命中率 3/6),而它报出来的"不传与 None 不等价"是**假的** ——
    参数层已证明两者发出同一个 URL。本仓的老教训同型:把上游抖动误报成回归失败。

    ⚠️ 容差不削弱判据:真出现语义差异(比如 `None` 被当"不过滤"),两次调用会落到
    **完全不同的产品线语料**(实测 6326 vs 31788 量级),对称差远超 1 条。
    """
    # ---- 第一层:参数层等价(确定性,零网络) ----
    net_mod = _net_mod()
    real_get = net_mod._get_json
    seen = []

    def spy(url, rate=None):
        seen.append(url)
        raise RuntimeError("spy-stop")     # 不发真实请求

    try:
        for kw in ({}, {"product_id": None}, {"product_id": 93}):
            net_mod._get_json = spy
            try:
                core.search([QUERY], **kw)
            except Exception:
                pass
            finally:
                net_mod._get_json = real_get
    finally:
        net_mod._get_json = real_get

    ok(len(seen) == 3, "spy 未抓到 3 次上游 URL(实抓 %d)" % len(seen))
    ok(seen[0] == seen[1],
       "不传 与 显式 product_id=None 必须发出**逐字节相同**的上游 URL(两态契约的本体):\n"
       "  不传      %s\n  显式None  %s" % (seen[0], seen[1]))
    ok(seen[0] == seen[2],
       "显式 93 与不传必须发出逐字节相同的 URL(默认线来自声明):\n"
       "  不传   %s\n  显式93 %s" % (seen[0], seen[2]))
    # 反之:显式 0 的参数形态**必须不同** —— 否则"不过滤"这条语义没落地。
    zero_seen = []
    net_mod._get_json = lambda url, rate=None: (zero_seen.append(url), {"content": [], "totalElements": 0, "totalPages": 0})[1]
    try:
        core.search([QUERY], product_id=0)
    finally:
        net_mod._get_json = real_get
    ok(zero_seen and zero_seen[0] != seen[0],
       "显式 0(不过滤)发出的 URL 必须与默认线**不同**(它应省略 productIds[0]):\n"
       "  0      %s\n  默认线 %s" % (zero_seen[:1], seen[0]))
    ok("productIds" not in zero_seen[0],
       "显式 0 应让上游**省略** productIds 参数(传 [0] 会被当值过滤): %s" % zero_seen[0])

    # ---- 第二层:结果集(联网),容忍截断边界抖动 ----
    default = core.search([QUERY])
    none_explicit = core.search([QUERY], product_id=None)
    zero = core.search([QUERY], product_id=0)
    pid93 = core.search([QUERY], product_id=93)

    def ids(r):
        return set(x["id"] for x in r["results"])

    # 容差 2 的依据见 docstring:同一 URL 连测 6 次的对称差上界实测为 2。
    JITTER_TOL = 2

    def near(a, b, tol=JITTER_TOL):
        """集合近似相等:对称差 ≤ tol(容忍上游截断边界的席位置换)。"""
        return len(a ^ b) <= tol

    # ① 不传 与 显式 None 等价(两态的核心)。
    ok(near(ids(default), ids(none_explicit)),
       "不传与显式 product_id=None 应等价(都是默认线;允许截断边界 %d 条抖动):\n"
       % JITTER_TOL +
       "  不传=%r\n  None=%r" % (sorted(ids(default))[:5], sorted(ids(none_explicit))[:5]))
    ok(default["effectiveProductId"] == none_explicit["effectiveProductId"],
       "不传与 None 的 effectiveProductId 应相同,实为 %r vs %r"
       % (default["effectiveProductId"], none_explicit["effectiveProductId"]))
    ok(default["effectiveProductId"] is not None,
       "effectiveProductId 不得为 null(两态):实为 %r" % (default["effectiveProductId"],))
    # ③ 显式 93 与不传等价(默认线来自声明)。
    ok(near(ids(pid93), ids(default)),
       "显式 93 应与不传等价(默认线就是声明里的 93;允许截断边界 %d 条抖动)。\n"
       "  对称差=%d\n  93  =%r\n  不传=%r"
       % (JITTER_TOL, len(ids(pid93) ^ ids(default)),
          sorted(ids(pid93))[:12], sorted(ids(default))[:12]))
    # ② 显式 0 与默认线**不等价**(不过滤会带进别的产品线语料)。
    #    这里**加容差同样不成立**:真差异是整个语料级(量级 5x),故仍用严格不等。
    ok(not near(ids(zero), ids(default)),
       "显式 0(不过滤)与默认线的召回集合不应近似相同 —— 若相同说明过滤被静默丢弃:\n"
       "  0=%r\n  默认=%r" % (sorted(ids(zero))[:5], sorted(ids(default))[:5]))
    # 不过滤的候选集应更大(实测同问句下 31788 vs 6330 量级),但只做方向性断言
    # (total 随语料漂移,不钉绝对值)。
    ok(zero["total"] >= default["total"],
       "不过滤的 total 不应小于默认线: %r vs %r" % (zero["total"], default["total"]))


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
    r = core.search(["应用为禁用状态[网关]"], product_id=93)
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
    code, d, err = cli("search", "--kw", QUERY, "--product", "93")
    ok(code == 0, "kd search 退出码 %r" % code)
    ok(d is not None, "kd search stdout 不是合法 JSON")
    check_subset(keys_of(d, "cli search"), SEARCH_KEYS, "cli search")
    ok(isinstance(d["total"], int) and d["total"] > 0, "CLI total 应为正整数,实为 %r" % (d["total"],))


@case("online: kd search --kw(唯一入口)+ 清单字段 + 超限通报", online=True)
def t_cli_search_manifest():
    """CLI 侧的 `--kw` 唯一入口(ADR-0016 决策 3):每词一路、逐字逐序。

    ⚠️ v6.6:`--max-routes` 与位置参数已删除,故旧用例的"退化为单路"断言
    改为**超限通报**的端到端验证(给 9 个 `--kw`,`keywordsDropped` 必须为 2)。
    """
    code, d, _ = cli("search", "--kw", "应用为禁用状态[网关]", "--product", "93")
    ok(code == 0, "kd search 退出码 %r" % code)
    ok(d is not None, "kd search stdout 不是合法 JSON")
    check_subset(keys_of(d, "cli search"), SEARCH_KEYS, "cli search")
    ok(all("hitRoutes" in x for x in d["results"]), "CLI 清单条目应带 hitRoutes")
    ok("contentText" not in (d["results"][0] if d["results"] else {}),
       "CLI 清单条目不得含 contentText")
    # 多个 --kw:各成一路,回显与顺序逐字一致。
    time.sleep(1.2)
    code, d2, _ = cli("search", "--kw", "信用额度", "--kw", "应收单 信用", "--product", "93")
    ok(code == 0, "kd search --kw 退出码 %r" % code)
    ok(d2 is not None, "kd search --kw stdout 不是合法 JSON")
    ok(d2["keywords"] == ["信用额度", "应收单 信用"],
       "--kw 应回显为 keywords,实为 %r" % (d2["keywords"],))
    ok(d2["queries"] == ["信用额度", "应收单 信用"],
       "两个 --kw 应各成一路且保序,实为 %r" % (d2["queries"],))
    ok(d2["keywordsDropped"] == 0, "两词未超限,不应报丢词")
    # ⚠️ 超限通报的端到端验证(9 个 --kw > 上限 7)。
    time.sleep(1.2)
    nine = ["词%d" % i for i in range(1, 10)]
    argv = []
    for k in nine:
        argv += ["--kw", k]
    code, d3, _ = cli("search", *argv, "--product", "93")
    ok(code == 0, "kd search 9 个 --kw 退出码 %r" % code)
    ok(d3["keywordsDropped"] == 2,
       "9 个 --kw 应报 keywordsDropped=2(ADR-0016 决策 4),实为 %r" % (d3["keywordsDropped"],))
    ok(d3["queries"] == nine[:7],
       "超限必须按顺序取前 7 个,实为 %r" % (d3["queries"],))
    ok(len(d3["queries"]) == 7 and d3["stats"]["upstreamCalls"] == 7,
       "7 路应恰好 7 次上游请求(每路恒 1 次): %r" % (d3["stats"],))


@case("online: kd read 进程级调用", online=True)
def t_cli_read():
    code, d, _ = cli("search", "--kw", QUERY, "--product", "93")
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
