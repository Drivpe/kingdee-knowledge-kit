#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
official-ai-teacher.py —— 官方 AI「教师模式」本地客户端(草案)

用途:用自己的登录会话,低频向官方 /aisapi/ai-search 提问,把 SSE 全流(含
intentRecognize/recallChunks/reRankChunks 步骤事件、isThink 思考流、documents 引用清单)
落盘为 JSONL,用作 kd 自建向量召回的评测金标(教师蒸馏),不做在线依赖。

纪律(必读):
- 仅限本人账号、人类频率(建议每天 ≤10 问,间隔 ≥30s);
- 不做批量、不并发、不代理他人使用;
- cookie 只存本机,不入 git、不入 corpus。

用法:
  1. 浏览器登录 vip.kingdee.com 后,F12 → Network → 任选一条 /aisapi 请求
     → Request Headers → 复制整行 cookie: 值,存到 ~/kd_bundle/cookie.txt(仅本机)
  2. python official-ai-teacher.py "你的问题" [--out captures/xxx.jsonl]
"""
import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request

COOKIE_FILE = os.path.expanduser("~/kd_bundle/cookie.txt")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/152.0.0.0 Safari/537.36"


def load_cookie() -> str:
    if not os.path.exists(COOKIE_FILE):
        sys.exit(f"cookie 文件不存在: {COOKIE_FILE}(见脚本头注释)")
    return open(COOKIE_FILE, encoding="utf-8").read().strip()


def ask(question: str, product_line_id=40, product_id=93, mode=None, timeout=120):
    cookie = load_cookie()
    q = urllib.parse.urlencode({
        "searchText": question,
        "useClarification": "true",
        "productLineId": product_line_id,
        "productId": product_id,
        "sessionId": "",  # 留空=新会话;复用会话请填服务端返回的 aiSearchSessionId
        "channel_level": "社区|导航|智能助手",
        **({"mode": mode} if mode else {}),
    })
    req = urllib.request.Request(
        "https://vip.kingdee.com/aisapi/ai-search?" + q,
        headers={"User-Agent": UA, "Cookie": cookie, "Accept": "text/event-stream"},
    )
    events = []
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        ctype = resp.headers.get("content-type", "")
        if "event-stream" not in ctype:
            body = resp.read().decode("utf-8", "replace")
            raise SystemExit(f"非 SSE 响应({resp.status} {ctype}): {body[:300]} —— 会话可能过期,重新导出 cookie")
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            try:
                events.append(json.loads(line[5:]))
            except json.JSONDecodeError:
                events.append({"_raw": line[:200]})
    return events


def summarize(events):
    steps = [e["step"] for e in events if "step" in e]
    think = "".join(e["message"] for e in events if e.get("isThink") and e.get("message"))
    answer = "".join(e["message"] for e in events if e.get("isThink") is False and e.get("message"))
    end = next((e for e in events if e.get("answerEnd")), None)
    docs = [
        {"entityId": d.get("entityId"), "entityType": d.get("entityType"),
         "chunkId": d.get("id"), "title": d.get("title")}
        for d in (end or {}).get("documents", [])
    ]
    return {"steps": steps, "answer": answer, "think": think, "documents": docs,
            "sessionId": (end or {}).get("aiSearchSessionId")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("question")
    ap.add_argument("--out", default=None, help="JSONL 落盘路径")
    ap.add_argument("--mode", default=None, help="AGENT=agent 模式")
    args = ap.parse_args()
    events = ask(args.question, mode=args.mode)
    rec = {"question": args.question, "capturedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "events": events, "summary": summarize(events)}
    if args.out:
        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, indent=2))
        print("saved:", args.out)
    s = rec["summary"]
    print("steps:", s["steps"])
    print("documents:", json.dumps(s["documents"], ensure_ascii=False, indent=1))
    print("answer:", s["answer"][:800])


if __name__ == "__main__":
    main()
