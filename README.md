# music-rec-card

一个简单的仿网易云音乐风格的每日推荐音乐卡片生成器，基于 PIL 实现。

## 使用

在命令行直接调用：

```
usage: music_card_gen.py [-h] [--platform {ncm,qq}] [--date DATE]
                         [--info TITLE ARTIST COVER_URL]
                         [--quote CONTENT SOURCE] [--inner-blurred] [--qrcode]
                         [--card-only] [--qq-music-cookie QQ_MUSIC_COOKIE]
                         [--music-id MUSIC_ID]

生成仿网易云音乐风格的音乐卡片

options:
  -h, --help            show this help message and exit
  --platform {ncm,qq}   获取歌曲的平台 ncm/qq
  --date DATE           日期 YYYY-MM-DD
  --info TITLE ARTIST COVER_URL
                        手动指定歌曲信息
  --quote CONTENT SOURCE
                        引言内容与来源
  --inner-blurred       卡片内部背景模糊
  --qrcode              生成二维码
  --card-only           仅生成卡片模式(移除日期与引言)
  --qq-music-cookie QQ_MUSIC_COOKIE
                        QQ 音乐 Cookie
  --music-id MUSIC_ID   歌曲 ID
```

在其他 Python 脚本中调用（确保 `music_card_gen.py` 在同目录）：

```python
import asyncio
from music_card_gen import generate_music_card_process

async def my_script():
    # 调用生成函数，获取 Image 对象
    img = await generate_music_card_process(
        date_str="2025-12-19",
        inner_blurred=True,
        show_qrcode=True
    )
    
    if img:
        # 可以直接处理 img 对象，例如发送到 Discord
        img.save("result_from_external.png")
        print("Got image!")
    else:
        print("Failed to generate.")

if __name__ == "__main__":
    asyncio.run(my_script())
```

## 致谢

本项目使用了 [Google Gemini](https://aistudio.google.com/app/prompts/new_chat?model=gemini-3-pro-preview) 辅助开发。

## 许可

本项目基于 [MIT License](LICENSE) 获得许可。