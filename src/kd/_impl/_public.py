#!/usr/bin/env python3
"""kd._impl._public —— 公开面函数:search / read。

这两个函数是整套件对外的全部能力。它们只做三件事:校验入参、装配预算与统计、
把编排结果包装成对外契约;**检索逻辑一行都不在这里**(在 _manifest / _detail)。
"""
import time

from ._config import (ENTITY_KINDS, UPSTREAM_TEXT_MAX, VERSION, _Budget,
                      _cfg_budget_search_max, _up_now, log, _rate_profile)
from ._detail import _DETAIL_KINDS, _detail
from ._errors import InternalError
from ._manifest import _search_manifest
from ._net import clamp_query


def _as_budget(value, default_fn, label):
    """入参 budget 的语义:None = 取默认档;显式传入(含 0)以其为准。

    budget=0 是合法入参(零上游请求),不得被 `or` 折成"未设限"——
    那会把"禁网"读成"无限",同时击穿"budget 是硬上限"的对外承诺。
    """
    bv = default_fn() if value is None else value
    if not (isinstance(bv, int) and not isinstance(bv, bool)) and \
            not (isinstance(bv, str) and str(bv).isdigit()):
        raise InternalError("bad budget: %r(应为非负整数)" % (value,))
    return _Budget(int(bv))


def search(text=None, keywords=None, product_id=93, page=1, page_size=10,
           global_=False, sorts_type=1, type_=None, max_routes=None, budget=None,
           rate=None):
    """唯一检索入口:多路拆词检索,**只出标题清单**(不返回正文)。

    清单 → 挑选 → 全读,三步解耦。本函数只负责第一步:把找到的条目按标题级信息
    列出来,交给你(调用方 agent)按标题匹配度决定读哪几篇。要全文走 `read(id, kind=type)`。

    text       检索词(str)。超过上游 100 原始字符 → raise QueryTooLong(不静默截断)。
    keywords   显式关键词列表(**跳过自动拆解**,每词一路)。与 text 二选一或并用:
               这是给 LLM 拆词留的入口——你在调用层把问题拆成关键词后传进来,
               内核不持有模型通道。给 keywords 时 text 可为 None。
    product_id 93=星空旗舰版(默认)/ 87=苍穹 / 1=企业版标准版;显式 0 = 不过滤(省略参数)。
               问句里出现产品别名时(「苍穹」等)由拆解器推导并覆盖本默认。
    page/page_size  清单分页(**非上游分页**):每路上游固定 pageSize=10,
               多路结果去重后得到清单,再切第 page 页。
    global_    跨全部产品检索。透传到每一路上游请求。
    sorts_type 默认排序(0/1=相关性,2/3=时间倒序)。仅在**某路未自带 sortsType** 时生效;
               原句路固定用 1(ADR-0009)。
    type_      可选过滤 knowledge|answer|article。每路都带该过滤,且每路独立跨页扫描。
    max_routes 最多用几路(默认取 query_routes.json 的 maxRoutes=7)。**没有 --multi 开关**:
               多路是默认行为,收敛参数而不是保留两套实现。`max_routes=1` = 单路精确。
    budget     上游请求硬上限(int,含 0);默认取 KSEARCH_SEARCH_BUDGET /
               query_routes.json 的 budget.maxUpstreamPerSearch。0 是合法的"零上游请求"。
    rate       限速档名(默认 interactive)。

    返回:`results[]` 每项含 type/id/title/url/hitRoutes/routes[],
    **不含 contentText**——要全文走 `read(id, kind=type)`。
    另有 routeErrors[](哪几路失败,用于区分"被上游拒绝"与"官方没这类文档")、
    budget_exhausted、routesPlanned/routesDegraded(路数塌缩)、scanNote、stats。

    **排序**:结果顺序 = (首次出现的路序号, 该路内的上游名次)。每一路都是官方综合排序
    的产物,本内核**只去重、不重排、不产生任何评分**;`hitRoutes` 是给你参考的信息字段,
    不参与排序。非法入参 raise InternalError。
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
                           page=int(page), page_size=int(page_size), global_=bool(global_),
                           sorts_type=int(sorts_type), type_=type_, max_routes=max_routes,
                           budget=bv, rate=rate)
    res["stats"] = {"upstreamCalls": _up_now() - n0,
                    "elapsedMs": round((time.time() - t0) * 1000, 1)}
    log("SEARCH:", str(text or "kw×%d" % len(keywords or []))[:50],
        "| routes", len(res.get("queries") or []),
        "| total", res.get("total"), "| returned", len(res.get("results") or []),
        "| upstream", res["stats"]["upstreamCalls"])
    return res


def read(kind, oid, budget=None, rate=None):
    """按类型读全文:kind ∈ knowledge | answer(问答帖全文,传 questionId) | article。

    深读结果直接返回,不写穿落地缓存(ADR-0011:内核不落盘)。
    `refresh` 形参已删除(2026-09-18):内核恒在线,该形参在原实现里恒无效。
    返回:上游全文包 + stats{upstreamCalls,elapsedMs}。kind/id 非法 raise InternalError。
    """
    kind = str(kind or "knowledge").lower()
    if kind not in _DETAIL_KINDS:
        raise InternalError("bad kind: %s(%s)" % (kind, "|".join(_DETAIL_KINDS)))
    if oid is None or str(oid).strip() == "":
        raise InternalError("id required: 传 search 结果条目的 id")
    n0, t0 = _up_now(), time.time()
    d = _detail(kind, oid, budget=budget, rate=rate)
    d["stats"] = {"upstreamCalls": _up_now() - n0, "elapsedMs": round((time.time() - t0) * 1000, 1)}
    log("READ[%s]:" % kind, oid, "| len", len(d.get("contentText") or ""))
    return d
