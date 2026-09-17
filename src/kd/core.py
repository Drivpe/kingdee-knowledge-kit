#!/usr/bin/env python3
"""kd.core —— 检索内核(可 import 的库,无 HTTP 层)。

迁移自 service/kingdee-ksearch-service.py(该文件已于 2026-09-17 随去服务化删除,工单 #22),
去掉全部 HTTP 处理与端点分发,只留检索编排逻辑。零账号/零 cookie/零点数/零外部依赖。

公开面(仅此三个高阶函数 + 三个异常类,工单 #26):
  ask(text=None, keywords=None, product_id=None, top_k=None, budget=None,
      rerank=None, refresh=False, rate=None) -> dict   一站式资料包
  search(text, product_id=None, page=1, page_size=10, global_=False,
         sorts_type=1, type_=None, rerank=None, budget=None, rate=None) -> dict
                                                        检索原语
  read(kind, oid, refresh=False, budget=None, rate=None) -> dict
                                                        按类型读全文
  QueryTooLong / UpstreamError / InternalError            可分类的调用错误

**内部件的封闭方式(工单 #26——前导下划线不足)**:
实现体(全部内部函数、常量、类、以及被 import 进来的 json/re/urllib 等名字)
放在私有子模块 `kd._core_impl` 里。之所以必须单独成文件:模块级定义若写在本文件,
import 时就会注册进 `kd.core` 的命名空间,`kd.core._rrf_fuse` 仍可属性访问——
`__all__` 只约束 `import *`,约束不了属性访问。搬进子模块后,本模块的命名空间里
**只有**上面 6 个公开名,其余一律不存在:
    kd.core._rrf_fuse  -> AttributeError
    kd.core.RRF_K      -> AttributeError
    kd.core.json       -> AttributeError
这不是"前导下划线"式的君子协定,而是命名空间级隔离;后续重构内部件不再构成
对外破坏性变更。校验见 `scripts/check_core_surface.py`。

**内部件仍需可读时**:`kd.core._impl()` 返回承载实现的那个模块对象(显式内部观测口),
仅用于测试与自检;它带前导下划线、不在 `__all__` 内,不参与公开契约。

上游接口(匿名,零 cookie):
  检索    GET https://vip.kingdee.com/api/search?text=&page=&pageSize=&global=&sortsType=&productIds[0]=
  知识全文 GET https://vip.kingdee.com/knowledgeapi/knowledge/{id}
  问题详情 GET https://vip.kingdee.com/api/questions/{id}、回答 GET .../api/answers/{id}
  文章     GET https://vip.kingdee.com/api/articles/{id}
  分享对话 GET https://vip.kingdee.com/aisapi/ai-search/sharing-chats/{chatId}

上游纪律:匿名链路,交互短突发 2-3 请求/秒 + 抖动(默认档),后台/摄取 1 请求/秒。
上游 text 有 100 原始字符硬闸——超限返回 HTTP 200 + errorCode:409 空壳,
实现体用 clamp_query() 压回并在压回时 raise QueryTooLong(不静默截断成"无匹配")。
"""
# 公开面:`kd.core` 顶层**只允许**出现这 6 个名字(工单 #26 验收标准)。
__all__ = ["ask", "search", "read", "QueryTooLong", "UpstreamError", "InternalError"]

# 实现载体。import 的副作用(定义全部内部件)发生在 kd._core_impl 的命名空间内,
# 不在本模块留下任何内部名。这里故意**不留模块级别名**:把 `_core_impl` 绑在本模块
# 会让 `kd.core._core_impl` 成为一条属性面,进而 `kd.core._core_impl._rrf_fuse`
# 又能摸到内部件——正是本工单要堵的洞。改用 _impl() 惰性取(见下)。
import importlib as _importlib

# 公开异常类:直接复用实现体里的类对象,身份一致——
# 实现体内部 raise 的实例,调用方 `except kd.core.QueryTooLong` 照常命中。
_IMPL = _importlib.import_module("kd._core_impl")

QueryTooLong = _IMPL.QueryTooLong
UpstreamError = _IMPL.UpstreamError
InternalError = _IMPL.InternalError

# 公开三个高阶函数:直接绑定实现体里的函数对象(不是包装层)。
# 签名、默认值、docstring、`__globals__`、返回结构全部原样,行为逐字段不变。
ask = _IMPL.ask
search = _IMPL.search
read = _IMPL.read

# 观测口别名在绑定完成后立即清除:`_IMPL` / `_importlib` 不得留在 __dict__ 里。
del _IMPL, _importlib


def __getattr__(name):
    """内部观测口(惰性,不进命名空间)。

    本工单要求 `kd.core` 的可见名恰为 6 个公开名,但内部件仍需一个**显式**的观测路径
    (测试、自检、CLI health 都要读内部配置),不能靠"从 kd.core 摸私有属性"这种
    君子协定。故用 PEP 562 模块级 `__getattr__`:

        kd.core._impl()        -> 承载实现的模块对象(kd._core_impl)
        kd.core._rrf_fuse      -> AttributeError(不会经过这里返回函数)

    只有白名单里的私有名才会被解析;其余一律 AttributeError,保证
    `vars(kd.core)` 恒等于 6 个公开名 + 双下划线元数据。
    """
    if name == "_impl":
        def _impl():
            """返回承载实现的模块对象(kd._core_impl);测试/自检用,不属公开契约。"""
            import importlib
            return importlib.import_module("kd._core_impl")
        return _impl
    raise AttributeError("module %r has no attribute %r" % (__name__, name))

