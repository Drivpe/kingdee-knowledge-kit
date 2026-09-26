#!/usr/bin/env python3
"""kd._impl._manifest —— 多路清单执行链(唯一检索路径)。

**排序契约(2026-09-18 定案,不得重开)**:本内核**不产生任何排序分**。

理由:每一次上游检索都由**官方综合排序**排好了,本内核拿到的每一路都是有序的。
多路的价值是"用不同关键词命中不同文档",不是"给文档打分"。故内核只做两件事:

  1. **去重**——同一条文档被多路命中时只留一条;
  2. **保持稳定顺序**——按(首次出现的路序号, 该路内的上游名次)。

两者的输入都不是算出来的:路序来自 `_plan_routes` 的路定义顺序(原句路恒为第 1 路),
路内名次直接是上游返回的下标。**没有加权、没有分数、没有时间衰减、没有 views 权重**,
也没有"命中路数排序"——那同样是算法评分。

`hitRoutes` / `routes[]` 仍然返回,但**降级为纯信息字段**:它们告诉调用方(LLM)
"这条被哪几路搜到了",供它自己判断该读哪篇,**不参与排序**。
"""
from ._config import _BudgetExhausted, cfg_max_routes, log
from ._errors import InternalError, UpstreamError
from ._routes import _dedupe_routes, _plan_routes
from ._upstream import _norm_item

# 每条路向上游翻页的上限(type_ 过滤时用于补齐)。
_MAX_SCAN_PAGES = 5


def _resolve(name):
    """把内部件名解析到**本包命名空间**(= 观测口 kd.core._impl() 返回的那个对象)。

    为什么不直接用 `from ._upstream import _search_upstream` 的局部绑定:
    测试与自检经 `kd.core._impl()`(即本包)替换内部件来注入故障——例如让第 1 路
    抛 UpstreamError,断言"失败路被记录且其余路照常返回"。局部绑定是 import 期的
    快照,替换包属性对它无效;而**拆分前那种替换是生效的**(全实现同处一个命名空间)。
    拆分不得改变这条可观测行为,故跨模块的上游出口一律经本函数解析。
    """
    import sys
    return getattr(sys.modules[__package__], name)


def _route_search_once(text, product_id, type_, budget, rate, global_=False, sorts_type=1,
                       page_size=10, want=10, max_scan_pages=_MAX_SCAN_PAGES):
    """单路检索:每路按 pageSize=10 向上游取,直到凑够 `want` 条或触到扫描上限。

    返回 (items, total, pages_scanned)。上游错误原样上传,由编排层分档处理
    (单路失败不拖垮整轮)。

    `want` 之所以可能大于 page_size:清单是"每路 10 条去重"的产物,总长度最坏
    只有 路数×10。若清单分页要第 2 页(page*page_size > 10),每路 1 页根本凑不出
    那一条清单——切出来会是空页。故按 `want = max(10, page*page_size)` 向上游多翻页,
    **只在调用方确实要深页时才多花请求**,浅页(默认 page=1)行为不变。

    type_ 每路都带:它是调用方的显式意图("我只要 knowledge"),不是路由策略。
    只在某一路扫会造成"同一意图下不同路召回能力不同"——本轮要消除的不可解释行为。
    代价是 type_+多路时最坏 routes×max_scan_pages 次请求,由预算硬卡。

    global_ 必须由调用方透传:它是"跨全部产品"的开关,**不是**路由策略。
    此前本函数把 global_ 硬编码为 False,导致 CLI 的 --global 静默失效(2026-09-18 修)。
    """
    items, seen = [], set()
    total, pages_scanned = 0, 0
    search_upstream = _resolve("_search_upstream")

    def collect(dd):
        for x in dd.get("content") or []:
            et = (x.get("entity-type") or "").lower()
            if type_ and et != str(type_).lower():
                continue
            key = (et, str(x.get("id") or ""))
            if key in seen:
                continue
            seen.add(key)
            n = _norm_item(x, et)
            if n:
                items.append(n)

    first = search_upstream(text, product_id, 1, page_size, global_, sorts_type, type_,
                            budget, rate)
    pages_scanned += 1
    total = max(total, first.get("totalElements") or 0)
    total_pages = first.get("totalPages")
    collect(first)

    pg = 2
    # 扫描补齐:type_ 过滤把混排结果里的目标类型抽出来;循环有 max_scan_pages 上限,
    # 但**每路独立计数**——路间不共享进度。
    while pg <= max_scan_pages and pg <= (total_pages or 1):
        if len(items) >= want:
            break
        dd = search_upstream(text, product_id, pg, page_size, global_, sorts_type, type_,
                             budget, rate)
        pages_scanned += 1
        collect(dd)
        pg += 1
    return items, total, pages_scanned


def _manifest_key(n):
    """多路清单的去重键:`f"{type}:{id}"`。

    为什么 answer 用回答 id 而不是 questionId:清单的单位是**条目**,而回答本身
    就是独立条目——同一个问题帖可以有采纳回答 + 多条普通回答,它们语义不同
    (采纳的是解、普通的是旁证),必须在清单里各自成条、可分别筛。`read("answer", …)`
    只认 questionId 是**读取路径**的约束(帖子级详情),与**列表路径**的条目粒度
    本来就该分开;两者混用一个键,会让同一帖的多条回答在清单里被静默合并成一条
    (丢掉其余回答)——这正是上游 answer 同时带 `id` 与 `questionId` 两个 id 空间
    所暴露的口径问题。questionId 仅进展示字段。
    """
    return "%s:%s" % (n.get("type") or "?", n.get("id") or "")


def _manifest_rank(route_hits, route_index):
    """多路清单排序键:**(首次出现的路序号, 该路内的上游名次)**。

    纯函数,入参是构造数据,便于离线断言(不打上游):
      route_hits   {key: {路序号,…}} —— 该条目被哪几路命中(**仅供展示/参考**)
      route_index  {key: (首次出现的路序号, 该路内名次)} —— 排序的唯一来源

    为什么第一维不是"命中路数":命中路数是一个**由本内核算出来的量**,
    按它排序等于本内核重新排序,而每一路本来就是官方综合排序的结果——
    那是在官方排序之上再叠一层我们自己的权重,与"让 LLM 按标题匹配度自己挑"
    的目标相反。用户 2026-09-18 明确:不使用算法排分,只去重。

    为什么 `route_hits` 仍是参数:签名保留以便调用方显式表达"这两个量是分开的",
    并作为回归钉子——**任何把 route_hits 用进排序键的改动都会让离线用例变红**。
    """
    def key(k):
        first_route, first_pos = route_index.get(k, (99, 99))
        return (first_route, first_pos)
    return key


def _manifest_fuse(route_lists):
    """多路结果 → 去重后的清单条目(带 hitRoutes / routes)。

    route_lists 是 [(路序号, [规范化条目, …]), …],顺序即路的执行顺序。
    返回 (entries, route_hits, first_seen):
      entries    按 (首次路序号, 路内名次) 排列的清单键
      route_hits {去重键: {路序号,…}} —— 纯信息,不参与排序
      first_seen {去重键: (首次路序号, 路内名次)} —— 排序载体
    """
    route_hits, first_seen, order = {}, {}, []
    for route_no, items in route_lists:
        for pos, n in enumerate(items, 1):
            if not n:
                continue
            k = _manifest_key(n)
            if k not in route_hits:
                route_hits[k] = set()
                first_seen[k] = (route_no, pos)
                order.append(k)
            route_hits[k].add(route_no)
    keys = sorted(order, key=_manifest_rank(route_hits, first_seen))
    return keys, route_hits, first_seen


def _manifest_project(n, route_nos):
    """清单条目的对外形态:只给标题级信息,不返回 contentText。

    调用方要全文走 `read(id, kind=type)`——"筛选"与"深读"两步解耦、各自可重试。
    没有 contentText 就不存在"替调用方决定读哪篇"这件事。

    除标题级信息外,透传上游的**原生信号**(采纳标记/回答数/评论数/点赞数/问题正文):
    它们是上游直接给的,不是本内核算的,正是调用方按标题匹配度挑选时需要的旁证。
    """
    return {"type": n.get("type"), "id": n.get("id"), "title": n.get("title"),
            "url": n.get("url"),
            "hitRoutes": len(route_nos), "routes": sorted(route_nos),
            "questionId": n.get("questionId"),
            "snippet": n.get("snippet"), "products": n.get("products") or [],
            "views": n.get("views"), "updatedAt": n.get("updatedAt"),
            # 上游原生信号(answer/article 专有,按存在透传,不写死键)
            "adopted": n.get("adopted"),
            "answersCount": n.get("answersCount"),
            "comments": n.get("comments"),
            "supports": n.get("supports"),
            "questionBody": n.get("questionBody")}


def _search_manifest(text=None, keywords=None, product_id=None, page=1, page_size=10,
                     global_=False, sorts_type=1, type_=None, max_routes=None,
                     budget=None, rate=None):
    """多路拆词 → 每路一次上游检索 → 去重 → 清单分页。**唯一检索路径。**

    设计(已定,勿重开):多路 + 只出清单 + 每路 pageSize=10 + **零排序分**。
    page/page_size 的作用对象是**清单**,不是上游分页:每路上游固定 pageSize=10,
    多路结果去重后得到清单,再按 (page-1)*page_size : page*page_size 切片。
    上游分页在多路下本就没有意义("第 2 页"是哪一路的第 2 页?)。
    """
    plan = cfg_max_routes() if max_routes is None else max_routes
    try:
        plan = max(1, min(int(plan), cfg_max_routes()))
    except Exception:
        raise InternalError("bad max_routes: %r(应为 1..%d 的整数)"
                            % (max_routes, cfg_max_routes()))
    route_list, product_id = _plan_routes(text=text, keywords=keywords, product_id=product_id)
    planned = min(len(route_list), plan)
    route_list, degraded = _dedupe_routes(route_list)
    route_list = route_list[:plan]

    route_lists, route_errors = [], []
    total, done = 0, 0
    # 每条路要凑够的条数下界:清单切片的下标上界是 page*page_size,清单由各路条目
    # 并集构成,故单路至少要能提供到这个深度,否则深页会切出空结果。
    # 取 max(10, ...) 保证默认浅页仍是"每路 10 条"不变。
    per_route_want = max(10, int(page) * int(page_size))
    for route_no, r in enumerate(route_list, 1):
        # 提前止损:余量见底就不必再发请求。硬闸仍在 _get_json 的 acquire()。
        if budget is not None and budget.remaining() == 0:
            budget.mark_exhausted()
            break
        try:
            items, t, _pages = _route_search_once(
                r["terms"], r.get("productIds"), type_, budget, rate,
                global_=global_, sorts_type=int(r.get("sortsType") or sorts_type or 1),
                page_size=10, want=per_route_want)
        except _BudgetExhausted:
            budget.mark_exhausted()
            break
        except UpstreamError as e:
            # 上游业务错误(HTTP 200 但 body 带 errorCode)。必须显式暴露给调用方:
            # 否则"某路被上游拒绝"会被读成"官方没这类文档"——清单是唯一交付物,
            # 这条路尤其不能静默。
            log("UPSTREAM_ERR:", "route=%s code=%s msg=%s" % (r.get("kind"), e.code, e.message))
            route_errors.append({"route": route_no, "kind": r.get("kind"),
                                 "terms": r.get("terms"),
                                 "error": "upstream_error", "code": e.code,
                                 "message": e.message})
            continue
        except Exception as e:
            log("ROUTE_ERR:", "route=%s terms=%s %s: %s"
                % (r.get("kind"), str(r.get("terms"))[:40], type(e).__name__, str(e)[:120]))
            route_errors.append({"route": route_no, "kind": r.get("kind"),
                                 "terms": r.get("terms"), "error": type(e).__name__,
                                 "code": None, "message": str(e)[:200]})
            continue
        done += 1
        total = max(total, t)
        route_lists.append((route_no, items))

    keys, route_hits, _first = _manifest_fuse(route_lists)
    by_key = {}
    for _no, items in route_lists:
        for n in items:
            by_key.setdefault(_manifest_key(n), n)
    manifest = [_manifest_project(by_key[k], route_hits[k]) for k in keys]
    clipped = manifest[(page - 1) * page_size: page * page_size]

    _used, _max, _exhausted = budget.snapshot() if budget else (None, None, False)
    budget_exhausted = bool(_exhausted)
    scan_parts = ["多路清单:%d/%d 路完成,每路 pageSize=10,去重后 %d 条"
                  % (done, planned, len(manifest))]
    if degraded:
        scan_parts.append("路数塌缩:计划 %d 路,去重后实际 %d 路(多路拆出同一串检索词)"
                          % (planned, len(route_list)))
    if type_:
        scan_parts.append("type=%s 过滤每路各带、跨页扫描独立计数(≤%d 页/路)"
                          % (type_, _MAX_SCAN_PAGES))
    if budget_exhausted:
        scan_parts.append("预算耗尽,实际完成 %d 路 / 计划 %d 路" % (done, planned))
    if route_errors:
        scan_parts.append("失败路 %d 条(见 routeErrors)" % len(route_errors))
    if not manifest:
        scan_parts.append("部分路失败,召回不完整" if route_errors else "无匹配")
    total_pages = (len(manifest) + page_size - 1) // page_size if page_size else 0
    return {"ok": True, "text": text, "keywords": list(keywords) if keywords else None,
            "total": total,
            "queries": [r["terms"] for r in route_list],
            "routesPlanned": planned, "routesDegraded": bool(degraded),
            # 本次**实际生效**的产品过滤:None=未带任何过滤,整数=产品线 id。
            # 这是调用方唯一可执行的产品线判据(与 --product 同值域,可直接比对):
            # 它经 _plan_routes 推导后的结果——问句里出现「苍穹」等别名时会被覆盖,
            # 故不能用调用方传入的原始值代替。原由 ask 独有,ask 删除后本内核若无此键,
            # 调用方就失去了核对产品线的落点(ANSWER-SPEC 第 8 条依赖它)。
            "effectiveProductId": product_id,
            "page": page, "pageSize": page_size, "totalPages": total_pages,
            "results": clipped,
            "routeErrors": route_errors,
            "budget_exhausted": budget_exhausted,
            "scanNote": ";".join(scan_parts)}
