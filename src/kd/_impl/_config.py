#!/usr/bin/env python3
"""kd._impl._config —— 单一真源常量、配置读取、预算与限速。

本模块只依赖标准库,是全包的依赖汇点:改版本号/预算档/限速只改这里,
不会牵动检索逻辑。
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
# 版本号:pyproject.toml 的 version 与此处一致(6.5.0 = 6.5 的三段写法),
# __init__.__version__ 与 cli._VERSION 均从此处取。
# 6.5 = 全面修复(ADR-0015,2026-09-28):链接口径按 kind 分档、稀有数字 token 抢第 1 路、
#       字段集检查改三段对账、read 的 budget 契约统一、「按标题挑」判据入文档。
# 6.4 = 契约重构(决策 D4-D14,2026-09-27):清单改**帖子级**、type/kind 统一改名
#       question、产品线字面推导整体删除、清单分页删除、字段集收敛并收进 contract.json。
# 6.3 = 单入口检索(ADR-0013):kd ask 删除、公开面收敛为 search/read + 三异常、零算法排序。
VERSION = "6.5"

# 实体类型白名单:search 的 --type 与 read 的 --kind 共用同一集合。
# ⚠️ 第三个值是 `question` 而**不是上游协议里的 `answer`**(决策 D5):上游
# `entity-type` 仍是 "Answer",映射点**只在 `_norm_item` 一处**(见 _upstream)。
# 此集合之外任何地方出现 "answer" 都是未映射的上游原始值泄漏。
ENTITY_KINDS = ("knowledge", "question", "article")

# 包内数据文件:拆解规则/预算/限速档(语料可配置;上游迁移时随包走)。
_ROUTE_CFG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                               "query_routes.json")
_ROUTE_CFG = None

# 对外契约声明(单行来源,决策 D13):清单字段集 / 禁止键 / 产品线编号表。
# 与 query_routes.json 同目录 —— 随包安装,装到用户机器上也能读到(测试/文档目录
# 装过去就没有)。读取失败回落空表,**不抛错**(与 _route_cfg 同纪律):声明缺失
# 时内核继续用内置兜底键集,不让"少一个数据文件"炸掉检索主链路。
_CONTRACT_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                              "contract.json")
_CONTRACT = None

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


def _route_cfg():
    """拆解规则/预算/限速配置(数据文件在包内,沉淀词表只改 query_routes.json)。"""
    global _ROUTE_CFG
    if _ROUTE_CFG is None:
        try:
            with open(_ROUTE_CFG_PATH, encoding="utf-8") as f:
                _ROUTE_CFG = json.load(f)
        except Exception as e:
            log("query_routes.json load fail:", str(e)[:120])
            _ROUTE_CFG = {}
    return _ROUTE_CFG


def cfg_max_routes():
    """计划路数上限(默认 7)。配置缺失时用兜底值,不抛错。"""
    return int(_route_cfg().get("maxRoutes") or 7)


def _contract():
    """对外契约声明(清单字段集 / 禁止键 / 产品线编号表)。读失败回落空表。"""
    global _CONTRACT
    if _CONTRACT is None:
        try:
            with open(_CONTRACT_PATH, encoding="utf-8") as f:
                _CONTRACT = json.load(f)
        except Exception as e:
            log("contract.json load fail:", str(e)[:120])
            _CONTRACT = {}
    return _CONTRACT


# 清单条目的**内置兜底键集**:仅在 contract.json 缺失/无该段时生效。
# 为什么留兜底而不是"声明缺失就报错":字段集是渲染细节,不是安全闸;
# 为它中断检索等于让一个数据文件决定套件能否工作(query_routes.json 同纪律)。
# ⚠️ 兜底集必须与 contract.json 的 resultKeys 保持一致 —— 由离线回归用例钉住
# (它同时读两边,任一处漂移即红),不靠人工誊抄。
_FALLBACK_RESULT_KEYS = ("type", "id", "title", "url", "hitRoutes", "routes", "snippet",
                         "products", "adopted", "answersCount", "comments", "supports",
                         "questionBody")
_FALLBACK_FORBIDDEN_KEYS = ("contentText", "fusedScore", "chunks", "contentLen", "useful",
                            "views", "updatedAt", "questionId")


def result_keys():
    """清单条目允许出现的键集(从 contract.json 声明取)。"""
    keys = ((_contract().get("search") or {}).get("resultKeys")
            or list(_FALLBACK_RESULT_KEYS))
    return tuple(str(k) for k in keys)


def result_forbidden_keys():
    """清单条目**出现即 FAIL** 的历史残留键集(从 contract.json 声明取)。"""
    keys = ((_contract().get("search") or {}).get("resultForbiddenKeys")
            or list(_FALLBACK_FORBIDDEN_KEYS))
    return tuple(str(k) for k in keys)


def top_keys():
    """search 顶层键集(从 contract.json 声明取)。"""
    return tuple(str(k) for k in ((_contract().get("search") or {}).get("topKeys") or []))


def link_policy():
    """链接政策(从 contract.json 的 `linkPolicy` 取):`{kind: "link"|"no-link"}`。

    ⚠️ 2026-09-28 起**代码真的读它**。此前它是 `"status": "pending"` 的纯占位、
    无任何消费者——于是文档抄了四遍口径各异(README 与 SKILL/ANSWER-SPEC 正面对撞),
    而声明这一份谁也没看。现在它是**唯一真源**:文档指向它,回归用例对着它断言。

    读取失败回落"全部不给链接"——**保守方向**是少给一个链接(读者损失一次跳转),
    而不是多给一个死链(读者以为资料不存在)。与 `_route_cfg` 同纪律:不抛错。
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


def _cfg_budget_search_max():
    """search 的上游请求硬上限默认档。

    语义:只出清单、不做深读。7 路 × 1 页 = 7 次基础请求,余量留给 type_ 的
    每路独立跨页扫描(≤5 页/路)与重试,故默认 24(可在 query_routes.json
    的 budget.maxUpstreamPerSearch 配置,或用 KSEARCH_SEARCH_BUDGET 覆盖)。
    """
    v = os.environ.get("KSEARCH_SEARCH_BUDGET")
    if v and str(v).isdigit():
        return int(v)
    cfg = _route_cfg()
    b = (cfg.get("budget") or {}).get("maxUpstreamPerSearch")
    if b:
        return int(b)
    # 兜底:7 路基础 + type_ 扫描余量(仅配置缺失时生效,有意保守)
    return cfg_max_routes() * 2 + 10


class _Budget:
    """单次 search 的上游请求硬上限。

    并发纪律(工单 #29):本类**全部**状态读写都在 `self._lock` 下进行。
    `acquire()` 把"检查余量"与"占用名额"合并为一次原子操作,消除
    check-then-act 竞态——若不原子,多个进入者可同时通过检查再各自自增,
    实际请求数溢出,`max` 就不再是硬上限。
    ⚠️ 归因更正(2026-09-27):本注释原写"深读路径经 ThreadPoolExecutor 并发进入",
    但 `ThreadPoolExecutor` 随 `ask` 一并下线,全仓已无任何线程池
    (`grep -rIn "ThreadPoolExecutor|concurrent" src/` 只剩注释本身)。
    锁仍然是必要的:`_fetch_for_item` 等路径与调用方的并发调用仍会进入本类,
    且"预算必须是硬上限"这一对外承诺不依赖某个特定并发实现而成立。
    """

    def __init__(self, max_upstream):
        self._lock = threading.Lock()
        # budget=0 是合法入参,语义为"零上游请求"(不是"未设限")。原写法
        # `int(x or 0) or None` 把 0 折成 None,等于把"禁网"读成"无限"——既是
        # 参数语义错,也直接击穿"budget 是硬上限"的对外承诺(见工单 #29)。
        # 只有 None / 空值才视为未设限。
        self.max = None if max_upstream is None else int(max_upstream)
        self.used = 0
        self.exhausted = False

    def acquire(self):
        """原子领取一个上游名额:有余量则占用并返回 True;耗尽则标记并抛 _BudgetExhausted。

        检查与自增在同一临界区内完成,不可分割——这是工单 #29 的核心修复点。
        """
        with self._lock:
            if self.max is not None and self.used >= self.max:
                self.exhausted = True
                raise _BudgetExhausted()
            self.used += 1
            return True

    def remaining(self):
        """持锁读余量(max 为 None 时视为无限)。用于编排层的"还剩多少"判断。"""
        with self._lock:
            if self.max is None:
                return None
            return max(0, self.max - self.used)

    def mark_exhausted(self):
        """持锁标记耗尽(供编排层在读余量后补记,保证标记不丢)。"""
        with self._lock:
            self.exhausted = True

    def snapshot(self):
        """持锁取 (used, max, exhausted) 一致性快照。"""
        with self._lock:
            return self.used, self.max, self.exhausted


class _BudgetExhausted(Exception):
    """预算耗尽信号:停止发起上游请求,返回已获资料。"""


class _RateLimiter:
    """上游限速(匿名链路):令牌桶实现,只对真实上游请求生效(_get_json 入口)。

    配置里只有 interactive 一档。曾存在 background 档(1 req/s)与两条切档通路
    (请求级 set_profile、环境变量 KSEARCH_RATE),两者均已删除:该档的唯一生产者
    是已随去服务化删除的摄取/评测脚本,场景不存在。
    公开签名 `rate=` 保留(对外契约);传入配置中不存在的档名时回落到保守默认,
    不报错、不静默切档。
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._next = 0.0

    def profile(self, name=None):
        cfg = _route_cfg().get("rate") or {}
        wanted = str(name or "interactive").lower()
        p = cfg.get(wanted)
        if not isinstance(p, dict):
            # 配置缺失/档名不存在:回落 interactive(配置里有则用它的数值,没有则用
            # 保守默认)。档名一并归一,避免"报 interactive 却按保守值限速"的名实不符。
            p = cfg.get("interactive")
            wanted = "interactive"
            if not isinstance(p, dict):
                p = {"burst": 1, "rps": 1.0, "jitterMs": [0, 120]}
        return wanted, p

    def wait(self, name=None):
        pname, p = self.profile(name)
        rps = float(p.get("rps") or 2.5)
        burst = max(1, int(p.get("burst") or 1))
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
