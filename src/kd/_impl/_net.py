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
import http.client
import urllib.request

from . import _config
from ._config import HDRS, UPSTREAM_TEXT_MAX
from ._errors import QueryTooLong, UpstreamError

# ---- 「什么算上游故障」的**单一分类来源**(2026-09-29,code review High-1 收口) ----
# 本仓库有两类失败混在一起,必须分开对待:
#   * **上游故障** —— 上游侧的原因,调用方该**重试**;已取回的数据不得因它丢弃;
#   * **程序缺陷** —— 我们自己的 bug(AttributeError/TypeError 等),应当**响亮地穿透**
#     成 `internal_error`("这是 bug 而非用法问题"),**不得**被伪装成"上游抖动"。
# 本元组只收前者,四态各自对应一种真实观测到的上游失败形态:
#   ① `UpstreamError` —— 上游"假 200":HTTP 层成功而 body 带 `errorCode`(见下);
#   ② `OSError`       —— 传输层:`URLError`(不可达/DNS/TLS)、`HTTPError`、
#                        `TimeoutError`/`socket.timeout`(20s 超时)、
#                        `ConnectionError`(`RemoteDisconnected`/`ConnectionReset`)、
#                        `ssl.SSLError` —— 这些都是 `OSError` 的子类;
#   ③ `json.JSONDecodeError` —— 响应不是合法 JSON(网关错误页、截断的 body);
#   ④ `http.client.HTTPException` —— **读响应体阶段**的 HTTP 协议层故障。
#      ⚠️ **这一类不是 `OSError` 子类**(它是 `Exception` 的直接子类),故**必须单列**:
#      实测 `isinstance(http.client.IncompleteRead(b"x"), OSError)` → **False**。
#      `IncompleteRead`(响应体读到一半连接断)与 `BadStatusLine`(状态行非法/为空)
#      在 `urlopen` + `r.read()` 的路径上**会真实发生**,属典型网络抖动形态。
#      (注:`RemoteDisconnected` 同时继承 `ConnectionResetError`,故已被 ② 覆盖;
#       这里单列是为了覆盖 `IncompleteRead` / `BadStatusLine` 这两个漏网的。)
# ⚠️ **刻意不用裸 `Exception`**:那会把程序缺陷一并吞成"上游故障" —— 那是把
# "我们写错了"伪装成"上游抖了",与本仓禁忌同型。
# ⚠️ **已知的过宽面(如实声明)**:`OSError` 也覆盖 `FileNotFoundError`/`PermissionError`
# 等本地文件错误。在本模块的真实路径上(`urlopen` 只发 https,不碰本地文件)它们
# **不会出现**,故不为此收窄 —— 收窄会丢掉 `ssl.SSLError`/`TimeoutError` 等真实形态,
# 得不偿失。若将来本模块引入本地文件读取,这条边界需要重新审视。
# ⚠️ **由此带来的一处口径放宽,如实声明**(2026-09-29,对抗性核实指出):
# `http.client.HTTPException` 也包含 **`InvalidURL`** —— 当**调用方传入的 id 畸形**
# 时(实测 `qid='907\nX'` → `InvalidURL`),它会被归成 `upstream_error`(可重试的
# 上游抖动),而严格说那是**输入问题**。选择接受这一放宽的理由:
#   * 该形态下无可点网页形式可补救,而 `upstream_error` **行动方向正确**(重试);
#   * 收窄它需要**逐子类白名单**(排除 `InvalidURL`),而 `HTTPException` 的子类
#     在不同 Python 版本间可能增减 —— 白名单会随版本失效,风险大于收益。
# ⚠️ 另一处对照:畸形 id 若含非 ASCII(实测 `qid='中文帖号'` → `UnicodeEncodeError`)
# **不在**本元组里,会穿透成 `internal_error`。即**两类畸形输入的对外表现不一致** ——
# 这是已知的不一致,**未修**(归入"输入校验"这一独立课题,不在本轮范围)。
# 消费者:`_detail` 的**页级 catch** 与**外层 catch**(两处必须同源,否则又会出现
# "某层认识这种失败、另一层不认识"的不对称 —— 那正是 High-1 的成因:
# `_manifest` 的检索侧早就有 `except Exception` 兜底,而深读侧只认 `UpstreamError`)。
UPSTREAM_FAILURES = (UpstreamError, OSError, json.JSONDecodeError,
                     http.client.HTTPException)


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

