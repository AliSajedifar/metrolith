"""Focused and mutation-oriented evidence for Git-aware maintenance hotspots."""

from __future__ import annotations

import io
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from modules.hotspots import (
    HIGH,
    HIGH_ATTENTION,
    HistorySource,
    LOW,
    LOW_ATTENTION,
    MEASURED,
    MEDIUM,
    MODERATE_ATTENTION,
    NOT_APPLICABLE,
    UNAVAILABLE,
    _parse_git_log,
    analyze_hotspots,
    canonical_json,
    classify_attention,
    extract_git_history,
    normalize_relative_path,
    signal_for,
    validate_hotspot_document,
)


SUBJECT = "local:test-subject"


def _run_git(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return completed.stdout.strip()


class GitRepositoryFixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="archlens_hotspots_")
        self.repository = Path(self.temporary.name) / "repository"
        self.repository.mkdir()
        _run_git(self.repository, "init", "-b", "main")
        _run_git(self.repository, "config", "user.email", "hotspots@archlens.invalid")
        _run_git(self.repository, "config", "user.name", "ArchLens Hotspots")

    def tearDown(self):
        self.temporary.cleanup()

    def write(self, relative: str, text: str) -> None:
        target = self.repository / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    def commit(self, message: str) -> str:
        _run_git(self.repository, "add", "-A")
        _run_git(self.repository, "commit", "-m", message)
        return _run_git(self.repository, "rev-parse", "HEAD")


class GitHistorySemanticsTests(GitRepositoryFixture):
    def test_exact_rename_is_one_lineage_and_dirty_worktree_is_observed(self):
        self.write("src/old.py", "value = 1\n")
        self.commit("create")
        _run_git(self.repository, "mv", "src/old.py", "src/naïve.py")
        head = self.commit("rename")
        self.write("src/naïve.py", "value = 2\n")
        self.write("src/untracked.py", "untracked = True\n")

        result = extract_git_history(
            self.repository, head, ["src/naïve.py", "src/untracked.py"]
        )

        self.assertEqual(result["status"], MEASURED)
        self.assertEqual(result["repository_worktree_state"], "dirty")
        measured = result["files"]["src/naïve.py"]
        self.assertEqual(measured["commits"], 2)
        self.assertEqual(measured["exact_renames_followed"], 1)
        self.assertEqual(measured["touched_lines"], 1)
        absent = result["files"]["src/untracked.py"]
        self.assertEqual(absent["status"], NOT_APPLICABLE)
        self.assertEqual(
            absent["unavailable_reason"], "not_tracked_at_analyzed_revision"
        )

    def test_deleted_file_at_revision_is_not_applicable(self):
        self.write("gone.py", "gone = True\n")
        self.commit("create")
        (self.repository / "gone.py").unlink()
        head = self.commit("delete")

        result = extract_git_history(self.repository, head, ["gone.py"])

        self.assertEqual(result["files"]["gone.py"]["status"], NOT_APPLICABLE)
        self.assertIsNone(result["files"]["gone.py"]["commits"])

    def test_missing_repository_is_unavailable_not_zero(self):
        result = extract_git_history(
            self.repository / "missing", "0" * 40, ["src/file.py"]
        )
        self.assertEqual(result["status"], UNAVAILABLE)
        self.assertEqual(result["files"]["src/file.py"]["status"], UNAVAILABLE)
        self.assertIsNone(result["files"]["src/file.py"]["commits"])

    def test_shallow_history_is_unavailable_not_one_commit(self):
        self.write("file.py", "one = 1\n")
        self.commit("one")
        self.write("file.py", "one = 2\n")
        head = self.commit("two")
        shallow = Path(self.temporary.name) / "shallow"
        subprocess.run(
            [
                "git",
                "clone",
                "--depth=1",
                self.repository.as_uri(),
                str(shallow),
            ],
            check=True,
            capture_output=True,
            timeout=30,
        )

        result = extract_git_history(shallow, head, ["file.py"])

        self.assertEqual(result["unavailable_reason"], "shallow_history")
        self.assertTrue(result["shallow"])
        self.assertIsNone(result["files"]["file.py"]["commits"])
        self.assertEqual(
            result["files"]["file.py"]["unavailable_reason"], "shallow_history"
        )

    def test_zero_churn_is_a_measured_zero(self):
        result = _parse_git_log(b"", {"zero.py"})["zero.py"]
        self.assertEqual(result["status"], MEASURED)
        self.assertEqual(result["commits"], 0)
        self.assertEqual(result["touched_lines"], 0)


class FakeView:
    def __init__(self, contributions, callables):
        self.repositories = (
            {
                "subject_key": SUBJECT,
                "repository_url": None,
                "working_tree_state": "dirty_worktree",
                "acquisition": {"analyzed_commit_sha": "a" * 40},
            },
        )
        self._contributions = contributions
        self._callables = callables
        self.has_contribution_ledger = True
        self.has_callable_artifact = True
        self.run_id = "portable-run"
        self.manifest = {
            "program_version": "3.8.0",
            "artifact_schema_version": "1.11.0",
        }

    def stream_contributions(self):
        yield from self._contributions

    def stream_callables(self):
        yield from self._callables


def _contribution(path: str) -> dict:
    return {
        "subject_key": SUBJECT,
        "repository_url": None,
        "relative_path": path,
        "detected_language": "Python",
        "source_files_contribution": 1,
        "structural_complexity_status": "complete",
        "lines_of_code": 10,
        "callable_count": 1,
    }


def _callable(path: str, cognitive: int) -> dict:
    return {
        "subject_key": SUBJECT,
        "repository_url": None,
        "relative_path": path,
        "structural_complexity_status": "complete",
        "nloc_status": "complete",
        "nloc": 5,
        "cyclomatic_complexity": cognitive + 1,
        "cognitive_complexity": cognitive,
        "max_nesting_depth": cognitive,
        "formal_parameter_count": 0,
    }


def _measured_history(commits_by_path: dict[str, int]) -> dict:
    files = {}
    for path, commits in commits_by_path.items():
        files[path] = {
            "status": MEASURED,
            "unavailable_reason": None,
            "commits": commits,
            "added_lines": commits,
            "deleted_lines": 0,
            "touched_lines": commits,
            "touched_lines_status": MEASURED,
            "binary_change_count": 0,
            "exact_renames_followed": 0,
        }
    return {
        "status": MEASURED,
        "unavailable_reason": None,
        "repository_worktree_state": "dirty",
        "shallow": False,
        "files": files,
    }


class ClassificationModelTests(unittest.TestCase):
    def test_attention_table_covers_high_low_combinations(self):
        self.assertEqual(classify_attention(HIGH, HIGH), HIGH_ATTENTION)
        self.assertEqual(classify_attention(HIGH, LOW), MODERATE_ATTENTION)
        self.assertEqual(classify_attention(LOW, HIGH), MODERATE_ATTENTION)
        self.assertEqual(classify_attention(MEDIUM, MEDIUM), MODERATE_ATTENTION)
        self.assertEqual(classify_attention(LOW, LOW), LOW_ATTENTION)
        self.assertIsNone(classify_attention(HIGH, None))

    def test_distinct_rank_has_no_universal_threshold_and_never_splits_ties(self):
        self.assertEqual(signal_for(1, [1, 1, 9])["signal"], LOW)
        self.assertEqual(signal_for(9, [1, 1, 9])["signal"], HIGH)
        self.assertEqual(signal_for(5000, [5000, 5000])["signal"], MEDIUM)
        self.assertIsNone(signal_for(None, [0, 1])["signal"])


class HotspotDocumentTests(unittest.TestCase):
    def setUp(self):
        values = {
            "src/a.py": (9, 9),
            "src/b.py": (9, 1),
            "src/c.py": (1, 9),
            "src/d.py": (1, 1),
        }
        contributions = [_contribution(path) for path in reversed(values)]
        callables = [_callable(path, value[0]) for path, value in values.items()]
        self.view = FakeView(contributions, callables)
        self.history = _measured_history(
            {path: value[1] for path, value in values.items()}
        )

    def build(self):
        with patch("modules.hotspots.extract_git_history", return_value=self.history):
            return analyze_hotspots(
                self.view,
                {SUBJECT: HistorySource(Path("C:/private/repository"), "test")},
            )

    def test_deterministic_attention_order_and_same_input_same_bytes(self):
        first = self.build()
        second = self.build()
        paths = [item["file"] for item in first.document["hotspots"]]
        self.assertEqual(
            paths, ["src/a.py", "src/b.py", "src/c.py", "src/d.py"]
        )
        self.assertEqual(canonical_json(first.document), canonical_json(second.document))
        self.assertEqual(
            [item["classification"] for item in first.document["hotspots"]],
            [
                HIGH_ATTENTION,
                MODERATE_ATTENTION,
                MODERATE_ATTENTION,
                LOW_ATTENTION,
            ],
        )

    def test_every_classification_is_explained_and_no_numeric_score_exists(self):
        document = self.build().document
        self.assertIsNone(document["classification_model"]["score"])
        self.assertIsNone(document["classification_model"]["thresholds"])
        for item in document["hotspots"]:
            self.assertEqual(len(item["reasons"]), 3)
            self.assertIn("observed", item["reasons"][0])
            self.assertIn("observed", item["reasons"][1])
            self.assertNotIn("hotspot_score", item)

    def test_mutation_guard_reversed_ordering_would_fail(self):
        document = self.build().document
        paths = [item["file"] for item in document["hotspots"]]
        self.assertNotEqual(paths, list(reversed(paths)))
        self.assertEqual(paths[0], "src/a.py")
        document["hotspots"].reverse()
        with self.assertRaisesRegex(ValueError, "ordering"):
            validate_hotspot_document(document)

    def test_mutation_guard_ignored_churn_would_fail(self):
        self.assertNotEqual(
            classify_attention(HIGH, HIGH), classify_attention(HIGH, LOW)
        )

    def test_mutation_guard_ignored_complexity_would_fail(self):
        self.assertNotEqual(
            classify_attention(HIGH, HIGH), classify_attention(LOW, HIGH)
        )

    def test_missing_git_status_is_not_treated_as_zero(self):
        unavailable = {
            "status": UNAVAILABLE,
            "unavailable_reason": "shallow_history",
            "repository_worktree_state": UNAVAILABLE,
            "shallow": True,
            "files": {
                path: {
                    **record,
                    "status": UNAVAILABLE,
                    "unavailable_reason": "shallow_history",
                    "commits": None,
                    "touched_lines": None,
                }
                for path, record in self.history["files"].items()
            },
        }
        with patch("modules.hotspots.extract_git_history", return_value=unavailable):
            document = analyze_hotspots(
                self.view,
                {SUBJECT: HistorySource(Path("repository"), "test")},
            ).document
        self.assertTrue(all(item["classification"] is None for item in document["hotspots"]))
        self.assertTrue(
            all(item["churn_signal"]["signal"] is None for item in document["hotspots"])
        )

    def test_mutation_guard_unstable_score_generation_would_fail(self):
        rendered = canonical_json(self.build().document)
        self.assertNotIn("hotspot_score", rendered)
        self.assertNotIn("generated_at", rendered)
        self.assertNotIn("timing", rendered)

    def test_windows_unicode_paths_normalize_and_absolute_paths_do_not_leak(self):
        contributions = [
            _contribution(r"src\مقدار.py"),
            _contribution(r"C:\Users\sample\secret.py"),
        ]
        callables = [_callable(r"src\مقدار.py", 2)]
        view = FakeView(contributions, callables)
        history = _measured_history({"src/مقدار.py": 3})
        private = Path(r"C:\Users\sample\private-repository")
        with patch("modules.hotspots.extract_git_history", return_value=history):
            document = analyze_hotspots(
                view, {SUBJECT: HistorySource(private, "test")}
            ).document
        rendered = canonical_json(document)
        self.assertEqual(document["hotspots"][0]["file"], "src/مقدار.py")
        self.assertNotIn("C:\\Users\\sample", rendered)
        self.assertNotIn("private-repository", rendered)


class PathPortabilityTests(unittest.TestCase):
    def test_portable_paths(self):
        self.assertEqual(normalize_relative_path(r"src\file.py"), "src/file.py")
        self.assertEqual(normalize_relative_path("src/naïve.py"), "src/naïve.py")
        for refused in (
            r"C:\repo\file.py",
            "/repo/file.py",
            "../file.py",
            "https://example.invalid/file.py",
            "",
        ):
            self.assertIsNone(normalize_relative_path(refused), refused)


class HotspotCliTests(unittest.TestCase):
    def test_parser_exposes_only_the_bounded_surface(self):
        import pipeline

        args = pipeline.build_cli().parse_args(
            [
                "hotspots",
                "run",
                "--repository",
                r"C:\src\project",
                "--output",
                "hotspots.json",
                "--timings",
            ]
        )
        self.assertEqual(args.command, "hotspots")
        self.assertEqual(args.repository, [r"C:\src\project"])
        self.assertEqual(args.output, Path("hotspots.json"))
        self.assertTrue(args.timings)

    def test_missing_history_emits_valid_document_without_reanalysis(self):
        from modules.cli import hotspots_command
        from validation.artifact_io.compatibility import (
            CompatibilityState,
            RunLifecycle,
        )

        view = FakeView([_contribution("src/file.py")], [_callable("src/file.py", 3)])
        view.structural_errors = ()
        view.compatibility = SimpleNamespace(
            state=CompatibilityState.SUPPORTED, reason="supported"
        )
        view.lifecycle = RunLifecycle.FINALIZED_VALID
        args = SimpleNamespace(
            run_directory=Path("run"),
            repository=[],
            output=None,
            overwrite=False,
            timings=False,
        )
        output = io.StringIO()
        with (
            patch("validation.artifact_io.reader.open_run", return_value=view),
            patch(
                "modules.benchmark_runner.run_benchmark",
                side_effect=AssertionError("hotspots must not reanalyze"),
            ),
            patch("sys.stdout", output),
        ):
            code = hotspots_command.handle(args)

        self.assertEqual(code, hotspots_command.EXIT_OK)
        rendered = output.getvalue()
        self.assertIn('"classification": null', rendered)
        self.assertIn('"local_repository_override_required"', rendered)


if __name__ == "__main__":
    unittest.main()
