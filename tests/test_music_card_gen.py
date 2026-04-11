import json
import subprocess
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from music_card.models import Mode, Platform
from music_card.parsing import QuoteParser
from music_card.providers import NcmProvider, QqProvider, coerce_mode
from music_card.renderer import MusicCard
from music_card.services import DailyRecommendationService, LyricsService, NowPlayingService


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


class ModeCoerceTests(unittest.TestCase):
    def test_now_playing_aliases(self):
        self.assertEqual(coerce_mode("now-playing"), Mode.NOW_PLAYING)
        self.assertEqual(coerce_mode("now_playing"), Mode.NOW_PLAYING)
        self.assertEqual(coerce_mode("nowplaying"), Mode.NOW_PLAYING)


class NowPlayingJsonTests(unittest.TestCase):
    def test_parse_now_playing_json_success(self):
        data = NowPlayingService.parse_json(
            '{"progress":13282,"duration":218608,"track":"謳歌爛漫","artist":"スリーズブーケ","coverUrl":"x","url":"https://open.spotify.com/track/abc"}'
        )
        self.assertEqual(data.progress_ms, 13282)
        self.assertEqual(data.duration_ms, 218608)
        self.assertEqual(data.track, "謳歌爛漫")
        self.assertEqual(data.artist, "スリーズブーケ")
        self.assertEqual(data.cover_url, "x")

    def test_parse_now_playing_json_missing_required_field(self):
        with self.assertRaises(ValueError):
            NowPlayingService.parse_json('{"duration":218608,"track":"Song","artist":"Artist","coverUrl":"x"}')

    def test_parse_now_playing_json_invalid_duration(self):
        with self.assertRaises(ValueError):
            NowPlayingService.parse_json('{"progress":1000,"duration":0,"track":"Song","artist":"Artist","coverUrl":"x"}')


class ColorSafetyTests(unittest.TestCase):
    def test_safe_qr_color_meets_min_contrast(self):
        bg = (253, 253, 253)
        color = MusicCard.get_safe_qr_color((220, 220, 220), bg)
        ratio = MusicCard._get_contrast_ratio(color, bg)
        self.assertGreaterEqual(ratio, 4.5)

    def test_format_duration_ms(self):
        self.assertEqual(MusicCard.format_duration_ms(13282), "0:13")
        self.assertEqual(MusicCard.format_duration_ms(218608), "3:38")
        self.assertEqual(MusicCard.format_duration_ms(3723000), "1:02:03")

    def test_now_playing_progress_colors_follow_theme(self):
        bg = Image.new("RGB", (60, 20), "#f5f5f5")
        played_a, unplayed_a = MusicCard.get_now_playing_progress_colors(bg, (32, 120, 220))
        played_b, unplayed_b = MusicCard.get_now_playing_progress_colors(bg, (220, 80, 60))
        self.assertNotEqual(played_a, played_b)
        self.assertNotEqual(unplayed_a, unplayed_b)
        self.assertGreater(MusicCard._get_contrast_ratio(played_a, unplayed_a), 1.05)

    def test_now_playing_progress_colors_close_to_deco_color(self):
        bg = Image.new("RGB", (60, 20), "#f5f5f5")
        theme = (48, 124, 212)
        deco = MusicCard.get_adaptive_deco_color(bg, theme)
        played, unplayed = MusicCard.get_now_playing_progress_colors(bg, theme)

        def distance(c1, c2):
            return sum(abs(c1[i] - c2[i]) for i in range(3))

        self.assertLess(distance(played, deco), distance(played, theme))
        self.assertLess(distance(unplayed, deco), distance(unplayed, theme))


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

        with patch("music_card.services.TTML") as mock_ttml:
            mock_ttml.return_value.text = "line1\nline2"
            text = await LyricsService().fetch("123", Platform.NCM, client)

        self.assertEqual(text, "line1\nline2")


class CliIntegrationTests(unittest.TestCase):
    @staticmethod
    def _start_now_playing_server(payload: dict):
        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path != "/now-playing":
                    self.send_response(404)
                    self.end_headers()
                    return
                body = json.dumps(payload).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format, *args):
                _ = format, args
                return

        server = HTTPServer(("127.0.0.1", 0), _Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return server, thread

    def test_cli_returns_nonzero_without_song_inputs(self):
        repo = Path(__file__).resolve().parents[1]
        proc = subprocess.run(
            [sys.executable, "-m", "music_card", "--mode", "card"],
            cwd=repo,
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 1)
        self.assertIn("无法获取歌曲信息", proc.stderr + proc.stdout)

    def test_cli_now_playing_requires_json(self):
        repo = Path(__file__).resolve().parents[1]
        proc = subprocess.run(
            [sys.executable, "-m", "music_card", "--mode", "now-playing"],
            cwd=repo,
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 1)
        self.assertIn("now-playing 模式需要提供 now playing 数据", proc.stderr + proc.stdout)

    def test_cli_now_playing_json_generates_image(self):
        repo = Path(__file__).resolve().parents[1]
        output = repo / "music_now_playing_0.png"
        if output.exists():
            output.unlink()

        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "music_card",
                "--mode",
                "now-playing",
                "--now-playing-json",
                '{"progress":13282,"duration":218608,"track":"Song","artist":"Artist","coverUrl":"x"}',
            ],
            cwd=repo,
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr + proc.stdout)
        self.assertTrue(output.exists())
        output.unlink(missing_ok=True)

    def test_cli_now_playing_data_url_has_priority_over_json(self):
        repo = Path(__file__).resolve().parents[1]
        output = repo / "music_now_playing_0.png"
        if output.exists():
            output.unlink()

        server, thread = self._start_now_playing_server(
            {
                "progress": 13282,
                "duration": 218608,
                "track": "Song",
                "artist": "Artist",
                "coverUrl": "x",
            }
        )

        try:
            proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "music_card",
                    "--mode",
                    "now-playing",
                    "--now-playing-json",
                    '{"progress":1,"duration":0}',
                    "--now-playing-data-url",
                    f"http://127.0.0.1:{server.server_port}/now-playing",
                ],
                cwd=repo,
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr + proc.stdout)
            self.assertTrue(output.exists())
        finally:
            output.unlink(missing_ok=True)
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
