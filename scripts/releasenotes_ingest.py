#!/usr/bin/env python3
"""releasenotes_ingest.py — 发版说明摄取(issue #15 / ADR-0005 决策 5 增补,手动触发,可重跑)

范围(票 #15 拍板):索引内覆盖——93 旗舰版,窗口 2025-09 起(含端点月)的发版说明,
来自「发版说明」词搜索索引深翻(复用 releasenotes_probe 的深翻/版本解析逻辑),
标题过滤苍穹/星瀚混入,版本定位以标题内嵌版本号/月份为准(updatedAt 是批量编辑时间不可信,
仅作幂等比对键)。预计 ~30 篇,以实测数为准。

行为:
  1. 深翻 /search(「发版说明」,sortsType=2,productId=93)穷尽到尾页 → 发版类候选;
  2. 标题过滤:含「苍穹/星瀚」跳过;ver_month(标题)解析且 >= 窗口 → 目标集;
  3. 逐篇经 /karticle 深读全文,按「一文档一 md + front-matter(产品=93/版本号或月份/
     发版月份/原 url/type=releasenotes)」写入 ~/.lingeebuild/releasenotes/,
     复用 service/docstore.py 的写盘+幂等(type/discovered_by 扩展,不破坏 #10 语义);
     正文=contentText 原文,不切段、不洗版式,保留官方「模块-问题-修复」结构;
  4. 断点续跑:目标清单与失败清单存 ~/.lingeebuild/releasenotes/_manifest.json;
     重跑时逐篇对已落盘文件的 updatedAt 比对,全部命中则零上游请求(--rescan 重新深翻发现新版本)。

上游隔离(红线:只写 ~/.lingeebuild/releasenotes/):运行中的 4097 服务 /karticle 会写穿
corpus(discovered_by=usage)与 landing(discovered_by=query),直接打它会把发版说明混进
landing/语料。故默认在本机起一个一次性隔离子服务(--port,默认 4098):KSEARCH_DB/
KSEARCH_CORPUS/KSEARCH_LANDING 全指向临时目录(结束后删除)、KSEARCH_RATE=background
(后台档 1 请求/秒+抖动),客户端另有 1 秒+抖动节流兜底;主服务不受任何影响。
--base-url 可显式改打已有服务(自行承担其写穿副作用)。

红线:零账号零 cookie;1 请求/秒+随机抖动,禁并发;单轮上游上限默认 60;
深读失败记入失败清单可重跑,不静默跳过。stdout=汇总 JSON,stderr=进度。

用法:
  python scripts/releasenotes_ingest.py                     # 首次:深翻+摄取(~45 请求);有 manifest 续跑
  python scripts/releasenotes_ingest.py --rescan            # 忽略 manifest 重新深翻(发现新版本)
  python scripts/releasenotes_ingest.py --dry-run           # 不打上游,报告将做什么
  python scripts/releasenotes_ingest.py --max-requests 30   # 收缩预算(超限即停,重跑续)
退出码:0=完成(含个别失败已记录);2=预算耗尽中途停止(重跑续);1=致命错误。
"""
import argparse, json, os, random, re, shutil, socket, subprocess, sys, tempfile, time
import urllib.error, urllib.parse, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVICE_PY = os.path.join(ROOT, "service", "kingdee-ksearch-service.py")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import releasenotes_probe as probe          # 深翻/版本解析逻辑直接复用(issue #11 产出)
sys.path.insert(0, os.path.join(ROOT, "service"))
import docstore                             # 写盘+幂等唯一实现(issue #10 产出,#15 复用)

RN_DIR = os.path.join(os.path.expanduser("~"), ".lingeebuild", "releasenotes")
MANIFEST_PATH = os.path.join(RN_DIR, "_manifest.json")
MAIN_URL = os.environ.get("KSEARCH_URL", "http://127.0.0.1:4097")   # 仅用于 /health 计数核对
PRODUCT = 93          # 星空旗舰版
WINDOW = "2025-09"    # 近 1 年窗口起点(含端点月)
SCAN_TERM = "发版说明"
CLOUD_SKIP = ("苍穹", "星瀚")   # 标题过滤混入(【正式版本】苍穹/【灰度】星瀚 等,票 #15)

_req_n = 0            # 发往子服务的请求数(每请求=恰好 1 次真实上游:cache=0、无 type 混排)
_last_call = [0.0]


def pace(sleep):
    """客户端节流兜底:1 请求/秒+抖动(服务端 background 档同样限速,双保险取慢者)。"""
    time.sleep(sleep + random.uniform(0.15, 0.55))


def http_json(base, path, timeout=90):
    global _req_n, _last_call
    gap = time.monotonic() - _last_call[0]
    if gap < 1.0:
        time.sleep(1.0 - gap)                       # 客户端最小 1s 间隔(红线,与服务端档位无关)
    url = base + path
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            d = json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        d = {"ok": False, "error": "HTTP %s: %s" % (e.code, (e.read(200) or b"").decode("utf-8", "replace"))}
    except Exception as e:
        d = {"ok": False, "error": str(e)[:150]}
    _last_call[0] = time.monotonic()
    _req_n += 1
    return d


class BudgetStop(Exception):
    pass


def guarded(fn, *a, **kw):
    """请求前查预算;超限抛 BudgetStop(已获资料保留,重跑续)。"""
    global _req_n
    if _req_n >= kw.pop("cap"):
        raise BudgetStop()
    return fn(*a, **kw)


# ---------- 隔离子服务 ----------
def port_free(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def spawn_child(port, tmp):
    """起一次性隔离子服务:写路径全指临时目录、后台档限速;等待 /health 就绪。"""
    env = dict(os.environ,
               KSEARCH_DB=os.path.join(tmp, "data", "ksearch.db"),
               KSEARCH_CORPUS=os.path.join(tmp, "corpus"),
               KSEARCH_LANDING=os.path.join(tmp, "landing"),
               KSEARCH_RATE="background", KSEARCH_INDEX="0")
    proc = subprocess.Popen([sys.executable, SERVICE_PY, str(port)], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    deadline = time.time() + 20
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError("隔离服务进程退出(code %s),端口 %d 可能被占" % (proc.returncode, port))
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d/health" % port, timeout=2) as r:
                h = json.loads(r.read().decode("utf-8", "replace"))
                if h.get("anonymous"):
                    return proc
        except Exception:
            time.sleep(0.4)
    raise RuntimeError("隔离服务 20s 未就绪(port %d)" % port)


# ---------- manifest(断点续跑) ----------
def load_manifest(window):
    try:
        with open(MANIFEST_PATH, encoding="utf-8") as f:
            m = json.load(f)
        if m.get("window") == window and m.get("product") == PRODUCT:
            return m
    except Exception:
        pass
    return None


def save_manifest(m):
    os.makedirs(RN_DIR, exist_ok=True)
    tmp = MANIFEST_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(m, f, ensure_ascii=False, indent=1)
    os.replace(tmp, MANIFEST_PATH)


# ---------- 深翻(复用 probe 逻辑) ----------
def scan_targets(base, args, out):
    """深翻「发版说明」到尾页;标题过滤苍穹/星瀚;ver_month 解析;窗口内为目标集。"""
    docs = {}
    pages_fetched = 0
    for page in range(1, args.pages + 1):
        qs = urllib.parse.urlencode({"text": SCAN_TERM, "page": page, "pageSize": args.size,
                                     "sortsType": 2, "cache": 0, "productId": PRODUCT})
        try:
            d = guarded(http_json, base, "/search?" + qs, cap=args.max_requests)
        except BudgetStop:
            out["requestBudget"]["truncated"] = True
            print("!! 达到单轮请求上限 %d,深翻提前收工(重跑续传)" % args.max_requests, file=sys.stderr)
            break
        pages_fetched = page
        rs = d.get("results") or []
        for x in rs:
            if x.get("type") == "knowledge" and probe.NOTE_RE.search(x.get("title") or ""):
                docs[str(x.get("id"))] = x
        print("== 深翻 p%d/%s 本页 %d 条,累计发版类 %d" % (page, d.get("totalPages"), len(rs), len(docs)),
              file=sys.stderr)
        if page >= (d.get("totalPages") or 1) or not rs:
            break
        pace(args.sleep)

    skipped_cloud, no_version, targets = [], [], []
    for x in docs.values():
        title = x.get("title") or ""
        if any(c in title for c in CLOUD_SKIP):
            skipped_cloud.append({"id": x.get("id"), "title": title})
            continue
        vm = probe.ver_month(title)
        if not vm:
            no_version.append({"id": x.get("id"), "title": title})
            continue
        if vm >= WINDOW:
            m = probe.VER_RE.search(title)
            targets.append({"id": x.get("id"), "title": title, "updatedAt": x.get("updatedAt"),
                            "version": m.group(0) if m else vm, "date": vm,
                            "url": "%s/knowledge/%s" % (probe.VIP, x.get("id"))})
    targets.sort(key=lambda t: (t["date"], str(t["id"])))
    out["scan"] = {"term": SCAN_TERM, "pagesFetched": pages_fetched, "releaseLikeDocs": len(docs),
                   "skippedCloud": len(skipped_cloud), "skippedCloudSample": skipped_cloud[:5],
                   "noVersionTitle": len(no_version), "inWindowTargets": len(targets),
                   "byMonth": dict(sorted(_tally(t["date"] for t in targets).items()))}
    return targets, skipped_cloud


def _tally(seq):
    t = {}
    for x in seq:
        t[x] = t.get(x, 0) + 1
    return t


# ---------- 深读 + 落盘 ----------
def ingest_targets(base, args, targets, out):
    """逐篇 /karticle 深读;已落盘且 updatedAt 一致 → 零上游跳过;失败记清单不静默。"""
    n = {"written": 0, "unchanged": 0, "error": 0, "failed": 0}
    failures = []
    for i, t in enumerate(targets, 1):
        path = docstore.doc_path(RN_DIR, "", t["id"])
        prev = docstore.read_front_matter(path)
        if docstore._upd_key(prev.get("updatedAt")) == docstore._upd_key(t.get("updatedAt")) and prev.get("type") == "releasenotes":
            n["unchanged"] += 1
            print("== [%d/%d] %s 命中已落盘,零上游" % (i, len(targets), t.get("version")), file=sys.stderr)
            continue
        try:
            d = guarded(http_json, base, "/karticle?id=%s&cache=0" % t["id"], cap=args.max_requests)
        except BudgetStop:
            out["requestBudget"]["truncated"] = True
            print("!! 达到单轮请求上限 %d,剩余目标留待重跑续传" % args.max_requests, file=sys.stderr)
            break
        body = d.get("contentText") or ""
        if not d.get("ok") or not body.strip():
            failures.append({"id": t["id"], "title": t["title"],
                             "error": d.get("error") or "empty contentText"})
            n["failed"] += 1
            print("!! [%d/%d] %s 深读失败:%s(记入失败清单,可重跑)" % (i, len(targets), t["id"], failures[-1]["error"]),
                  file=sys.stderr)
            pace(args.sleep)
            continue
        fields = {"id": t["id"], "type": "releasenotes", "product": PRODUCT,
                  "version": t["version"], "date": t["date"],
                  "url": d.get("url") or t["url"], "title": d.get("title") or t["title"],
                  "updatedAt": d.get("updatedAt") or t.get("updatedAt"),
                  "discovered_by": "releasenotes"}
        r = docstore.write_doc(path, fields, body)
        n[{"written": "written", "unchanged": "unchanged", "error": "error"}[r]] += 1
        if r == "written" and d.get("updatedAt") is not None:
            t["updatedAt"] = d.get("updatedAt")   # 以详情端点的 updatedAt 为幂等锚,回写 manifest
        print("== [%d/%d] %s %s(%s)len=%d" % (i, len(targets), t.get("version"), r, t["id"],
                                              len(body)), file=sys.stderr)
        pace(args.sleep)
    out["ingest"] = dict(n, failures=failures)
    return n


def main():
    global _req_n
    ap = argparse.ArgumentParser(description="发版说明摄取(issue #15,手动触发,≤60 上游请求/轮)")
    ap.add_argument("--port", type=int, default=0, help="隔离子服务端口(默认 0=自动挑空闲端口)")
    ap.add_argument("--base-url", default=None, help="改打已有服务(注意其 /karticle 会写穿 corpus/landing)")
    ap.add_argument("--window", default=WINDOW)
    ap.add_argument("--pages", type=int, default=14, help="深翻最多页数(pageSize=50 覆盖 total≈509)")
    ap.add_argument("--size", type=int, default=50)
    ap.add_argument("--sleep", type=float, default=0.0, help="客户端额外间隔(服务端 background 档已限 1/秒)")
    ap.add_argument("--max-requests", type=int, default=60, help="单轮上游请求上限(红线 60)")
    ap.add_argument("--rescan", action="store_true", help="忽略 manifest 重新深翻(发现新版本)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    print("发版说明摄取:窗口 %s 起、product=%d、上限 %d 请求、落盘 %s%s" %
          (a.window, PRODUCT, a.max_requests, RN_DIR, "(dry-run)" if a.dry_run else ""), file=sys.stderr)
    t0 = time.time()
    out = {"ok": True, "dryRun": a.dry_run, "product": PRODUCT, "window": a.window,
           "requestBudget": {"cap": a.max_requests, "used": 0}}

    manifest = None if a.rescan else load_manifest(a.window)
    proc, tmp = None, None
    try:
        if a.base_url:
            base = a.base_url.rstrip("/")
            out["service"] = {"mode": "external", "baseUrl": base,
                              "note": "/karticle 会写穿该服务的 corpus/landing,自行承担"}
            print("使用外部服务 %s" % base, file=sys.stderr)
        else:
            if a.port == 0:   # 自动挑空闲端口(本机 409x 常驻其他服务实例)
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.bind(("127.0.0.1", 0))
                    a.port = s.getsockname()[1]
            if not port_free(a.port):
                print(json.dumps({"ok": False, "error": "port %d 已被占用,请换 --port" % a.port},
                                 ensure_ascii=False))
                return 1
            tmp = tempfile.mkdtemp(prefix="lingeebuild-ingest-")
            base = "http://127.0.0.1:%d" % a.port
            if a.dry_run:
                out["service"] = {"mode": "isolated-child", "port": a.port, "rateProfile": "background",
                                  "note": "dry-run 不实际起服务"}
            else:
                proc = spawn_child(a.port, tmp)
                out["service"] = {"mode": "isolated-child", "port": a.port, "pid": proc.pid,
                                  "rateProfile": "background",
                                  "note": "一次性隔离子服务:corpus/landing/db 指向临时目录,不打穿主服务数据"}
                print("隔离服务就绪 %s (pid %s, 写路径→%s)" % (base, proc.pid, tmp), file=sys.stderr)

        # ---- 目标集:manifest 续跑 or 深翻 ----
        if manifest:
            targets = manifest["targets"]
            out["scan"] = {"resumed": True, "manifestScannedAt": manifest.get("scannedAt"),
                           "inWindowTargets": len(targets),
                           "byMonth": dict(sorted(_tally(t["date"] for t in targets).items()))}
            print("续跑:manifest(%s)目标 %d 篇,跳过深翻" % (manifest.get("scannedAt"), len(targets)), file=sys.stderr)
        elif a.dry_run:
            out["scan"] = {"resumed": False, "note": "dry-run:将深翻「%s」≤%d 页,过滤苍穹/星瀚+窗口后逐篇 /karticle" % (SCAN_TERM, a.pages)}
            targets = []
        else:
            targets, _sk = scan_targets(base, a, out)
            manifest = {"schema": 1, "scannedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
                        "product": PRODUCT, "window": a.window, "targets": targets, "failures": []}
            save_manifest(manifest)
            print("深翻完成:窗口内目标 %d 篇,manifest 已写" % len(targets), file=sys.stderr)

        # ---- 逐篇深读落盘 ----
        if a.dry_run:
            missing = [t for t in targets
                       if docstore._upd_key(docstore.read_front_matter(
                           docstore.doc_path(RN_DIR, "", t["id"])).get("updatedAt"))
                       != docstore._upd_key(t.get("updatedAt"))]
            out["dryRun"] = {"targets": len(targets), "wouldFetch": len(missing),
                             "wouldSkipOnDisk": len(targets) - len(missing)}
            print("DRY:将深读 %d 篇,%d 篇已落盘跳过" % (len(missing), len(targets) - len(missing)), file=sys.stderr)
        else:
            n = ingest_targets(base, a, targets, out)
            manifest["failures"] = out["ingest"]["failures"]
            save_manifest(manifest)

        # ---- landing 计数核对(不强改服务,汇总 JSON 报告) ----
        try:
            with urllib.request.urlopen(MAIN_URL + "/health", timeout=5) as r:
                h = json.loads(r.read().decode("utf-8", "replace"))
            rn_files = sum(1 for f in os.listdir(RN_DIR) if f.endswith(".md")) if os.path.isdir(RN_DIR) else 0
            out["landing"] = {"mainServiceLanding": h.get("landing"),
                              "releasenotesCountedInLanding": False,
                              "releasenotesFiles": rn_files,
                              "note": "landing 计数只含 ~/.lingeebuild/landing(%s),发版说明库独立目录不在其中;篇数见 releasenotesFiles" % h.get("landing", {}).get("path")}
        except Exception as e:
            out["landing"] = {"error": str(e)[:120]}

        out["requestBudget"]["used"] = _req_n
        out["requestBudget"]["upstreamNote"] = "每请求=1 次真实上游(cache=0、无 type 混排、无并发)"
        out["elapsedSec"] = round(time.time() - t0, 1)
        out["manifest"] = MANIFEST_PATH
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 2 if out["requestBudget"].get("truncated") else 0
    finally:
        if proc is not None:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
            print("隔离服务已停止", file=sys.stderr)
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
