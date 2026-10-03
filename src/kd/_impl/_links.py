#!/usr/bin/env python3
"""kd._impl._links —— 条目对外链接的**唯一归属地**(模板 / 必需段 / 回答号选取 / 政策闸门)。

为什么必须是一个模块(2026-10-01,K1):模板原先散在 `_upstream._URL_OF` +
`_question_url`,而"该取哪个回答号"被**推导了两遍**:
  * 清单侧走 `_manifest_merge` 的回落链(采纳优先 → 上游首条);
  * read 侧走 `_question_detail` 的 `bestAnswer[0]` **单点取法**。
两侧对**同一个帖子**给出不同结果,实测复现:同帖两条都未被采纳的回答
(`A1`/`A2`),`search` 给 `…/questions/<qid>/answers/A1`,而 `read` 给 `None`。
后果落在 ANSWER-SPEC 那条纪律上(它让 agent **照抄 `url` 字段**):同一个帖子,
列清单时给得出链接、写正文时却给不出。

现在只有这里知道"链接长什么样"。改链接规则 = 改本模块;别处不得再拼 url。

⚠️ **三档分层的分工(别把闸门提前)**:
  * `compose_url` —— **数据形状层**:如实指向上游内容页。**不含**政策闸门;
  * `link_for_item` —— **读者拿到的形态**:`compose_url` + `apply_link_policy`,
    政策闸门住在这里,故调用点不可能忘;
  * `_pick_answer_id` —— 回答号选取(零算法契约,见其 docstring)。

⚠️ 清单路径(`_manifest_project`)仍是**它那条通路的唯一闸门** ——
`_upstream._norm_item` 各处经 `compose_url` 如实拼出 url、**不加**闸门,
再由 `_manifest_project` 按政策抑制(见 `_manifest.py` 的 `_manifest_project`,
那里是清单路径的链接政策唯一闸门:它把 `compose_url` 的产物交给
`apply_link_policy`)。在那里也加闸门即变成**两个闸门**,政策就"有两处生效点",
违反单一来源。
"""
from ._config import VIP, apply_link_policy

# 条目对外链接模板。可点性**按路径而异**(唯一真源 = contract.json 的 linkPolicy):
# `knowledge/`、`article/`、`question/` **都给链接**。
#
# ⚠️ **问答的模板是长形式,不是短形式**(2026-09-29,链接实测):
#   * `/questions/<帖子号>/answers/<回答号>` —— 实测 **22/22 可点**;
#   * `/question/<帖子号>`(单数) —— 实测 **22/22 不可点**(该路径整体不存在);
#   * `/questions/<帖子号>`(复数、无 aid 段) —— 实测 **4/4 不可点**
#     (路径存在,但**缺必需的回答号段**)。
#   **回答号必须精确**:`+1` / `1` / `0` 均落 `/error/404`(站点做精确校验)。
# 故本模板有**两个**占位符,且任一为空时**不得拼出短形式** —— 短形式恒死,
# 拼它等于给读者一个死链(见 `compose_url` 的必需段规则)。
#
# ⚠️ 模板本身与可点性是**两件事**:模板恒按上表产出(它如实指向上游的内容页路径),
# 是否把该 url 交给读者由 linkPolicy 决定。
#
# ⚠️ 陷阱(留证,以免重复踩;两个方向的误判都**实际发生过**):
#   * `question/` 的**最终 HTTP 状态码是 200**(成功重定向到 404 页),只看状态码会
#     把失效链接误判为可用——必须看 `url_effective`;
#   * `article/248777993676668672` 首跳 **302**,但最终 URL 是
#     `/knowledge/248777993710223104` 且该页**可点**(被迁移成知识文档)。
#     只看首跳状态码会把可点链接误判为失效——09-18 与 09-27 两份文档都这么记错过。
_URL_OF = {"knowledge": VIP + "/knowledge/%s",
           "question": VIP + "/questions/%s/answers/%s",
           "article": VIP + "/article/%s"}
# ⚠️ **本表只含恒有链接的三档,`other` 刻意不设模板**(2026-09-29 收口)。
# `other` 档恒 `no-link`,故任何模板都不会被交给读者 —— 而表中原先确实有一行
# `"other": None`,其注释自称"`apply_link_policy` 需要一个可被抑制的值"。
# **那句话与代码不符**:`apply_link_policy(kind, url)` 收的是**条目里的 `url`**
# (由 `_norm_item` 如实透传上游原值、由 `_manifest_project` 抑制),
# **从不读 `_URL_OF`**。实测(哨兵实验:把 `_URL_OF["other"]` 换成哨兵串,
# 两种政策各跑一轮)哨兵**从未出现在输出里**;内存删键后与基线做键级 diff,
# `results` **逐字段相同**。即该键是**生产死键**,唯一读点是测试的存在性断言 ——
# 本仓已把"只有测试读"判为病(`CONTEXT.md` 的契约声明条,同型病在 `topKeys` 上
# 复发过一次),故正确收口是**删键**,不是给它补一个假消费者。
# 证据链未丢:`other` 无可点网页形式的实测记录在
# `docs/research/2026-09-29-额外类型链接形式复验.md`,并已完整承载于
# `contract.json` 的 `linkPolicy.evidence.other`。


def _s(x):
    """段值归一:None → `""`、其余 `str()` 后 strip。空结果即"该段缺失"。

    ⚠️ **口径来源**:这是原 `_question_url` 里那两行(`str(qid or "")` /
    `str(aid or "")` + `.strip()`)的等价形式,抽出成函数只为**三档共用同一口径**。
    ⚠️ **边界如实记**(2026-10-01 独立终审 A-1 指出,勿再自称"逐字等价"):
    knowledge / article 档原先是**裸插值**(`_URL_OF["knowledge"] % kid if kid else None`),
    **不做 strip**;现在经本函数也 strip 了。这是**唯一一处行为差异** ——
    实测无实际影响(上游 id 恒为无空白的数字串),且方向是保守的:带空白时
    宁可归一,而不是把一个含空格的死链交给读者。
    """
    return str(x or "").strip()


def _pick_answer_id(answer_ids):
    """回答号选取`[(回答号, 是否采纳), …]` → 一个回答号或 `None`。

    **零算法契约 —— 已裁定的一处放宽,不得再自称"逐字等于"**(2026-10-03):
    规则的**基准**是 `_manifest_merge` 原先那条帖级回落链,内核不排序、不打分、
    不猜"哪条更像解":
      1. **采纳回答优先**(`isAdopt` 为真的那条)—— 链接应当指向"解"本身;
      2. 无采纳时回落**调用方给的第一条** —— 上游本身按综合排序返回,首条即最好的
         默认落点(`ns` 的顺序就是上游返回顺序,内核一个字都没动过);
      3. 候选集为空、或候选里没有可用的回答号 → `None`(不给链接)。

    ⚠️⚠️ **相对 HEAD 的行为放宽(口径来源:2026-10-03 用户裁定"承认放宽")**:
    原实现的两段是 `str(qid or "")` / `str(aid or "")` **先归一后判空**,而帖级回落链
    在 `_manifest_merge` 里走的是**真值过滤** —— `"   "` 为真 ⇒ 不跳过。本函数把
    归一化(`_s()`)放在**选取之前**,于是"空白段"被视为**该段缺失**而跳过,
    落到下一个非空候选。这就是"首个**可用**回答号"取代"首条候选"的那一档新判断。
    实测两版对照:

    | 构造 `answer_ids` | HEAD 版回落链 | 本函数 |
    |---|---|---|
    | `[("   ",False),("11",False)]` | `None` | `"11"` |
    | `[("   ",False)]` | `None` | `None` |
    | `[("11",False),("12",False)]` | `"11"` | `"11"` |

    **生产不可达**:真实上游的回答号恒为**无空白的数字串**,故这一档判不出差异;
    它是**契约/实现不符**,不是线上故障(勿把它写成"线上 bug")。
    保留放宽的理由:语义上"跨过缺段找一个可用回答号"优于"整帖给不出链接"
    (后者让读者白损失一次跳转),且方向与 `compose_url` 的必需段规则一致 ——
    带空白时宁可归一、宁可跳过,也不把含空格的死链交给读者。
    守护用例:`t_pick_answer_id_multi_candidate_blank_skip`(多候选构造,变异必红)。

    ⚠️ **每个回答号都必须经 `_s()` 归一**(2026-10-01 收口;独立终审 A-1 抓出):
    原 `_question_url` 对**两段**都做 `str(...).strip()`,而本模块初版只对
    `item_id` 走 `_s()`、`answers` 里的回答号直接用裸值 —— 同一函数两条路径口径
    不一。危害不是措辞问题:
      * `aid="   "`(纯空白):原实现 strip 后为空 → `None`;**不收口则拼出
        `/questions/<qid>/answers/   `** —— 一个**形状合法但打不开**的链接,
        正是 `linkPolicy.forbidden` 要防的那一类(读者以为资料在那里);
      * `aid=" 11 "`:拼出带空格的 URL(上游做精确校验,等价于给错回答号)。
    归一化放在**本函数内**,因为清单侧与 read 侧**都经它**选回答号 —— 放在任一个
    调用点都只覆盖一半,那正是"同一事实两处推导"的老病。

    ⚠️ **两侧必然同号的理由**(收敛机理,别以为还是两套规则):
      * 清单侧:`_manifest_merge(ns)` 的 `ns` 是**上游返回顺序**下的同帖条目;
      * read 侧:`_detail._question_detail` 对回答列表做过
        `answers.sort(key=lambda a: (not a["adopted"]))` —— 即"采纳在前、
        其余保持上游顺序",与 ①② 语义**相同**。
      故对同一构造,两侧选出的是同一个回答号(见 `t_link_single_source_*`)。
    """
    adopted, first = None, None
    for aid, is_adopted in (answer_ids or []):
        aid = _s(aid)
        if not aid:
            continue
        if first is None:
            first = aid
        if is_adopted:
            adopted = aid
            break
    return adopted if adopted is not None else first


def compose_url(kind, item_id=None, answers=None):
    """条目 → 上游内容页 URL(**数据形状层,不含政策闸门**);给不出则 `None`。

    模板选择:`kind` 有模板才拼(表里只有 knowledge / question / article 三档,
    `other` 无模板 → `None`)。

    **必需段规则**(缺任一段即 `None`,**绝不回落短形式**):
      * `question` 必需 **帖子号 + 回答号** 两段(`/questions/<qid>/answers/<aid>`);
      * `knowledge` / `article` 必需一段(实体 id)。

    回答号选取:**问答必须显式给候选集** `answers=[(回答号, 是否采纳), …]`,
    由 `_pick_answer_id` 按零算法契约选一条;候选集为空或未给 → `None`。
    ⚠️ **不得拿 `item_id`(帖子号)顶替回答号** —— 两个 id 空间不同,理由见实现处注释。

    ⚠️ **不得回落短形式**:`/question/<qid>`(单数)实测 22/22 死、复数无 aid 段
    4/4 死。"给不出长形式"时正确的做法是**不给链接**(读者损失一次跳转),
    而不是给一个必然打不开的地址(读者以为资料不存在)。
    """
    tpl = _URL_OF.get(kind)
    if not tpl:
        # ⚠️ `other` 档恒走到这里(表里刻意没有它的模板);它不是错误路径。
        return None
    if kind == "question":
        # ⚠️ **问答必须显式给候选集**(2026-10-01,Lead 复核 A 批时收口):
        # `item_id` 是**帖子号**,而回答号是**另一个 id 空间**(决策 D6),不得互相顶替。
        # 拿帖子号顶替回答号会拼出 `/questions/<qid>/answers/<qid>` ——
        # 一个**看起来合法、实则错误**的链接:它不像短形式那样恒死(读者不会
        # 立刻发现打不开),故危害比短形式更大。依据是 `linkPolicy.forbidden[1]`
        # 的同一条精神:**给不出正确的,就不给**。
        #   * 回答号已知 → `compose_url("question", 帖子号, [(回答号, None)])`;
        #   * 从候选集选 → `compose_url("question", 帖子号, [(回答号, 是否采纳), …])`。
        aid = _pick_answer_id(answers)
        qid = _s(item_id)
        if not qid or not aid:
            return None
        return tpl % (qid, aid)
    aid = _s(item_id)
    if not aid:
        return None
    return tpl % aid


def link_for_item(kind, item_id=None, answers=None):
    """**读者拿到的形态** = `compose_url` + `apply_link_policy`(政策闸门住这里)。

    `read` 侧一律走本函数:拿不到政策闸门就忘了加 —— 而"声明必须有消费者"
    是本仓硬纪律(`_config.apply_link_policy` 的 docstring 记着那次真实的漏挂)。
    清单侧走 `compose_url`,闸门由 `_manifest_project` 施加(见模块 docstring)。
    """
    return apply_link_policy(kind, compose_url(kind, item_id, answers))


# ⚠️ **原 `_question_url(qid, aid)` 已删除**(2026-10-01,K1 收口,Lead 复核后):
# 它原先承载"回答号已知时的长形式拼法",而重构后
# `compose_url("question", 帖子号, [(回答号, None)])` **逐字覆盖**了同一件事 ——
# 于是它**零生产读点、零回归读点**,只剩 `__init__` 的再导出在撑一个名字。
#
# 本仓判例明确:`_URL_OF["other"]` 因"唯一读点是测试的存在性断言"而被**删键**,
# 而不是补一个假消费者(见上方注释里的哨兵实验);此处同理,而且更彻底 ——
# 连测试都不读它。留一个没有消费者的名字,等于给下一个人一条"看起来是入口、
# 其实没人走"的岔路,而本仓的纪律正是「声明必须有消费者,否则'单一来源'是假的」。
#
# "回答号已知"的场景现在的正确写法:
#     compose_url("question", 帖子号, [(回答号, None)])   # 单元素候选集

