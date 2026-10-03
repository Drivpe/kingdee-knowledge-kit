#!/usr/bin/env python3
"""kd._impl._errors —— 可分类的对外错误。

三个异常类的**类对象**被 kd.core 直接复用(不是包装),故实现体内部 raise 的实例,
调用方 `except kd.core.QueryTooLong` 照常命中。身份一致性由公开面守卫判据 6 钉住,
本模块是这三个类唯一的定义地——不得在别处再造一份同名类。

⚠️ 本模块同时承载一处**内部件** `_raise_min`(2026-10-03,X7 收口):它不属于对外
契约,放在这里是因为它的全部语义都关于异常(重抛),且两个消费方
(`_manifest` / `_detail`)本就从本模块 import 异常类 —— 放这里**不新增任何 import 边**,
也不引入循环依赖(本模块零内部依赖)。**不得**从公开面导出它。
"""


class UpstreamError(Exception):
    """上游业务错误(HTTP 200 但 body 带 errorCode)。

    上游的"假 200"形态:HTTP 层成功,业务层失败。不识别就会把"查询被拒绝"
    静默降级成"无匹配结果"——本套件多处注释记着这条教训。
    """

    def __init__(self, code, message):
        self.code = code
        self.message = message
        super().__init__("upstream %s: %s" % (code, message))


class QueryTooLong(ValueError):
    """检索词超过上游 100 原始字符硬闸。

    压回上限内确实能跑通,但静默截断会把"查询被上游拒绝"伪装成"官方没这类文档"
    ——故显式报错,由调用方决定是否用 `clamped` 字段里的压回值重试。
    """

    def __init__(self, original, clamped, limit):
        self.original = original
        self.clamped = clamped
        self.limit = limit
        super().__init__("query too long: %d > %d upstream chars" % (len(original), limit))


class InternalError(Exception):
    """参数/内部使用错误(CLI 默认映射为用法错误退出码 2,**按 `code` 分档**)。

    ⚠️ **可选分类标识 `code`**(2026-09-29 收口,"错误码的分类不依赖文案"):

    本类原先只有自由文本 message,于是"到底是哪一种内部错"只能由**消费方对文案做
    子串匹配**来区分 —— `cli._guard` 曾写 `if "没有它的全文端点" in str(e): ...` 来
    判定 `other` 档的能力边界。那种写法的后果不是"丑",而是**改一个措辞就会让分类
    静默漂移**:`unsupported_kind`(exit 1)会无声退回 `usage`(exit 2),把调用方
    引向"去改参数",而它其实没传错;且当时**没有任何回归用例**覆盖这一档
    (`grep unsupported_kind tests/` 零命中),漂移不会有信号。

    现在**抛出处**用 `code=` 显式声明分类,文案与分类彻底解耦:

        raise InternalError("…文案随便怎么写…", code="unsupported_kind")
        # 消费者只读 `e.code`,永不读 message

    约定:
      * `code=None`(默认,**含全部既有单参调用**)= 未分类 → CLI 按**用法错误**
        (exit 2)兜底 —— 与改动前的行为逐字相同,故单参调用完全兼容;
      * 已知分类见 `_detail._detail`(当前**唯一的分类值**是 `unsupported_kind`)。
        ⚠️ **不在这里预置枚举清单**:没有消费者的档位就是死机制("声明必须有消费者"),
        新增分类时在**抛出处**声明,并在 `cli._guard` 里给它一条映射。
    """

    def __init__(self, message, code=None):
        self.code = code
        super().__init__(message)


def _raise_min(accounted):
    """并发块里的"记账后按最小键重抛"—— 三处共用的**唯一**实现(2026-10-03,X7)。

    `accounted` 是 `{键: sys.exc_info() 三元组}`;本函数重抛**最小键**那一格的异常,
    并带上它原始的 traceback。

    ## 为什么键必须是路号 / 页号 / 下标

    取 `min(键)` 是**确定性**要求,不是"取最早":同一输入必须恒抛同一个异常,
    而"最早"取决于线程调度(本仓硬契约:完成先后不得泄漏进结果)。
    实测过的翻车形态:同一对缺陷只把耗时对调,抛出类型就从 `TypeError` 变成
    `AttributeError`。

    ## 为什么统一"存 `sys.exc_info()`"而不是"存异常对象"

    三处原先**不是同形**的(澄清 X7 的前提):

    | 位置 | 原存的值 | 原重抛写法 |
    |---|---|---|
    | `_manifest` 路由级 | `sys.exc_info()` 三元组 | `raise t[1].with_traceback(t[2])` |
    | `_detail` 页级 | 异常**对象** | `raise failed_pages[min(...)]` |
    | `_detail` 详情补全块 | 异常**对象** | `raise det_failed[min(...)]` |

    后两处只留异常对象 ⇒ traceback 在**收集时**就已丢失(except 块退出后
    `__traceback__` 虽挂得住,但重抛点是收集点而非失败点),异常链更浅。
    统一到"存三元组 + 本函数重抛"后,三处都获得**完整的原始 traceback** —— 这是
    **行为变好**,方向与"失败必须可见"一致。

    ⚠️ **不得**改成 `raise accounted[min(accounted)]`(存对象那套):那会静默丢掉
    `_manifest` 侧原本刻意保留的 traceback 穿透(该处注释原就写着"带原 traceback 穿透")。

    ⚠️ **`accounted` 为空时不得调用** —— 本函数不做兜底(无异常可抛时"抛什么"没有
    正确答案,那是调用方的前置条件)。调用点必须自带 `if bug:` 之类的守卫。
    """
    tb = accounted[min(accounted)]
    raise tb[1].with_traceback(tb[2])
