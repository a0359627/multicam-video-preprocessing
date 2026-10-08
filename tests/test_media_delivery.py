"""Offline official-CLI checks of editable XML and audible MP4 deliverables.

Set MULTICAM_TEST_OUTPUT_DIR to preserve generated media for manual NLE review.
The fixtures are synthetic; no customer footage, cloud service or model is used.
"""

import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from urllib.parse import unquote, urlsplit
import xml.etree.ElementTree as ET

import numpy as np

from media_fixtures import (SAMPLE_RATE, correlation, decode_audio, delivery_fixture,
                            edit_audio, frame_rgb, probe, run, signal, video, write_wav)


SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "multicam-video-preprocessing" / "scripts"
TOOLS_AVAILABLE = shutil.which("ffmpeg") and shutil.which("ffprobe")
EDL_RANGES = [(1.0, 3.0), (4.0, 6.0)]


@unittest.skipUnless(TOOLS_AVAILABLE, "FFmpeg and FFprobe are required for media integration tests")
class TestMediaDelivery(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        parent = os.environ.get("MULTICAM_TEST_OUTPUT_DIR")
        if parent:
            Path(parent).expanduser().mkdir(parents=True, exist_ok=True)
        cls.output = Path(tempfile.mkdtemp(prefix="media-delivery-", dir=str(Path(parent).expanduser()) if parent else None))
        if not parent:
            cls.addClassCleanup(shutil.rmtree, cls.output)
        else:
            print(f"\nPreserved media delivery fixtures: {cls.output}", file=sys.stderr)
        cls.fixtures = {}
        cls.metrics = {}

    def record(self, name, **values):
        self.metrics[name] = values
        (self.output / "verification.json").write_text(json.dumps(self.metrics, indent=2), encoding="utf-8")

    def fixture(self, name, **kwargs):
        if name not in self.fixtures:
            folder = self.output / name
            metadata, edl = delivery_fixture(folder, **kwargs)
            self.fixtures[name] = folder, metadata, edl
        return self.fixtures[name]

    def export(self, folder, edl, *, suffix="", extra=()):
        output = folder / f"final_cut_full{suffix}.xml"
        run([sys.executable, SCRIPTS / "export_fcp7_xml.py", "-e", edl, "-m", folder,
             "-o", output, "--strict-edl", "--lang", "zh-TW", *extra])
        return output, ET.parse(output).getroot()

    def render(self, folder, edl, *, suffix="", extra=()):
        output = folder / f"final_cut_full{suffix}.mp4"
        run([sys.executable, SCRIPTS / "edl_to_video.py", "--edl", edl, "--media-dir", folder,
             "-o", output, "--encoder", "libx264", "--workers", "1", "--video-bitrate", "500k",
             "--strict-edl", "--lang", "zh-TW", *extra])
        return output

    def assert_linked_xml(self, root, metadata, *, master=False, raw=False):
        sequence = root.find("sequence")
        rate = metadata["video_format"]["fps"]
        expected_frames = sum(round(end * rate) - round(start * rate) for start, end in EDL_RANGES)
        self.assertEqual(int(sequence.findtext("duration")), expected_frames)
        sample = sequence.find("media/video/format/samplecharacteristics")
        self.assertEqual(int(sample.findtext("width")), 320)
        self.assertEqual(int(sample.findtext("height")), 180)
        definitions = {}
        for node in root.findall(".//file"):
            if node.find("pathurl") is not None:
                path = Path(unquote(urlsplit(node.findtext("pathurl")).path))
                self.assertTrue(path.is_file(), f"XML links missing media: {path}")
                definitions[node.attrib["id"]] = (path, node)
        clips = sequence.findall("media/video/track/clipitem")
        self.assertEqual(len(clips), 3, "Two contiguous CAM1 rows should be one editable clip")
        self.assertEqual([int(c.findtext("start")) for c in clips], [0, round(2 * rate), round(3 * rate)])
        self.assertEqual([c.findtext("name") for c in clips], ["CAM1", "CAM2", "CAM1"])
        for clip in clips:
            camera = metadata["cameras"][int(clip.findtext("name")[-1]) - 1]
            linked = definitions[clip.find("file").attrib["id"]][0]
            self.assertEqual(linked.resolve(), Path(camera["source_path" if raw else "synced_path"]).resolve())
        offset = metadata["trim"]["ref_start_sec"] if raw else 0
        self.assertEqual(int(clips[0].findtext("in")), round((1 + offset) * rate))
        self.assertEqual(int(clips[1].findtext("in")), round((4 + offset) * rate))

        tracks = sequence.findall("media/audio/track")
        expected_camera_channels = sum(next(s["channels"] for s in probe(c["synced_path"])["streams"]
                                           if s["codec_type"] == "audio") for c in metadata["cameras"])
        expected_count = expected_camera_channels + (2 if master else 0)
        self.assertEqual(len(tracks), expected_count)
        self.assertEqual(sum(t.findtext("enabled") == "TRUE" for t in tracks), 2 if master else expected_count)
        for index, track in enumerate(tracks):
            spans = track.findall("clipitem")
            self.assertEqual(len(spans), 2, "Audio spans should join across camera cuts but preserve the removed interval")
            self.assertEqual([int(c.findtext("start")) for c in spans], [0, round(2 * rate)])
            for clip in spans:
                _, definition = definitions[clip.find("file").attrib["id"]]
                channels = int(definition.findtext("media/audio/channelcount"))
                self.assertLessEqual(int(clip.findtext("sourcetrack/trackindex")), channels)
                self.assertEqual(clip.findtext("enabled"), track.findtext("enabled"))
                gain = float(clip.findtext("filter/effect/parameter/value"))
                self.assertAlmostEqual(gain, 1.0 if master else 0.5)
            if master and index < 2:
                expected_in = round((1 + metadata["trim"]["ref_start_sec"] - metadata["master_audio"]["offset_sec"]) * rate)
                self.assertEqual(int(spans[0].findtext("in")), expected_in)

    def assert_picture_and_sound(self, movie, metadata):
        info = probe(movie)
        picture = next(s for s in info["streams"] if s["codec_type"] == "video")
        sound = next(s for s in info["streams"] if s["codec_type"] == "audio")
        self.assertEqual((picture["width"], picture["height"]), (320, 180))
        self.assertGreaterEqual(sound["channels"], 1)
        fps = metadata["video_format"]["fps"]
        expected_frames = sum(round(end * fps) - round(start * fps) for start, end in EDL_RANGES)
        self.assertEqual(int(picture["nb_frames"]), expected_frames,
                         "Every EDL frame must survive segment encoding and concatenation")
        expected = expected_frames / fps
        self.assertAlmostEqual(float(info["format"]["duration"]), expected, delta=0.09)
        self.assertGreater(movie.stat().st_size, 1000)
        for t, dominant in ((0.5, 0), (2.5, 2), (3.5, 0)):
            pixel = frame_rgb(movie, t)
            self.assertGreater(pixel[dominant], 150, f"Wrong camera or blank frame at {t}s: {pixel}")
            self.assertGreater(pixel[dominant] - pixel[2 if dominant == 0 else 0], 100)
        audio = decode_audio(movie)
        self.assertGreater(float(np.sqrt(np.mean(audio ** 2))), 0.005)
        self.assertAlmostEqual(len(audio) / SAMPLE_RATE, expected, delta=0.1)
        return audio

    def test_xml_25fps_no_master_stereo_links_and_gap(self):
        folder, metadata, edl = self.fixture("no_master_25")
        _, root = self.export(folder, edl)
        self.assertEqual(root.findtext("sequence/rate/timebase"), "25")
        self.assertEqual(root.findtext("sequence/rate/ntsc"), "FALSE")
        self.assert_linked_xml(root, metadata)

    def test_xml_ntsc_mixed_mono_stereo_and_raw_mapping(self):
        folder, metadata, edl = self.fixture("no_master_ntsc_mono", fps="30000/1001", channels=(1, 2))
        _, root = self.export(folder, edl)
        self.assertEqual(root.findtext("sequence/rate/timebase"), "30")
        self.assertEqual(root.findtext("sequence/rate/ntsc"), "TRUE")
        self.assert_linked_xml(root, metadata)
        _, raw_root = self.export(folder, edl, suffix="_raw", extra=("--use-raw-media",))
        self.assert_linked_xml(raw_root, metadata, raw=True)

    def test_no_master_mp4_keeps_both_cameras_audible_across_cuts(self):
        folder, metadata, edl = self.fixture("no_master_25")
        movie = self.render(folder, edl)
        audio = self.assert_picture_and_sound(movie, metadata)
        camera_audio = [decode_audio(c["synced_path"]) for c in metadata["cameras"]]
        expected = edit_audio((camera_audio[0] + camera_audio[1]) / 2, EDL_RANGES)
        self.assertGreater(correlation(audio, expected), 0.90, "Rendered audio is not the continuous camera mix with the EDL gap removed")
        # In the opening CAM1 picture, both unrelated camera signals must remain audible.
        first_second = slice(0, SAMPLE_RATE)
        for camera in camera_audio:
            contribution = edit_audio(camera, EDL_RANGES)
            self.assertGreater(correlation(audio[first_second], contribution[first_second]), 0.50)
        self.record("camera_mix_25fps", movie=str(movie), audio_correlation=correlation(audio, expected),
                    duration=float(probe(movie)["format"]["duration"]))

    def test_ntsc_mono_stereo_movie_audio_and_timing(self):
        folder, metadata, edl = self.fixture("no_master_ntsc_mono", fps="30000/1001", channels=(1, 2))
        movie = self.render(folder, edl)
        audio = self.assert_picture_and_sound(movie, metadata)
        camera_audio = [decode_audio(c["synced_path"]) for c in metadata["cameras"]]
        length = min(map(len, camera_audio))
        mixed = (camera_audio[0][:length] + camera_audio[1][:length]) / 2
        fps = metadata["video_format"]["fps"]
        quantized_ranges = [(round(start * fps) / fps, round(end * fps) / fps) for start, end in EDL_RANGES]
        expected = edit_audio(mixed, quantized_ranges)
        self.assertGreater(correlation(audio, expected), 0.90)
        self.record("camera_mix_ntsc_mono_stereo", movie=str(movie),
                    audio_correlation=correlation(audio, expected), duration=float(probe(movie)["format"]["duration"]))

    @unittest.skipUnless(sys.platform == "darwin", "VideoToolbox hardware regression requires macOS")
    def test_ntsc_videotoolbox_keeps_every_edl_frame(self):
        folder, metadata, edl = self.fixture("no_master_ntsc_mono", fps="30000/1001", channels=(1, 2))
        movie = self.render(folder, edl, suffix="_videotoolbox", extra=("--encoder", "h264_videotoolbox"))
        audio = self.assert_picture_and_sound(movie, metadata)
        picture = next(s for s in probe(movie)["streams"] if s["codec_type"] == "video")
        self.assertIn("h264_videotoolbox", picture.get("tags", {}).get("encoder", ""),
                      "Hardware regression must not silently pass through libx264 fallback")
        camera_audio = [decode_audio(c["synced_path"]) for c in metadata["cameras"]]
        length = min(map(len, camera_audio))
        mixed = (camera_audio[0][:length] + camera_audio[1][:length]) / 2
        fps = metadata["video_format"]["fps"]
        ranges = [(round(start * fps) / fps, round(end * fps) / fps) for start, end in EDL_RANGES]
        self.assertGreater(correlation(audio, edit_audio(mixed, ranges)), 0.90)

    def test_master_xml_six_tracks_and_mp4_uses_offset_master(self):
        folder, metadata, edl = self.fixture("with_master_25", master=True)
        _, root = self.export(folder, edl)
        self.assert_linked_xml(root, metadata, master=True)
        movie = self.render(folder, edl, extra=("--sync-json", folder / "multicam_sync.json"))
        audio = self.assert_picture_and_sound(movie, metadata)
        master = decode_audio(metadata["master_audio"]["path"])
        source_offset = metadata["trim"]["ref_start_sec"] - metadata["master_audio"]["offset_sec"]
        expected = edit_audio(master, EDL_RANGES, offset=source_offset)
        correct = correlation(audio, expected)
        self.assertGreater(correct, 0.90, "Master audio is not aligned to the trimmed camera timeline")
        self.assertLess(abs(correlation(audio, edit_audio(master, EDL_RANGES))), 0.20,
                        "Fixture must detect missing master offset compensation")
        camera_audio = [decode_audio(c["synced_path"]) for c in metadata["cameras"]]
        camera_mix = edit_audio((camera_audio[0] + camera_audio[1]) / 2, EDL_RANGES)
        self.assertLess(abs(correlation(audio, camera_mix)), 0.20,
                        "Master result should be distinguishable from camera-only sound")
        self.record("master_mix_25fps", movie=str(movie), master_correlation=correct,
                    camera_mix_correlation=correlation(audio, camera_mix),
                    wrong_offset_correlation=correlation(audio, edit_audio(master, EDL_RANGES)),
                    duration=float(probe(movie)["format"]["duration"]))

    def test_mkv_master_video_rate_does_not_change_audio_frame_coordinates(self):
        folder, metadata, edl = self.fixture("with_mkv_master_60fps", master=True)
        original_master = Path(metadata["master_audio"]["path"])
        mkv_master = folder / "external_master_60fps.mkv"
        # PCM avoids introducing an AAC priming offset in the test recording.
        # Only the container video clock differs from the camera/sequence clock.
        video(mkv_master, original_master, color="green", fps="60", duration=10.4,
              audio_codec="pcm_s16le")
        self.assertEqual(next(s["r_frame_rate"] for s in probe(mkv_master)["streams"]
                              if s["codec_type"] == "video"), "60/1")
        metadata["master_audio"]["path"] = str(mkv_master)
        (folder / "multicam_sync.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        _, root = self.export(folder, edl)
        self.assert_linked_xml(root, metadata, master=True)
        master_definition = next(node for node in root.findall(".//file")
                                 if node.findtext("name") == mkv_master.name)
        self.assertIsNone(master_definition.find("media/video"), "Master must be declared as audio-only even when its container has video")
        self.assertEqual(master_definition.findtext("rate/timebase"), "25")
        self.assertEqual(master_definition.findtext("rate/ntsc"), "FALSE")
        self.assertEqual(int(master_definition.findtext("duration")), 260)
        self.assertEqual(master_definition.findtext("timecode/string"), "00:00:00:00")
        self.assertEqual(master_definition.findtext("timecode/frame"), "0")
        movie = self.render(folder, edl)
        audio = self.assert_picture_and_sound(movie, metadata)
        source_offset = metadata["trim"]["ref_start_sec"] - metadata["master_audio"]["offset_sec"]
        expected = edit_audio(decode_audio(mkv_master), EDL_RANGES, offset=source_offset)
        self.assertGreater(correlation(audio, expected), 0.90)
        self.record("mkv_master_60fps_in_25fps_sequence", movie=str(movie),
                    master_correlation=correlation(audio, expected),
                    duration=float(probe(movie)["format"]["duration"]))

    def test_real_stage1_sync_with_and_without_optional_master(self):
        folder = self.output / "actual_acoustic_sync"
        folder.mkdir(exist_ok=True)
        common = signal(14, 314)
        ref_audio = np.column_stack((common[:12 * SAMPLE_RATE], common[:12 * SAMPLE_RATE] * 0.9))
        target_offset = 0.64
        target_audio = common[round(target_offset * SAMPLE_RATE):round((12 + target_offset) * SAMPLE_RATE)]
        ref_wav, target_wav, master_wav = folder / "reference.wav", folder / "target.wav", folder / "master.wav"
        write_wav(ref_wav, ref_audio)
        write_wav(target_wav, target_audio * 0.8)
        write_wav(master_wav, np.pad(ref_audio, ((round(0.36 * SAMPLE_RATE), SAMPLE_RATE), (0, 0))))
        ref_video, target_video = folder / "z_reference.mp4", folder / "a_target.mp4"
        video(ref_video, ref_wav, duration=12)
        video(target_video, target_wav, color="blue", duration=12)
        for with_master in (False, True):
            with self.subTest(with_master=with_master):
                output = folder / ("with_master" if with_master else "without_master")
                args = [sys.executable, SCRIPTS / "multicam_pipeline.py", "--ref", ref_video,
                        "--targets", target_video, "--ref-start", "1", "--ref-end", "10",
                        "--normalize", "--merge", "--strict-sync", "--full-scan", "--encoder", "libx264",
                        "--workers", "1", "--video-bitrate", "500k", "-o", output]
                if with_master:
                    args.extend(["--master-audio", master_wav])
                log = run(args)
                (output / "stage1-test.log").write_text(log, encoding="utf-8")
                metadata = json.loads((output / "multicam_sync.json").read_text())
                self.assertEqual(metadata["schema_version"], 2)
                self.assertAlmostEqual(metadata["cameras"][1]["offset_sec"], target_offset, delta=0.002)
                self.assertEqual([c["camera_id"] for c in metadata["cameras"]], ["CAM1", "CAM2"])
                for camera in metadata["cameras"]:
                    self.assertTrue(Path(camera["source_path"]).is_file())
                    self.assertTrue(Path(camera["synced_path"]).is_file())
                self.assertEqual(metadata["video_format"]["fps_num"], 25)
                self.assertAlmostEqual(metadata["trim"]["ref_start_sec"], 1)
                merged = output / "multicam_merged_full.mp4"
                self.assertTrue(merged.is_file())
                mixed = decode_audio(merged)
                if with_master:
                    self.assertAlmostEqual(metadata["master_audio"]["offset_sec"], -0.36, delta=0.002)
                    self.assertGreaterEqual(metadata["master_audio"]["peak_z_score"], 7)
                    expected = edit_audio(decode_audio(master_wav), [(1, 10)], offset=0.36)
                else:
                    self.assertIsNone(metadata["master_audio"])
                    cams = [decode_audio(c["synced_path"]) for c in metadata["cameras"]]
                    length = min(map(len, cams))
                    expected = (cams[0][:length] + cams[1][:length]) / 2
                self.assertGreater(correlation(mixed, expected), 0.85)
                self.record("actual_sync_with_master" if with_master else "actual_sync_without_master",
                            target_offset_sec=metadata["cameras"][1]["offset_sec"],
                            master_audio=metadata["master_audio"],
                            grid_audio_correlation=correlation(mixed, expected),
                            merged_video=str(merged))


if __name__ == "__main__":
    unittest.main()
