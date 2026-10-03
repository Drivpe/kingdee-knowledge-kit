#!/usr/bin/env python3
"""kd._impl._upstream —— 上游检索调用与条目规范化。

`_norm_item` 是**上游响应形状**与**本套件条目形状**之间唯一的翻译层:
上游的每个历史怪癖(点号同层键、双 id 空间、字符串布尔、entity-type 大小写)
都在这里收口,别处不得再解析上游原始字段。

⚠️ **`answer` → `question` 的唯一映射点**(决策 D5/D6,2026-09-27):
上游协议里问答条目的 `entity-type` 仍是 `"Answer"`,而本套件对外一律用 `question`
(与 `read` 的 `kind` 同集合)。这个翻译**只在本文件的 answer 分支发生**,此文件之外
的代码、文档、CLI 一律不得出现 `answer` 这个上游原始值。

⚠️ **`upstream_type_of` / `_UPSTREAM_TYPE_OF` 已于 v6.6 删除**(ADR-0016 决策 3):
`--type` 参数删除后,"按类型过滤上游条目"这条通路失去唯一入口,该映射表变成
零消费者的死代码。清单不再做类型过滤——调用方从清单条目的 `type` 字段自己筛
(ADR-0016 决策 3 的明示取舍)。
"""
import urllib.parse

from . import _net
from ._config import UPSTREAM_TEXT_MAX, VIP
from ._links import compose_url
from ._net import clamp_query
from ._text import _is_true, _title_of, html2text

# ⚠️ **链接规则已于 2026-10-01(K1)整体搬到 `_links.py`** —— 那里是条目对外链接的
# **唯一归属地**:模板(`_URL_OF` 连同 22/22 可点、单数短形式 22/22 死、复数无 aid 段
# 4/4 死、回答号必须精确、两个方向的误判陷阱、模板与可点性是两件事、`other` 档无模板
# 的收口那份论证)、必需段规则、回答号选取(`_pick_answer_id`,零算法契约)与政策闸门
# (`link_for_item`)都在那一处。本模块只**调用** `compose_url`,不再自己拼 url ——
# 原先"该取哪个回答号"在清单侧与 read 侧各推导一遍,实测同一帖给出不同结果。
#
# ⚠️ **本模块的调用点刻意不加政策闸门**:`_manifest_project` 仍是清单路径的**唯一**
# 闸门(见 `_manifest.py` 的 `_manifest_project` —— 它才是清单路径的唯一生效点)。
# 这里加一道就变成两个闸门 —— 政策有两处生效点,
# 单一来源即失效。故 `_norm_item` 用 `compose_url`(数据形状层),不是 `link_for_item`。
#
# ⚠️ 模板表 `_URL_OF` **已不在本模块定义** —— 它在 `_links.py`(该名字仍由
# `_impl/__init__.py` 从那里**再导出**,故观测口 `core._impl()._URL_OF` 与既有回归
# 断言不受影响)。本模块不再 import 它:留着不用的 import 就是"第二处真相"的入口。
# ⚠️ 同批搬去的 `_question_url` 已在 `_links.py` 内**删除**(重构后零读点),
# 本模块也从未引用它 —— 见 `_links.py` 末尾的留证注释。

# ⚠️ 原有一个 `_KNOWN_ET` 映射常量,已删除(2026-09-29,code review L8):
# `_norm_item` 用的是 `if et == "..."` 字面量分支,**那个常量全仓零读点** ——
# 留着一份"看起来是映射表、实际没人用"的第二份真相,比没有更具误导性。
# 认不出的值不再丢弃,而是归入 `other`(工单 #32)。

# `entity-type` 归入 `other` 档时,**不按类型细分** —— 对外只有一个 `other`。
# 上游原始值仍如实保留在条目的 `upstreamType` 里(信息不丢,便于调用方自己分辨)。


def _norm_item(x, et):
    """上游原始条目 → 本套件条目。`et` 是上游的 `entity-type`。

    ⚠️ **`et` 的契约(2026-09-29 收紧)**:调用方(`_manifest._norm`)须先做
    **宽容归一**(NFKC 折叠 + strip + lower)。本函数内部仍按小写字面量比较 ——
    这是刻意的(映射表就是一份小写字面量表),但**不再假设"调用方一定会做"**:
    拿不到已知值时返回 None,而"丢了什么"由 `_norm` 的调用链负责可见性
    (见 `_manifest._norm` 的宽容层与 `other` 档)。

    历史教训(工单 #31):原契约写着「`et` **已小写**」而**函数内零防御** ——
    把调用方的 `.lower()` 去掉会退化为全量静默丢弃,而离线用例如测不出来
    (直调本函数的用例自己传小写,绕过了归一那一层)。

    ⚠️ **帖子号即 `id`**(决策 D6,2026-09-27):问答条目的 `id` 就是**帖子号**
    (上游的 `questionId`),不是回答 id。理由与后果:

      * 清单是**帖子级**的(ADR-0014):一个帖子下的多条回答在上游是多个独立条目,
        合并为一条后回答数走 `answersCount` 原生信号;故条目的 `id` 必须是帖子号,
        否则同一帖会各自成条(旧形态),且读取时还得回答"该传哪个 id";
      * 上游同时给的回答 id 与 `questionId` 两个 id 空间,在这里**收口成一个**:
        `questionId` 字段整体删除,`id` 取帖子号。
      * `url` 用**长形式**(帖子号 + 回答号)—— 拼法见 `_links.compose_url`
        (2026-09-29 改判为长形式;2026-10-01 K1 起归属 `_links`)。

    ⚠️ **`comments` 三类都取**(D2 修复,v6.6):上游**三类条目都给** `comments`
    (实测同一响应内 0/2/3),而原实现只在 answer 分支取它——knowledge/article 的
    该键被**归一化白丢**(上游给了、我们扔了,无任何信号)。三处分支现在都取。
    """
    hl = x.get("highlight") or {}
    classes = [c.get("name") for c in (x.get("classifies") or []) if c.get("name")]
    if et == "knowledge":
        kid = str(x.get("knowledgeId") or x.get("id") or "")
        return {"type": "knowledge", "id": kid,
                "url": compose_url("knowledge", kid),
                "title": _title_of(hl.get("title"), x.get("title")),
                "snippet": html2text(hl.get("content") or x.get("summary") or "")[:400] or None,
                "comments": x.get("comments"),
                "products": classes[:3]}
    if et == "answer":
        # ← 上游 "answer" 在这里翻译成对本 `question`;**这是全仓唯一的映射点**。
        q = x.get("question") or {}
        qid = str(x.get("questionId") or q.get("id") or "")
        # ⚠️ **回答号**:上游 search 的 answer 条目里两个 id 都有 ——
        # `questionId` 是帖子号、`x["id"]` 是**回答号**(2026-09-29 起取用)。
        # 原实现只取前者、把回答号丢掉,于是拼不出可点的长形式(`/question/<qid>` 恒死)。
        aid = str(x.get("id") or "")
        return {"type": "question", "id": qid,
                # 长形式 `/questions/<帖子号>/answers/<回答号>`;回答号缺失时给 None
                # (不给短形式——它 22/22 不可点,给了就是死链)。**模板与必需段规则
                # 现在只在 `_links.py` 一处**;这里如实建 url,政策闸门在 `_manifest_project`。
                #
                # ⚠️ **本条目的 url 在清单主链上会被帖级合并重算**(2026-10-01,独立终审
                # Standards 轴指出后核实):`_manifest_merge` 对 `type=="question"`
                # **无条件**重算 url(因为帖级要按"采纳优先"在**同一帖的多条回答**里挑
                # 一条,而单条回答不知道自己是不是被采纳的那条)。故本行在**端到端 search**
                # 上的值不直接进入输出 —— 它的作用是:
                #   ① 回答级条目**本身是合法的中间形态**(`_norm_item` 也是公开观测口
                #      `core._impl()._norm_item`,直调它的判据读这一行);
                #   ② knowledge / article 两档**没有**帖级合并那一步,本行的值就是
                #      最终值(实测:两档 norm 的 url 与 merge 的输出逐字相同)。
                # 之所以不删:删了会让"归一即产出合法条目"这条契约在 question 档破例,
                # 且会让直调 `_norm_item` 的判据失去被测对象。**留着但如实记下**,
                # 比给一个看起来有消费者的字段好(同 `total` 那次的处置原则)。
                "url": compose_url("question", qid, [(aid, None)]),
                # ⚠️ 回答号随条目走,供 `_manifest_merge` 在**帖级合并**时按
                # "采纳优先"选一条来定 URL(同帖多条回答各有自己的回答号)。
                # 它是**内部字段**(前导下划线),不在 `contract.json` 的条目字段集里,
                # 故由 `_manifest_project` 按白名单投影时自然被丢掉。
                "_answerId": aid,
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
                "url": compose_url("article", arid),
                "title": _title_of(hl.get("title"), x.get("title")),
                "snippet": html2text(hl.get("content") or x.get("summary") or "")[:400] or None,
                "comments": x.get("comments"),
                "products": classes[:3],
                "supports": x.get("supports")}
    # ---- `other` 档:收容 3 个已知值之外的**每一种** entity-type(工单 #32)----
    #
    # ⚠️ **这是本缺陷的正面修复**:原实现到此 `return None` —— 上游给的
    # `LearningCourse`(课程)/ `LearningPath`(学习路径)/ `KnowledgeSpecial`(专题)/
    # `LearningBroadcast`(直播)等**一律静默丢弃**,且丢弃**没有任何信号**。
    # 端到端实测后果:搜「微课」→ `total: 212`(**当时口径**;该顶层字段已于 v6.9 删除,
    # 是历史的观测值而非现状)而 `results` 只 2 条、`routeErrors: []`、
    # `scanNote` 写"1/1 路完成" —— 三处矛盾同时出现,调用方会告诉用户"官方没这类资料"。
    # 这是本项目最忌讳的失效形态:**把"我们没读懂"伪装成"上游没有"**。
    #
    # ⚠️ **默认隐藏**(调用方需显式开关才看得到):用户判定这些类型
    # 「质量很低,得和前面三种类型区分开来」—— 它们确实不是同一种资料
    # (课程是 video/document/ppt 课件,学习路径是一串课程的目录)。
    # 隐藏时**必须告知跳过了多少条**(见 `_search_manifest` 的 `otherSkipped`),
    # 否则就是"用一处静默换另一处静默"。
    #
    # ⚠️ **id 形状不固定**(实现时须知道):课程 id 既有小整数(`"6898"`)也有雪花串
    # (`"200641239941605888"`),同一次响应内混排;上游另给 `xId`
    # 形如 `"LearningCourse-6898"`。**不得**按长度或字符集判形状。
    # 取值顺序:`id` → `knowledgeId` → `xId`(去类型前缀),尽量给出可追溯的标识。
    xid = str(x.get("xId") or "")
    oid = str(x.get("id") or x.get("knowledgeId") or "")
    if not oid:
        # `xId` 形如 "LearningCourse-6898" —— 去掉类型前缀再当 id。
        oid = xid.split("-", 1)[1] if "-" in xid else xid
    return {"type": "other", "id": oid,
                # ⚠️ **保留上游原值,由 linkPolicy 抑制**(2026-09-29 修正 H2):
                # 原实现硬编码 `url: None`,于是 `linkPolicy.other = "no-link"`
                # **永远读不到** —— 政策既无生产者也无消费者,违反本仓硬纪律
                # 「声明必须有消费者,否则'单一来源'是假的」。
                # 现在如实透传上游 url(实测 `LearningBroadcast` 带第三方域
                # `live.vhall.com`;课程/路径/专题多数没有该字段),
                # 由 `_manifest_project` 的 `apply_link_policy` 按政策抑制 ——
                # 这正是模板与可点性"两件事"的原则:模板如实反映上游数据形状,
                # **是否交给读者由政策决定**(见 `_URL_OF` 的论证)。
                "url": x.get("url"),
                # ⚠️ `xId` 一并保留(**去重键的兜底**,2026-09-29 修正 C1):
                # `other` 是**跨类型收容桶**,而各类型 id 空间互不相干、形状相同。
                # 若两条不同实体恰好 id 相同(实测课程与学习路径都用雪花串,
                # 课程还有 `6898` 这种小整数),`_manifest_key` 会把它们折成一条 ——
                # 那就是"用一处静默换另一处静默"。`xId` 带类型前缀,是最后一道区分。
                "xId": xid,
            "title": _title_of(hl.get("title"), x.get("title")),
            "snippet": html2text(hl.get("content") or hl.get("description")
                                 or x.get("summary") or x.get("subtitle") or "")[:400] or None,
            "comments": x.get("comments"),
            "products": classes[:3],
            # ⚠️ **上游原始类型名如实保留**:对外只有一个 `other`,但调用方
            # 需要知道"这到底是什么"才能自行判断要不要看。丢掉它就等于
            # 把"我们认得但不当一类"又变回"看不出是什么"。
            "upstreamType": str(x.get("entity-type") or ""),
            # 课程类常见资源形态(video/document/ppt);其余类型为 None。
            "resourceType": x.get("resourceType")}
    return None


def _search_upstream(text, product_id, page, page_size, global_, sorts_type, rate=None):
    """单次上游检索。所有路最终都经这里,故参数语义必须逐条准确。

    product_id:0 或 None = **不过滤**——必须省略参数。传 `productIds[0]=0`
    上游会当真值过滤(实测把 Knowledge 挤出前排),这是"不过滤"与"过滤到 0 号产品"
    的语义分界。
    global_:跨全部产品的开关,由调用方透传;上游只认字符串 "true"/"false"。

    ⚠️ **本函数是「检索侧」的唯一网络出口**(A6 的注入点):测试把它换成假实现即可
    从 `core.search` 真跑完整链路而不触网。原先为"让测试替换生效"而存在的
    `_manifest._resolve()` 字符串查表已随 v6.6 删除——网络出口改为**显式注入**,
    不再依赖包属性猴补丁。

    ⚠️ **措辞更正(2026-09-29)**:原文写"本函数是**全内核**唯一的网络出口",
    那**是错的**——深读侧(`_detail`)的 5 个 `_get_json` 调用**完全不经过它**。
    唯一真出口是 `_net._get_json`(见该模块 docstring);
    本函数只是**检索侧**的业务语义注入点。

    ⚠️ 原 `type_` / `budget` 形参已删(v6.6):`type_` 在函数体内**零使用**
    (上游无类型过滤参数,过滤曾是收响应后做的),`budget` 随预算机制整体删除。
    """
    text = clamp_query(text, UPSTREAM_TEXT_MAX)  # 入口压回:上游 100 字硬闸
    params = {"text": text, "page": page, "pageSize": page_size,
              "global": "true" if global_ else "false", "sortsType": sorts_type}
    if product_id and int(product_id) != 0:
        params["productIds[0]"] = int(product_id)
    # ⚠️ 属性访问而非 `from ._net import _get_json`(2026-09-29 出口统一):
    # 快照形态下替换 `_net._get_json` 拦不住这里的请求——正是离线泄漏的成因之一。
    return _net._get_json(VIP + "/api/search?" + urllib.parse.urlencode(params), rate)
