#!/usr/bin/env python3
"""kd._impl._errors —— 可分类的对外错误。

三个异常类的**类对象**被 kd.core 直接复用(不是包装),故实现体内部 raise 的实例,
调用方 `except kd.core.QueryTooLong` 照常命中。身份一致性由公开面守卫判据 6 钉住,
本模块是这三个类唯一的定义地——不得在别处再造一份同名类。
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
    """参数/内部使用错误(CLI 映射为用法错误退出码 2)。"""
