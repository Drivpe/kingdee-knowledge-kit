#!/usr/bin/env python3
"""kd —— 金蝶官方知识 CLI(AI-first,匿名检索 + 资料包)
AI 是第一用户:stdout 只出 JSON 数据,进度/日志走 stderr,永不交互、永无 ANSI 色码、强制 UTF-8。
退出码契约:0=成功 1=服务/上游错误(stdout 带错误 JSON) 2=用法错误(argparse,stderr)。
所有命令默认输出 JSON。服务地址可用环境变量覆盖:KSEARCH_URL(默认 http://127.0.0.1:4097)。

本套件**零模型依赖**:只做检索与资料包,不合成回答(ADR-0008)。
合成权在调用方 agent:用户 → agent → `kd ask` 拿资料包 → 子代理按规范合成 → 返回「答案 + 结构化引用」。
回答格式规范见 docs/ANSWER-SPEC.md;子代理提示词模板与置信度判据见 skills/…/SKILL.md。
"""
import argparse, json, os, re, sys, urllib.request, urllib.parse

BASE = os.environ.get("KSEARCH_URL", "http://127.0.0.1:4097")


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


# ---- kd read --chunk(票 #20 / ADR-0007):官方 AI 引用溯源,匿名直连上游 ----
# 匿名 GET /aisapi/document-chunks/{chunkId} 零 cookie 返回 200:{content(块全文),
# documentId, entityId, entityType, id, title}——官方从不引用网页 URL,引用 = 标题+entityId+chunkId。
# 注意:该能力 v6.2 应收编进检索服务(唯一事实源),当前为避免与票 #18 改服务文件冲突而暂放 CLI。
CHUNK_API = "https://vip.kingdee.com/aisapi/document-chunks/"
_CHUNK_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"


def _read_chunk(chunk_id):
    """按 chunkId 匿名拉官方 chunk 全文。上游纪律:调用间隔 ≥3 秒,chunkId 不可枚举、勿批量。"""
    cid = str(chunk_id).strip()
    if not re.fullmatch(r"\d{3,}", cid):
        _fail("bad_chunk_id", "非法 chunkId: %s(应为纯数字 ID)" % cid,
              hint="chunkId 只能取自官方 AI 回答/分享对话的引用,不可编造",
              example="kd read 2659901 --chunk")
    req = urllib.request.Request(CHUNK_API + cid, headers={"User-Agent": _CHUNK_UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            d = json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            _fail("chunk_not_found", "HTTP 404:无此 chunkId %s" % cid,
                  hint="chunkId 不可枚举,请核对来源引用;上游请求间隔需 ≥3 秒(限频红线)",
                  example="kd read 2659901 --chunk")
        _fail("upstream_http_%s" % e.code, "官方 chunk 端点 HTTP %s(含 429 限频可能)" % e.code,
              hint="等待 ≥3 秒再重试;持续失败说明上游接口可能变更,勿高频重试",
              example="kd read 2659901 --chunk")
    except Exception as e:
        _fail("upstream_unreachable", str(e)[:200],
              hint="检查网络连接;端点为匿名 GET " + CHUNK_API + "{chunkId}",
              example="kd read 2659901 --chunk")
    if not (isinstance(d, dict) and d.get("content")):
        # 防「假 200」:上游可能 200 返回错误体,状态码不可靠(ADR-0007 备选取舍),以 content 字段为准
        _fail("chunk_bad_payload", "上游 200 但未返回 content 字段(限频/假 200/接口变更)",
              hint="等待 ≥3 秒再重试;原文片段: %s" % json.dumps(d, ensure_ascii=False)[:200],
              example="kd read 2659901 --chunk")
    return d


def cmd_read(a):
    if a.chunk:
        # 票 #20 / ADR-0007:按官方 AI 引用 chunkId 还原块全文(标题+entityId+chunkId 引用形态的解析端)。
        # v6.2 应收编进检索服务(唯一事实源),当前为避免与票 #18 改服务文件冲突而暂放 CLI。
        _out(_read_chunk(a.id))
        return
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
    # v6.2:服务端内置多路关键词拆解(原句路+症状词路+字段/实体名词路+产品词路,≤7 路 RRF 融合);
    # --kw 显式关键词可跳过自动拆解。返回体含 routes[]/budget{max,used}/budget_exhausted。
    # 并附 synthesisBrief:客观召回信号,供调用方 agent 判定召回置信度(ADR-0010)。
    body = {"topK": a.topk}
    if a.kw:
        body["keywords"] = a.kw
    else:
        body["text"] = a.text
    if a.product is not None: body["productId"] = a.product
    if a.budget: body["budget"] = a.budget
    pack = _http("/ask", body, timeout=180)
    routes = pack.get("routes") or []
    if routes:
        _prog("多路拆解 %d 路:" % len(routes), " | ".join(str(r.get("terms") or "") for r in routes))
    b = pack.get("budget") or {}
    _prog("上游预算: %s/%s%s" % (b.get("used"), b.get("max"),
                                  "(已耗尽,返回已获资料)" if pack.get("budget_exhausted") else ""))
    pack["synthesisBrief"] = _synthesis_brief(pack)
    _out(pack)


def cmd_share(a):
    _out(_http("/share", {"link": a.link}))


def cmd_manifest(_a):
    _out(_http("/manifest"))


def cmd_health(_a):
    _out(_http("/health"))


# ---- 合成权在调用方(ADR-0008):kd 只产资料包,不调任何模型通道 ----
# 原先的 kd ai(cmd_ai/_chat/AI_SPEC_PROMPT/_ai_fallback/_source_text)已删除。
# 规范要点与置信度三档判据见 docs/ANSWER-SPEC.md 与 skills/…/SKILL.md(子代理提示词模板)。


def _synthesis_brief(pack):
    """给调用方 agent 的合成摘要:把客观召回信号显式抬到顶层,
    供子代理判定召回置信度(ADR-0010——没有信号只能凭正文猜,那不算判定)。
    只做搬运与去冗,不产生任何新判断。"""
    src = pack.get("sources") or []
    b = pack.get("budget") or {}
    routes = pack.get("routes") or []
    # 命中的路由种类:用于判断"词汇鸿沟"是否真的被桥接(只有产品路命中 = 片段路全空)
    kinds = []
    for r in routes:
        k = str(r.get("kind") or "")
        if k and k not in kinds:
            kinds.append(k)
    scores = [s.get("fusedScore") for s in src if isinstance(s.get("fusedScore"), (int, float))]
    return {
        "sourceCount": len(src),
        # 各源的融合分(高→低),子代理据此判断召回是否集中/是否有明显赢家
        "topScores": [round(x, 5) for x in sorted(scores, reverse=True)[:5]],
        "routeKinds": kinds,
        "routeCount": len(routes),
        "budgetExhausted": bool(pack.get("budget_exhausted")),
        "upstreamUsed": b.get("used"),
        "upstreamMax": b.get("max"),
        "recallHint": ("检索未命中任何文档" if not src else
                       "上游预算耗尽,本轮召回不完整" if pack.get("budget_exhausted") else
                       "召回正常"),
    }


def main():
    p = argparse.ArgumentParser(
        prog="kd",
        description="金蝶官方知识 CLI(匿名免费:零账号/零点数/零模型)。AI-first:默认输出 JSON,stdout=数据 stderr=进度;"
                    "kd ask 是唯一常规入口(内置多路关键词拆解+预算),search/read 是手动细粒度调试命令;"
                    "本套件只产资料包、不合成回答(ADR-0008)——调用方 agent 拿资料包按 docs/ANSWER-SPEC.md 自己合成。",
        epilog='示例:\n'
               '  kd ask "BOM分母变平方" --topk 4  # 常规问题一律用它:内置多路关键词拆解,一站式资料包\n'
               '  kd ask --kw "信用额度" --kw "应收单 信用"  # 显式关键词(跳过自动拆解)\n'
               '  kd search "信用额度控制" --product 93 --type answer  # 手动细粒度调试命令\n'
               '  kd read 402990431979506944                    # 读全文(kind 照抄 search 结果的 type)\n'
               '  kd read 2659901 --chunk                       # 按官方 AI 引用 chunkId 匿名还原块全文(ADR-0007)\n'
               '  kd share https://vip.kingdee.com/link/s/xxxx   # 读官方分享对话',
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("search", help="手动细粒度调试命令:检索知识库(常规问题请用 kd ask;三种实体全返回)", epilog='示例: kd search "信用额度控制" --product 93 --type answer', formatter_class=argparse.RawDescriptionHelpFormatter)
    s.add_argument("text", help="关键词(具体功能名/业务名词/报错词)")
    s.add_argument("--product", type=int, default=93, help="93=星空旗舰版(默认) 87=苍穹 1=企业版/标准版 0=不过滤")
    s.add_argument("--type", choices=["knowledge", "answer", "article"], default=None, help="按实体类型过滤")
    s.add_argument("--page", type=int, default=1)
    s.add_argument("--size", type=int, default=10, help="每页条数(≤50)")
    s.add_argument("--global_", dest="global_", action="store_true", help="跨全部产品")
    s.set_defaults(fn=cmd_search)

    s = sub.add_parser("read", help="手动细粒度调试命令:读全文(常规问题请用 kd ask);--kind 照抄 search 结果的 type 字段(knowledge=官方文档/answer=问答帖全文/article=社区文章);--chunk 按官方 AI 引用 chunkId 匿名还原块全文(ADR-0007)", epilog='示例:\n'
               '  kd read 402990431979506944                    # knowledge 条目 → 官方文档全文\n'
               '  kd read 799346568250934528 --kind answer      # answer 条目 → 问题+全部回答+追问链(传 questionId)\n'
               '  kd read 56784392135739905 --kind article      # article 条目 → 社区文章全文\n'
               '  kd read 2659901 --chunk                       # 官方 AI 引用 chunkId → 块全文+entityId 映射(匿名,间隔≥3秒)', formatter_class=argparse.RawDescriptionHelpFormatter)
    s.add_argument("id", help="search 结果条目的 id(answer 条目传其 questionId);--chunk 模式下传官方 AI 引用的 chunkId")
    s.add_argument("--kind", choices=["knowledge", "answer", "article"], default="knowledge",
                   help="实体类型,照抄 search 结果的 type 字段(默认 knowledge)")
    s.add_argument("--chunk", action="store_true",
                   help="按官方 AI 引用 chunkId 匿名读块全文(GET /aisapi/document-chunks/{id},ADR-0007 引用形态的溯源端)")
    s.set_defaults(fn=cmd_read)

    s = sub.add_parser("ask", help="唯一常规入口:一站式资料包(内置多路关键词拆解 ≤7 路 RRF+深读 topK 全文+上游预算+召回信号摘要,供调用方 agent 合成回答)", epilog='示例: kd ask "信用额度怎么控制" --topk 4 / kd ask --kw "信用额度" --kw "应收单 信用"', formatter_class=argparse.RawDescriptionHelpFormatter)
    s.add_argument("text", nargs="?", default=None, help="自然语言问题或关键词")
    s.add_argument("--kw", action="append", default=None, help="多关键词模式(可重复,跳过自动拆解)")
    s.add_argument("--product", type=int, default=None)
    s.add_argument("--topk", type=int, default=4)
    s.add_argument("--budget", type=int, default=None, help="上游请求硬上限覆盖(默认 32,超限即停)")
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
