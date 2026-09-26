#!/usr/bin/env python3
"""kd._impl._text —— 上游 HTML → 纯文本,以及标题降级链。

零依赖的纯函数层:不触网、不读配置,可单独喂合成数据断言(离线用例直接打这里)。
"""
import re


def html2text(h):
    """上游 HTML 片段 → 纯文本。

    只做标签订阅与实体还原,不做排版推断:上游正文里的结构靠 【】/数字序号表达,
    那些由 chunk 层负责,不在本函数职责内。
    """
    if not h:
        return ""
    h = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", h, flags=re.S | re.I)
    h = re.sub(r"<br\s*/?>", "\n", h, flags=re.I)
    h = re.sub(r"</(p|div|tr|h[1-6]|li|table)>", "\n", h, flags=re.I)
    h = re.sub(r"</t[dh]>", "\t", h, flags=re.I)
    h = re.sub(r"<[^>]+>", "", h)
    h = (h.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<")
          .replace("&gt;", ">").replace("&quot;", '"').replace("&#39;", "'"))
    lines = [re.sub(r"[ \t]+\n", "\n", re.sub(r"[ \t]{2,}", " ", ln)).strip() for ln in h.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(ln for ln in lines if ln))


def _is_true(v):
    """上游布尔字段是字符串 "true"/"false" 形态,不是 JSON 布尔。"""
    return str(v or "").lower() == "true"


def _title_of(*cands):
    """标题解析:按优先级取第一个**非空**候选,逐个 html2text 后判空。

    为什么不能写成 `html2text(a or b or "")`:那些候选里混着"" 与 None,
    而 html2text 对纯高亮标签串(如上游只回 `<em>禁用</em>` 的边界)会产出非空结果、
    对真正缺失的字段才产出""。若只用 `a or b` 短路,一个"存在但为空串"的高优先候选
    会挡住后面真正有货的候选;反之若先 html2text 再 or,`<em>x</em>` 这种候选又能
    正确胜出。此处显式逐级判空,把降级链写死成可预期顺序,避免 silent None。

    上游 answer 条目的标题在 `highlight["question.title"]`(**含点号的同层键**,
    不是嵌套对象 `highlight["question"]["title"]`)——实测 2026-09-17 该键存在且非空;
    但 `question` 对象里**没有** title 字段(实测其键集里只有 description 等),
    故原第二级兜底 `q.get("title")` 恒为空,是死分支。保留它作为形状防御,
    同时补上 `x["title"]`——上游某些时期会把标题平铺在条目顶层。
    """
    for c in cands:
        if c is None:
            continue
        t = html2text(c).strip()
        if t:
            return t
    return None
