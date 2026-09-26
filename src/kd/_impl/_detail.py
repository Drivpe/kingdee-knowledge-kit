#!/usr/bin/env python3
"""kd._impl._detail —— 按 kind 读全文(知识 / 问答 / 文章)。

三个 kind 的取数与展开逻辑各自独立,但**分发点只应有一处**(`_detail`),
调用方(read)无需知道分发表存在。
"""
from ._config import VIP, _BudgetExhausted, log
from ._errors import UpstreamError
from ._net import _get_json
from ._text import _is_true, html2text
from ._upstream import _URL_OF

# 详情并发度:answer 帖要展开多条回答,串行会是深读里最贵的一段。
_DETAIL_WORKERS = 4


def _knowledge_article(kid, budget=None, rate=None):
    d = _get_json(VIP + "/knowledgeapi/knowledge/" + str(kid), budget, rate)
    return {"ok": True, "id": str(kid), "type": "knowledge", "title": d.get("title"),
            "contentText": html2text(d.get("content")),
            "url": _URL_OF["knowledge"] % kid,
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


def _question_detail(qid, with_answers=True, max_answer_pages=3, max_detail=5, budget=None,
                     rate=None):
    """问答帖全文:问题正文 + 最佳答案 + 回答列表(可展开详情)。

    ⚠️ 只认 questionId:上游 `/api/questions/{id}` 传回答 id 必 404。
    """
    d = _get_json(VIP + "/api/questions/" + str(qid), budget, rate)
    out = {"ok": True, "id": str(qid), "type": "answer", "title": d.get("title"),
           "contentText": html2text(d.get("description")),
           "url": _URL_OF["answer"] % qid,
           "isSolved": d.get("isSolved"), "answersCount": d.get("answers"),
           "views": d.get("views"), "rewardCoins": d.get("rewardCoins"),
           "products": _q_products(d),
           "createdAt": d.get("createdAt"), "updatedAt": d.get("updatedAt")}
    best = d.get("bestAnswer")
    if isinstance(best, list) and best:
        out["bestAnswer"] = _answer_brief(best[0])
    if with_answers:
        # 回答展开(翻页+逐条详情)是深读里最贵的请求。三种截断成因,全部显式置位
        # `truncated`,并给出"已取/总数"两个数字——截断而不标记等于把"资料不完整"
        # 伪装成"资料就是这样",调用方判档会因此失真。
        #   ① 翻页上限(max_answer_pages):正常退出循环,此前不置位 = 静默截断
        #   ② 逐条详情上限(max_detail):列表摘要未被补全为详情
        #   ③ 上游预算耗尽(_BudgetExhausted)
        truncated = False
        answers, page, total_pages = [], 1, None
        try:
            while page <= max_answer_pages:
                ad = _get_json(VIP + "/api/questions/%s/answers?page=%d&pageSize=20" % (qid, page),
                               budget, rate)
                for a in ad.get("content") or []:
                    answers.append(_answer_brief(a))
                tp = ad.get("totalPages") or 1
                total_pages = tp if total_pages is None else max(total_pages, tp)
                if page >= tp:
                    break
                page += 1
            if total_pages is not None and page >= max_answer_pages and max_answer_pages < total_pages:
                truncated = True   # 翻页上限先到,帖内还有未取的页
            answers.sort(key=lambda a: (not a["adopted"]))
            if len(answers) > max(max_detail, 0):
                truncated = True   # 详情补全只覆盖前 N 条,其余仍是列表摘要
            for a in answers[:max(max_detail, 0)]:
                det = _answer_brief(_get_json(VIP + "/api/answers/" + a["id"], budget, rate))
                if len(det.get("contentText") or "") > len(a.get("contentText") or ""):
                    a["contentText"] = det["contentText"]
                if det.get("discussion"):
                    a["discussion"] = det["discussion"]
        except _BudgetExhausted:
            truncated = True
        except UpstreamError as e:
            # 上游故障必须显式记日志,不得与"该路无结果"混为一谈。
            log("UPSTREAM_ERR:", "route=question_detail qid=%s code=%s msg=%s"
                % (qid, e.code, e.message))
            total_pages = None
            truncated = True
        if truncated:
            out["truncated"] = True
        out["answersTaken"] = len(answers)
        out["answersTotal"] = d.get("answers")
        out["answers"] = answers
    return out


def _article_detail(aid, budget=None, rate=None):
    d = _get_json(VIP + "/api/articles/" + str(aid), budget, rate)
    classes = [c.get("name") for c in (d.get("classifies") or []) if c.get("name")]
    return {"ok": True, "id": str(aid), "type": "article", "title": d.get("title"),
            "contentText": html2text(d.get("content")),
            "url": _URL_OF["article"] % aid,
            "products": classes[:3], "supports": d.get("supports"), "views": d.get("views"),
            "updatedAt": d.get("updatedAt")}


def _detail(kind, oid, budget=None, rate=None):
    """详情统一入口(纯在线,不写穿落地缓存)。

    保留它是因为 3 个 kind 的分发点只应有一处;调用方无需知道分发表存在。
    URL 由各 kind 函数按 `_URL_OF` 模板统一构造,不在此处补齐。
    `refresh` 形参已删除(2026-09-18):内核恒在线,该形参在原实现里恒无效。
    """
    return _DETAIL_FN[kind](oid, budget=budget, rate=rate)


def _fetch_for_item(item, budget=None, rate=None):
    """按条目取详情(供内部编排使用)。单条失败不抛,降级为 {"ok": False}。

    answer 只认问题 id(questionId):清单条目的 `id` 是回答 id,拿去请求
    /api/questions/{id} 必 404。此前的 `or item["id"]` 兜底会把"传错 id"
    伪装成一次真实请求,故移除——口径单一,传错即快速失败。
    """
    try:
        if item["type"] == "knowledge":
            d = _detail("knowledge", item["id"], budget=budget, rate=rate)
        elif item["type"] == "answer":
            d = _detail("answer", item["questionId"], budget=budget, rate=rate)
        elif item["type"] == "article":
            d = _detail("article", item["id"], budget=budget, rate=rate)
        else:
            d = None
    except _BudgetExhausted:
        d = {"ok": False, "error": "budget_exhausted"}
    except Exception as e:
        d = {"ok": False, "error": str(e)[:150]}
    return d


_DETAIL_FN = {"knowledge": _knowledge_article, "answer": _question_detail,
              "article": _article_detail}
_DETAIL_KINDS = tuple(_DETAIL_FN)
