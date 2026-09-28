#!/usr/bin/env python3
"""kd._impl._public —— 公开面函数:search / read。

这两个函数是整套件对外的全部能力。它们只做三件事:校验入参、装配预算与统计、
把编排结果包装成对外契约;**检索逻辑一行都不在这里**(在 _manifest / _detail)。
"""
import time

from ._config import (ENTITY_KINDS, UPSTREAM_TEXT_MAX, VERSION, _Budget,
                      _BudgetExhausted, _cfg_budget_search_max, _up_now,
                      default_product_id, log, _rate_profile)
from ._detail import _DETAIL_KINDS, _detail
from ._errors import InternalError, UpstreamError
from ._manifest import _search_manifest
from ._net import clamp_query

# ⚠️ 签名的 `product_id` 默认值**必须**从声明派生(不能写 `93` 字面量):
# 它是"不传 `--product` 时生效"的唯一载体,而 `contract.json` 的 `productIds.default`
# 是那份声明的单一来源。写死字面量会让声明改了而签名没跟上,且**没有任何信号**
# (实测:改声明 `default` 后,`default_product_id()` 变了、签名默认值仍是旧的)。
# 用模块级常量而不是直接写 `default_product_id()`:签名默认值在函数定义时求值一次,
# 内联调用会在每次定义/重载时重新读文件(import 期契约足够,且更可预测)。
_DEFAULT_PRODUCT_ID = default_product_id()


def _as_budget(value, default, label):
    """入参 budget 的语义:None = 取该入口的默认档;显式传入(含 0)以其为准。

    ⚠️ **两个公开函数共用本函数**(2026-09-28 修)。此前只有 `search` 经它归一化,
    `read` 把自己收到的裸 int 直接递给 `_Budget`,于是同一公开面里同名参数两套契约:

        core.search(..., budget=3)              -> 正常
        core.read(..., budget=3)                -> AttributeError: 'int' object
                                                   has no attribute 'acquire'

    该异常被 `cli._guard` 归为 `internal_error`「这是 bug 而非用法问题」,把排查
    方向带偏;而 CLI 侧没有 `read --budget`,故只能由库调用触发、零测试覆盖。
    现在两个入口都经这里归一化:`budget=` 在 `search` / `read` 上同义。

    `default` 是该入口"未传时"的档,可为 callable 或裸值:
      * `search` -> `_cfg_budget_search_max`(默认 24);
      * `read`   -> `None`,语义是**不设限**(read 的请求数由帖子长度决定,
                    没有一个像样的固定档,故维持原契约)。
    `None` 作为默认值与"未设限"是同一件事,故无需第二套分支。

    budget=0 是合法入参(零上游请求),不得被 `or` 折成"未设限"——
    那会把"禁网"读成"无限",同时击穿"budget 是硬上限"的对外承诺。
    """
    bv = default() if callable(default) else default
    if value is not None:
        bv = value
    if bv is None:
        return _Budget(None)
    if isinstance(bv, bool) or not (isinstance(bv, int) or
                                    (isinstance(bv, str) and str(bv).isdigit())):
        raise InternalError("bad %s: %r(应为非负整数)" % (label, value))
    return _Budget(int(bv))


def search(text=None, keywords=None, product_id=_DEFAULT_PRODUCT_ID,
           global_=False, sorts_type=1, type_=None, max_routes=None, budget=None,
           rate=None):
    """唯一检索入口:多路拆词检索,**只出帖级标题清单**(不返回正文)。
    清单 → 挑选 → 全读,三步解耦。本函数只负责第一步:把找到的条目按标题级信息
    列出来,交给你(调用方 agent)按标题匹配度决定读哪几篇。要全文走 `read(id, kind=type)`。

    text       检索词(str)。超过上游 100 原始字符 → raise QueryTooLong(不静默截断)。
    keywords   显式关键词列表(**替代自动拆解**,每词一路)。与 text 可并用且推荐并用:
               这是给 LLM 拆词留的入口——你在调用层把问题拆成关键词后传进来,
               内核不持有模型通道。**原句路恒常存在**(2026-09-27):给了 text 由 text 充第 1 路,
               只给 keywords 时**第 1 个 keyword 充任原句路**(故它不再另占一路)。
    product_id 93=星空旗舰版(默认)/ 87=苍穹 / 1=企业版标准版 / 2=星空侧二开问答专区;
               显式 0 = 不过滤(省略参数)。**产品线由你判定后显式传入**(2026-09-27):
               内核**不做任何字面推导**,问句里出现「苍穹」也不再改写本值;
               不传即默认 93。要真正的不过滤必须显式传 0。
    global_    跨全部产品检索。透传到每一路上游请求。
    sorts_type 默认排序(0/1=相关性,2/3=时间倒序)。仅在**某路未自带 sortsType** 时生效;
               原句路固定用 1(ADR-0009)。
    type_      可选过滤 knowledge|question|article。每路都带该过滤,且每路独立跨页扫描。
               ⚠️ 问答档的对外名是 **question**(上游协议里叫 answer,映射只在 _norm_item)。
    max_routes 最多用几路(默认取 query_routes.json 的 maxRoutes=7)。**没有 --multi 开关**:
               多路是默认行为,收敛参数而不是保留两套实现。`max_routes=1` = 单路精确。
    budget     上游请求硬上限(int,含 0);默认取 KSEARCH_SEARCH_BUDGET /
               query_routes.json 的 budget.maxUpstreamPerSearch。0 是合法的"零上游请求"。
    rate       限速档名(默认 interactive)。

    返回:`results[]` 每项含 type/id/title/url/hitRoutes/routes[],
    **不含 contentText**——要全文走 `read(id, kind=type)`。
    ⚠️ `results[]` 是**帖子级**的(ADR-0014):同一帖的多条回答已合并为一条,
    回答数看 `answersCount`、"这帖有采纳答案"看 `adopted`。
    另有 routeErrors[](哪几路失败,用于区分"被上游拒绝"与"官方没这类文档")、
    budget_exhausted、routesPlanned/routesDegraded(路数塌缩)、scanNote、stats。

    **排序**:结果顺序 = (首次出现的路序号, 该路内的上游名次)。每一路都是官方综合排序
    的产物,本内核**只去重、不重排、不产生任何评分**;`hitRoutes` 是给你参考的信息字段,
    不参与排序。非法入参 raise InternalError。

    ⚠️ **清单分页已删除**(2026-09-27,决策 D10):`page`/`page_size` 形参与
    `--page`/`--size` 参数一并移除,顶层不再有 `page`/`pageSize`/`totalPages`,
    清单一次给全(上限是"路数×10 归并后")。要更多就换词重搜。

    ⚠️ `product_id` 的**值域三态**(别再踩,已实测暴露):
      * **省略该参数** → 签名默认值 = 产品线默认编号(星空旗舰版 93);
      * **显式 `None`** → "**不过滤**";这是 Python 调用方表达"我明确不要产品过滤"
        的方式(CLI 侧没有对应写法,CLI 用 `--product 0`);
      * **显式整数**(含 `0`)→ 原样直通。
    三者不是同一个值:早期把 `None` 一律折成默认 93,会让 Python 调用方**无法**表达
    "不过滤"——实测 `product_id=None` 与 `product_id=0` 的 total 从 31788 变成 6326,
    即过滤被静默加上了。**故本函数对 product_id 不做任何转换,只直通。**
    """
    if not (str(text or "").strip() or keywords):
        raise InternalError("text or keywords required: 传具体功能名/业务名词/报错词")
    if type_ and str(type_).lower() not in ENTITY_KINDS:
        raise InternalError("bad type: %s(%s)" % (type_, "|".join(ENTITY_KINDS)))
    if text:
        clamp_query(str(text), UPSTREAM_TEXT_MAX, strict=True)
    for kw in (keywords or []):
        clamp_query(str(kw), UPSTREAM_TEXT_MAX, strict=True)
    bv = _as_budget(budget, _cfg_budget_search_max, "budget")
    n0, t0 = _up_now(), time.time()
    res = _search_manifest(text=text, keywords=keywords, product_id=product_id,
                           global_=bool(global_), sorts_type=int(sorts_type), type_=type_,
                           max_routes=max_routes, budget=bv, rate=rate)
    res["stats"] = {"upstreamCalls": _up_now() - n0,
                    "elapsedMs": round((time.time() - t0) * 1000, 1)}
    log("SEARCH:", str(text or "kw×%d" % len(keywords or []))[:50],
        "| routes", len(res.get("queries") or []),
        "| total", res.get("total"), "| returned", len(res.get("results") or []),
        "| upstream", res["stats"]["upstreamCalls"])
    return res


def read(kind, oid, budget=None, rate=None):
    """按类型读全文:kind ∈ knowledge | question(问答帖全文,传帖子号) | article。

    深读结果直接返回,不写穿落地缓存(ADR-0011:内核不落盘)。
    `refresh` 形参已删除(2026-09-18):内核恒在线,该形参在原实现里恒无效。
    ⚠️ kind 一律照抄清单条目的 `type` 字段——问答档是 `question`(2026-09-27 改名),
    清单条目的 `id` 就是帖子号,直接传即可。
    budget     上游请求硬上限(int,含 0);**默认不设限**(None)。
               ⚠️ 2026-09-28 修:此前本形参只被签名承诺、实际未归一化——
               传 `budget=3` 会把裸 int 递进 `_Budget` 并抛
               `AttributeError: 'int' object has no attribute 'acquire'`。
               现在与 `search` 走同一个 `_as_budget`,两个入口同义。
    rate       限速档名(默认 interactive)。
    返回:上游全文包 + stats{upstreamCalls,elapsedMs}。kind/id 非法 raise InternalError。
    """
    kind = str(kind or "knowledge").lower()
    if kind not in _DETAIL_KINDS:
        raise InternalError("bad kind: %s(%s)" % (kind, "|".join(_DETAIL_KINDS)))
    if oid is None or str(oid).strip() == "":
        raise InternalError("id required: 传 search 结果条目的 id")
    bv = _as_budget(budget, None, "budget")
    n0, t0 = _up_now(), time.time()
    try:
        d = _detail(kind, oid, budget=bv, rate=rate)
    except _BudgetExhausted:
        # budget=0(或余量不足)时 knowledge/article 这条单请求路径无法完成。
        # ⚠️ 必须转成**公开异常类**:`_BudgetExhausted` 是内部信号(不在 core.__all__ 内),
        # 漏出去等于让调用方面对一个它无法 import、无法分类的异常。
        # 问答档不同:`_question_detail` 自己在内部吞掉它并置 truncated(有部分结果)。
        #
        # ⚠️ 用 UpstreamError 而不是 InternalError:后者被 `cli._guard` 映射成退出码 2
        # (用法错误),而"预算不足以完成这次读取"**不是用法错误**——CLI 侧
        # `--budget 0` 是合法入参,只是这次读不成。归成用法错误会误导排查方向
        # (与工单 #24 记的那类"hint 带偏"同型)。
        raise UpstreamError(0, "上游预算不足以完成本次 read:该 kind 至少需要 1 次请求"
                               "(不传 budget 即不设限)")
    d["stats"] = {"upstreamCalls": _up_now() - n0, "elapsedMs": round((time.time() - t0) * 1000, 1)}
    log("READ[%s]:" % kind, oid, "| len", len(d.get("contentText") or ""))
    return d
