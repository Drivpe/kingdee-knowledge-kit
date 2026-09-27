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


def _derive_product_id(sources, product_id):
    """按**优先级顺序**扫各推导源 → 产品线 id(推导)。**两条入口共用**,是唯一落点。

    `sources` 是**有序**字符串序列,靠前者更权威。调用方按"这句话是谁说的"排:
    `text`(调用方明确给的整句)排在拆出来的关键词之前。

    语义边界(2026-09-27 定案,修缺陷 E):
      别名推导覆盖的是**默认兜底值**,不是调用方的明确选择。故:

        * `None` / `93`(默认兜底)→ 问句出现别名时按别名推导(否则默认 93 会把
          所有问句都当成旗舰版问题,2026-09-17 定案);
        * `0`(显式真不过滤)→ **不被字面改写**。显式 0 是唯一能拿到不过滤的方式,
          被问句里的「苍穹」二字改成 87 会让该语义消失(旧实现在 `text="苍穹 …"`
          + `product_id=0` 时回显 87,违反 spec「显式 0 = 真不过滤」);
        * 其他显式值(87 / 1 / 2 / 3 …)→ 尊重调用方,不被问句字面覆盖。
          调用层(LLM)已自行判定产品线时,内核不再用字面匹配二次改写它。

    ⚠️ 为什么必须收成公共出口:本函数此前**只在规则分支存在**,`keywords` 分支
    提前 return 绕过了它——于是「给了整句 + 给了拆好的词」这条最自然的 LLM 用法
    反而丢掉产品线推导(问句含「苍穹」却回显默认 93),拿苍穹的问题去搜旗舰版资料
    再当苍穹答案输出(2026-09-06 串线事故的形态)。

    ⚠️ 为什么要分优先级而不是拼成一个串(2026-09-27 补):拼成一维串会丢掉
    "谁说的"这层信息——实测 `text="苍穹 A"` + `--kw A --kw 旗舰版` 拼串后推到 93,
    而调用方在 text 里明确说了「苍穹」。产品线判定直接改变全部召回语料
    (实测 87 与 93 在同一问句下 **top10 零交集**),故这层优先级有实际后果,不是洁癖。

    ⚠️ 多别名同现的裁决规则(2026-09-27 定义,防"字典字面量顺序当契约"):一个源里
    可能同时出现多个别名(「苍穹 旗舰版」)时,**取在文本中出现位置最靠前的那个**
    (即"用户先说的更可能是主话题");位置相同则取更长者。
    这条规则自然处理嵌套别名——`"星空旗舰版"` 里的 `"旗舰版"` 位置更靠后,故外层胜出。
    定义它的理由:`query_routes.json` 的 dict 顺序**不是**任何人承诺的契约,而它是
    产品线判定的唯一依据;若不定规则,任何一次 JSON 重排都会静默改变判定结果。
    ⚠️ 这是**新定义的规则**,会改变少数组合的既有行为(实测 `"苍穹 旗舰版"` 原按表序
    得 93,现得 87——因「苍穹」在文本中更靠前)。跨产品线同现的问句本身罕见,
    两个方向都可辩护;此处选"先出现优先"是因为它**不受别名长度偏置**且不依赖配置顺序。
    """
    # 防呆:调用方误传字符串时,`for src in sources` 会**逐字符**迭代,每个字符都不含
    # 多字别名 → 静默返回原值、无异常无日志。签名刚从 `(text, …)` 改成 `(sources, …)`,
    # 迁移期这个形态最易出现,故就地归一而非静默降级。
    if isinstance(sources, str):
        sources = [sources]
    if product_id not in (None, 93):
        return product_id
    cfg = _route_cfg()
    aliases = ((cfg.get("productAliases") or {}).get("alias") or {})
    for src in sources:
        t = str(src or "")
        if not t:
            continue
        # 先出现者优先;同一位置取更长者。不依赖 alias 表的遍历序。
        best = None  # (位置, -长度, pid)
        for name, pid in aliases.items():
            n = str(name)
            pos = t.find(n)
            if pos < 0:
                continue
            key = (pos, -len(n))
            if best is None or key < best[0]:
                best = (key, pid)
        if best is not None:
            return best[1]
    return product_id


def _plan_routes(text=None, keywords=None, product_id=None):
    """一句话 → ≤maxRoutes 路检索词。返回 (routes, product_id)。

    两条入口:
      * `keywords` 显式给出 → 每词一路,替代全部自动拆解(调用层 LLM 拆词走这条);
        **原句路恒常存在**(2026-09-27):text 非空则由 text 充任,否则第 1 个 keyword 充任。
      * 否则按规则拆:原句路 + 症状词路 + 字段/实体名词路 + 产品/上下文词路。
    规则数据全部在包内 query_routes.json,不在代码里。

    产品过滤:显式关键词路径与规则路径**同权**携带 productIds——
    否则显式关键词会绕过 --product 造成串线。
    产品线推导亦同权:两条入口都过 `_derive_product_id`(缺陷 E)。
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
        # ⚠️ 推导源必须在 `kws.pop(0)` **之前**取(下方升格原句路会消费掉第一个)。
        # 否则 text 为空时,升格为原句路的那个关键词恰好从推导源里消失——
        # 而它正是"整句",产品词最可能就在它里面。
        derive_sources = [raw_text] + list(kws)
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
        # 产品线推导走**公共出口**(缺陷 E),推导源**按优先级**排列(缺陷 E 补修):
        #   1. `text` —— 调用方明确给的整句,最权威;
        #   2. 其余 `keywords` —— text 为空时第 1 个关键词已升格为原句路,故它也在其中。
        # 两者都要看:`kd search --kw "苍穹 XXX"`(text 为空、整句在 keywords 里)
        # 此前推出 93 而 `kd search "苍穹 XXX"` 推出 87——同句不同线,且两命令第 1 路
        # terms 逐字相同。分优先级而非拼串,是为了保住"text 里说的话 > 拆出的片段":
        # 否则 `text="苍穹 A"` + `--kw A --kw 旗舰版` 会因拼串丢掉 text 说的「苍穹」。
        product_id = _derive_product_id(derive_sources, product_id)
        if product_id and int(product_id) != 0:
            for r in routes:
                r["productIds"] = int(product_id)
        return routes, product_id
    text = str(text or "")
    # 问句产品词优先于默认:默认 93(旗舰版)是"用户没说时的兜底",
    # 不是"覆盖用户所说"。推导细节与边界见 `_derive_product_id` 的 docstring。
    product_id = _derive_product_id([text], product_id)
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
