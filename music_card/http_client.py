"""异步 HTTP 客户端封装模块。

职责：
1. 统一 `aiohttp` 会话生命周期管理。
2. 提供文本、字节、JSON 与重定向 URL 查询的轻量封装。
3. 在错误场景下返回可判定的兜底值，便于业务层处理。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

import aiohttp

logger = logging.getLogger(__name__)


class HttpClient:
    """简易异步 HTTP 客户端。

    参数：
    - headers: 会话级默认请求头。
    - timeout: 总超时时间（秒）。
    - trust_env: 是否信任环境代理配置。
    """

    def __init__(
        self,
        headers: Optional[Dict[str, str]] = None,
        timeout: int = 10,
        trust_env: bool = False,
    ):
        self._headers = headers or {}
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._trust_env = trust_env
        self._session: Optional[aiohttp.ClientSession] = None

    async def __aenter__(self) -> "HttpClient":
        """进入上下文并确保会话可用。"""
        await self._ensure_session()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        """退出上下文并关闭会话。"""
        await self.close()

    async def _ensure_session(self) -> aiohttp.ClientSession:
        """按需创建会话并复用。"""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers=self._headers,
                timeout=self._timeout,
                trust_env=self._trust_env,
            )
        return self._session

    async def close(self) -> None:
        """显式关闭会话。"""
        if self._session and not self._session.closed:
            await self._session.close()

    async def get_text(self, url: str, **kwargs: Any) -> tuple[int, str]:
        """发起 GET 请求并返回 (状态码, 文本内容)。"""
        session = await self._ensure_session()
        try:
            async with session.get(url, **kwargs) as resp:
                return resp.status, await resp.text()
        except Exception as exc:
            logger.error("HTTP GET text failed: %s", exc)
            return 0, ""

    async def get_bytes(self, url: str, **kwargs: Any) -> tuple[int, bytes]:
        """发起 GET 请求并返回 (状态码, 二进制内容)。"""
        session = await self._ensure_session()
        try:
            async with session.get(url, **kwargs) as resp:
                return resp.status, await resp.read()
        except Exception as exc:
            logger.error("HTTP GET bytes failed: %s", exc)
            return 0, b""

    async def get_json(self, url: str, **kwargs: Any) -> tuple[int, Any]:
        """发起 GET 请求并尝试解析 JSON。"""
        session = await self._ensure_session()
        try:
            async with session.get(url, **kwargs) as resp:
                text = await resp.text()
                return resp.status, json.loads(text)
        except Exception as exc:
            logger.error("HTTP GET json failed: %s", exc)
            return 0, None

    async def post_json(self, url: str, payload: Dict[str, Any], **kwargs: Any) -> tuple[int, Any]:
        """发起 POST JSON 请求并尝试解析响应 JSON。"""
        session = await self._ensure_session()
        try:
            async with session.post(url, json=payload, **kwargs) as resp:
                text = await resp.text()
                return resp.status, json.loads(text)
        except Exception as exc:
            logger.error("HTTP POST json failed: %s", exc)
            return 0, None

    async def resolve_final_url(
        self,
        url: str,
        max_redirects: int = 10,
    ) -> Optional[str]:
        """跟随重定向并返回最终 URL。"""
        session = await self._ensure_session()
        try:
            async with session.get(
                url,
                allow_redirects=True,
                max_redirects=max_redirects,
            ) as response:
                return str(response.url)
        except Exception as exc:
            logger.error("resolve_final_url failed: %s", exc)
            return None
