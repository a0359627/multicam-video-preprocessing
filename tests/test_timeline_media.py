import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from modules.timeline_media import probe_media, continuous_ranges, validate_source_range


class TimelineMediaTests(unittest.TestCase):
    def test_invalid_average_rate_uses_nominal_fraction(self):
        for bad in ("0/0", "0/1", ""):
            with self.subTest(rate=bad), patch("modules.timeline_media.subprocess.run") as run:
                run.return_value = types.SimpleNamespace(stdout=json.dumps({"streams": [
                    {"codec_type": "video", "avg_frame_rate": bad, "r_frame_rate": "30000/1001", "width": 3840, "height": 2160},
                    {"codec_type": "audio", "channels": 1, "duration": "4.0"}],
                    "format": {"duration": "5.0"}}))
                result = probe_media("unused.mp4")
                self.assertAlmostEqual(result["fps"], 30000 / 1001)
                self.assertEqual(result["audio_duration"], 4)

    def test_only_adjacent_ranges_join(self):
        result = continuous_ranges([{"start_sec": 1, "end_sec": 2}, {"start_sec": 2, "end_sec": 3},
                                    {"start_sec": 4, "end_sec": 5}])
        self.assertEqual(result, [(1, 3), (4, 5)])

    def test_missing_master_coverage_is_rejected(self):
        source = {"name": "Master", "offset": -2, "duration": 5}
        with self.assertRaises(ValueError):
            validate_source_range(source, 1, 4)
        validate_source_range(source, 2, 7)
        with self.assertRaises(ValueError):
            validate_source_range(source, 2, 8)
