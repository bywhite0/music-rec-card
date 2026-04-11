"""`python -m music_card` 入口。"""

from __future__ import annotations

import asyncio
import sys

from .cli import main


def run() -> None:
    """运行命令行入口。"""
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())


if __name__ == "__main__":
    run()
