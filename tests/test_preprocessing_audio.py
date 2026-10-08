import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "multicam-video-preprocessing" / "scripts"))

import multicam_pipeline
from modules.audio_normalizer import normalize_all_audio_tracks
from modules.reporter import export_sync_json


class PreprocessingAudioTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        # Identical basenames are normal when camera cards have separate folders.
        self.ref_path = self.root / "z_reference" / "C0001.MP4"
        self.target_path = self.root / "a_target" / "C0001.MP4"
        for path in (self.ref_path, self.target_path):
            path.parent.mkdir()
            path.write_bytes(b"camera")
        self.output_dir = self.root / "output"
        self.ref = {"path": str(self.ref_path), "basename": "C0001.MP4", "duration_sec": 30.0}
        self.targets = [{
            "target_video": str(self.target_path), "target_basename": "C0001.MP4",
            "duration_sec": 30.0, "offset_sec": 2.0, "confidence": 99.0, "peak_z_score": 25.0,
        }]
        self.format = {"fps": 25.0, "fps_num": 25, "fps_den": 1, "width": 1920, "height": 1080}

    def run_pipeline(self, extra=None, master=None):
        argv = [
            "multicam_pipeline.py", "--ref", str(self.ref_path), "--targets", str(self.target_path),
            "--output-dir", str(self.output_dir), "--merge",
        ] + (extra or [])
        def export(source, destination, start, end, **kwargs):
            Path(destination).write_bytes(b"new synchronized master")
            return 0.0
        def compose(sources, destination, **kwargs):
            Path(destination).write_bytes(b"grid")
            return 0.0
        with patch.object(sys, "argv", argv), patch.object(
            multicam_pipeline, "probe_reference_video", return_value=self.format,
        ), patch.object(
            multicam_pipeline, "sync_all_targets", return_value=(self.ref.copy(), [r.copy() for r in self.targets]),
        ), patch.object(
            multicam_pipeline, "align_master_audio", return_value=master,
        ) as alignment, patch.object(
            multicam_pipeline, "cut_single_clip", side_effect=export,
        ) as cut, patch.object(
            multicam_pipeline, "compose_multicam_video", side_effect=compose,
        ) as grid, contextlib.redirect_stdout(io.StringIO()):
            multicam_pipeline.main()
        metadata = json.loads((self.output_dir / "multicam_sync.json").read_text())
        return metadata, alignment, cut, grid

    def test_no_master_uses_camera_order_and_distinct_synced_outputs(self):
        data, alignment, cut, grid = self.run_pipeline()
        self.assertIsNone(data["master_audio"])
        self.assertIsNone(alignment.call_args.args[0])
        self.assertEqual(data["trim"]["ref_start_sec"], 2.0)
        self.assertEqual(data["trim"]["ref_end_sec"], 30.0)
        self.assertEqual(data["video_format"], self.format)
        self.assertEqual([camera["camera_id"] for camera in data["cameras"]], ["CAM1", "CAM2"])
        self.assertEqual(data["cameras"][0]["source_path"], str(self.ref_path))
        self.assertEqual(data["cameras"][1]["source_path"], str(self.target_path))
        self.assertNotEqual(data["cameras"][0]["synced_path"], data["cameras"][1]["synced_path"])
        self.assertIsNone(grid.call_args.kwargs["master_audio_path"])
        self.assertEqual(cut.call_count, 2)

    def test_master_grid_source_time_includes_reference_trim(self):
        master_path = self.root / "master.wav"
        master_path.write_bytes(b"master")
        master = {"path": str(master_path), "offset_sec": -3.0, "duration_sec": 40.0,
                  "confidence": 99.0, "peak_z_score": 25.0}
        data, alignment, cut, grid = self.run_pipeline(["--master-audio", str(master_path)], master)
        self.assertEqual(data["master_audio"], master)
        self.assertEqual(grid.call_args.kwargs["master_audio_offset_sec"], 5.0)

    def test_existing_large_master_is_reexported(self):
        self.output_dir.mkdir()
        stale = self.output_dir / "CAM1_C0001_synced.MP4"
        stale.write_bytes(b"x" * 1000001)
        data, alignment, cut, grid = self.run_pipeline()
        self.assertEqual(cut.call_count, 2)
        self.assertEqual(stale.read_bytes(), b"new synchronized master")

    def test_output_path_cannot_overwrite_source_recording(self):
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            self.run_pipeline(["--ref-output", str(self.ref_path)])
        self.assertEqual(self.ref_path.read_bytes(), b"camera")

    def test_invalid_camera_range_cannot_be_silently_clamped(self):
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            self.run_pipeline(["--ref-start", "0"])

    def test_normalization_temp_files_do_not_collide_for_same_filename(self):
        with patch("modules.audio_normalizer.normalize_single_audio_track", return_value=0), contextlib.redirect_stdout(io.StringIO()):
            audio_map, _ = normalize_all_audio_tracks([str(self.ref_path), str(self.target_path)], str(self.root))
        self.assertNotEqual(audio_map[str(self.ref_path)], audio_map[str(self.target_path)])

    def test_analysis_metadata_has_explicit_camera_ids_and_no_synced_paths(self):
        destination = self.root / "analysis.json"
        export_sync_json(destination, self.ref, self.targets, video_format=self.format)
        data = json.loads(destination.read_text())
        self.assertEqual(data["schema_version"], 2)
        self.assertIsNone(data["cameras"][0]["synced_path"])
        self.assertEqual(data["cameras"][1]["camera_id"], "CAM2")


if __name__ == "__main__":
    unittest.main()
