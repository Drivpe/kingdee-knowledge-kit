#!/usr/bin/env python3
"""kd —— 金蝶官方知识检索内核(可 import 的库 + CLI)。

包形态(ADR-0011 去服务化):
  src/kd/core.py      检索编排;公开面只有 ask / search / read
  src/kd/cli.py       argparse 命令面
  src/kd/__main__.py  python -m kd 入口
  src/kd/query_routes.json  多路拆解规则(数据文件,语料可配置)

导入方式:把 `src/` 加进 sys.path 后 `from kd import core`;或直接用仓库根的启动器
`python3 src/kd_run.py …`(它自建 bootstrap),以及 `PYTHONPATH=src python3 -m kd …`。
"""
import importlib as _importlib

# 版本号单一真源:实现体里的 VERSION。此前本文件写 0.1.0、cli 写 6.2、
# pyproject 写 6.2.0 —— pipx install 会把 0.1.0 当成真实版本号发布。
__version__ = _importlib.import_module("kd._core_impl").VERSION
del _importlib

__all__ = ["core", "cli"]
