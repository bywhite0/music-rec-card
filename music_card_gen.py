import argparse
import asyncio
import calendar
import json
import re
import sys
from datetime import datetime
from io import BytesIO
from typing import Optional, Dict, Any
from xml.dom.minidom import parseString, Document
from ttml.ttml import TTML

import aiohttp
import qrcode
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageOps, ImageEnhance


class MusicCard:
    DAILY = "daily"
    CARD = "card"
    LYRIC = "lyric"

    def __init__(self, font_path: str, platform: str = "ncm"):
        self.font_path = font_path
        self.platform = platform

        # 布局常量
        self.W = 1000
        self.MARGIN_TOP = 40
        self.MARGIN_SIDE = 40
        self.MARGIN_BOTTOM = 90
        self.CARD_W = self.W - (self.MARGIN_SIDE * 2)
        self.INNER_PAD = 40
        self.CONTENT_LEFT_X = self.MARGIN_SIDE + self.INNER_PAD
        self.CONTENT_RIGHT_X = self.MARGIN_SIDE + self.CARD_W - self.INNER_PAD
        self.MAX_TEXT_W = self.CARD_W - (self.INNER_PAD * 2)

        # 颜色常量
        self.C_MAIN = "#2A2A2A"
        self.C_SUB = "#555555"
        self.C_QUOTE = "#4A4A4A"
        self.C_ACCENT = "#D0D0D0"
        self.C_FOOTER_CENTER = "#555555"

    # --- 数据获取 ---

    @staticmethod
    async def download_image(url: str) -> Image.Image:
        """[异步] 下载图片"""
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36 Edg/142.0.0.0",
        }
        if not url:
            return Image.new('RGB', (600, 600), color='#D3D3D3')
        try:
            async with aiohttp.ClientSession(headers=headers, trust_env=True) as session:
                async with session.get(url, timeout=10) as resp:
                    if resp.status != 200:
                        print(f"图片下载失败: {resp.status}，尝试使用 TLS 指纹...")
                        try:
                            from curl_cffi import requests
                            response = requests.get(
                                url,
                                impersonate="chrome110"
                            )
                            return Image.open(BytesIO(response.content))
                        except Exception as e:
                            print(f"图片下载出错: {e}")
                        return Image.new('RGB', (600, 600), color='#D3D3D3')
                    content = await resp.read()
                    return Image.open(BytesIO(content))
        except Exception as e:
            print(f"图片下载出错: {e}")
            return Image.new('RGB', (600, 600), color='#D3D3D3')

    @staticmethod
    async def fetch_ncm_song_info(music_id: str) -> Optional[Dict]:
        """[异步] 获取网易云音乐歌曲详情"""
        url = f"https://music.163.com/api/song/detail/?id={music_id}&ids=%5B{music_id}%5D"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"}
        print(f"正在从 NCM API 获取 ID {music_id} 的信息...")
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers) as resp:
                    if resp.status != 200: return None
                    text_resp = await resp.text()
                    data = json.loads(text_resp)
                    if not data.get('songs'): return None
                    song = data['songs'][0]
                    return {
                        "title": song['name'],
                        "artist": " / ".join([a['name'] for a in song['artists']]),
                        "cover_url": song['album']['picUrl'],
                        "music_id": music_id
                    }
        except Exception as e:
            print(f"NCM API Error: {e}")
            return None

    @staticmethod
    async def fetch_qq_music_info(music_id: str, cookie: str) -> Optional[Dict]:
        """[异步] 获取 QQ 音乐歌曲详情"""
        url = f"https://y.qq.com/n/ryqq_v2/songDetail/{music_id}"
        headers = {
            "Accept": "*/*",
            "Connection": "keep-alive",
            "Cookie": cookie,
            "User-Agent": """Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"""
        }
        print(f"正在访问 QQ 音乐网页端获取 ID {music_id} 的信息...")
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers) as resp:
                    if resp.status != 200: return None
                    text_resp = await resp.text()
                    begin_index = text_resp.find("""window.__INITIAL_DATA__ =""") + 25
                    end_index = text_resp.find("""</script>""", begin_index)
                    if begin_index == 24 or end_index == -1 or end_index <= begin_index: return None
                    data = json.loads(text_resp[begin_index: end_index].replace('undefined', 'null'))
                    if not data.get('detail'): return None
                    song = data['songList'][0]
                    return {
                        "title": f"{song['title']} ({song['subtitle']})" if song['subtitle'] and len(
                            song['subtitle']) else song['title'],
                        "artist": " / ".join([singer['name'] for singer in song['singer']]),
                        "cover_url": f"https://y.qq.com/music/photo_new/T002R1200x1200M000{song['album']['mid']}.jpg",
                        "music_id": music_id
                    }
        except Exception as e:
            print(f"NCM API Error: {e}")
            return None

    @staticmethod
    async def fetch_daily_recommendation(date_str: str) -> Optional[Dict]:
        """
        [异步] 获取每日推荐 (API Priority 1)
        URL: https://amlldb.bikonoo.com/api/daily-recommendations?date={date}
        """
        url = f"https://amlldb.bikonoo.com/api/daily-recommendations?date={date_str}"
        print(f"正在获取 {date_str} 的每日推荐...")
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        print(f"Error: {resp.status}")
                        return None

                    # API 可能返回 "null" 或 JSON 对象
                    text_resp = await resp.text()
                    if not text_resp or text_resp.strip() == "null":
                        print("该日期无每日推荐数据")
                        return None

                    data = json.loads(text_resp)
                    # 校验返回数据是否有效
                    if not isinstance(data, dict) or 'ncm_id' not in data:
                        return None

                    return {
                        "music_id": data.get('ncm_id'),
                        "date": data.get('date'),  # "2025-12-17"
                        "username": data.get('username'),  # -> quote_source
                        "comment": data.get('comment'),  # -> quote_content
                        "cover_path": data.get('cover')  # -> cover_url
                    }
        except Exception as e:
            print(f"Error: {e}")
            return None

    # --- 图像处理算法 ---

    @staticmethod
    def get_dominant_color(image: Image.Image):
        return image.copy().resize((1, 1), resample=Image.Resampling.HAMMING).getpixel((0, 0))

    @staticmethod
    def get_adaptive_month_color(bg_sample, theme_rgb):
        bg_color = bg_sample.resize((1, 1), resample=Image.Resampling.HAMMING).getpixel((0, 0))
        bg_lum = (bg_color[0] * 299 + bg_color[1] * 587 + bg_color[2] * 114) / 1000

        def adjust(c, factor):
            return tuple(min(255, max(0, int(i * factor))) for i in c)

        return adjust(theme_rgb, 0.6) if bg_lum > 140 else adjust(theme_rgb, 1.8)

    @staticmethod
    def get_adaptive_deco_color(bg_sample, theme_rgb):
        bg_color = bg_sample.resize((1, 1), resample=Image.Resampling.HAMMING).getpixel((0, 0))
        bg_lum = (bg_color[0] * 299 + bg_color[1] * 587 + bg_color[2] * 114) / 1000

        def blend(c1, c2, ratio):
            return tuple(int(c1[i] * (1 - ratio) + c2[i] * ratio) for i in range(3))

        white = (255, 255, 255)
        # 亮背景混白色90%，暗背景混白色20%
        return blend(theme_rgb, white, 0.90) if bg_lum > 150 else blend(theme_rgb, white, 0.2)

    @staticmethod
    def get_contrasting_text_color(region_image):
        color = region_image.resize((1, 1), resample=Image.Resampling.HAMMING).getpixel((0, 0))
        lum = (color[0] * 299 + color[1] * 587 + color[2] * 114) / 1000
        return "#4a3b32" if lum > 120 else "#f2f2f2"

    @staticmethod
    def _get_relative_luminance(rgb):
        """计算 sRGB 颜色的相对亮度"""
        r, g, b = [x / 255.0 for x in rgb]
        r = r / 12.92 if r <= 0.03928 else ((r + 0.055) / 1.055) ** 2.4
        g = g / 12.92 if g <= 0.03928 else ((g + 0.055) / 1.055) ** 2.4
        b = b / 12.92 if b <= 0.03928 else ((b + 0.055) / 1.055) ** 2.4
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    @classmethod
    def _get_contrast_ratio(cls, rgb1, rgb2):
        """计算两种颜色之间的对比度"""
        lum1 = cls._get_relative_luminance(rgb1)
        lum2 = cls._get_relative_luminance(rgb2)
        if lum1 > lum2:
            return (lum1 + 0.05) / (lum2 + 0.05)
        else:
            return (lum2 + 0.05) / (lum1 + 0.05)

    @classmethod
    def get_safe_qr_color(cls, theme_rgb, bg_color_rgb=(253, 253, 253)):
        """
        [最终改进] 确保二维码颜色与实际背景有足够的对比度 (WCAG > 4.5:1)。
        :param theme_rgb: 原始主题色
        :param bg_color_rgb: 二维码区域的实际平均背景色
        """
        min_contrast_ratio = 4.5

        current_color = list(theme_rgb)

        # 循环调整，直到对比度达标
        while cls._get_contrast_ratio(tuple(current_color), bg_color_rgb) < min_contrast_ratio:
            # 如果已经是纯黑，则停止
            if current_color[0] <= 5 and current_color[1] <= 5 and current_color[2] <= 5:
                return (0, 0, 0)

            # 按比例调暗颜色
            current_color[0] = max(0, int(current_color[0] * 0.9))
            current_color[1] = max(0, int(current_color[1] * 0.9))
            current_color[2] = max(0, int(current_color[2] * 0.9))

        return tuple(current_color)

    @staticmethod
    def generate_styled_qrcode(data, theme_color, size=120):
        qr = qrcode.QRCode(version=1, border=1, box_size=10)
        qr.add_data(data)
        qr.make(fit=True)
        qr_img = qr.make_image(fill_color="black", back_color="white").convert("RGBA")

        new_data = []
        tr, tg, tb = theme_color
        for item in qr_img.getdata():
            # 黑色 -> 主题色带透明度; 白色 -> 透明
            if item[0] < 128:
                new_data.append((tr, tg, tb, 230))
            else:
                new_data.append((255, 255, 255, 0))
        qr_img.putdata(new_data)
        return qr_img.resize((size, size), Image.Resampling.LANCZOS)

    @staticmethod
    def create_gradient_mask(w, h):
        gradient = Image.new('L', (1, h))
        for y in range(h):
            gradient.putpixel((0, y), int(255 * (y / h)))
        return gradient.resize((w, h))

    @staticmethod
    def create_rounded_mask(size, radius):
        """
        [改进] 创建带抗锯齿效果的圆角蒙版。
        """
        # 1. 超采样：定义一个放大倍数，2倍、4倍或更高
        upscale_factor = 8

        # 2. 创建一个放大 upscale_factor 倍的画布
        scaled_size = (size[0] * upscale_factor, size[1] * upscale_factor)
        scaled_radius = radius * upscale_factor

        # 3. 在这个大画布上绘制圆角矩形
        mask = Image.new("L", scaled_size, 0)
        draw = ImageDraw.Draw(mask)
        draw.rounded_rectangle(
            (0, 0) + scaled_size,
            radius=scaled_radius,
            fill=255
        )

        # 4. 将大图像高质量地缩小回原始尺寸
        #    Image.Resampling.LANCZOS 是高质量的缩小算法，能产生平滑的边缘
        return mask.resize(size, Image.Resampling.LANCZOS)

    # --- 布局与绘制 ---

    @staticmethod
    def contains_cjk(text: str) -> bool:
        """
        检查字符串是否包含 CJK 字符。
        """
        for char in text:
            # CJK 统一表意文字 U+4E00..U+9FFF
            # CJK 兼容表意文字 U+F900..U+FAFF
            # ... 还有很多，但这个范围已经能覆盖绝大部分场景
            if '\u4e00' <= char <= '\u9fff':
                return True
        return False

    def _process_text_wrapping(self, draw, text, font, max_width):
        """
        [最终国际化版] 智能换行方法。
        - 对 CJK 文本进行逐字换行，不加连字符。
        - 对西文文本进行单词间换行，并为超长单词自动添加连字符。
        """
        final_lines = []

        # 获取基础行高
        bbox = font.getbbox("高")
        line_height = bbox[3] - bbox[1]

        for paragraph in text.split('\n'):
            paragraph = paragraph.strip()

            if not paragraph:
                final_lines.append("")
                continue

            # --- 判断文本类型，选择不同策略 ---
            if self.contains_cjk(paragraph):
                # --- 策略 A: CJK 文本处理 (逐字换行) ---
                current_line = ""
                for char in paragraph:
                    if draw.textlength(current_line + char, font=font) <= max_width:
                        current_line += char
                    else:
                        final_lines.append(current_line)
                        current_line = char
                if current_line:
                    final_lines.append(current_line)
            else:
                # --- 策略 B: 西文文本处理 (基于单词换行) ---
                words = paragraph.split(' ')
                current_line = ""
                for word in words:
                    # 处理单个单词超长的情况
                    word_width = draw.textlength(word, font=font)
                    if word_width > max_width:
                        if current_line:
                            final_lines.append(current_line)
                            current_line = ""

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

                    # 正常的单词拼接逻辑
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


    def _draw_text_right(self, draw, text, font, right_x, y, fill):
        w = draw.textlength(text, font=font)
        draw.text((right_x - w, y), text, font=font, fill=fill)

    async def generate(self,
                       data: Dict[str, Any],
                       inner_blurred: bool = False,
                       show_qrcode: bool = False,
                       mode: str = DAILY) -> Image.Image:
        """
        生成音乐卡片的核心方法
        :param data: 包含 title, artist, cover_url, quote_content, quote_source, date_obj, music_id
        :param inner_blurred: 是否开启内部模糊
        :param show_qrcode: 是否显示二维码
        :param mode: 制卡模式
        :return: PIL.Image 对象
        """
        # 准备数据
        title = data.get('title', '')
        artist = data.get('artist', '')
        cover_url = data.get('cover_url', '')
        quote_content = data.get('quote_content', '')
        quote_source = data.get('quote_source', '')
        date_obj = data.get('date_obj', datetime.now())
        music_id = data.get('music_id')

        date_month_str = calendar.month_abbr[date_obj.month]
        date_day_int = date_obj.day

        # 下载资源
        print(f"下载封面: {cover_url}")
        cover_img_raw = (await self.download_image(cover_url)).convert("RGB")
        theme_rgb = self.get_dominant_color(cover_img_raw)
        print(f"识别主题色: {theme_rgb}")

        try:
            font_title = ImageFont.truetype(self.font_path, 44)
            font_artist = ImageFont.truetype(self.font_path, 26)
            font_date_num = ImageFont.truetype(self.font_path, 90)
            font_date_month = ImageFont.truetype(self.font_path, 40)
            font_quote = ImageFont.truetype(self.font_path, 34)
            font_quote_sub = ImageFont.truetype(self.font_path, 26)
            font_deco = ImageFont.truetype(self.font_path, 100)
            font_fc = ImageFont.truetype(self.font_path, 32)
            font_fo = ImageFont.truetype(self.font_path, 22)
        except IOError:
            print(f"字体加载失败: {self.font_path}")
            return Image.new('RGB', (100, 100), color='red')

        # 布局计算
        QR_SIZE = 120
        QR_GAP = 20

        # 文字最大宽度 (避让二维码)
        text_w = self.MAX_TEXT_W
        if show_qrcode and music_id:
            text_w -= (QR_SIZE + QR_GAP)

        temp_draw = ImageDraw.Draw(Image.new("RGB", (1, 1)))

        # 头部文本高度
        t_lines, t_h = self._process_text_wrapping(temp_draw, title, font_title, text_w)
        a_lines, a_h = self._process_text_wrapping(temp_draw, artist, font_artist, text_w)

        text_block_h = (len(t_lines) * t_h * 1.3) + 15 + (len(a_lines) * a_h * 1.5)

        # 头部实际高度 (取文本块与二维码的较大值)
        header_h_real = max(text_block_h, QR_SIZE) if (show_qrcode and music_id) else text_block_h

        match mode:
            case self.CARD:
                header_section_h = header_h_real
                middle_h = 0
                footer_inner_h = 60
            case self.DAILY:
                header_section_h = header_h_real + 30 + 4 + 40  # + padding + line + padding

                # 中间区域 (日期 & 引言)
                q_x = self.CONTENT_LEFT_X + 240
                q_max_w = self.CONTENT_RIGHT_X - q_x
                q_lines, q_font_h = self._process_text_wrapping(temp_draw, quote_content, font_quote, q_max_w)
                q_h = (len(q_lines) * q_font_h * 1.6) + 40 + 30  # + deco + padding
                middle_h = max(200, q_h)

                # 底部
                footer_inner_h = 20 + 20 + 32 + 25
            case self.LYRIC:
                header_section_h = header_h_real + 30 + 4 + 40  # + padding + line + padding

                # 中间区域 (日期 & 引言)
                q_x = self.CONTENT_LEFT_X
                q_max_w = self.CONTENT_RIGHT_X - q_x
                q_lines, q_font_h = self._process_text_wrapping(temp_draw, quote_content, font_quote, q_max_w)
                q_h = (len(q_lines) * q_font_h * 1.6) + 40 + 30  # + deco + padding
                middle_h = max(200, q_h)

                # 底部
                footer_inner_h = 20 + 20 + 32 + 25

        # 总高度
        cover_size = self.MAX_TEXT_W
        total_card_h = self.INNER_PAD + cover_size + header_section_h + footer_inner_h
        if mode != self.CARD:
            total_card_h += 30 + middle_h
        total_img_h = int(total_card_h + self.MARGIN_TOP + self.MARGIN_BOTTOM)

        # 绘制主背景
        bg_img = ImageOps.fit(cover_img_raw, (self.W, total_img_h), method=Image.Resampling.LANCZOS)
        bg_img = bg_img.filter(ImageFilter.GaussianBlur(radius=100))
        bg_img = ImageEnhance.Brightness(bg_img).enhance(0.7)

        # 卡片背景
        if inner_blurred:
            card_crop = bg_img.crop((self.MARGIN_SIDE, self.MARGIN_TOP,
                                     self.MARGIN_SIDE + self.CARD_W, self.MARGIN_TOP + total_card_h))
            card_crop = ImageEnhance.Brightness(card_crop).enhance(1.2)
            white_layer = Image.new("RGB", card_crop.size, "#FDFDFD")
            mask = self.create_gradient_mask(card_crop.width, card_crop.height)
            card_bg = Image.composite(white_layer, card_crop, mask)
        else:
            card_bg = Image.new("RGB", (self.CARD_W, int(total_card_h)), "#FDFDFD")

        # 贴卡片
        card_mask = self.create_rounded_mask(card_bg.size, 40)
        bg_img.paste(card_bg, (self.MARGIN_SIDE, self.MARGIN_TOP), card_mask)
        draw = ImageDraw.Draw(bg_img)

        # 贴封面
        cover_resized = ImageOps.fit(cover_img_raw, (cover_size, cover_size), method=Image.Resampling.LANCZOS)
        bg_img.paste(cover_resized,
                     (self.CONTENT_LEFT_X, self.MARGIN_TOP + self.INNER_PAD),
                     self.create_rounded_mask(cover_resized.size, 30))

        # 绘制内容
        cursor_y = self.MARGIN_TOP + self.INNER_PAD + cover_size + 30
        header_start_y = cursor_y

        # 绘制头部部分
        # 标题
        for line in t_lines:
            draw.text((self.CONTENT_LEFT_X, cursor_y), line, font=font_title, fill=self.C_MAIN)
            cursor_y += t_h * 1.3
        cursor_y += 15
        # 艺术家
        for line in a_lines:
            draw.text((self.CONTENT_LEFT_X, cursor_y), line, font=font_artist, fill=self.C_SUB)
            cursor_y += a_h * 1.5

        # 二维码 (头部右侧)
        if show_qrcode and music_id:
            song_url = f"https://music.163.com/#/song?id={music_id}" if self.platform == "ncm" else f"https://y.qq.com/n/ryqq_v2/songDetail/{music_id}"

            # 1. 计算二维码的精确位置和区域
            qr_x = int(self.CONTENT_RIGHT_X - QR_SIZE)
            qr_y = int(header_start_y)
            qr_region_box = (qr_x, qr_y, qr_x + QR_SIZE, qr_y + QR_SIZE)

            # 2. 采样该区域的平均背景色
            qr_background_sample = bg_img.crop(qr_region_box)
            avg_bg_color = self.get_dominant_color(qr_background_sample)

            # 3. 使用实际背景色计算安全的二维码颜色
            safe_qr_color = self.get_safe_qr_color(theme_rgb, avg_bg_color)

            # 4. 生成并粘贴二维码
            qr_img = self.generate_styled_qrcode(song_url, safe_qr_color, size=QR_SIZE)
            bg_img.paste(qr_img, (qr_x, qr_y), qr_img)

        sep_y = header_start_y + header_h_real
        if mode != self.CARD:
            # 分隔线
            sep_y = header_start_y + header_h_real + 30
            for x in range(self.CONTENT_LEFT_X, self.CONTENT_RIGHT_X, 20):
                draw.ellipse((x, sep_y, x + 4, sep_y + 4), fill=self.C_ACCENT)

            mid_y = sep_y + 40

            # 绘制中间部分

            if mode == self.DAILY:
                # 日期
                date_x = self.CONTENT_LEFT_X + 20
                month_color = self.get_adaptive_month_color(
                    bg_img.crop((date_x, mid_y, date_x + 80, mid_y + 40)), theme_rgb
                )
                draw.text((date_x, mid_y), date_month_str, font=font_date_month, fill=month_color)

                m_bbox = font_date_month.getbbox("A")
                draw.text((date_x, mid_y + (m_bbox[3] - m_bbox[1]) + 10), str(date_day_int), font=font_date_num,
                          fill=self.C_MAIN)

            # 引言
            q_curr_y = mid_y + 5
            # 获取默认字体的行高
            q_bbox = font_quote.getbbox("高")
            q_font_h = q_bbox[3] - q_bbox[1]

            # 逐行处理原始引言文本
            for raw_line in quote_content.split('\n'):
                # 匹配对齐标记
                match = re.match(r'^\[([:_-]+)\](.*)', raw_line.strip())

                if match:
                    # --- 情况 A: 行首有对齐标记 ---
                    spec, text_content = match.groups()
                    text_content = text_content.strip()

                    # 1. 确定字体
                    use_small_font = '_' in spec
                    font_size = int(font_quote.size * 0.8) if use_small_font else font_quote.size
                    target_font = ImageFont.truetype(self.font_path, font_size) if use_small_font else font_quote
                    target_bbox = target_font.getbbox("高")
                    target_font_h = target_bbox[3] - target_bbox[1]

                    # 2. 确定对齐方式
                    norm_spec = spec.replace('_', '-')
                    align = "left"  # 默认为左对齐
                    if ":-:" in norm_spec:
                        align = "center"
                    elif "-:" in norm_spec:
                        align = "right"

                    # 3. 文本换行 (使用 80% 宽度)
                    wrap_width = q_max_w * 0.8
                    margin_w = q_max_w * 0.1
                    wrapped_sub_lines, _ = self._process_text_wrapping(draw, text_content, target_font, wrap_width)

                    # 4. 绘制换行后的每一行
                    for sub_line in wrapped_sub_lines:
                        sub_line_width = draw.textlength(sub_line, font=target_font)

                        x_pos = q_x  # 默认是 `:-` (左对齐)
                        if align == "center":  # `:-:` (居中)
                            x_pos = (q_x + margin_w) + (wrap_width - sub_line_width) / 2
                        elif align == "right":  # `-:` (右对齐)
                            # 对齐到整个可用区域的右侧
                            x_pos = (q_x + q_max_w) - sub_line_width

                        draw.text((x_pos, q_curr_y), sub_line, font=target_font, fill=self.C_QUOTE)
                        q_curr_y += target_font_h * 1.6

                else:
                    # --- 情况 B: 普通行 (默认行为) ---
                    if not raw_line:  # 处理空行
                        q_curr_y += q_font_h * 1.6
                        continue

                    # 1. 文本换行 (使用 100% 宽度)
                    wrapped_sub_lines, _ = self._process_text_wrapping(draw, raw_line, font_quote, q_max_w)

                    # 2. 绘制换行后的每一行
                    for sub_line in wrapped_sub_lines:
                        draw.text((q_x, q_curr_y), sub_line, font=font_quote, fill=self.C_QUOTE)
                        q_curr_y += q_font_h * 1.6

            # 装饰引号
            deco_x = self.CONTENT_RIGHT_X - 80
            deco_y = q_curr_y - 20
            deco_color = self.get_adaptive_deco_color(
                bg_img.crop((int(deco_x), int(deco_y), int(deco_x + 60), int(deco_y + 60))), theme_rgb
            )
            draw.text((deco_x, deco_y), "❞", font=font_deco, fill=deco_color)

            # 来源
            if mode == self.DAILY:
                self._draw_text_right(draw, "--来自 @" + quote_source + " 的评论", font_quote_sub, self.CONTENT_RIGHT_X,
                                    q_curr_y + 20, self.C_SUB)

            # 绘制底部
            # 虚线分隔符 & 文字
            bot_sep_y = mid_y + middle_h + 10
            for x in range(self.CONTENT_LEFT_X, self.CONTENT_RIGHT_X, 20):
                draw.ellipse((x, bot_sep_y, x + 4, bot_sep_y + 4), fill=self.C_ACCENT)

            foot_base_y = bot_sep_y

            foot_y = foot_base_y + 24
            ct = "AMLL 亲友团 | 今日推荐" if mode == self.DAILY else "歌词制卡"
            ct_bbox = draw.textbbox((0, 0), ct, font=font_fc)
            draw.text((self.MARGIN_SIDE + (self.CARD_W - (ct_bbox[2] - ct_bbox[0])) / 2, foot_y),
                      ct, font=font_fc, fill=self.C_FOOTER_CENTER)
        else:
            foot_base_y = sep_y

        # 水印
        outer_y = self.MARGIN_TOP + total_card_h + 20
        outer_right_x = self.MARGIN_SIDE + self.CARD_W
        outer_color = self.get_contrasting_text_color(
            bg_img.crop((self.W - 300, total_img_h - 80, self.W, total_img_h)))

        self._draw_text_right(draw, "Designed by HamuChan", font_fo, outer_right_x, outer_y, outer_color)
        self._draw_text_right(draw, "Generated by KhBot v1.6.1", font_fo, outer_right_x, outer_y + 30, outer_color)

        # 左侧二维码 (外部)
        # if show_qrcode and music_id:
        #    song_url = f"https://music.163.com/#/song?id={music_id}"
        #    ext_qr = self.generate_styled_qrcode(song_url, theme_rgb, size=80)
        #    bg_img.paste(ext_qr, (self.MARGIN_SIDE, int(outer_y)-5), ext_qr)

        return bg_img


def ttml_to_lys(content: str):
    dom: Document = parseString(content)
    ttml: TTML = TTML(dom)
    orig, _ = ttml.to_lys()

    return orig

async def fetch_lys(music_id: str, platform: str):
    folder = {
        "ncm": "ncm-lyrics",
        "qq": "qq-lyrics"
    }
    url = f"https://amlldb.bikonoo.com/{folder[platform]}/{music_id}.ttml"
    print(f"正在获取 {music_id} 的 TTML 文件...")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    print(f"Error: {resp.status}")
                    return None

                # API 可能返回 "null" 或 JSON 对象
                text_resp = await resp.text()
                if not text_resp or text_resp.strip() == "null":
                    print("该 ID 所对应歌词还无人制作")
                    return None

                return ttml_to_lys(text_resp)
    except Exception as e:
        print(f"Error: {e}")
        return None


def lys_to_lines(lys_line: str):
    """
    解析歌词行，处理后，用换行符连接并返回。
    """
    processed_lines = []
    line_regex = re.compile(r'^\[(\d)\](.*)$')

    def process_lyric_line(_property: str, _text: str):
        match _property:
            case '0' | '3':
                return "[:-:]" + _text
            case '1' | '4':
                return "[:-]" + _text
            case '2' | '5':
                return "[-:]" + _text
            case '6':
                return "[:_:]" + _text
            case '7':
                return "[:_]" + _text
            case '8':
                return "[_:]" + _text
            case _:
                return _text

    for line in lys_line.strip().split('\n'):
        line = re.sub(r"\(\d+,\d+\)", '', line.strip())
        matches = line_regex.match(line)
        if matches:
            # 提取分钟、秒、毫秒和文本
            property = matches.group(1)
            text = matches.group(2)

            # 调用函数处理并保存结果
            processed_line = process_lyric_line(property, text)
            processed_lines.append(processed_line)

    return '\n'.join(processed_lines)

# --- 逻辑控制入口 ---

async def generate_music_card_process(
        platform: str,
        mode: str,
        date_str: str,
        music_id_arg: Optional[str] = None,
        info_arg: Optional[list] = None,
        quote_arg: Optional[list] = None,
        inner_blurred: bool = False,
        show_qrcode: bool = False,
        font_path: str = "PingFangSC.ttf",
        qq_music_cookie: str = ""
) -> Optional[Image.Image]:
    """
    逻辑控制中心：根据优先级获取数据并调用绘图
    优先级：每日推荐 API > 命令行 MUSIC ID > 命令行手动 Info
    """
    card_gen = MusicCard(font_path, platform)
    final_data = {}

    # 初始化日期对象
    try:
        date_obj = datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        print(f"⚠️ 日期格式错误 ({date_str})，使用今日")
        date_obj = datetime.now()
        date_str = date_obj.strftime("%Y-%m-%d")

    final_data['date_obj'] = date_obj
    daily_data = None

    if mode == MusicCard.DAILY:
        # 尝试获取每日推荐
        daily_data = await card_gen.fetch_daily_recommendation(date_str)

    if daily_data:
        print("成功获取每日推荐数据")
        # 从每日推荐中提取 music_id 和推荐语
        rec_music_id = daily_data.get('music_id')
        if rec_music_id:
            # 补充歌曲信息
            song_info = await card_gen.fetch_ncm_song_info(
                rec_music_id) if platform == "ncm" else await card_gen.fetch_qq_music_info(rec_music_id,
                                                                                           qq_music_cookie)
            if song_info:
                cover = daily_data.get('cover_path')
                if cover and cover != "/tj/wfm.jpg":
                    if cover[0] == "/":
                        song_info['cover_url'] = "https://amlldb.bikonoo.com" + cover
                    else:
                        song_info['cover_url'] = cover
                final_data.update(song_info)  # title, artist, cover_url, music_id

        # 覆盖引言
        if daily_data.get('comment'):
            final_data['quote_content'] = daily_data['comment']
        if daily_data.get('username'):
            src = daily_data['username']
            final_data['quote_source'] = src  # if src.startswith("-") else f"- {src}"

    elif mode != MusicCard.CARD:
        # 回退到 NCM ID 参数
        if mode == MusicCard.DAILY:
            print("无每日推荐或获取失败，检查命令行参数...")

        if music_id_arg:
            song_info = await card_gen.fetch_ncm_song_info(music_id_arg) if platform == "ncm" else await card_gen.fetch_qq_music_info(music_id_arg, qq_music_cookie)
            if song_info:
                final_data.update(song_info)
        # 回退到手动 Info 参数
        elif info_arg and len(info_arg) == 3:
            final_data['title'] = info_arg[0]
            final_data['artist'] = info_arg[1]
            final_data['cover_url'] = info_arg[2]

    # 检查是否具备生成条件
    if 'title' not in final_data:
        print("错误: 无法获取歌曲信息 (Daily API 返回值为空, 且未提供有效 NCM ID 或 Info)")
        return None

    # 处理手动引言 (仅当 API 未提供引言时，才使用命令行参数覆盖默认值)
    # 如果 Daily API 已经填入了 quote_content，则忽略命令行 quote
    if 'quote_content' not in final_data:
        if mode == MusicCard.LYRIC:
            lys = await fetch_lys(music_id_arg, platform)
            if not lys:
                print("错误: 无法获取歌曲信息 (Daily API 返回值为空, 且未提供有效 NCM ID 或 Info)")
                return None
            final_data['quote_content'] = lys_to_lines(lys)
        elif quote_arg and len(quote_arg) == 2:
            final_data['quote_content'] = quote_arg[0].replace('\\n', '\n')
            final_data['quote_source'] = quote_arg[1]
        else:
            # 默认引言
            final_data['quote_content'] = "想要和你一起 一同实现远大的梦想"
            final_data['quote_source'] = "RuriChan"

    # 生成图片
    return await card_gen.generate(final_data, inner_blurred, show_qrcode, mode)


# --- 命令行入口 ---

async def main():
    parser = argparse.ArgumentParser(description="生成仿网易云音乐风格的音乐卡片")
    parser.add_argument("--platform", type=str, choices=["ncm", "qq"], default="ncm", help="获取歌曲的平台 ncm/qq")
    parser.add_argument("--mode", type=str, choices=["daily", "card", "lyric"], default="daily", help="制卡模式")
    parser.add_argument("--date", type=str, default=datetime.now().strftime("%Y-%m-%d"), help="日期 YYYY-MM-DD")
    parser.add_argument("--info", nargs=3, metavar=('TITLE', 'ARTIST', 'COVER_URL'), help="手动指定歌曲信息")
    parser.add_argument("--quote", nargs=2, metavar=('CONTENT', 'SOURCE'), help="引言内容与来源")
    parser.add_argument("--inner-blurred", action="store_true", help="卡片内部背景模糊")
    parser.add_argument("--qrcode", action="store_true", help="生成二维码")
    parser.add_argument("--qq-music-cookie", type=str, help="QQ 音乐 Cookie")
    parser.add_argument("--music-id", type=str, help="歌曲 ID")

    args = parser.parse_args()

    img = await generate_music_card_process(
        platform=args.platform,
        mode=args.mode,
        date_str=args.date,
        music_id_arg=args.music_id,
        info_arg=args.info,
        quote_arg=args.quote,
        inner_blurred=args.inner_blurred,
        show_qrcode=args.qrcode,
        qq_music_cookie=args.qq_music_cookie
    )

    if img:
        filename = ''
        match args.mode:
            case MusicCard.LYRIC:
                filename = f"music_lyric_{args.music_id}.png"
            case MusicCard.CARD:
                filename = f"music_card_{args.music_id}.png"
            case MusicCard.DAILY:
                filename = f"music_card_{args.date}.png"
        img.save(filename)
        print(f"图片保存成功: {filename}")
        # img.show()


if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
