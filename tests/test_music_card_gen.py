import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from music_card_gen import (
    DailyRecommendationService,
    LyricsService,
    MusicCard,
    NcmProvider,
    Platform,
    QqProvider,
    QuoteParser,
)


class _FakeHttpClient:
    def __init__(self):
        self.text_response = (200, "")
        self.json_response = (200, None)
        self.post_response = (200, None)
        self.final_url = None

    async def get_text(self, url, **kwargs):
        _ = url, kwargs
        return self.text_response

    async def get_json(self, url, **kwargs):
        _ = url, kwargs
        return self.json_response

    async def post_json(self, url, payload, **kwargs):
        _ = url, payload, kwargs
        return self.post_response

    async def resolve_final_url(self, url, max_redirects=10):
        _ = url, max_redirects
        return self.final_url


class QuoteParserTests(unittest.TestCase):
    def test_parse_quote_tokens(self):
        parsed = QuoteParser.parse("[:-:]Line A\n[-:]Line B\nNormal")
        self.assertEqual(len(parsed.lines), 3)
        self.assertEqual(parsed.lines[0].spec, ":-:")
        self.assertEqual(parsed.lines[1].spec, "-:")
        self.assertEqual(parsed.lines[2].spec, None)
        self.assertFalse(parsed.pure_center)


class ColorSafetyTests(unittest.TestCase):
    def test_safe_qr_color_meets_min_contrast(self):
        bg = (253, 253, 253)
        color = MusicCard.get_safe_qr_color((220, 220, 220), bg)
        ratio = MusicCard._get_contrast_ratio(color, bg)
        self.assertGreaterEqual(ratio, 4.5)


class ProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_ncm_provider_success(self):
        client = _FakeHttpClient()
        client.json_response = (
            200,
            {
                "songs": [
                    {
                        "name": "Song A",
                        "artists": [{"name": "Artist A"}],
                        "album": {"picUrl": "https://example.com/a.jpg"},
                    }
                ]
            },
        )

        song = await NcmProvider().fetch_song("123", client)
        self.assertIsNotNone(song)
        self.assertEqual(song.title, "Song A")
        self.assertEqual(song.artist, "Artist A")

    async def test_ncm_provider_404(self):
        client = _FakeHttpClient()
        client.json_response = (404, None)
        song = await NcmProvider().fetch_song("123", client)
        self.assertIsNone(song)

    async def test_qq_provider_success(self):
        client = _FakeHttpClient()
        client.post_response = (
            200,
            {
                "req": {
                    "data": {
                        "tracks": [
                            {
                                "title": "Song B",
                                "subtitle": "Sub",
                                "singer": [{"name": "Singer B"}],
                                "album": {"mid": "ABC123"},
                            }
                        ]
                    }
                }
            },
        )

        song = await QqProvider().fetch_song("00123", client)
        self.assertIsNotNone(song)
        self.assertEqual(song.title, "Song B (Sub)")
        self.assertEqual(song.artist, "Singer B")

    async def test_qq_provider_invalid_id(self):
        client = _FakeHttpClient()
        song = await QqProvider().fetch_song("not-a-valid-id", client)
        self.assertIsNone(song)


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_daily_recommendation_service(self):
        client = _FakeHttpClient()
        client.text_response = (
            200,
            '{"ncm_id":"123","date":"2026-01-01","username":"u","comment":"c","cover":"/x.jpg"}',
        )

        rec = await DailyRecommendationService().fetch("2026-01-01", client)
        self.assertIsNotNone(rec)
        self.assertEqual(rec.music_id, "123")

    async def test_lyrics_service(self):
        client = _FakeHttpClient()
        client.text_response = (200, "<tt></tt>")

        with patch("music_card_gen.TTML") as mock_ttml:
            mock_ttml.return_value.text = "line1\nline2"
            text = await LyricsService().fetch("123", Platform.NCM, client)

        self.assertEqual(text, "line1\nline2")


class CliIntegrationTests(unittest.TestCase):
    def test_cli_returns_nonzero_without_song_inputs(self):
        repo = Path(__file__).resolve().parents[1]
        proc = subprocess.run(
            [sys.executable, "music_card_gen.py", "--mode", "card"],
            cwd=repo,
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 1)
        self.assertIn("无法获取歌曲信息", proc.stderr + proc.stdout)


if __name__ == "__main__":
    unittest.main()
