#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_core_surface.py —— kd.core 公开面守卫(工单 #26)。

背景:工单 #25 的交付说明称「内部件旧名已全部不可见」,独立复验却测出
`hasattr(kd.core, "_rrf_fuse") is True`。偏差之所以发生,是因为人工复述
「我改过了」不可靠,而没有可执行的判据。本脚本就是那个判据。

判据(任一不成立即 fail,退出码 1):
  1. `kd.core` 顶层可属性访问的非双下划线名字,**恰好**等于公开面白名单;
     任何多出来的名字(内部函数/常量/被 import 的模块)都算「漏网名字」。
  2. 白名单里的名字必须**真实可用**(不能只在 __all__ 里挂名)。
  3. `__all__` 与白名单逐项一致(顺序与集合都比)。
  4. 已知的旧内部件(top_chunks / _rrf_fuse / Budget / RateLimiter / RRF_K …)
     一律不可属性访问——这是回归钉子,防止将来有人把实现搬回 kd.core。
  5. 公开函数的签名与白名单基线逐字一致(改签名 = 破坏性变更)。
  6. 异常类的身份在「公开名」与「实现体」之间一致
     (否则 `except kd.core.QueryTooLong` 会漏接实现体抛出的实例)。
  7. 观测口依赖的内部件仍可解析:`cli.py` 的 `cmd_health` 经 `core._impl()` 读取
     若干内部件,那些名字必须仍存在于实现体模块中。判据 4 钉的是「旧内部件不得
     变成 kd.core 的属性」,方向相反,覆盖不到"内部件被删/改名导致 health 静默崩"。
     依赖清单从 cmd_health 源码里提取(不手写第二份真相,避免清单自身腐烂)。

用法:
  python3 scripts/check_core_surface.py            # 人类可读输出
  python3 scripts/check_core_surface.py --json     # 机器可读
  python3 scripts/check_core_surface.py --quiet    # 只出退出码
不在仓库根时用 KD_SRC 指定 src 路径。
"""
import argparse
import inspect
import json
import os
import re
import sys

# ---- 公开面基线(工单 #26 验收标准:真能对外用的只有这 6 个) ----
PUBLIC_API = ["ask", "search", "read", "QueryTooLong", "UpstreamError", "InternalError"]

# 公开函数签名基线(逐字;与工单给定基线一致)。
SIGNATURE_BASELINE = {
    "ask": "(text=None, keywords=None, product_id=None, top_k=None, budget=None, "
           "rerank=None, refresh=False, rate=None)",
    "search": "(text, product_id=None, page=1, page_size=10, global_=False, sorts_type=1, "
              "type_=None, rerank=None, budget=None, rate=None)",
    "read": "(kind, oid, refresh=False, budget=None, rate=None)",
}

# 回归钉子:这些名字在任何情况下都不得成为 kd.core 的可属性访问名。
# 取自工单 #25 声明的「已改私有」清单 + 工单 #26 点名的漏网名单 + 模块级常量。
FORBIDDEN_NAMES = [
    # 工单 #25 声称已私有化(顶层不可见)的旧名
    "plan_routes", "Budget", "RateLimiter", "chunk_text", "top_chunks",
    "knowledge_search", "ask_bundle",
    # 工单 #26 实测的漏网名字
    "_rrf_fuse",
    # 其他内部实现件
    "_select_top", "_plan_routes", "_Budget", "_BudgetExhausted", "_RateLimiter",
    "_chunk_text", "_top_chunks", "_knowledge_search", "_ask_bundle",
    "clamp_query", "html2text", "log", "_route_cfg", "_cfg_budget_max",
    "_rate_profile", "_get_json", "_detail", "_norm_item", "_terms", "_fused_key",
    "_salient_chunks", "_synthesis_brief", "_fetch_for_item", "_answer_brief",
    "_answer_detail", "_article_detail", "_knowledge_article", "_question_detail",
    "_search_upstream", "_fresh_bonus", "_rerank_bonus", "_is_true", "_up_inc", "_up_now",
    # 模块级常量/配置(不得裸露为公开名)
    "RERANK_DEFAULT", "RRF_K", "UPSTREAM_TEXT_MAX", "INDEX_DEFAULT", "VIP", "UA", "HDRS",
    # 被 import 进来的标准库/第三方名(同样不该成为 kd.core 的属性面)
    "json", "re", "os", "sys", "math", "random", "time", "threading", "hashlib",
    "urllib", "ThreadPoolExecutor",
]


def _src_dir():
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)
    cand = os.environ.get("KD_SRC") or os.path.join(root, "src")
    if not os.path.isdir(cand):
        sys.exit("[check_core_surface] src 目录不存在: %s" % cand)
    return cand


def _health_impl_deps(src):
    """从 cli.py 的 cmd_health 里提取 `_cp.<name>` 形式的内部件依赖。

    判据 7 的数据来源。为什么不手写清单:`_cp.X` 是 health 与实现体之间的真实耦合,
    手写清单会在下次改 health 时与代码脱节,变成第二处腐烂的真相。
    只认 `_cp.<identifier>` 字面量,故 `_cp._route_cfg()` / `_cp.RERANK_DEFAULT`
    都能抓到,而间接引用(如经局部变量转手)会漏——漏了也只是少一条钉子,不会误报。
    """
    cli_path = os.path.join(src, "kd", "cli.py")
    try:
        with open(cli_path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return None
    m = re.search(r"def cmd_health\(.*?(?=\ndef |\Z)", text, re.S)
    if not m:
        return None
    return sorted(set(re.findall(r"_cp\.([A-Za-z_][A-Za-z0-9_]*)", m.group(0))))


def collect():
    """执行全部判据,返回结果字典。不抛异常(便于 --json 稳定输出)。"""
    src = _src_dir()
    if src not in sys.path:
        sys.path.insert(0, src)

    import kd.core as core  # noqa: E402

    visible = sorted(n for n in vars(core) if not n.startswith("__"))
    expected = sorted(PUBLIC_API)

    leaks = sorted(set(visible) - set(expected))          # 多出来的 = 漏网名字
    missing = sorted(set(expected) - set(visible))        # 白名单里挂空名的

    # 判据 2:白名单里的名字必须真实可用
    dead = []
    for n in PUBLIC_API:
        try:
            getattr(core, n)
        except AttributeError:
            dead.append(n)

    # 判据 3:__all__ 与白名单一致(顺序 + 集合)
    declared = list(getattr(core, "__all__", []))
    all_matches = declared == list(PUBLIC_API)

    # 判据 4:回归钉子
    forbidden_hits = [n for n in FORBIDDEN_NAMES if hasattr(core, n)]

    # 判据 5:公开函数签名逐字一致
    sig_mismatch = []
    for fname, want in SIGNATURE_BASELINE.items():
        fn = getattr(core, fname, None)
        if fn is None:
            sig_mismatch.append({"name": fname, "want": want, "got": "<missing>"})
            continue
        got = str(inspect.signature(fn))
        if got != want:
            sig_mismatch.append({"name": fname, "want": want, "got": got})

    # 判据 6:异常类身份一致(公开名 vs 实现体)
    exc_identity = {}
    impl = None
    if hasattr(core, "_impl"):
        try:
            impl = core._impl()
        except Exception as e:  # pragma: no cover
            exc_identity["_impl() error"] = repr(e)
    for cls in ("QueryTooLong", "UpstreamError", "InternalError"):
        pub = getattr(core, cls, None)
        inner = getattr(impl, cls, None) if impl is not None else None
        exc_identity[cls] = bool(pub is not None and inner is not None and pub is inner)

    # 判据 7:cmd_health 经观测口依赖的内部件必须仍可解析。
    # 抓的是"内部件被删/改名 → health 静默崩、守卫照样 PASS"这一类漂移。
    health_deps = _health_impl_deps(src)
    health_missing = []
    if health_deps is None:
        health_deps = []
    elif impl is not None:
        health_missing = [n for n in health_deps if not hasattr(impl, n)]

    checks = {
        "no_leaked_names": not leaks,
        "no_missing_names": not missing,
        "no_dead_names": not dead,
        "__all__ matches baseline": all_matches,
        "forbidden names absent": not forbidden_hits,
        "signatures unchanged": not sig_mismatch,
        "exception identity consistent": all(exc_identity.values()),
        "health impl deps resolvable": not health_missing,
    }
    return {
        "ok": all(checks.values()),
        "module_file": getattr(core, "__file__", None),
        "visible_names": visible,
        "visible_count": len(visible),
        "expected": list(PUBLIC_API),
        "leaks": leaks,
        "missing": missing,
        "dead": dead,
        "__all__": declared,
        "forbidden_hits": forbidden_hits,
        "signature_mismatch": sig_mismatch,
        "exception_identity": exc_identity,
        "health_deps": health_deps,
        "health_missing": health_missing,
        "checks": checks,
    }


def main():
    ap = argparse.ArgumentParser(description="kd.core 公开面守卫(工单 #26)")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--quiet", action="store_true", help="只出退出码")
    a = ap.parse_args()

    r = collect()
    if a.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
    elif not a.quiet:
        print("kd.core 公开面守卫 —— %s" % r["module_file"])
        print("  可见名(%d): %s" % (r["visible_count"], ", ".join(r["visible_names"])))
        print("  白名单(%d): %s" % (len(r["expected"]), ", ".join(r["expected"])))
        print("  漏网名字: %s" % (", ".join(r["leaks"]) if r["leaks"] else "无"))
        print("  缺失名字: %s" % (", ".join(r["missing"]) if r["missing"] else "无"))
        print("  禁用名命中: %s" % (", ".join(r["forbidden_hits"]) if r["forbidden_hits"] else "无"))
        print("  签名偏差: %s" % (json.dumps(r["signature_mismatch"], ensure_ascii=False)
                                  if r["signature_mismatch"] else "无"))
        print("  异常身份: %s" % r["exception_identity"])
        print("  health 内部件依赖(%d): %s" % (len(r["health_deps"]), ", ".join(r["health_deps"]) or "无"))
        if r["health_missing"]:
            print("  失联依赖: %s(health 会崩,必须在实现体里补回或改 cmd_health)"
                  % ", ".join(r["health_missing"]))
        for k, v in r["checks"].items():
            print("  [%s] %s" % ("PASS" if v else "FAIL", k))
        print("结论: %s" % ("PASS(公开面=ask/search/read+3 异常,零漏网)" if r["ok"] else "FAIL"))
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
