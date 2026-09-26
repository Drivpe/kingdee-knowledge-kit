#!/usr/bin/env python3
"""kd._impl._routes —— 一句话 → 多路检索词(拆解器)。

**本模块是规则实现,不含任何模型调用。** 接口形状
`(text, keywords, product_id) → (routes[], product_id)` 对"规则实现"与
"LLM 实现"都成立——LLM 拆词发生在**调用层**(Agent 拆好词后经 `keywords`
传进 `search`),内核永不持有模型通道。
"""
import re

from ._config import UPSTREAM_TEXT_MAX, _route_cfg, cfg_max_routes
from ._net import clamp_query

# 展示序:截断时保席位的路种。产品词路携带 productIds 过滤、原句路携带完整语境,
# 两者信息维度不可被片段路挤掉。
_PINNED_KINDS = ("product", "raw:question", "raw")


def _salient_chunks(text, stopwords):
    """CJK 连续段去掉停用词/功能词后切成候选症状词(≥2 字)。

    拉丁/数字 token 由调用方单独扫描提取(中英混写常无空格,
    如"分母显示27000"里的 27000)。
    """
    sw = [re.escape(s) for s in (stopwords or []) if s]
    out = []
    for seg in re.findall(r"[\u4e00-\u9fff]+", str(text or "")):
        parts = re.split("(?:%s)" % "|".join(sw), seg) if sw else [seg]
        out += [p for p in parts if len(p) >= 2]
    return out


def _plan_routes(text=None, keywords=None, product_id=None):
    """一句话 → ≤maxRoutes 路检索词。返回 (routes, product_id)。

    两条入口:
      * `keywords` 显式给出 → 每词一路,替代全部自动拆解(调用层 LLM 拆词走这条);
        **原句路恒常存在**(2026-09-27):text 非空则由 text 充任,否则第 1 个 keyword 充任。
      * 否则按规则拆:原句路 + 症状词路 + 字段/实体名词路 + 产品/上下文词路。
    规则数据全部在包内 query_routes.json,不在代码里。

    产品过滤:显式关键词路径与规则路径**同权**携带 productIds——
    否则显式关键词会绕过 --product 造成串线。
    """
    cfg = _route_cfg()
    max_routes = cfg_max_routes()
    raw_cfg = cfg.get("rawRoute") or {}
    raw_sorts = int(raw_cfg.get("sortsType", 1))
    raw_max = int(raw_cfg.get("maxChars") or UPSTREAM_TEXT_MAX)
    if keywords:  # 调用方显式关键词:每词一路,**原句路恒常补入**(ADR-0009)
        # 为什么显式关键词路径也要原句路:原句路的价值是独立于拆词的
        # (完整语境的词汇鸿沟召回)。旧实现在本分支直接丢弃 text、只留回显,
        # 于是"我给整句 + 我给拆好的词"这种最自然的用法反而丢了原句路——
        # 用户 2026-09-27 拍板:**默认就是要原句的**,不留开关。
        kws, seen = [], set()
        for k in keywords:
            k = str(k).strip()
            if k and k not in seen:
                seen.add(k)
                kws.append(k)
        routes = []
        raw_text = str(text or "").strip()
        if raw_text:
            # text 非空 → 由它充任原句路(固定 sortsType=1,占第 1 路)。
            routes.append({"kind": "raw:question", "terms": clamp_query(raw_text, raw_max),
                           "why": "原句路(调用方给了 text,自动补为第 1 路;"
                                  "片段路的词汇鸿沟无法覆盖时由此救回)",
                           "sortsType": raw_sorts})
        elif kws:
            # text 未给 → 第 1 个关键词就是调用方给的"整句/完整报错串",由它充任原句路
            # (故它不再另占一条 explicit 路:路数不变,只有排序姿态变精确)。
            routes.append({"kind": "raw:question", "terms": kws.pop(0),
                           "why": "原句路(调用方未给 text,取第 1 个关键词充任)",
                           "sortsType": raw_sorts})
        for k in kws:
            routes.append({"kind": "explicit", "terms": k, "why": "调用方显式关键词"})
        routes = routes[:max_routes]
        if product_id and int(product_id) != 0:
            for r in routes:
                r["productIds"] = int(product_id)
        return routes, product_id
    text = str(text or "")
    aliases = ((cfg.get("productAliases") or {}).get("alias") or {})
    # 问句产品词优先于默认:默认 93(旗舰版)是"用户没说时的兜底",
    # 不是"覆盖用户所说"。问句里出现「苍穹」「企业版」等别名时,推导值覆盖默认,
    # 否则默认 93 会让所有问句都被当成旗舰版问题(2026-09-17 定案)。
    for name, pid in aliases.items():
        if str(name) in text:
            product_id = pid
            break
    latin = []  # 扫描提取(中英混写无空格:"MRP运算""分母显示27000"里的 MRP/27000 也要拿到)
    for t in re.findall(r"[A-Za-z0-9][A-Za-z0-9.%/_:-]*", text):
        if t not in latin:
            latin.append(t)
    cjk = _salient_chunks(text, cfg.get("stopwords"))
    routes, used = [], set()

    # 0) 原句路(ADR-0009):先占席位,固定 sortsType=1,不受截断挤压
    raw_route = None
    if text.strip():
        raw_route = {"kind": "raw:question", "terms": clamp_query(text.strip(), raw_max),
                     "why": "原句路(相关性排序;片段路的词汇鸿沟无法覆盖时由此救回)",
                     "sortsType": raw_sorts}

    def take(term):
        used.add(term)
        return term

    # 1) 症状词路:按类别归拢(命中模式词/包含模式词的原文片段)
    numeric = [t for t in latin if re.fullmatch(r"\d+", t)]
    cats = cfg.get("symptomCategories")
    cats = cats.get("categories", []) if isinstance(cats, dict) else (cats or [])
    for i, cat in enumerate(cats):
        terms = []
        for p in (cat.get("patterns") or []):
            p = str(p)
            if p.lower() in [t.lower() for t in latin]:
                terms.append(take(next(t for t in latin if t.lower() == p.lower())))
            else:
                hit = next((c for c in cjk if p in c and c not in used), None)
                if hit:
                    terms.append(take(hit))
        if i == 0 and terms:
            terms += [t for t in numeric if t not in used][:2]  # 纯数字量词归第一症状路
        if terms:
            routes.append({"kind": "symptom:" + str(cat.get("name") or "symptom"),
                           "terms": " ".join(terms[:4]), "why": cat.get("why")})
    leftover_cjk = [c for c in cjk if c not in used]
    leftover_lat = [t for t in latin if t not in used and not re.fullmatch(r"\d+", t)]
    # 2) 字段/实体名词路:症状反推领域术语(词汇鸿沟的桥)
    ent = cfg.get("entityRules")
    ent = ent.get("rules", []) if isinstance(ent, dict) else (ent or [])
    for rule in ent:
        when = rule.get("whenAny") or []
        if when and not any(str(w) in text for w in when):
            continue
        rgx = rule.get("whenRegex")
        if rgx:
            try:
                if not re.search(rgx, text):
                    continue
            except re.error:
                pass
        for r in (rule.get("routes") or []):
            terms = r.get("terms") if isinstance(r, dict) else r
            if terms:
                routes.append({"kind": "entity:" + str(rule.get("name") or "?"), "terms": str(terms),
                               "why": (r.get("why") if isinstance(r, dict) else None) or rule.get("why")})
    # 3) 产品/上下文词路:剩余 token(产品名/BOM 等)+productIds 过滤
    if leftover_lat or leftover_cjk:
        routes.append({"kind": "product", "terms": " ".join((leftover_lat + leftover_cjk)[:3]),
                       "why": "产品/上下文词路(携带 productIds 过滤)"})
    # 兜底:拆解一路未出(原句路关闭或文本为空)→ 原句一路
    if not routes and not raw_route and text.strip():
        routes.append({"kind": "raw", "terms": clamp_query(text.strip(), raw_max),
                       "why": "拆解无命中,原句检索"})
    if raw_route:
        routes.insert(0, raw_route)
    if product_id and int(product_id) != 0:
        for r in routes:
            r["productIds"] = int(product_id)
    # 上限截断:产品词路与原句路保席位(两者携带的信息维度不可被片段路挤掉)
    if len(routes) > max_routes:
        pinned = [r for r in routes if r["kind"] in _PINNED_KINDS]
        rest = [r for r in routes if r["kind"] not in _PINNED_KINDS]
        routes = rest[:max(0, max_routes - len(pinned))] + pinned
        routes.sort(key=lambda r: (0 if r["kind"] == "raw:question" else
                                   2 if r["kind"] == "product" else 1))
    return routes[:max_routes], product_id


def _dedupe_routes(route_list):
    """丢弃检索词完全相同的重复路,返回 (去重后列表, 是否发生塌缩)。

    拆解器会让原句路与产品/上下文词路产出同一串词(实证:`信用额度控制` →
    raw:question 与 product 两路 terms 逐字相同)。不去重就是对同一 query
    发两次上游请求——既浪费预算,又会让该词条目的 hitRoutes 虚高。

    "发生塌缩"是**事实**,必须能被调用方看见(故返回布尔而非静默收敛):
    塌缩后清单实际只由更少的路构成,调用方判断召回广度时要知道这件事。
    """
    out, seen = [], set()
    for r in route_list:
        t = str(r.get("terms") or "")
        if t in seen:
            continue
        seen.add(t)
        out.append(r)
    return out, len(out) < len(route_list)
