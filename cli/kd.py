#!/usr/bin/env python3
"""kd —— 金蝶官方知识 CLI(AI-first,匿名检索;可选模型通道合成回答)
AI 是第一用户:stdout 只出 JSON 数据,进度/日志走 stderr,永不交互、永无 ANSI 色码、强制 UTF-8。
退出码契约:0=成功 1=服务/上游错误(stdout 带错误 JSON) 2=用法错误(argparse,stderr)。
所有命令默认输出 JSON。服务地址可用环境变量覆盖:KSEARCH_URL(默认 http://127.0.0.1:4097)。
合成回答两条路:kd ai 用你自己的模型通道(KAI_BASE/KAI_MODEL,不可用自动降级资料包);
或调用方 AI 拿 kd ask 资料包自己合成。回答格式规范:docs/ANSWER-SPEC.md(单一事实源)。
"""
import argparse, json, os, re, sys, urllib.request, urllib.parse

BASE = os.environ.get("KSEARCH_URL", "http://127.0.0.1:4097")
KAI_BASE = os.environ.get("KAI_BASE", "http://127.0.0.1:4090").rstrip("/")
KAI_MODEL = os.environ.get("KAI_MODEL", "glm-5.3-flash")


def _out(obj):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stdout.write(json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


def _prog(*a):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.write("[kd] " + " ".join(str(x) for x in a) + "\n")


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
    # --kind 与 search 结果的 type 字段一一对应,agent 零思考照抄:
    # knowledge→/karticle(官方文档全文);answer→/question(问答帖全文,传 questionId);
    # article→/article(社区文章全文)。单条回答(/answer)已被问答帖全文覆盖,不再单独暴露。
    body = {"id": a.id}
    if a.kind == "knowledge":
        _out(_http("/karticle", body))
    elif a.kind == "answer":
        _out(_http("/question", body))
    else:
        _out(_http("/article", body))


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


# ---- kd ai:关键词规划 → 资料包 → 按 ANSWER-SPEC 合成(模型通道不可用自动降级) ----

AI_SPEC_PROMPT = (
    "你是金蝶云·星空知识助手。仅依据提供的资料回答,遵循回答规范(ANSWER-SPEC):\n"
    "1. 结构化 Markdown 三段式:## 问题原因分析 → ## 解决方案(编号步骤,给可直接执行的菜单路径/字段名/参数) → ## 操作边界(适用版本/前提/不覆盖的情况);\n"
    "2. 并列信息用 Markdown 表格;\n"
    "3. 正文关键结论后标 [n],文末 ## 参考来源 列出 [n] 标题 —— 类型(官方文档/社区问答/社区文章);\n"
    "4. 资料未覆盖的部分明确写「现有资料未覆盖」,绝不编造菜单路径、字段名、接口名;\n"
    "5. 官方文档确认的直接陈述,社区经验标注「来自社区经验」。用中文回答。"
)


def _chat(messages, timeout=180, temperature=0.2):
    """调 OpenAI 兼容端点(KAI_BASE 不带 /v1,路径按原样透传拼接)。
    伪装 UA 是铁律:python-urllib 默认 UA 经代理透传后会被上游 Cloudflare 403。"""
    body = json.dumps({"model": KAI_MODEL, "messages": messages, "temperature": temperature}).encode("utf-8")
    req = urllib.request.Request(KAI_BASE + "/chat/completions", data=body,
                                 headers={"Content-Type": "application/json", "User-Agent": "curl/8.9.1"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.loads(r.read().decode("utf-8", "replace"))
    return (d["choices"][0]["message"]["content"] or "").strip()


def _ai_fallback(pack, reason):
    _prog("模型通道不可用,降级返回资料包:", reason)
    pack = dict(pack)
    pack["fallback"] = True
    pack["fallbackReason"] = str(reason)[:200]
    _out(pack)
    sys.exit(0)


def _source_text(s):
    """资料包单源的合成素材:knowledge/article 优先命中段落 chunks(标题感知切片,token 更省更准,
    引用可到 [chunk#seq]);无 chunks 退化全文。answer 取问题正文+前 3 条回答(采纳标 [采纳])。
    单源有界 6000 字。"""
    d = s.get("detail") or {}
    if s.get("type") == "answer":
        parts = [d.get("contentText") or ""]
        for ans in (d.get("answers") or [])[:3]:
            parts.append(("[采纳] " if ans.get("adopted") else "") + (ans.get("contentText") or ""))
        return "\n".join(p for p in parts if p)[:6000]
    ch = d.get("chunks")
    if ch:
        joined = "\n".join("[chunk#%s%s] %s" % (c.get("seq"), (" " + c["heading"]) if c.get("heading") else "", c["text"])
                           for c in ch)
        return joined[:6000]
    return (d.get("contentText") or "")[:6000]


def cmd_ai(a):
    _prog("模型通道:", KAI_BASE, "|", KAI_MODEL)
    # 第 1 段 LLM:问题 → 2-4 个检索关键词;规划失败退化为原句检索(不降级,合成仍可救)
    kws = None
    try:
        raw = _chat([
            {"role": "system", "content": "你是金蝶云·星空知识库的检索关键词规划师。把用户问题转写为 2-4 个"
                                          "具体检索关键词(功能名/业务名词/报错词,不要整句)。只输出 JSON 字符串数组,"
                                          "例如 [\"BOM\",\"生产单位数量\"]。"},
            {"role": "user", "content": a.text}], timeout=60)
        m = re.search(r"\[.*\]", raw, re.S)
        parsed = json.loads(m.group(0)) if m else None
        if isinstance(parsed, list) and parsed and all(isinstance(x, str) and x.strip() for x in parsed):
            kws = parsed[:4]
    except SystemExit:
        raise
    except Exception as e:
        _prog("关键词规划失败(%s),退化为原句检索" % e)
    if not kws:
        kws = [a.text]
    _prog("关键词:", " / ".join(kws))

    # 资料包(服务错误按错误契约直接退出,不走降级——降级只兜模型通道)
    pack = _http("/ask", {"keywords": kws, "topK": a.topk, **({"productId": a.product} if a.product else {})})
    if not pack.get("sources"):
        return _ai_fallback(pack, "检索无命中,跳过合成直接返回资料包")

    # 第 2 段 LLM:资料 → 按 ANSWER-SPEC 合成
    src = []
    for i, s in enumerate(pack["sources"], 1):
        src.append("[%d] 标题:%s | 类型:%s | 链接:%s\n%s" %
                   (i, s.get("title", ""), s.get("type", ""), s.get("url", ""), _source_text(s)))
    try:
        answer = _chat([
            {"role": "system", "content": AI_SPEC_PROMPT},
            {"role": "user", "content": "用户问题:%s\n\n检索资料:\n%s" % (a.text, "\n\n".join(src))}])
    except SystemExit:
        raise
    except Exception as e:
        return _ai_fallback(pack, "合成失败:%s" % e)
    _out({"ok": True, "fallback": False, "model": KAI_MODEL, "question": a.text,
          "keywords": kws, "answer": answer,
          "references": [{"n": i, "title": s.get("title", ""), "type": s.get("type", ""), "url": s.get("url", "")}
                         for i, s in enumerate(pack["sources"], 1)]})


def main():
    p = argparse.ArgumentParser(
        prog="kd",
        description="金蝶官方知识 CLI(匿名免费:零账号/零点数/零官方 LLM)。AI-first:默认输出 JSON,stdout=数据 stderr=进度;"
                    "kd ai 用你的模型通道合成带引用回答(遵循 docs/ANSWER-SPEC.md),或调用方 AI 拿 kd ask 资料包自己合成。",
        epilog='示例:\n'
               '  kd search "信用额度控制" --product 93 --type answer\n'
               '  kd read 402990431979506944                    # 读全文(kind 照抄 search 结果的 type)\n'
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

    s = sub.add_parser("read", help="读全文:--kind 照抄 search 结果的 type 字段(knowledge=官方文档/answer=问答帖全文/article=社区文章)", epilog='示例:\n'
               '  kd read 402990431979506944                    # knowledge 条目 → 官方文档全文\n'
               '  kd read 799346568250934528 --kind answer      # answer 条目 → 问题+全部回答+追问链(传 questionId)\n'
               '  kd read 56784392135739905 --kind article      # article 条目 → 社区文章全文', formatter_class=argparse.RawDescriptionHelpFormatter)
    s.add_argument("id", help="search 结果条目的 id(answer 条目传其 questionId)")
    s.add_argument("--kind", choices=["knowledge", "answer", "article"], default="knowledge",
                   help="实体类型,照抄 search 结果的 type 字段(默认 knowledge)")
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

    s = sub.add_parser("ai", help="语义识别→检索→按 ANSWER-SPEC 合成带引用回答(需模型通道;不可用自动降级资料包)", epilog='示例:\n'
               '  kd ai "BOM分母27000 MRP运算变成平方" --topk 4\n'
               '  环境变量:KAI_BASE(默认 http://127.0.0.1:4090,OpenAI 兼容,勿带 /v1) KAI_MODEL(默认 glm-5.3-flash)', formatter_class=argparse.RawDescriptionHelpFormatter)
    s.add_argument("text", help="自然语言问题")
    s.add_argument("--topk", type=int, default=4, help="深读资料条数(1-8)")
    s.add_argument("--product", type=int, default=None, help="93=星空旗舰版 87=苍穹 1=企业版/标准版 0=不过滤")
    s.set_defaults(fn=cmd_ai)

    s = sub.add_parser("manifest", help="机器可读能力清单(端点/参数/示例)")
    s.set_defaults(fn=cmd_manifest)

    s = sub.add_parser("health", help="服务存活与版本", epilog="服务挂了: 重跑安装器或仓库 scripts/start-service(.ps1/.sh)", formatter_class=argparse.RawDescriptionHelpFormatter)
    s.set_defaults(fn=cmd_health)

    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
