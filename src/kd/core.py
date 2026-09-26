#!/usr/bin/env python3
"""kd.core —— 检索内核(可 import 的库,无 HTTP 层)。

零账号/零 cookie/零点数/零外部依赖。

公开面(仅此两个高阶函数 + 三个异常类):
  search(text=None, keywords=None, product_id=93, page=1, page_size=10,
         global_=False, sorts_type=1, type_=None, max_routes=None, budget=None,
         rate=None) -> dict
                      唯一检索入口:多路拆词检索,**只出标题清单**(不返回正文)
  read(kind, oid, budget=None, rate=None) -> dict
                      按类型读全文
  QueryTooLong / UpstreamError / InternalError            可分类的调用错误

**三步解耦(本内核的设计主轴)**:清单 → 挑选 → 全读。
  1. `search` 用多路关键词检索,只列出标题级信息;
  2. 调用方(agent)按标题匹配度挑出要读哪几篇;
  3. `read` 取全文,由调用方合成回答(ADR-0008:合成权在调用方)。
内核**不替调用方决定读哪篇**,也不产生任何排序评分——每一路都是上游综合排序的产物,
本内核只做去重(2026-09-18 定案,见 ADR-0013)。

**内部件的封闭方式(工单 #26——前导下划线不足)**:
实现体(全部内部函数、常量、类、以及被 import 进来的 json/re/urllib 等名字)
放在私有实现包里(`kd._impl`,按职责拆成若干子模块)。之所以必须不在本文件:模块级
定义若写在本文件,import 时就会注册进 `kd.core` 的命名空间,`kd.core._rrf_fuse`
仍可属性访问——`__all__` 只约束 `import *`,约束不了属性访问。搬进私有包后,
本模块的命名空间里**只有**上面 5 个公开名,其余一律不存在:
    kd.core.ask        -> AttributeError
    kd.core.RRF_K      -> AttributeError
    kd.core.json       -> AttributeError
这不是"前导下划线"式的君子协定,而是命名空间级隔离;后续重构内部件不再构成
对外破坏性变更。校验见 `scripts/check_core_surface.py`。

**内部件仍需可读时**:`kd.core._impl()` 返回承载实现的包对象(显式内部观测口),
仅用于测试与自检;它带前导下划线、不在 `__all__` 内,不参与公开契约。

上游接口(匿名,零 cookie):
  检索    GET https://vip.kingdee.com/api/search?text=&page=&pageSize=&global=&sortsType=&productIds[0]=
  知识全文 GET https://vip.kingdee.com/knowledgeapi/knowledge/{id}
  问题详情 GET https://vip.kingdee.com/api/questions/{id}、回答 GET .../api/answers/{id}
  文章     GET https://vip.kingdee.com/api/articles/{id}

上游纪律:匿名链路,交互短突发 2-3 请求/秒 + 抖动(默认档)。
上游 text 有 100 原始字符硬闸——超限返回 HTTP 200 + errorCode:409 空壳,
实现体用 clamp_query() 压回并在压回时 raise QueryTooLong(不静默截断成"无匹配")。
"""
# 公开面:`kd.core` 顶层**只允许**出现这 5 个名字(工单 #26 验收标准)。
__all__ = ["search", "read", "QueryTooLong", "UpstreamError", "InternalError"]

# 实现载体。import 的副作用(定义全部内部件)发生在 kd._impl 的命名空间内,
# 不在本模块留下任何内部名。这里故意**不留模块级别名**:把 `_impl` 绑在本模块
# 会让 `kd.core._impl` 成为一条属性面,进而能摸到内部件——正是本机制要堵的洞。
# 改用 _impl() 惰性取(见下)。
import importlib as _importlib

# 公开异常类:直接复用实现体里的类对象,身份一致——
# 实现体内部 raise 的实例,调用方 `except kd.core.QueryTooLong` 照常命中。
_IMPL = _importlib.import_module("kd._impl")

QueryTooLong = _IMPL.QueryTooLong
UpstreamError = _IMPL.UpstreamError
InternalError = _IMPL.InternalError

# 公开两个高阶函数:直接绑定实现体里的函数对象(不是包装层)。
# 签名、默认值、docstring、`__globals__`、返回结构全部原样,行为逐字段不变。
search = _IMPL.search
read = _IMPL.read

# 观测口别名在绑定完成后立即清除:`_IMPL` / `_importlib` 不得留在 __dict__ 里。
del _IMPL, _importlib


def __getattr__(name):
    """内部观测口(惰性,不进命名空间)。

    本机制要求 `kd.core` 的可见名恰为 5 个公开名,但内部件仍需一个**显式**的观测路径
    (测试、自检、CLI health 都要读内部配置),不能靠"从 kd.core 摸私有属性"这种
    君子协定。故用 PEP 562 模块级 `__getattr__`:

        kd.core._impl()        -> 承载实现的包对象(kd._impl)
        kd.core._rrf_fuse      -> AttributeError(不会经过这里返回函数)

    只有白名单里的私有名才会被解析;其余一律 AttributeError,保证
    `vars(kd.core)` 恒等于 5 个公开名 + 双下划线元数据。
    """
    if name == "_impl":
        def _impl():
            """返回承载实现的包对象(kd._impl);测试/自检用,不属公开契约。"""
            import importlib
            return importlib.import_module("kd._impl")
        return _impl
    raise AttributeError("module %r has no attribute %r" % (__name__, name))
