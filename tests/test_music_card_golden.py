import sys
import unittest
from pathlib import Path

from PIL import Image

from music_card import Mode
from music_card.parsing import QuoteParser

GOLDEN_HELPER_DIR = Path(__file__).resolve().parent / "golden"
if str(GOLDEN_HELPER_DIR) not in sys.path:
    sys.path.insert(0, str(GOLDEN_HELPER_DIR))

from golden_utils import (  # noqa: E402
    build_payload,
    compute_diff_metrics,
    expected_path,
    render_mode,
    save_diff_artifacts,
)


class GoldenRegressionTests(unittest.IsolatedAsyncioTestCase):
    MAX_DIFF_THRESHOLD = 2.0
    MEAN_DIFF_THRESHOLD = 0.02
    CHANGED_RATIO_THRESHOLD = 0.001

    def test_quote_samples_are_multiline_and_style_parseable(self):
        daily_quote = build_payload(Mode.DAILY).quote_content
        lyric_quote = build_payload(Mode.LYRIC).quote_content

        self.assertIn("\n", daily_quote)
        self.assertIn("\n", lyric_quote)

        daily_parsed = QuoteParser.parse(daily_quote)
        lyric_parsed = QuoteParser.parse(lyric_quote)

        self.assertGreaterEqual(len(daily_parsed.lines), 5)
        self.assertGreaterEqual(len(lyric_parsed.lines), 4)
        self.assertTrue(any(line.spec == ":-:" for line in daily_parsed.lines))
        self.assertTrue(any(line.spec == "-:" for line in lyric_parsed.lines))

    async def test_mode_renders_match_golden(self):
        modes = (Mode.DAILY, Mode.CARD, Mode.LYRIC, Mode.NOW_PLAYING)
        missing = [str(expected_path(mode)) for mode in modes if not expected_path(mode).exists()]
        if missing:
            self.fail(
                "Missing golden files:\n"
                + "\n".join(missing)
                + "\nRun: python tests/golden/update_golden.py"
            )

        for mode in modes:
            with self.subTest(mode=mode.value):
                expected = Image.open(expected_path(mode)).convert("RGB")
                actual = (await render_mode(mode)).convert("RGB")
                metrics = compute_diff_metrics(expected, actual)

                failed = (
                    metrics["same_size"] < 1.0
                    or metrics["max_diff"] > self.MAX_DIFF_THRESHOLD
                    or metrics["mean_diff"] > self.MEAN_DIFF_THRESHOLD
                    or metrics["changed_ratio"] > self.CHANGED_RATIO_THRESHOLD
                )

                if failed:
                    save_diff_artifacts(mode, expected, actual)
                    self.fail(
                        "Golden mismatch for mode={mode}. "
                        "max_diff={max_diff:.4f}, mean_diff={mean_diff:.6f}, changed_ratio={changed_ratio:.6f}. "
                        "Artifacts saved under tests/golden/artifacts."
                        .format(mode=mode.value, **metrics)
                    )


if __name__ == "__main__":
    unittest.main()
