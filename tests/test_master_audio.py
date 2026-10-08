import json
import math
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch
import wave

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "multicam-video-preprocessing" / "scripts"))

from modules.audio_sync import sync_all_targets
from modules.master_audio import align_master_audio, probe_reference_video


class MasterAudioTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = os.path.join(self.directory.name, "external.wav")
        Path(self.path).touch()
        self.ref_info = {"path": "/raw/reference.mp4", "duration_sec": 100.0}

    def align(self, offset=0.0, duration=100.0, score=20.0, start=0.0, end=100.0):
        with patch("modules.master_audio._audio_duration", return_value=duration), patch(
            "modules.master_audio.sync_single_target",
            return_value={"offset_sec": offset, "peak_z_score": score, "confidence": 95.0},
        ) as sync:
            result = align_master_audio(self.path, self.ref_info, start, end)
        return result, sync

    @patch("modules.master_audio.sync_single_target")
    @patch("modules.master_audio._probe_stream")
    def test_omitted_master_does_not_probe_or_align(self, probe, sync):
        self.assertIsNone(align_master_audio(None, self.ref_info, 0.0, 100.0))
        probe.assert_not_called()
        sync.assert_not_called()

    def test_missing_explicit_master_is_an_error(self):
        with self.assertRaisesRegex(ValueError, "file not found"):
            align_master_audio(self.path + ".missing", self.ref_info, 0, 10)

    @patch("modules.master_audio._probe_stream", return_value={"streams": []})
    @patch("modules.master_audio.sync_single_target")
    def test_video_without_audio_is_rejected_before_alignment(self, sync, probe):
        with self.assertRaisesRegex(ValueError, "no audio stream"):
            align_master_audio(self.path, self.ref_info, 0, 10)
        sync.assert_not_called()

    def test_negative_offset_preserves_raw_reference_time(self):
        master, sync = self.align(offset=-12.25, duration=120)
        self.assertEqual(master["path"], os.path.abspath(self.path))
        self.assertEqual(master["offset_sec"], -12.25)
        self.assertEqual(master["duration_sec"], 120)
        self.assertIs(sync.call_args.args[0], self.ref_info)
        self.assertEqual(0 - master["offset_sec"], 12.25)

    def test_positive_offset_accepts_explicitly_trimmed_range(self):
        master, _ = self.align(offset=12.25, duration=100, start=13, end=95)
        self.assertAlmostEqual(13 - master["offset_sec"], 0.75)

    def test_late_master_does_not_silently_shorten_camera_range(self):
        with self.assertRaisesRegex(ValueError, "camera range was not shortened"):
            self.align(offset=1, duration=110)

    def test_short_master_does_not_silently_shorten_camera_range(self):
        with self.assertRaisesRegex(ValueError, "camera range was not shortened"):
            self.align(duration=99)

    def test_low_confidence_is_rejected_even_without_strict_sync(self):
        with self.assertRaisesRegex(ValueError, "confidence is too low"):
            self.align(score=6.9)

    def test_nonfinite_alignment_is_rejected(self):
        for value in (math.nan, math.inf):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "non-finite"):
                self.align(offset=value)

    @patch("modules.master_audio._probe_stream", return_value={
        "streams": [{"duration": "50"}], "format": {"duration": "100"},
    })
    @patch("modules.master_audio.sync_single_target", return_value={
        "offset_sec": 0, "peak_z_score": 20, "confidence": 95,
    })
    def test_audio_stream_duration_overrides_longer_video_container(self, sync, probe):
        with self.assertRaisesRegex(ValueError, "camera range was not shortened"):
            align_master_audio(self.path, self.ref_info, 0, 90)

    @patch("modules.master_audio._probe_stream", return_value={
        "streams": [{"tags": {"DURATION": "00:00:50.000000000"}}],
        "format": {"duration": "100"},
    })
    @patch("modules.master_audio.sync_single_target", return_value={
        "offset_sec": 0, "peak_z_score": 20, "confidence": 95,
    })
    def test_matroska_audio_duration_tag_is_used(self, sync, probe):
        with self.assertRaisesRegex(ValueError, "camera range was not shortened"):
            align_master_audio(self.path, self.ref_info, 0, 90)

    @patch("modules.master_audio._probe_stream", return_value={
        "streams": [{}], "format": {"duration": "100"},
    })
    @patch("modules.master_audio.sync_single_target", return_value={
        "offset_sec": 0, "peak_z_score": 20, "confidence": 95,
        "analyzed_audio_duration_sec": 50,
    })
    def test_unknown_audio_duration_uses_full_decode_not_container_duration(self, sync, probe):
        with self.assertRaisesRegex(ValueError, "camera range was not shortened"):
            align_master_audio(self.path, self.ref_info, 0, 90, sample_dur=10)
        self.assertTrue(sync.call_args.kwargs["full_scan"])
        self.assertIsNone(sync.call_args.kwargs["max_dur"])


class VideoFormatProbeTest(unittest.TestCase):
    @patch("modules.master_audio.subprocess.run")
    def test_retains_exact_ntsc_frame_rate(self, run):
        run.return_value.stdout = json.dumps({"streams": [{
            "width": 3840, "height": 2160, "avg_frame_rate": "30000/1001",
            "r_frame_rate": "30000/1001",
        }]})
        actual = probe_reference_video("camera.mp4")
        self.assertEqual(actual["fps_num"], 30000)
        self.assertEqual(actual["fps_den"], 1001)
        self.assertAlmostEqual(actual["fps"], 30000 / 1001)
        self.assertEqual((actual["width"], actual["height"]), (3840, 2160))

    @patch("modules.master_audio._probe_stream", return_value={"streams": [{
        "width": 1920, "height": 1080, "avg_frame_rate": "0/0", "r_frame_rate": "25/1",
    }]})
    def test_falls_back_to_stream_frame_rate(self, probe):
        self.assertEqual(probe_reference_video("camera.mov")["fps"], 25.0)

    @patch("modules.master_audio._probe_stream", return_value={"streams": []})
    def test_audio_only_reference_is_rejected(self, probe):
        with self.assertRaisesRegex(ValueError, "no video stream"):
            probe_reference_video("audio.wav")


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg is required for acoustic fixture")
class MasterAcousticIntegrationTest(unittest.TestCase):
    def test_shared_waveform_finds_earlier_master_without_an_extra_sync_algorithm(self):
        with tempfile.TemporaryDirectory() as directory:
            sr = 8000
            rng = np.random.default_rng(250108)
            raw = (rng.normal(0, 2200, 16 * sr)).astype(np.int16)
            master_path = os.path.join(directory, "master.wav")
            ref_path = os.path.join(directory, "ref.wav")
            for path, samples in ((master_path, raw), (ref_path, raw[2 * sr:14 * sr])):
                with wave.open(path, "wb") as output:
                    output.setnchannels(1)
                    output.setsampwidth(2)
                    output.setframerate(sr)
                    output.writeframes(samples.tobytes())
            ref_info, _ = sync_all_targets(ref_path, [], sr=sr, workers=1, full_scan=True)
            result = align_master_audio(master_path, ref_info, 0, 12, sr=sr, full_scan=True)
            self.assertAlmostEqual(result["offset_sec"], -2.0, delta=1 / sr)
            self.assertGreaterEqual(result["peak_z_score"], 7)
            self.assertAlmostEqual(result["duration_sec"], 16.0)


if __name__ == "__main__":
    unittest.main()
