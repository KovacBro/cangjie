#!/usr/bin/env python
"""免安装入口：python scripts/cj.py <子命令>"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cangjie.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
