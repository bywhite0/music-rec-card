"""图像资源下载模块。

职责：
1. 下载封面图片。
2. 在常规下载失败时使用 curl_cffi 做 TLS 指纹兜底。
"""

from __future__ import annotations

import logging
from io import BytesIO

from PIL import Image

from .http_client import HttpClient

logger = logging.getLogger(__name__)


async def download_image_with_fallback(url: str, http_client: HttpClient) -> Image.Image:
    """下载图片并在失败时回退到 TLS 指纹请求。

    输入：
    - url: 图片地址。
    - http_client: 已初始化的 HTTP 客户端。

    输出：
    - 可用的 `PIL.Image` 对象；若失败则返回默认灰底占位图。
    """
    if not url:
        return Image.new("RGB", (600, 600), color="#D3D3D3")

    status, content = await http_client.get_bytes(url)
    if status == 200 and content:
        try:
            return Image.open(BytesIO(content))
        except Exception as exc:
            logger.warning("图片解码失败，尝试 fallback: %s", exc)

    logger.warning("图片下载失败: %s，尝试使用 TLS 指纹...", status)
    try:
        from curl_cffi import requests

        response = requests.get(url, impersonate="chrome110")
        return Image.open(BytesIO(response.content))
    except Exception as exc:
        logger.error("图片下载出错: %s", exc)
        return Image.new("RGB", (600, 600), color="#D3D3D3")
