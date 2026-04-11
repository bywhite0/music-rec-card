"""歌曲信息提供者模块。

职责：
1. 定义平台提供者接口协议。
2. 为 NCM / QQ / Apple Music 提供统一的歌曲查询能力。
3. 提供平台与模式的容错转换函数。
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, Optional, Protocol, Union

from .constants import DEFAULT_USER_AGENT
from .http_client import HttpClient
from .models import Mode, Platform, SongInfo

logger = logging.getLogger(__name__)


class SongInfoProvider(Protocol):
    """歌曲信息提供者协议。"""

    async def fetch_song(self, music_id: str, http_client: HttpClient) -> Optional[SongInfo]:
        """根据平台 ID 查询歌曲信息。"""
        raise NotImplementedError

    def build_song_url(self, music_id: str, storefront: str = "cn") -> str:
        """根据平台规则生成歌曲落地页链接。"""
        raise NotImplementedError


class NcmProvider:
    """网易云音乐歌曲信息提供者。"""

    async def fetch_song(self, music_id: str, http_client: HttpClient) -> Optional[SongInfo]:
        """从 NCM API 查询歌曲。"""
        url = f"https://music.163.com/api/song/detail/?id={music_id}&ids=%5B{music_id}%5D"
        headers = {
            "User-Agent": DEFAULT_USER_AGENT,
        }
        logger.info("正在从 NCM API 获取 ID %s 的信息...", music_id)
        status, data = await http_client.get_json(url, headers=headers)
        if status != 200 or not data or not data.get("songs"):
            return None

        try:
            song = data["songs"][0]
            return SongInfo(
                title=song["name"],
                artist=" / ".join([a["name"] for a in song["artists"]]),
                cover_url=song["album"]["picUrl"],
                music_id=str(music_id),
            )
        except (KeyError, TypeError, IndexError) as exc:
            logger.error("NCM payload parse error: %s", exc)
            return None

    def build_song_url(self, music_id: str, storefront: str = "cn") -> str:
        """生成 NCM 歌曲链接。"""
        _ = storefront
        return f"https://music.163.com/#/song?id={music_id}"


class QqProvider:
    """QQ 音乐歌曲信息提供者。"""

    _ID_PATTERN = re.compile(r"^[0-9a-zA-Z]+$")
    _SHORT_LINK_PATTERN = re.compile(r"u\?__=[0-9a-zA-Z]")
    _EXTRACT_PATTERN = re.compile(r"(songDetail/|songmid=)([0-9a-zA-Z]+)")

    async def normalize_music_id(self, raw_music_id: str, http_client: HttpClient) -> Optional[str]:
        """标准化 QQ 音乐 ID，兼容短链与 URL。"""
        if self._ID_PATTERN.match(raw_music_id):
            return raw_music_id

        music_id = raw_music_id
        if self._SHORT_LINK_PATTERN.findall(raw_music_id):
            resolved = await http_client.resolve_final_url(raw_music_id)
            if resolved is None:
                return None
            music_id = resolved

        match = self._EXTRACT_PATTERN.search(music_id)
        if not match:
            return None
        return match.group(2)

    async def fetch_song(self, music_id: str, http_client: HttpClient) -> Optional[SongInfo]:
        """从 QQ 音乐接口查询歌曲。"""
        normalized_id = await self.normalize_music_id(music_id, http_client)
        if not normalized_id:
            return None

        logger.info("正在访问 QQ 音乐网页端获取 ID %s 的信息...", normalized_id)
        req_body: Dict[str, Any] = {
            "comm": {
                "ct": "26",
                "cv": "2010101",
                "v": "2010101",
            },
            "req": {
                "module": "music.trackInfo.UniformRuleCtrl",
                "method": "CgiGetTrackInfo",
                "param": {
                    "types": [1],
                    "ctx": 0,
                },
            },
        }

        if normalized_id.startswith("00"):
            req_body["req"]["param"]["mids"] = [str(normalized_id)]
        else:
            try:
                req_body["req"]["param"]["ids"] = [int(normalized_id)]
            except ValueError:
                return None

        status, data = await http_client.post_json("https://u.y.qq.com/cgi-bin/musicu.fcg", req_body)
        if status != 200 or not data or not data.get("req"):
            return None

        try:
            song = data["req"]["data"]["tracks"][0]
            title = song["title"]
            subtitle = song.get("subtitle")
            if subtitle and len(subtitle):
                title = f"{title} ({subtitle})"

            return SongInfo(
                title=title,
                artist=" / ".join([singer["name"] for singer in song["singer"]]),
                cover_url=f"https://y.qq.com/music/photo_new/T002R1200x1200M000{song['album']['mid']}.jpg",
                music_id=str(normalized_id),
            )
        except (KeyError, TypeError, IndexError) as exc:
            logger.error("QQ payload parse error: %s", exc)
            return None

    def build_song_url(self, music_id: str, storefront: str = "cn") -> str:
        """生成 QQ 音乐歌曲链接。"""
        _ = storefront
        return f"https://y.qq.com/n/ryqq_v2/songDetail/{music_id}"


class AppleProvider:
    """Apple Music 歌曲信息提供者。"""

    def __init__(self, country: str = "cn"):
        self.country = country

    async def fetch_song(self, music_id: str, http_client: HttpClient) -> Optional[SongInfo]:
        """从 iTunes Lookup API 查询歌曲。"""
        _ = http_client
        try:
            from curl_cffi.requests import AsyncSession
        except ImportError:
            logger.error("请先安装 curl_cffi 以使用 Apple Music 功能: pip install curl_cffi")
            return None

        logger.info("正在从 Apple Music API 获取 ID %s 的信息...", music_id)
        base_url = "https://itunes.apple.com/lookup"
        params = {
            "id": music_id,
            "entity": "song",
            "country": self.country,
            "limit": 1,
        }

        try:
            async with AsyncSession(impersonate="chrome120") as session:
                response = await session.get(base_url, params=params)
                if response.status_code != 200:
                    logger.error("AM API Error: %s", response.status_code)
                    return None

                data = response.json()
                if data.get("resultCount", 0) <= 0:
                    logger.warning("未找到歌曲 ID: %s", music_id)
                    return None

                song_data = data["results"][0]
                cover_url = song_data.get("artworkUrl100", "")
                if cover_url:
                    cover_url = cover_url.replace("100x100bb", "1024x1024bb")

                return SongInfo(
                    title=song_data.get("trackName", ""),
                    artist=song_data.get("artistName", ""),
                    cover_url=cover_url,
                    music_id=str(song_data.get("trackId", music_id)),
                    song_url=song_data.get("trackViewUrl"),
                )
        except Exception as exc:
            logger.error("AM API Error: %s", exc)
            return None

    def build_song_url(self, music_id: str, storefront: str = "cn") -> str:
        """生成 Apple Music 歌曲链接。"""
        return f"https://music.apple.com/{storefront}/song/{music_id}"


def coerce_platform(value: Union[str, Platform]) -> Platform:
    """将输入值转为平台枚举，失败时回退到 NCM。"""
    if isinstance(value, Platform):
        return value
    try:
        return Platform(value)
    except ValueError:
        logger.warning("未知平台 %s，回退到 ncm", value)
        return Platform.NCM


def coerce_mode(value: Union[str, Mode]) -> Mode:
    """将输入值转为模式枚举，失败时回退到 daily。"""
    if isinstance(value, Mode):
        return value
    try:
        normalized = str(value).strip().lower().replace("_", "-")
        if normalized == "nowplaying":
            normalized = Mode.NOW_PLAYING.value
        return Mode(normalized)
    except ValueError:
        logger.warning("未知模式 %s，回退到 daily", value)
        return Mode.DAILY


def get_song_provider(platform: Platform, am_storefront: str = "cn") -> SongInfoProvider:
    """根据平台选择歌曲提供者实现。"""
    if platform == Platform.NCM:
        return NcmProvider()
    if platform == Platform.QQ:
        return QqProvider()
    if platform == Platform.AM:
        return AppleProvider(am_storefront)
    return NcmProvider()
