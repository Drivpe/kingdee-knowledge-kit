#!/usr/bin/env python3
"""kd._impl._upstream —— 上游检索调用与条目规范化。

`_norm_item` 是**上游响应形状**与**本套件条目形状**之间唯一的翻译层:
上游的每个历史怪癖(点号同层键、双 id 空间、字符串布尔)都在这里收口,
别处不得再解析上游原始字段。
"""
import urllib.parse

from ._config import UPSTREAM_TEXT_MAX, VIP
from ._net import _get_json, clamp_query
from ._text import _is_true, _title_of, html2text

# 条目对外链接模板。可点性**按路径而异**(2026-09-17 实测,ADR-0012 决策 3):
#   knowledge/<id> → HTTP 200 且含正文,可点;
#   article/<id>   → 302 到 knowledge/<新id>(路径迁移,最终 200);
#   question/<id>  → 302 到 /error/404(帖子被删)。
# ⚠️ 陷阱:`question/` 的**最终 HTTP 状态码是 200**(成功重定向到了 404 页面),
# 只看状态码会误判为可用——必须看 url_effective。故引用优先用 knowledge/。
_URL_OF = {"knowledge": VIP + "/knowledge/%s", "answer": VIP + "/question/%s",
           "article": VIP + "/article/%s"}


def _norm_item(x, et):
    """上游原始条目 → 本套件条目。`et` 是上游的 `entity-type`(已小写)。

    双 id 空间(重要):answer 条目同时带 `id`(回答 id)与 `questionId`(帖子 id)。
    条目的 `id` 恒为**回答 id**——清单的单位是条目,同一帖的不同回答语义不同
    (采纳的是解、普通的是旁证),必须各自成条。读取路径另行只认 questionId
    (见 `_fetch_for_item`),两个 id 空间不得混用。
    """
    hl = x.get("highlight") or {}
    classes = [c.get("name") for c in (x.get("classifies") or []) if c.get("name")]
    if et == "knowledge":
        kid = str(x.get("knowledgeId") or x.get("id") or "")
        return {"type": "knowledge", "id": kid,
                "url": _URL_OF["knowledge"] % kid if kid else None,
                "title": _title_of(hl.get("title"), x.get("title")),
                "snippet": html2text(hl.get("content") or x.get("summary") or "")[:400] or None,
                "products": classes[:3],
                "views": x.get("views"), "useful": x.get("useful"),
                "contentLen": x.get("contentLen"), "updatedAt": x.get("updatedAt")}
    if et == "answer":
        q = x.get("question") or {}
        qid = str(x.get("questionId") or q.get("id") or "")
        return {"type": "answer", "id": str(x.get("id") or ""), "questionId": qid,
                "url": _URL_OF["answer"] % qid if qid else None,
                # 点号同层键优先;q["title"] 为形状防御(实测该字段不存在);
                # x["title"] 兜住"标题被平铺到条目顶层"的上游变体。
                "title": _title_of(hl.get("question.title"), q.get("title"), x.get("title")),
                "questionBody": html2text(q.get("description") or "")[:500] or None,
                "snippet": html2text(hl.get("description") or x.get("summary") or "")[:400] or None,
                "adopted": _is_true(x.get("isAdopt")),
                "answersCount": q.get("answers"),
                "products": classes[:3] or ([q.get("moduleName")] if q.get("moduleName") else []),
                "views": x.get("views"), "comments": x.get("comments"),
                "contentLen": x.get("contentLen"), "updatedAt": x.get("updatedAt")}
    if et == "article":
        arid = str(x.get("id") or "")
        return {"type": "article", "id": arid,
                "url": _URL_OF["article"] % arid if arid else None,
                "title": _title_of(hl.get("title"), x.get("title")),
                "snippet": html2text(hl.get("content") or x.get("summary") or "")[:400] or None,
                "products": classes[:3],
                "views": x.get("views"), "supports": x.get("supports"),
                "contentLen": x.get("contentLen"), "updatedAt": x.get("updatedAt")}
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
