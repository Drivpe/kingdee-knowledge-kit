#!/usr/bin/env python3
"""kd._impl._upstream —— 上游检索调用与条目规范化。

`_norm_item` 是**上游响应形状**与**本套件条目形状**之间唯一的翻译层:
上游的每个历史怪癖(点号同层键、双 id 空间、字符串布尔、entity-type 大小写)
都在这里收口,别处不得再解析上游原始字段。

⚠️ **`answer` → `question` 的唯一映射点**(决策 D5/D6,2026-09-27):
上游协议里问答条目的 `entity-type` 仍是 `"Answer"`,而本套件对外一律用 `question`
(与 `read` 的 `kind` 同集合)。这个翻译**只在本文件的 answer 分支发生**,此文件之外
的代码、文档、CLI 一律不得出现 `answer` 这个上游原始值。
"""
import urllib.parse

from ._config import UPSTREAM_TEXT_MAX, VIP
from ._net import _get_json, clamp_query
from ._text import _is_true, _title_of, html2text

# 条目对外链接模板。可点性**按路径而异**(2026-09-28 定案,唯一真源 = contract.json
# 的 linkPolicy):`knowledge/` 与 `article/` **给链接**,`question/` **不给**
# (匿名 9/9 + 登录态 1 条,两条证据方向一致)。
#
# ⚠️ 模板本身与可点性是**两件事**:模板恒按上表产出(它如实指向上游的内容页路径),
# 是否把该 url 交给读者由 linkPolicy 决定。不要因为"某个 kind 不给链接"就改模板。
#
# ⚠️ 陷阱(留证,以免重复踩;两个方向的误判都**实际发生过**):
#   * `question/` 的**最终 HTTP 状态码是 200**(成功重定向到 404 页),只看状态码会
#     把失效链接误判为可用——必须看 `url_effective`;
#   * `article/248777993676668672` 首跳 **302**,但最终 URL 是
#     `/knowledge/248777993710223104` 且该页**可点**(被迁移成知识文档)。
#     只看首跳状态码会把可点链接误判为失效——09-18 与 09-27 两份文档都这么记错过。
#   * 不要试图把 `question/` 改成 `questions/`(复数)——实测该路径同样不存在。
_URL_OF = {"knowledge": VIP + "/knowledge/%s", "question": VIP + "/question/%s",
           "article": VIP + "/article/%s"}

# 对外 kind/type 词汇 → **上游 entity-type** 词汇。
#
# ⚠️ 这是 `question ↔ answer` 映射的**第二半**,必须与下面 `_norm_item` 里的
# `et == "answer"` 分支成对存在(一个管"发请求时怎么比较",一个管"收响应时怎么翻译")。
# 只改一半会出现**静默零结果**:上游返回的都是 `Answer`,若过滤条件拿对外的
# `"question"` 去比,`et != "question"` 恒真 → 全部条目被丢弃 → 清单空、且无任何报错
# (实测就是这么暴露的:type=question 检索零结果)。
# 上游把大小写写成 `Knowledge`/`Answer`/`Article`,故比较前一律 lower()。
_UPSTREAM_TYPE_OF = {"knowledge": "knowledge", "question": "answer", "article": "article"}


def upstream_type_of(kind):
    """对外 kind → 上游 entity-type(已小写)。未知 kind 原样返回(交给上层报错)。"""
    k = str(kind or "").lower()
    return _UPSTREAM_TYPE_OF.get(k, k)


def _norm_item(x, et):
    """上游原始条目 → 本套件条目。`et` 是上游的 `entity-type`(已小写)。

    ⚠️ **帖子号即 `id`**(决策 D6,2026-09-27):问答条目的 `id` 就是**帖子号**
    (上游的 `questionId`),不是回答 id。理由与后果:

      * 清单是**帖子级**的(ADR-0014):一个帖子下的多条回答在上游是多个独立条目,
        合并为一条后回答数走 `answersCount` 原生信号;故条目的 `id` 必须是帖子号,
        否则同一帖会各自成条(旧形态),且读取时还得回答"该传哪个 id";
      * 上游同时给的回答 id 与 `questionId` 两个 id 空间,在这里**收口成一个**:
        `questionId` 字段整体删除,`id` 取帖子号。`_fetch_for_item` 直接用 `item["id"]`。
      * `url` 用帖子号而不是回答 id:`question/<帖子号>` 是详情的真实路径。
    """
    hl = x.get("highlight") or {}
    classes = [c.get("name") for c in (x.get("classifies") or []) if c.get("name")]
    if et == "knowledge":
        kid = str(x.get("knowledgeId") or x.get("id") or "")
        return {"type": "knowledge", "id": kid,
                "url": _URL_OF["knowledge"] % kid if kid else None,
                "title": _title_of(hl.get("title"), x.get("title")),
                "snippet": html2text(hl.get("content") or x.get("summary") or "")[:400] or None,
                "products": classes[:3]}
    if et == "answer":
        # ← 上游 "answer" 在这里翻译成对本 `question`;**这是全仓唯一的映射点**。
        q = x.get("question") or {}
        qid = str(x.get("questionId") or q.get("id") or "")
        return {"type": "question", "id": qid,
                "url": _URL_OF["question"] % qid if qid else None,
                # 点号同层键优先;q["title"] 为形状防御(实测该字段不存在);
                # x["title"] 兜住"标题被平铺到条目顶层"的上游变体。
                "title": _title_of(hl.get("question.title"), q.get("title"), x.get("title")),
                "questionBody": html2text(q.get("description") or "")[:500] or None,
                "snippet": html2text(hl.get("description") or x.get("summary") or "")[:400] or None,
                # 帖级信号:同帖多条回答被合并后,这几项由 _manifest_merge 聚合。
                "adopted": _is_true(x.get("isAdopt")),
                "answersCount": q.get("answers"),
                "comments": x.get("comments"),
                "products": classes[:3] or ([q.get("moduleName")] if q.get("moduleName") else [])}
    if et == "article":
        arid = str(x.get("id") or "")
        return {"type": "article", "id": arid,
                "url": _URL_OF["article"] % arid if arid else None,
                "title": _title_of(hl.get("title"), x.get("title")),
                "snippet": html2text(hl.get("content") or x.get("summary") or "")[:400] or None,
                "products": classes[:3],
                "supports": x.get("supports")}
    return None


def _search_upstream(text, product_id, page, page_size, global_, sorts_type, type_, budget=None,
                     rate=None):
    """单次上游检索。所有路最终都经这里,故参数语义必须逐条准确。

    product_id:0 或 None = **不过滤**——必须省略参数。传 `productIds[0]=0`
    上游会当真值过滤(实测把 Knowledge 挤出前排),这是"不过滤"与"过滤到 0 号产品"
    的语义分界。
    global_:跨全部产品的开关,由调用方透传;上游只认字符串 "true"/"false"。
    """
    text = clamp_query(text, UPSTREAM_TEXT_MAX)  # 入口压回:上游 100 字硬闸
    params = {"text": text, "page": page, "pageSize": page_size,
              "global": "true" if global_ else "false", "sortsType": sorts_type}
    if product_id and int(product_id) != 0:
        params["productIds[0]"] = int(product_id)
    return _get_json(VIP + "/api/search?" + urllib.parse.urlencode(params), budget, rate)
