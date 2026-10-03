#!/usr/bin/env python3
"""kd._impl._config —— 单一真源常量、契约声明、限速、上游计数。

本模块只依赖标准库,是全包的依赖汇点:改版本号/路数上限/限速只改这里,
不会牵动检索逻辑。

⚠️ **v6.6 结构变更**(ADR-0016,2026-09-28):原 `query_routes.json`(拆解规则 +
预算 + 限速)整体删除,其仍有效的两个值(路数上限、限速档)并入 `contract.json`
的 `limits` 段——**包内数据文件从两个收敛为一个**。同时删除的还有:
  * **预算机制整套**(`_Budget` / `_BudgetExhausted` / `_cfg_budget_search_max` /
    `KSEARCH_SEARCH_BUDGET`):跨页扫描删除后每路恒发 1 次请求(实测 7 词 = 7 次),
    预算**永不可触发**,留着是死机制(ADR-0016 决策 3/5)。
"""
import json
import os
import random
import sys
import threading
import time

# ---- 上游入口(匿名链路,零 cookie) ----
VIP = "https://vip.kingdee.com"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/152.0.0.0"
HDRS = {"User-Agent": UA, "Accept": "application/json"}

# ---- 单一真源(守卫/回归钉住,勿在别处复制字面量) ----
# 版本号:pyproject.toml 的 version 与此处一致(6.6.0 = 6.6 的三段写法),
# __init__.__version__ 与 cli._VERSION 均从此处取。
# 6.9 = 删顶层 `total`(2026-10-01,**破坏性变更**):
#       顶层返回体不再带 `total`(`contract.json` 的 `topKeys` 与内置兜底键集同步删除,
#       真实输出跟着消失 —— 声明即渲染闸门,有回归钉子)。
#       理由:**它没有任何可行动的用法** —— 本仓文档明写「比对检索效果只看金标位次,
#       不看 total」;而它是**各路上游 `totalElements` 的最大值**,既不是清单长度、
#       也不是过滤后该类型的条数(实测 `total: 74` 而 `results: []`),读它只会得出
#       错误结论。留一个被自家文档劝退的字段 = 给调用方一个不该读的数。
#       差额解释的载体改为:`results` 长度 + `otherSkipped` + `keywordsDropped`
#       + `routeErrors`。
#       连带删除:`_route_search_once` 读上游 `totalElements` 那一段(顶层 `total` 是
#       它唯一的消费者,删完它就成了"生产者无消费者",与 `total` 同病灶)。
#       ⚠️ `answersTotal`(read 侧)是另一个概念,不动;测试桩里的 `totalElements`
#       是**上游响应形状**的模拟,保留。
# 6.8 = 台账校正轮收尾(2026-09-29,**含行为修复**):
#       ① **上游故障时已取回的结果不再被丢弃**(口径限定:针对**上游故障**整类 ——
#         网络不可达/超时/响应非 JSON/上游业务错误壳;程序缺陷仍穿透为内部错误)。
#         6.7 的首次修复只保住首页(实测同构造 HEAD 保 8 条、6.7 得 5 条、修后 8 条);
#         深读侧的失败分类与检索侧统一为单一来源(`_net.UPSTREAM_FAILURES`)。
#       ② 频率档位的兜底值收成共享单一来源,消除与限速器的静默分叉(档位为空 dict 时
#         `_burst()` 得 7 而 `wait()` 用 1);
#       ③ 删除一处无消费者的链接模板(声明必须有消费者);④ 删除回归套件两个绕道取值函数
#         (工单 #33 项三 3.2 收口);⑤ 文档一致性钉子纳入 `CONTEXT.md`。
#       ⚠️ 对外契约**未变**(顶层键集/字段集/linkPolicy 均不动),故非破坏性变更;
#       抬版本的理由是"实现行为有实质修复",而非契约变更。
# 6.7 = 内核行为出入修复与交互提速(ADR-0017,**破坏性变更**):顶层删 `routesDegraded`
#       (用户裁定「重复的就不要提示了」;它原先报得自相矛盾)、`search` 新增顶层
#       `otherSkipped` 与 `include_other` 形参、清单新增 `other` 档(罕见类型默认隐藏)、
#       `question` 的 url 改为带回答号的长形式且 `linkPolicy.question` 由 no-link 改 link、
#       `limits.rate` 上调到 burst 7 / rps 5、`limits` 新增 maxDetail、
#       `read` 删 `max_answer_pages`、网络出口统一为 `_net._get_json` 单点。
#       施工记录见 docs/specs/2026-09-29-内核行为出入修复与交互提速-施工规格.md。
# 6.6 = 检索词生成权移交调用层(ADR-0016,2026-09-28)**破坏性变更**:
#       内核不再生成任何检索词(拆词器/路序表/截断优先级整体删除)、CLI 删位置参数与
#       --type/--max-routes/--budget/--chunk、预算机制删除、--product 三态收两态、
#       清单字段集按类型分三份、read 截断改字符串枚举、topKeys 补消费者。
# 6.5 = 全面修复(ADR-0015):链接口径按 kind 分档、稀有数字 token 抢第 1 路、
#       字段集检查改三段对账、read 的 budget 契约统一、「按标题挑」判据入文档。
# 6.4 = 契约重构(决策 D4-D14):清单改**帖子级**、type/kind 统一改名 question、
#       产品线字面推导整体删除、清单分页删除、字段集收敛并收进 contract.json。
# 6.3 = 单入口检索(ADR-0013):kd ask 删除、公开面收敛为 search/read + 三异常、零算法排序。
VERSION = "6.9"

# 实体类型白名单:read 的 --kind 共用此集合(原 search 的 --type 已删除)。
# ⚠️ 第三个值是 `question` 而**不是上游协议里的 `answer`**(决策 D5):上游
# `entity-type` 仍是 "Answer",映射点**只在 `_norm_item` 一处**(见 _upstream)。
# 此集合之外任何地方出现 "answer" 都是未映射的上游原始值泄漏。
#
# ⚠️ **第四个值是 `other`**(2026-09-29,工单 #32):收容 3 个已知值之外的每一种
# 上游 `entity-type`(实测至少还有 `LearningCourse` / `LearningPath` /
# `KnowledgeSpecial` / `LearningBroadcast`)。在此之前它们被 `_norm_item`
# **静默丢弃** —— 搜「微课」得到 `total: 212` 而 `results` 只 2 条(该顶层字段自 v6.9
# 起已删除,此处是当时实测的返回体),且无任何错误信号。
# ⚠️ `other` 是**合法 kind**(清单里会出现它,故 `read` 不能拿它当非法值),
# 但它的全文端点**不存在**(上游各类型的详情端点形状不一,`LearningPath` 无端点、
# `LearningBroadcast` 在第三方域),故 `read(kind="other")` 须给出**准确**的提示,
# 而不是让调用方以为自己传错了 kind。
ENTITY_KINDS = ("knowledge", "question", "article", "other")

# 对外契约声明(**包内唯一数据文件**,决策 D13):字段集 / 禁止键 / 产品线编号表 /
# 内核运行参数(limits)。随包安装,装到用户机器上也能读到(测试/文档目录装过去
# 就没有)。读取失败回落内置兜底,**不抛错**:声明缺失时内核继续用兜底键集,
# 不让"少一个数据文件"炸掉检索主链路。
_CONTRACT_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                              "contract.json")
_CONTRACT = None
# 声明是否真的读到。None = 还没读过(首次读取时确定);读失败 → False。
# 消费者见 `contract_loaded()`(ADR-0016 决策 7)。
_CONTRACT_OK = None

# 上游 text 参数硬上限:100 原始字符(含标点/空格/换行,均计 1)。
# 超限返回 HTTP 200 + {"errorCode":409,...},body 无 totalElements ——
# 不识别就会把"查询超限"静默降级成"无匹配结果"(2026-09-16 实测)。
UPSTREAM_TEXT_MAX = 100

# 内核不落盘(ADR-0011 决策 3):落地缓存与包内日志写盘整体摘除。
# log() 保留为**写 stderr**而非删除:它是 core 内部通用观测点(约 10 处调用),
# 删函数会把"日志"这个关注点炸进每个调用点。契约:stdout 只出 JSON,日志走 stderr。
def log(*a):
    """观测日志 → stderr(不落盘、不写 ~/.kd/)。失败静默——日志不能影响检索主链路。"""
    try:
        sys.stderr.write("[kd] " + " ".join(str(x) for x in a) + "\n")
    except Exception:
        pass


def _contract():
    """对外契约声明(字段集 / 禁止键 / 产品线编号表 / 内核运行参数)。读失败回落空表。

    ⚠️ 读失败**不只是日志**(ADR-0016 决策 7,v6.6 审查补):回落空表会让链接政策
    回落"全部不给链接" → 全部 url 静默变 null,而调用方无从分辨"官方这些条目没有
    链接"与"内核没读到声明"。故把读状态记在 `_CONTRACT_OK` 上,由
    `contract_loaded()` 供返回体标注(`contractCfgLoaded`)。
    """
    global _CONTRACT, _CONTRACT_OK
    # ⚠️ **声明只在本进程的首次读盘时进缓存**(`_CONTRACT is None` 只成立一次)。
    # 由此有一条**刻意保留、不是 bug** 的进程内不对称(2026-10-01,D1):
    #   * "**不传** `product_id`" 用的是**签名默认值**(`_public` 的
    #     `_DEFAULT_PRODUCT_ID = default_product_id()`),它是 **import 期**求值的快照;
    #   * "**显式传** `None`" 走 `_norm_product_id` → **调用期**现读 `default_product_id()`。
    # 两者在进程内**不同源** —— 但生产上根本看不见:进程内改 `contract.json` 不会被
    # 任何人读到(本函数已把声明缓存住),CLI 每次调用又都是新进程;要构造出这个分叉,
    # 必须**手动清掉 `_CONTRACT`** 再调用(报告里那次实测正是测试构造)。
    # 故**不要**"修"它:退回 import 快照会让 `default_product_id()` 重新变成零消费者的
    # 函数(违反本仓「声明必须有消费者,否则'单一来源'是假的」),改用哨兵默认值又会
    # 撞红断言「签名默认值必须等于声明默认值」。ADR-0016 决策 3 那句"不传与传 `None`
    # 同义"在**进程之外完全成立**。
    if _CONTRACT is None:
        try:
            with open(_CONTRACT_PATH, encoding="utf-8") as f:
                _CONTRACT = json.load(f)
            _CONTRACT_OK = True
        except Exception as e:
            log("contract.json load fail:", str(e)[:120])
            _CONTRACT = {}
            _CONTRACT_OK = False
    return _CONTRACT


def contract_loaded():
    """声明是否**真的读到**(读失败时 False)。

    消费者:顶层 `contractCfgLoaded`(ADR-0016 决策 7)。不要拿"字段集非空"代替它
    ——`_contract()` 读失败会回落空表,而各处读取又都各自回落内置兜底值,于是
    "读失败"与"读到了但内容恰好一样"在字段层面不可区分。
    """
    _contract()  # 触发一次读取,确保状态是当前的
    # ⚠️ 测试会直接给 `_CONTRACT` 赋值做注入(那时代码路径上"声明"是存在的),
    # 故 `_CONTRACT_OK is None`(从未走过读盘)按"已加载"处理 —— 否则注入型用例
    # 会让这个键变 False,把"测试注入"误报成"声明读失败"。
    return bool(_CONTRACT_OK) if _CONTRACT_OK is not None else bool(_CONTRACT)


def _limits():
    """内核自己的运行参数(非对外契约)。读失败回落空表。"""
    v = _contract().get("limits")
    return v if isinstance(v, dict) else {}


def max_keywords():
    """单次 search 收词上限(默认 7)。

    实测依据:7 个词 = 7 次上游请求 = 3.33 秒。超限行为见 `_search_manifest`:
    **按调用方给的顺序取前 N 个**,并在返回体里写明 `keywordsDropped`。

    ⚠️ 兜底值必须取自 `_FALLBACK_MAX_KEYWORDS`,**不得**在这里另写一个字面量
    (v6.6 审查修):此前两处都写裸 `7`,而离线用例钉的是那个**常量** ——
    于是"声明缺失时的兜底值"与"用例证明过的兜底值"是**两份**,改一处不会红。
    CONTEXT.md:35 点名的同型病(「声明必须有消费者,否则'单一来源'是假的」)。
    """
    try:
        v = _limits().get("maxKeywords")
        return int(v) if v else _FALLBACK_MAX_KEYWORDS
    except Exception:
        return _FALLBACK_MAX_KEYWORDS


def max_detail_knowledge():
    """问答深读里**逐条详情展开**的条数上限(默认 5)。

    ⚠️ **2026-09-29 新增声明**(工单 #30)。原先它是 `_question_detail` 的一个
    默认值 5 的形参,而**全仓无任何调用方传值**、也不在任何声明里 ——
    即"碰巧等于实测最大值"的魔数(实测 25 条帖子的回答数:中位 2、90 分位 4、最大 5,
    正好卡在边界上,再多一条就会静默截断)。

    同批**删掉**的另一个数字是翻页上限 `max_answer_pages=3`,它**永不可触发**
    (10 个问答帖全 `totalPages=1`;要触发需单帖 >60 回答,实测 0 条)。

    为什么把它收进声明而不是留着当形参默认值:项目自己的纪律是
    「**声明必须有消费者,否则'单一来源'是假的**」(CONTEXT.md 契约声明条);
    一个没有任何调用方、也不在声明里的数字,谁都不知道该不该改、改了会怎样。
    """
    try:
        v = _limits().get("maxDetail")
        return int(v) if v else _FALLBACK_MAX_DETAIL
    except Exception:
        return _FALLBACK_MAX_DETAIL


# 清单条目的**内置兜底键集**:仅在 contract.json 缺失/无该段时生效。
# 为什么留兜底而不是"声明缺失就报错":字段集是渲染细节,不是安全闸;
# 为它中断检索等于让一个数据文件决定套件能否工作。
# ⚠️ 兜底集必须与 contract.json 的字段声明保持一致 —— 由离线回归用例钉住
# (它同时读两边,任一处漂移即红),不靠人工誊抄。
_FALLBACK_COMMON_KEYS = ("type", "id", "title", "url", "snippet", "products",
                         "comments", "hitRoutes", "routes")
_FALLBACK_BY_TYPE_KEYS = {
    "knowledge": (),
    "question": ("adopted", "answersCount", "questionBody"),
    "article": ("supports",),
    # ⚠️ `other` 档(2026-09-29,工单 #32):见 `ENTITY_KINDS` 的论证。
    # `upstreamType` 必须保留 —— 它是"这到底是什么"的唯一线索。
    "other": ("upstreamType", "resourceType"),
}
_FALLBACK_FORBIDDEN_KEYS = ("contentText", "fusedScore", "chunks", "contentLen", "useful",
                            "views", "updatedAt", "questionId")
_FALLBACK_MAX_KEYWORDS = 7
_FALLBACK_BURST = 7
_FALLBACK_MAX_DETAIL = 5
_FALLBACK_TOP_KEYS = ("ok", "keywords", "queries", "routesPlanned",
                      "effectiveProductId", "results", "otherSkipped",
                      "routeErrors", "keywordsDropped", "scanNote", "contractCfgLoaded",
                      "stats")

# ---- read 侧的**内置兜底键集**(2026-10-01,D12):仅在 contract.json 缺失/无 `read` 段时生效 ----
# 与 search 侧同纪律(见上面那段论证):键集是渲染细节,不是安全闸,为它中断深读
# 等于让一个数据文件决定套件能否工作。故只回落、不抛错。
# ⚠️ **三档的真实产出键集**(2026-10-01 逐档实测登记,不是照抄 tests 里手写的那份):
#   knowledge: ok id type title contentText url products updatedAt (+stats)
#   question : 公共段 + isSolved answersCount views rewardCoins createdAt
#              answersTaken answersTotal (+ 条件键 bestAnswer/truncated/answers)(+stats)
#   article  : 公共段 + supports views (+stats)
# ⚠️ `other` 档**不在** `_FALLBACK_READ_BY_TYPE_KEYS` 里:它是合法 kind 但没有全文
# 端点(`_detail` 抛 `unsupported_kind`),read 的键集里不存在这一档。
# ⚠️ 兜底集必须与 contract.json 的 `read` 段保持一致 —— 由离线回归用例钉住
# (`t_read_keys_vs_real_output` 的 ① 段同时读两边),不靠人工誊抄。
_FALLBACK_READ_TOP_KEYS = ("ok", "id", "type", "title", "contentText", "url",
                           "products", "updatedAt", "stats")
_FALLBACK_READ_BY_TYPE_KEYS = {
    "knowledge": (),
    "question": ("isSolved", "answersCount", "views", "rewardCoins", "createdAt",
                 "answersTaken", "answersTotal"),
    "article": ("supports", "views"),
}
# **条件键**:有才留(`bestAnswer` 依赖上游给了采纳答案、`truncated` 依赖发生截断、
# `answers` 依赖 `with_answers` 分支)。它们与"恒在键"同属投影白名单,只是**不保证出现**。
_FALLBACK_READ_CONDITIONAL_BY_TYPE = {"question": ("bestAnswer", "truncated", "answers")}
# `truncated` 的两值枚举(见 contract.json 的 `read.note_truncatedValues`)。
# 具名映射而**不是**有序元组:代码里要按语义取值(`["answerLimit"]`),靠下标
# 取第二值会把"枚举顺序"变成隐式契约(改动顺序即静默改语义)。
_FALLBACK_READ_TRUNCATED_VALUES = {"answerLimit": "answer_limit",
                                   "upstreamError": "upstream_error"}
# `other` 档的**开关状态**:由 `_search_manifest` 的 `include_other` 形参控制,
# 不经环境变量(与"删掉 KSEARCH_RATE 等隐式通路"的纪律一致)。


def result_keys(kind=None):
    """清单条目允许出现的键集(从 contract.json 声明取)。

    `kind=None` → **公共键集**(三类恒可达的键);给 kind → 公共 + 该类型专属。
    类型不适用的键**不会**出现在该类型条目上(ADR-0016 决策 6):"结构性不适用"
    与"上游没给值"是两件事,混成 `null` 会让调用方分不清。故本函数是**白名单下界**
    的来源,`_manifest_project` 按它拼键、缺值不产出。
    """
    s = _contract().get("search") or {}
    common = s.get("resultKeysCommon") or list(_FALLBACK_COMMON_KEYS)
    keys = [str(k) for k in common]
    if kind is not None:
        by = s.get("resultKeysByType")
        by = by if isinstance(by, dict) else _FALLBACK_BY_TYPE_KEYS
        extra = by.get(str(kind))
        if extra is None:
            extra = _FALLBACK_BY_TYPE_KEYS.get(str(kind)) or ()
        keys += [str(k) for k in extra]
    return tuple(keys)


def result_forbidden_keys():
    """清单条目**出现即 FAIL** 的历史残留键集(从 contract.json 声明取)。

    ⚠️ 保留声明是**刻意的**(ADR-0016 决策 8):它记着"这些键是刻意删掉的"
    (contentText / fusedScore / questionId / views / updatedAt …),防后人顺手加回来。
    但它是**文档记录**,不是运行期闸门——清单字段是白名单拼出来的,白名单外产不出键,
    对生产输出断言"没出现禁键"是恒绿的重言式(T4 修掉的"回声与自回声比"同型)。
    故生产路径不读它,只有回归用例读(作为一份可引用的记录)。
    """
    keys = ((_contract().get("search") or {}).get("resultForbiddenKeys")
            or list(_FALLBACK_FORBIDDEN_KEYS))
    return tuple(str(k) for k in keys)


def top_keys():
    """search 顶层键集(从 contract.json 声明取)。

    ⚠️ **v6.6 起真的被生产代码读**(ADR-0016 决策 8):`_search_manifest` 的顶层字段
    也照声明拼,与条目侧同构。此前它在生产路径**零调用**(实测 `rg` 仅剩定义与导出),
    与 CONTEXT.md 的纪律直接冲突:**「声明必须有消费者,否则'单一来源'是假的」**
    ——声明写了而没人读,等于文档多抄一份。
    """
    keys = ((_contract().get("search") or {}).get("topKeys") or list(_FALLBACK_TOP_KEYS))
    return tuple(str(k) for k in keys)


def read_keys(kind=None):
    """read 返回体**允许出现**的键集(从 contract.json 的 `read` 段取)。

    这是 `read` 侧声明段的**生产消费者**(2026-10-01,D12):`_detail.project_read`
    按它投影三档返回体 —— 声明里没列的键**一律不出现在最终返回体**,这就是
    「声明即闸门」的机制(有回归钉子:`t_read_keys_vs_real_output` 的注入段)。

    `kind=None` → **公共键集**(三档恒在的键);给 kind → 公共 + 该档专属恒在键
    **+ 该档条件键**。条件键必须并入:`bestAnswer`/`truncated`/`answers` 与恒在键
    同属白名单,只是不保证出现;漏并入它们会让投影**静默丢掉**这几个键
    (那正是本仓最忌的"一处静默换另一处静默")。

    ⚠️ 与 `result_keys()` 同纪律:读取失败/类型未知一律回落内置兜底集,**不抛错**。

    ⚠️ **`other` 档落到的是"仅公共键"**:它是合法 kind 但没有全文端点
    (`_detail` 抛 `unsupported_kind`),故上述兜底对它是**不可达分支**;写成通用回落
    是为了让本函数对任何 kind 都不抛错(与 `link_for` 的"未知 kind → 保守值"同口径)。
    """
    r = _contract().get("read") or {}
    keys = r.get("topKeys") or list(_FALLBACK_READ_TOP_KEYS)
    keys = [str(k) for k in keys]
    if kind is not None:
        k = str(kind)
        for section, fallback in (
                ("keysByType", _FALLBACK_READ_BY_TYPE_KEYS),
                ("conditionalByType", _FALLBACK_READ_CONDITIONAL_BY_TYPE)):
            by = r.get(section)
            by = by if isinstance(by, dict) else fallback
            extra = by.get(k)
            if extra is None:
                extra = fallback.get(k) or ()
            keys += [str(x) for x in extra]
    return tuple(keys)


def read_truncated_values():
    """read(question) 的 `truncated` **字符串枚举两值**(从 contract.json 的 `read` 段取)。

    具名映射(而**不是**有序元组):调用方按语义取值(`["answerLimit"]`),靠下标
    取第二值会把"枚举顺序"变成隐式契约 —— 声明里改个顺序就静默改了语义。

    ⚠️ 2026-10-01(D12):这两值原先在 `_question_detail` 里各写一遍字面量,而枚举表
    (docstring / ANSWER-SPEC / ADR-0016 决策 7)是它们唯一的语义说明 —— 改枚举要同时
    改四处,没人盯得住。现在单一来源是声明,代码经本函数取(有回归钉子:改声明里的
    值 → 真实输出的 `truncated` 跟着变)。

    ⚠️ **逐键回落,不整份丢、不抛错**(与 `_burst_of` 同纪律):声明里只写了一半时,
    另一半仍取兜底值 —— 声明里的坏值不该让深读主链路崩溃。
    """
    out = dict(_FALLBACK_READ_TRUNCATED_VALUES)
    try:
        v = (_contract().get("read") or {}).get("truncatedValues")
        if isinstance(v, dict):
            for name in out:
                got = v.get(name)
                if got:
                    out[name] = str(got)
    except Exception:
        return dict(_FALLBACK_READ_TRUNCATED_VALUES)
    return out


def link_policy():
    """链接政策(从 contract.json 的 `linkPolicy` 取):`{kind: "link"|"no-link"}`。

    ⚠️ 2026-09-28 起**代码真的读它**。此前它是 `"status": "pending"` 的纯占位、
    无任何消费者——于是文档抄了四遍口径各异(README 与 SKILL/ANSWER-SPEC 正面对撞),
    而声明这一份谁也没看。现在它是**唯一真源**:文档指向它,回归用例对着它断言。

    读取失败回落"全部不给链接"——**保守方向**是少给一个链接(读者损失一次跳转),
    而不是多给一个死链(读者以为资料不存在)。与 `_contract` 同纪律:不抛错。
    """
    p = (_contract().get("linkPolicy") or {}).get("rule") or {}
    return {str(k): str(v) for k, v in p.items()} or {
        "knowledge": "no-link", "question": "no-link", "article": "no-link"}


def link_for(kind):
    """某个 kind 的链接政策:"link" 或 "no-link"。未知 kind → 保守 `no-link`。"""
    return link_policy().get(str(kind or "").lower(), "no-link")


def apply_link_policy(kind, url):
    """按链接政策决定**是否把该 url 交给读者**;不给则返回 None。

    ⚠️ 这是本声明**在生产路径上的唯一生效点**(2026-09-28)。此前 `linkPolicy` 只在
    文档里被引用、在测试里被断言,**生产路径零调用** —— 于是清单/全文照样把
    `question/<id>` 的 url 交给调用方,而该路径实测 9/9 + 登录态 1 条全部不可点。
    那与"修复前的 `pending` 占位"实质相同:声明写了,行为没变。

    为什么不直接删掉 url 字段:字段仍在契约里(它是上游数据形状的如实反映),
    只是**值**由政策决定 —— 政策是"给不给读者",不是"数据是否存在"。
    调用方拿到 `url: null` 即等于"这条来源不可给链接,只给标题与出处"。

    ⚠️ `url` 本来就是 None(上游没给)时仍返回 None,不做区分:两者对读者同义。
    """
    if url is None:
        return None
    return url if link_for(kind) == "link" else None


def default_product_id():
    """产品线默认编号(不传 --product 时生效)。声明缺失回落 93。"""
    v = (_contract().get("productIds") or {}).get("default")
    return 93 if v is None else int(v)


# ---- 「整份声明读不到」时的**保守默认档位** ----
# ⚠️ 与 `_FALLBACK_BURST` 是**两个不同的概念**,不得混为一谈(2026-09-29,code review M-2):
#   * `_CONSERVATIVE_RATE`(本常量)—— 声明**整份读不到**时的档位。它刻意取 burst=1,
#     含义是"我们不知道红线,故按最保守的 1 处理"。
#   * `_FALLBACK_BURST` —— **档位存在但缺 burst 键**时的回落值(见 `_burst_of`)。
# 两者的适用路径**互不重叠**:声明整份读不到 → 走本常量(得 1),
# 此时 `_FALLBACK_BURST` **不会**生效。原先把 `{"burst": 1, ...}` 直接写在这里,
# 而 `_burst_of` 的 docstring 与钉子写成"共享单一来源"的通则 —— 那是**夸大了**
# `_FALLBACK_BURST` 的适用范围(它只覆盖"缺键"这一形态,不覆盖"整份读不到")。
# 抽成常量是为了让这条边界**可被引用与断言**,而不是散落的字面量。
_CONSERVATIVE_RATE = {"burst": 1, "rps": 1.0, "jitterMs": [0, 120]}


def _burst_of(prof):
    """档位 dict → **单次在飞请求上界**。本值是唯一来源,两个消费者共用。

    消费者(必须恒等,否则"并发上界取 burst"这句话是假的):
      * `_RateLimiter.wait()` —— 限速器认定的一次突发允许量;
      * `_burst()` —— `_detail` 的翻页并发上界。

    ⚠️ **2026-09-29 修正(真实分叉已实测)**:此前两处**各写各的兜底** ——
    `wait()` 写 `int(p.get("burst") or 1)`,而 `_burst()` 写
    `int(... or _FALLBACK_BURST)`。于是**同一个档位 dict 在两条路径上得出不同答案**:
      实测 `_CONTRACT = {"limits": {"rate": {"interactive": {}}}}`(档位存在但为空 dict)
      → `profile()` 回落到 `{}`(空档位),此时 `_burst()` = **7**、`wait()` = **1**。
    收成单一函数后两处恒等,且 `_FALLBACK_BURST` 有了**共享的**消费者。

    ⚠️ **适用范围(精确边界,勿夸大;2026-09-29 code review M-2)**:本函数只在
    **"档位 dict 存在但 burst 键缺失/为假值/非数值"**这一形态下用 `_FALLBACK_BURST`。
    **声明整份读不到**时走的是 `_CONSERVATIVE_RATE`(burst=1),`profile()` 早已给出
    burst=1,本函数的 `or` 分支**不会触发** —— 即 `_FALLBACK_BURST` 在那条路径上
    **零消费**。这是刻意的语义分层(不知道红线 → 最保守;知道档位但缺键 → 与
    `maxKeywords` 对齐的上界),但**必须写明**,否则又成了"自称覆盖、实际不覆盖"。

    ⚠️ **非数值/非 dict 一律回落,不抛错**(2026-09-29,code review L1 + 对抗性核实 F3):
    `int("x")` 会抛 `ValueError`,而 `prof` 本身若非 dict 则 `.get` 会抛 `AttributeError`
    —— 而 `_burst()` 有 `except Exception` 兜底、`wait()` 没有,于是坏值在两条路径上
    前者静默回落 7、后者**炸掉整个 search/read**。
    现改为**就地归一**(先判类型,再容错转换):声明里的坏值不该让检索主链路崩溃
    (与 `max_keywords` / `max_detail` 的 `try/except` 同口径),更不该两副面孔。
    ⚠️ **口径边界(勿夸大成"归一彻底")**:本函数只归一 **burst 这一个键**。
    声明里 **`rps` / `jitterMs` 的坏值不在此处兜底** —— `wait()` 里的
    `float(p.get("rps"))` 仍可能抛错。那是另一处(限速器的其余键),本轮未动,属已知边界。
    """
    if not isinstance(prof, dict):
        return _FALLBACK_BURST
    try:
        return max(1, int(prof.get("burst") or _FALLBACK_BURST))
    except (TypeError, ValueError):
        return _FALLBACK_BURST


class _RateLimiter:
    """上游限速(匿名链路):令牌桶实现,只对真实上游请求生效(_get_json 入口)。

    配置里只有 interactive 一档(v6.6 起配置源为 `contract.json` 的 `limits.rate`)。
    曾存在 background 档(1 req/s)与两条切档通路(请求级 set_profile、环境变量
    KSEARCH_RATE),两者均已删除:该档的唯一生产者是已随去服务化删除的摄取/评测脚本,
    场景不存在。公开签名 `rate=` 保留(对外契约);传入配置中不存在的档名时回落到
    保守默认,不报错、不静默切档。
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._next = 0.0

    def profile(self, name=None):
        cfg = _limits().get("rate") or {}
        wanted = str(name or "interactive").lower()
        p = cfg.get(wanted)
        if not isinstance(p, dict):
            # 配置缺失/档名不存在:回落 interactive(配置里有则用它的数值,没有则用
            # 保守默认)。档名一并归一,避免"报 interactive 却按保守值限速"的名实不符。
            p = cfg.get("interactive")
            wanted = "interactive"
            if not isinstance(p, dict):
                p = dict(_CONSERVATIVE_RATE)
        return wanted, p

    def wait(self, name=None):
        pname, p = self.profile(name)
        rps = float(p.get("rps") or 2.5)
        # ⚠️ 走 `_burst_of` 单一来源(2026-09-29):此前这里写死 `or 1`,
        # 与 `_burst()` 的 `or _FALLBACK_BURST` 分叉(空档位时 1 vs 7,实测)。
        burst = _burst_of(p)
        j = p.get("jitterMs") or [0, 0]
        interval = 1.0 / rps
        with self._lock:
            now = time.monotonic()
            t = max(self._next, now - burst * interval)  # 短突发:允许 burst 个请求立即通过
            delay = t - now
            self._next = t + interval
        if delay > 0:
            log("RATE[%s] 节流 %.2fs (burst=%d rps=%.1f)" % (pname, delay, burst, rps))
        time.sleep(max(delay, 0.0) + random.uniform(float(j[0]), float(j[1])) / 1000.0)


_RATE = _RateLimiter()


def _rate_profile():
    """读当前限速档名(固定 interactive;切档通路已删除)。"""
    return _RATE.profile()[0]


def _burst():
    """当前档位的 `burst`(单次操作最大在飞请求数;声明 `limits.rate`)。

    ⚠️ **消费者**(2026-09-29,code review H3):`_detail` 的翻页并发度上界。
    翻页上限删除后,"上游说了算"的并发度会把单次 `read` 放大到 199 线程 / 200 请求
    (实测 `totalPages=200`),而 ADR-0017 明标"10+ 路未实测、不应外推"。
    故并发上界取本值 —— **与"单次操作最大请求数"对齐**,不自造第二个限速器
    (`burst` 的定义本就如此:`maxKeywords=7` 是单次 search 的路数上限)。

    ⚠️ **与限速器共用 `_burst_of` 单一来源**(2026-09-29 修正):本函数原先自带
    一份 `or _FALLBACK_BURST`,与 `wait()` 里的 `or 1` **分叉**(空档位时 7 vs 1,
    已实测)。这不是理论问题 —— 它让"并发上界取 burst"这句话在配置异常时不成立:
    限速器按 1 限流,而翻页仍可放 7 路。收口后两条路径恒等。
    """
    try:
        return _burst_of(_RATE.profile()[1])
    except Exception:
        return _FALLBACK_BURST


# ---- 上游调用计数(每次调用取前后差值) ----
_UP_LOCK = threading.Lock()
_UP_N = 0


def _up_inc():
    global _UP_N
    with _UP_LOCK:
        _UP_N += 1


def _up_now():
    with _UP_LOCK:
        return _UP_N
