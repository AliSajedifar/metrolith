"""Direct Revision Diff acceptance scenarios.

Hermetic throughout: every Git repository is synthesized in a temporary
directory, and the one remote case patches acquisition rather than reaching the
network, exactly as the existing runner tests do.

The properties under test are deliberately not "the numbers changed". They are:

* the four identity concepts stay separate — subject, revision, analyzed scope,
  and contract compatibility never stand in for one another;
* a refusal is a refusal, and is distinguishable from a completed comparison
  that found differences;
* every reported change is attributable to recorded evidence, and a residual
  that does not close is reported rather than absorbed;
* neither side is measured until both are known to be measurable.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from modules import preflight, revision_diff
from modules.cli import diff_command
from modules.config import AnalysisConfig
from modules.preflight import PreflightRefused, parser_capability

PYTHON_SOURCE = "class App:\n    def run(self):\n        return 1\n"
HELPER_SOURCE = "def helper():\n    return 2\n"


def git(path: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(path), *arguments],
        check=True, capture_output=True, text=True,
    )
    return completed.stdout


class DiffFixture(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="archlens_diff_")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self._outputs = 0

    # -- fixtures ---------------------------------------------------------

    def make_repository(self, name: str = "project") -> Path:
        path = self.root / name
        path.mkdir(parents=True)
        subprocess.run(["git", "init", "-b", "main", str(path)],
                       check=True, capture_output=True)
        git(path, "config", "user.email", "tests@example.com")
        git(path, "config", "user.name", "Tests")
        # Pinned so the fixture's bytes are the fixture's bytes on every host.
        git(path, "config", "core.autocrlf", "false")
        (path / "app.py").write_text(PYTHON_SOURCE, encoding="utf-8", newline="\n")
        git(path, "add", "-A")
        git(path, "commit", "-m", "first")
        return path

    def commit(self, path: Path, message: str = "change") -> str:
        git(path, "add", "-A")
        git(path, "commit", "-m", message)
        return git(path, "rev-parse", "HEAD").strip()

    def head(self, path: Path) -> str:
        return git(path, "rev-parse", "HEAD").strip()

    def config(self) -> AnalysisConfig:
        self._outputs += 1
        return AnalysisConfig.from_env(
            workspace=self.root,
            output_root=self.root / f"out{self._outputs}",
            cache_root=self.root / "cache",
            temporary_directory=self.root / "worktrees",
            workers=1,
        )

    # -- driver -----------------------------------------------------------

    def diff(self, source, from_side, to_side, **kwargs) -> dict:
        """Resolve both sides and build the document, as the CLI handler does."""
        expected = kwargs.pop("expected_language", "Python")
        subject_key = kwargs.pop("subject_key", None)
        tracked_only = kwargs.pop("tracked_only", False)
        config = kwargs.pop("config", None) or self.config()
        self.assertFalse(kwargs, f"unexpected keyword arguments: {sorted(kwargs)}")

        source_text = str(source) if source is not None else None
        left_spec = revision_diff.parse_side(
            from_side, "from", source=source_text, tracked_only=tracked_only
        )
        right_spec = revision_diff.parse_side(
            to_side, "to", source=source_text, tracked_only=tracked_only
        )
        with patch("modules.benchmark_runner._distribution_version", return_value="4.0.0"):
            left, right = diff_command.resolve_sides(
                left_spec, right_spec, config,
                subject_key=subject_key,
                expected_language=expected,
                architecture_type="unknown",
            )
        return revision_diff.build_diff(left, right, subject_override=subject_key)

    def analyze_once(self, source, revision=None, *, subject_key=None) -> Path:
        """Produce one standalone run, for the run-to-run diff cases."""
        from modules.benchmark_runner import run_benchmark
        from modules.repository_input import RepositorySpec

        spec = RepositorySpec(
            url="", architecture_type="unknown", expected_language="Python",
            local_path=str(source), revision=revision, subject_key=subject_key,
        )
        with patch("modules.benchmark_runner._distribution_version", return_value="4.0.0"):
            summary = run_benchmark(
                repository_specs=[spec], config=self.config(),
                acquisition_mode="offline", command_line_arguments=["analyze"],
                single_repository=True,
            )
        return Path(summary["run_directory"])

    # -- helpers ----------------------------------------------------------

    def delta_of(self, payload, dimension):
        for entry in payload["aggregate_metric_deltas"]:
            if entry["dimension"] == dimension:
                return entry["delta"]
        return 0

    def attribution_of(self, payload, dimension):
        for entry in payload["metric_attribution"]:
            if entry["dimension"] == dimension:
                return entry
        return None

    def paths(self, payload, category):
        return sorted(
            item["relative_path"] for item in payload["file_evidence"][category]
        )


class SideSpecifierTests(DiffFixture):
    """One uniform grammar rather than a flag per source combination."""

    def test_each_supported_side_shape_parses(self):
        cases = {
            "worktree": ("worktree", None),
            "abc123": ("revision", "abc123"),
            "run:/tmp/run": ("existing_run", None),
            "remote:https://github.com/acme/app#deadbeef": ("remote_revision", "deadbeef"),
        }
        for text, (kind, revision) in cases.items():
            with self.subTest(side=text):
                spec = revision_diff.parse_side(text, "from", source="/src")
                self.assertEqual(spec.kind, kind)
                if revision:
                    self.assertEqual(spec.revision, revision)

    def test_a_remote_side_without_a_revision_is_refused(self):
        """An unpinned remote side would not be reproducible."""
        with self.assertRaises(ValueError) as caught:
            revision_diff.parse_side(
                "remote:https://github.com/acme/app", "from", source=None
            )
        self.assertIn("reproducible", str(caught.exception))

    def test_a_revision_side_without_a_source_is_refused(self):
        with self.assertRaises(ValueError):
            revision_diff.parse_side("abc123", "to", source=None)

    def test_an_empty_side_is_refused(self):
        with self.assertRaises(ValueError):
            revision_diff.parse_side("   ", "from", source="/src")


class IdenticalRevisionTests(DiffFixture):
    """The controlling case: nothing changed, so nothing may be reported."""

    def test_the_same_commit_on_both_sides_yields_no_metric_delta(self):
        repository = self.make_repository()
        head = self.head(repository)
        payload = self.diff(repository, head, head)

        self.assertTrue(payload["comparability"]["comparable"])
        self.assertEqual(payload["aggregate_metric_deltas"], [])
        self.assertEqual(payload["per_language_metric_deltas"], [])
        self.assertEqual(payload["measurement_status_changes"], [])
        self.assertFalse(payload["has_differences"])

    def test_the_same_commit_has_an_identical_analysis_scope_hash(self):
        repository = self.make_repository()
        head = self.head(repository)
        payload = self.diff(repository, head, head)
        self.assertEqual(
            payload["sides"]["from"]["analysis_scope_hash"],
            payload["sides"]["to"]["analysis_scope_hash"],
            "the same revision materializes to the same analyzed bytes",
        )

    def test_the_semantic_oracle_agrees_that_nothing_changed(self):
        """Semantic Projection 2.0 as a guard, not as the diff itself."""
        repository = self.make_repository()
        head = self.head(repository)
        payload = self.diff(repository, head, head)
        projection = payload["semantic_projection"]
        self.assertTrue(projection["available"], projection["reason"])
        self.assertTrue(projection["equal"])


class SourceChangeTests(DiffFixture):
    """Commit A against commit B, with a change whose effect is known."""

    def _two_commits(self):
        repository = self.make_repository()
        first = self.head(repository)
        (repository / "app.py").write_text(
            PYTHON_SOURCE + "\n    def extra(self):\n        return 3\n",
            encoding="utf-8", newline="\n",
        )
        second = self.commit(repository, "add a method")
        return repository, first, second

    def test_a_known_source_change_is_reported_with_its_delta(self):
        repository, first, second = self._two_commits()
        payload = self.diff(repository, first, second)

        self.assertTrue(payload["comparability"]["comparable"])
        self.assertTrue(payload["has_differences"])
        self.assertGreater(self.delta_of(payload, "lines_of_code"), 0)
        self.assertEqual(self.delta_of(payload, "methods_functions"), 1)

    def test_the_revision_and_the_scope_hash_both_move(self):
        repository, first, second = self._two_commits()
        payload = self.diff(repository, first, second)
        dimensions = {
            item["dimension"] for item in payload["source_identity_changes"]
        }
        self.assertIn("analyzed_commit_sha", dimensions)
        self.assertIn("analysis_scope_hash", dimensions)

    def test_a_changed_scope_hash_never_makes_the_sides_incomparable(self):
        """The core semantic rule: scope identity is not a comparability verdict."""
        repository, first, second = self._two_commits()
        payload = self.diff(repository, first, second)

        self.assertNotEqual(
            payload["sides"]["from"]["analysis_scope_hash"],
            payload["sides"]["to"]["analysis_scope_hash"],
        )
        self.assertTrue(payload["comparability"]["comparable"])
        self.assertEqual(payload["comparability"]["result"], "comparison_completed")
        scope = next(
            item for item in payload["comparability"]["dimensions"]
            if item["dimension"] == "analysis_scope_hash"
        )
        self.assertFalse(scope["blocking"])

    def test_a_file_content_change_is_attributed_with_no_residual(self):
        repository, first, second = self._two_commits()
        payload = self.diff(repository, first, second)

        self.assertEqual(self.paths(payload, "content_changed"), ["app.py"])
        attribution = self.attribution_of(payload, "lines_of_code")
        self.assertEqual(attribution["evidence_level"], "exact_observed_delta_reconciliation")
        self.assertEqual(attribution["residual"], 0)
        self.assertEqual(attribution["from_added_files"], 0)
        self.assertEqual(attribution["from_removed_files"], 0)
        self.assertEqual(
            attribution["from_changed_files"], attribution["observed_delta"]
        )


class FileLevelEvidenceTests(DiffFixture):
    def test_an_added_file_is_reported_and_attributed(self):
        repository = self.make_repository()
        first = self.head(repository)
        (repository / "added.py").write_text(
            HELPER_SOURCE, encoding="utf-8", newline="\n"
        )
        second = self.commit(repository, "add a file")

        payload = self.diff(repository, first, second)
        self.assertEqual(self.paths(payload, "added"), ["added.py"])
        self.assertEqual(self.paths(payload, "removed"), [])
        self.assertEqual(self.delta_of(payload, "source_files"), 1)

        attribution = self.attribution_of(payload, "lines_of_code")
        self.assertEqual(attribution["residual"], 0)
        self.assertGreater(attribution["from_added_files"], 0)

    def test_a_removed_file_is_reported_and_attributed(self):
        repository = self.make_repository()
        (repository / "helper.py").write_text(
            HELPER_SOURCE, encoding="utf-8", newline="\n"
        )
        first = self.commit(repository, "add helper")
        (repository / "helper.py").unlink()
        second = self.commit(repository, "remove helper")

        payload = self.diff(repository, first, second)
        self.assertEqual(self.paths(payload, "removed"), ["helper.py"])
        self.assertEqual(self.paths(payload, "added"), [])
        self.assertEqual(self.delta_of(payload, "source_files"), -1)

        attribution = self.attribution_of(payload, "lines_of_code")
        self.assertEqual(attribution["residual"], 0)
        self.assertLess(attribution["from_removed_files"], 0)

    def test_a_pure_rename_is_typed_as_exact_content_evidence(self):
        repository = self.make_repository()
        (repository / "helper.py").write_text(
            HELPER_SOURCE, encoding="utf-8", newline="\n"
        )
        first = self.commit(repository, "add helper")
        git(repository, "mv", "helper.py", "renamed.py")
        second = self.commit(repository, "rename helper")

        payload = self.diff(repository, first, second)
        renames = payload["file_evidence"]["renames"]
        self.assertEqual(len(renames), 1, renames)
        self.assertEqual(renames[0]["evidence"], "exact_content_rename")
        self.assertEqual(renames[0]["from_path"], "helper.py")
        self.assertEqual(renames[0]["to_path"], "renamed.py")
        # A pure rename moves no metric.
        self.assertEqual(self.delta_of(payload, "lines_of_code"), 0)

    def test_an_ambiguous_content_match_is_never_reported_as_a_rename(self):
        """Two identical files cannot yield a certain correspondence."""
        added = [
            {"relative_path": "a2.py", "content_hash": "h1"},
            {"relative_path": "b2.py", "content_hash": "h1"},
        ]
        removed = [
            {"relative_path": "a1.py", "content_hash": "h1"},
            {"relative_path": "b1.py", "content_hash": "h1"},
        ]
        found = revision_diff.detect_renames(added, removed)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["evidence"], "ambiguous_content_match")
        self.assertIsNone(found[0]["from_path"])
        self.assertIsNone(found[0]["to_path"])
        self.assertEqual(found[0]["candidate_from_paths"], ["a1.py", "b1.py"])

    def test_no_similarity_heuristic_exists(self):
        """Rename evidence is exact-match only; nothing infers a near match."""
        source = (
            Path(__file__).resolve().parent.parent / "modules" / "revision_diff.py"
        ).read_text(encoding="utf-8")
        for forbidden in ("difflib", "SequenceMatcher", "similarity_ratio"):
            self.assertNotIn(forbidden, source)


class WorktreeSideTests(DiffFixture):
    """A worktree side is an ArchLens-controlled snapshot, never live files."""

    def test_a_commit_against_a_clean_worktree_is_diffable(self):
        repository = self.make_repository()
        head = self.head(repository)
        payload = self.diff(repository, head, "worktree")

        self.assertTrue(payload["comparability"]["comparable"])
        self.assertEqual(
            payload["sides"]["to"]["source_mode"], "local_worktree_snapshot"
        )
        self.assertEqual(payload["aggregate_metric_deltas"], [])

    def test_a_commit_against_a_dirty_worktree_reports_the_uncommitted_change(self):
        repository = self.make_repository()
        head = self.head(repository)
        (repository / "app.py").write_text(
            PYTHON_SOURCE + "\n    def uncommitted(self):\n        return 4\n",
            encoding="utf-8", newline="\n",
        )
        payload = self.diff(repository, head, "worktree")

        self.assertTrue(payload["comparability"]["comparable"])
        self.assertEqual(self.delta_of(payload, "methods_functions"), 1)
        self.assertEqual(self.paths(payload, "content_changed"), ["app.py"])

    def test_an_untracked_file_is_included_unless_tracked_only(self):
        repository = self.make_repository()
        head = self.head(repository)
        (repository / "untracked.py").write_text(
            HELPER_SOURCE, encoding="utf-8", newline="\n"
        )

        included = self.diff(repository, head, "worktree")
        self.assertEqual(self.paths(included, "added"), ["untracked.py"])

        excluded = self.diff(repository, head, "worktree", tracked_only=True)
        self.assertEqual(self.paths(excluded, "added"), [])

    def test_the_diff_never_mutates_the_users_repository(self):
        repository = self.make_repository()
        head = self.head(repository)
        self.diff(repository, head, "worktree")

        self.assertFalse(
            (repository / ".git" / "worktrees").exists(),
            "revision materialization must use git archive, never git worktree add",
        )
        self.assertEqual(git(repository, "status", "--porcelain").strip(), "")
        self.assertEqual(self.head(repository), head)

    def test_scope_hashes_correspond_to_the_snapshots_actually_analyzed(self):
        """A dirty worktree cannot share the committed revision's scope hash."""
        repository = self.make_repository()
        head = self.head(repository)
        clean = self.diff(repository, head, "worktree")
        self.assertEqual(
            clean["sides"]["from"]["analysis_scope_hash"],
            clean["sides"]["to"]["analysis_scope_hash"],
            "a clean worktree of an LF fixture matches its revision materialization",
        )

        (repository / "app.py").write_text(
            PYTHON_SOURCE + "\n    def dirty(self):\n        return 5\n",
            encoding="utf-8", newline="\n",
        )
        dirty = self.diff(repository, head, "worktree")
        self.assertNotEqual(
            dirty["sides"]["from"]["analysis_scope_hash"],
            dirty["sides"]["to"]["analysis_scope_hash"],
        )


class LineEndingScopeDivergenceTests(DiffFixture):
    """CRLF divergence is recorded as evidence, never normalized away."""

    def test_a_crlf_worktree_differs_in_scope_from_the_lf_revision(self):
        repository = self.make_repository()
        head = self.head(repository)
        # Rewrite the working tree with CRLF without committing. The committed
        # revision materializes as LF; the worktree bytes are genuinely
        # different bytes, and ArchLens hashes what it parses.
        (repository / "app.py").write_bytes(
            PYTHON_SOURCE.replace("\n", "\r\n").encode("utf-8")
        )
        payload = self.diff(repository, head, "worktree")

        self.assertNotEqual(
            payload["sides"]["from"]["analysis_scope_hash"],
            payload["sides"]["to"]["analysis_scope_hash"],
            "the bytes hashed are the bytes parsed; no normalization is applied",
        )
        self.assertTrue(
            payload["comparability"]["comparable"],
            "a line-ending scope divergence is evidence, not incomparability",
        )
        self.assertEqual(self.delta_of(payload, "lines_of_code"), 0)


class SubjectIdentityTests(DiffFixture):
    def test_two_different_subjects_are_refused_rather_than_diffed(self):
        one = self.make_repository("one")
        two = self.make_repository("two")
        first = self.head(one)

        payload = self.diff(one, first, f"run:{self.analyze_once(two)}")
        self.assertFalse(payload["comparability"]["comparable"])
        self.assertEqual(payload["comparability"]["result"], "comparison_refused")
        self.assertFalse(payload["subject"]["same_logical_subject"])
        reasons = {item["dimension"] for item in payload["comparability"]["refusal_reasons"]}
        self.assertIn("subject_key", reasons)

    def test_a_refusal_reports_no_metric_deltas_at_all(self):
        """Refused is not 'compared and found different'."""
        one = self.make_repository("one")
        two = self.make_repository("two")
        payload = self.diff(one, self.head(one), f"run:{self.analyze_once(two)}")

        self.assertEqual(payload["aggregate_metric_deltas"], [])
        self.assertEqual(payload["per_language_metric_deltas"], [])
        self.assertFalse(payload["file_evidence"]["evaluable"])

    def test_an_explicit_override_permits_a_cross_subject_diff(self):
        one = self.make_repository("one")
        two = self.make_repository("two")
        run = self.analyze_once(two, subject_key="shared-subject")

        payload = self.diff(
            one, self.head(one), f"run:{run}", subject_key="shared-subject"
        )
        self.assertTrue(payload["comparability"]["comparable"])
        self.assertEqual(payload["subject"]["subject_key_override"], "shared-subject")

    def test_an_override_never_claims_the_sides_were_naturally_the_same(self):
        """The override declares comparability; it must not fake the evidence."""
        one = self.make_repository("one")
        two = self.make_repository("two")
        run = self.analyze_once(two, subject_key="shared-subject")

        payload = self.diff(
            one, self.head(one), f"run:{run}", subject_key="shared-subject"
        )
        subject = payload["subject"]
        self.assertNotEqual(
            subject["natural_subject_key_from"], subject["natural_subject_key_to"]
        )
        self.assertFalse(subject["same_logical_subject"])


class ContractCompatibilityTests(DiffFixture):
    def test_an_incompatible_metric_contract_refuses_the_comparison(self):
        repository = self.make_repository()
        head = self.head(repository)
        run = self.analyze_once(repository, revision=head)

        # Rewrite only the declared contract on one side. The measurement is
        # untouched, so a comparison would look perfectly clean while comparing
        # numbers defined by different rules.
        manifest_path = run / "run_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["metric_contract_version"] = "9.9.9"
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
        )

        payload = self.diff(repository, head, f"run:{run}")
        self.assertFalse(payload["comparability"]["comparable"])
        reasons = {item["dimension"] for item in payload["comparability"]["refusal_reasons"]}
        self.assertIn("metric_contract_version", reasons)
        self.assertEqual(payload["aggregate_metric_deltas"], [])

    def test_an_incompatible_exclusion_policy_refuses_the_comparison(self):
        repository = self.make_repository()
        head = self.head(repository)
        run = self.analyze_once(repository, revision=head)

        manifest_path = run / "run_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["exclusion_policy_version"] = "0.0.1"
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
        )

        payload = self.diff(repository, head, f"run:{run}")
        self.assertFalse(payload["comparability"]["comparable"])
        reasons = {item["dimension"] for item in payload["comparability"]["refusal_reasons"]}
        self.assertIn("exclusion_policy_version", reasons)

    def test_the_scope_and_revision_dimensions_are_reported_but_never_blocking(self):
        repository = self.make_repository()
        payload = self.diff(repository, self.head(repository), "worktree")
        for name in ("analysis_scope_hash", "analyzed_commit_sha", "source_mode"):
            with self.subTest(dimension=name):
                entry = next(
                    item for item in payload["comparability"]["dimensions"]
                    if item["dimension"] == name
                )
                self.assertFalse(entry["blocking"])


class RunToRunDiffTests(DiffFixture):
    def test_two_existing_runs_diff_without_re_measuring(self):
        repository = self.make_repository()
        head = self.head(repository)
        first = self.analyze_once(repository, revision=head)
        (repository / "app.py").write_text(
            PYTHON_SOURCE + "\n    def extra(self):\n        return 3\n",
            encoding="utf-8", newline="\n",
        )
        second_head = self.commit(repository, "extend")
        second = self.analyze_once(repository, revision=second_head)

        payload = self.diff(None, f"run:{first}", f"run:{second}")
        self.assertTrue(payload["comparability"]["comparable"])
        self.assertFalse(payload["sides"]["from"]["freshly_analyzed"])
        self.assertFalse(payload["sides"]["to"]["freshly_analyzed"])
        self.assertEqual(payload["sides"]["from"]["run_directory"], str(first))
        self.assertEqual(self.delta_of(payload, "methods_functions"), 1)

    def test_a_malformed_run_is_refused_with_a_typed_error(self):
        from validation.artifact_io.errors import ArtifactStructureError

        repository = self.make_repository()
        run = self.analyze_once(repository, revision=self.head(repository))
        (run / "run_manifest.json").write_text("{ not json", encoding="utf-8")

        with self.assertRaises((ArtifactStructureError, revision_diff.DiffRefused)):
            self.diff(None, f"run:{run}", f"run:{run}")

    def test_a_malformed_run_exits_with_the_invalid_artifact_code(self):
        repository = self.make_repository()
        run = self.analyze_once(repository, revision=self.head(repository))
        (run / "run_manifest.json").write_text("{ not json", encoding="utf-8")

        args = ExitCodeTests._Args(
            source=None, from_side=f"run:{run}", to_side=f"run:{run}",
            subject_key=None, tracked_only=False, expected_language="python",
            architecture_type="unknown", format="text", output=None,
        )
        code = diff_command.handle(args, self.config())
        self.assertEqual(code, revision_diff.EXIT_INVALID_ARTIFACTS)

    def test_a_run_directory_that_does_not_exist_is_refused(self):
        repository = self.make_repository()
        head = self.head(repository)
        with self.assertRaises(Exception):
            self.diff(repository, head, "run:/no/such/run/directory")

    def test_a_clean_worktree_run_diffs_against_a_later_dirty_snapshot(self):
        """clean worktree -> dirty snapshot, via a preserved run for the baseline."""
        repository = self.make_repository()
        clean = self.analyze_once(repository)
        (repository / "app.py").write_text(
            PYTHON_SOURCE + "\n    def dirty(self):\n        return 9\n",
            encoding="utf-8", newline="\n",
        )
        payload = self.diff(repository, f"run:{clean}", "worktree")

        self.assertTrue(payload["comparability"]["comparable"])
        self.assertEqual(
            payload["sides"]["from"]["source_mode"], "local_worktree_snapshot"
        )
        self.assertEqual(
            payload["sides"]["to"]["source_mode"], "local_worktree_snapshot"
        )
        self.assertEqual(self.delta_of(payload, "methods_functions"), 1)
        self.assertEqual(self.paths(payload, "content_changed"), ["app.py"])


class UnexplainedResidualTests(DiffFixture):
    """Reconciliation must be able to *fail*, or it proves nothing.

    Every other attribution test asserts a zero residual, which a reconciliation
    hardcoded to close would satisfy just as happily. This one fabricates an
    aggregate the per-file ledger cannot account for and requires the residual
    to surface it.
    """

    def test_an_aggregate_its_own_ledger_cannot_account_for_is_flagged(self):
        repository = self.make_repository()
        head = self.head(repository)
        first = self.analyze_once(repository, revision=head)
        second = self.analyze_once(repository, revision=head)

        # Inflate one side's reported total without touching a single per-file
        # contribution. The files are identical, so the ledger accounts for a
        # delta of zero while the aggregate now claims +1000.
        analysis_path = second / "analysis.json"
        analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
        analysis[0]["metrics"]["aggregate"]["lines_of_code"] += 1000
        analysis_path.write_text(
            json.dumps(analysis, indent=2, sort_keys=True), encoding="utf-8"
        )

        payload = self.diff(None, f"run:{first}", f"run:{second}")
        attribution = self.attribution_of(payload, "lines_of_code")

        self.assertIsNotNone(attribution)
        self.assertEqual(attribution["observed_delta"], 1000)
        self.assertEqual(attribution["evidence_level"], "unexplained_residual")
        self.assertEqual(attribution["residual"], 1000)

    def test_a_missing_ledger_makes_attribution_not_evaluable_not_zero(self):
        """Absent evidence is never reported as a closed reconciliation."""
        repository = self.make_repository()
        head = self.head(repository)
        first = self.analyze_once(repository, revision=head)
        second = self.analyze_once(repository, revision=head)

        analysis_path = second / "analysis.json"
        analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
        analysis[0]["metrics"]["aggregate"]["lines_of_code"] += 5
        analysis_path.write_text(
            json.dumps(analysis, indent=2, sort_keys=True), encoding="utf-8"
        )
        for run in (first, second):
            for path in list(run.glob("contributions.csv")):
                path.unlink()
            container = run / "contributions"
            if container.is_dir():
                for path in container.glob("*"):
                    path.unlink()
                container.rmdir()

        payload = self.diff(None, f"run:{first}", f"run:{second}")
        attribution = self.attribution_of(payload, "lines_of_code")
        self.assertEqual(attribution["evidence_level"], "not_evaluable_missing_evidence")
        self.assertIsNone(attribution["residual"])
        self.assertIn("contribution ledger", attribution["not_evaluable_reason"])


class StatusTransitionTests(DiffFixture):
    """Measurement status is its own category, never folded into metric deltas."""

    def test_a_newly_unparseable_file_is_reported_as_a_status_transition(self):
        repository = self.make_repository()
        (repository / "widget.js").write_text(
            "export function widget() { return 1; }\n",
            encoding="utf-8", newline="\n",
        )
        first = self.commit(repository, "add javascript")
        (repository / "widget.js").write_text(
            "export function widget( {\n", encoding="utf-8", newline="\n"
        )
        second = self.commit(repository, "break javascript")

        payload = self.diff(repository, first, second)
        self.assertTrue(payload["comparability"]["comparable"])

        changed = {
            (item["dimension"], item["scope"])
            for item in payload["measurement_status_changes"]
        }
        self.assertTrue(
            changed,
            "a file that stopped parsing must surface as a status change, not "
            "only as a metric delta",
        )
        dimensions = {item["dimension"] for item in payload["measurement_status_changes"]}
        self.assertTrue(
            {"analysis_status", "metric_status", "parser_status"} & dimensions,
            f"expected a measurement-status transition, saw {sorted(dimensions)}",
        )

    def test_the_status_change_is_separate_from_the_metric_delta_list(self):
        repository = self.make_repository()
        (repository / "widget.js").write_text(
            "export function widget() { return 1; }\n",
            encoding="utf-8", newline="\n",
        )
        first = self.commit(repository, "add javascript")
        (repository / "widget.js").write_text(
            "export function widget( {\n", encoding="utf-8", newline="\n"
        )
        second = self.commit(repository, "break javascript")

        payload = self.diff(repository, first, second)
        metric_dimensions = {
            item["dimension"] for item in payload["aggregate_metric_deltas"]
        }
        self.assertFalse(
            metric_dimensions & {"analysis_status", "metric_status"},
            "status belongs in measurement_status_changes, never in metric deltas",
        )


class NonGitSnapshotTests(DiffFixture):
    """A plain directory has no revisions, but is still a diffable subject."""

    def _plain(self, name: str, body: str) -> Path:
        path = self.root / name
        path.mkdir(parents=True)
        (path / "app.py").write_text(body, encoding="utf-8", newline="\n")
        return path

    def test_two_directory_snapshots_diff_under_an_explicit_subject_identity(self):
        before = self._plain("before", PYTHON_SOURCE)
        after = self._plain(
            "after", PYTHON_SOURCE + "\n    def extra(self):\n        return 3\n"
        )
        first = self.analyze_once(before, subject_key="declared-subject")
        second = self.analyze_once(after, subject_key="declared-subject")

        payload = self.diff(
            None, f"run:{first}", f"run:{second}", subject_key="declared-subject"
        )
        self.assertTrue(payload["comparability"]["comparable"])
        self.assertEqual(
            payload["sides"]["from"]["source_mode"], "local_directory_snapshot"
        )
        self.assertEqual(self.delta_of(payload, "methods_functions"), 1)

    def test_a_revision_side_is_refused_for_a_non_git_directory(self):
        plain = self._plain("plain", PYTHON_SOURCE)
        with self.assertRaises(Exception):
            self.diff(plain, "abc1234", "worktree")


class RemoteSideTests(DiffFixture):
    """Remote revision against a local exact revision of the same subject."""

    @contextmanager
    def _offline_remote(self, path: Path, sha: str):
        from modules.acquisition import AcquiredRepository, AcquisitionRecord

        @contextmanager
        def acquire(spec, config, mode="latest", progress=None):
            del config, mode
            if progress:
                progress("using patched offline acquisition")
            yield AcquiredRepository(
                path,
                AcquisitionRecord(
                    repository_url=spec.url,
                    repository_owner=spec.owner,
                    repository_name=spec.repository_name,
                    requested_commit_sha=spec.commit_sha,
                    analyzed_commit_sha=sha,
                    resolved_ref="refs/heads/main",
                    default_branch="main",
                    acquisition_mode="offline",
                    cache_status="reused",
                    remote_checked=False,
                    fetch_timestamp=None,
                    checkout_timestamp="2026-08-09T00:00:00Z",
                    commit_verification_status="verified",
                    fetch_method="offline_cache",
                    cache_hit=True,
                    cached_commit_available=True,
                ),
            )

        with patch("modules.benchmark_runner.acquire_repository", acquire):
            yield

    def test_a_remote_revision_diffs_against_a_local_revision_of_one_subject(self):
        repository = self.make_repository()
        head = self.head(repository)
        with self._offline_remote(repository, head):
            payload = self.diff(
                repository,
                "remote:https://github.com/acme/project#" + head,
                head,
                subject_key="acme-project",
            )

        self.assertTrue(payload["comparability"]["comparable"])
        self.assertEqual(payload["sides"]["from"]["kind"], "remote_revision")
        self.assertEqual(payload["sides"]["to"]["source_mode"], "local_git_revision")
        self.assertEqual(payload["subject"]["subject_key_override"], "acme-project")
        self.assertEqual(payload["aggregate_metric_deltas"], [])

    def test_the_same_commit_from_two_sources_analyzes_identical_content(self):
        """Source mode never decides comparability, and never moves the scope.

        The two sides analyze byte-identical content, so no file is added,
        removed or changed, and no metric moves.

        Their `analysis_scope_hash` values are equal too. They did not used to
        be: the hash covered the raw Git file mode, and a `git archive`
        materialization is not a Git repository, so the same file was recorded
        as `git_mode=100644` from a checkout and `git_mode=None` from an
        archive. That made the scope digest record whether Git metadata was
        observable rather than what was analyzed. Since no production path reads
        a regular file's mode, it was removed from the hash construction
        (`ANALYSIS_SCOPE_HASH_VERSION` 2.0.0).
        """
        repository = self.make_repository()
        head = self.head(repository)
        with self._offline_remote(repository, head):
            payload = self.diff(
                repository,
                "remote:https://github.com/acme/project#" + head,
                head,
                subject_key="acme-project",
            )

        self.assertTrue(payload["comparability"]["comparable"])
        self.assertEqual(payload["aggregate_metric_deltas"], [])
        evidence = payload["file_evidence"]
        self.assertTrue(evidence["evaluable"])
        self.assertEqual(evidence["counts"],
                         {"added": 0, "removed": 0, "content_changed": 0})
        self.assertGreater(evidence["unchanged_count"], 0)
        self.assertEqual(
            payload["sides"]["from"]["analysis_scope_hash"],
            payload["sides"]["to"]["analysis_scope_hash"],
            "identical analyzed bytes at one commit must share one scope hash "
            "regardless of which acquisition path produced them",
        )

    def test_no_spurious_scope_change_is_reported_across_acquisition_paths(self):
        repository = self.make_repository()
        head = self.head(repository)
        with self._offline_remote(repository, head):
            payload = self.diff(
                repository,
                "remote:https://github.com/acme/project#" + head,
                head,
                subject_key="acme-project",
            )

        moved = {item["dimension"] for item in payload["source_identity_changes"]}
        self.assertNotIn("analysis_scope_hash", moved)


class TwoSideBarrierTests(DiffFixture):
    """Neither side may be measured until both are known to be measurable."""

    def test_a_missing_grammar_refuses_before_either_side_parses(self):
        from modules import benchmark_runner as runner

        repository = self.make_repository()
        (repository / "Widget.java").write_text(
            "public class Widget { public int run() { return 1; } }\n",
            encoding="utf-8", newline="\n",
        )
        head = self.commit(repository, "add java")

        parsed: list[str] = []
        original = runner.compute_repository_metrics

        def recording(inventory, *args, **kwargs):
            parsed.append(str(inventory.root))
            return original(inventory, *args, **kwargs)

        probes = dict(preflight.probe_parser_capabilities())
        probes[parser_capability("Java")] = preflight.CapabilityCheck(
            capability=parser_capability("Java"),
            state=preflight.CapabilityState.UNAVAILABLE,
            blocking=False,
            evidence="simulated: grammar not installed",
            reason=preflight.CapabilityReason.GRAMMAR_UNAVAILABLE,
        )

        with (
            patch.object(runner, "compute_repository_metrics", recording),
            patch("modules.preflight.probe_parser_capabilities", lambda: probes),
        ):
            with self.assertRaises(PreflightRefused) as caught:
                self.diff(repository, head, "worktree")

        report = caught.exception.report
        self.assertEqual(
            [check.capability for check in report.refusals], ["java_parser"]
        )
        self.assertEqual(
            parsed, [],
            "a side entered metric parsing despite a two-side capability refusal",
        )

    def test_a_grammar_needed_only_by_the_second_side_still_refuses_first(self):
        """The exact failure mode the two-side barrier exists to prevent.

        The baseline is pure Python and measurable. Only the comparison side
        introduces the Java file whose grammar is missing. A barrier that
        checked one side, or checked each side just before it parsed, would
        measure the baseline and only then discover the problem — leaving a
        half-diff whose deltas are indistinguishable from real source change.
        """
        from modules import benchmark_runner as runner

        repository = self.make_repository()
        baseline = self.head(repository)
        (repository / "Widget.java").write_text(
            "public class Widget { public int run() { return 1; } }\n",
            encoding="utf-8", newline="\n",
        )
        later = self.commit(repository, "introduce java")

        parsed: list[str] = []
        original = runner.compute_repository_metrics

        def recording(inventory, *args, **kwargs):
            parsed.append(str(inventory.root))
            return original(inventory, *args, **kwargs)

        probes = dict(preflight.probe_parser_capabilities())
        probes[parser_capability("Java")] = preflight.CapabilityCheck(
            capability=parser_capability("Java"),
            state=preflight.CapabilityState.UNAVAILABLE,
            blocking=False,
            evidence="simulated: grammar not installed",
            reason=preflight.CapabilityReason.GRAMMAR_UNAVAILABLE,
        )

        with (
            patch.object(runner, "compute_repository_metrics", recording),
            patch("modules.preflight.probe_parser_capabilities", lambda: probes),
        ):
            with self.assertRaises(PreflightRefused) as caught:
                self.diff(repository, baseline, later)

        self.assertEqual(
            [check.capability for check in caught.exception.report.refusals],
            ["java_parser"],
        )
        self.assertEqual(
            parsed, [],
            "the measurable baseline side was parsed even though the other side "
            "could not be measured",
        )
        requiring = caught.exception.report.refusals[0].required_by
        self.assertEqual(
            [item for item in requiring if item.startswith("to")], list(requiring),
            f"only the 'to' side needs Java, so only it should be named: {requiring}",
        )

    def test_the_refusal_names_both_requiring_sides(self):
        from modules import benchmark_runner as runner

        repository = self.make_repository()
        (repository / "Widget.java").write_text(
            "public class Widget { }\n", encoding="utf-8", newline="\n"
        )
        head = self.commit(repository, "add java")

        probes = dict(preflight.probe_parser_capabilities())
        probes[parser_capability("Java")] = preflight.CapabilityCheck(
            capability=parser_capability("Java"),
            state=preflight.CapabilityState.UNAVAILABLE,
            blocking=False,
            evidence="simulated: grammar not installed",
            reason=preflight.CapabilityReason.GRAMMAR_UNAVAILABLE,
        )

        with (
            patch.object(runner, "compute_repository_metrics", lambda *a, **k: None),
            patch("modules.preflight.probe_parser_capabilities", lambda: probes),
        ):
            with self.assertRaises(PreflightRefused) as caught:
                self.diff(repository, head, "worktree")

        requiring = caught.exception.report.refusals[0].required_by
        self.assertEqual(len(requiring), 2, requiring)
        self.assertTrue(any(item.startswith("from") for item in requiring))
        self.assertTrue(any(item.startswith("to") for item in requiring))


class OutputContractTests(DiffFixture):
    """Producer-to-schema contract, written with the schema, not after it."""

    def _payload(self):
        repository = self.make_repository()
        first = self.head(repository)
        (repository / "added.py").write_text(
            HELPER_SOURCE, encoding="utf-8", newline="\n"
        )
        second = self.commit(repository, "add a file")
        return self.diff(repository, first, second)

    def test_emitted_json_validates_against_the_registered_schema(self):
        from validation.artifact_io.schema_store import validate_document

        payload = self._payload()
        violations = validate_document("revision_diff_output", payload, "diff.json")
        self.assertEqual([str(item) for item in violations], [])

    def test_a_refused_diff_also_validates_against_the_schema(self):
        from validation.artifact_io.schema_store import validate_document

        one = self.make_repository("one")
        two = self.make_repository("two")
        payload = self.diff(one, self.head(one), f"run:{self.analyze_once(two)}")

        violations = validate_document("revision_diff_output", payload, "diff.json")
        self.assertEqual([str(item) for item in violations], [])

    def test_the_emitted_version_matches_the_registered_schema_version(self):
        from validation.artifact_io.schema_store import schema_version

        self.assertEqual(
            revision_diff.REVISION_DIFF_FORMAT_VERSION,
            schema_version("revision_diff_output"),
        )

    def test_the_schema_still_forbids_undeclared_properties(self):
        from validation.artifact_io.schema_store import load_schema

        self.assertFalse(load_schema("revision_diff_output")["additionalProperties"])

    def test_an_undeclared_property_is_rejected(self):
        """Mutation proof: the contract test can actually fail."""
        from validation.artifact_io.schema_store import validate_document

        payload = self._payload()
        payload["invented_field"] = True
        violations = validate_document("revision_diff_output", payload, "diff.json")
        self.assertTrue(violations)

    def test_the_document_round_trips_as_json(self):
        payload = self._payload()
        text = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)
        self.assertEqual(json.loads(text), payload)

    def test_the_text_rendering_is_produced_and_mentions_no_html(self):
        payload = self._payload()
        rendered = diff_command.render_text(payload)
        self.assertIn("Metrolith revision diff", rendered)
        self.assertIn("Metric attribution", rendered)
        self.assertNotIn("<", rendered)


class ExitCodeTests(DiffFixture):
    """`diff` distinguishes 'no difference', 'differences' and 'refused'."""

    class _Args:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    def _args(self, source, from_side, to_side, **overrides):
        values = dict(
            source=str(source) if source is not None else None,
            from_side=from_side, to_side=to_side, subject_key=None,
            tracked_only=False, expected_language="python",
            architecture_type="unknown", format="text", output=None,
        )
        values.update(overrides)
        return self._Args(**values)

    def test_identical_sides_exit_zero(self):
        repository = self.make_repository()
        head = self.head(repository)
        with patch("modules.benchmark_runner._distribution_version", return_value="4.0.0"):
            code = diff_command.handle(
                self._args(repository, head, head), self.config()
            )
        self.assertEqual(code, revision_diff.EXIT_NO_DIFFERENCE)

    def test_a_real_change_exits_one(self):
        repository = self.make_repository()
        first = self.head(repository)
        (repository / "added.py").write_text(
            HELPER_SOURCE, encoding="utf-8", newline="\n"
        )
        second = self.commit(repository, "add")
        with patch("modules.benchmark_runner._distribution_version", return_value="4.0.0"):
            code = diff_command.handle(
                self._args(repository, first, second), self.config()
            )
        self.assertEqual(code, revision_diff.EXIT_DIFFERENCES)

    def test_a_bad_side_specifier_exits_with_the_usage_code(self):
        repository = self.make_repository()
        code = diff_command.handle(
            self._args(repository, "remote:https://example.com/x", "worktree"),
            self.config(),
        )
        self.assertEqual(code, revision_diff.EXIT_USAGE)

    def test_a_different_subject_exits_with_the_refusal_code(self):
        one = self.make_repository("one")
        two = self.make_repository("two")
        run = self.analyze_once(two)
        with patch("modules.benchmark_runner._distribution_version", return_value="4.0.0"):
            code = diff_command.handle(
                self._args(one, self.head(one), f"run:{run}"), self.config()
            )
        self.assertEqual(code, revision_diff.EXIT_REFUSED)

    def test_the_output_file_is_written_when_requested(self):
        repository = self.make_repository()
        head = self.head(repository)
        destination = self.root / "reports" / "diff.json"
        with patch("modules.benchmark_runner._distribution_version", return_value="4.0.0"):
            diff_command.handle(
                self._args(repository, head, head, output=destination, format="json"),
                self.config(),
            )
        self.assertTrue(destination.is_file())
        written = json.loads(destination.read_text(encoding="utf-8"))
        # 1.1.0 since C5 added complexity deltas under their own verdict.
        self.assertEqual(written["revision_diff_format_version"], "1.2.0")


class NoSecondMetricPathTests(unittest.TestCase):
    """`diff` orchestrates; it must never compute a metric itself."""

    def test_the_diff_engine_imports_no_measurement_module(self):
        source = (
            Path(__file__).resolve().parent.parent / "modules" / "revision_diff.py"
        ).read_text(encoding="utf-8")
        for forbidden in ("core_metrics", "compute_repository_metrics", "tree_sitter"):
            with self.subTest(symbol=forbidden):
                self.assertNotIn(forbidden, source)

    def test_the_command_reuses_the_canonical_runner(self):
        source = (
            Path(__file__).resolve().parent.parent / "modules" / "cli"
            / "diff_command.py"
        ).read_text(encoding="utf-8")
        self.assertIn("run_benchmark", source)
        self.assertNotIn("compute_repository_metrics", source)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
