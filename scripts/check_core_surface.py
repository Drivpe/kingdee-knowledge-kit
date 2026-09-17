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
     覆盖三种失败,任一即 FAIL——本判据不靠判据 6 兜底,能独立失败:
       a. 取不到依赖清单:读不到 cli.py,或正则定位不到 cmd_health(函数被改名/删除)。
       b. 清单为空:函数定位到了,但体内没有任何 `_cp.<name>` 引用(取数被抽走)。
       c. 清单非空但有条目在实现体里已失联(名字被删/改名,health 会崩)。
     **不覆盖**:只认 `_cp.<identifier>` 字面量,经局部变量转手或 getattr 拼接的
     间接引用抓不到。可接受,因为这类漏只表现为少一条钉子(漏报),不会误报;
     要绕开它须主动改写 cmd_health 取数方式,属有心规避,而本判据挡的是自然腐烂。

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
    "ask": "(text=None, keywords=None, product_id=93, top_k=None, budget=None, "
           "rerank=None, refresh=False, rate=None)",
    "search": "(text, product_id=None, page=1, page_size=10, global_=False, sorts_type=1, "
              "type_=None, rerank=None, budget=None, rate=None, routes=None)",
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

    **三态返回**,调用方必须靠 `status` 区分,不得再把三者压成同一个结果:
      - status="unavailable":取不到依赖清单(读不到 cli.py / 正则失配——函数被改名、
        删除、或换成了 async/lambda 等本正则不认的写法)。这是**否定证据**,
        守卫无法证伪「health 仍健康」,故判据 7 必须 FAIL 并报出原因。
      - status="empty":文件读到了、函数定位到了,但函数体里一条 `_cp.` 引用都没有。
        同样是 FAIL——health 本就要经观测口读内部件,没有引用意味着提取口径失效
        (逻辑被抽走、前缀改名、或依赖改从别的入口取)。
      - status="ok":清单非空,逐项拿实现体校验。

    已知局限(明确接受):只认 `_cp.<identifier>` 字面量。`_cp._route_cfg()` /
    `_cp.RERANK_DEFAULT` 这类属性访问都能抓到,但经局部变量转手
    (如 `cov = _cp; cov._route_cfg()`)或 getattr 拼接的间接引用抓不到。
    之所以可接受:这种漏只表现为「少一条钉子」(漏报),不会把健康的 health 判成坏;
    真要绕开它得主动改写 cmd_health 的取数方式,属于有心规避而非自然腐烂,
    而自然腐烂恰恰是本判据要挡的东西。代价是清单变了要跟着改 health 才会被发现,
    换来的是不手写第二份真相。
    """
    cli_path = os.path.join(src, "kd", "cli.py")
    try:
        with open(cli_path, encoding="utf-8") as f:
            text = f.read()
    except OSError as e:
        return {
            "status": "unavailable",
            "reason": "读不到 %s(%s)" % (cli_path, e.__class__.__name__),
            "path": cli_path,
            "deps": [],
        }
    m = re.search(r"def cmd_health\(.*?(?=\ndef |\Z)", text, re.S)
    if not m:
        return {
            "status": "unavailable",
            "reason": "无法从 %s 定位 cmd_health(函数被改名/删除,或换成了本正则不认的写法)"
                      % cli_path,
            "path": cli_path,
            "deps": [],
        }
    deps = sorted(set(re.findall(r"_cp\.([A-Za-z_][A-Za-z0-9_]*)", m.group(0))))
    if not deps:
        return {
            "status": "empty",
            "reason": "cmd_health 已定位,但函数体内没有任何 `_cp.<name>` 引用(取数逻辑被抽走/前缀被改)",
            "path": cli_path,
            "deps": [],
        }
    return {"status": "ok", "reason": "", "path": cli_path, "deps": deps}


def _read_pyproject_version(root):
    """从 pyproject.toml 取 version(不引第三方 toml 解析,只认本项目的一行写法)。"""
    path = os.path.join(root, "pyproject.toml")
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                m = re.match(r'\s*version\s*=\s*["\']([^"\']+)["\']', line)
                if m:
                    return m.group(1), path
    except OSError as e:
        return None, "%s(%s)" % (path, e.__class__.__name__)
    return None, path


def _check_version_single_source(src):
    """版本号单一真源:实现体 VERSION 是唯一出处,其余三处必须由它派生。

    比较口径:pyproject 用三段(PEP 440),实现体用两段,故只比"前两段"是否一致;
    __init__.__version__ 与 cli._VERSION 必须与实现体逐字相等。
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        import importlib
        impl = importlib.import_module("kd._core_impl")
        pkg = importlib.import_module("kd")
        kdcli = importlib.import_module("kd.cli")
    except Exception as e:
        return False, "无法 import 以校验版本(%s: %s)" % (e.__class__.__name__, e)

    want = getattr(impl, "VERSION", None)
    if not want:
        return False, "实现体缺少 VERSION 常量(单一真源不存在)"

    got_pkg = getattr(pkg, "__version__", None)
    got_cli = getattr(kdcli, "_VERSION", None)
    pyv, pypath = _read_pyproject_version(root)

    problems = []
    if got_pkg != want:
        problems.append("kd.__version__=%r != VERSION=%r" % (got_pkg, want))
    if got_cli != want:
        problems.append("cli._VERSION=%r != VERSION=%r" % (got_cli, want))
    if pyv is None:
        problems.append("读不到 pyproject.toml 的 version(%s)" % pypath)
    elif str(pyv).split(".")[:2] != str(want).split(".")[:2]:
        problems.append("pyproject version=%r 前两段 != VERSION=%r" % (pyv, want))

    if problems:
        return False, "; ".join(problems)
    return True, ""


def _check_kind_consistency(src, impl):
    """kind 集合一致性:ENTITY_KINDS / _DETAIL_KINDS / cli 白名单三处必须同集合。

    cli 侧不 import(它有 argparse 副作用且 import 期就要 _impl()),改用正则读源,
    这与 _health_impl_deps 的"不手写第二份真相"同纪律。
    """
    if impl is None:
        return False, "kd.core._impl() 不可用,无法校验 kind 集合"
    entity = getattr(impl, "ENTITY_KINDS", None)
    detail = getattr(impl, "_DETAIL_KINDS", None)
    if not entity or not detail:
        return False, "实现体缺少 ENTITY_KINDS 或 _DETAIL_KINDS"
    if set(entity) != set(detail):
        return False, "ENTITY_KINDS=%r != _DETAIL_KINDS=%r" % (entity, detail)

    cli_path = os.path.join(src, "kd", "cli.py")
    try:
        with open(cli_path, encoding="utf-8") as f:
            text = f.read()
    except OSError as e:
        return False, "读不到 %s(%s)" % (cli_path, e.__class__.__name__)

    # cli 必须从实现体取白名单(单一真源),不得复制字面量。
    # 判据:文件里不得出现裸的 ("knowledge", "answer", "article") 字面量。
    literal = re.search(r'\(\s*"knowledge"\s*,\s*"answer"\s*,\s*"article"\s*\)', text)
    if literal:
        return False, ("cli.py 里仍有裸 kind 字面量(第 %d 字符处)——"
                       "应从 _IMPL.ENTITY_KINDS 取,单一真源"
                       % literal.start())
    if "_IMPL.ENTITY_KINDS" not in text and "ENTITY_KINDS" not in text:
        return False, "cli.py 未引用 ENTITY_KINDS(白名单与实现体脱钩)"
    return True, ""


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
    # 三态:取不到清单(unavailable)/ 清单为空(empty)/ 清单非空(ok)。
    # 前两态都 FAIL——本判据不靠判据 6 兜底,必须能独立失败:即使 _impl() 一切正常,
    # 「找不到被守卫对象」也不能算「被守卫对象健康」。
    extracted = _health_impl_deps(src)
    health_deps = extracted["deps"]
    health_missing = []
    if extracted["status"] == "ok" and impl is not None:
        health_missing = [n for n in health_deps if not hasattr(impl, n)]
    health_probe = extracted["status"] == "ok" and not health_missing
    if extracted["status"] != "ok":
        health_probe_reason = extracted["reason"]
    elif impl is None:
        health_probe_reason = "kd.core._impl() 不可用,无法校验内部件(判据 6 同源)"
        health_probe = False
    elif health_missing:
        health_probe_reason = "内部件失联: %s" % ", ".join(health_missing)
    else:
        health_probe_reason = ""

    # 判据 8:版本号单一真源。
    # 抓的是"实现体 VERSION / 包 __version__ / cli._VERSION / pyproject version
    # 四份字面量各自演化"这一类漂移——此前实测为 6.2 / 0.1.0 / 6.2 / 6.2.0,
    # 而 pipx install 会把 __version__ 当成发布版本号。
    version_probe, version_reason = _check_version_single_source(src)

    # 判据 9:kind 集合一致性。
    # 抓的是"read 的 --kind 白名单 / search 的 --type 白名单 / 详情分发表
    # 三份字面量不同源"——此前分发表有 4 个 kind(answer_detail)、公开白名单 3 个,
    # 加删 kind 不会被任何断言发现。
    kind_probe, kind_reason = _check_kind_consistency(src, impl)

    checks = {
        "no_leaked_names": not leaks,
        "no_missing_names": not missing,
        "no_dead_names": not dead,
        "__all__ matches baseline": all_matches,
        "forbidden names absent": not forbidden_hits,
        "signatures unchanged": not sig_mismatch,
        "exception identity consistent": all(exc_identity.values()),
        "health impl deps resolvable": health_probe,
        "version single source": version_probe,
        "kind set consistent": kind_probe,
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
        "health_deps_status": extracted["status"],
        "health_deps_source": extracted["path"],
        "health_deps_reason": extracted["reason"],
        "health_deps": health_deps,
        "health_missing": health_missing,
        "health_check_reason": health_probe_reason,
        "version_probe": version_probe,
        "version_reason": version_reason,
        "kind_probe": kind_probe,
        "kind_reason": kind_reason,
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
        print("  health 内部件依赖(%d, %s): %s"
              % (len(r["health_deps"]), r["health_deps_status"],
                 ", ".join(r["health_deps"]) or "无"))
        if r["health_check_reason"]:
            print("  health 判据 7 失败原因: %s" % r["health_check_reason"])
        if r["health_missing"]:
            print("  失联依赖: %s(health 会崩,必须在实现体里补回或改 cmd_health)"
                  % ", ".join(r["health_missing"]))
        if r["version_reason"]:
            print("  版本判据 8 失败原因: %s" % r["version_reason"])
        if r["kind_reason"]:
            print("  kind 判据 9 失败原因: %s" % r["kind_reason"])
        for k, v in r["checks"].items():
            print("  [%s] %s" % ("PASS" if v else "FAIL", k))
        print("结论: %s" % ("PASS(公开面=ask/search/read+3 异常,零漏网)" if r["ok"] else "FAIL"))
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
