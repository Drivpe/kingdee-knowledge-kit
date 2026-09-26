#!/usr/bin/env python3
"""kd._impl._net —— 上游 HTTP 出口与检索词硬闸。

全包**唯一**发起上游请求的地方:预算领取与限速都在这里落地,故
"预算是否真的是硬上限"只需审视本模块。
"""
import json
import urllib.request

from ._config import HDRS, UPSTREAM_TEXT_MAX, _RATE, _up_inc
from ._errors import QueryTooLong, UpstreamError


def clamp_query(text, limit=None, strict=False):
    """把检索词压到上限内。默认 limit=UPSTREAM_TEXT_MAX(100 原始字符)。

    上游是硬闸而不是软截断:超限返回 HTTP 200 + errorCode:409 空壳。因此
    strict=True 时对超限输入显式 raise QueryTooLong(附 clamped 压回值),
    绝不静默截断——静默会把"查询超限"伪装成"官方没这类文档"。
    strict=False(内部路径)按上限压回,保证上游请求永远合法。
    """
    t = str(text or "")
    n = int(limit or UPSTREAM_TEXT_MAX)
    clamped = t[:n]
    if strict and len(t) > n:
        raise QueryTooLong(t, clamped, n)
    return clamped


def _get_json(url, budget=None, rate=None):
    """上游 GET → 解析后的 JSON。预算与限速的唯一落地处。

    顺序有讲究:领取名额必须在 `_RATE.wait()` **之前**——限速等待期间持有名额,
    才能保证"同时最多 max 个线程在飞",而不是"同时最多 max 个线程通过了检查"。
    """
    if budget is not None:
        budget.acquire()
    _RATE.wait(rate)
    _up_inc()
    req = urllib.request.Request(url, headers=HDRS)
    with urllib.request.urlopen(req, timeout=20) as r:
        d = json.loads(r.read().decode("utf-8", "replace"))
    # 上游"假 200":HTTP 200 但 body 是错误壳(如 text 超 100 字符的 errorCode:409)。
    # 不识别会把"查询超限"静默降级成"无匹配结果"。
    if isinstance(d, dict) and d.get("errorCode"):
        raise UpstreamError(int(d["errorCode"]), str(d.get("message") or "")[:200])
    return d
