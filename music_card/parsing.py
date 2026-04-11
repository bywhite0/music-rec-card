"""文本解析模块。

职责：
1. 处理引言中的 HTML 转义符与 emoji 清洗。
2. 解析引言样式语法（如 `[:-:]`、`[-]`）。
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Dict

import emoji

from .models import QuoteLine, QuoteParseResult


def from_html_escaped(text: str) -> str:
    """将常见 HTML 转义字符还原为普通文本。"""
    return (
        text.replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&amp;", "&")
        .replace("&quot;", "\"")
        .replace("&#39;", "'")
    )


@lru_cache(maxsize=1)
def _build_emoji_regex() -> re.Pattern[str]:
    """构建 emoji 清洗正则（带缓存，避免重复构建开销）。"""
    language_pack: Dict[str, str] = {
        data["en"]: em
        for em, data in emoji.EMOJI_DATA.items()
        if "en" in data and data["status"] <= emoji.STATUS["fully_qualified"]
    }
    emoji_regex = "|".join(map(re.escape, sorted(language_pack.values(), key=len, reverse=True)))
    return re.compile(emoji_regex)


def clean_emojis(text: str) -> str:
    """将 emoji 替换为占位符，避免字体渲染异常。"""
    return _build_emoji_regex().sub("�", text)


class QuoteParser:
    """引言语法解析器。"""

    _PATTERN = re.compile(r"^\[([:_-]+)\](.*)")

    @classmethod
    def parse(cls, raw_text: str) -> QuoteParseResult:
        """解析引言文本，输出逐行样式信息与居中标记。

        规则说明：
        - `[spec]text` 形式表示该行有样式控制。
        - `spec == "-"` 时表示分段/留白行。
        - `pure_center` 用于识别“全居中模式”，影响后续排版宽度策略。
        """
        normalized = clean_emojis(from_html_escaped(raw_text))
        lines: list[QuoteLine] = []
        pure_center = True

        # 逐行提取样式标记，未命中的按普通文本行处理。
        for line in normalized.split("\n"):
            stripped = line.strip()
            match = cls._PATTERN.match(stripped)
            if match:
                spec, text_content = match.groups()
                text_content = text_content.strip()
                lines.append(QuoteLine(spec=spec, text=text_content))
                if spec != "-":
                    pure_center &= spec == ":-:" or spec == ":_:"
            else:
                lines.append(QuoteLine(spec=None, text=stripped))

        return QuoteParseResult(lines=lines, pure_center=pure_center)
