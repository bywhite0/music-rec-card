import asyncio
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from music_card_gen import Mode

from golden_utils import EXPECTED_DIR, expected_path, render_mode


async def main() -> None:
    EXPECTED_DIR.mkdir(parents=True, exist_ok=True)
    for mode in (Mode.DAILY, Mode.CARD, Mode.LYRIC):
        image = await render_mode(mode)
        path = expected_path(mode)
        image.save(path, format="PNG", optimize=True)
        print(f"updated {path}")


if __name__ == "__main__":
    asyncio.run(main())
