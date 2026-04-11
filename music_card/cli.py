"""命令行入口模块。

职责：
1. 解析命令行参数。
2. 调用业务编排层生成卡片。
3. 保存最终图片并返回进程状态码。
"""

from __future__ import annotations

import argparse
import logging
from datetime import datetime

from .models import CardRequest, Mode, QuoteData, SongInfo
from .providers import coerce_mode, coerce_platform
from .usecases import generate_card

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    """构建 CLI 参数解析器。"""
    parser = argparse.ArgumentParser(description="生成仿网易云音乐风格的音乐卡片")
    parser.add_argument("--platform", type=str, choices=["ncm", "qq", "am"], default="ncm", help="获取歌曲的平台 ncm/qq/am")
    parser.add_argument("--mode", type=str, choices=["daily", "card", "lyric"], default="daily", help="制卡模式")
    parser.add_argument("--date", type=str, default=datetime.now().strftime("%Y-%m-%d"), help="日期 YYYY-MM-DD")
    parser.add_argument("--info", nargs=3, metavar=("TITLE", "ARTIST", "COVER_URL"), help="手动指定歌曲信息")
    parser.add_argument("--quote", nargs=2, metavar=("CONTENT", "SOURCE"), help="引言内容与来源")
    parser.add_argument("--inner-blurred", action="store_true", help="卡片内部背景模糊")
    parser.add_argument("--qrcode", action="store_true", help="生成二维码")
    parser.add_argument("--music-id", type=str, help="歌曲 ID")
    parser.add_argument("--am-storefront", type=str, default="cn", help="Apple Music 商店地区 (默认 cn)")
    return parser


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

    request = CardRequest(
        platform=coerce_platform(args.platform),
        mode=coerce_mode(args.mode),
        date_str=args.date,
        music_id=args.music_id,
        song_info=song_info,
        quote=quote,
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

    result.image.save(filename)
    logger.info("图片保存成功: %s", filename)
