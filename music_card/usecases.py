"""业务编排模块。

职责：
1. 组织“拉取数据 -> 组装载荷 -> 调用渲染器”的完整流程。
2. 对外提供高层调用入口，供 CLI 与外部脚本复用。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from PIL import Image

from .constants import AMLL_BASE_URL
from .http_client import HttpClient
from .models import CardPayload, CardRequest, CardResult, Mode, NowPlayingData, QuoteData, SongInfo
from .providers import coerce_mode, coerce_platform, get_song_provider
from .renderer import MusicCard
from .services import DailyRecommendationService, LyricsService, NowPlayingService

logger = logging.getLogger(__name__)


async def fetch_lines(music_id: str, platform: str) -> Optional[str]:
    """按平台与歌曲 ID 获取歌词文本。"""
    safe_platform = coerce_platform(platform)
    async with HttpClient(timeout=10, trust_env=True) as http_client:
        return await LyricsService().fetch(music_id, safe_platform, http_client)


def _parse_date_or_today(date_str: str) -> tuple[datetime, str]:
    """解析日期字符串，失败时回退到今天。"""
    try:
        date_obj = datetime.strptime(date_str, "%Y-%m-%d")
        return date_obj, date_str
    except ValueError:
        logger.warning("⚠️ 日期格式错误 (%s)，使用今日", date_str)
        date_obj = datetime.now()
        return date_obj, date_obj.strftime("%Y-%m-%d")


def _build_now_playing_payload(
    now_playing: Optional[NowPlayingData],
    date_obj: datetime,
    fallback_music_id: Optional[str],
) -> tuple[Optional[CardPayload], Optional[str]]:
    """构建并校验 now-playing 的渲染载荷。"""
    if now_playing is None:
        return None, "错误: now-playing 模式需要提供 now playing 数据"

    if not isinstance(now_playing.track, str) or not now_playing.track.strip():
        return None, "错误: now-playing 数据缺少有效 track"
    if not isinstance(now_playing.artist, str) or not now_playing.artist.strip():
        return None, "错误: now-playing 数据缺少有效 artist"
    if not isinstance(now_playing.cover_url, str) or not now_playing.cover_url.strip():
        return None, "错误: now-playing 数据缺少有效 coverUrl"

    try:
        if isinstance(now_playing.duration_ms, bool) or isinstance(now_playing.progress_ms, bool):
            raise ValueError("bool-is-not-int")
        duration_ms = int(now_playing.duration_ms)
        progress_ms = int(now_playing.progress_ms)
    except (TypeError, ValueError):
        return None, "错误: now-playing 的 progress/duration 必须是整数"

    if duration_ms <= 0:
        return None, "错误: now-playing 的 duration 必须大于 0"

    payload = CardPayload(
        title=now_playing.track,
        artist=now_playing.artist,
        cover_url=now_playing.cover_url,
        quote_content="",
        quote_source="",
        date_obj=date_obj,
        music_id=fallback_music_id or "0",
        song_url=now_playing.song_url,
        progress_ms=progress_ms,
        duration_ms=duration_ms,
    )
    return payload, None


async def _resolve_now_playing_data(req: CardRequest, http_client: HttpClient) -> tuple[Optional[NowPlayingData], Optional[str]]:
    """按优先级解析 now-playing 数据：URL > JSON > 对象。"""
    service = NowPlayingService()
    try:
        if req.now_playing_data_url:
            return await service.fetch_from_url(req.now_playing_data_url, http_client), None
        if req.now_playing_json:
            return service.parse_json(req.now_playing_json), None
        if req.now_playing:
            return req.now_playing, None
        return None, "错误: now-playing 模式需要提供 now playing 数据"
    except ValueError as exc:
        return None, f"错误: {exc}"


async def generate_card(req: CardRequest) -> CardResult:
    """高层入口：根据请求生成卡片。

    返回：
    - 成功：`CardResult(success=True, image=...)`
    - 失败：`CardResult(success=False, error_code=..., message=...)`
    """
    platform = coerce_platform(req.platform)
    mode = coerce_mode(req.mode)
    date_obj, normalized_date = _parse_date_or_today(req.date_str)

    provider = get_song_provider(platform, req.am_storefront)
    renderer = MusicCard(
        font_path=req.font_path,
        platform=platform,
        am_storefront=req.am_storefront,
        style=req.style,
        layout=req.layout,
    )

    final_song: Optional[SongInfo] = None
    quote_content: Optional[str] = None
    quote_source: str = ""

    try:
        async with HttpClient(timeout=15, trust_env=True) as http_client:
            if mode == Mode.NOW_PLAYING:
                now_playing_data, resolve_error = await _resolve_now_playing_data(req, http_client)
                if now_playing_data is None:
                    return CardResult(
                        success=False,
                        error_code="NOW_PLAYING_INVALID",
                        message=resolve_error or "错误: now-playing 数据无效",
                    )

                payload, error_message = _build_now_playing_payload(now_playing_data, date_obj, req.music_id)
                if payload is None:
                    return CardResult(
                        success=False,
                        error_code="NOW_PLAYING_INVALID",
                        message=error_message or "错误: now-playing 数据无效",
                    )

                image = await renderer.generate(
                    payload,
                    inner_blurred=req.inner_blurred,
                    show_qrcode=False,
                    mode=mode,
                    http_client=http_client,
                )

                return CardResult(
                    success=True,
                    image=image,
                    music_id=payload.music_id or "0",
                    message="ok",
                )

            daily_data = None
            if mode == Mode.DAILY:
                daily_data = await DailyRecommendationService().fetch(normalized_date, http_client)

            if daily_data:
                logger.info("成功获取每日推荐数据")
                rec_music_id = daily_data.music_id
                if rec_music_id:
                    final_song = await provider.fetch_song(str(rec_music_id), http_client)
                    if final_song:
                        cover = daily_data.cover_path
                        if cover and cover != "/tj/wfm.jpg":
                            if cover.startswith("/"):
                                final_song.cover_url = AMLL_BASE_URL + cover
                            else:
                                final_song.cover_url = cover

                if daily_data.comment:
                    quote_content = daily_data.comment
                if daily_data.username:
                    quote_source = daily_data.username
            else:
                if mode == Mode.DAILY:
                    logger.info("无每日推荐或获取失败，检查命令行参数...")

                if req.music_id:
                    final_song = await provider.fetch_song(req.music_id, http_client)
                elif req.song_info:
                    final_song = SongInfo(
                        title=req.song_info.title,
                        artist=req.song_info.artist,
                        cover_url=req.song_info.cover_url,
                        music_id=req.song_info.music_id or (req.music_id or "0"),
                        song_url=req.song_info.song_url,
                    )

            if not final_song:
                return CardResult(
                    success=False,
                    error_code="SONG_INFO_UNAVAILABLE",
                    message="错误: 无法获取歌曲信息 (Daily API 返回值为空, 且未提供有效 NCM ID 或 Info)",
                )

            if not quote_content:
                if mode == Mode.LYRIC:
                    if not final_song.music_id or final_song.music_id == "0":
                        return CardResult(
                            success=False,
                            error_code="LYRIC_MUSIC_ID_REQUIRED",
                            message="错误: lyric 模式需要有效 music_id",
                        )

                    lines = await LyricsService().fetch(final_song.music_id, platform, http_client)
                    if not lines:
                        return CardResult(
                            success=False,
                            error_code="LYRIC_UNAVAILABLE",
                            message="错误: 无法获取歌词内容",
                        )
                    quote_content = lines
                elif req.quote:
                    quote_content = req.quote.content.replace("\\n", "\n")
                    quote_source = req.quote.source
                else:
                    quote_content = "想要和你一起 一同实现远大的梦想"
                    quote_source = "RuriChan"

            song_url = final_song.song_url
            if not song_url and final_song.music_id and final_song.music_id != "0":
                song_url = provider.build_song_url(final_song.music_id, storefront=req.am_storefront)

            payload = CardPayload(
                title=final_song.title,
                artist=final_song.artist,
                cover_url=final_song.cover_url,
                quote_content=quote_content,
                quote_source=quote_source,
                date_obj=date_obj,
                music_id=final_song.music_id,
                song_url=song_url,
            )

            image = await renderer.generate(
                payload,
                inner_blurred=req.inner_blurred,
                show_qrcode=req.show_qrcode,
                mode=mode,
                http_client=http_client,
            )

            return CardResult(
                success=True,
                image=image,
                music_id=final_song.music_id or "0",
                message="ok",
            )
    except Exception as exc:
        logger.exception("生成音乐卡片时发生未处理异常")
        return CardResult(
            success=False,
            error_code="UNHANDLED_EXCEPTION",
            message=f"错误: {exc}",
        )


async def generate_music_card_process(
    platform: str,
    mode: str,
    date_str: str,
    music_id_arg: Optional[str] = None,
    info_arg: Optional[list] = None,
    quote_arg: Optional[list] = None,
    inner_blurred: bool = False,
    show_qrcode: bool = False,
    font_path: str = "PingFang.ttc",
    am_storefront: str = "cn",
    now_playing_arg: Optional[NowPlayingData] = None,
    now_playing_json_arg: Optional[str] = None,
    now_playing_data_url_arg: Optional[str] = None,
) -> Optional[tuple[Image.Image, str]]:
    """对外友好的流程入口，返回 `(image, music_id)`。"""
    song_info = None
    if info_arg and len(info_arg) == 3:
        song_info = SongInfo(
            title=info_arg[0],
            artist=info_arg[1],
            cover_url=info_arg[2],
            music_id=music_id_arg or "0",
        )

    quote = None
    if quote_arg and len(quote_arg) == 2:
        quote = QuoteData(content=quote_arg[0], source=quote_arg[1])

    request = CardRequest(
        platform=coerce_platform(platform),
        mode=coerce_mode(mode),
        date_str=date_str,
        music_id=music_id_arg,
        song_info=song_info,
        quote=quote,
        now_playing=now_playing_arg,
        now_playing_json=now_playing_json_arg,
        now_playing_data_url=now_playing_data_url_arg,
        inner_blurred=inner_blurred,
        show_qrcode=show_qrcode,
        font_path=font_path,
        am_storefront=am_storefront,
    )

    result = await generate_card(request)
    if not result.success or result.image is None:
        logger.error(result.message)
        return None
    return result.image, (result.music_id or "0")
