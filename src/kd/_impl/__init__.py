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
     ⚠️ 正因如此,子模块**跨模块调用上游出口时必须经本包命名空间解析**
     (见 `_manifest._resolve`),否则测试替换 `kd._impl._search_upstream` 注入故障
     会失效——原单文件实现里那种替换是生效的,拆分不得破坏它。

子模块地图:
  _errors    三个对外异常类(类对象身份被 kd.core 复用)
  _config    单一真源常量、路由配置、契约声明(contract.json)、预算、限速、上游计数、log
  _net       上游 HTTP 出口(_get_json)、检索词硬闸(clamp_query)
  _text      html2text、标题降级链(_title_of)
  _upstream  上游检索调用与条目规范化(_norm_item)、链接模板(_URL_OF)
             ⚠️ 上游 `answer` → 对外 `question` 的**唯一映射点**在这里
  _routes    拆解器(_plan_routes)、路去重(_dedupe_routes)
             ⚠️ 产品线判定已于 2026-09-27 整体删除,product_id 直通
  _manifest  多路清单执行链(唯一检索路径)、帖级归并(_manifest_merge)
  _detail    按 kind 读全文
  _public    公开面函数 search / read
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
    _Budget,
    _BudgetExhausted,
    _CONTRACT,
    _CONTRACT_PATH,
    _FALLBACK_FORBIDDEN_KEYS,
    # ⚠️ 兜底键集与 _CONTRACT 缓存对外导出是**刻意的**:回归用例要拿它们与声明做
    # **交叉核对**(两边同源就恒等成立,见 t_contract_fallback_in_sync 的 2026-09-28 更正),
    # 还要模拟"声明读不到"以验证兜底分支真的会生效。不导出等于那条断言只能自比。
    _FALLBACK_RESULT_KEYS,
    _ROUTE_CFG_PATH,
    _RATE,
    _cfg_budget_search_max,
    _contract,
    _rate_profile,
    _route_cfg,
    _up_now,
    apply_link_policy,
    cfg_max_routes,
    default_product_id,
    link_for,
    link_policy,
    log,
    result_forbidden_keys,
    result_keys,
    top_keys,
)
from ._net import _get_json, clamp_query  # noqa: F401
from ._text import _is_true, _title_of, html2text  # noqa: F401
from ._upstream import _URL_OF, _norm_item, _search_upstream, upstream_type_of  # noqa: F401
from ._routes import _dedupe_routes, _plan_routes, _salient_chunks  # noqa: F401
from ._manifest import (  # noqa: F401
    _MAX_SCAN_PAGES,
    _PER_ROUTE_WANT,
    _manifest_fuse,
    _manifest_key,
    _manifest_merge,
    _manifest_project,
    _manifest_rank,
    _route_search_once,
    _route_sorts_type,
    _search_manifest,
)
from ._detail import (  # noqa: F401
    _DETAIL_FN,
    _DETAIL_KINDS,
    _answer_brief,
    _article_detail,
    _detail,
    _knowledge_article,
    _question_detail,
)
