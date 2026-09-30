#!/usr/bin/env python3
"""kd._impl —— kd.core 的私有实现包(按职责拆分的装配层)。

**为什么是包而不是单文件**:kd.core 的公开面隔离依赖"实现体不在 kd.core 的命名空间里"
这一机制(工单 #26)。此前全部实现挤在一个 1331 行的模块 `kd._core_impl.py` 里,
47 个顶层定义横跨 9 块职责(配置·预算·限速·上游计数·文本清洗·切片·重排·规范化·
多路编排·拆词·详情深读·公开面)。为了让 `kd.core.RRF_K` 抛 AttributeError,
**整个实现体被迫保持单文件**——那是用模块粒度的 seam 去解决可见性粒度的问题。

拆成包后,seam 落在**职责簇**:每个私有子模块自成一个可独立阅读的单位,
而可见性隔离仍由"kd.core 只 import 本包"保证(隔离与文件数量无关)。

**装配层的职责(两件,都只有它做)**:
  1. 向 `kd.core` 提供 5 个公开名(2 函数 + 3 异常);
  2. 把全部内部件汇集到**本包命名空间**,成为内部件的**唯一名字解析点**——
     `kd.core._impl()` 返回本包对象,测试/自检经它读内部件(守卫判据 7 逐项校验
     `cli.cmd_health` 依赖的名字仍可解析)。

子模块地图:
  _errors    三个对外异常类(类对象身份被 kd.core 复用)
  _config    单一真源常量、契约声明(contract.json)、限速、上游计数、log
  _net       上游 HTTP 出口(_get_json)、检索词硬闸(clamp_query)
             「什么算上游故障」的**单一分类来源**(UPSTREAM_FAILURES:2026-09-29 新增,
             code review High-1)。检索侧与深读侧的失败隔离共用它,避免"某一层认识
             这种失败、另一层不认识"的不对称。
             ⚠️ **本模块的 `_get_json` 是全内核唯一的网络出口**(2026-09-29 更正:
             原文说这个位置属于 `_search_upstream`,**那句是错的** —— 深读侧 5 个
             调用完全不经过它)。调用方一律属性访问(`_net._get_json`),不得快照
  _text      html2text、标题降级链(_title_of)
  _upstream  上游检索调用与条目规范化(_norm_item)、链接模板(_URL_OF)
             ⚠️ 上游 `answer` → 对外 `question` 的**唯一映射点**在这里
             ⚠️ 本模块的 `_search_upstream` 是**检索侧**的唯一出口(不是全内核的)
  _routes    调用方给的词 → 检索路(_plan_routes)、路去重(_dedupe_routes)
             ⚠️ 拆词器已于 v6.6 整体删除(ADR-0016):本模块**不生成任何检索词**
  _manifest  多路清单执行链(唯一检索路径)、帖级归并(_manifest_merge)、声明投影
  _detail    按 kind 读全文
  _public    公开面函数 search / read

⚠️ **v6.6 导出面收缩**(ADR-0016,2026-09-28):随机制删除的导出有
`_Budget` / `_BudgetExhausted` / `_cfg_budget_search_max` / `_route_cfg` /
`_ROUTE_CFG_PATH`(预算与旧配置源)、`upstream_type_of`(随 `--type` 删除)、
`_salient_chunks`(拆词器)、`_truncate_routes` / `_route_sorts_type` / `_MAX_SCAN_PAGES`
(截断优先级与跨页扫描)。`cli.cmd_health` 的 `_cp.<name>` 引用已同步——
**删机制时漏同步这里会让 health 崩**,守卫判据逐项校验。
"""

# ---- 版本单一真源(包 __version__ 与 cli._VERSION 都从此取) ----
from ._config import VERSION  # noqa: F401

# ---- 公开面:2 个函数 + 3 个异常(唯独这几个会被 kd.core 取走) ----
from ._errors import InternalError, QueryTooLong, UpstreamError  # noqa: F401
from ._public import read, search  # noqa: F401

# ---- 内部件汇集:观测口(kd.core._impl())与测试/自检经此读取 ----
# 这些名字构成内部件的"名字解析点"。cli.cmd_health 体内 `_cp.<name>` 引用的每一项
# 必须在下面出现(守卫判据 7 会从 cli.py 源码提取清单并逐项校验可解析)。
from ._config import (  # noqa: F401
    ENTITY_KINDS,
    HDRS,
    UPSTREAM_TEXT_MAX,
    VIP,
    _CONTRACT,
    _CONTRACT_PATH,
    _FALLBACK_BY_TYPE_KEYS,
    # ⚠️ 兜底键集与 _CONTRACT 缓存对外导出是**刻意的**:回归用例要拿它们与声明做
    # **交叉核对**(两边同源就恒等成立,见 t_contract_fallback_in_sync 的 2026-09-28 更正),
    # 还要模拟"声明读不到"以验证兜底分支真的会生效。不导出等于那条断言只能自比。
    _FALLBACK_COMMON_KEYS,
    _FALLBACK_FORBIDDEN_KEYS,
    _FALLBACK_BURST,
    _FALLBACK_MAX_DETAIL,
    _FALLBACK_MAX_KEYWORDS,
    _FALLBACK_TOP_KEYS,
    _RATE,
    _contract,
    _limits,
    _burst,
    _burst_of,
    _rate_profile,
    _up_now,
    apply_link_policy,
    contract_loaded,
    default_product_id,
    link_for,
    link_policy,
    log,
    max_detail_knowledge,
    max_keywords,
    result_forbidden_keys,
    result_keys,
    top_keys,
)
from ._net import UPSTREAM_FAILURES, _get_json, clamp_query  # noqa: F401
from ._text import _is_true, _title_of, html2text  # noqa: F401
from ._upstream import _URL_OF, _norm_item, _search_upstream  # noqa: F401
from ._routes import _dedupe_routes, _plan_routes, _stamp_product  # noqa: F401
from ._manifest import (  # noqa: F401
    _PER_ROUTE_WANT,
    _manifest_fuse,
    _manifest_key,
    _manifest_merge,
    _manifest_project,
    _route_search_once,
    _search_manifest,
    project_top,
)
from ._detail import (  # noqa: F401
    _DETAIL_FN,
    _DETAIL_KINDS,
    _answer_brief,
    _article_detail,
    _knowledge_article,
    _question_detail,
)
# ⚠️ **`_detail` 函数改名为 `_detail_fn` 导出**(2026-09-29,工单 #33)。
# 原先写 `from ._detail import _detail` —— 那个名字**遮蔽了同名的子模块**:
#     import kd._impl._net as m     -> module    (可用)
#     import kd._impl._detail as m  -> function  (不是模块!)
#     kd._impl._detail._get_json    -> AttributeError
# 这是本包唯一的一处真遮蔽(其余 8 个撞车名的包属性确实就是子模块对象)。
# 代价已经真实发生:回归测试为此专门写了 `_upstream_mod()` / `_detail_mod()`
# 两个 helper **绕道 `sys.modules`**,并注明"不能写 `import kd._impl._detail as m`"。
# 改名后 `import kd._impl._detail` 语义正确,两个 helper 可直接简化为普通 import。
from ._detail import _detail as _detail_fn  # noqa: F401
