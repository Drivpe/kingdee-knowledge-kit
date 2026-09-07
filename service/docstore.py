#!/usr/bin/env python3
"""docstore —— md + front-matter 落盘的唯一实现(写穿 + updatedAt 幂等)。

v6 起所有本地文档沉淀共用本模块,保证「一文档一 md + front-matter」格式全站一致:
  - 落地缓存 landing(issue #10,~/.lingeebuild/landing,discovered_by=query)
  - corpus 语料目录(v5 遗留写穿/摄入/ share stub,数据不动,代码改走本模块)
  - 发版说明库 releasenotes(issue #15,一版本一 md)——直接复用,勿再另写一套

文件约定(由 issue #10 确立):
  <root>/<子目录>/<id>.md
  ---
  id: <文档 id>            # 发版说明库可用 <产品>-<版本> 等复合 id
  type: <类型>
  url: <原文链接>
  title: <单行标题>
  updatedAt: <上游 updatedAt>   # 有才写;幂等比对的唯一依据
  discovered_by: <query|usage|share|...>        # 发现来源
  summary: <摘要,截 300 字>     # 可选
  stub: true                    # 可选:标题+摘要 stub,正文深读后无条件升级
  ---
  <正文全文>

幂等规则(write_doc):
  1. 已有文件的 updatedAt == 本次 updatedAt → 返回 "unchanged",不重写
     (mtime 不变,rg/文件监控零扰动);
  2. 已有文件是 stub 且本次带正文 → 无条件升级为全文(stub 无可信 updatedAt 可比);
  3. updatedAt 变了 → 整文件覆盖(原子性:本地毫秒级单次写,无半截文件风险);
  4. 双方都无 updatedAt 时按空串比对,同样命中 "unchanged"。

写盘是本地毫秒级操作,调用方(检索服务深读路径)同步调用即可,不阻塞回答;
内部异常一律吞掉返回 "error",绝不向上抛——沉淀失败不能影响检索/回答主链路。

对外接口(issue #10 prefactor,#15 直接 import):
  read_front_matter(path) -> dict
      容错读已有文件的 front-matter(前 4KB,键值均字符串;无/坏文件返回 {})。
  doc_path(root, sub, oid) -> str
      标准路径 root/<sub>/<oid>.md。
  write_doc(path, fields, body, prev=None, lock=None, log=None) -> "written"|"unchanged"|"error"
      fields: 有序 dict,渲染顺序即 dict 顺序;值为 None/"" 的键跳过(可选字段直接省略);
              值一律单行化;summary 截 300 字。
      prev:   可传入 read_front_matter 结果避免重复读;缺省自己读。
      lock:   可选 threading.Lock(同目录多线程写时传共享锁;缺省用模块默认锁)。
      log:    可选 callable(str),写盘失败时回调(服务传 log())。
"""
import os
import re
import threading

_DEFAULT_LOCK = threading.Lock()


def read_front_matter(path):
    """读已有文件的 front-matter(容错:文件不存在/非 md 格式返回 {})。"""
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read(4096)
    except Exception:
        return {}
    if not text.startswith("---"):
        return {}
    fm = {}
    for ln in text.split("\n", 40)[1:]:
        if ln.strip() == "---":
            break
        m = re.match(r"([A-Za-z_]+):\s*(.*)", ln)
        if m:
            fm[m.group(1)] = m.group(2).strip()
    return fm


def doc_path(root, sub, oid):
    """标准落盘路径:root/<sub>/<oid>.md(子目录按 type 分型,rg 按目录圈定范围)。"""
    return os.path.join(root, str(sub or "").lower(), "%s.md" % str(oid or "").strip())


def _upd_key(v):
    """updatedAt 比对键:统一转字符串去空白(上游常给 epoch 毫秒 int,落盘后是 str,须等价可比)。"""
    s = str(v).strip() if v is not None else ""
    return "" if s.lower() in ("none", "") else s


def write_doc(path, fields, body, prev=None, lock=None, log=None):
    """写穿一个 md 文档(幂等,见模块 docstring)。返回 "written"|"unchanged"|"error"。"""
    oid = str((fields or {}).get("id") or "").strip()
    if not oid:
        return "error"
    if prev is None:
        prev = read_front_matter(path)
    if prev:
        upgraded = prev.get("stub") == "true" and body  # stub → 全文:无条件升级
        if not upgraded and _upd_key(prev.get("updatedAt")) == _upd_key(fields.get("updatedAt")):
            return "unchanged"
    lines = ["---"]
    for k, v in (fields or {}).items():
        if v is None or v == "":
            continue
        v = re.sub(r"\s+", " ", str(v)).strip()
        if k == "summary":
            v = v[:300]
        lines.append("%s: %s" % (k, v))
    lines.append("---")
    try:
        with (lock or _DEFAULT_LOCK):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n\n" + (body or ""))
        return "written"
    except Exception as e:
        if log:
            try:
                log("docstore write fail:", str(e)[:100])
            except Exception:
                pass
        return "error"
