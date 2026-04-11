"""卡片渲染模块。

职责：
1. 负责布局测量与图像绘制。
2. 提供若干兼容性静态方法（下载图片、拉取歌曲信息等）。
3. 输出最终 `PIL.Image` 卡片对象。
"""

from __future__ import annotations

import calendar
import logging
from datetime import datetime
from functools import lru_cache
from typing import Any, Dict, Optional, Union

import qrcode
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

from . import assets
from .constants import DEFAULT_USER_AGENT
from .http_client import HttpClient
from .models import CardLayout, CardPayload, CardStyle, LayoutMetrics, Mode, Platform, QuoteParseResult, SongInfo
from .parsing import QuoteParser
from .providers import AppleProvider, NcmProvider, QqProvider, coerce_mode, coerce_platform, get_song_provider
from .services import DailyRecommendationService

logger = logging.getLogger(__name__)


class MusicCard:
    """音乐卡片渲染器。"""

    DAILY = Mode.DAILY.value
    CARD = Mode.CARD.value
    LYRIC = Mode.LYRIC.value
    NOW_PLAYING = Mode.NOW_PLAYING.value

    Regular = 2
    Medium = 5
    Semibold = 8
    Light = 11
    Thin = 14
    Ultralight = 17

    def __init__(
        self,
        font_path: str,
        platform: Union[str, Platform] = Platform.NCM,
        am_storefront: str = "cn",
        style: Optional[CardStyle] = None,
        layout: Optional[CardLayout] = None,
    ):
        """初始化渲染器并缓存布局/样式常量。"""
        self.font_path = font_path
        self.platform = coerce_platform(platform)
        self.am_storefront = am_storefront
        self.style = style or CardStyle()
        self.layout = layout or CardLayout()

        self.W = self.layout.width
        self.MARGIN_TOP = self.layout.margin_top
        self.MARGIN_SIDE = self.layout.margin_side
        self.MARGIN_BOTTOM = self.layout.margin_bottom
        self.CARD_W = self.layout.card_width
        self.INNER_PAD = self.layout.inner_pad
        self.CONTENT_LEFT_X = self.layout.content_left_x
        self.CONTENT_RIGHT_X = self.layout.content_right_x
        self.MAX_TEXT_W = self.layout.max_text_width

        self.C_MAIN = self.style.main
        self.C_SUB = self.style.sub
        self.C_QUOTE = self.style.quote
        self.C_ACCENT = self.style.accent
        self.C_FOOTER_CENTER = self.style.footer_center

    @staticmethod
    async def download_image(url: str) -> Image.Image:
        """下载封面图片，自动应用默认请求头。"""
        headers = {"User-Agent": DEFAULT_USER_AGENT}
        async with HttpClient(headers=headers, timeout=10, trust_env=True) as http_client:
            return await assets.download_image_with_fallback(url, http_client)

    @staticmethod
    async def fetch_ncm_song_info(music_id: str) -> Optional[Dict[str, Any]]:
        """按网易云 ID 查询歌曲信息。"""
        async with HttpClient(timeout=15, trust_env=True) as http_client:
            song = await NcmProvider().fetch_song(music_id, http_client)
            return _song_info_to_dict(song)

    @staticmethod
    async def get_final_url(
        url: str,
        max_redirects: int = 10,
        timeout: int = 30,
    ) -> Optional[str]:
        """获取链接最终跳转地址。"""
        async with HttpClient(timeout=timeout, trust_env=True) as http_client:
            return await http_client.resolve_final_url(url, max_redirects=max_redirects)

    @staticmethod
    async def fetch_qq_music_info(music_id: str) -> Optional[Dict[str, Any]]:
        """按 QQ 音乐 ID 查询歌曲信息。"""
        async with HttpClient(timeout=15, trust_env=True) as http_client:
            song = await QqProvider().fetch_song(music_id, http_client)
            return _song_info_to_dict(song)

    @staticmethod
    async def fetch_apple_music_info(music_id: str, country: str = "cn") -> Optional[Dict[str, Any]]:
        """按 Apple Music ID 查询歌曲信息。"""
        async with HttpClient(timeout=15, trust_env=True) as http_client:
            song = await AppleProvider(country).fetch_song(music_id, http_client)
            return _song_info_to_dict(song)

    @staticmethod
    async def fetch_daily_recommendation(date_str: str) -> Optional[Dict[str, Any]]:
        """按日期获取每日推荐并转换成兼容字典。"""
        async with HttpClient(timeout=10, trust_env=True) as http_client:
            rec = await DailyRecommendationService().fetch(date_str, http_client)
            if not rec:
                return None
            return {
                "music_id": rec.music_id,
                "date": rec.date,
                "username": rec.username,
                "comment": rec.comment,
                "cover_path": rec.cover_path,
            }

    @staticmethod
    def get_dominant_color(image: Image.Image) -> tuple[int, int, int]:
        """通过 1x1 缩放提取主色。"""
        return MusicCard._sample_average_rgb(image)

    @staticmethod
    def _sample_average_rgb(image: Image.Image) -> tuple[int, int, int]:
        """通过 1x1 缩放提取区域平均颜色。"""
        return image.resize((1, 1), resample=Image.Resampling.HAMMING).getpixel((0, 0))

    @staticmethod
    def _blend_rgb(c1: tuple[int, int, int], c2: tuple[int, int, int], ratio: float) -> tuple[int, int, int]:
        """按比例混合两种 RGB 颜色。"""
        return tuple(int(c1[i] * (1 - ratio) + c2[i] * ratio) for i in range(3))

    @staticmethod
    def _adjust_rgb(color: tuple[int, int, int], factor: float) -> tuple[int, int, int]:
        """按倍率调节颜色亮度并限制在合法范围。"""
        return tuple(min(255, max(0, int(channel * factor))) for channel in color)

    @staticmethod
    def _get_perceived_luminance(rgb: tuple[int, int, int]) -> float:
        """计算感知亮度（YIQ 近似）。"""
        return (rgb[0] * 299 + rgb[1] * 587 + rgb[2] * 114) / 1000

    @staticmethod
    def get_adaptive_month_color(bg_sample: Image.Image, theme_rgb: tuple[int, int, int]) -> tuple[int, int, int]:
        """根据背景亮度调整月份文字颜色。"""
        bg_color = MusicCard._sample_average_rgb(bg_sample)
        bg_lum = MusicCard._get_perceived_luminance(bg_color)
        return MusicCard._adjust_rgb(theme_rgb, 0.6) if bg_lum > 140 else MusicCard._adjust_rgb(theme_rgb, 1.8)

    @staticmethod
    def get_adaptive_deco_color(bg_sample: Image.Image, theme_rgb: tuple[int, int, int]) -> tuple[int, int, int]:
        """根据背景亮度调整装饰引号颜色。"""
        bg_color = MusicCard._sample_average_rgb(bg_sample)
        bg_lum = MusicCard._get_perceived_luminance(bg_color)
        white = (255, 255, 255)
        return (
            MusicCard._blend_rgb(theme_rgb, white, 0.2)
            if bg_lum > 150
            else MusicCard._blend_rgb(theme_rgb, white, 0.6)
        )

    @classmethod
    def get_now_playing_progress_colors(
        cls,
        bg_sample: Image.Image,
        theme_rgb: tuple[int, int, int],
    ) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
        """基于二维码配色算法计算进度条颜色（已播放、未播放）。"""
        bg_color = cls._sample_average_rgb(bg_sample)

        # 先对两段颜色都应用二维码配色收敛逻辑。
        white = (255, 255, 255)
        played_seed = cls._blend_rgb(theme_rgb, bg_color, 0.18)
        unplayed_seed = cls._blend_rgb(theme_rgb, white, 0.46)
        played_color = cls.get_safe_qr_color(played_seed, bg_color)
        unplayed_color = cls.get_safe_qr_color(unplayed_seed, bg_color)

        # 再拉开亮度差：已播放更深，未播放更浅。
        played_color = cls._adjust_rgb(played_color, 0.78)
        unplayed_color = cls._adjust_rgb(unplayed_color, 1.24)

        # 若层次仍不足，继续分离亮度，确保视觉层次明显。
        for _ in range(3):
            if cls._get_contrast_ratio(played_color, unplayed_color) >= 1.35:
                break
            played_color = cls._adjust_rgb(played_color, 0.90)
            unplayed_color = cls._adjust_rgb(unplayed_color, 1.08)

        # 保证已播放整体不比未播放更亮。
        if cls._get_relative_luminance(played_color) > cls._get_relative_luminance(unplayed_color):
            played_color, unplayed_color = unplayed_color, played_color

        return played_color, unplayed_color

    @staticmethod
    def get_contrasting_text_color(region_image: Image.Image) -> str:
        """为指定区域选择高对比文本颜色。"""
        color = MusicCard._sample_average_rgb(region_image)
        lum = MusicCard._get_perceived_luminance(color)
        return "#4a3b32" if lum > 120 else "#f2f2f2"

    @staticmethod
    def _get_relative_luminance(rgb: tuple[int, int, int]) -> float:
        """计算 sRGB 相对亮度（WCAG 公式）。"""
        r, g, b = [x / 255.0 for x in rgb]
        r = r / 12.92 if r <= 0.03928 else ((r + 0.055) / 1.055) ** 2.4
        g = g / 12.92 if g <= 0.03928 else ((g + 0.055) / 1.055) ** 2.4
        b = b / 12.92 if b <= 0.03928 else ((b + 0.055) / 1.055) ** 2.4
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    @classmethod
    def _get_contrast_ratio(cls, rgb1: tuple[int, int, int], rgb2: tuple[int, int, int]) -> float:
        """计算两种颜色的对比度。"""
        lum1 = cls._get_relative_luminance(rgb1)
        lum2 = cls._get_relative_luminance(rgb2)
        if lum1 > lum2:
            return (lum1 + 0.05) / (lum2 + 0.05)
        return (lum2 + 0.05) / (lum1 + 0.05)

    @classmethod
    def get_safe_qr_color(
        cls,
        theme_rgb: tuple[int, int, int],
        bg_color_rgb: tuple[int, int, int] = (253, 253, 253),
    ) -> tuple[int, int, int]:
        """收敛得到满足可读性要求的二维码颜色。"""
        min_contrast_ratio = 4.5
        current_color = list(theme_rgb)

        # 若对比不足，逐步压暗主题色直到达到阈值。
        while cls._get_contrast_ratio(tuple(current_color), bg_color_rgb) < min_contrast_ratio:
            if current_color[0] <= 5 and current_color[1] <= 5 and current_color[2] <= 5:
                return 0, 0, 0
            current_color[0] = max(0, int(current_color[0] * 0.9))
            current_color[1] = max(0, int(current_color[1] * 0.9))
            current_color[2] = max(0, int(current_color[2] * 0.9))

        return tuple(current_color)

    @staticmethod
    def generate_styled_qrcode(data: str, theme_color: tuple[int, int, int], size: int = 120) -> Image.Image:
        """生成带主题色与透明背景的二维码。"""
        qr = qrcode.QRCode(version=1, border=1, box_size=10)
        qr.add_data(data)
        qr.make(fit=True)
        qr_img = qr.make_image(fill_color="black", back_color="white").convert("RGBA")

        new_data = []
        tr, tg, tb = theme_color
        for item in qr_img.getdata():
            if item[0] < 128:
                new_data.append((tr, tg, tb, 230))
            else:
                new_data.append((255, 255, 255, 0))

        qr_img.putdata(new_data)
        return qr_img.resize((size, size), Image.Resampling.LANCZOS)

    @staticmethod
    def create_gradient_mask(w: int, h: int) -> Image.Image:
        """创建纵向渐变蒙版，用于卡片内部磨砂效果。"""
        ending = 0.9
        limit = 255 * ending
        break_percent = 0.5
        break_opa = limit * break_percent
        break_h = min(1100, h * break_percent * ending)
        exponent = 1.5

        data: list[int] = []
        # 上半段线性过渡，下半段指数过渡，避免层次突兀。
        for y in range(h):
            if y < break_h:
                val = int(break_opa * (y / break_h)) if break_h > 0 else 0
            else:
                denom = h - break_h
                ratio = (y - break_h) / denom if denom > 0 else 1
                ratio = ratio ** exponent
                val = int(break_opa + (limit - break_opa) * ratio)
            data.append(val)

        gradient = Image.new("L", (1, h))
        gradient.putdata(data)
        return gradient.resize((w, h))

    @staticmethod
    def create_rounded_mask(size: tuple[int, int], radius: int) -> Image.Image:
        """创建高质量圆角蒙版。"""
        upscale_factor = 8
        scaled_size = (size[0] * upscale_factor, size[1] * upscale_factor)
        scaled_radius = radius * upscale_factor

        mask = Image.new("L", scaled_size, 0)
        draw = ImageDraw.Draw(mask)
        draw.rounded_rectangle((0, 0) + scaled_size, radius=scaled_radius, fill=255)
        return mask.resize(size, Image.Resampling.LANCZOS)

    @staticmethod
    def contains_cjk(text: str) -> bool:
        """判断文本是否包含 CJK 字符。"""
        for char in text:
            if "\u4e00" <= char <= "\u9fff":
                return True
        return False

    def _process_text_wrapping(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        font: ImageFont.FreeTypeFont,
        max_width: float,
    ) -> tuple[list[str], int]:
        """按给定宽度对文本进行自动换行。"""
        final_lines: list[str] = []
        bbox = font.getbbox("高")
        line_height = bbox[3] - bbox[1]

        for paragraph in text.split("\n"):
            paragraph = paragraph.strip()
            if not paragraph:
                final_lines.append("")
                continue

            # CJK 文本按字符粒度换行，确保中英文混排时不丢字。
            if self.contains_cjk(paragraph):
                current_line = ""
                for char in paragraph:
                    if draw.textlength(current_line + char, font=font) <= max_width:
                        current_line += char
                    else:
                        if " " in current_line:
                            final_lines.append(current_line[: current_line.rindex(" ")])
                            current_line = current_line[current_line.rindex(" ") + 1 :]
                            current_line += char
                        else:
                            final_lines.append(current_line)
                            current_line = char
                if current_line:
                    final_lines.append(current_line)
            else:
                words = paragraph.split(" ")
                current_line = ""
                for word in words:
                    word_width = draw.textlength(word, font=font)
                    if word_width > max_width:
                        if current_line:
                            final_lines.append(current_line)
                            current_line = ""

                        # 超长英文单词按字符拆分并添加连字符，避免溢出。
                        hyphen_width = draw.textlength("-", font=font)
                        effective_max_width = max_width - hyphen_width
                        temp_chunk = ""
                        for char in word:
                            if draw.textlength(temp_chunk + char, font=font) <= effective_max_width:
                                temp_chunk += char
                            else:
                                final_lines.append(temp_chunk + "-")
                                temp_chunk = char
                        if temp_chunk:
                            current_line = temp_chunk
                        continue

                    separator = " " if current_line else ""
                    test_line = current_line + separator + word
                    if draw.textlength(test_line, font=font) <= max_width:
                        current_line = test_line
                    else:
                        final_lines.append(current_line)
                        current_line = word

                if current_line:
                    final_lines.append(current_line)

        return final_lines, line_height

    @staticmethod
    def _draw_text_right(
        draw: ImageDraw.ImageDraw,
        text: str,
        font: ImageFont.FreeTypeFont,
        right_x: float,
        y: float,
        fill: str,
    ) -> None:
        """按右边界坐标绘制文本。"""
        w = draw.textlength(text, font=font)
        draw.text((right_x - w, y), text, font=font, fill=fill)

    @staticmethod
    @lru_cache(maxsize=256)
    def _cached_font(font_path: str, size: int, index: int) -> ImageFont.FreeTypeFont:
        """缓存字体对象，减少重复加载。"""
        return ImageFont.truetype(font_path, size, index=index)

    def _load_font(self, size: int, index: int) -> ImageFont.FreeTypeFont:
        """加载指定字号与字重索引的字体。"""
        return self._cached_font(self.font_path, size, index)

    def _prepare_fonts(self) -> Dict[str, ImageFont.FreeTypeFont]:
        """准备渲染阶段使用的字体集合。"""
        return {
            "title": self._load_font(44, self.Semibold),
            "artist": self._load_font(26, self.Semibold),
            "date_num": self._load_font(90, self.Medium),
            "date_month": self._load_font(40, self.Medium),
            "quote": self._load_font(34, self.Regular),
            "quote_sub": self._load_font(26, self.Light),
            "deco": self._load_font(100, self.Medium),
            "footer_center": self._load_font(32, self.Thin),
            "footer_outer": self._load_font(22, self.Regular),
            "progress_time": self._load_font(28, self.Semibold),
        }

    def _measure_quote_height(
        self,
        draw: ImageDraw.ImageDraw,
        quote_parse: QuoteParseResult,
        q_max_w: float,
        font_quote: ImageFont.FreeTypeFont,
        font_quote_small: ImageFont.FreeTypeFont,
    ) -> float:
        """测量引言区域真实高度。"""
        q_h_real = 0.0
        q_font_h = font_quote.getbbox("高")[3] - font_quote.getbbox("高")[1]
        small_q_font_h = (
            font_quote_small.getbbox("高")[3] - font_quote_small.getbbox("高")[1]
        )

        for quote_line in quote_parse.lines:
            spec = quote_line.spec
            text_content = quote_line.text

            if spec:
                if spec == "-":
                    if not text_content.strip():
                        q_h_real += q_font_h * 0.25
                    else:
                        q_h_real += q_font_h * 0.5 + q_font_h / 1.5
                else:
                    use_small_font = "_" in spec
                    target_font = font_quote_small if use_small_font else font_quote
                    target_font_h = small_q_font_h if use_small_font else q_font_h
                    wrap_width = q_max_w * (1 if quote_parse.pure_center else 0.8)
                    wrapped_sub_lines, _ = self._process_text_wrapping(
                        draw,
                        text_content.strip(),
                        target_font,
                        wrap_width,
                    )
                    q_h_real += len(wrapped_sub_lines) * target_font_h * 1.6
            else:
                if not text_content.strip():
                    q_h_real += q_font_h * 1.6
                    continue
                wrapped_sub_lines, _ = self._process_text_wrapping(draw, text_content, font_quote, q_max_w)
                q_h_real += len(wrapped_sub_lines) * q_font_h * 1.6

        return q_h_real

    def measure_layout(
        self,
        payload: CardPayload,
        mode: Mode,
        fonts: Dict[str, ImageFont.FreeTypeFont],
        quote_parse: QuoteParseResult,
        show_qrcode: bool,
    ) -> LayoutMetrics:
        """测量整张卡片的布局参数。"""
        qr_size = 120
        qr_gap = 20

        text_w = self.MAX_TEXT_W
        if show_qrcode and payload.music_id:
            text_w -= qr_size + qr_gap

        temp_draw = ImageDraw.Draw(Image.new("RGB", (1, 1)))
        t_lines, t_h = self._process_text_wrapping(temp_draw, payload.title, fonts["title"], text_w)
        a_lines, a_h = self._process_text_wrapping(temp_draw, payload.artist, fonts["artist"], text_w)

        text_block_h = (len(t_lines) * t_h * 1.3) + 15 + (len(a_lines) * a_h * 1.5)
        header_h_real = max(text_block_h, qr_size) if (show_qrcode and payload.music_id) else text_block_h

        q_max_w = 0.0
        q_x = 0.0
        footer_inner_h = 0.0
        header_section_h = 0.0
        middle_h = 0.0

        # card / now-playing 模式不渲染中部引言区域，daily/lyric 模式会保留中部区域。
        if mode == Mode.CARD:
            header_section_h = header_h_real
            middle_h = 0
            footer_inner_h = 60
        elif mode == Mode.NOW_PLAYING:
            header_section_h = header_h_real
            middle_h = 0
            # 为进度条与时间标签预留空间。
            footer_inner_h = 170
        else:
            header_section_h = header_h_real + 30 + 4 + 40
            q_x = self.CONTENT_LEFT_X + (240 if mode == Mode.DAILY else 0)
            q_max_w = self.CONTENT_RIGHT_X - q_x

            font_quote_small = self._load_font(int(fonts["quote"].size * 0.8), self.Regular)
            q_h_real = self._measure_quote_height(
                temp_draw,
                quote_parse,
                q_max_w,
                fonts["quote"],
                font_quote_small,
            )

            if mode == Mode.DAILY:
                q_h = q_h_real + 40 + 30
                footer_inner_h = 20 + 20 + 32 + 25
            else:
                q_h = q_h_real + 30
                footer_inner_h = 0

            middle_h = max(200, q_h)

        cover_size = self.MAX_TEXT_W
        total_card_h = self.INNER_PAD + cover_size + header_section_h + footer_inner_h
        if mode in {Mode.DAILY, Mode.LYRIC}:
            total_card_h += 30 + middle_h
        total_img_h = int(total_card_h + self.MARGIN_TOP + self.MARGIN_BOTTOM)

        return LayoutMetrics(
            text_w=text_w,
            t_lines=t_lines,
            a_lines=a_lines,
            t_h=t_h,
            a_h=a_h,
            header_h_real=header_h_real,
            header_section_h=header_section_h,
            middle_h=middle_h,
            footer_inner_h=footer_inner_h,
            q_x=q_x,
            q_max_w=q_max_w,
            total_card_h=total_card_h,
            total_img_h=total_img_h,
            cover_size=cover_size,
            qr_size=qr_size,
        )

    def render_background(
        self,
        cover_img_raw: Image.Image,
        metrics: LayoutMetrics,
        inner_blurred: bool,
    ) -> tuple[Image.Image, ImageDraw.ImageDraw]:
        """绘制背景层、卡片容器与封面图。"""
        bg_img = ImageOps.fit(
            cover_img_raw,
            (self.W, metrics.total_img_h),
            method=Image.Resampling.LANCZOS,
        )
        bg_img = bg_img.filter(ImageFilter.GaussianBlur(radius=100))
        bg_img = ImageEnhance.Brightness(bg_img).enhance(0.7)

        if inner_blurred:
            card_crop = bg_img.crop(
                (
                    self.MARGIN_SIDE,
                    self.MARGIN_TOP,
                    self.MARGIN_SIDE + self.CARD_W,
                    self.MARGIN_TOP + metrics.total_card_h,
                )
            )
            card_crop = ImageEnhance.Brightness(card_crop).enhance(1.2)
            white_layer = Image.new("RGB", card_crop.size, "#FDFDFD")
            mask = self.create_gradient_mask(card_crop.width, card_crop.height)
            card_bg = Image.composite(white_layer, card_crop, mask)
        else:
            card_bg = Image.new("RGB", (self.CARD_W, int(metrics.total_card_h)), "#FDFDFD")

        card_mask = self.create_rounded_mask(card_bg.size, 40)
        bg_img.paste(card_bg, (self.MARGIN_SIDE, self.MARGIN_TOP), card_mask)

        cover_resized = ImageOps.fit(
            cover_img_raw,
            (metrics.cover_size, metrics.cover_size),
            method=Image.Resampling.LANCZOS,
        )
        bg_img.paste(
            cover_resized,
            (self.CONTENT_LEFT_X, self.MARGIN_TOP + self.INNER_PAD),
            self.create_rounded_mask(cover_resized.size, 30),
        )

        return bg_img, ImageDraw.Draw(bg_img)

    def _build_song_url(self, payload: CardPayload) -> str:
        """构建歌曲链接，优先使用 payload 自带链接。"""
        if payload.song_url:
            return payload.song_url
        if not payload.music_id:
            return ""
        provider = get_song_provider(self.platform, self.am_storefront)
        return provider.build_song_url(payload.music_id, storefront=self.am_storefront)

    def render_header(
        self,
        draw: ImageDraw.ImageDraw,
        bg_img: Image.Image,
        payload: CardPayload,
        metrics: LayoutMetrics,
        fonts: Dict[str, ImageFont.FreeTypeFont],
        show_qrcode: bool,
        theme_rgb: tuple[int, int, int],
    ) -> float:
        """绘制标题、歌手以及可选二维码区域。"""
        cursor_y = self.MARGIN_TOP + self.INNER_PAD + metrics.cover_size + 30
        header_start_y = cursor_y

        for line in metrics.t_lines:
            draw.text((self.CONTENT_LEFT_X, cursor_y), line, font=fonts["title"], fill=self.C_MAIN)
            cursor_y += metrics.t_h * 1.3
        cursor_y += 15

        for line in metrics.a_lines:
            draw.text((self.CONTENT_LEFT_X, cursor_y), line, font=fonts["artist"], fill=self.C_SUB)
            cursor_y += metrics.a_h * 1.5

        if show_qrcode and payload.music_id:
            song_url = self._build_song_url(payload)
            qr_x = int(self.CONTENT_RIGHT_X - metrics.qr_size)
            qr_y = int(header_start_y)
            qr_region_box = (qr_x, qr_y, qr_x + metrics.qr_size, qr_y + metrics.qr_size)
            qr_background_sample = bg_img.crop(qr_region_box)
            avg_bg_color = self.get_dominant_color(qr_background_sample)
            # 二维码颜色会按背景对比度自动收敛，避免扫码困难。
            safe_qr_color = self.get_safe_qr_color(theme_rgb, avg_bg_color)
            qr_img = self.generate_styled_qrcode(song_url, safe_qr_color, size=metrics.qr_size)
            bg_img.paste(qr_img, (qr_x, qr_y), qr_img)

        return header_start_y

    @staticmethod
    def format_duration_ms(milliseconds: int) -> str:
        """将毫秒时长格式化为 `m:ss` 或 `h:mm:ss`。"""
        total_seconds = max(0, int(milliseconds)) // 1000
        hours, remain = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remain, 60)
        if hours > 0:
            return f"{hours}:{minutes:02d}:{seconds:02d}"
        return f"{minutes}:{seconds:02d}"

    def render_now_playing_progress(
        self,
        draw: ImageDraw.ImageDraw,
        bg_img: Image.Image,
        payload: CardPayload,
        metrics: LayoutMetrics,
        fonts: Dict[str, ImageFont.FreeTypeFont],
        header_start_y: float,
        theme_rgb: tuple[int, int, int],
    ) -> None:
        """绘制 now-playing 的进度条与时长文本。"""
        duration_ms = payload.duration_ms or 0
        progress_ms = payload.progress_ms or 0
        if duration_ms <= 0:
            return

        clamped_progress = max(0, min(progress_ms, duration_ms))
        ratio = clamped_progress / duration_ms

        bar_left = self.CONTENT_LEFT_X
        bar_right = self.CONTENT_RIGHT_X
        bar_width = bar_right - bar_left
        bar_height = 14
        bar_top = header_start_y + metrics.header_h_real + 34
        bar_bottom = bar_top + bar_height
        radius = bar_height // 2
        bar_sample = bg_img.crop((int(bar_left), int(bar_top), int(bar_right), int(bar_bottom)))
        played_color, unplayed_color = self.get_now_playing_progress_colors(bar_sample, theme_rgb)

        draw.rounded_rectangle(
            (bar_left, bar_top, bar_right, bar_bottom),
            radius=radius,
            fill=unplayed_color,
        )

        if ratio > 0:
            played_right = bar_left + (bar_width * ratio)
            draw.rounded_rectangle(
                (bar_left, bar_top, played_right, bar_bottom),
                radius=radius,
                fill=played_color,
            )

        time_y = bar_bottom + 14
        draw.text(
            (bar_left, time_y),
            self.format_duration_ms(clamped_progress),
            font=fonts["progress_time"],
            fill=self.C_SUB,
        )
        self._draw_text_right(
            draw,
            self.format_duration_ms(duration_ms),
            fonts["progress_time"],
            bar_right,
            time_y,
            self.C_SUB,
        )

    def _draw_dot_line(self, draw: ImageDraw.ImageDraw, y: float) -> None:
        """绘制横向点状分割线。"""
        for x in range(self.CONTENT_LEFT_X, self.CONTENT_RIGHT_X, 20):
            draw.ellipse((x, y, x + 4, y + 4), fill=self.C_ACCENT)

    def render_quote(
        self,
        draw: ImageDraw.ImageDraw,
        bg_img: Image.Image,
        payload: CardPayload,
        metrics: LayoutMetrics,
        fonts: Dict[str, ImageFont.FreeTypeFont],
        quote_parse: QuoteParseResult,
        mode: Mode,
        theme_rgb: tuple[int, int, int],
        header_start_y: float,
    ) -> float:
        """绘制中部引言区域，并返回底部分隔线坐标。"""
        sep_y = header_start_y + metrics.header_h_real
        if mode in {Mode.CARD, Mode.NOW_PLAYING}:
            return sep_y

        sep_y = header_start_y + metrics.header_h_real + 30
        self._draw_dot_line(draw, sep_y)
        mid_y = sep_y + 40

        date_month_str = calendar.month_abbr[payload.date_obj.month]
        date_day_int = payload.date_obj.day

        if mode == Mode.DAILY:
            date_x = self.CONTENT_LEFT_X + 20
            month_color = self.get_adaptive_month_color(
                bg_img.crop((date_x, mid_y, date_x + 80, mid_y + 40)),
                theme_rgb,
            )
            draw.text((date_x, mid_y), date_month_str, font=fonts["date_month"], fill=month_color)
            m_bbox = fonts["date_month"].getbbox("A")
            draw.text(
                (date_x, mid_y + (m_bbox[3] - m_bbox[1]) + 10),
                str(date_day_int),
                font=fonts["date_num"],
                fill=self.C_MAIN,
            )

        deco_x = self.CONTENT_RIGHT_X - 80
        deco_y = metrics.total_img_h - 260 if mode == Mode.DAILY else metrics.total_img_h - 180
        deco_color = self.get_adaptive_deco_color(
            bg_img.crop((int(deco_x), int(deco_y), int(deco_x + 60), int(deco_y + 60))),
            theme_rgb,
        )
        draw.text((deco_x, deco_y), "”", font=fonts["deco"], fill=deco_color)

        q_curr_y = mid_y + 5
        q_font_h = fonts["quote"].getbbox("高")[3] - fonts["quote"].getbbox("高")[1]

        # 逐行应用引言语法，支持分段、对齐和小号文字。
        for quote_line in quote_parse.lines:
            spec = quote_line.spec
            text_content = quote_line.text

            if spec:
                if spec == "-":
                    padding_v = q_font_h * 0.25
                    if not text_content:
                        q_curr_y += padding_v
                    else:
                        # 分段标题行：中间文字 + 两侧虚点线。
                        div_font_size = max(8, int(fonts["quote"].size / 1.5))
                        div_font = self._load_font(div_font_size, self.Regular)
                        div_text_w = draw.textlength(text_content, font=div_font)
                        div_bbox = div_font.getbbox("Hg")
                        div_text_h = div_bbox[3] - div_bbox[1]

                        div_total_w = metrics.q_max_w * 0.5
                        center_x = metrics.q_x + metrics.q_max_w / 2
                        area_start_x = center_x - div_total_w * 0.75
                        area_end_x = center_x + div_total_w * 0.75
                        text_x = center_x - div_text_w / 2
                        text_gap = 8
                        div_mid_y = q_curr_y + padding_v + (div_text_h / 2)

                        left_line_end = text_x - text_gap
                        if left_line_end > area_start_x:
                            for lx in range(int(area_start_x), int(left_line_end), 4):
                                draw.point((lx, div_mid_y), fill=self.C_QUOTE)

                        draw.text(
                            (text_x, div_mid_y),
                            text_content,
                            font=div_font,
                            fill=self.C_QUOTE,
                            anchor="lm",
                        )

                        right_line_start = text_x + div_text_w + text_gap
                        if right_line_start < area_end_x:
                            for lx in range(int(right_line_start), int(area_end_x), 4):
                                draw.point((lx, div_mid_y), fill=self.C_QUOTE)

                        q_curr_y += padding_v + div_text_h + padding_v
                else:
                    use_small_font = "_" in spec
                    font_size = int(fonts["quote"].size * 0.8) if use_small_font else fonts["quote"].size
                    target_font = self._load_font(font_size, self.Regular) if use_small_font else fonts["quote"]
                    target_font_h = target_font.getbbox("高")[3] - target_font.getbbox("高")[1]

                    norm_spec = spec.replace("_", "-")
                    align = "left"
                    if ":-:" in norm_spec:
                        align = "center"
                    elif "-:" in norm_spec:
                        align = "right"

                    wrap_width = metrics.q_max_w * (1 if quote_parse.pure_center else 0.8)
                    margin_w = metrics.q_max_w * 0.1
                    wrapped_sub_lines, _ = self._process_text_wrapping(draw, text_content, target_font, wrap_width)

                    for sub_line in wrapped_sub_lines:
                        sub_line_width = draw.textlength(sub_line, font=target_font)
                        x_pos = metrics.q_x
                        if align == "center":
                            x_pos = (
                                metrics.q_x
                                + margin_w * int(not quote_parse.pure_center)
                                + (wrap_width - sub_line_width) / 2
                            )
                        elif align == "right":
                            x_pos = (metrics.q_x + metrics.q_max_w) - sub_line_width

                        draw.text((x_pos, q_curr_y), sub_line, font=target_font, fill=self.C_QUOTE)
                        q_curr_y += target_font_h * 1.6
            else:
                if not text_content:
                    q_curr_y += q_font_h * 1.6
                    continue

                wrapped_sub_lines, _ = self._process_text_wrapping(
                    draw,
                    text_content,
                    fonts["quote"],
                    metrics.q_max_w,
                )
                for sub_line in wrapped_sub_lines:
                    draw.text((metrics.q_x, q_curr_y), sub_line, font=fonts["quote"], fill=self.C_QUOTE)
                    q_curr_y += q_font_h * 1.6

        if mode == Mode.DAILY:
            self._draw_text_right(
                draw,
                "--来自 @" + payload.quote_source + " 的评论",
                fonts["quote_sub"],
                self.CONTENT_RIGHT_X,
                q_curr_y + 20,
                self.C_SUB,
            )

            bot_sep_y = mid_y + metrics.middle_h + 10
            self._draw_dot_line(draw, bot_sep_y)

            foot_base_y = bot_sep_y
            foot_y = foot_base_y + 24
            ct = "AMLL 亲友团 | 今日推荐"
            ct_bbox = draw.textbbox((0, 0), ct, font=fonts["footer_center"])
            draw.text(
                (self.MARGIN_SIDE + (self.CARD_W - (ct_bbox[2] - ct_bbox[0])) / 2, foot_y),
                ct,
                font=fonts["footer_center"],
                fill=self.C_FOOTER_CENTER,
            )
            return foot_base_y

        return sep_y

    def render_footer(
        self,
        draw: ImageDraw.ImageDraw,
        bg_img: Image.Image,
        metrics: LayoutMetrics,
        fonts: Dict[str, ImageFont.FreeTypeFont],
    ) -> None:
        """绘制卡片外部水印信息。"""
        outer_y = self.MARGIN_TOP + metrics.total_card_h + 20
        outer_right_x = self.MARGIN_SIDE + self.CARD_W
        outer_color = self.get_contrasting_text_color(
            bg_img.crop((self.W - 300, metrics.total_img_h - 80, self.W, metrics.total_img_h))
        )

        self._draw_text_right(
            draw,
            self.style.watermark_designed,
            fonts["footer_outer"],
            outer_right_x,
            outer_y,
            outer_color,
        )
        self._draw_text_right(
            draw,
            self.style.watermark_generated,
            fonts["footer_outer"],
            outer_right_x,
            outer_y + 30,
            outer_color,
        )

    async def generate(
        self,
        data: Union[CardPayload, Dict[str, Any]],
        inner_blurred: bool = False,
        show_qrcode: bool = False,
        mode: Union[str, Mode] = Mode.DAILY,
        http_client: Optional[HttpClient] = None,
    ) -> Image.Image:
        """执行完整渲染流程并返回最终图像。"""
        payload = _payload_from_any(data)
        safe_mode = coerce_mode(mode)
        effective_show_qrcode = show_qrcode and safe_mode != Mode.NOW_PLAYING

        created_client = False
        client = http_client
        if client is None:
            created_client = True
            client = HttpClient(headers={"User-Agent": DEFAULT_USER_AGENT}, timeout=10, trust_env=True)
            await client.__aenter__()

        try:
            logger.info("下载封面: %s", payload.cover_url)
            cover_img_raw = (await assets.download_image_with_fallback(payload.cover_url, client)).convert("RGB")
            theme_rgb = self.get_dominant_color(cover_img_raw)
            logger.info("识别主题色: %s", theme_rgb)

            try:
                fonts = self._prepare_fonts()
            except IOError:
                logger.error("字体加载失败: %s", self.font_path)
                return Image.new("RGB", (100, 100), color="red")

            quote_parse = QuoteParser.parse(payload.quote_content)
            metrics = self.measure_layout(payload, safe_mode, fonts, quote_parse, effective_show_qrcode)
            bg_img, draw = self.render_background(cover_img_raw, metrics, inner_blurred)
            header_start_y = self.render_header(
                draw,
                bg_img,
                payload,
                metrics,
                fonts,
                effective_show_qrcode,
                theme_rgb,
            )
            if safe_mode == Mode.NOW_PLAYING:
                self.render_now_playing_progress(draw, bg_img, payload, metrics, fonts, header_start_y, theme_rgb)
            else:
                self.render_quote(
                    draw,
                    bg_img,
                    payload,
                    metrics,
                    fonts,
                    quote_parse,
                    safe_mode,
                    theme_rgb,
                    header_start_y,
                )
            self.render_footer(draw, bg_img, metrics, fonts)
            return bg_img
        finally:
            if created_client:
                await client.__aexit__(None, None, None)


def _song_info_to_dict(song: Optional[SongInfo]) -> Optional[Dict[str, Any]]:
    """将 `SongInfo` 转换为兼容字典结构。"""
    if song is None:
        return None
    payload = {
        "title": song.title,
        "artist": song.artist,
        "cover_url": song.cover_url,
        "music_id": song.music_id,
    }
    if song.song_url:
        payload["song_url"] = song.song_url
    return payload


def _payload_from_any(data: Union[CardPayload, Dict[str, Any]]) -> CardPayload:
    """将字典/对象统一转换为 `CardPayload`。"""
    if isinstance(data, CardPayload):
        return data

    def pick(*keys: str) -> Any:
        for key in keys:
            if key in data:
                return data[key]
        return None

    return CardPayload(
        title=pick("title", "track") or "",
        artist=pick("artist") or "",
        cover_url=pick("cover_url", "coverUrl") or "",
        quote_content=data.get("quote_content", ""),
        quote_source=data.get("quote_source", ""),
        date_obj=data.get("date_obj", datetime.now()),
        music_id=data.get("music_id"),
        song_url=pick("song_url", "url"),
        progress_ms=_coerce_optional_int(pick("progress_ms", "progress")),
        duration_ms=_coerce_optional_int(pick("duration_ms", "duration")),
    )


def _coerce_optional_int(value: Any) -> Optional[int]:
    """将可选值转换为 int，失败时返回 None。"""
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
