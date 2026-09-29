#!/usr/bin/env python3
"""kd._impl._manifest —— 多路清单执行链(唯一检索路径)。

**排序契约(2026-09-18 定案,不得重开)**:本内核**不产生任何排序分**。

理由:每一次上游检索都由**官方综合排序**排好了,本内核拿到的每一路都是有序的。
多路的价值是"用不同关键词命中不同文档",不是"给文档打分"。故内核只做两件事:

  1. **去重**——同一条文档被多路命中时只留一条;
  2. **保持稳定顺序**——按(首次出现的路序号, 该路内的上游名次)。

两者的输入都不是算出来的:路序**逐字等于调用方给词的顺序**(ADR-0016 决策 2),
路内名次直接是上游返回的下标。**没有加权、没有分数、没有时间衰减、没有 views 权重**,
也没有"命中路数排序"——那同样是算法评分。

`hitRoutes` / `routes[]` 仍然返回,但**降级为纯信息字段**:它们告诉调用方(LLM)
"这条被哪几路搜到了",供它自己判断该读哪篇,**不参与排序**。

⚠️ **v6.6 删除面**(ADR-0016,2026-09-28):
  * `_resolve()` 字符串查表 —— 网络出口改为**模块级可替换的单一入口**
    (`_upstream._search_upstream`),不再需要为猴补丁包属性而设的间接层;
  * `type_` 过滤与跨页扫描(`_MAX_SCAN_PAGES` 的 while 循环)—— `--type` 删除后
    该循环永不执行(实测:不带 `--type` 时第一页就返回 10 条 = 目标条数);
  * 预算机制(`budget` / `_Budget` / `_BudgetExhausted`)—— 每路恒 1 次请求,
    预算永不可触发,`budget_exhausted` 顶层键随之消失;
  * `max_routes` 形参 —— 上限只在声明里有一个值(`limits.maxKeywords`);
  * 顶层 `text` 键 —— 位置参数删除后,"整句"不再有特殊地位,回显只留 `keywords[]`。
"""
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import _upstream
from ._config import (apply_link_policy, contract_loaded, log, max_keywords,
                      result_keys, top_keys)
from ._errors import InternalError, UpstreamError
from ._routes import _dedupe_routes, _plan_routes
from ._upstream import _question_url

# 每路上游固定取 10 条。清单是"每路 10 条去重归并"的产物,总长最坏只有 路数×10。
# ⚠️ 原 `_MAX_SCAN_PAGES`(跨页扫描上限)已随 `type_` 过滤一并删除:它是"为 type_
# 补齐服务的"旋钮,删除后该循环永不执行(决策 5)。
_PER_ROUTE_WANT = 10


def _route_search_once(text, product_id, rate, global_=False, sorts_type=1,
                       page_size=_PER_ROUTE_WANT):
    """单路检索:**只发 1 次请求**(pageSize=10),不做任何跨页补齐。

    返回 `(items, total)`。上游错误原样上传,由编排层分档处理(单路失败不拖垮整轮)。

    ⚠️ **跨页扫描已删除**(ADR-0016 决策 5):原 `while pg <= max_scan_pages` 循环是
    "为 `--type` 补齐服务的"——把混排结果里的目标类型抽出来需要多翻几页。`--type`
    删除后该循环**永不执行**(实测:不带 `--type` 时第一页就返回 10 条 = 目标条数),
    留着是死机制,且它会让"每次请求数不可预测"。现在每路恒 1 次请求:
    7 个词 = 7 次请求 = 3.33 秒(实测)。
    要更深由调用方自己换词或换参数,而不是内核自行扩充取数深度(禁止清单)。

    ⚠️ **本函数经 `_upstream._search_upstream` 调用网络出口**(模块级可替换的单一
    入口):测试把它换成假实现即可从 `core.search` 真跑完整链路而不触网。
    不写成 `from ._upstream import _search_upstream` 的局部绑定——那是 import 期快照,
    替换模块属性对它无效。

    global_ 必须由调用方透传:它是"跨全部产品"的开关,**不是**路由策略。
    """
    items = []
    dd = _upstream._search_upstream(text, product_id, 1, page_size, global_,
                                    sorts_type, rate)
    total = dd.get("totalElements") or 0
    for x in dd.get("content") or []:
        n = _norm(x)
        if n:
            items.append(n)
    return items, total


def _norm(x):
    """上游条目 → 本套件条目(经 `_upstream._norm_item`,`entity-type` 决定分支)。

    保留这一层薄封装是因为 `_norm_item` 需要上游 `entity-type` 作为分派键,而
    `_route_search_once` 只关心"这条能不能归一化"。映射点本身仍在 `_upstream` 一处。

    ⚠️ **宽容层**(2026-09-29,工单 #31):`.strip()` + `.lower()` + **NFKC 折叠**。
    实测上游确实会给出带空白/尾换行/全角的 `entity-type`,而 `_norm_item` 内部
    **零归一**、直接字面量比较 —— 于是这些变体被**整条静默丢弃**
    (`_norm` 返回 None,调用方只看到"结果变少了",没有任何信号)。

    这一步不是洁癖,而是**唯一的防线**:`_norm_item` 的形参契约写着「`et` 已小写」,
    **完全依赖唯一调用方遵守、函数内部无任何防御** —— 把这里的 `.lower()` 去掉,
    `_norm_item` 会退化为大小写敏感、全量静默丢弃。而 48 条离线用例**测不出来**
    (直调 `_norm_item` 的用例自己传小写,绕过了这一层)。

    ⚠️ NFKC 折叠治全角(`'Ｋｎｏｗｌｅｄｇｅ'` → `'knowledge'`);`.strip()` 治前后空白
    与尾换行(**含 NBSP** —— `str.strip()` 默认就把 `\\xa0` 当空白,无需额外处理)。
    """
    et = (x.get("entity-type") or "")
    if not isinstance(et, str):
        et = str(et)
    # NFKC 会把全角字母折成半角;strip() 去掉首尾空白(含 \n、\xa0);lower() 统一大小写。
    et = unicodedata.normalize("NFKC", et).strip().lower()
    return _upstream._norm_item(x, et)


def _manifest_key(n):
    """多路清单的去重键:`f"{type}:{id}"` —— **清单的单位是帖子**(ADR-0014)。

    ⚠️ **`other` 档必须并入 `upstreamType`**(2026-09-29 修正,code review C1):
    `f"{type}:{id}"` 的前提是"同一个 type 下 id 唯一"。前三个已知类型成立
    (knowledge / question / article 各有自己的 id 空间),但 **`other` 是一个
    跨类型的收容桶** —— 它把 `LearningCourse` / `LearningPath` /
    `KnowledgeSpecial` / `LearningBroadcast` **压成同一个 `type="other"`**,
    而它们的 id 空间**互不相干、形状相同**(实测课程与学习路径都用雪花串,
    课程还有 `6898` 这种小整数)。

    后果(实测复现):4 条不同实体 → 去重键只剩 2 个 → `total: 4` 而 `results: 2`,
    `routeErrors: []`、无任何提示 —— **与本轮要治的原始缺陷一字不差的形态**
    (「把'我们没读懂'伪装成'上游没有'」)。即若不处理,就是**用一处静默换另一处静默**。

    现行口径:同一帖的多条回答**合并为一条**,`id` 就是帖子号(见 `_norm_item`),
    故本键天然按帖子归并。已知取舍:同帖的多条回答不再能分开筛;回答数由
    `answersCount` 原生信号承担,"这帖里有采纳答案"由 `adopted` 承担。
    """
    kind = n.get("type") or "?"
    if kind == "other":
        # 跨类型收容桶:类型名必须进键,否则不同实体会互相吃掉。
        ut = str(n.get("upstreamType") or "")
        oid = str(n.get("id") or "")
        if not oid:
            oid = str(n.get("xId") or "")
        return "other:%s:%s" % (ut, oid)
    return "%s:%s" % (kind, n.get("id") or "")


def _manifest_merge(ns):
    """同帖多条目 → 一条**帖级**条目(ADR-0014,决策 D4)。

    上游搜索端点按**回答**返回:一个帖子下 3 条回答就是 3 个条目,各带自己的回答 id
    与同一个帖子号。清单的单位改成帖子后,这几条必须合并成一条——与人类搜社区时
    看到的形态一致。

    合并规则(逐项,**不引入任何排序分**——排序键仍是 (首次路序, 路内名次)):
      * `type`/`id`/`title`/`questionBody` —— 同帖取值本就相同,**取首个非空**;
      * `snippet` —— 各条回答的命中片段不同,**取上游给的第一条非空**(不拼接:
        拼接会把不相邻的回答片段伪装成一段连续文本);
      * `products` —— 取首个非空列表;
      * `url` —— **采纳优先**(见下),这是唯一一处"合并会改变值"的字段;
      * `adopted` —— **任一侧有值即为该值,两值取或**;两侧都无值则保持缺失。
        ⚠️ **D1 修复(v6.6)**:原写法 `bool(out.get("adopted")) or bool(n.get("adopted"))`
        把缺失键的 `None` 折成 `False` —— 于是一条**没有该字段**的 article 被合成了
        假的 `adopted: false`(上游从没说过它未被采纳,是我们替它说的)。
        实测影响:`tests` 那条"非问答条目 adopted 应为 None"的在线断言在真实数据上
        会红,只因合成数据的 id 互异而从未触发合并。
      * `answersCount` / `comments` / `supports` —— **取最大值**。上游各条各自复述
        同一帖的计数(正常同值),取 max 保证不因某条缺字段而丢掉真值。

    ⚠️ **`url` 的帖级取舍(2026-09-29,问答链接改判)**:问答条目现在是
    `/questions/<帖子号>/answers/<回答号>` 的**长形式**,故一个帖子下有几条回答
    就有几个**不同的** url —— 帖级合并必须选一个。规则:

      1. **采纳回答优先**(`isAdopt` 为真的那条)—— 链接应当指向"解"本身;
      2. 无采纳时回落**上游给的第一条** —— 上游本身按综合排序返回,首条即最好的
         默认落点,内核不引入任何自己的判断(零算法排序契约不变)。

    这与 `adopted` 的"取或"是两件事:`adopted` 说的是"这帖里有采纳答案",
    `url` 说的是"点开该落在哪条回答上"。
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
        # 帖级布尔:任一侧**有值**时取或。两侧都无值 → 保持缺失(不得合成 False,D1)。
        vals = [v for v in (out.get("adopted"), n.get("adopted")) if v is not None]
        out["adopted"] = any(bool(v) for v in vals) if vals else None
        for k in ("answersCount", "comments", "supports"):
            a, b = out.get(k), n.get(k)
            if isinstance(a, int) and isinstance(b, int):
                out[k] = max(a, b)
            elif b is not None and a is None:
                out[k] = b
    # 问答帖的 url 单独定:采纳回答优先,无则回落到上游首条(见 docstring)。
    if out.get("type") == "question":
        adopted = [n for n in ns if n.get("adopted") and n.get("_answerId")]
        pick = (adopted or [n for n in ns if n.get("_answerId")] or [None])[0]
        out["url"] = _question_url(out.get("id"), pick.get("_answerId")) if pick else None
    return out


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
    (ADR-0013),把清单粒度从回答级改成帖子级(ADR-0014)**不得**顺带引入排序分。
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
    # ⚠️ **直接保序,不排序**(2026-09-29,工单 #33,用户裁定)。
    #
    # 原先这里写 `sorted(order, key=_manifest_rank(first_seen))`,而 `_manifest_rank`
    # 实测是**恒等函数**:200 组随机构造下 `sorted(order, key=rank) == order` 恒成立,
    # 500 次试验 `first_seen` 取值恒不相交(单射,无 tie-break)。
    # 即"排完等于没排"——用户裁定删掉 `sorted` 与 `rank`,把伪装去掉。
    #
    # ⚠️ **真正的顺序契约不在那段被删的代码里,而在下面这个隐式不变量上**:
    #     `order.append(k)` 与 `first_seen[k] = (route_no, pos)` 是**同处写入**的
    #     —— 两者只在 `k not in route_hits` 的分支里各发生一次,故
    #     `order` 的元素次序**逐字等于** `first_seen` 的(路序, 名次)次序。
    #     这就是"清单顺序 = 调用方给词的顺序, 该路内上游名次"的载体。
    # 删 `rank` 时若把这段一并删掉(只留 `keys = list(order)` 而不留说明),
    # 下一个人会以为"顺序不重要" —— 而它是 ADR-0016 决策 2 的硬契约。
    # 另:`_manifest_fuse` 仍返回 `first_seen`(`_search_manifest` 不用它,
    # 但它是上面这条不变量的**可观测形式**,测试与排查都靠它)。
    keys = list(order)
    by_key = {k: _manifest_merge(groups[k]) for k in keys}
    return keys, route_hits, first_seen, by_key


def _manifest_project(n, route_nos):
    """清单条目的对外形态:只给标题级信息,不返回 contentText。

    调用方要全文走 `read(id, kind=type)`——"筛选"与"深读"两步解耦、各自可重试。
    没有 contentText 就不存在"替调用方决定读哪篇"这件事。

    **字段集 = 该条目 `type` 的键集(公共 + 类型专属),从 contract.json 声明取**
    (决策 D13 单一来源 + ADR-0016 决策 6 分三份)。声明里列了哪些键,本函数就产出
    哪些键 —— 故"多出字段/少了字段"两种情况都能被声明侧抓住。

    ⚠️ **取值规则(ADR-0016 决策 6,别改)**:
      * **类型不适用的键直接不出现在该条目上** —— 例如 article 不会有 `adopted`。
        这不是"填 null 也可以",而是一个语义区分:
          - **结构性不适用**(知识文档永远没有回答数)= 键**不出现**;
          - **上游没给值**(这条问答帖上游没给 comment 数)= 值为 `null`。
        混成同一种表示会让调用方分不清"这个字段对本类型无意义"与"这次没取到"。
      * 故本函数**不再**用 `{k: n.get(k) for k in result_keys()}` 统一填 `None`
        (那是旧形态,它让三类条目长着同一套键)。

    除标题级信息外,透传上游的**原生信号**(采纳标记/回答数/评论数/问题正文):
    它们是上游直接给的,不是本内核算的,正是调用方按标题匹配度挑选时需要的旁证。

    ⚠️ 条目已是**帖级**(`_manifest_merge` 合并过同帖多条回答),故这些信号的语义
    都是帖级的:`adopted` = "这帖里有采纳答案"。
    """
    # ⚠️ 不用 `{...} | {...}`(PEP 584 的 dict 合并运算符,Python 3.9+):
    # pyproject.toml 声明 `requires-python = ">=3.8"`,README 也写「Python 3.8+」。
    # 在 3.8 上这会直接 SyntaxError —— 而本套件号称"纯标准库、零依赖、装了就能跑",
    # 声明与实现不一致会让 3.8 用户拿到一个连 import 都过不去的包。
    kind = n.get("type")
    out = {k: n.get(k) for k in result_keys(kind)}
    # 这两个是**内核算出**的命中信息,不来自上游条目,故不在声明里当条目字段。
    out["hitRoutes"] = len(route_nos)
    out["routes"] = sorted(route_nos)
    # ⚠️ **链接政策的生效点**(2026-09-28;口径 2026-09-29 改判):三档都给链接,
    # 而 `other` 档给不出(实测无可点网页形式)。**这里是政策唯一的运行期闸门** ——
    # `apply_link_policy` 是它的消费者,声明不落到生产路径就等于没修
    # (CONTEXT.md:「声明必须有消费者,否则'单一来源'是假的」)。
    # (键恒在:它是公共键,政策改的是**值**而不是"这个字段对本类型是否有意义"。)
    out["url"] = apply_link_policy(kind, out.get("url"))
    return out


def _search_manifest(keywords=None, product_id=None, blank_dropped=0, global_=False,
                     sorts_type=1, rate=None, include_other=False):
    """调用方给的词 → 每路一次上游检索 → 去重归并 → 出**帖级**清单。**唯一检索路径。**

    设计(已定,勿重开):多路 + 只出清单 + 每路 pageSize=10 + **零排序分**。

    ⚠️ **内核不生成任何检索词**(ADR-0016 决策 1):`queries[]` 与调用方给的
    `keywords` **逐字、逐序**相同。本函数不再是"拆词入口",而是"发送 + 归并"。

    ⚠️ 原 `text` / `type_` / `max_routes` / `budget` 形参已于 v6.6 删除:
      * `text` —— 位置参数删除后无来源;顶层 `text` 键一并消失(契约不挂恒 null 的坑);
      * `type_` —— 清单不做类型过滤,调用方从条目的 `type` 字段自己筛;
      * `max_routes` —— 上限只在声明里一个值(`limits.maxKeywords`);
      * `budget` —— 预算机制整套删除(每路恒 1 次请求,不可触发)。

    ⚠️ **清单分页已整体删除**(决策 D10,2026-09-27)。取舍:清单总长最坏就是
    "路数×10 去重归并后"的量,不能翻页时只能拿全部;需要更多就换词重搜。

    `include_other` 由 `_public.search` 传入(默认 `False` = **隐藏** `other` 档):
    上游罕见类型(课程/路径/专题/直播)单独收容成一档,默认不混进清单以保护调用方的
    挑选信噪比;隐藏时把跳过的条数写进顶层 `otherSkipped` 与 `scanNote` ——
    **隐藏必须可见**,否则"total 大而 results 小"的差额又会变成无解释的静默。

    `blank_dropped` 由 `_public.search` 传入:调用方给的词里有几条是空串/纯空白
    (调用层已拦掉"全是空白"的输入,这里只处理**部分空**)。它必须进 `scanNote`——
    否则 `["甲","",""]` 只发 1 路而返回体看不出少了词,调用方会把自己的输入错误
    读成"内核只召回了 1 路"。规格 §A4:「违规的不是说不,是**不说**」。
    """
    # ---- 收词与上限:超出按顺序取前 N,并**写明丢了几条**(ADR-0016 决策 4) ----
    limit = max_keywords()
    route_list, product_id = _plan_routes(keywords=keywords, product_id=product_id)
    # `routesPlanned` 的口径(沿用 spec 第 3 节冻结的语义):**去重前、受上限截断后**
    # 的计划路数。不要改成去重后的实际路数——那不是这个字段的语义。
    planned = min(len(route_list), limit)
    route_list = _dedupe_routes(route_list)
    # ⚠️ 截断 = **按调用方给的顺序取前 N**(ADR-0016 决策 2),不再有"截断优先级"。
    # 原 `_truncate_routes` / `_TRUNC_PRIORITY` 已删:"按重要性保席位"这个概念整体消失
    # ——路序已逐字等于调用方给的顺序,内核没有做选择。违规的不是"取前 N",是**不说**。
    dropped = max(0, len(route_list) - limit)
    route_list = route_list[:limit]
    # ⚠️ **`routesDegraded` 已整体删除**(2026-09-29,工单 #33,用户裁定)。
    #
    # 用户原话:「**重复的就不要提示了,非重复的超上限的提示**」。
    # 按此口径 `keywordsDropped`(去重后词数 − 上限)**恰好是对的**,保留;
    # 要撤的是**重复词那一支的提示** —— 它原先报得**不一致**:
    #     `A B A`(3 词 1 重复)报「路数塌缩:3→2 路」;
    #     `A…G A`(8 词 1 重复)只字不提。**同一原因时而报时而不报。**
    # 留一个恒 false 的键是"死机制",与本仓"不留占位、不承诺不存在的能力"冲突,
    # 故整套删除(顶层键 + scanNote 句 + CLI stderr 句),不是置 false。
    #
    # ⚠️ **去重本身保留**(`_dedupe_routes`):不得对同一 query 发两次上游请求。

    # ---- 路由并发(ADR-0017 决策 4 阶段一):串行 for → 并发池 ----
    #
    # ⚠️ **收益归因**:典型工作流(search 3 路 + read 2 篇)中位 2372ms → 976ms,
    # 靠的是"网络往返重叠",**与放宽红线无关**(不动任何数字)。
    # 串行时网络完全不重叠,实测串行吃掉 60–70%、限速只占 20–40%。
    #
    # ⚠️ **并发度就是路数**(不加额外闸):`maxKeywords = 7` 已是声明里的上限,
    # 而 `_RateLimiter` 才是唯一的节流来源(实测上游在 7 路并发下零限流信号:
    # 无 Retry-After / X-RateLimit-*,totalElements 与串行逐条一致,
    # 并发 3 路 395ms ≈ 单请求 394ms 即真并发)。这里不再叠一层自造的并发闸 ——
    # 那会是"第二个限速器",与本仓"单一来源"的纪律冲突。
    #
    # ⚠️ **顺序契约(不得让完成先后决定顺序)**:`route_lists` 与 `route_errors`
    # 最终仍**严格按路号**排列。做法是按下标就地写回、最后按序过滤 ——
    # 不是"谁先回来谁先 append"(那会让清单顺序随网络抖动漂移,
    # 而排序契约 `(首次出现的路序号, 该路内上游名次)` 依赖路序稳定)。
    results, errors = {}, {}
    if route_list:
        with ThreadPoolExecutor(max_workers=len(route_list)) as pool:
            futures = {}
            for route_no, r in enumerate(route_list, 1):
                # sortsType 由调用方给的值直通(原句路的固定 sortsType 特殊性已随
                # ADR-0009 废止消失,`_route_sorts_type` 的 `or` 链风险一并消失)。
                # ⚠️ 这里必须取 `r["productIds"]`(经 `.get()` 容忍缺键),**不能**直接传
                # `product_id`(v6.6 审查复核):规格 A7 曾把本行判为"死分支、可删",但实测
                # 它**不是死的** —— `_stamp_product` 对"不过滤"(`0`/`None`)**刻意不写**
                # `productIds` 键,于是 `r.get(...)` 得到 `None`(省略上游参数 = 真不过滤);
                # 若改成传 `product_id`,显式 `0` 会原样送给上游,而上游把
                # `productIds[0]=0` **当真值过滤**(会把 Knowledge 挤出前排)。
                # 改成传 `product_id` 会让 `t_product_id_two_states` 直接红:
                # 「显式 0 时上游必须省略产品过滤,实收 [0]」。故此处保留原写法。
                futures[pool.submit(
                    _route_search_once, r["terms"], r.get("productIds"), rate,
                    global_=global_, sorts_type=int(sorts_type),
                    page_size=_PER_ROUTE_WANT)] = (route_no, r)
            for fut in as_completed(futures):
                route_no, r = futures[fut]
                try:
                    items, t = fut.result()
                except UpstreamError as e:
                    # 上游业务错误(HTTP 200 但 body 带 errorCode)。必须显式暴露给调用方:
                    # 否则"某路被上游拒绝"会被读成"官方没这类文档"——清单是唯一交付物,
                    # 这条路尤其不能静默。
                    log("UPSTREAM_ERR:", "route=%s code=%s msg=%s"
                        % (r.get("kind"), e.code, e.message))
                    errors[route_no] = {"route": route_no, "kind": r.get("kind"),
                                        "terms": r.get("terms"),
                                        "error": "upstream_error", "code": e.code,
                                        "message": e.message}
                    continue
                except Exception as e:
                    # ⚠️ **失败隔离不变**:单路失败只进 `routeErrors`,不拖垮整轮
                    # (与并发前的 `continue` 语义逐字相同)。
                    log("ROUTE_ERR:", "route=%s terms=%s %s: %s"
                        % (r.get("kind"), str(r.get("terms"))[:40], type(e).__name__,
                           str(e)[:120]))
                    errors[route_no] = {"route": route_no, "kind": r.get("kind"),
                                        "terms": r.get("terms"), "error": type(e).__name__,
                                        "code": None, "message": str(e)[:200]}
                    continue
                results[route_no] = (items, t)
    # 按路号还原顺序(并发完成顺序**不得**泄漏进清单)。
    route_lists = [(n, results[n][0]) for n in sorted(results)]
    route_errors = [errors[n] for n in sorted(errors)]
    done = len(results)
    total = max((t for _items, t in results.values()), default=0)
    # ⚠️ `done` / `total` 在并发前是**循环内累加**的;现在改成"从结果集重算" ——
    # 与串行版逐字等价(每路至多贡献一次 done,`total` 取全部成功路的 max),
    # 但不再依赖任何共享可变状态(那样就必须加锁,而锁会让这段变脆)。

    # 去重归并:同帖多条回答在此合并为一条(ADR-0014),排序键不受影响。
    keys, route_hits, _first, by_key = _manifest_fuse(route_lists)
    # ---- `other` 档的**默认隐藏 + 通报跳过数**(2026-09-29,工单 #32)----
    # ⚠️ 隐藏是**默认**行为(用户裁定:这些类型质量低,得与前三档区分开),
    # 但**隐藏必须可见** —— 否则就是用一处静默换另一处静默:
    # 调用方看到"total 很大、results 很小"却不知道差额是开关造成的。
    # `otherSkipped` 就是那个通报口。
    other_keys = [k for k in keys if (by_key[k].get("type") == "other")]
    other_skipped = 0 if include_other else len(other_keys)
    if not include_other and other_keys:
        keys = [k for k in keys if k not in set(other_keys)]
    manifest = [_manifest_project(by_key[k], route_hits[k]) for k in keys]

    scan_parts = ["多路清单:%d/%d 路完成,每路 pageSize=10,归并后 %d 条(帖子级)"
                  % (done, planned, len(manifest))]
    if dropped:
        # ADR-0016 决策 4:丢词必须在返回体里可见。这是既有纪律("宁可报错也不静默
        # 截断",见 clamp_query)在多路场景的落地。
        scan_parts.append("收词超上限:收到 %d 个词,只发前 %d 个(丢 %d 条,见 keywordsDropped)"
                          % (len(route_list) + dropped, limit, dropped))
    if blank_dropped:
        # ⚠️ 部分空串的丢弃也要说(v6.6 审查补):它不是"超上限",是调用方输入里有
        # 无效词。此前静默跳过 —— 调用方看到路数变少却找不到原因。
        scan_parts.append("丢弃空白词 %d 条(调用方输入里的空串/纯空白,不产生检索路)"
                          % blank_dropped)
    if route_errors:
        scan_parts.append("失败路 %d 条(见 routeErrors)" % len(route_errors))
    if other_skipped:
        # ⚠️ **这条通报是本档的存在理由之一**(工单 #32):没有它,
        # "total 212 而 results 2 条"的差额就仍然无解释 —— 那正是本缺陷的原始形态。
        scan_parts.append("隐藏 %d 条罕见类型(课程/路径/专题等,见 otherSkipped;"
                          "需显式打开才返回)" % other_skipped)
    if not manifest:
        scan_parts.append("部分路失败,召回不完整" if route_errors else "无匹配")

    # ⚠️ **顶层字段照 `topKeys` 声明拼**(ADR-0016 决策 8 / B4):
    # 此前 `topKeys` 在生产路径**零调用**(实测 `rg` 仅剩定义与导出),与 CONTEXT.md
    # 的纪律直接冲突:**「声明必须有消费者,否则'单一来源'是假的」**。
    # 现在声明是渲染闸门:从声明里删一个键,真实输出就跟着少一个(有回归钉子)。
    payload = {
        "ok": True,
        "keywords": list(keywords) if keywords else None,
        "total": total,
        "queries": [r["terms"] for r in route_list],
        "routesPlanned": planned,
        # 本次**实际生效**的产品过滤:整数=产品线 id,0=不过滤。
        # 这是调用方唯一可执行的产品线判据(与 --product 同值域,可直接比对):
        # **恒等于调用方传入的值**(不传即声明默认)——2026-09-27 起内核不再做任何
        # 字面推导(决策 D14),问句里的「苍穹」二字不再改写它。
        # ⚠️ v6.6 起**不再出现 `null`**(决策 3 三态收两态):`None` 与"不传"同义,
        # 都在 `_public.search` 归一到声明默认值;唯一特殊值是显式 `0`(不过滤)。
        "effectiveProductId": product_id,
        "results": manifest,
        # 被隐藏掉的 other 档条数(0 = 没跳过或开关已开)。见上方通报段。
        "otherSkipped": other_skipped,
        "routeErrors": route_errors,
        # ADR-0016 决策 4:丢了几条词。0 表示没丢。
        "keywordsDropped": dropped,
        "scanNote": ";".join(scan_parts),
        # ADR-0016 决策 7:声明(cfg)是否真的读到。false 时链接政策已回落
        # "全部不给链接" → 所有 url 是 null,调用方必须能分辨"没链接"与"没读到声明"。
        "contractCfgLoaded": contract_loaded(),
    }
    # ⚠️ 调 `project_top` 而**不是**再抄一遍推导式:那是本函数自己刚立的单一实现点,
    # 抄一份就等于"同一个事实有两份",改一处漏一处即刻静默漂移。
    return project_top(payload)


def project_top(payload):
    """按 `topKeys` 声明投影顶层字段(声明的**消费者**)。

    单独抽出来是因为**两个地方**都要用它:
      * `_search_manifest` 返回前(它产出的那批键);
      * `_public.search` 注入 `stats` 之后——`stats` 也是声明里的顶层键,
        若只在前一处投影,它就绕过声明(从声明删 `stats`,真实输出仍带着它)。

    声明里没有的键**一律不出现在最终返回体**——这就是"删声明即删输出"的机制。
    """
    return {k: payload[k] for k in top_keys() if k in payload}
