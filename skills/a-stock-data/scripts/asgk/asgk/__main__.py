"""python -m asgk 入口（等价 asgk console_script）。"""
import sys

from asgk.cli import main

if __name__ == "__main__":
    sys.exit(main())
