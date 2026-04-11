"""上游业务服务模块。

职责：
1. 拉取每日推荐数据。
2. 拉取并解析 TTML 歌词文本。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from ttml.ttml import TTML

from .constants import AMLL_BASE_URL, TTML_DB_URL_PREFIX
from .http_client import HttpClient
from .models import DailyRecommendation, NowPlayingData, Platform

logger = logging.getLogger(__name__)


class DailyRecommendationService:
    """每日推荐服务。"""

    async def fetch(self, date_str: str, http_client: HttpClient) -> Optional[DailyRecommendation]:
        """按日期获取每日推荐，失败返回 None。"""
        logger.info("正在获取 %s 的每日推荐...", date_str)
        url = f"{AMLL_BASE_URL}/api/daily-recommendations?date={date_str}"
        status, text = await http_client.get_text(url)
        if status != 200:
            logger.warning("Daily recommendation request failed: %s", status)
            return None

        if not text or text.strip() == "null":
            logger.info("该日期无每日推荐数据")
            return None

        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            logger.error("Daily recommendation JSON decode error: %s", exc)
            return None

        if not isinstance(data, dict) or "ncm_id" not in data:
            return None

        return DailyRecommendation(
            music_id=data.get("ncm_id"),
            date=data.get("date"),
            username=data.get("username"),
            comment=data.get("comment"),
            cover_path=data.get("cover"),
        )


class LyricsService:
    """歌词服务。"""

    _FOLDER_MAP: Dict[Platform, str] = {
        Platform.NCM: "ncm-lyrics",
        Platform.QQ: "qq-lyrics",
        Platform.AM: "am-lyrics",
    }

    async def fetch(self, music_id: str, platform: Platform, http_client: HttpClient) -> Optional[str]:
        """获取并解析指定平台歌曲的 TTML 歌词。"""
        folder = self._FOLDER_MAP.get(platform)
        if not folder:
            logger.warning("目前不支持平台 %s 的歌词获取", platform.value)
            return None

        url = f"{TTML_DB_URL_PREFIX}/{folder}/{music_id}.ttml"
        logger.info("正在从 %s 获取 %s 的 TTML 文件...", url, music_id)
        status, text = await http_client.get_text(url)
        if status != 200:
            logger.warning("TTML fetch failed: %s", status)
            return None

        if not text or text.strip() == "null":
            logger.info("该 ID 所对应歌词还无人制作")
            return None

        try:
            return TTML(text).text
        except Exception as exc:
            logger.error("TTML parse failed: %s", exc)
            return None


class NowPlayingService:
    """Now Playing 数据服务（解析 + 远程拉取）。"""

    @staticmethod
    def _pick_value(payload: dict[str, Any], *keys: str) -> Any:
        for key in keys:
            if key in payload:
                return payload[key]
        return None

    @classmethod
    def _parse_int_field(cls, payload: dict[str, Any], field_name: str, *aliases: str) -> int:
        raw_value = cls._pick_value(payload, *aliases)
        if raw_value is None:
            raise ValueError(f"缺少字段: {field_name}")
        if isinstance(raw_value, bool):
            raise ValueError(f"{field_name} 必须是整数")
        try:
            return int(raw_value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field_name} 必须是整数") from exc

    @classmethod
    def _parse_text_field(cls, payload: dict[str, Any], field_name: str, *aliases: str) -> str:
        raw_value = cls._pick_value(payload, *aliases)
        if raw_value is None:
            raise ValueError(f"缺少字段: {field_name}")
        if not isinstance(raw_value, str):
            raise ValueError(f"{field_name} 必须是字符串")
        normalized = raw_value.strip()
        if not normalized:
            raise ValueError(f"{field_name} 不能为空")
        return normalized

    @classmethod
    def parse_payload(cls, payload: dict[str, Any]) -> NowPlayingData:
        """解析并校验 now-playing 字典结构。"""
        progress_ms = cls._parse_int_field(payload, "progress", "progress_ms", "progress")
        duration_ms = cls._parse_int_field(payload, "duration", "duration_ms", "duration")
        if duration_ms <= 0:
            raise ValueError("duration 必须大于 0")

        track = cls._parse_text_field(payload, "track", "track", "title")
        artist = cls._parse_text_field(payload, "artist", "artist")
        cover_url = cls._parse_text_field(payload, "coverUrl", "cover_url", "coverUrl")

        song_url = cls._pick_value(payload, "song_url", "url")
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

    @classmethod
    def parse_json(cls, raw_json: str) -> NowPlayingData:
        """解析并校验 now-playing JSON 字符串。"""
        try:
            payload = json.loads(raw_json)
        except json.JSONDecodeError as exc:
            raise ValueError("now-playing JSON 解析失败") from exc

        if not isinstance(payload, dict):
            raise ValueError("now-playing JSON 必须是对象")

        return cls.parse_payload(payload)

    async def fetch_from_url(self, data_url: str, http_client: HttpClient) -> NowPlayingData:
        """通过 URL 拉取 now-playing JSON 并解析。"""
        status, payload = await http_client.get_json(data_url)
        if status != 200:
            raise ValueError(f"now-playing 数据 URL 请求失败: HTTP {status}")
        if payload is None:
            raise ValueError("now-playing 数据 URL 返回为空或非法 JSON")
        if not isinstance(payload, dict):
            raise ValueError("now-playing 数据 URL 必须返回 JSON 对象")
        return self.parse_payload(payload)
