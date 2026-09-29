#!/usr/bin/env python3
"""kd._impl._net —— 上游 HTTP 出口与检索词硬闸。

全包**唯一**发起上游请求的地方,故"限速是否真的生效"只需审视本模块。

⚠️ **两个消费方必须走模块属性访问,不得 `from ._net import _get_json`**
(2026-09-29 出口统一,ADR-0017 前置):

    from ._net import _get_json      # ❌ import 期快照,替换 `_net._get_json` 对它无效
    from . import _net               # ✅ 调用点写 `_net._get_json(...)`
    ... _net._get_json(url, rate)

这条纪律不是洁癖:`_upstream` 与 `_detail` 原先是快照形态,于是**没有任何单一注入点
能拦住全部上游请求**。实测(用 `sitecustomize` 钩 `urllib.request.urlopen` 跑完整
48 条离线用例)抓到 **1 次真实上游请求而 48/48 仍全绿**,承重点是装机自检
(`install.sh` / `install.ps1`)—— 跑在用户机器上,有网就发、没网就静默吞。

⚠️ **限速器同样是快照陷阱**:本模块原先 `from ._config import _RATE`,于是
`_net._RATE is _config._RATE -> True`——替换任一侧都不生效。现改属性访问,
"替换 `_net._get_json` 即零网络 / 替换 `_config._RATE` 即换限速器"两条注入通路才成立。

⚠️ **预算机制已于 v6.6 整体删除**(ADR-0016 决策 3):跨页扫描删除后每路恒发
1 次请求(实测 7 词 = 7 次),预算**永不可触发**,留着是死机制。`_get_json` 因此
不再收 `budget` 形参——限额领取原本是它的第一件事,现在整段消失。
"""
import json
import urllib.request

from . import _config
from ._config import HDRS, UPSTREAM_TEXT_MAX
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


def _get_json(url, rate=None):
    """上游 GET → 解析后的 JSON。限速的唯一落地处。

    ⚠️ **本函数是全内核唯一的上游请求出口**(唯一注入点)。调用方必须经
    `_net._get_json` 属性访问,不得 import 期绑定——理由见模块 docstring。
    替换它即零网络(离线组"真的不联网"的实现基础)。
    """
    _config._RATE.wait(rate)
    _config._up_inc()
    req = urllib.request.Request(url, headers=HDRS)
    with urllib.request.urlopen(req, timeout=20) as r:
        d = json.loads(r.read().decode("utf-8", "replace"))
    # 上游"假 200":HTTP 200 但 body 是错误壳(如 text 超 100 字符的 errorCode:409)。
    # 不识别会把"查询超限"静默降级成"无匹配结果"。
    if isinstance(d, dict) and d.get("errorCode"):
        raise UpstreamError(int(d["errorCode"]), str(d.get("message") or "")[:200])
    return d

