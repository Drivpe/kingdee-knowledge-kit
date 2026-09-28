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
from ._config import (_BudgetExhausted, apply_link_policy, cfg_max_routes, log,
                      result_keys)
from ._errors import InternalError, UpstreamError
from ._routes import _dedupe_routes, _plan_routes, _truncate_routes
from ._upstream import _norm_item, upstream_type_of

# 每条路向上游翻页的上限(type_ 过滤时用于补齐)。
_MAX_SCAN_PAGES = 5

# 每路上游固定取 10 条。清单是"每路 10 条去重归并"的产物,总长最坏只有 路数×10。
_PER_ROUTE_WANT = 10


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


def _route_sorts_type(route, fallback):
    """取某路的 sortsType:路自带值优先,否则用调用方给的值,再否则默认 1。

    缺陷 H 修复(2026-09-27):旧写法 `int(r.get("sortsType") or sorts_type or 1)`
    用 `or` 链取值,而 **0 是 falsy** —— 路或调用方明确指定 `sortsType=0`
    (相关性排序)时会被静默折成 1。当前上游实测 0 与 1 等价(都是相关性排序),
    故这是**潜伏 bug**:一旦配置改用 0 表达某个与 1 不同的姿态(或上游区分二者),
    会得到静默错误的排序且无任何信号。此处改为显式判空,保住 0。
    """
    v = route.get("sortsType")
    if v is None:
        v = fallback
    if v is None:
        return 1
    return int(v)


def _route_search_once(text, product_id, type_, budget, rate, global_=False, sorts_type=1,
                       page_size=_PER_ROUTE_WANT, want=_PER_ROUTE_WANT,
                       max_scan_pages=_MAX_SCAN_PAGES):
    """单路检索:每路按 pageSize=10 向上游取,直到凑够 `want` 条或触到扫描上限。

    返回 (items, total, pages_scanned)。上游错误原样上传,由编排层分档处理
    (单路失败不拖垮整轮)。

    `want` 恒等于 `page_size`(10):清单是"每路 10 条去重归并"的产物,不存在
    "要凑到更深"的调用场景——清单分页已于 2026-09-27 整体删除(决策 D10),
    原先那个 `want = max(10, page*page_size)` 的深页补齐逻辑随之消失。
    两个形参保留是因为它们是**跨页扫描的通用旋钮**(type_ 过滤时用于补齐),
    不是分页语义的残留。

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
        # ⚠️ 过滤比较必须用**上游词汇**:`type_` 是对外 kind(`question`),而上游
        # `entity-type` 是 `Answer`。直接拿对外的值去比会恒不相等 → 条目全被丢弃
        # → 清单空且无报错(实测暴露过)。映射表在 _upstream,只此一处。
        want_up = upstream_type_of(type_) if type_ else None
        for x in dd.get("content") or []:
            et = (x.get("entity-type") or "").lower()
            if want_up and et != want_up:
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
    """多路清单的去重键:`f"{type}:{id}"` —— **清单的单位是帖子**(ADR-0014)。

    ⚠️ 这条口径于 2026-09-27 反转(决策 D4),旧实现按**回答 id** 去重,理由曾写作
    "同一帖的不同回答语义不同(采纳的是解、普通的是旁证),必须各自成条、可分别筛"。
    用户推翻了该理由:"我自己人类搜索社区的时候问题和答案都是在一起的"。

    现行口径:同一帖的多条回答**合并为一条**,`id` 就是帖子号(见 `_norm_item`),
    故本键天然按帖子归并。已知取舍:同帖的多条回答不再能分开筛;回答数由
    `answersCount` 原生信号承担,"这帖里有采纳答案"由 `adopted` 承担。
    """
    return "%s:%s" % (n.get("type") or "?", n.get("id") or "")


def _manifest_merge(ns):
    """同帖多条目 → 一条**帖级**条目(ADR-0014,决策 D4)。

    上游搜索端点按**回答**返回:一个帖子下 3 条回答就是 3 个条目,各带自己的回答 id
    与同一个帖子号。清单的单位改成帖子后,这几条必须合并成一条——与人类搜社区时
    看到的形态一致。

    合并规则(逐项,**不引入任何排序分**——排序键仍是 (首次路序, 路内名次)):
      * `type`/`id`/`title`/`url`/`questionBody` —— 同帖取值本就相同,**取首个非空**;
      * `snippet` —— 各条回答的命中片段不同,**取上游给的第一条非空**(不拼接:
        拼接会把不相邻的回答片段伪装成一段连续文本);
      * `products` —— 取首个非空列表;
      * `adopted` —— **任一为真即为真**。语义由"这条回答被采纳"变为"这帖里有采纳答案";
      * `answersCount` / `comments` / `supports` —— **取最大值**。上游各条各自复述
        同一帖的计数(正常同值),取 max 保证不因某条缺字段而丢掉真值。
    """
    out = dict(ns[0]) if ns else {}
    for n in ns[1:]:
        for k in ("type", "id", "title", "url", "questionBody"):
            if not out.get(k) and n.get(k):
                out[k] = n[k]
        if not out.get("snippet") and n.get("snippet"):
            out["snippet"] = n["snippet"]
        if not out.get("products") and n.get("products"):
            out["products"] = n["products"]
        # 帖级布尔:任一为真即为真(合并不得把"这帖有采纳答案"洗成假)。
        out["adopted"] = bool(out.get("adopted")) or bool(n.get("adopted"))
        for k in ("answersCount", "comments", "supports"):
            a, b = out.get(k), n.get(k)
            if isinstance(a, int) and isinstance(b, int):
                out[k] = max(a, b)
            elif b is not None and a is None:
                out[k] = b
    return out


def _manifest_rank(route_index):
    """多路清单排序键:**(首次出现的路序号, 该路内的上游名次)**。

    纯函数,入参是构造数据,便于离线断言(不打上游):
      route_index  {key: (首次出现的路序号, 该路内名次)} —— 排序的唯一来源

    ⚠️ **2026-09-28 删掉了第一形参 `route_hits`**:它在函数体内**零使用**,
    是"纯装饰性形参"。它当初存的意图是"签名保留以便调用方显式表达这两个量是分开的,
    并作为回归钉子"——但**钉子不该由装饰性形参承担**:
      * 它不阻止任何事(把命中路数用进排序键要靠改函数体,不靠保留形参);
      * 它误导读代码的人以为排序与命中路数有关(恰好与本契约相反);
      * 真正的钉子已由离线用例 `t_manifest_order_route_first` 承担——那条用例
        构造"命中 1 路但路序靠前"vs"命中 2 路但路序靠后",**方向相反**,能真正
        区分新旧两种实现。
    故删形参、把防线留在有断言的地方。

    为什么第一维不是"命中路数":命中路数是一个**由本内核算出来的量**,
    按它排序等于本内核重新排序,而每一路本来就是官方综合排序的结果——
    那是在官方排序之上再叠一层我们自己的权重,与"让 LLM 按标题匹配度自己挑"
    的目标相反。用户 2026-09-18 明确:不使用算法排分,只去重(ADR-0013 决策 2)。
    """
    def key(k):
        first_route, first_pos = route_index.get(k, (99, 99))
        return (first_route, first_pos)
    return key


def _manifest_fuse(route_lists):
    """多路结果 → 去重后的清单条目(带 hitRoutes / routes)。

    route_lists 是 [(路序号, [规范化条目, …]), …],顺序即路的执行顺序。
    返回 (keys, route_hits, first_seen, by_key):
      keys       按 (首次路序号, 路内名次) 排列的清单键
      route_hits {去重键: {路序号,…}} —— 纯信息,不参与排序
      first_seen {去重键: (首次路序号, 路内名次)} —— 排序载体
      by_key     {去重键: 帖级条目} —— 同帖多条目已由 `_manifest_merge` 合并

    ⚠️ 合并(`_manifest_merge`)在这里发生,**排序键不受其影响**:排序仍只看
    "首次出现的路序号 + 该路内上游名次",取的是该帖最早被哪一路第几名召回。
    合并只改变条目的**字段内容**(帖级聚合),不改变任何顺序——零算法排序是硬契约
    (ADR-0013),本轮把清单粒度从回答级改成帖子级(ADR-0014)**不得**顺带引入排序分。
    """
    route_hits, first_seen, order, groups = {}, {}, [], {}
    for route_no, items in route_lists:
        for pos, n in enumerate(items, 1):
            if not n:
                continue
            k = _manifest_key(n)
            if k not in route_hits:
                route_hits[k] = set()
                first_seen[k] = (route_no, pos)
                order.append(k)
                groups[k] = []
            route_hits[k].add(route_no)
            groups[k].append(n)
    keys = sorted(order, key=_manifest_rank(first_seen))
    by_key = {k: _manifest_merge(groups[k]) for k in keys}
    return keys, route_hits, first_seen, by_key


def _manifest_project(n, route_nos):
    """清单条目的对外形态:只给标题级信息,不返回 contentText。

    调用方要全文走 `read(id, kind=type)`——"筛选"与"深读"两步解耦、各自可重试。
    没有 contentText 就不存在"替调用方决定读哪篇"这件事。

    字段集**从 contract.json 声明取**(决策 D13:单一来源)。缺失时回落内置兜底集,
    由离线回归用例钉住"兜底集与声明一致"。声明里列了哪些键,本函数就产出哪些键
    (值可为 None),故"多出字段/少了字段"两种情况都能被声明侧抓住。

    除标题级信息外,透传上游的**原生信号**(采纳标记/回答数/评论数/点赞数/问题正文):
    它们是上游直接给的,不是本内核算的,正是调用方按标题匹配度挑选时需要的旁证。

    ⚠️ 条目已是**帖级**(`_manifest_merge` 合并过同帖多条回答),故这些信号的语义
    都是帖级的:`adopted` = "这帖里有采纳答案"。
    """
    # ⚠️ 不用 `{...} | {...}`(PEP 584 的 dict 合并运算符,Python 3.9+):
    # pyproject.toml 声明 `requires-python = ">=3.8"`,README 也写「Python 3.8+」。
    # 在 3.8 上这会直接 SyntaxError —— 而本套件号称"纯标准库、零依赖、装了就能跑",
    # 声明与实现不一致会让 3.8 用户拿到一个连 import 都过不去的包。
    out = {k: n.get(k) for k in result_keys()}
    # 这两个是**内核算出**的命中信息,不来自上游条目,故不在声明里当条目字段。
    out["hitRoutes"] = len(route_nos)
    out["routes"] = sorted(route_nos)
    # ⚠️ **链接政策的生效点**(2026-09-28):`question/` 实测不可点,故其 url 不给读者。
    # 见 `_config.apply_link_policy` 的论证——声明不落到生产路径就等于没修。
    out["url"] = apply_link_policy(n.get("type"), out.get("url"))
    return out


def _search_manifest(text=None, keywords=None, product_id=None,
                     global_=False, sorts_type=1, type_=None, max_routes=None,
                     budget=None, rate=None):
    """多路拆词 → 每路一次上游检索 → 去重归并 → 出**帖级**清单。**唯一检索路径。**

    设计(已定,勿重开):多路 + 只出清单 + 每路 pageSize=10 + **零排序分**。

    ⚠️ **清单分页已整体删除**(决策 D10,2026-09-27):原先 `page`/`page_size`
    形参与切片逻辑在此处,"为深页多翻页"的 `per_route_want` 随之消失。
    取舍:清单总长最坏就是"路数×10 去重归并后"的量,不能翻页时只能拿全部;
    默认每路只取 10 条,7 路归并后通常十几条,深页本就不值一套复杂度。
    需要更多就换词重搜(或补 `--kw`)。
    """
    plan = cfg_max_routes() if max_routes is None else max_routes
    try:
        plan = max(1, min(int(plan), cfg_max_routes()))
    except Exception:
        raise InternalError("bad max_routes: %r(应为 1..%d 的整数)"
                            % (max_routes, cfg_max_routes()))
    route_list, product_id = _plan_routes(text=text, keywords=keywords, product_id=product_id)
    # `routesPlanned` 的口径由 spec 第 3 节冻结:是**去重前、受 max_routes 截断后**的
    # 计划路数。不要改成去重后的实际路数——那不是这个字段的语义。
    planned = min(len(route_list), plan)
    route_list, _deduped = _dedupe_routes(route_list)
    # ⚠️ 这里**不能再写 `route_list[:plan]`**(2026-09-28,T2 修):前缀切片是"按执行顺序
    # 截断",它会切掉保席位路。token 路插到第 1 位后,`--max-routes 1` 的前缀切片
    # 就切出 token、把原句路静默删除,击穿 ADR-0009 决策 3(原句路保席位)。
    # 改调唯一实现,按**截断优先级**选路(原句 → token → 其余 → product)。
    route_list = _truncate_routes(route_list, plan)
    # 缺陷 G 的假阳性修复(2026-09-27):`routesDegraded` 必须按 spec 自己的公式算
    # ——「`queries[]` 去重后的实际路数 < 计划路数」——而不是直接沿用
    # `_dedupe_routes` 的布尔值。后者报的是"拆解器原始产出里有重复",与
    # "计划执行的路数是否真的被去重削减"是两件事。
    # 反例(修前):`max_routes=1` + 拆出 2 路同词 → planned=1、去重后实际=1,
    # 但因 `_dedupe_routes` 看到重复就返回 true,回显 `routesDegraded=true` 且
    # scanNote 写出"计划 1 路,去重后实际 1 路"——两个数字相同却说塌缩,自相矛盾。
    # 按 spec 公式,此处 1 < 1 为假,正确地不报塌缩(截断本就不是塌缩)。
    degraded = len(route_list) < planned

    route_lists, route_errors = [], []
    total, done = 0, 0
    for route_no, r in enumerate(route_list, 1):
        # 提前止损:余量见底就不必再发请求。硬闸仍在 _get_json 的 acquire()。
        if budget is not None and budget.remaining() == 0:
            budget.mark_exhausted()
            break
        try:
            items, t, _pages = _route_search_once(
                r["terms"], r.get("productIds"), type_, budget, rate,
                global_=global_, sorts_type=_route_sorts_type(r, sorts_type),
                page_size=_PER_ROUTE_WANT, want=_PER_ROUTE_WANT)
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

    # 去重归并:同帖多条回答在此合并为一条(ADR-0014),排序键不受影响。
    keys, route_hits, _first, by_key = _manifest_fuse(route_lists)
    manifest = [_manifest_project(by_key[k], route_hits[k]) for k in keys]

    _used, _max, _exhausted = budget.snapshot() if budget else (None, None, False)
    budget_exhausted = bool(_exhausted)
    scan_parts = ["多路清单:%d/%d 路完成,每路 pageSize=10,归并后 %d 条(帖子级)"
                  % (done, planned, len(manifest))]
    if degraded:
        # spec 第 3 节口径:写明「路数塌缩 N→M」,N=计划路数、M=去重后实际路数。
        # `degraded` 已按 `len(route_list) < planned` 判定,故此处的两个数字必然不同
        # (不会再出现旧写法那种"计划 1 路,去重后实际 1 路"的自相矛盾句子)。
        scan_parts.append("路数塌缩:%d→%d 路(多路拆出同一串检索词)"
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
    return {"ok": True, "text": text, "keywords": list(keywords) if keywords else None,
            "total": total,
            "queries": [r["terms"] for r in route_list],
            "routesPlanned": planned, "routesDegraded": bool(degraded),
            # 本次**实际生效**的产品过滤:None=未带任何过滤,整数=产品线 id。
            # 这是调用方唯一可执行的产品线判据(与 --product 同值域,可直接比对):
            # **恒等于调用方传入的值**(不传即 93)——2026-09-27 起内核不再做任何
            # 字面推导(决策 D14),问句里的「苍穹」二字不再改写它。它的作用从
            # "把内核的推导结果告诉调用方"变成"让调用方确认这一轮到底用了哪条线":
            # 调用层判定了产品线就显式传,回显值可与传入值逐字核对。
            # 原由 ask 独有,ask 删除后本内核若无此键,调用方就失去核对落点
            # (ANSWER-SPEC 第 8 条依赖它)。
            "effectiveProductId": product_id,
            "results": manifest,
            "routeErrors": route_errors,
            "budget_exhausted": budget_exhausted,
            "scanNote": ";".join(scan_parts)}
