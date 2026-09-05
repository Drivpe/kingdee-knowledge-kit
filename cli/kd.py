#!/usr/bin/env python3
"""kd —— 金蝶官方知识 CLI(AI-first,纯检索+资料包,零 LLM)
AI 是第一用户:stdout 只出 JSON 数据,进度/日志走 stderr,永不交互、永无 ANSI 色码、强制 UTF-8。
退出码契约:0=成功 1=服务/上游错误(stdout 带错误 JSON) 2=用法错误(argparse,stderr)。
所有命令默认输出 JSON。服务地址可用环境变量覆盖:KSEARCH_URL(默认 http://127.0.0.1:4097)。
合成回答由调用方 AI 完成:kd ask 返回资料包,当前模型拿包即写带引用回答。
"""
import argparse, json, os, sys, urllib.request, urllib.parse

BASE = os.environ.get("KSEARCH_URL", "http://127.0.0.1:4097")


def _out(obj):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stdout.write(json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


def _fail(code, message, hint="", example=""):
    _out({"error": {"code": code, "message": message, "hint": hint, "example": example}})
    sys.exit(1)


def _http(path, body=None, timeout=90):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    req = urllib.request.Request(BASE + path, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        try:
            payload = json.loads(e.read().decode("utf-8", "replace"))
            if isinstance(payload, dict) and payload.get("error"):
                _out(payload)
                sys.exit(1)
        except SystemExit:
            raise
        except Exception:
            pass
        _fail("http_%s" % e.code, "HTTP %s on %s" % (e.code, path),
              hint="查看服务状态: kd health", example="kd health")
    except Exception as e:
        _fail("service_unreachable", str(e)[:200],
              hint="启动服务: 见仓库 scripts/start-service(.ps1/.sh),或重跑安装器",
              example="kd health")


def cmd_search(a):
    qs = {"text": a.text, "page": a.page, "pageSize": a.size}
    if a.product: qs["productId"] = a.product
    if a.type: qs["type"] = a.type
    if a.global_: qs["global"] = "true"
    _out(_http("/search?" + urllib.parse.urlencode(qs)))


def cmd_read(a):
    body = {"id": a.id}
    if a.kind == "article":
        _out(_http("/article", body))
    else:
        _out(_http("/karticle", body))


def cmd_ask(a):
    body = {"topK": a.topk}
    if a.kw:
        body["keywords"] = a.kw
    else:
        body["text"] = a.text
    if a.product: body["productId"] = a.product
    _out(_http("/ask", body))


def cmd_share(a):
    _out(_http("/share", {"link": a.link}))


def cmd_manifest(_a):
    _out(_http("/manifest"))


def cmd_health(_a):
    _out(_http("/health"))


def main():
    p = argparse.ArgumentParser(
        prog="kd",
        description="金蝶官方知识 CLI(匿名免费:零账号/零点数/零 LLM)。AI-first:默认输出 JSON,stdout=数据 stderr=进度;"
                    "合成回答由调用方 AI 拿 kd ask 资料包完成。",
        epilog='示例:\n'
               '  kd search "信用额度控制" --product 93 --type answer\n'
               '  kd question 799346568250934528        # 问答帖全文(问题+全部回答)\n'
               '  kd article 402990431979506944         # 知识库文档全文\n'
               '  kd ask "信用额度怎么控制" --topk 4     # 一站式资料包(喂给当前模型合成)\n'
               '  kd share https://vip.kingdee.com/link/s/xxxx   # 读官方分享对话',
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("search", help="检索知识库(知识/问答/文章三种实体全返回)", epilog='示例: kd search "信用额度控制" --product 93 --type answer', formatter_class=argparse.RawDescriptionHelpFormatter)
    s.add_argument("text", help="关键词(具体功能名/业务名词/报错词)")
    s.add_argument("--product", type=int, default=93, help="93=星空旗舰版(默认) 87=苍穹 1=企业版/标准版 0=不过滤")
    s.add_argument("--type", choices=["knowledge", "answer", "article"], default=None, help="按实体类型过滤")
    s.add_argument("--page", type=int, default=1)
    s.add_argument("--size", type=int, default=10, help="每页条数(≤50)")
    s.add_argument("--global_", dest="global_", action="store_true", help="跨全部产品")
    s.set_defaults(fn=cmd_search)

    s = sub.add_parser("question", help="问答帖全文:问题+全部回答+追问链", epilog="示例: kd question 799346568250934528", formatter_class=argparse.RawDescriptionHelpFormatter)
    s.add_argument("id", help="search 结果 answer 条目的 questionId")
    s.set_defaults(fn=lambda a: _out(_http("/question", {"id": a.id})))

    s = sub.add_parser("answer", help="单条回答全文", epilog="示例: kd answer 799691682479252480", formatter_class=argparse.RawDescriptionHelpFormatter)
    s.add_argument("id", help="search 结果 answer 条目的 id")
    s.set_defaults(fn=lambda a: _out(_http("/answer", {"id": a.id})))

    s = sub.add_parser("article", help="读全文(默认知识库文档;--kind article 读社区文章)", epilog="示例: kd article 402990431979506944 / kd article 56784392135739905 --kind article", formatter_class=argparse.RawDescriptionHelpFormatter)
    s.add_argument("id")
    s.add_argument("--kind", choices=["knowledge", "article"], default="knowledge")
    s.set_defaults(fn=cmd_read)

    s = sub.add_parser("ask", help="一站式资料包:检索+深读 topK 全文(供当前模型合成回答)", epilog='示例: kd ask "信用额度怎么控制" --topk 4 / kd ask --kw "信用额度" --kw "应收单 信用"', formatter_class=argparse.RawDescriptionHelpFormatter)
    s.add_argument("text", nargs="?", default=None, help="自然语言问题或关键词")
    s.add_argument("--kw", action="append", default=None, help="多关键词模式(可重复)")
    s.add_argument("--product", type=int, default=None)
    s.add_argument("--topk", type=int, default=4)
    s.set_defaults(fn=cmd_ask)

    s = sub.add_parser("share", help="读官方 AI 分享对话(传分享短链/页面链接/chatId)", epilog="示例: kd share https://vip.kingdee.com/link/s/xxxx", formatter_class=argparse.RawDescriptionHelpFormatter)
    s.add_argument("link")
    s.set_defaults(fn=cmd_share)

    s = sub.add_parser("manifest", help="机器可读能力清单(端点/参数/示例)")
    s.set_defaults(fn=cmd_manifest)

    s = sub.add_parser("health", help="服务存活与版本", epilog="服务挂了: 重跑安装器或仓库 scripts/start-service(.ps1/.sh)", formatter_class=argparse.RawDescriptionHelpFormatter)
    s.set_defaults(fn=cmd_health)

    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
