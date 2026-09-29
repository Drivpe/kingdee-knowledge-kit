#!/usr/bin/env python3
"""kd._impl._detail —— 按 kind 读全文(知识 / 问答 / 文章)。

三个 kind 的取数与展开逻辑各自独立,但**分发点只应有一处**(`_detail`),
调用方(read)无需知道分发表存在。

⚠️ 问答这一档的对外 kind 是 **`question`**(决策 D5,2026-09-27),不是上游协议里的
`answer`。上游 `/api/questions/{帖子号}` 按**帖子**返回(问题正文 + 采纳答案 +
全部回答列表),故 kind 名与"帖子级"这一事实对齐;上游原始值 `answer` 只在
`_upstream._norm_item` 一处被翻译,本模块不出现它。
"""
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import _net
from ._config import (ENTITY_KINDS, VIP, _burst, apply_link_policy, log,
                      max_detail_knowledge)
from ._errors import InternalError, UpstreamError
from ._text import _is_true, html2text
from ._upstream import _URL_OF, _question_url

# ⚠️ **深读侧的全部网络调用必须经 `_net._get_json` 属性访问**(2026-09-29 出口统一)。
# 原先写 `from ._net import _get_json` 是 import 期快照 —— 替换 `_net._get_json`
# 拦不住本模块的 5 个调用点,于是"离线组真的不联网"这条承诺是**概率性的**:
# 有网就真发、没网就静默吞(实测抓到 1 次真实上游请求而 48/48 仍全绿)。
# 这是本仓"声明必须有消费者,否则'单一来源'是假的"那条纪律的又一例 ——
# `_upstream` 的 docstring 声称自己是"全内核唯一出口",而本模块的调用完全不经过它。


def _knowledge_article(kid, rate=None):
    d = _net._get_json(VIP + "/knowledgeapi/knowledge/" + str(kid), rate)
    return {"ok": True, "id": str(kid), "type": "knowledge", "title": d.get("title"),
            "contentText": html2text(d.get("content")),
            "url": apply_link_policy("knowledge", _URL_OF["knowledge"] % kid),
            "products": [p.get("name") for p in (d.get("products") or [])][:3],
            "updatedAt": d.get("updatedAt")}


def _answer_brief(a):
    """单条回答的摘要形态(含追问链)。"""
    disc = []
    for e in (a.get("appendQuestionsAndAnswers") or []):
        if isinstance(e, dict):
            t = html2text(e.get("content") or e.get("plainTextContent") or "")
            if t:
                disc.append({"creator": (e.get("creator") or {}).get("name")
                             if isinstance(e.get("creator"), dict) else None,
                             "contentText": t})
    return {"id": str(a.get("id") or ""),
            "contentText": html2text(a.get("description") or a.get("summary") or a.get("content") or ""),
            "adopted": _is_true(a.get("isAdopt")),
            "usefuls": a.get("usefuls"), "comments": a.get("comments"),
            "creator": (a.get("creator") or {}).get("name"),
            "createdAt": a.get("createdAt"),
            "discussion": disc or None}


def _q_products(d):
    mod = d.get("module") or d.get("domain") or {}
    pn = mod.get("pathName")
    return pn.split("/") if pn else []


def _question_detail(qid, with_answers=True, max_detail=None, rate=None):
    """问答帖全文:问题正文 + 最佳答案 + 回答列表(可展开详情)。

    入参 `qid` 是**帖子号**——上游 `/api/questions/{id}` 只认帖子号。

    ⚠️ `type` 回填的是对外 kind `question`(决策 D5);上游协议里的 `answer` 不在
    这里的对外字段中出现。清单条目的 `id` 就是帖子号,故 `read(kind="question", id)`
    直接可读,不存在"该传哪个 id"的问题(双 id 空间已随 D6 消失)。

    ⚠️ **`truncated` 为字符串枚举**(ADR-0016 决策 7,v6.6)。原实现是布尔 `true`,
    把两种成因压成同一个值:

      | 值 | 含义 | LLM 该做什么 |
      |---|---|---|
      | `"answer_limit"` | 帖子太长没读完(**逐条详情只展开前 N 条**;翻页已无上限,2026-09-29) | 接受现状,在答案里声明 |
      | `"upstream_error"` | 上游报错 | **重试** |

    为什么必须分开:`truncated` 是召回置信度判定(ADR-0010)的输入之一。把
    "上游坏了"与"资料就这么多"压成同一个值,会让判定**在最该保守的时候给出乐观
    结论**——上游故障时调用方会以为"这帖只有这些答案"。

    ⚠️ 原 `budget` 形参与 `_BudgetExhausted` 分支已删(v6.6):预算机制整套删除。
    同时规格已确认 `page_limit` 与 `budget` 两档**不存在**——清单侧不再截断
    (跨页扫描删除 + 预算删除),截断只可能发生在问答深读的这一处。
    """
    d = _net._get_json(VIP + "/api/questions/" + str(qid), rate)
    # ⚠️ **深度从声明取**(2026-09-29,工单 #30):`max_detail` 原先是个默认值 5 的
    # 形参,而**全仓无任何调用方传值**;它也不在 `contract.json` 里,故那个 5 是
    # 一个"碰巧等于实测最大值"的魔数(实测 25 条帖子回答数最大 5,卡在边界上)。
    # 现在它是声明里的 `limits.maxDetail`(单一来源),默认 None 表示"照声明取";
    # 显式传值仍可用于测试注入。
    # 公开签名 `core.read(kind, oid, rate)` **不变**,也不新增命令行参数 ——
    # 理由沿用 ADR-0016 决策 3 删 `--budget` 的先例:没有调用方可用它,
    # 就等于画上去的旋钮。
    max_detail = max_detail_knowledge() if max_detail is None else max_detail
    # ⚠️ **问答的 url 是长形式**(2026-09-29 改判):`/questions/<帖子号>/answers/<回答号>`。
    # 回答号取自 `d["bestAnswer"][0]["id"]`(采纳回答的回答号)—— 实测网页长形式
    # 22/22 可点,而短形式 `/question/<qid>` 恒死。
    # ⚠️ `d["answers"]` 是回答**条数**(整数),**不是**数组,不可当数组遍历;
    # 回答列表另有端点(`/api/questions/<qid>/answers`)。
    # ⚠️ 拿不到采纳回答时**给不出链接**(`_question_url` 返回 None)——不得回落短形式。
    best = d.get("bestAnswer")
    best_aid = str(best[0].get("id") or "") if (isinstance(best, list) and best
                                                and isinstance(best[0], dict)) else ""
    out = {"ok": True, "id": str(qid), "type": "question", "title": d.get("title"),
           "contentText": html2text(d.get("description")),
           "url": apply_link_policy("question", _question_url(qid, best_aid)),
           "isSolved": d.get("isSolved"), "answersCount": d.get("answers"),
           "views": d.get("views"), "rewardCoins": d.get("rewardCoins"),
           "products": _q_products(d),
           "createdAt": d.get("createdAt"), "updatedAt": d.get("updatedAt")}
    if isinstance(best, list) and best:
        out["bestAnswer"] = _answer_brief(best[0])
    if with_answers:
        # 回答展开(翻页+逐条详情)是深读里最贵的请求。截断**必须显式标记**并给出
        # "已取/总数"两个数字——截断而不标记等于把"资料不完整"伪装成"资料就是这样"。
        # 两类成因分开记(见 docstring 的枚举表)。
        truncated = None
        answers, total_pages = [], None
        try:
            # ---- 翻页:先取第 1 页,再决定还剩几页(ADR-0017 决策 4) ----
            # ⚠️ 不能"把页号全并发出去":`totalPages` 只有第 1 页的响应里才有。
            # 故顺序是**先决后并**:首页单独取,据此算出页号集合,其余页并发取。
            # ⚠️ **翻页上限已删除**(2026-09-29,工单 #30):原 `max_answer_pages=3`
            # 实测**永不可触发**(10 个问答帖全 `totalPages=1`;25 条帖子回答数中位 2、
            # 90 分位 4、最大 5;要触发需单帖 >60 回答,0 条)。留着是"画上去的旋钮"。
            # 现在**有多少页取多少页**,截断只可能来自 `max_detail`(逐条详情展开)。
            first = _net._get_json(VIP + "/api/questions/%s/answers?page=1&pageSize=20"
                                   % qid, rate)
            pages = [_answer_brief(a) for a in (first.get("content") or [])]
            # ⚠️ **首页与 `answers` 同步绑定**(2026-09-29 修回归):原写法把 `answers = pages`
            # 放在并发块**之后**,于是翻页任一路抛 `UpstreamError` 时异常穿透到外层 `except`,
            # `answers` 仍停在初始 `[]` —— **已经成功取回的首页回答被整体丢弃**,
            # 把"上游抖一次"伪装成"这帖没回答"(与 HEAD 对照:同桩下 HEAD 保住 5 条,原写法得 0 条)。
            # 绑定同一列表对象后,后续 `pages.extend(...)` 就地生效,异常路径也能保住已取页。
            answers = pages
            total_pages = first.get("totalPages") or 1
            if total_pages > 1:
                # 并发取剩余页;结果按**页号**归位 —— 完成先后不得决定回答顺序
                # (与路由并发同一条纪律,见 `_manifest._search_manifest`)。
                #
                # ⚠️ **并发度必须有上界**(2026-09-29 修正,code review H3):
                # 原写法 `max_workers=total_pages - 1` 让**上游决定我们的并发度** ——
                # 实测构造 `totalPages=200` 时会建 199 个线程、发 200 个请求
                # (旧实现上限 3 页,67 倍),而 `urlopen` timeout=20s、
                # `_RateLimiter` interval 200ms → 末位请求要等约 40 秒,
                # 必然超时。这正好踩在 ADR-0017 自标的「**10+ 路未实测,不应外推**」
                # 边界上。
                # 现取 `limits.rate.interactive.burst`(当前 7)作上界:**与"单次操作
                # 最大在飞请求数"对齐**,不再自造第二个限速器(那会违反"单一来源")。
                workers = max(1, min(total_pages - 1, _burst()))
                # ⚠️ **已完成页必须归位到 `pages` 上,不能攒在局部变量里等全部成功**
                # (2026-09-29 二次修正,复现为真回归)。首版把各页结果先收进函数局部
                # `rest[p]`,等**全部** future 成功穿越循环后才 `pages.extend(...)`;
                # 于是任一路抛 `UpstreamError` 时 `rest` 随栈帧丢弃 ——
                # **已经 in-flight 返回、只差归位的那几页被整体丢掉**。
                # 实测(同构造:page1=5 条、page2=3 条、page3 抛错且最先完成):
                # 首版 `answersTaken=5`,而 HEAD 的串行版在同一构造下保住 **8** 条
                # —— 即"修首页丢弃"只修了一半,并发块内又新开了一处丢失面。
                # 现改为:失败只**记录不中断**,收齐后按页号归位,最后再抛
                # —— 异常路径因此也能保住已完成页;`sorted(ordered)` 保证完成先后
                # 仍不决定回答顺序(与路由并发同一条纪律)。
                ordered, first_exc = {}, None
                try:
                    with ThreadPoolExecutor(max_workers=workers) as pool:
                        futs = {pool.submit(
                            _net._get_json,
                            VIP + "/api/questions/%s/answers?page=%d&pageSize=20" % (qid, p),
                            rate): p for p in range(2, total_pages + 1)}
                        # ⚠️ **遍历全部 future,不因某个失败而中断循环**(2026-09-29 二次修正):
                        # 首版 `for fut in as_completed(futs): ad = fut.result()` 一旦某页抛错,
                        # 循环体立刻向外交棒 —— 其余**已经 in-flight 返回、只差被收集**的页
                        # 连同 `rest` 一起随栈帧丢弃。实测(同构造:page1=5、page2=3、page3 抛错
                        # 且最先完成):首版与"只用 `finally` 归位"的中间版都只得 5 条,而 HEAD 串行版
                        # 保住 8 条 —— 说明**打断收集循环**才是丢页的主因(光挪归位时机不够)。
                        # 现改为:失败只记下**第一个**异常并继续收集,成功的页全部落进 `ordered`;
                        # 收集完毕后再抛出,交外层 `except` 置 `upstream_error`。
                        for fut in as_completed(futs):
                            p = futs[fut]
                            try:
                                ad = fut.result()
                            except UpstreamError as e:
                                if first_exc is None:
                                    first_exc = e
                                continue
                            ordered[p] = [_answer_brief(a) for a in (ad.get("content") or [])]
                            # ⚠️ 原为 `total_pages = max(total_pages, ad.get("totalPages") or 1)`
                            # —— 该值是**写后不读**的死写(循环内已用 `range(2, total_pages+1)`
                            # 固定了页号集合,后面的判断也不再用它),属"留死机制"。
                            # 已删除(2026-09-29,code review H4)。
                finally:
                    # ⚠️ **归位写在 `finally` 里,而不是只写在正常路径上**(2026-09-29):
                    # 本函数只把 `UpstreamError` 当"上游故障"处理;若某页抛**其它**异常
                    # (如解析类错误),它会一路穿透 —— 此时若归位只写在正常路径,
                    # 已收集的页就跟着栈帧一起丢。而穿透场景下函数**本就不返回结果**
                    # (`out` 尚未构造),故这**不构成新的数据丢失面**;写在 `finally`
                    # 纯属加固:让"已取即保留"这条不变量与异常类型无关。
                    # 就地 extend 到 `pages`(与 `answers` 同一对象),**不重新赋值**。
                    for p in sorted(ordered):
                        pages.extend(ordered[p])
                if first_exc is not None:
                    raise first_exc
            # ⚠️ `total_pages` 之后**不再被赋值**(原 `except` 分支里的
            # `total_pages = None` 也是死写,一并删除 —— 同上 H4)。
            # ⚠️ `answers` 已在首页处绑定同一个 `pages` 列表对象,此处**不得重新赋值** ——
            # 重赋值会把异常路径上已保住的部分结果再次切断(见首页处的回归说明)。
            answers.sort(key=lambda a: (not a["adopted"]))
            # 详情补全只覆盖前 N 条,其余仍是列表摘要 —— 这是**唯一**剩下的截断来源。
            if len(answers) > max(max_detail, 0):
                truncated = "answer_limit"
            # 逐条详情并发取(最多 max_detail 条)。⚠️ **按下标写回**:每个 future
            # 只改自己那一条,顺序由列表下标固定,与完成先后无关。
            targets = answers[:max(max_detail, 0)]
            if targets:
                with ThreadPoolExecutor(max_workers=len(targets)) as pool:
                    futs = {pool.submit(_net._get_json, VIP + "/api/answers/" + a["id"],
                                        rate): a for a in targets}
                    for fut in as_completed(futs):
                        a = futs[fut]          # 直接拿到该元素本身(列表里同一个对象)
                        det = _answer_brief(fut.result())
                        if len(det.get("contentText") or "") > len(a.get("contentText") or ""):
                            a["contentText"] = det["contentText"]
                        if det.get("discussion"):
                            a["discussion"] = det["discussion"]
        except UpstreamError as e:
            # ③ 上游故障:与"资料就这么多"是两件事——调用方该重试,不该接受现状。
            log("UPSTREAM_ERR:", "route=question_detail qid=%s code=%s msg=%s"
                % (qid, e.code, e.message))
            # ⚠️ 原此处 `total_pages = None` 是**死写**(后面无读点),已删(工单 #30/H4)。
            truncated = "upstream_error"
        if truncated:
            out["truncated"] = truncated
        out["answersTaken"] = len(answers)
        out["answersTotal"] = d.get("answers")
        out["answers"] = answers
    return out


def _article_detail(aid, rate=None):
    d = _net._get_json(VIP + "/api/articles/" + str(aid), rate)
    classes = [c.get("name") for c in (d.get("classifies") or []) if c.get("name")]
    return {"ok": True, "id": str(aid), "type": "article", "title": d.get("title"),
            "contentText": html2text(d.get("content")),
            "url": apply_link_policy("article", _URL_OF["article"] % aid),
            "products": classes[:3], "supports": d.get("supports"), "views": d.get("views"),
            "updatedAt": d.get("updatedAt")}


def _detail(kind, oid, rate=None):
    """详情统一入口(纯在线,不写穿落地缓存)。

    保留它是因为 3 个 kind 的分发点只应有一处;调用方无需知道分发表存在。
    URL 由各 kind 函数按 `_URL_OF` 模板统一构造,不在此处补齐。
    `refresh` 形参已删除(2026-09-18):内核恒在线,该形参在原实现里恒无效。

    ⚠️ **`other` 档是合法 kind 但没有全文端点**(2026-09-29,工单 #32):
    清单里会出现 `type: "other"` 的条目,故调用方照抄 `type` 传进来是**正确用法**,
    必须给出**准确**的提示(说清"这一档没有全文端点"),而不是让 `_detail` 直接
    `KeyError` —— 那会被 `cli._guard` 兜成 `internal_error`("这是 bug 而非用法问题"),
    把调用方引向"我是不是传错了"的方向,而它其实没传错。
    """
    fn = _DETAIL_FN.get(kind)
    if fn is None:
        if kind in ENTITY_KINDS:
            # 合法 kind、但没有全文端点:这是**已知的能力边界**,不是调用方错误。
            raise InternalError(
                "kind=%r 是合法类型,但内核**没有它的全文端点**:这一档收容的是"
                "上游罕见实体(课程/学习路径/专题/直播等),各类型的详情端点形状不一"
                "(部分无端点、个别 url 在第三方域),故未接入 read。"
                "清单条目本身已给出 title 与 upstreamType,可据此自行判断。" % (kind,))
        raise InternalError("bad kind: %s(%s)" % (kind, "|".join(_DETAIL_KINDS)))
    return fn(oid, rate=rate)


_DETAIL_FN = {"knowledge": _knowledge_article, "question": _question_detail,
              "article": _article_detail}
# ⚠️ `_DETAIL_KINDS` 是**真的有全文端点**的那几档 —— 它决定 `read` 的 `--kind`
# 可选值(CLI choices)与分发表。`other` **刻意不在其中**(见 `_detail` 的论证)。
# ⚠️ 但 `read(kind="other")` 仍须能被**区分**出来 —— 故 `_detail` 里先查
# `ENTITY_KINDS` 再报错,而不是一律 `bad kind`。两处合起来才是完整语义:
#   `_DETAIL_KINDS` = 可读全集的档;`other` = 合法但不可读。
_DETAIL_KINDS = tuple(_DETAIL_FN)
