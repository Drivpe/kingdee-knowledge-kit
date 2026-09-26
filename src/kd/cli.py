#!/usr/bin/env python3
"""kd.cli —— 金蝶官方知识 CLI 的命令面(argparse + JSON 输出)。

契约(不变):stdout 只出 JSON,进度/日志走 stderr,错误是带 hint 的 JSON,
退出码 0=成功 / 1=上游或内部错误 / 2=用法错误(argparse),永不交互、永无 ANSI 色码、强制 UTF-8。

命令面三条:search / read / health。`ask` 已于 2026-09-18 删除(ADR-0013:
两套排序哲学并存、RRF 是有损压缩;检索收敛为「清单 → 挑选 → 全读」一条路径)。
`share` 已于 2026-09-17 删除。子命令一律**真正删除**,不留 "unsupported" 占位
——占位会把「已知失败」混进后续回归基线。
"""
import argparse
import json
import sys

from . import core

# 单一真源:版本号与类型白名单都从实现体取,不在本文件复制字面量。
_IMPL = core._impl()

_VERSION = _IMPL.VERSION
_VALID_KINDS = _IMPL.ENTITY_KINDS
_VALID_TYPES = _IMPL.ENTITY_KINDS


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
            "example": 'kd search "信用额度控制"',
            "limit": e.limit,
            "length": len(e.original),
            "clamped": e.clamped,
        }})
        sys.exit(1)
    except core.InternalError as e:
        _usage_error(str(e), hint="查看用法: kd --help", example='kd search "信用额度控制"')
    except core.UpstreamError as e:
        _fail("upstream_error", "上游业务错误 errorCode=%s: %s" % (e.code, e.message),
              hint="上游以 HTTP 200 返回错误壳(常见于 text 超 100 字符或接口变更);请勿高频重试",
              example='kd search "信用额度控制"')
    except KeyError as e:
        _fail("bad_argument", "无法识别的参数: %s" % e,
              hint="查看用法: kd --help", example='kd search "信用额度控制"')
    except Exception as e:
        _fail("internal_error", "%s: %s" % (type(e).__name__, str(e)[:200]),
              hint="内核异常(诊断信息见 stderr);这是 bug 而非用法问题,请带上 stderr 内容反馈",
              example='kd search "信用额度控制"')


def cmd_search(a):
    pack = _guard(lambda: core.search(a.text, keywords=a.kw, product_id=a.product,
                                      page=a.page, page_size=a.size, global_=a.global_,
                                      type_=a.type, max_routes=a.max_routes,
                                      budget=a.budget))
    _prog("多路拆解 %d 路: %s" % (len(pack.get("queries") or []),
                                  " | ".join(str(q) for q in pack.get("queries") or [])))
    if pack.get("routesDegraded"):
        _prog("路数塌缩:计划 %s 路,去重后实际 %d 路"
              % (pack.get("routesPlanned"), len(pack.get("queries") or [])))
    if pack.get("budget_exhausted"):
        _prog("上游预算耗尽,清单不完整")
    _out(pack)


def cmd_read(a):
    if a.chunk:
        # 官方 AI 引用 chunkId 溯源(ADR-0007 / 票 #20 的解析端)。
        # chunk 溯源能力从未落地:官方无「按文档列出全部 chunk」端点,
        # chunkId 的唯一来源是登录态 SSE 终止帧与官方分享对话 —— 消费端备好但无产出端可喂。
        _fail("chunk_not_in_core", "--chunk(官方 AI 引用 chunkId 溯源)尚未并入 kd.core",
              hint="该能力未落地:官方无「按文档列出全部 chunk」端点,chunkId 只能来自登录态;"
                   "消费端已移除,当前无可用入口")
    _out(_guard(lambda: core.read(a.kind, a.id)))


def cmd_health(_a):
    """内核自检(无服务、无端口语义):只验证库可 import、配置可读、公开面完整。

    与旧 /health 的差别:不再报"服务存活",也没有端点列表与 db/corpus/landing 计数
    ——那些对象在去服务化后已不存在,报它们等于承诺不存在的能力。
    """
    # 内部件在私有实现包里(kd._impl),kd.core 顶层不暴露。
    # 自检本就要读内部件,故经 core._impl() 观测口取——输出字段与取值逻辑不变。
    _cp = core._impl()
    cfg = _cp._route_cfg()
    routes_max = int(cfg.get("maxRoutes") or 7)
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
        "budgetMax": _cp._cfg_budget_search_max(),
        "rateProfile": _cp._rate_profile(),
        "textMax": _cp.UPSTREAM_TEXT_MAX,
        "commands": ["search", "read", "health"],
        "note": "单入口检索(ADR-0013,2026-09-18):kd ask 已删除,search 是唯一检索入口"
                "(多路拆词 + 只出标题清单 + 零排序评分),要全文走 kd read。"
                "去服务化(工单 #20):kd 进程内直连 kd.core,不依赖 127.0.0.1:4097,"
                "不读 KSEARCH_URL。",
    })


def build_parser():
    p = argparse.ArgumentParser(
        prog="kd",
        description="金蝶官方知识 CLI(匿名免费:零账号/零点数/零模型)。AI-first:默认输出 JSON,"
                    "stdout=数据 stderr=进度。检索收敛为一条路径(ADR-0013):"
                    "kd search 出多路清单(只给标题级信息)→ 你按标题匹配度挑 → kd read 取全文 →"
                    " 你按 docs/ANSWER-SPEC.md 合成回答。"
                    "本套件只产清单/全文、不合成回答(ADR-0008)——排序由上游综合排序决定,"
                    "内核只去重,不产生任何评分。",
        epilog='示例:\n'
               '  kd search "应用为禁用状态[网关]" --product 93   # 唯一检索入口:多路清单,带 hitRoutes\n'
               '  kd search --kw "2510" --kw "应用为禁用状态[网关]"  # 稀有 token 抢第 1 路 + 原句保召回\n'
               '  kd read 402990431979506944                       # 从清单挑出 id 再读全文(kind 照抄 type)\n'
               '  kd health                                        # 内核自检(库模式,无服务)',
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version="kd %s(library mode)" % _VERSION)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("search", help="唯一检索入口:多路拆词检索,只出标题清单"
                                      "(每路 pageSize=10,按路序+上游原生序;要全文再用 kd read)",
                       epilog='示例:\n'
                              '  kd search "应用为禁用状态[网关]" --product 93   # 清单带 hitRoutes/routes\n'
                              '  kd search --kw "2510" --kw "应用为禁用状态[网关]" --product 93  # 稀有 token 抢第 1 路 + 原句保召回\n'
                              '  kd search --kw "信用额度" --kw "应收单 信用"     # 显式关键词(LLM 拆词入口;第 1 词充原句路)\n'
                              '  kd search "信用额度控制" --type answer          # 类型过滤(每路各带,独立跨页扫描)\n'
                              '  kd search "信用额度控制" --max-routes 1         # 退化为单路(上游原生序)\n'
                              '  kd read 402990431979506944                      # 从清单里挑出的 id 再读全文',
                       formatter_class=argparse.RawDescriptionHelpFormatter)
    s.add_argument("text", nargs="?", default=None,
                   help="关键词(具体功能名/业务名词/报错词);只给 --kw 时可省略")
    s.add_argument("--kw", action="append", default=None,
                   help="显式关键词(可重复,每词一路)——LLM 拆好词的入口。"
                        "原句路恒常存在:给了 text 由 text 充任,只给 --kw 时第 1 个 --kw 充任")
    s.add_argument("--product", type=int, default=93,
                   help="93=星空旗舰版(默认) 87=苍穹 1=企业版/标准版 0=不过滤(显式指定才生效)")
    s.add_argument("--type", choices=list(_VALID_TYPES), default=None, help="按实体类型过滤")
    s.add_argument("--page", type=int, default=1,
                   help="清单分页页码(作用于多路去重后的清单,非上游分页)")
    s.add_argument("--size", type=int, default=10,
                   help="清单每页条数(作用于清单,非上游分页;近上游默认 10)")
    s.add_argument("--max-routes", dest="max_routes", type=int, default=None,
                   help="最多用几路拆词(默认取 maxRoutes=7;1=单路精确)")
    s.add_argument("--global", dest="global_", action="store_true", help="跨全部产品")
    s.add_argument("--budget", type=int, default=None,
                   help="上游请求硬上限覆盖(默认 24,超限即停并置 budget_exhausted)")
    s.set_defaults(fn=cmd_search)

    s = sub.add_parser("read", help="取全文:先用 kd search 出清单,再 kd read 挑中的条目"
                                    ";--kind 照抄清单里的 type 字段"
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
                   help="按官方 AI 引用 chunkId 匿名读块全文(ADR-0007;该能力未落地,"
                        "当前无可用入口——执行时报错而非静默忽略)")
    s.set_defaults(fn=cmd_read)

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
