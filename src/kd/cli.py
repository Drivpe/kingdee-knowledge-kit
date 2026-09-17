#!/usr/bin/env python3
"""kd.cli —— 金蝶官方知识 CLI 的命令面(argparse + JSON 输出)。

契约(不变):stdout 只出 JSON,进度/日志走 stderr,错误是带 hint 的 JSON,
退出码 0=成功 / 1=上游或内部错误 / 2=用法错误(argparse),永不交互、永无 ANSI 色码、强制 UTF-8。

去服务化(工单 #20):命令面**进程内直连 `kd.core`**,无 HTTP、无服务、无端口;
`KSEARCH_URL` 已不再被读取。检索内核是可 import 的库,`kd` 是唯一执行入口。

命令面:search / read / ask / health 四条。`share` 已删除(2026-09-17 本次重构定调:
「保持三个公开函数」优先于 ADR-0011 决策 6 的「6 个命令全部转发」)——子命令
**真正删除**,不留 "unsupported" 占位(占位会把「已知失败」混进后续回归基线)。
"""
import argparse
import json
import sys

from . import core

_VERSION = "6.2"

_VALID_KINDS = ("knowledge", "answer", "article")
_VALID_TYPES = ("knowledge", "answer", "article")


def _out(obj):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stdout.write(json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


def _prog(*a):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.write("[kd] " + " ".join(str(x) for x in a) + "\n")
    except Exception:
        pass


def _fail(code, message, hint="", example=""):
    """运行期错误:退出码 1,JSON 走 stdout(与既有契约一致)。"""
    _out({"error": {"code": code, "message": message, "hint": hint, "example": example}})
    sys.exit(1)


def _usage_error(message, hint="", example=""):
    """用法错误:退出码 2,JSON 仍走 stdout(与既有契约一致)。"""
    _out({"error": {"code": "usage", "message": message, "hint": hint, "example": example}})
    sys.exit(2)


def _guard(fn):
    """统一错误映射:core 的异常 → 带 hint 的 JSON + 退出码 1(用法类 → 2)。"""
    try:
        return fn()
    except core.QueryTooLong as e:
        # 上游 100 原始字符硬闸:压回值随错误一并返回,是否重试由调用方决定。
        _out({"error": {
            "code": "query_too_long",
            "message": str(e),
            "hint": "上游 text 硬上限 %d 原始字符(含标点/空格):超限上游返回 errorCode:409 空壳。"
                    "请精简后重试,或用 clamped 里的压回值重发。" % e.limit,
            "example": "kd ask \"信用额度控制\"",
            "limit": e.limit,
            "length": len(e.original),
            "clamped": e.clamped,
        }})
        sys.exit(1)
    except core.InternalError as e:
        _usage_error(str(e), hint="查看用法: kd --help", example="kd ask \"信用额度控制\"")
    except core.UpstreamError as e:
        _fail("upstream_error", "上游业务错误 errorCode=%s: %s" % (e.code, e.message),
              hint="上游以 HTTP 200 返回错误壳(常见于 text 超 100 字符或接口变更);请勿高频重试",
              example="kd ask \"信用额度控制\"")
    except KeyError as e:
        _fail("bad_argument", "无法识别的参数: %s" % e,
              hint="查看用法: kd --help", example="kd ask \"信用额度控制\"")
    except Exception as e:
        _fail("internal_error", "%s: %s" % (type(e).__name__, str(e)[:200]),
              hint="内核异常(诊断信息见 stderr);这是 bug 而非用法问题,请带上 stderr 内容反馈",
              example="kd ask \"信用额度控制\"")


def cmd_search(a):
    _out(_guard(lambda: core.search(a.text, product_id=a.product, page=a.page, page_size=a.size,
                                    global_=a.global_, type_=a.type)))


def cmd_read(a):
    if a.chunk:
        # 官方 AI 引用 chunkId 溯源(ADR-0007 / 票 #20 的解析端)。
        # chunk 溯源能力从未落地(CONTEXT.md「chunk 溯源」):官方无「按文档列出全部 chunk」
        # 端点,chunkId 的唯一来源是登录态 SSE 终止帧与官方分享对话 —— 消费端备好但无产出端可喂。
        # 旧入口 cli/kd.py 已随去服务化删除(工单 #22/#24),故此能力当前无任何可用入口。
        _fail("chunk_not_in_core", "--chunk(官方 AI 引用 chunkId 溯源)尚未并入 kd.core",
              hint="该能力未落地:官方无「按文档列出全部 chunk」端点,chunkId 只能来自登录态;"
                   "消费端已移除,当前无可用入口")
    _out(_guard(lambda: core.read(a.kind, a.id)))


def cmd_ask(a):
    def run():
        return core.ask(text=a.text if not a.kw else None, keywords=a.kw,
                        product_id=a.product, top_k=a.kw_topk, budget=a.budget)
    pack = _guard(run)
    b = pack.get("budget") or {}
    _prog("多路拆解 %d 路: %s" % (len(pack.get("routes") or []),
                                  " | ".join(str(r.get("terms") or "") for r in pack.get("routes") or [])))
    _prog("上游预算: %s/%s%s" % (b.get("used"), b.get("max"),
                                 "(已耗尽,返回已获资料)" if pack.get("budget_exhausted") else ""))
    _out(pack)


def cmd_health(_a):
    """内核自检(无服务、无端口语义):只验证库可 import、配置可读、公开面完整。

    与旧 /health 的差别:不再报"服务存活",也没有端点列表与 db/corpus/landing 计数
    ——那些对象在去服务化后已不存在,报它们等于承诺不存在的能力。
    """
    # 工单 #26:内部件已移入私有实现模块(kd._core_impl),kd.core 顶层不再暴露。
    # 自检本就要读内部件,故经 core._impl() 观测口取——输出字段与取值逻辑不变。
    _cp = core._impl()
    cfg = _cp._route_cfg()
    routes_max = int(cfg.get("maxRoutes") or 7)
    topk = int(((cfg.get("deepRead") or {}).get("topK")) or 4)
    missing = [n for n in core.__all__ if not hasattr(core, n)]
    _out({
        "ok": not missing,
        "service": "kd.core v%s(库模式:无服务、无端口、无 HTTP)" % _VERSION,
        "anonymous": True,
        "http": False,
        "noDiskWrite": True,   # 内核不落盘:无 landing 写穿、无 ~/.kd/ 日志
        "python": sys.version.split()[0],
        "executable": sys.executable,
        "coreApi": list(core.__all__),
        "missingApi": missing,
        "routesCfg": _cp._ROUTE_CFG_PATH,
        "routesCfgLoaded": bool(cfg),
        "maxRoutes": routes_max,
        "deepReadTopK": topk,
        "budgetMax": _cp._cfg_budget_max(),
        "rateProfile": _cp._rate_profile(None),
        "rerank": _cp.RERANK_DEFAULT,
        "textMax": _cp.UPSTREAM_TEXT_MAX,
        "commands": ["search", "read", "ask", "health"],
        "note": "去服务化重构(工单 #20):kd 进程内直连 kd.core,不依赖 127.0.0.1:4097,"
                "不读 KSEARCH_URL。share 已删除(2026-09-17 本次重构定调,非 ADR-0011;"
                "该 ADR 决策 6 原为「6 个命令全部转发」)。",
    })


def build_parser():
    p = argparse.ArgumentParser(
        prog="kd",
        description="金蝶官方知识 CLI(匿名免费:零账号/零点数/零模型)。AI-first:默认输出 JSON,"
                    "stdout=数据 stderr=进度;kd ask 是唯一常规入口(内置多路关键词拆解+预算),"
                    "search/read 是手动细粒度调试命令;本套件只产资料包、不合成回答(ADR-0008)——"
                    "调用方 agent 拿资料包按 docs/ANSWER-SPEC.md 自己合成。",
        epilog='示例:\n'
               '  kd ask "BOM分母变平方" --topk 4  # 常规问题一律用它:内置多路关键词拆解,一站式资料包\n'
               '  kd ask --kw "信用额度" --kw "应收单 信用"  # 显式关键词(跳过自动拆解)\n'
               '  kd search "信用额度控制" --product 93 --type answer  # 手动细粒度调试命令\n'
               '  kd read 402990431979506944                    # 读全文(kind 照抄 search 结果的 type)\n'
               '  kd health                                     # 内核自检(库模式,无服务)',
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version="kd %s(library mode)" % _VERSION)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("search", help="手动细粒度调试命令:检索知识库(常规问题请用 kd ask;三种实体全返回)",
                       epilog='示例: kd search "信用额度控制" --product 93 --type answer',
                       formatter_class=argparse.RawDescriptionHelpFormatter)
    s.add_argument("text", help="关键词(具体功能名/业务名词/报错词)")
    s.add_argument("--product", type=int, default=93, help="93=星空旗舰版(默认) 87=苍穹 1=企业版/标准版 0=不过滤")
    s.add_argument("--type", choices=list(_VALID_TYPES), default=None, help="按实体类型过滤")
    s.add_argument("--page", type=int, default=1)
    s.add_argument("--size", type=int, default=10, help="每页条数(≤50)")
    s.add_argument("--global", dest="global_", action="store_true", help="跨全部产品")
    s.set_defaults(fn=cmd_search)

    s = sub.add_parser("read", help="手动细粒度调试命令:读全文(常规问题请用 kd ask);"
                                    "--kind 照抄 search 结果的 type 字段"
                                    "(knowledge=官方文档/answer=问答帖全文/article=社区文章)",
                       epilog='示例:\n'
                              '  kd read 402990431979506944                    # knowledge 条目 → 官方文档全文\n'
                              '  kd read 799346568250934528 --kind answer      # answer 条目 → 问题+全部回答+追问链(传 questionId)\n'
                              '  kd read 56784392135739905 --kind article      # article 条目 → 社区文章全文',
                       formatter_class=argparse.RawDescriptionHelpFormatter)
    s.add_argument("id", help="search 结果条目的 id(answer 条目传其 questionId)")
    s.add_argument("--kind", choices=list(_VALID_KINDS), default="knowledge",
                   help="实体类型,照抄 search 结果的 type 字段(默认 knowledge)")
    s.add_argument("--chunk", action="store_true",
                   help="按官方 AI 引用 chunkId 匿名读块全文(ADR-0007;尚未并入内核,"
                        "当前需用旧入口 python3 cli/kd.py read <id> --chunk)")
    s.set_defaults(fn=cmd_read)

    s = sub.add_parser("ask", help="唯一常规入口:一站式资料包(内置多路关键词拆解 ≤7 路 RRF"
                                   "+深读 topK 全文+上游预算+召回信号摘要,供调用方 agent 合成回答)",
                       epilog='示例: kd ask "信用额度怎么控制" --topk 4 / '
                              'kd ask --kw "信用额度" --kw "应收单 信用"',
                       formatter_class=argparse.RawDescriptionHelpFormatter)
    s.add_argument("text", nargs="?", default=None, help="自然语言问题或关键词(≤100 原始字符)")
    s.add_argument("--kw", action="append", default=None, help="多关键词模式(可重复,跳过自动拆解)")
    s.add_argument("--product", type=int, default=None)
    s.add_argument("--topk", dest="kw_topk", type=int, default=None,
                   help="深读条数 1-8(默认取 query_routes.json 的 deepRead.topK=4)")
    s.add_argument("--budget", type=int, default=None, help="上游请求硬上限覆盖(默认 64,超限即停)")
    s.set_defaults(fn=cmd_ask)

    s = sub.add_parser("health", help="内核自检(库模式:无服务、无端口、无 HTTP)",
                       epilog="本命令自检内核本身,不再探测服务——去服务化后没有服务可探。",
                       formatter_class=argparse.RawDescriptionHelpFormatter)
    s.set_defaults(fn=cmd_health)

    return p


def main(argv=None):
    a = build_parser().parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
