#!/usr/bin/env python3
"""kd._impl._public —— 公开面函数:search / read。

这两个函数是整套件对外的全部能力。它们只做三件事:校验入参、装配统计、
把编排结果包装成对外契约;**检索逻辑一行都不在这里**(在 _manifest / _detail)。
"""
import time

# ⚠️ **`VERSION` 曾在此 import 并进 `__all__`** —— 是**死 import**:
# 本模块代码零读点,而"版本单一真源"的两条链都不经过这里
# (`kd.__version__` / `cli._VERSION` 走 `kd._impl.VERSION`,那份来自 `_config`)。
# 2026-10-01 死 import 清理时删除 import 与 `__all__` 里那一项
# (编号口径见 ADR-0018 末节:它**不是** ADR-0014 的 `D14`,那套编号指的是
# "产品线字面推导删除";`.scratch/` 里那第二套编号已随该目录 gitignore 而不可解析)。
# ⚠️ 同理删除 `_DETAIL_KINDS`:它决定 `read --kind` 的 choices,而那个消费者在
# `_detail` / `cli`,本模块零读点(`read` 的类型校验走 `ENTITY_KINDS`)。
from ._config import (ENTITY_KINDS, UPSTREAM_TEXT_MAX, _up_now,
                      default_product_id, log)
from ._detail import _detail
from . import _detail as _detail_mod
from ._errors import InternalError
from ._manifest import _search_manifest, project_top
from ._net import clamp_query

# ⚠️ 签名的 `product_id` 默认值**必须**从声明派生(不能写 `93` 字面量):
# 它是"不传 `--product` 时生效"的唯一载体,而 `contract.json` 的 `productIds.default`
# 是那份声明的单一来源。写死字面量会让声明改了而签名没跟上,且**没有任何信号**
# (实测:改声明 `default` 后,`default_product_id()` 变了、签名默认值仍是旧的)。
# 用模块级常量而不是直接写 `default_product_id()`:签名默认值在函数定义时求值一次,
# 内联调用会在每次定义/重载时重新读文件(import 期契约足够,且更可预测)。
_DEFAULT_PRODUCT_ID = default_product_id()


def _norm_product_id(product_id):
    """产品线取值**三态收两态**(ADR-0016 决策 3,v6.6)。

    原三态(实测暴露过、也修过)是:
      * 省略该参数 → 签名默认值 = 声明默认编号;
      * 显式 `None` → "**不过滤**"(Python 调用方表达"我明确不要产品过滤"的方式);
      * 显式整数(含 `0`)→ 原样直通。

    用户裁定收成**两态**:`None` 与"不传"同义(都是默认),唯一特殊值是显式 `0`。
    理由:三态里 `None` 与 `0` **同效而值域不同**(都是"不过滤"却要调用方记两个写法),
    而 CLI 侧从来没暴露过 `None` 这一态(它只有 `--product 0`)。收成两态后
    `effectiveProductId` **不再出现 `null`**,调用方与回显逐字可比对。

    ⚠️ 这是一条**破坏性变更**:旧调用方若用 `product_id=None` 表达"不过滤",
    改传 `0` 即可(语义完全一致,见 `_stamp_product`)。
    """
    if product_id is None:
        # ⚠️ **跟随声明,而不是取导入期快照**(2026-09-29,工单 #33)。
        # 原先这里返回 `_DEFAULT_PRODUCT_ID`(import 期求值一次),
        # 于是改了 `contract.json` 的 `productIds.default` 后:
        #   `default_product_id()` → 新值,而本函数 → 旧值。
        # 生产路径**从不重读声明**,故"改声明不生效"只在进程内可见 ——
        # 真问题是 `default_product_id()` 当时**零运行时消费者**。
        # 现在本行就是它的消费者。
        # ⚠️ **签名默认值仍必须是模块级常量**:回归断言
        # `sig.parameters["product_id"].default == DEFAULT_PRODUCT_ID`,
        # 且签名默认值在函数定义时求值一次是刻意的(更可预测)。
        return default_product_id()
    return product_id


def search(keywords=None, product_id=_DEFAULT_PRODUCT_ID,
           global_=False, sorts_type=1, rate=None, include_other=False):
    """唯一检索入口:多路检索,**只出帖级标题清单**(不返回正文)。
    清单 → 挑选 → 全读,三步解耦。本函数只负责第一步:把找到的条目按标题级信息
    列出来,交给你(调用方 agent)按标题匹配度决定读哪几篇。要全文走 `read(id, kind=type)`。

    ⚠️ **内核不生成任何检索词**(ADR-0016 决策 1,2026-09-28):`keywords` 是**唯一**
    输入,内核把收到的词**原样、按原顺序**发往上游——不拆解、不扩充、不前置、不排序。
    `queries[]` 与你给的词逐字、逐序相同;结果顺序 = (你给词的顺序, 该词内上游名次)。
    拆词是**调用层**的职责:你按 SKILL.md 的拆词规范把问题拆成关键词后传进来,
    内核不持有模型通道。**原句路已随本 ADR 整体废止**(ADR-0009)——原句不再是特殊路,
    它就是你想发的一个词。

    keywords   检索词列表,**必填**(空则 raise InternalError)。**每词一路**。
               ⚠️ 超过上限(声明 `limits.maxKeywords`,默认 7)时**按你给的顺序取前 N 个**,
               并在返回体里写明 `keywordsDropped`(丢了几条)+ `scanNote` 说明。
               依据:7 个词 = 7 次上游请求 = 3.33 秒。
    product_id 93=星空旗舰版(默认)/ 87=苍穹 / 1=企业版标准版 / 2=星空侧二开问答专区;
               显式 0 = 不过滤。**产品线由你判定后显式传入**(2026-09-27):内核**不做
               任何字面推导**,问句里出现「苍穹」也不再改写本值。**两态**(v6.6):
               不传与传 `None` 同义(都是声明默认);要真正的不过滤必须显式传 `0`。
    global_    跨全部产品检索。透传到每一路上游请求。
    sorts_type 默认排序(0/1=相关性,2/3=时间倒序)。直通到每一路。
    rate       限速档名(默认 interactive)。
    include_other
               **是否返回「其他」档**(默认 `False` = 隐藏)。上游除了三类已知实体,
               还返回罕见类型(实测 `LearningCourse` 课程 / `LearningPath` 学习路径 /
               `KnowledgeSpecial` 专题 / `LearningBroadcast` 直播);
               它们质量低、且与前三档**不是同一种资料**(课程是 video/ppt 课件),
               故**默认不混进清单**,以免拖累你的挑选信噪比。
               ⚠️ **隐藏时返回体会写明跳过了多少条**(`otherSkipped` + `scanNote`)——
               所以"没看到"永远能从返回体区分出是"开关关着"还是"上游没有"。
               这一档的 `type` 是 `other`,另带 `upstreamType`(上游原始类型名,
               如 `LearningCourse`)供你自行判断;⚠️ **不给网页链接**
               (实测无任何可点的网页形式,见 `contract.json` 的 `linkPolicy`)。

    返回:`results[]` 每项含 type/id/title/url 等标题级字段,**不含 contentText**
    ——要全文走 `read(id, kind=type)`。另有 routeErrors[](哪几路失败,用于区分
    "被上游拒绝"与"官方没这类文档")、routesPlanned(计划路数)、otherSkipped(隐藏了
    几条罕见类型)、keywordsDropped、scanNote、stats。
    ⚠️ **顶层没有 `routesDegraded`**(2026-09-29 删除):它原先报"路数塌缩(你给了重复词)",
    而实测报得自相矛盾,用户口径为「重复的就不要提示了」。重复词仍会被去重,只是不再通报。
    ⚠️ `results[]` 是**帖子级**的(ADR-0014):同一帖的多条回答已合并为一条,
    回答数看 `answersCount`、"这帖有采纳答案"看 `adopted`。
    ⚠️ `results[]` 的字段集**按条目类型分四份**(ADR-0016 决策 6 + 2026-09-29 新增 `other`):
    类型不适用的键**直接不出现**(例如 article 没有 `adopted`),而不是填 `null`——
    "结构性不适用"与"上游没给值"是两件事。三类恒有的公共键见
    `contract.json` 的 `resultKeysCommon`。

    **排序**:结果顺序 = (你给词的顺序, 该词内上游名次)。每一路都是官方综合排序
    的产物,本内核**只去重、不重排、不产生任何评分**;`hitRoutes` 是给你参考的信息字段,
    不参与排序。非法入参 raise InternalError。
    """
    if not keywords:
        raise InternalError("keywords required: 传拆好的检索词(每词一路)"
                            "(内核不再自行拆词,ADR-0016)")
    # ⚠️ **非序列入参必须显式报错**(v6.6 施工裁定,A2 的直接后果):位置参数 `text`
    # 删除后,旧写法 `core.search("整句")` 不再 TypeError——它会把字符串**按字符**
    # 绑到 `keywords` 上,于是"一句话"被静默发成 N 个字的 N 路请求(每个汉字单独成路,
    # 必然零结果或极差召回),而调用方拿到的是一个结构完全合法的返回体。
    # 这是本项目最忌的"静默失效":宁可报错也不静默(clamp_query 同纪律)。
    # 故显式拒绝 str/bytes,并把正确写法写进 hint。
    if isinstance(keywords, (str, bytes)):
        raise InternalError(
            "keywords 应为**词列表**,收到单个字符串: %r。"
            "内核不再收整句(位置参数 text 已删,ADR-0016)——请先按 SKILL.md 的拆词规范"
            "把问题拆成关键词,再以列表传入:search(keywords=[\"词1\", \"词2\"])"
            % (keywords[:40] if isinstance(keywords, str) else keywords,))
    # ⚠️ **dict 同类拒掉**(v6.6 审查补):`dict` 是可迭代的,`for kw in {...}` 会遍历
    # **键**,于是 `search({"甲":1,"乙":2})` 静默变成 `["甲","乙"]` —— 与字符串入参
    # 同型的静默失效(调用方以为传了一个映射,内核当成了词列表)。这里不收映射,
    # 没有"键即词"的语义可言。
    if isinstance(keywords, dict):
        raise InternalError(
            "keywords 应为词列表,收到 dict: %r。若你确实想按映射传,请显式取键:"
            "search(keywords=list(你的映射))" % (list(keywords)[:5],))
    # ⚠️ **必须先把可迭代物化成列表**(v6.6 审查补):生成器只能消费一次。下面的
    # 校验要遍历两遍(算有效词数、算丢弃数),不物化会让生成器在第二遍为空,
    # 于是有效词被误报成"全是空串/空白",hint 完全指错方向。
    keywords = list(keywords)
    for kw in keywords:
        clamp_query(str(kw), UPSTREAM_TEXT_MAX, strict=True)
    # ⚠️ **空串/空白词一经丢弃必须可见**(v6.6 审查补,§A4):`["甲","",""]` 此前只发
    # 1 路而 `keywordsDropped:0`、scanNote 只字不提 —— 调用方看到的是"我一个词只召回
    # 1 路",实际是"你给的 3 个词里有 2 个是空的,内核替你扔了"。规格 §A4 的口径是
    # 「违规的不是说不,是**不说**」,故丢弃一律通报(下面把 blankDropped 交给编排层)。
    blanks = [k for k in keywords if not str(k).strip()]
    # 全是空白 = 没有词:`[""]` / `["  ", ""]` 在 `_plan_routes` 里会被逐个 strip 后跳过,
    # 产出一条 **0 路的空清单**——结构合法、`ok:true`、`results:[]`,调用方只会以为
    # "官方没这类文档"。这正是本项目最忌的静默失效(与"空 text"同类),故与
    # `not keywords` 同处理:显式报错,让调用方知道是**它没给词**,而不是官方没有资料。
    if len(blanks) == len(keywords):
        raise InternalError("keywords 里没有有效检索词(全是空串/空白): %r"
                            "(内核不做拆词,空输入会产出 0 路空清单——宁可报错也不静默)"
                            % (keywords[:5],))
    pid = _norm_product_id(product_id)
    n0, t0 = _up_now(), time.time()
    res = _search_manifest(keywords=keywords, product_id=pid, blank_dropped=len(blanks),
                           global_=bool(global_), sorts_type=int(sorts_type), rate=rate,
                           include_other=bool(include_other))
    res["stats"] = stats = {"upstreamCalls": _up_now() - n0,
                            "elapsedMs": round((time.time() - t0) * 1000, 1)}
    # ⚠️ 日志必须在**投影之前**读 `stats`:投影后它可能已被声明删掉(那正是投影的
    # 目的)。此前写成投影之后读 `res["stats"]["upstreamCalls"]` —— 从声明删 `stats`
    # 会直接抛 KeyError,把"删声明即删输出"这条机制变成一条崩溃路径。
    # 绑到局部变量 `stats` 后,日志与声明彻底解耦。
    log("SEARCH:", "kw×%d" % len(keywords),
        "| routes", len(res.get("queries") or []),
        "| otherSkipped", res.get("otherSkipped"),
        "| returned", len(res.get("results") or []),
        "| upstream", stats["upstreamCalls"])
    # ⚠️ `stats` 注入之后**再过一次声明投影**(ADR-0016 决策 8):`stats` 也是
    # `topKeys` 里的顶层键,若只在 `_search_manifest` 里投影,它就绕过了声明——
    # 从声明删 `stats` 而真实输出仍带着它,等于声明对这一个键没有约束力。
    return project_top(res)


def read(kind, oid, rate=None):
    """按类型读全文:kind ∈ knowledge | question(问答帖全文,传帖子号) | article。

    深读结果直接返回,不写穿落地缓存(ADR-0011:内核不落盘)。
    `refresh` 形参已删除(2026-09-18):内核恒在线,该形参在原实现里恒无效。
    `budget` 形参已删除(v6.6):预算机制整套移除(ADR-0016 决策 3)。
    ⚠️ kind 一律照抄清单条目的 `type` 字段——问答档是 `question`(2026-09-27 改名),
    清单条目的 `id` 就是帖子号,直接传即可。
    rate       限速档名(默认 interactive)。
    返回:上游全文包 + stats{upstreamCalls,elapsedMs}。kind/id 非法 raise InternalError。
    ⚠️ 问答帖的 `truncated` 是**字符串枚举**(ADR-0016 决策 7):`"answer_limit"`
    (帖子太长没读完,接受现状并在答案里声明)或 `"upstream_error"`(**上游报错,该重试**)。
    两者必须分开——把"上游坏了"与"资料就这么多"压成同一个值,会让召回置信度判定
    在最该保守的时候给出乐观结论。
    ⚠️ 该标记是**给调用方看的诊断**,不得出现在给用户的答案正文里(见 ANSWER-SPEC)。
    """
    kind = str(kind or "knowledge").lower()
    # ⚠️ 校验分两层(2026-09-29,工单 #32):
    #   ① 不是任何已知类型 → 真·非法 kind(调用方传错了);
    #   ② 是已知类型但没有全文端点(`other`)→ **合法但不可读**。
    # 两者必须分开报:合并成一句 `bad kind` 会把"这一档没有端点"引向
    # "我是不是传错了 kind"的方向,而调用方其实是照抄清单 `type` 的**正确用法**。
    # ② 的分支在 `_detail` 里(它才是"有哪些端点"的知情人)。
    if kind not in ENTITY_KINDS:
        raise InternalError("bad kind: %s(%s)" % (kind, "|".join(ENTITY_KINDS)))
    if oid is None or str(oid).strip() == "":
        raise InternalError("id required: 传 search 结果条目的 id")
    n0, t0 = _up_now(), time.time()
    d = _detail(kind, oid, rate=rate)
    d["stats"] = {"upstreamCalls": _up_now() - n0, "elapsedMs": round((time.time() - t0) * 1000, 1)}
    # ⚠️ 日志必须在**投影之前**读 `contentText`(用 `.get`):投影后它可能已被声明删掉
    # (那正是投影的目的),写 `d["contentText"]` 会把"删声明即删输出"变成一条崩溃路径
    # ——与 search 侧日志行读 `stats` 的那次踩坑同型。
    log("READ[%s]:" % kind, oid, "| len", len(d.get("contentText") or ""))
    # ⚠️ `stats` 注入之后**再过一次声明投影**(2026-10-01,D12):`stats` 也是
    # `read.topKeys` 里的键,而 `_detail` 的投影发生在注入**之前** —— 若只投影那一次,
    # 从声明删 `stats` 而真实输出仍带着它,声明对这一个键就没有约束力。
    # 与 `_public.search` 的两段式同构(那里是 `_search_manifest` 返回前一次 +
    # 本函数注入 `stats` 之后一次)。
    # ⚠️ 走 `_detail_mod.project_read` 的**属性访问**、而不是 import 期的名字快照:
    # 快照拦不住"替换模块属性"的注入(本仓已踩过:`from ._net import _get_json` 那次,
    # 见 `_detail.py` 顶部论证),而投影正是必须可注入的那一件 —— 回归用例要靠替换它
    # 证明"声明即闸门",不是在测一句断言。
    return _detail_mod.project_read(d, kind)


__all__ = ["search", "read"]
