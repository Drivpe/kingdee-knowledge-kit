#!/usr/bin/env python3
"""python -m kd 入口(按包目录运行:PYTHONPATH=src python3 -m kd)。
仓库内更省事的等价写法:`python3 src/kd_run.py …`(无需设 PYTHONPATH)。"""
import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
