"""上游业务服务模块。

职责：
1. 拉取每日推荐数据。
2. 拉取并解析 TTML 歌词文本。
"""

from __future__ import annotations

import json
import logging
from typing import Dict, Optional

from ttml.ttml import TTML

from .constants import AMLL_BASE_URL, TTML_DB_URL_PREFIX
from .http_client import HttpClient
from .models import DailyRecommendation, Platform

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
