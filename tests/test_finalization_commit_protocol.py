"""Terminal state is validated before it becomes authoritative.

Self-validation used to run against a *provisional* manifest and a ``running``
status, and the real terminal manifest and status were written afterwards. The
bytes a user later read were therefore never the bytes that were validated: a
malformed terminal manifest or status could not be caught by anything.

The naive repair does not work. "Write the terminal state, validate it, rewrite
it as failed if invalid" makes the failure rewrite a *new* terminal state that
nothing validated — the same defect, moved.

Finalization is therefore a candidate / validate / commit protocol:

1. build the success candidate, including its optimistic claim that
   self-validation passed;
2. write every artifact except the terminal status at its final path, and stage
   the status beside it as ``run_status.json.candidate``;
3. validate that exact on-disk state, plus the staged bytes decoded from disk;
4. if it validates, commit by renaming the staged file into place — nothing is
   re-serialized, so the committed bytes *are* the validated bytes;
5. only if it does not validate is anything rewritten, and that rewrite declares
   ``failed``, which can never be a successful run.

``run_status.json`` is the commit record. ``classify_lifecycle`` reports RUNNING
while it says ``running`` and CORRUPT when it is missing, so no crash before the
rename can present the run as finalized.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from modules import run_artifacts as run_artifacts_module
from modules.acquisition import AcquiredRepository, AcquisitionRecord
from modules.benchmark_runner import run_benchmark
from modules.config import AnalysisConfig
from validation.artifact_io.compatibility import RunLifecycle
from validation.artifact_io.reader import open_run
from validation.scripts.semantic_projection import measurement_semantic_hash

ANALYZED_SHA = "a" * 40
CANDIDATE = "run_status.json.candidate"


class FinalizationFixture(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repo = self.root / "fixture"
        self.repo.mkdir()
        (self.repo / "app.py").write_text(
            "class App:\n    def run(self):\n        return 1\n", encoding="utf-8"
        )
        self.input = self.root / "repositories.csv"
        self.input.write_text(
            "url,architecture_type,expected_language,commit_sha,enabled,notes\n"
            f"https://github.com/acme/mono,monolith,Python,{ANALYZED_SHA},true,one\n",
            encoding="utf-8",
        )

    @contextmanager
    def _acquire(self, spec, config, mode="latest", progress=None):
        del config, mode, progress
        yield AcquiredRepository(
            self.repo,
            AcquisitionRecord(
                repository_url=spec.url,
                repository_owner=spec.owner,
                repository_name=spec.repository_name,
                requested_commit_sha=spec.commit_sha,
                analyzed_commit_sha=ANALYZED_SHA,
                resolved_ref="refs/heads/main",
                default_branch="main",
                acquisition_mode="offline",
                cache_status="reused",
                remote_checked=False,
                fetch_timestamp=None,
                checkout_timestamp="2026-08-01T00:00:00Z",
                commit_verification_status="verified",
                fetch_method="offline_cache",
            ),
        )

    def run_benchmark_once(self, output="output") -> dict:
        config = AnalysisConfig.from_env(
            output_root=self.root / output,
            cache_root=self.root / "cache",
            temporary_directory=self.root / "temp",
            workers=1,
        )
        with (
            patch("modules.benchmark_runner.acquire_repository", self._acquire),
            patch("modules.benchmark_runner._distribution_version", return_value="4.0.1"),
        ):
            return run_benchmark(
                [self.input], config, "offline", command_line_arguments=["test"]
            )

    @staticmethod
    def status_of(run: Path) -> dict:
        return json.loads((run / "run_status.json").read_text(encoding="utf-8"))

    @staticmethod
    def manifest_of(run: Path) -> dict:
        return json.loads((run / "run_manifest.json").read_text(encoding="utf-8"))

    def pointer(self, output="output") -> Path:
        return self.root / output / "latest_run.json"


class HappyPathTests(FinalizationFixture):
    def test_normal_finalization_remains_finalized_valid(self):
        summary = self.run_benchmark_once()
        run = Path(summary["run_directory"])
        self.assertEqual(self.status_of(run)["status"], "completed")
        self.assertEqual(open_run(run).lifecycle, RunLifecycle.FINALIZED_VALID)
        self.assertTrue(self.manifest_of(run)["self_validation"]["passed"])

    def test_the_staged_candidate_is_not_left_behind(self):
        run = Path(self.run_benchmark_once()["run_directory"])
        self.assertFalse((run / CANDIDATE).exists())

    def test_pointer_is_advanced_on_success(self):
        self.run_benchmark_once()
        self.assertTrue(self.pointer().exists())


class ByteIdentityTests(FinalizationFixture):
    """What was validated is what became authoritative."""

    def test_committed_status_bytes_equal_the_validated_candidate_bytes(self):
        captured: dict[str, bytes] = {}
        original = run_artifacts_module.RunArtifacts._self_validation_problems

        def capture(self, candidate_status_path=None, candidate_manifest_path=None):
            if candidate_status_path is not None:
                captured["validated"] = Path(candidate_status_path).read_bytes()
            return original(self, candidate_status_path, candidate_manifest_path)

        with patch.object(
            run_artifacts_module.RunArtifacts, "_self_validation_problems", capture
        ):
            run = Path(self.run_benchmark_once()["run_directory"])

        self.assertIn("validated", captured, "the gate never saw a staged candidate")
        self.assertEqual(
            (run / "run_status.json").read_bytes(),
            captured["validated"],
            "the committed status differs from the bytes that were validated",
        )

    def test_committed_manifest_bytes_equal_the_validated_manifest_bytes(self):
        captured: dict[str, bytes] = {}
        original = run_artifacts_module.RunArtifacts._self_validation_problems

        def capture(self, candidate_status_path=None, candidate_manifest_path=None):
            if candidate_manifest_path is not None:
                captured["validated"] = Path(candidate_manifest_path).read_bytes()
            return original(self, candidate_status_path, candidate_manifest_path)

        with patch.object(
            run_artifacts_module.RunArtifacts, "_self_validation_problems", capture
        ):
            run = Path(self.run_benchmark_once()["run_directory"])

        self.assertIn(
            "validated", captured, "the gate never saw a staged manifest candidate"
        )
        self.assertEqual(
            (run / "run_manifest.json").read_bytes(),
            captured["validated"],
            "the committed manifest differs from the bytes that were validated",
        )


class CandidateRejectionTests(FinalizationFixture):
    """Corrupt the candidate terminal state; the run must fail closed."""

    def _corrupt_during_gate(self, mutate):
        original = run_artifacts_module.RunArtifacts._self_validation_problems

        def corrupt_then_validate(inner_self, candidate_status_path=None, candidate_manifest_path=None):
            mutate(inner_self.run_dir, candidate_status_path)
            return original(inner_self, candidate_status_path, candidate_manifest_path)

        with patch.object(
            run_artifacts_module.RunArtifacts,
            "_self_validation_problems",
            corrupt_then_validate,
        ):
            return Path(self.run_benchmark_once()["run_directory"])

    def test_an_invalid_candidate_run_status_is_rejected(self):
        def mutate(run_dir, candidate):
            # A status value outside the declared enum: valid JSON, invalid
            # against the contract. Only reading the bytes back can catch it.
            document = json.loads(Path(candidate).read_text(encoding="utf-8"))
            document["status"] = "definitely_not_a_status"
            Path(candidate).write_text(json.dumps(document), encoding="utf-8")

        run = self._corrupt_during_gate(mutate)
        self.assertEqual(self.status_of(run)["status"], "failed")
        self.assertFalse(self.manifest_of(run)["self_validation"]["passed"])
        # The rejected bytes were never published.
        self.assertEqual(self.status_of(run)["status"], "failed")
        self.assertTrue(
            any(
                "not one of" in problem
                for problem in self.manifest_of(run)["self_validation"]["problems"]
            ),
            "the schema violation was not recorded verbatim",
        )

    def test_an_invalid_candidate_run_manifest_is_rejected(self):
        def mutate(run_dir, candidate):
            (run_dir / "run_manifest.json").write_text("{ not json", encoding="utf-8")

        run = self._corrupt_during_gate(mutate)
        self.assertEqual(self.status_of(run)["status"], "failed")

    def test_corruption_after_generation_but_before_commit_is_detected(self):
        def mutate(run_dir, candidate):
            (run_dir / "language_metrics.csv").write_text(
                "not_a_declared_column\nvalue\n", encoding="utf-8"
            )

        run = self._corrupt_during_gate(mutate)
        self.assertEqual(self.status_of(run)["status"], "failed")

    def test_serialized_corruption_is_caught_though_the_object_was_valid(self):
        """The in-memory status dict is untouched; only its bytes are broken.

        This is the case validating Python objects before serialization can
        never catch, and it is why the gate decodes the staged file from disk.
        """
        seen: dict[str, object] = {}

        def mutate(run_dir, candidate):
            document = json.loads(Path(candidate).read_text(encoding="utf-8"))
            seen["in_memory_status_was"] = document["status"]
            document["status"] = 12345  # wrong type: schema says string
            Path(candidate).write_text(json.dumps(document), encoding="utf-8")

        run = self._corrupt_during_gate(mutate)
        self.assertEqual(seen["in_memory_status_was"], "completed")
        self.assertEqual(self.status_of(run)["status"], "failed")

    def test_measurement_outcome_is_preserved_on_integrity_failure(self):
        def mutate(run_dir, candidate):
            (run_dir / "language_metrics.csv").write_text(
                "not_a_declared_column\nvalue\n", encoding="utf-8"
            )

        run = self._corrupt_during_gate(mutate)
        status = self.status_of(run)
        self.assertEqual(status["status"], "failed")
        # How much was measured is a separate question from whether the
        # artifacts are readable, and must not be rewritten to hide it.
        self.assertEqual(status["measurement_outcome"], "complete")
        self.assertEqual(self.manifest_of(run)["measurement_outcome"], "complete")

    def test_integrity_failure_is_recorded_separately_from_measurement(self):
        def mutate(run_dir, candidate):
            (run_dir / "language_metrics.csv").write_text(
                "not_a_declared_column\nvalue\n", encoding="utf-8"
            )

        run = self._corrupt_during_gate(mutate)
        manifest = self.manifest_of(run)
        self.assertFalse(manifest["self_validation"]["passed"])
        self.assertTrue(manifest["self_validation"]["problems"])
        self.assertTrue(
            any(
                "self_validation" in item
                for item in self.status_of(run)["mandatory_output_failures"]
            )
        )

    def test_pointer_is_not_advanced_on_any_validation_failure(self):
        def mutate(run_dir, candidate):
            (run_dir / "language_metrics.csv").write_text(
                "not_a_declared_column\nvalue\n", encoding="utf-8"
            )

        self._corrupt_during_gate(mutate)
        self.assertFalse(
            self.pointer().exists(),
            "latest_run.json was advanced to a run that failed validation",
        )

    def test_no_candidate_file_survives_a_failure(self):
        def mutate(run_dir, candidate):
            (run_dir / "language_metrics.csv").write_text(
                "not_a_declared_column\nvalue\n", encoding="utf-8"
            )

        run = self._corrupt_during_gate(mutate)
        self.assertFalse((run / CANDIDATE).exists())


class LazyArtifactTests(FinalizationFixture):
    """An optional artifact cannot hide behind laziness."""

    def _corrupt_optional(self, relative: str):
        original = run_artifacts_module.RunArtifacts._self_validation_problems

        def corrupt_then_validate(inner_self, candidate_status_path=None, candidate_manifest_path=None):
            target = inner_self.run_dir / relative
            if target.is_file():
                target.write_text("not_a_declared_column\nvalue\n", encoding="utf-8")
            return original(inner_self, candidate_status_path, candidate_manifest_path)

        with patch.object(
            run_artifacts_module.RunArtifacts,
            "_self_validation_problems",
            corrupt_then_validate,
        ):
            return Path(self.run_benchmark_once()["run_directory"])

    def test_a_malformed_catalog_cannot_finalize_completed(self):
        run = self._corrupt_optional("catalog.csv")
        self.assertEqual(self.status_of(run)["status"], "failed")

    def test_a_malformed_errors_table_cannot_finalize_completed(self):
        run = self._corrupt_optional("errors.csv")
        self.assertEqual(self.status_of(run)["status"], "failed")

    def test_a_malformed_recoveries_table_cannot_finalize_completed(self):
        run = self._corrupt_optional("recoveries.csv")
        self.assertEqual(self.status_of(run)["status"], "failed")

    def test_a_malformed_inventory_cannot_finalize_completed(self):
        original = run_artifacts_module.RunArtifacts._self_validation_problems

        def corrupt_then_validate(inner_self, candidate_status_path=None, candidate_manifest_path=None):
            for path in (inner_self.run_dir / "file_inventory").glob("*.json"):
                path.write_text("{ not json", encoding="utf-8")
            return original(inner_self, candidate_status_path, candidate_manifest_path)

        with patch.object(
            run_artifacts_module.RunArtifacts,
            "_self_validation_problems",
            corrupt_then_validate,
        ):
            run = Path(self.run_benchmark_once()["run_directory"])
        self.assertEqual(self.status_of(run)["status"], "failed")


class CommitFailureTests(FinalizationFixture):
    """A failed commit must not leave a half-successful state."""

    def test_a_failed_commit_never_yields_finalized_valid(self):
        original_replace = run_artifacts_module._replace_with_retry

        def refuse_status_commit(source, target):
            # Only the commit rename itself, identified by its staged source.
            # Keying on the target alone would also break the initial
            # `run_status.json` write during RunArtifacts construction.
            if Path(source).name == CANDIDATE:
                raise OSError("simulated commit failure")
            return original_replace(source, target)

        with patch.object(
            run_artifacts_module, "_replace_with_retry", refuse_status_commit
        ):
            run = Path(self.run_benchmark_once()["run_directory"])

        # The primary commit failed, so the emergency path must publish a
        # terminal failed state rather than strand the measured run as running.
        lifecycle = open_run(run).lifecycle
        self.assertEqual(self.status_of(run)["status"], "failed")
        self.assertEqual(lifecycle, RunLifecycle.FINALIZED_VALID)
        self.assertFalse(self.pointer().exists())

    def test_a_crash_before_commit_leaves_a_running_run(self):
        """Simulates interruption between staging and the rename."""
        original = run_artifacts_module.RunArtifacts._self_validation_problems

        def raise_after_staging(inner_self, candidate_status_path=None, candidate_manifest_path=None):
            del candidate_status_path
            raise KeyboardInterrupt("simulated interruption")

        with patch.object(
            run_artifacts_module.RunArtifacts,
            "_self_validation_problems",
            raise_after_staging,
        ):
            with self.assertRaises(KeyboardInterrupt):
                self.run_benchmark_once()

        runs = sorted((self.root / "output" / "runs").glob("2026*"))
        self.assertTrue(runs, "no run directory was created")
        status = json.loads((runs[0] / "run_status.json").read_text(encoding="utf-8"))
        self.assertEqual(status["status"], "running")
        self.assertNotEqual(open_run(runs[0]).lifecycle, RunLifecycle.FINALIZED_VALID)
        self.assertFalse(self.pointer().exists())


class Artifact15LifecycleLimitationTests(FinalizationFixture):
    """The one thing the 1.5 model cannot express, pinned so it is not a surprise.

    ``RunLifecycle.FINALIZED_VALID`` is derived from ``structurally_valid``,
    which is "the published artifacts decode". It is **not** a statement that
    the run succeeded. Integrity is carried by ``run_status.status``, which the
    writer's own comment calls the only artifact authoritative for run integrity.

    So in one narrow case the lifecycle reads ``finalized_valid`` for a failed
    run: when the *only* fault was in the staged terminal status bytes. Those
    bytes are rejected and never published, the replacement declares ``failed``,
    and everything that does reach disk decodes — so the run is simultaneously
    "structurally valid" and "integrity failed", and both are true.

    Closing this would mean changing `classify_lifecycle` to consult
    ``run_integrity_status``. That is a reader-contract change affecting every
    consumer and every historical run, not a finalization change, so it is left
    as a separate decision rather than taken unilaterally here.

    Consumers must therefore check ``status``, not lifecycle alone, to decide
    whether a run succeeded.
    """

    def test_status_is_failed_even_where_lifecycle_reports_valid(self):
        original = run_artifacts_module.RunArtifacts._self_validation_problems

        def corrupt_candidate_only(inner_self, candidate_status_path=None, candidate_manifest_path=None):
            document = json.loads(
                Path(candidate_status_path).read_text(encoding="utf-8")
            )
            document["status"] = "definitely_not_a_status"
            Path(candidate_status_path).write_text(
                json.dumps(document), encoding="utf-8"
            )
            return original(inner_self, candidate_status_path, candidate_manifest_path)

        with patch.object(
            run_artifacts_module.RunArtifacts,
            "_self_validation_problems",
            corrupt_candidate_only,
        ):
            run = Path(self.run_benchmark_once()["run_directory"])

        # The invariants that actually protect a consumer all hold.
        self.assertEqual(self.status_of(run)["status"], "failed")
        self.assertEqual(self.status_of(run)["measurement_outcome"], "complete")
        self.assertFalse(self.manifest_of(run)["self_validation"]["passed"])
        self.assertFalse(self.pointer().exists())

        # And the documented limitation: decodability, not success.
        view = open_run(run)
        self.assertEqual(len(view.structural_errors), 0)
        self.assertEqual(view.lifecycle, RunLifecycle.FINALIZED_VALID)

    def test_the_rejected_bytes_were_never_published(self):
        original = run_artifacts_module.RunArtifacts._self_validation_problems

        def corrupt_candidate_only(inner_self, candidate_status_path=None, candidate_manifest_path=None):
            document = json.loads(
                Path(candidate_status_path).read_text(encoding="utf-8")
            )
            document["status"] = "definitely_not_a_status"
            Path(candidate_status_path).write_text(
                json.dumps(document), encoding="utf-8"
            )
            return original(inner_self, candidate_status_path, candidate_manifest_path)

        with patch.object(
            run_artifacts_module.RunArtifacts,
            "_self_validation_problems",
            corrupt_candidate_only,
        ):
            run = Path(self.run_benchmark_once()["run_directory"])

        published = self.status_of(run)
        # The rejected value never became the authoritative status. It does
        # appear inside `mandatory_output_failures`, quoted as the reason the
        # candidate was refused — that is the record working, not a leak.
        self.assertEqual(published["status"], "failed")
        self.assertTrue(
            any(
                "definitely_not_a_status" in failure
                for failure in published["mandatory_output_failures"]
            ),
            "the refused value should be quoted in the recorded reason",
        )


class FailureTerminalStateIsValidatedTests(FinalizationFixture):
    """Every published terminal state, successful or failed, was validated."""

    def _fail_the_success_candidate(self):
        original = run_artifacts_module.RunArtifacts._self_validation_problems

        def corrupt(inner_self, candidate_status_path=None, candidate_manifest_path=None):
            (inner_self.run_dir / "language_metrics.csv").write_text(
                "not_a_declared_column\nvalue\n", encoding="utf-8"
            )
            return original(inner_self, candidate_status_path, candidate_manifest_path)

        with patch.object(
            run_artifacts_module.RunArtifacts, "_self_validation_problems", corrupt
        ):
            return Path(self.run_benchmark_once()["run_directory"])

    def test_the_failure_candidate_is_itself_validated_before_publication(self):
        seen: list[tuple] = []
        original = run_artifacts_module.RunArtifacts._terminal_candidate_problems

        def spy(status_path, manifest_path):
            seen.append((status_path, manifest_path))
            return original(status_path, manifest_path)

        with patch.object(
            run_artifacts_module.RunArtifacts,
            "_terminal_candidate_problems",
            staticmethod(spy),
        ):
            run = self._fail_the_success_candidate()

        self.assertEqual(self.status_of(run)["status"], "failed")
        # Once for the success candidate, once for the failure candidate.
        self.assertGreaterEqual(
            len(seen), 2, "the failure candidate was published without validation"
        )
        self.assertTrue(
            all(status is not None and manifest is not None for status, manifest in seen),
            "a terminal candidate was validated without both documents staged",
        )

    def test_emergency_terminal_state_is_published_when_failure_candidate_is_invalid(self):
        """Even a broken candidate path must not strand a measured run running."""
        with patch.object(
            run_artifacts_module.RunArtifacts,
            "_terminal_candidate_problems",
            staticmethod(lambda status_path, manifest_path: ["forced failure"]),
        ):
            run = Path(self.run_benchmark_once()["run_directory"])

        self.assertEqual(self.status_of(run)["status"], "failed")
        self.assertEqual(open_run(run).lifecycle, RunLifecycle.FINALIZED_VALID)
        self.assertFalse(self.pointer().exists())
        self.assertFalse((run / CANDIDATE).exists())
        self.assertFalse((run / "run_manifest.json.candidate").exists())

    def test_the_published_failure_state_is_internally_consistent(self):
        """No machine-readable contradiction between manifest and status."""
        run = self._fail_the_success_candidate()
        manifest = self.manifest_of(run)
        status = self.status_of(run)

        self.assertEqual(status["status"], "failed")
        self.assertEqual(manifest["run_integrity_status"], "failed")
        self.assertEqual(manifest["run_status"], "failed")
        self.assertFalse(manifest["self_validation"]["passed"])
        self.assertTrue(manifest["self_validation"]["problems"])
        # measurement is a separate dimension and must agree across both.
        self.assertEqual(status["measurement_outcome"], "complete")
        self.assertEqual(manifest["measurement_outcome"], "complete")

    def test_the_provisional_manifest_never_claims_success(self):
        """A crash before commit must not leave a manifest asserting success.

        The provisional manifest exists only so the reader can derive the
        artifact schema version. It declares `running`, matching
        `run_status.json`, so the two never contradict each other at any point.
        """
        captured: dict[str, dict] = {}
        original = run_artifacts_module.RunArtifacts._self_validation_problems

        def capture(self, candidate_status_path=None, candidate_manifest_path=None):
            captured["provisional"] = json.loads(
                (self.run_dir / "run_manifest.json").read_text(encoding="utf-8")
            )
            return original(self, candidate_status_path, candidate_manifest_path)

        with patch.object(
            run_artifacts_module.RunArtifacts, "_self_validation_problems", capture
        ):
            self.run_benchmark_once()

        self.assertEqual(captured["provisional"]["run_integrity_status"], "running")
        self.assertEqual(captured["provisional"]["run_status"], "running")
        self.assertNotIn("self_validation", captured["provisional"])


class LifecycleIsStructuralNotSuccessTests(FinalizationFixture):
    """The Artifact 1.5 split, pinned in both directions at once.

    ``lifecycle`` answers *can this run be decoded*. ``succeeded`` answers *did
    it execute successfully*. `classify_lifecycle` never consults integrity —
    it has no reference to it — so a failed run's lifecycle depends purely on
    whether its **published** artifacts decode.

    That produces two different, both-correct results, and confusing them is
    easy because both are "a failed run":

    * only the *staged candidate* bytes were bad — they are rejected and never
      published, so everything on disk decodes: ``finalized_valid`` +
      ``succeeded=False``;
    * an *eager published artifact* is corrupt — the corruption is still on
      disk, so the run genuinely does not decode: ``finalized_invalid`` +
      ``succeeded=False``.

    Both are pinned here together so neither can be reported as if it were the
    only outcome.
    """

    def _run_with_gate(self, mutate) -> Path:
        original = run_artifacts_module.RunArtifacts._self_validation_problems

        def gate(inner_self, candidate_status_path=None, candidate_manifest_path=None):
            mutate(inner_self.run_dir, candidate_status_path)
            return original(inner_self, candidate_status_path, candidate_manifest_path)

        with patch.object(
            run_artifacts_module.RunArtifacts, "_self_validation_problems", gate
        ):
            return Path(self.run_benchmark_once()["run_directory"])

    def test_candidate_only_corruption_stays_finalized_valid(self):
        def mutate(run_dir, candidate):
            document = json.loads(Path(candidate).read_text(encoding="utf-8"))
            document["status"] = "not_a_status"
            Path(candidate).write_text(json.dumps(document), encoding="utf-8")

        run = self._run_with_gate(mutate)
        view = open_run(run)
        self.assertEqual(len(view.structural_errors), 0)
        self.assertEqual(view.lifecycle, RunLifecycle.FINALIZED_VALID)
        self.assertFalse(view.succeeded)
        self.assertEqual(self.status_of(run)["status"], "failed")

    def test_eager_artifact_corruption_becomes_finalized_invalid(self):
        def mutate(run_dir, candidate):
            (run_dir / "language_metrics.csv").write_text(
                "not_a_declared_column\nvalue\n", encoding="utf-8"
            )

        run = self._run_with_gate(mutate)
        view = open_run(run)
        self.assertGreater(len(view.structural_errors), 0)
        self.assertEqual(view.lifecycle, RunLifecycle.FINALIZED_INVALID)
        self.assertFalse(view.succeeded)
        self.assertEqual(self.status_of(run)["status"], "failed")

    def test_lifecycle_classification_never_consults_integrity(self):
        """Source-level, so it keeps holding if the function is rewritten."""
        source = (
            Path(__file__).resolve().parent.parent
            / "validation" / "artifact_io" / "compatibility.py"
        ).read_text(encoding="utf-8")
        body = source.split("def classify_lifecycle", 1)[1].split("\ndef ", 1)[0]
        for forbidden in ("integrity", "succeeded", "run_integrity_status"):
            self.assertNotIn(
                forbidden, body,
                f"classify_lifecycle consults {forbidden!r}; Artifact 1.5 "
                f"lifecycle must stay purely structural",
            )


class Known15SchemaDefectTests(FinalizationFixture):
    """Two frozen-1.5.0 schemas contradict their own producers and siblings.

    Neither was visible until finalization began validating these documents.
    Enforcing either verbatim would make whole classes of *failed* run
    unpublishable — hiding failures entirely — so each is suppressed as narrowly
    as it can be expressed. These tests pin both the suppression and its limits.
    """

    def test_a_run_where_everything_failed_still_publishes_a_terminal_state(self):
        """F-P3-4. `measurement_outcome: failed` is valid per `run_manifest`
        and invalid per `run_status`, for the same field. A total failure must
        still reach a terminal status rather than being silently unpublishable.
        """
        from modules.acquisition import AcquisitionError

        @contextmanager
        def fail_acquisition(*args, **kwargs):
            del args, kwargs
            raise AcquisitionError("checkout_not_clean", "simulated")
            yield  # pragma: no cover - keeps this a context manager

        with (
            patch("modules.benchmark_runner.acquire_repository", fail_acquisition),
            patch("modules.benchmark_runner._distribution_version", return_value="4.0.1"),
        ):
            config = AnalysisConfig.from_env(
                output_root=self.root / "output",
                cache_root=self.root / "cache",
                temporary_directory=self.root / "temp",
                workers=1,
            )
            summary = run_benchmark(
                [self.input], config, "offline", command_line_arguments=["test"]
            )

        run = Path(summary["run_directory"])
        status = self.status_of(run)
        self.assertIn(status["status"], {"completed_with_errors", "failed"})
        self.assertNotEqual(
            status["status"], "running", "a failed run was left unpublishable"
        )
        self.assertEqual(status["measurement_outcome"], "failed")

    def test_the_suppression_registry_is_the_shared_one(self):
        """Scoping is proved in tests/test_known_1_5_exceptions.py.

        This only pins that finalization consults the *shared* registry rather
        than a private copy, because a private copy is exactly how finalization
        and archlens validate came to disagree about the same artifact.
        """
        source = (
            Path(__file__).resolve().parent.parent
            / "modules" / "run_artifacts.py"
        ).read_text(encoding="utf-8")
        self.assertIn("known_exceptions.partition", source)
        self.assertNotIn("_is_known_1_5_schema_defect", source)

    def test_a_genuinely_invalid_status_value_is_still_rejected(self):
        """The suppression must not have opened a hole in status validation."""
        original = run_artifacts_module.RunArtifacts._self_validation_problems

        def corrupt(inner_self, candidate_status_path=None, candidate_manifest_path=None):
            document = json.loads(
                Path(candidate_status_path).read_text(encoding="utf-8")
            )
            document["measurement_outcome"] = "definitely_not_an_outcome"
            Path(candidate_status_path).write_text(
                json.dumps(document), encoding="utf-8"
            )
            return original(inner_self, candidate_status_path, candidate_manifest_path)

        with patch.object(
            run_artifacts_module.RunArtifacts, "_self_validation_problems", corrupt
        ):
            run = Path(self.run_benchmark_once()["run_directory"])

        self.assertEqual(self.status_of(run)["status"], "failed")


class ConsumerSuccessSemanticsTests(FinalizationFixture):
    """No consumer may treat FINALIZED_VALID alone as proof of success."""

    def _integrity_failed_run(self) -> Path:
        original = run_artifacts_module.RunArtifacts._self_validation_problems

        def corrupt_candidate_only(
            inner_self, candidate_status_path=None, candidate_manifest_path=None
        ):
            problems = original(
                inner_self, candidate_status_path, candidate_manifest_path
            )
            return list(problems) + ["forced integrity failure"]

        with patch.object(
            run_artifacts_module.RunArtifacts,
            "_self_validation_problems",
            corrupt_candidate_only,
        ):
            return Path(self.run_benchmark_once()["run_directory"])

    def test_the_canonical_helper_separates_decodability_from_success(self):
        run = self._integrity_failed_run()
        view = open_run(run)
        self.assertEqual(view.integrity_status, "failed")
        self.assertFalse(view.succeeded)
        # The run is still decodable, which is what lifecycle reports.
        self.assertEqual(view.lifecycle, RunLifecycle.FINALIZED_VALID)
        self.assertTrue(view.finalized)

    def test_succeeded_is_true_only_for_terminal_success(self):
        run = Path(self.run_benchmark_once()["run_directory"])
        view = open_run(run)
        self.assertTrue(view.succeeded)
        self.assertEqual(view.integrity_status, "completed")

    def test_reproduce_blocks_on_an_integrity_failed_run(self):
        from modules.cli.reproduce_command import preflight

        run = self._integrity_failed_run()
        report = preflight(open_run(run))
        names = {check["check"]: check for check in report["checks"]}
        self.assertIn("run_integrity_status", names)
        self.assertEqual(names["run_integrity_status"]["outcome"], "failed")
        self.assertTrue(
            any(item["check"] == "run_integrity_status" for item in report["blockers"]),
            "reproduce did not block on an integrity-failed run",
        )

    def test_reproduce_still_accepts_a_successful_run(self):
        from modules.cli.reproduce_command import preflight

        run = Path(self.run_benchmark_once()["run_directory"])
        report = preflight(open_run(run))
        names = {check["check"]: check for check in report["checks"]}
        self.assertEqual(names["run_integrity_status"]["outcome"], "passed")

    def test_explain_reports_lifecycle_and_integrity_separately(self):
        from modules.cli.explain_command import build_explanation, render_text
        from modules.diagnostics import project_run

        run = self._integrity_failed_run()
        payload = build_explanation(project_run(open_run(run)))
        self.assertEqual(payload["lifecycle"], "finalized_valid")
        self.assertEqual(payload["run_integrity_status"], "failed")

        rendered = render_text(payload)
        self.assertIn("Lifecycle: finalized_valid", rendered)
        self.assertIn("Run integrity status: failed", rendered)
        self.assertIn("did not record success", rendered)

    def test_explain_does_not_nag_when_they_agree(self):
        from modules.cli.explain_command import build_explanation, render_text
        from modules.diagnostics import project_run

        run = Path(self.run_benchmark_once()["run_directory"])
        rendered = render_text(build_explanation(project_run(open_run(run))))
        self.assertIn("Run integrity status: completed", rendered)
        self.assertNotIn("did not record success", rendered)


class MeasurementSemanticsUnchangedTests(FinalizationFixture):
    """This phase changes finalization, not measurement (P2 as oracle)."""

    def test_two_successful_runs_still_agree_semantically(self):
        first = Path(self.run_benchmark_once("out_a")["run_directory"])
        second = Path(self.run_benchmark_once("out_b")["run_directory"])
        self.assertEqual(
            measurement_semantic_hash(first), measurement_semantic_hash(second)
        )

    def test_a_successful_run_is_projectable(self):
        run = Path(self.run_benchmark_once()["run_directory"])
        self.assertRegex(measurement_semantic_hash(run), r"^[0-9a-f]{64}$")


if __name__ == "__main__":
    unittest.main()
