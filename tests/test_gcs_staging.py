"""Offline staging isolation and cleanup tests; never contact Google services."""

import contextlib
import io
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from modules import gcp_client, llm_client
import generate_edl


class MemoryStorage:
    """Model GCS generation preconditions, including replacement races."""

    def __init__(self):
        self.objects = {}
        self.uploads = []
        self.deletes = []
        self.next_generation = 1

    def bucket(self, bucket):
        return types.SimpleNamespace(blob=lambda name: MemoryBlob(self, (bucket, name)))


class MemoryBlob:
    def __init__(self, storage, key):
        self.storage, self.key = storage, key
        self.metadata = None
        self.generation = None
        self.size = None

    def reload(self):
        obj = self.storage.objects[self.key]
        self.generation = obj["generation"]
        self.size = len(obj["data"])
        self.metadata = dict(obj["metadata"])

    def upload_from_filename(self, path, *, content_type, timeout, if_generation_match):
        existing = self.storage.objects.get(self.key)
        actual = existing["generation"] if existing else 0
        if actual != if_generation_match:
            raise RuntimeError("412 generation precondition failed")
        self.generation = self.storage.next_generation
        self.storage.next_generation += 1
        self.storage.objects[self.key] = {
            "generation": self.generation,
            "data": Path(path).read_bytes(),
            "metadata": dict(self.metadata),
        }
        self.storage.uploads.append((self.key, if_generation_match))

    def delete(self, *, if_generation_match):
        self.storage.deletes.append((self.key, if_generation_match))
        if self.storage.objects[self.key]["generation"] != if_generation_match:
            raise RuntimeError("412 generation precondition failed")
        del self.storage.objects[self.key]


class StagingTestCase(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.temp = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.media = self.temp / "multicam_merged_full.mp4"
        self.media.write_bytes(b"same footage")
        self.storage = MemoryStorage()
        self.stack.enter_context(patch.object(gcp_client, "ensure_gcs_bucket"))
        self.stack.enter_context(patch.object(gcp_client, "get_gcs_storage_client", return_value=self.storage))
        self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        self.stack.enter_context(contextlib.redirect_stderr(io.StringIO()))

    def upload(self, job, **kwargs):
        return gcp_client.upload_file_to_gcs_with_cache(
            str(self.media), "bucket", project="project", staging_job=job, **kwargs
        )


class TestGcsStaging(StagingTestCase):
    def test_two_jobs_same_filename_and_content_are_isolated(self):
        first, second = gcp_client.GcsStagingJob(), gcp_client.GcsStagingJob()
        first_uri, second_uri = self.upload(first), self.upload(second)
        self.assertNotEqual(first_uri, second_uri)
        self.assertIn(f"/raw/{first.job_id}/", first_uri)
        self.assertEqual(len(self.storage.objects), 2)
        first.cleanup(project="project")
        self.assertNotIn(gcp_client.parse_gcs_uri(first_uri), self.storage.objects)
        self.assertIn(gcp_client.parse_gcs_uri(second_uri), self.storage.objects)
        self.assertEqual(len(self.storage.deletes), 1)

    def test_same_job_reuses_matching_upload_and_force_updates_ownership(self):
        job = gcp_client.GcsStagingJob()
        uri = self.upload(job)
        first_generation = job.generation_for(uri)
        self.assertEqual(self.upload(job), uri)
        self.assertEqual(len(self.storage.uploads), 1)
        self.assertEqual(self.upload(job, force_upload=True), uri)
        self.assertEqual(self.storage.uploads[-1][1], first_generation)
        new_generation = job.generation_for(uri)
        self.assertNotEqual(first_generation, new_generation)
        job.cleanup()
        self.assertEqual(self.storage.deletes[-1][1], new_generation)

    def test_external_uri_and_other_job_uri_cannot_be_deleted(self):
        job, other = gcp_client.GcsStagingJob(), gcp_client.GcsStagingJob()
        uri = self.upload(job)
        self.assertFalse(gcp_client.delete_gcs_blob(uri))
        self.assertFalse(gcp_client.delete_gcs_blob(uri, staging_job=other))
        self.assertEqual(self.storage.deletes, [])
        self.assertEqual(len(self.storage.objects), 1)

    def test_replacement_generation_is_neither_adopted_nor_deleted(self):
        job = gcp_client.GcsStagingJob()
        uri = self.upload(job)
        obj = self.storage.objects[gcp_client.parse_gcs_uri(uri)]
        owned_generation = obj["generation"]
        obj["generation"] += 100
        with self.assertRaisesRegex(RuntimeError, "412"):
            self.upload(job)
        self.assertEqual(job.generation_for(uri), owned_generation)
        job.cleanup()
        self.assertEqual(len(self.storage.objects), 1)
        self.assertEqual(self.storage.deletes[-1][1], owned_generation)

    def test_missing_upload_generation_fails_closed(self):
        job = gcp_client.GcsStagingJob()
        with patch.object(MemoryBlob, "upload_from_filename", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "no generation"):
                self.upload(job)
        job.cleanup()
        self.assertEqual(self.storage.deletes, [])

    def test_audio_namespace_keeps_raw_lifecycle_prefix(self):
        job = gcp_client.GcsStagingJob()
        uri = self.upload(job, gcs_prefix="raw/audio_chunks")
        self.assertIn(f"/raw/{job.job_id}/audio_chunks/", uri)

    def test_drive_cache_is_scoped_to_job_and_owns_uploaded_generation(self):
        first, second = gcp_client.GcsStagingJob(), gcp_client.GcsStagingJob()
        metadata = {"id": "drive-id", "name": self.media.name,
                    "size": self.media.stat().st_size, "md5Checksum": "drive-md5"}
        with patch.object(gcp_client, "get_gdrive_file_metadata", return_value=metadata), \
                patch.object(gcp_client, "download_gdrive_file_with_cache", return_value=str(self.media)) as download:
            first_uri, _ = gcp_client.transfer_gdrive_to_gcs_with_cache(
                "drive-id", "bucket", staging_job=first
            )
            cached_uri, _ = gcp_client.transfer_gdrive_to_gcs_with_cache(
                "drive-id", "bucket", staging_job=first
            )
            second_uri, _ = gcp_client.transfer_gdrive_to_gcs_with_cache(
                "drive-id", "bucket", staging_job=second
            )
        self.assertEqual(first_uri, cached_uri)
        self.assertNotEqual(first_uri, second_uri)
        self.assertEqual(download.call_count, 2)
        self.assertEqual(len(self.storage.uploads), 2)
        first.cleanup()
        self.assertIn(gcp_client.parse_gcs_uri(second_uri), self.storage.objects)


VALID_EDL = "```csv\nStart_Time,End_Time,Best_Camera,Reason\n00:00.000,00:05.000,CAM1,Speaker\n```"
INVALID_EDL = "```csv\nStart_Time,End_Time,Best_Camera,Reason\n00:05.000,00:00.000,CAM1,Speaker\n```"


class TestGeneratorStaging(StagingTestCase):
    def run_generator(self, video=None, cleanup=True, extra=(), response=VALID_EDL,
                      model_error=None, client_error=None, agentic_error=None):
        argv = ["generate_edl.py", "-v", str(video or self.media), "-o", str(self.temp),
                "--project", "project", "--gcs-bucket", "bucket", "--strict-edl"]
        if cleanup:
            argv.append("--cleanup-gcs")
        argv.extend(extra)
        with patch.object(sys, "argv", argv), \
                patch.object(generate_edl, "get_vertex_client", side_effect=client_error, return_value=Mock()), \
                patch.object(generate_edl, "load_prompt_template", return_value="Prompt"), \
                patch.object(generate_edl, "call_agentic_video_edl", side_effect=agentic_error,
                             return_value=(response, {}, 0.1)) as agentic, \
                patch.object(generate_edl, "generate_edl_content_standard", side_effect=model_error,
                             return_value=(response, {}, 0.1)) as standard:
            generate_edl.main()
        return agentic, standard

    def test_success_cleanup_and_outputs(self):
        self.run_generator()
        self.assertEqual(self.storage.objects, {})
        self.assertEqual(len(self.storage.uploads), 1)
        self.assertEqual(len(self.storage.deletes), 1)
        self.assertTrue((self.temp / "edl_full.csv").stat().st_size)
        self.assertTrue((self.temp / "edl_full_report.md").stat().st_size)

    def test_without_cleanup_leaves_job_object_for_lifecycle(self):
        self.run_generator(cleanup=False)
        self.assertEqual(len(self.storage.objects), 1)
        self.assertEqual(self.storage.deletes, [])

    def test_external_gcs_input_is_preserved_with_cleanup(self):
        self.run_generator(video="gs://bucket/raw/existing.mp4")
        self.assertEqual(self.storage.uploads, [])
        self.assertEqual(self.storage.deletes, [])

    def test_agentic_fallback_reuses_one_upload(self):
        agentic, standard = self.run_generator(agentic_error=RuntimeError("unsupported"))
        self.assertEqual(agentic.call_args.args[0], standard.call_args.args[0])
        self.assertEqual(len(self.storage.uploads), 1)
        self.assertEqual(self.storage.objects, {})

    def test_model_failure_cleans_owned_upload(self):
        with self.assertRaisesRegex(RuntimeError, "failed"):
            self.run_generator(extra=("--processing", "standard"), model_error=RuntimeError("failed"))
        self.assertEqual(self.storage.objects, {})
        self.assertEqual(len(self.storage.deletes), 1)

    def test_client_initialization_failure_also_cleans_upload(self):
        with self.assertRaisesRegex(RuntimeError, "client unavailable"):
            self.run_generator(client_error=RuntimeError("client unavailable"))
        self.assertEqual(self.storage.objects, {})
        self.assertEqual(len(self.storage.deletes), 1)

    def test_strict_validation_writes_evidence_then_cleans(self):
        with self.assertRaises(SystemExit) as stopped:
            self.run_generator(response=INVALID_EDL)
        self.assertEqual(stopped.exception.code, 1)
        self.assertTrue((self.temp / "edl_full.csv").stat().st_size)
        self.assertTrue((self.temp / "edl_full_report.md").stat().st_size)
        self.assertEqual(self.storage.objects, {})

    def test_drive_input_uses_job_scope_and_valid_force_keyword(self):
        metadata = {"id": "drive-id", "name": self.media.name,
                    "size": self.media.stat().st_size, "md5Checksum": "drive-md5"}
        with patch.object(gcp_client, "get_gdrive_file_metadata", return_value=metadata), \
                patch.object(gcp_client, "download_gdrive_file_with_cache", return_value=str(self.media)):
            self.run_generator(video="gdrive://drive-id", extra=("--force-upload",))
        self.assertEqual(len(self.storage.uploads), 1)
        self.assertEqual(len(self.storage.deletes), 1)
        self.assertEqual(self.storage.objects, {})


class TestLlmStaging(StagingTestCase):
    def setUp(self):
        super().setUp()
        google_module, genai_module = types.ModuleType("google"), types.ModuleType("google.genai")
        genai_module.types = types.SimpleNamespace(
            Part=types.SimpleNamespace(from_uri=lambda **kwargs: kwargs),
            GenerateContentConfig=lambda **kwargs: kwargs,
        )
        google_module.genai = genai_module
        self.stack.enter_context(patch.dict(sys.modules, {"google": google_module, "google.genai": genai_module}))
        self.client = Mock()
        self.stack.enter_context(patch.object(llm_client, "get_vertex_client", return_value=self.client))
        self.stack.enter_context(patch.object(llm_client.time, "sleep"))

    def call(self, **kwargs):
        return llm_client.call_vertex_generate_content(
            "Prompt", project="project", gcs_bucket="bucket", **kwargs
        )

    def test_model_retry_reuses_uri_and_cleans_exact_generation(self):
        self.client.models.generate_content.side_effect = [RuntimeError("429"), types.SimpleNamespace(text="OK")]
        self.assertEqual(self.call(audio_path=str(self.media)), "OK")
        calls = self.client.models.generate_content.call_args_list
        self.assertEqual(calls[0].kwargs["contents"], calls[1].kwargs["contents"])
        self.assertEqual(len(self.storage.uploads), 1)
        self.assertEqual(len(self.storage.deletes), 1)
        self.assertEqual(self.storage.objects, {})

    def test_model_failure_cleans_audio(self):
        self.client.models.generate_content.side_effect = RuntimeError("403 denied")
        with self.assertRaisesRegex(RuntimeError, "403"):
            self.call(audio_path=str(self.media))
        self.assertEqual(self.storage.objects, {})
        self.assertEqual(len(self.storage.deletes), 1)

    def test_external_uri_is_preserved(self):
        self.client.models.generate_content.return_value = types.SimpleNamespace(text="OK")
        self.call(gcs_uri="gs://bucket/shared.wav")
        self.assertEqual(self.storage.uploads, [])
        self.assertEqual(self.storage.deletes, [])


if __name__ == "__main__":
    unittest.main()
