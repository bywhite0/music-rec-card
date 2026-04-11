"""命令行入口模块。

职责：
1. 解析命令行参数。
2. 调用业务编排层生成卡片。
3. 保存最终图片并返回进程状态码。
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime
from typing import Any

from .http_client import HttpClient
from .models import CardRequest, Mode, NowPlayingData, QuoteData, SongInfo
from .providers import coerce_mode, coerce_platform
from .usecases import generate_card

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    """构建 CLI 参数解析器。"""
    parser = argparse.ArgumentParser(description="生成仿网易云音乐风格的音乐卡片")
    parser.add_argument("--platform", type=str, choices=["ncm", "qq", "am"], default="ncm", help="获取歌曲的平台 ncm/qq/am")
    parser.add_argument(
        "--mode",
        type=str,
        choices=["daily", "card", "lyric", "now-playing"],
        default="daily",
        help="制卡模式",
    )
    parser.add_argument("--date", type=str, default=datetime.now().strftime("%Y-%m-%d"), help="日期 YYYY-MM-DD")
    parser.add_argument("--info", nargs=3, metavar=("TITLE", "ARTIST", "COVER_URL"), help="手动指定歌曲信息")
    parser.add_argument("--quote", nargs=2, metavar=("CONTENT", "SOURCE"), help="引言内容与来源")
    parser.add_argument("--now-playing-data-url", type=str, help="Now Playing 数据 URL")
    parser.add_argument("--now-playing-json", type=str, help="Now Playing 数据 JSON 字符串")
    parser.add_argument("--inner-blurred", action="store_true", help="卡片内部背景模糊")
    parser.add_argument("--qrcode", action="store_true", help="生成二维码")
    parser.add_argument("--music-id", type=str, help="歌曲 ID")
    parser.add_argument("--am-storefront", type=str, default="cn", help="Apple Music 商店地区 (默认 cn)")
    return parser


def _pick_value(payload: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in payload:
            return payload[key]
    return None


def _parse_int_field(payload: dict[str, Any], field_name: str, *aliases: str) -> int:
    raw_value = _pick_value(payload, *aliases)
    if raw_value is None:
        raise ValueError(f"缺少字段: {field_name}")
    if isinstance(raw_value, bool):
        raise ValueError(f"{field_name} 必须是整数")
    try:
        return int(raw_value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} 必须是整数") from exc


def _parse_text_field(payload: dict[str, Any], field_name: str, *aliases: str) -> str:
    raw_value = _pick_value(payload, *aliases)
    if raw_value is None:
        raise ValueError(f"缺少字段: {field_name}")
    if not isinstance(raw_value, str):
        raise ValueError(f"{field_name} 必须是字符串")
    normalized = raw_value.strip()
    if not normalized:
        raise ValueError(f"{field_name} 不能为空")
    return normalized


def parse_now_playing_json(raw_json: str) -> NowPlayingData:
    """解析并校验 now-playing JSON 输入。"""
    try:
        payload = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise ValueError("now-playing JSON 解析失败") from exc

    if not isinstance(payload, dict):
        raise ValueError("now-playing JSON 必须是对象")

    progress_ms = _parse_int_field(payload, "progress", "progress_ms", "progress")
    duration_ms = _parse_int_field(payload, "duration", "duration_ms", "duration")
    if duration_ms <= 0:
        raise ValueError("duration 必须大于 0")

    track = _parse_text_field(payload, "track", "track", "title")
    artist = _parse_text_field(payload, "artist", "artist")
    cover_url = _parse_text_field(payload, "coverUrl", "cover_url", "coverUrl")

    song_url = _pick_value(payload, "song_url", "url")
    if song_url is not None:
        if not isinstance(song_url, str):
            raise ValueError("url 必须是字符串")
        song_url = song_url.strip() or None

    return NowPlayingData(
        progress_ms=progress_ms,
        duration_ms=duration_ms,
        track=track,
        artist=artist,
        cover_url=cover_url,
        song_url=song_url,
    )


async def fetch_now_playing_json_from_url(data_url: str) -> str:
    """从 URL 拉取 now-playing JSON 文本。"""
    async with HttpClient(timeout=10, trust_env=True) as http_client:
        status, text = await http_client.get_text(data_url)
    if status != 200:
        raise ValueError(f"now-playing 数据 URL 请求失败: HTTP {status}")
    if not text.strip():
        raise ValueError("now-playing 数据 URL 返回为空")
    return text


async def main() -> None:
    """执行 CLI 主流程。"""
    args = build_parser().parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    song_info = None
    if args.info and len(args.info) == 3:
        song_info = SongInfo(
            title=args.info[0],
            artist=args.info[1],
            cover_url=args.info[2],
            music_id=args.music_id or "0",
        )

    quote = None
    if args.quote and len(args.quote) == 2:
        quote = QuoteData(content=args.quote[0], source=args.quote[1])

    now_playing = None
    if args.now_playing_data_url or args.now_playing_json:
        try:
            if args.now_playing_data_url:
                raw_json = await fetch_now_playing_json_from_url(args.now_playing_data_url)
            else:
                raw_json = args.now_playing_json
            now_playing = parse_now_playing_json(raw_json)
        except ValueError as exc:
            logger.error("错误: %s", exc)
            raise SystemExit(1)

    request = CardRequest(
        platform=coerce_platform(args.platform),
        mode=coerce_mode(args.mode),
        date_str=args.date,
        music_id=args.music_id,
        song_info=song_info,
        quote=quote,
        now_playing=now_playing,
        inner_blurred=args.inner_blurred,
        show_qrcode=args.qrcode,
        font_path="PingFang.ttc",
        am_storefront=args.am_storefront,
    )

    result = await generate_card(request)
    if not result.success or result.image is None:
        logger.error(result.message or "生成失败")
        raise SystemExit(1)

    music_id = result.music_id or "0"
    match coerce_mode(args.mode):
        case Mode.LYRIC:
            filename = f"music_lyric_{music_id}.png"
        case Mode.CARD:
            filename = f"music_card_{music_id}.png"
        case Mode.DAILY:
            filename = f"music_card_{args.date}.png"
        case Mode.NOW_PLAYING:
            filename = f"music_now_playing_{music_id}.png"

    result.image.save(filename)
    logger.info("图片保存成功: %s", filename)
