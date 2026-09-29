#!/usr/bin/env python3
"""kd._impl._routes —— 调用方给的词 → 检索路。**本模块不生成任何检索词。**

⚠️ **拆词器已于 2026-09-28 整体删除**(ADR-0016 决策 1)。本模块曾含
`_rare_token`(稀有数字 token 抢第 1 路)、`_salient_chunks`(CJK 片段切分)、
`_EXEC_ORDER` / `_TRUNC_PRIORITY`(两张顺序表)、以及消费 `symptomCategories` /
`entityRules` / `stopwords` 三张词表的规则拆解器。三处都有**实测负结果**在案:

  | 内核的自行决定 | 实测结果 |
  |---|---|
  | 把 `BOM`(拉丁缩写)前置到第 1 路 | 金标从第 2 **掉到第 12** |
  | 把泛词拆成各自一路 | 金标**掉出前 30** |
  | 截断时按"重要性"选路 | 与另一处"直接砍尾巴"的截断并存,保席位靠碰巧 |

共同根源是**内核在做它猜不准的事**:判断"哪个词更该搜"需要语义,规则做不到。

现在的口径(ADR-0016 决策 1、2):检索词**只来自调用方**,内核把收到的词
**原样**发往上游——不拆解、不扩充、不前置、不排序。`queries[]` 与调用方给的词
逐字、逐序相同;路序 = 调用方给词的顺序。内核在检索侧的职责收敛为一句话:
**原样发送调用方给的词,去重归并结果。**

⚠️ 产品线判定不在这里(决策 D14,2026-09-27):`product_id` **直通**,调用方传什么
就是什么(不传即签名默认)。`product_id=0` / `None` 是"真不过滤"(省略上游参数)。
"""
from ._net import clamp_query


def _plan_routes(keywords=None, product_id=None):
    """调用方给的词 → route 列表。**逐字、逐序**,不删改、不重排、不补充。

    返回 `(routes, product_id)`:第二个元素恒等于入参(直通,决策 D14)。

    这里**没有 `text` 形参**(A2):位置参数删除后,"整句"这个概念不再有特殊地位
    ——它就是调用方给的一个词,与其余词同权同序。原句路(ADR-0009)已随本 ADR
    整体废止,见模块 docstring。

    唯一的两处"清洗"都不是生成行为:
      * 首尾空白 strip —— 空白不是词的一部分,不 strip 会把调用方的输入错误
        变成一次必然零结果的上游请求;
      * 跳过空串 —— 同因;`_public.search` 已在校验层拦住"全空输入",并对
        **部分空**的情形在 `scanNote` 里通报丢弃条数。
    去重**不在这里**:`_dedupe_routes` 负责(调用方给重复词是会发生的,去重必须做)。
    ⚠️ 原先它把"发生塌缩"作为事实通报出去,该通报已于 2026-09-29 按用户裁定撤销
    (「重复的就不要提示了」),详见 `_dedupe_routes` 的说明。

    ⚠️ 每路**只留 `kind` / `terms`(及可选的 `productIds`)**:`kind` 有真实消费者
    (`routeErrors[]` 与失败日志要指出"哪一路失败");`terms` 是发给上游的文本。
    原有一个恒定串 `"why": "调用方关键词(内核原样发送)"` 已在 v6.6 审查中删除 ——
    拆词器删掉后它**全仓零消费者**(只在 `contract.json` 之外的内部结构里躺着一份),
    属"声明无消费者",与 ADR-0016「一律真正删除,不留占位」一致。
    """
    routes = []
    for k in (keywords or []):
        term = str(k).strip()
        if not term:
            continue
        routes.append({"kind": "explicit", "terms": clamp_query(term)})
    return _stamp_product(routes, product_id), product_id


def _stamp_product(routes, product_id):
    """给每一路盖上产品线过滤(原地改,返回同一列表)。显式 0 / None = 不过滤。

    `product_id=0` 与 `None` 都是"真不过滤",必须**完全不写** `productIds` 键:
    上游对 `productIds[0]=0` 会当真值过滤(实测把 Knowledge 挤出前排),
    这是"不过滤"与"过滤到 0 号产品"的语义分界(见 `_search_upstream`)。
    """
    if product_id and int(product_id) != 0:
        for r in routes:
            r["productIds"] = int(product_id)
    return routes


def _dedupe_routes(route_list):
    """丢弃检索词完全相同的重复路,返回**去重后的列表**。

    调用方给重复词是会发生的(它会这么写:`--kw A --kw B --kw A`)。不去重就是对
    同一 query 发两次上游请求——既浪费一次请求,又会让该词条目的 hitRoutes 虚高。

    ⚠️ **本函数原先还返回第二个布尔值("是否发生塌缩")** —— **已于 2026-09-29 删除**
    (工单 #33 项一,用户裁定「重复的就不要提示了」)。当时删掉了 `routesDegraded`
    这套对外通报机制,那个布尔值随即**全仓零消费者**,只剩调用点一处
    `route_list, _deduped = ...` 的解包 —— 属"留死值",与本仓
    「声明必须有消费者」的纪律冲突。故连同返回值一起删除,不再宣称有一个
    并不存在的通报对象。

    ⚠️ 它与"截断"是两件事(缺陷 G 的口径):截断是"词数超过上限",塌缩是
    "同一串词出现多次"。截断的通报由 `keywordsDropped` 承担,与本函数无关。
    """
    out, seen = [], set()
    for r in route_list:
        t = str(r.get("terms") or "")
        if t in seen:
            continue
        seen.add(t)
        out.append(r)
    return out
