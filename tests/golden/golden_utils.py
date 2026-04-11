from __future__ import annotations

from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Dict
from unittest.mock import patch

from PIL import Image, ImageChops, ImageDraw, ImageFont

from music_card_gen import CardPayload, Mode, MusicCard, Platform

GOLDEN_DIR = Path(__file__).resolve().parent
EXPECTED_DIR = GOLDEN_DIR / "expected"
ARTIFACT_DIR = GOLDEN_DIR / "artifacts"

MODE_TO_FILENAME: Dict[Mode, str] = {
    Mode.DAILY: "daily.png",
    Mode.CARD: "card.png",
    Mode.LYRIC: "lyric.png",
}


def expected_path(mode: Mode) -> Path:
    return EXPECTED_DIR / MODE_TO_FILENAME[mode]


def artifact_actual_path(mode: Mode) -> Path:
    return ARTIFACT_DIR / f"{mode.value}.actual.png"


def artifact_diff_path(mode: Mode) -> Path:
    return ARTIFACT_DIR / f"{mode.value}.diff.png"


@lru_cache(maxsize=1)
def build_cover_image() -> Image.Image:
    size = 720
    img = Image.new("RGB", (size, size), "#203040")
    draw = ImageDraw.Draw(img)

    for y in range(size):
        r = int(30 + (140 * y / size))
        g = int(50 + (90 * y / size))
        b = int(90 + (120 * y / size))
        draw.line((0, y, size, y), fill=(r, g, b))

    draw.ellipse((80, 90, 560, 570), outline="#F2E8C6", width=10)
    draw.rectangle((130, 420, 620, 650), outline="#D9C59A", width=8)
    draw.polygon([(550, 110), (680, 260), (560, 300)], fill="#A65D57")

    return img


def _golden_font_loader(font_path: str, size: int, index: int):
    _ = font_path, index
    return ImageFont.load_default(size=size)


async def _fake_download_image(url: str, http_client) -> Image.Image:
    _ = url, http_client
    return build_cover_image().copy()


def _daily_quote_sample() -> str:
    return "\n".join(
        [
            "[:-:]Golden baseline heading",
            "[:_]small subtitle baseline",
            "[-]Section",
            "[:-]This baseline quote line is intentionally long to trigger deterministic wrapping behavior in daily mode rendering.",
            "普通中文行用于测试自动换行与样式解析。",
            "[-]",
            "[-:]Right aligned final line",
        ]
    )


def _lyric_quote_sample() -> str:
    return "\n".join(
        [
            "[:-]Lyric line A is intentionally long for wrapping checks in lyric mode.",
            "[:_]lyric translation line with smaller font",
            "[-:]lyric right aligned line",
            "普通中文歌词用于测试换行。",
        ]
    )


def build_payload(mode: Mode) -> CardPayload:
    quote_by_mode = {
        Mode.DAILY: _daily_quote_sample(),
        Mode.CARD: "Card mode quote placeholder",
        Mode.LYRIC: _lyric_quote_sample(),
    }
    source_by_mode = {
        Mode.DAILY: "GoldenUser",
        Mode.CARD: "CardUser",
        Mode.LYRIC: "LyricUser",
    }

    return CardPayload(
        title="Golden Test Song 标题",
        artist="Test Artist / 测试歌手",
        cover_url="https://example.com/cover.jpg",
        quote_content=quote_by_mode[mode],
        quote_source=source_by_mode[mode],
        date_obj=datetime(2026, 3, 14),
        music_id="123456",
        song_url="https://example.com/song/123456",
    )


async def render_mode(mode: Mode) -> Image.Image:
    card = MusicCard(font_path="__golden_font__", platform=Platform.NCM)
    payload = build_payload(mode)
    show_qrcode = mode in {Mode.DAILY, Mode.CARD}

    with patch.object(MusicCard, "_cached_font", staticmethod(_golden_font_loader)):
        with patch("music_card_gen.download_image_with_fallback", side_effect=_fake_download_image):
            image = await card.generate(
                payload,
                inner_blurred=True,
                show_qrcode=show_qrcode,
                mode=mode,
            )
    return image.convert("RGB")


def compute_diff_metrics(expected: Image.Image, actual: Image.Image) -> dict[str, float]:
    expected_rgb = expected.convert("RGB")
    actual_rgb = actual.convert("RGB")

    if expected_rgb.size != actual_rgb.size:
        return {
            "same_size": 0.0,
            "max_diff": 255.0,
            "mean_diff": 255.0,
            "changed_ratio": 1.0,
        }

    diff = ImageChops.difference(expected_rgb, actual_rgb).convert("L")
    hist = diff.histogram()
    pixel_count = diff.width * diff.height

    non_zero = sum(hist[1:])
    total = sum(value * hist[value] for value in range(256))
    max_diff = max((value for value in range(256) if hist[value] > 0), default=0)

    return {
        "same_size": 1.0,
        "max_diff": float(max_diff),
        "mean_diff": float(total / pixel_count),
        "changed_ratio": float(non_zero / pixel_count),
    }


def save_diff_artifacts(mode: Mode, expected: Image.Image, actual: Image.Image) -> None:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    artifact_actual_path(mode).parent.mkdir(parents=True, exist_ok=True)
    actual.convert("RGB").save(artifact_actual_path(mode))
    diff = ImageChops.difference(expected.convert("RGB"), actual.convert("RGB"))
    diff.save(artifact_diff_path(mode))
