#!/usr/bin/env python3
"""kd 启动器(仓库内直跑用,免设 PYTHONPATH)。

  python3 src/kd_run.py --help
  python3 src/kd_run.py ask "信用额度控制"

包内等价形态(需 PYTHONPATH):PYTHONPATH=src python3 -m kd --help
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from kd.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
