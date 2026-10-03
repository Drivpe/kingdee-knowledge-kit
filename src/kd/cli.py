#!/usr/bin/env python3
"""kd.cli —— 金蝶官方知识 CLI 的命令面(argparse + JSON 输出)。

契约(不变):stdout 只出 JSON,进度/日志走 stderr,错误是带 hint 的 JSON,
退出码 0=成功 / 1=上游或内部错误 / 2=用法错误(argparse),永不交互、永无 ANSI 色码、强制 UTF-8。

命令面三条:search / read / health。`ask` 已于 2026-09-18 删除(ADR-0013)。
子命令一律**真正删除**,不留 "unsupported" 占位——占位会把「已知失败」混进回归基线。

⚠️ **v6.6 参数面破坏性变更**(ADR-0016 决策 3,2026-09-28):
  * **删位置参数**:`kd search "整句"` 不再成立,改 `kd search --kw "词1" --kw "词2"`。
    内核不再拆词(决策 1),检索词只来自调用方,`--kw` 是唯一入口。
  * **删 `--type`**:调用方从清单条目的 `type` 字段自己筛类型。
  * **删 `--max-routes`**:上限只在声明里一个值(`limits.maxKeywords`),超限按顺序取前 N。
  * **删 `--budget`**:每路恒 1 次请求,预算永不可触发。
  * **删 `--chunk`**:解析后立刻报错的空参数,连帮助文本一并删(与已删的 `ask` 同处理)。
  * **`--product` 三态收两态**:不传与传 `None` 同义(都是默认),不过滤只由显式 `0` 表达。
"""
import argparse
import json
import sys

from . import core

# 单一真源:版本号与类型白名单都从实现体取,不在本文件复制字面量。
_IMPL = core._impl()

_VERSION = _IMPL.VERSION
# kind 白名单只有一个来源:实现体的 ENTITY_KINDS。`--type` 已随 v6.6 删除,
# 现在只有 `read --kind` 用它(集合语义仍是"实体类型白名单")。
_VALID_KINDS = _IMPL.ENTITY_KINDS


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


def _guard(fn, op="search"):
    """统一错误映射:core 的异常 → 带 hint 的 JSON + 退出码 1(用法类 → 2)。

    `op` 让 hint 与**实际发生的事**对应。此前 hint 全部写死成 search 的语境,
    于是 `kd read <错id>` 拿到上游 404 时提示会说"text 超 100 字符"——与 read
    毫无关系,把排查方向直接带偏(工单 #24 记录的误导项)。
    """
    read_op = (op == "read")
    _ex = 'kd read 402990431979506944' if read_op else 'kd search --kw "信用额度控制"'
    try:
        return fn()
    except core.QueryTooLong as e:
        # 上游 100 原始字符硬闸:压回值随错误一并返回,是否重试由调用方决定。
        _out({"error": {
            "code": "query_too_long",
            "message": str(e),
            "hint": "上游 text 硬上限 %d 原始字符(含标点/空格):超限上游返回 errorCode:409 空壳。"
                    "请精简后重试,或用 clamped 里的压回值重发。" % e.limit,
            "example": _ex,
            "limit": e.limit,
            "length": len(e.original),
            "clamped": e.clamped,
        }})
        sys.exit(1)
    except core.InternalError as e:
        # ⚠️ **`other` 档不是用法错误**(2026-09-29,code review L10):它是**内核承认的
        # 类型但没有详情端点**(能力边界)—— 报 `usage` + "查看用法: kd --help" 与它
        # 自己的 message("你其实没传错")正相矛盾,会把调用方引向"改参数"而那不是解法。
        # 故按能力边界报 `unsupported_kind`(退出码仍是 1:这不是命令行用错)。
        #
        # ⚠️ **分类只读 `e.code`,不对 message 做子串匹配**(2026-09-29 收口):
        # 原先这里写 `if "没有它的全文端点" in str(e) or "合法类型" in str(e)` —— 分类
        # 挂在**文案**上,于是改一个措辞就让 `unsupported_kind`(exit 1)无声退化成
        # `usage`(exit 2),而那一档当时**零回归覆盖**,漂移没有任何信号。
        # 现在分类由**抛出处**声明(`_detail` 里 `code="unsupported_kind"`),文案与分类
        # 彻底解耦;本文件里再出现任何文案子串判断都算回归(有用例按源码钉住)。
        if getattr(e, "code", None) == "unsupported_kind":
            _fail("unsupported_kind", str(e),
                  hint="这是**已知的能力边界**,不是参数写错:该档收容的上游罕见实体"
                       "没有统一可用的详情端点。清单条目已给出 title 与 upstreamType,"
                       "可据此判断要不要另找途径。",
                  example='kd search --kw "课程" --include-other')
        # 兜底:**未带分类**的 InternalError 仍按用法错误处理(含全部"参数写错"的形态:
        # 未知 kind / id 为空 / keywords 缺失等),与改动前逐字相同。
        _usage_error(str(e), hint="查看用法: kd --help", example=_ex)
    except core.UpstreamError as e:
        if read_op:
            _fail("upstream_error", "上游业务错误 errorCode=%s: %s" % (e.code, e.message),
                  hint="按 id 取全文时上游报错。常见原因:传的 id 与 kind 不匹配"
                       "(kind 照抄清单条目的 type 字段),或该实体已被上游删除/迁移。"
                       "请回到 search 清单照抄条目的 id/type 后重试。",
                  example=_ex)
        _fail("upstream_error", "上游业务错误 errorCode=%s: %s" % (e.code, e.message),
              hint="上游以 HTTP 200 返回错误壳(常见于检索词超 100 字符或接口变更);请勿高频重试",
              example=_ex)
    except KeyError as e:
        _fail("bad_argument", "无法识别的参数: %s" % e,
              hint="查看用法: kd --help", example=_ex)
    except Exception as e:
        _fail("internal_error", "%s: %s" % (type(e).__name__, str(e)[:200]),
              hint="内核异常(诊断信息见 stderr);这是 bug 而非用法问题,请带上 stderr 内容反馈",
              example=_ex)


def cmd_search(a):
    # ⚠️ `--product` 不传时**不传该实参**,让内核用它自己的签名默认(= 声明里的
    # 产品线默认编号)。不能写成 `product_id=a.product` + argparse default=None:
    # 那会把"CLI 用户没传"表达成"显式 None",进而无法区分"没传"与"显式不过滤"
    # ——本内核的语义是"不传即默认",而"不过滤"只由显式 0 表达(ADR-0016 决策 3)。
    kw = {} if a.product is None else {"product_id": a.product}
    pack = _guard(lambda: core.search(keywords=a.kw, global_=a.global_,
                                      include_other=bool(a.include_other), **kw))
    _prog("多路检索 %d 路: %s" % (len(pack.get("queries") or []),
                                  " | ".join(str(q) for q in pack.get("queries") or [])))
    if pack.get("otherSkipped"):
        # 罕见类型默认隐藏,但**必须可见**(工单 #32):否则"清单比实际召回少了一截"
        # 的差额又会变成无解释的静默。
        _prog("隐藏 %d 条罕见类型(课程/路径/专题等;需 --include-other 才返回)"
              % pack.get("otherSkipped"))
    if pack.get("keywordsDropped"):
        # ADR-0016 决策 4:丢词必须在人读进度里也可见(stderr),不止在返回体里。
        _prog("收词超上限:丢弃 %d 个词(只发前 %d 个)"
              % (pack.get("keywordsDropped"), len(pack.get("queries") or [])))
    _out(pack)


def cmd_read(a):
    _out(_guard(lambda: core.read(a.kind, a.id), op="read"))


def cmd_health(_a):
    """内核自检(无服务、无端口语义):只验证库可 import、配置可读、公开面完整。

    与旧 /health 的差别:不再报"服务存活",也没有端点列表与 db/corpus/landing 计数
    ——那些对象在去服务化后已不存在,报它们等于承诺不存在的能力。
    """
    # 内部件在私有实现包里(kd._impl),kd.core 顶层不暴露。
    # 自检本就要读内部件,故经 core._impl() 观测口取——输出字段与取值逻辑不变。
    _cp = core._impl()
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
        # ⚠️ v6.6:数据文件收敛为一个(contract.json)。原 routesCfg/routesCfgLoaded/
        # budgetMax 三个字段随 query_routes.json 与预算机制一并删除——报它们等于
        # 承诺不存在的能力(与"不留 unsupported 占位"同纪律)。
        "contractCfg": _cp._CONTRACT_PATH,
        "contractCfgLoaded": bool(_cp._contract()),
        "maxKeywords": _cp.max_keywords(),
        "rateProfile": _cp._rate_profile(),
        "textMax": _cp.UPSTREAM_TEXT_MAX,
        "commands": ["search", "read", "health"],
        "note": "单入口检索(ADR-0013,2026-09-18):kd ask 已删除,search 是唯一检索入口"
                "(多路关键词 + 只出标题清单 + 零排序评分),要全文走 kd read。"
                "⚠️ v6.6(ADR-0016):内核**不再生成任何检索词** —— 检索词只来自调用方"
                "(--kw),内核原样按序发送;拆词规范见 SKILL.md。"
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
                    "内核只去重,不产生任何评分。"
                    "⚠️ 内核**不拆词**(ADR-0016):你必须先按 SKILL.md 的拆词规范把问题拆成"
                    "关键词,再经 --kw 传入。",
        epilog='示例:\n'
               '  kd search --kw "应用为禁用状态[网关]" --kw "2510"   # 唯一检索入口:每词一路,按序\n'
               '  kd search --kw "信用额度控制" --kw "应收单 信用" --product 93\n'
               '  kd read 402990431979506944                        # 从清单挑出 id 再读全文(kind 照抄 type)\n'
               '  kd health                                         # 内核自检(库模式,无服务)',
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version="kd %s(library mode)" % _VERSION)
    sub = p.add_subparsers(dest="cmd", required=True)

    # ⚠️ help 文案里的内核数值**从单一来源取**,不再写死字面量(2026-10-01,D2 同型收口):
    # 原先这里写死 `pageSize=10` 与 `默认 7 词`,而它们是 `_PER_ROUTE_WANT` 与
    # `limits.maxKeywords` 的值 —— 改了内核而文案没跟上时,用户看到的是**错的说明**,
    # 且**没有任何信号**(本仓纪律:「生存期只有一处真相」)。
    s = sub.add_parser("search", help="唯一检索入口:多路关键词检索,只出帖级标题清单"
                                      "(每路 pageSize=%d,按你给词的顺序;要全文再用 kd read)"
                                      % _IMPL._PER_ROUTE_WANT,
                       epilog='示例:\n'
                              '  kd search --kw "应用为禁用状态[网关]" --kw "2510" --product 93\n'
                              '  kd search --kw "信用额度" --kw "应收单 信用"   # 拆好的词,按序发,每词一路\n'
                              '  kd search --kw "信用额度控制" --product 0      # 0=不过滤(显式才生效)\n'
                              '  kd read 402990431979506944                     # 从清单里挑出的 id 再读全文',
                       formatter_class=argparse.RawDescriptionHelpFormatter)
    # ⚠️ 位置参数 `text` 已删除(ADR-0016 决策 3):内核不拆词,`--kw` 是唯一入口。
    s.add_argument("--kw", action="append", default=None, required=True,
                   help="检索词(可重复,**每词一路**)。**内核原样、按你给的顺序发送**"
                        "(不拆解/不扩充/不前置/不排序)。顺序即召回顺序:排序键第一维是"
                        "『你给的第几个词』。拆词规范见 SKILL.md 的「拆词规范」节。"
                        "超过上限(默认 %d 词)按顺序取前 N 个,返回体写明 keywordsDropped"
                        % _IMPL.max_keywords())
    s.add_argument("--product", type=int, default=None,
                   help="93=星空旗舰版(默认) 87=苍穹 1=企业版/标准版 2=星空侧二开问答专区 "
                        "0=不过滤(显式指定才生效)。产品线由你判定;不传即默认,内核不做字面推导")
    s.add_argument("--global", dest="global_", action="store_true", help="跨全部产品")
    s.add_argument("--include-other", dest="include_other", action="store_true",
                   help="**返回「其他」档**(默认隐藏)。上游除三类已知实体外还返回罕见"
                        "类型(课程/学习路径/专题/直播),它们质量低且不是同一种资料,"
                        "故默认不混进清单。隐藏时返回体写明跳过了多少条"
                        "(otherSkipped),故『没看到』永远能区分是开关还是上游没有。"
                        "⚠️ 这一档不给网页链接(实测无可点形式)")
    s.set_defaults(fn=cmd_search)

    s = sub.add_parser("read", help="取全文:先用 kd search 出清单,再 kd read 挑中的条目"
                                    ";--kind 照抄清单里的 type 字段"
                                    "(knowledge=官方文档/question=问答帖全文/article=社区文章)",
                       epilog='示例:\n'
                              '  kd read 402990431979506944                    # knowledge 条目 → 官方文档全文\n'
                              '  kd read 799346568250934528 --kind question    # question 条目 → 问题+全部回答+追问链(传帖子号)\n'
                              '  kd read 56784392135739905 --kind article      # article 条目 → 社区文章全文',
                       formatter_class=argparse.RawDescriptionHelpFormatter)
    s.add_argument("id", help="search 结果条目的 id(帖子级:问答条目传的就是帖子号)")
    s.add_argument("--kind", choices=list(_VALID_KINDS), default="knowledge",
                   help="实体类型,照抄 search 结果的 type 字段(默认 knowledge)")
    s.set_defaults(fn=cmd_read)

    s = sub.add_parser("health", help="内核自检(库模式:无服务、无端口、无 HTTP)",
                       epilog="本命令自检内核本身,不再探测服务——去服务化后没有服务可探。",
                       formatter_class=argparse.RawDescriptionHelpFormatter)
    s.set_defaults(fn=cmd_health)

    return p


def _harden_console():
    """把控制台 I/O 的**错误处理**降级为替换,保留控制台自身编码(2026-10-01,I2)。

    ⚠️ **为什么必须在 `parse_args()` 之前**:`argparse` 的 `print_help()` 与用法错误
    **直接写 `sys.stdout` / `sys.stderr`**,而 `_out` / `_prog` 里的 `reconfigure`
    **保护不到它们** —— `--help` 在 `_out` 之前就 `SystemExit` 了。
    实测(zh-CN Windows 默认控制台 = cp936/GBK):`kd --help` 抛
    `UnicodeEncodeError: 'gbk' codec can't encode character '\\u26a0'`、**退出码 1**
    (文案里的 `⚠️`)。连带后果是 `install.ps1` 的验证闸门 —— 它跑的就是含
    `t_cli_help`(断言 `--help` 退出码 0)的离线回归 —— 在中文 Windows 上**必然判红**。
    任意平台可复现(故回归钉子是离线、跨平台的):
        PYTHONIOENCODING=gbk python3 src/kd_run.py --help

    做法是**只改 `errors`,不改 `encoding`**:帮助文案里的中文在 cp936 上仍可读,
    编不出的字符(`⚠️` 这类)降级成 `?`。⚠️ **不得**在这里设 `encoding="utf-8"` ——
    控制台编码不该由我们改写;JSON 输出的**字节级稳定**由 `_out` 单独保证
    (agents 会解析它,那一条不动)。
    失败时静默(参考 `_prog` 的既有写法:`stdout` 可能是 `StringIO`,没有 `reconfigure`)。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:
            pass


def main(argv=None):
    _harden_console()
    a = build_parser().parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
