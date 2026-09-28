#!/usr/bin/env python3
"""kd._impl._routes —— 一句话 → 多路检索词(拆解器)。

**本模块是规则实现,不含任何模型调用。** 接口形状
`(text, keywords, product_id) → (routes[], product_id)` 对"规则实现"与
"LLM 实现"都成立——LLM 拆词发生在**调用层**(Agent 拆好词后经 `keywords`
传进 `search`),内核永不持有模型通道。

⚠️ **产品线不在这里判定**(决策 D14,2026-09-27):本模块曾有一套
`_derive_product_id`(别名表 + 推导源优先级 + "先出现优先"裁决规则),已**整体删除**
——函数、别名表、裁决规则全删。理由是它猜的是一句话里有没有产品名,而这件事
调用层比规则懂;且产品线判定错误等于拿完全不同的语料作答(实测同问句下
`--product 87` 与 `93` 的 top10 **零交集**)。

现在的口径:`product_id` **直通** —— 调用方传什么就是什么(不传即 `_config` 的
默认 93),内核不再用字面匹配二次改写它。换线靠调用层自己发第二轮。
`product_id=0` 仍是"真不过滤"(显式指定唯一途径)。

⚠️ **稀有 token 路(T2,2026-09-28)**:规则路径会把问句里的**纯数字串**
(≥3 位)单独拎成一路并**抢占第 1 路**,原句路退到第 2 路。这不是给 token 打分,
是**路序**——排序键第一维是"首次出现的路序号"(ADR-0013,零算法排序不受影响)。
判据与负结果见 `query_routes.json` 的 `tokenRoute` 段:**只认纯数字**,
拉丁词与中文词一律不前置(实测 'BOM' 前置会把金标从第 2 挤到第 12)。
"""
import re

from ._config import UPSTREAM_TEXT_MAX, _route_cfg, cfg_max_routes
from ._net import clamp_query

# 未截断时的**执行顺序**(= 最终路序 = 排序键第一维的来源)。
# token 抢在第 1 位是本轮(T2)的核心行为变更;原句退到第 2 位但**不丢**。
_EXEC_ORDER = {"token": 0, "raw:question": 1, "raw": 1, "product": 3}

# 超出 max_routes 时的**截断优先级**(数字小者先留)。三个保席位路种:
# 原句路携带完整语境、token 路携带唯一的收窄信号、产品词路携带 productIds 过滤,
# 三者的信息维度都不可被片段路挤掉。
# ⚠️ 与 _EXEC_ORDER **不是**同一张表,两者不得合并 —— 见 `_truncate_routes` 的论证:
# 原句路按 ADR-0009 决策 3 必须在截断时优先于 token 路(改动前它就是这么被保住的,
# 本轮不得破坏)。`raw` 是"拆解无命中"的兜底路,与原句路同权。
_TRUNC_PRIORITY = {"raw:question": 0, "raw": 0, "token": 1, "product": 2}


def _rare_token(text, cfg):
    """问句里**唯一值得抢占第 1 路**的稀有 token;没有则返回 None(T2,2026-09-28)。

    ⚠️ 只认**纯数字串**。这不是保守,是一条实测出来的负结果:

      | 前置到第 1 路的词 | 上游 total | 金标 B 位次 |
      |---|---|---|
      | `2510`(错误码)   | 2      | **1**(从第 4 升上来) |
      | `生产单位数量`(实体词) | 4506 | 2(未变) |
      | `分母变平方`(症状词) | 22038 | 2(未变) |
      | **`BOM`(拉丁缩写)** | 1308 | **12**(从第 2 掉下去) |

    即:"某个拉丁词看起来像标识符"**不等于**"它比同问句里的其他路更能收窄候选集"
    ——判断这件事需要语义,规则做不到。而**纯数字**形态在中文语料里几乎不碰撞
    (实测 `2510` 把候选集从 4302 压到 2,四个数量级),是唯一被背书的前置形态。

    同时**只取一个**:多 token 各自成路会互相挤掉席位(`maxRoutes` 有限),
    而实测有效形态恰是"单一路只放它自己"。取问句里**首个**满足条件的数字串
    ——错误码/单号通常唯一且写在最显眼处。

    `minDigits` 挡掉 `1`/`02` 这类页数序号(实测这类词在语料里无区分度)。
    """
    tr = cfg.get("tokenRoute") or {}
    if not tr.get("enabled", True):
        return None
    min_digits = int(tr.get("minDigits") or 3)
    m = re.search(r"\d{%d,}" % min_digits, str(text or ""))
    # 只取**首个**满足条件者,这是刻意的而非偷懒:实测有效形态就是"单一路只放它自己"
    # (与原句/中文词合并会稀释 —— E8 实测合并后掉出前 30),而 `maxRoutes` 有限,
    # 多 token 各自成路会互相挤掉席位。故**不提供"前置多个 token"的旋钮**:
    # 一个跑不出来的配置项比没有这个配置项更糟(它承诺了一个不存在的行为)。
    return m.group(0) if m else None


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
      * 否则按规则拆:稀有 token 路(纯数字)+ 原句路 + 症状词路 + 字段/实体名词路
        + 产品/上下文词路。
    规则数据全部在包内 query_routes.json,不在代码里。

    产品过滤:显式关键词路径与规则路径**同权**携带 productIds——
    否则显式关键词会绕过 --product 造成串线。

    ⚠️ 产品线**原样直通**(决策 D14,2026-09-27):本函数**不再推导产品线**。
    返回值第二个元素恒等于入参 `product_id`。调用层判定了产品线就显式传,
    内核不再用问句字面覆盖它(详见模块 docstring)。
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
        return _stamp_product(routes, product_id), product_id
    text = str(text or "")
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

    # 0') 稀有 token 路(T2,2026-09-28):纯数字串独立成路并**抢占第 1 路**。
    #     为什么值得动路序:排序键第一维是"首次出现的路序号",故"最能把候选集压窄的
    #     那一路必须排在第 1 位"。实测同一组词仅换路序,[2510, 原句] 得第 1、
    #     [原句, 2510] 得第 4。判据与负结果详见 `_rare_token`。
    token_route = None
    _tok = _rare_token(text, cfg)
    if _tok:
        token_route = {"kind": "token", "terms": clamp_query(_tok, raw_max),
                       "why": "稀有 token 路(纯数字标识符:错误码/单号强收窄,"
                              "实测候选集可压窄四个数量级)",
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
    # token 路插在**最前**(抢第 1 路),原句路退到第 2 路 —— 这是 T2 的核心行为变更。
    # ⚠️ 原句路仍是 ADR-0009 的"恒常参与路",只是**排位**让给收窄能力最强的那一路;
    #   它按 `_TRUNC_PRIORITY` 保席位(且截断时优先级高于 token 路),不会被截掉。
    if token_route:
        routes.insert(0, token_route)
    if raw_route:
        routes.insert(1 if token_route else 0, raw_route)
    _stamp_product(routes, product_id)
    # 上限截断:按优先级保席位(token / 原句 / 产品词路都携带不可被片段路挤掉的维度),
    # 具体优先级与论证见 `_truncate_routes`(**唯一实现**,不要让调用方另行切片)。
    routes = _truncate_routes(routes, max_routes)
    return routes, product_id


def _stamp_product(routes, product_id):
    """给每一路盖上产品线过滤(原地改,返回同一列表)。显式 0 / None = 不过滤。

    两条入口(keywords 显式路径与规则路径)**共用本函数**,因为产品过滤必须对两条
    路径**同权生效**——否则显式关键词会绕过 `--product` 造成串线(2026-09-06 事故形态)。

    `product_id=0` 与 `None` 都是"真不过滤",必须**完全不写** `productIds` 键:
    上游对 `productIds[0]=0` 会当真值过滤(实测把 Knowledge 挤出前排),
    这是"不过滤"与"过滤到 0 号产品"的语义分界(见 `_search_upstream`)。
    """
    if product_id and int(product_id) != 0:
        for r in routes:
            r["productIds"] = int(product_id)
    return routes


def _truncate_routes(route_list, limit):
    """把路数截到 `limit`,**按截断优先级**保席位。全仓唯一的路截断实现。

    ⚠️ 为什么必须收在一处(2026-09-28,T2 修):截断在**两个地方**发生——
      * `_plan_routes` 内部(拆解产出多于 `cfg_max_routes()` 时);
      * `_search_manifest` 里按调用方的 `--max-routes` 再截一次(`route_list[:plan]`)。
    后者是一句**前缀切片**,它保不保席位**完全取决于路序**。本轮把 token 路插到
    第 1 位后,`--max-routes 1` 的前缀切片就切出了 token 路、把原句路静默删除——
    **击穿了 ADR-0009 决策 3**(「原句路保席位,截断时不被挤掉」)。

    即:"截断优先级"与"执行顺序"是**两张不同的表**,而前缀切片把两者混为一谈。
    本函数把优先级判据收成唯一实现,两个截断点都调它,不再各写一次。

    截断优先级:`原句 → token → 其余 → product`(原句最高,依 ADR-0009 决策 3)。
    返回**按执行顺序**排好的列表(截断后的相对路序不变,便于调用方直接执行)。
    """
    if limit is None or len(route_list) <= limit:
        return list(route_list)
    keep = sorted(route_list, key=lambda r: _TRUNC_PRIORITY.get(r["kind"], 2))[:limit]
    return sorted(keep, key=lambda r: _EXEC_ORDER.get(r["kind"], 2))


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
