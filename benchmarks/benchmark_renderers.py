"""Benchmark the Pillow and Skia music-card renderers.

Run with:

    python -m benchmarks.benchmark_renderers
"""

from __future__ import annotations

import argparse
import asyncio
import gc
import json
import math
import statistics
import sys
from contextlib import ExitStack
from dataclasses import asdict, dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from time import perf_counter_ns
from typing import Awaitable, Callable
from unittest.mock import patch

from PIL import Image, ImageDraw, ImageFont

from music_card import CardPayload, Mode, MusicCard, Platform

try:
    from music_card_skia import SkiaMusicCardRenderer
except ImportError as exc:  # pragma: no cover - exercised only on missing optional dependency.
    SkiaMusicCardRenderer = None  # type: ignore[assignment]
    SKIA_IMPORT_ERROR = exc
else:
    SKIA_IMPORT_ERROR = None


ALL_MODES = (Mode.DAILY, Mode.CARD, Mode.LYRIC, Mode.NOW_PLAYING)
PILLOW_FONT_INDICES = (
    MusicCard.Regular,
    MusicCard.Medium,
    MusicCard.Semibold,
    MusicCard.Light,
    MusicCard.Thin,
    MusicCard.Ultralight,
)


@dataclass(frozen=True)
class RenderStats:
    samples: int
    total_ms: float
    mean_ms: float
    median_ms: float
    min_ms: float
    max_ms: float
    p95_ms: float
    stdev_ms: float


@dataclass(frozen=True)
class ModeBenchmark:
    mode: str
    pillow: RenderStats
    skia: RenderStats | None
    skia_speedup_vs_pillow: float | None


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be greater than 0")
    return parsed


def _non_negative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("value must be 0 or greater")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Benchmark Pillow and Skia music-card rendering performance.")
    parser.add_argument("--iterations", type=_positive_int, default=30, help="Measured iterations per renderer.")
    parser.add_argument("--warmup", type=_non_negative_int, default=5, help="Warmup iterations per renderer.")
    parser.add_argument(
        "--modes",
        nargs="+",
        choices=[mode.value for mode in ALL_MODES],
        default=[mode.value for mode in ALL_MODES],
        help="Modes to benchmark.",
    )
    parser.add_argument("--font-path", help="Font path used by both renderers when possible.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    return parser


def build_cover_image() -> Image.Image:
    size = 720
    image = Image.new("RGB", (size, size), "#203040")
    draw = ImageDraw.Draw(image)

    for y in range(size):
        r = int(24 + (120 * y / size))
        g = int(44 + (96 * y / size))
        b = int(88 + (112 * y / size))
        draw.line((0, y, size - 1, y), fill=(r, g, b))

    draw.ellipse((74, 86, 560, 572), outline="#F0D48C", width=12)
    draw.rectangle((128, 418, 624, 652), outline="#D86652", width=9)
    draw.polygon([(540, 104), (684, 268), (558, 310)], fill="#A95D66")
    draw.line((110, 120, 650, 650), fill="#F6E7BD", width=6)
    return image


def build_payload(mode: Mode) -> CardPayload:
    quote_by_mode = {
        Mode.DAILY: "\n".join(
            [
                "[:-:]Benchmark heading",
                "[:_]small benchmark subtitle",
                "[-]Section",
                "[:-]A long benchmark quote line that should wrap consistently across repeated render calls.",
                "Plain text line for steady wrapping and paint work.",
                "[-:]Right aligned closing line",
            ]
        ),
        Mode.CARD: "Card mode quote placeholder",
        Mode.LYRIC: "\n".join(
            [
                "[:-]Lyric benchmark line with enough text to exercise wrapping.",
                "[:_]translated lyric benchmark line",
                "[-:]right aligned lyric line",
            ]
        ),
        Mode.NOW_PLAYING: "",
    }
    source_by_mode = {
        Mode.DAILY: "BenchmarkUser",
        Mode.CARD: "BenchmarkUser",
        Mode.LYRIC: "BenchmarkUser",
        Mode.NOW_PLAYING: "",
    }
    return CardPayload(
        title="Benchmark Test Song",
        artist="Benchmark Artist",
        cover_url="benchmark://cover",
        quote_content=quote_by_mode[mode],
        quote_source=source_by_mode[mode],
        date_obj=datetime(2026, 3, 14),
        music_id="42",
        song_url="https://example.com/song/42",
        progress_ms=13_282 if mode == Mode.NOW_PLAYING else None,
        duration_ms=218_608 if mode == Mode.NOW_PLAYING else None,
    )


def _candidate_font_paths() -> tuple[Path, ...]:
    return (
        Path(r"C:\Windows\Fonts\PingFang.ttc"),
        Path("/System/Library/Fonts/PingFang.ttc"),
        Path("/System/Library/Fonts/Supplemental/PingFang.ttc"),
        Path(r"C:\Windows\Fonts\msyh.ttc"),
        Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"),
        Path(r"C:\Windows\Fonts\arial.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    )


def resolve_font_path(font_path: str | None) -> str:
    if font_path:
        return str(Path(font_path).expanduser())

    for candidate in _candidate_font_paths():
        if candidate.exists():
            return str(candidate)
    return "__benchmark_missing_font__"


def _can_load_font(font_path: str, index: int) -> bool:
    try:
        ImageFont.truetype(font_path, 12, index=index)
    except OSError:
        return False
    return True


@lru_cache(maxsize=256)
def _first_face_font_loader(font_path: str, size: int, index: int) -> ImageFont.FreeTypeFont:
    _ = index
    try:
        return ImageFont.truetype(font_path, size, index=0)
    except OSError:
        return ImageFont.load_default(size=size)


@lru_cache(maxsize=256)
def _default_font_loader(font_path: str, size: int, index: int) -> ImageFont.FreeTypeFont:
    _ = font_path, index
    return ImageFont.load_default(size=size)


def pillow_font_strategy(font_path: str) -> tuple[str, Callable[[str, int, int], ImageFont.FreeTypeFont] | None]:
    if all(_can_load_font(font_path, index) for index in PILLOW_FONT_INDICES):
        return "configured font collection", None
    if _can_load_font(font_path, 0):
        return "configured font first face", _first_face_font_loader
    return "Pillow default font", _default_font_loader


def _stats(samples: list[float]) -> RenderStats:
    sorted_samples = sorted(samples)
    p95_index = min(len(sorted_samples) - 1, max(0, math.ceil(len(sorted_samples) * 0.95) - 1))
    return RenderStats(
        samples=len(samples),
        total_ms=sum(samples),
        mean_ms=statistics.fmean(samples),
        median_ms=statistics.median(samples),
        min_ms=min(samples),
        max_ms=max(samples),
        p95_ms=sorted_samples[p95_index],
        stdev_ms=statistics.stdev(samples) if len(samples) > 1 else 0.0,
    )


def _ensure_rendered(name: str, image: Image.Image) -> None:
    if not isinstance(image, Image.Image):
        raise TypeError(f"{name} did not return a Pillow image")
    if image.width <= 0 or image.height <= 0:
        raise RuntimeError(f"{name} returned an empty image")
    if image.size == (100, 100):
        raise RuntimeError(f"{name} returned the Pillow renderer font-error placeholder")


async def _time_renderer(
    name: str,
    render_once: Callable[[], Awaitable[Image.Image]],
    *,
    warmup: int,
    iterations: int,
) -> RenderStats:
    for _ in range(warmup):
        _ensure_rendered(name, await render_once())

    gc.collect()
    samples: list[float] = []
    for _ in range(iterations):
        started = perf_counter_ns()
        image = await render_once()
        elapsed_ms = (perf_counter_ns() - started) / 1_000_000
        _ensure_rendered(name, image)
        samples.append(elapsed_ms)
    return _stats(samples)


async def benchmark_mode(mode: Mode, font_path: str, warmup: int, iterations: int) -> ModeBenchmark:
    payload = build_payload(mode)
    pillow_renderer = MusicCard(font_path=font_path, platform=Platform.NCM)

    async def render_pillow() -> Image.Image:
        return await pillow_renderer.generate(payload, inner_blurred=True, show_qrcode=False, mode=mode)

    pillow_stats = await _time_renderer("Pillow", render_pillow, warmup=warmup, iterations=iterations)

    if SkiaMusicCardRenderer is None:
        return ModeBenchmark(mode=mode.value, pillow=pillow_stats, skia=None, skia_speedup_vs_pillow=None)

    skia_renderer = SkiaMusicCardRenderer(font_path=font_path, platform=Platform.NCM)

    async def render_skia() -> Image.Image:
        return await skia_renderer.generate(payload, inner_blurred=True, show_qrcode=False, mode=mode)

    skia_stats = await _time_renderer("Skia", render_skia, warmup=warmup, iterations=iterations)
    return ModeBenchmark(
        mode=mode.value,
        pillow=pillow_stats,
        skia=skia_stats,
        skia_speedup_vs_pillow=pillow_stats.mean_ms / skia_stats.mean_ms,
    )


async def run_benchmark(args: argparse.Namespace) -> tuple[str, str, list[ModeBenchmark]]:
    font_path = resolve_font_path(args.font_path)
    font_strategy, pillow_font_loader = pillow_font_strategy(font_path)
    cover_image = build_cover_image()

    async def fake_pillow_download(url: str, http_client: object) -> Image.Image:
        _ = url, http_client
        return cover_image.copy()

    async def fake_skia_load_cover(url: str, http_client: object | None = None) -> Image.Image:
        _ = url, http_client
        return cover_image.copy()

    with ExitStack() as stack:
        stack.enter_context(patch("music_card.assets.download_image_with_fallback", side_effect=fake_pillow_download))
        if SkiaMusicCardRenderer is not None:
            stack.enter_context(patch("music_card_skia.renderer.skia_renderer.load_cover_image", side_effect=fake_skia_load_cover))
        if pillow_font_loader is not None:
            stack.enter_context(patch.object(MusicCard, "_cached_font", staticmethod(pillow_font_loader)))

        results = []
        for mode_value in args.modes:
            results.append(await benchmark_mode(Mode(mode_value), font_path, args.warmup, args.iterations))
    return font_path, font_strategy, results


def _format_ms(value: float) -> str:
    return f"{value:9.2f}"


def _describe_speedup(speedup: float) -> str:
    if speedup >= 1:
        return f"Skia is {speedup:.2f}x faster than Pillow by mean time"
    return f"Skia is {(1 / speedup):.2f}x slower than Pillow by mean time"


def format_text(
    *,
    iterations: int,
    warmup: int,
    font_path: str,
    font_strategy: str,
    results: list[ModeBenchmark],
) -> str:
    lines = [
        "Pillow vs Skia renderer benchmark",
        f"iterations={iterations} warmup={warmup}",
        f"font={font_path}",
        f"pillow-font-strategy={font_strategy}",
        "",
        "mode         renderer   mean(ms) median(ms)   min(ms)   max(ms)   p95(ms) stdev(ms)  total(ms)",
        "-----------  --------  --------- ---------- --------- --------- --------- --------- ----------",
    ]
    for result in results:
        lines.append(
            f"{result.mode:<11}  Pillow   "
            f"{_format_ms(result.pillow.mean_ms)} {_format_ms(result.pillow.median_ms)} "
            f"{_format_ms(result.pillow.min_ms)} {_format_ms(result.pillow.max_ms)} "
            f"{_format_ms(result.pillow.p95_ms)} {_format_ms(result.pillow.stdev_ms)} "
            f"{_format_ms(result.pillow.total_ms)}"
        )
        if result.skia is None or result.skia_speedup_vs_pillow is None:
            continue
        lines.append(
            f"{'':<11}  Skia     "
            f"{_format_ms(result.skia.mean_ms)} {_format_ms(result.skia.median_ms)} "
            f"{_format_ms(result.skia.min_ms)} {_format_ms(result.skia.max_ms)} "
            f"{_format_ms(result.skia.p95_ms)} {_format_ms(result.skia.stdev_ms)} "
            f"{_format_ms(result.skia.total_ms)}"
        )
        lines.append(f"{'':<11}  {_describe_speedup(result.skia_speedup_vs_pillow)}")
    return "\n".join(lines)


async def async_main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if SKIA_IMPORT_ERROR is not None:
        print(f"music_card_skia unavailable, benchmarking Pillow only. ({SKIA_IMPORT_ERROR})", file=sys.stderr)

    font_path, font_strategy, results = await run_benchmark(args)
    if args.json:
        print(
            json.dumps(
                {
                    "iterations": args.iterations,
                    "warmup": args.warmup,
                    "font_path": font_path,
                    "pillow_font_strategy": font_strategy,
                    "results": [asdict(result) for result in results],
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        print(
            format_text(
                iterations=args.iterations,
                warmup=args.warmup,
                font_path=font_path,
                font_strategy=font_strategy,
                results=results,
            )
        )
    return 0


def main() -> None:
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    raise SystemExit(asyncio.run(async_main()))


if __name__ == "__main__":
    main()
