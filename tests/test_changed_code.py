from __future__ import annotations

import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from argparse import Namespace
from dataclasses import replace
from pathlib import Path

from modules.changed_code import (
    _metric_deltas,
    build_document,
    canonical_json,
    render_text,
)
from modules.cli import changed_command
from modules.config import AnalysisConfig
from modules.git_change_extractor import (
    GitChangeSet,
    _parse_raw,
    extract_git_changes,
)
from modules.revision_source import (
    RevisionUnavailable,
    analyze_revision_pair,
    resolve_revision_pair,
)


class SyntheticRepository:
    def __init__(self, root: Path):
        self.path = root / "subject"
        self.path.mkdir(parents=True)
        self.git("init", "--quiet")
        self.git("config", "user.email", "changed-code@example.invalid")
        self.git("config", "user.name", "Changed Code Tests")
        self.git("config", "core.autocrlf", "false")

    def git(self, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            ["git", "-C", str(self.path), *arguments],
            check=check,
            capture_output=True,
            text=False,
        )

    def write(self, relative: str, value: str | bytes) -> None:
        target = self.path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(value.encode("utf-8") if isinstance(value, str) else value)

    def remove(self, relative: str) -> None:
        (self.path / relative).unlink()

    def commit(self, message: str) -> str:
        self.git("add", "-A")
        self.git("commit", "--quiet", "--allow-empty", "-m", message)
        return self.git("rev-parse", "HEAD").stdout.strip().decode("ascii")


def independent_name_status(
    repository: SyntheticRepository, base: str, head: str, *, exact_renames: bool = False
) -> list[tuple[str, tuple[str, ...]]]:
    arguments = ["diff", "--name-status", "-z"]
    arguments.append("--find-renames=100%" if exact_renames else "--no-renames")
    output = repository.git(*arguments, base, head).stdout.split(b"\0")
    found: list[tuple[str, tuple[str, ...]]] = []
    index = 0
    while index < len(output):
        status = output[index]
        index += 1
        if not status:
            continue
        count = 2 if status.startswith((b"R", b"C")) else 1
        paths = tuple(
            output[index + offset].decode("utf-8", errors="surrogateescape")
            for offset in range(count)
        )
        index += count
        found.append((status.decode("ascii"), paths))
    return found


class RevisionResolutionTests(unittest.TestCase):
    def test_equal_revisions_and_non_ancestor_commits(self):
        with tempfile.TemporaryDirectory() as holder:
            repository = SyntheticRepository(Path(holder))
            repository.write("root.txt", "root\n")
            root = repository.commit("root")
            equal = resolve_revision_pair(repository.path, root, root)
            self.assertEqual(equal.base.sha, equal.head.sha)
            self.assertEqual(equal.ancestry, "ancestor")

            repository.git("switch", "--quiet", "-c", "left", root)
            repository.write("left.txt", "left\n")
            left = repository.commit("left")
            repository.git("switch", "--quiet", "-c", "right", root)
            repository.write("right.txt", "right\n")
            right = repository.commit("right")
            pair = resolve_revision_pair(repository.path, left, right)
            self.assertEqual(pair.ancestry, "not_ancestor")

    def test_missing_revision_is_unavailable_not_empty(self):
        with tempfile.TemporaryDirectory() as holder:
            repository = SyntheticRepository(Path(holder))
            repository.commit("root")
            with self.assertRaises(RevisionUnavailable) as raised:
                resolve_revision_pair(repository.path, "missing", "HEAD")
            self.assertEqual(raised.exception.reason, "base_revision_unavailable")

    def test_shallow_missing_revision_has_typed_reason(self):
        with tempfile.TemporaryDirectory() as holder:
            root = Path(holder)
            repository = SyntheticRepository(root)
            repository.write("app.py", "value = 1\n")
            base = repository.commit("base")
            repository.write("app.py", "value = 2\n")
            repository.commit("head")
            shallow = root / "shallow"
            subprocess.run(
                [
                    "git", "clone", "--quiet", "--depth", "1", "--no-local",
                    repository.path.as_uri(), str(shallow),
                ],
                check=True,
                capture_output=True,
            )
            with self.assertRaises(RevisionUnavailable) as raised:
                resolve_revision_pair(shallow, base, "HEAD")
            self.assertEqual(
                raised.exception.reason,
                "base_revision_missing_in_shallow_clone",
            )


class GitChangeExtractionTests(unittest.TestCase):
    def test_added_deleted_modified_and_zero_length_anchors_match_oracle(self):
        with tempfile.TemporaryDirectory() as holder:
            repository = SyntheticRepository(Path(holder))
            repository.write("delete.py", "first\nsecond\n")
            repository.write("insert.py", "one\ntwo\nthree\n")
            repository.write("remove.py", "one\ntwo\nthree\n")
            base = repository.commit("base")
            repository.remove("delete.py")
            repository.write("insert.py", "one\ntwo\ninserted\nthree\n")
            repository.write("remove.py", "one\nthree\n")
            repository.write("added.py", "new\n")
            head = repository.commit("head")

            changes = extract_git_changes(resolve_revision_pair(repository.path, base, head))
            by_path = {
                item.head_path or item.base_path: item for item in changes.file_changes
            }
            self.assertEqual(
                [item.change_kind for item in changes.file_changes].count("modified"), 2
            )
            self.assertEqual(by_path["added.py"].hunks[0].base.line_count, 0)
            self.assertEqual(by_path["delete.py"].hunks[0].head.line_count, 0)
            insertion = by_path["insert.py"].hunks[0]
            self.assertEqual(insertion.base.line_count, 0)
            self.assertEqual(insertion.head.line_count, 1)
            deletion = by_path["remove.py"].hunks[0]
            self.assertEqual(deletion.base.line_count, 1)
            self.assertEqual(deletion.head.line_count, 0)

            oracle = independent_name_status(repository, base, head)
            oracle_paths = {path for _status, paths in oracle for path in paths}
            extracted_paths = {
                path
                for item in changes.file_changes
                for path in (item.base_path, item.head_path)
                if path is not None
            }
            self.assertEqual(extracted_paths, oracle_paths)

    def test_exact_rename_and_edited_rename_are_conservative(self):
        with tempfile.TemporaryDirectory() as holder:
            repository = SyntheticRepository(Path(holder))
            repository.write("same.py", "def same():\n    return 1\n")
            repository.write("edited.py", "def edited():\n    return 1\n")
            base = repository.commit("base")
            repository.git("mv", "same.py", "renamed.py")
            repository.git("mv", "edited.py", "edited-renamed.py")
            repository.write("edited-renamed.py", "def edited():\n    return 2\n")
            head = repository.commit("head")

            changes = extract_git_changes(resolve_revision_pair(repository.path, base, head))
            renames = [item for item in changes.file_changes if item.change_kind == "renamed_exact"]
            self.assertEqual(len(renames), 1)
            self.assertEqual((renames[0].base_path, renames[0].head_path), (
                "same.py", "renamed.py"
            ))
            self.assertEqual(renames[0].hunks, ())
            edited = {
                (item.change_kind, item.base_path, item.head_path)
                for item in changes.file_changes
                if "edited" in (item.base_path or item.head_path or "")
            }
            self.assertEqual(edited, {
                ("deleted", "edited.py", None),
                ("added", None, "edited-renamed.py"),
            })
            oracle = independent_name_status(repository, base, head, exact_renames=True)
            self.assertIn(("R100", ("same.py", "renamed.py")), oracle)

    def test_binary_and_unicode_paths_remain_distinct(self):
        with tempfile.TemporaryDirectory() as holder:
            repository = SyntheticRepository(Path(holder))
            repository.write("blob.bin", b"\x00\x01\x02")
            repository.write("کد نمونه.py", "value = 1\n")
            base = repository.commit("base")
            repository.write("blob.bin", b"\x00\x01\x03")
            repository.write("کد نمونه.py", "value = 2\n")
            head = repository.commit("head")
            changes = extract_git_changes(resolve_revision_pair(repository.path, base, head))
            by_path = {item.head_path: item for item in changes.file_changes}
            self.assertEqual(by_path["blob.bin"].hunk_status, "unavailable")
            self.assertEqual(
                by_path["blob.bin"].hunk_unavailable_reason, "binary_content"
            )
            self.assertEqual(by_path["کد نمونه.py"].hunk_status, "complete")
            self.assertTrue(by_path["کد نمونه.py"].hunks)

    def test_equal_trees_are_a_complete_empty_change_set(self):
        with tempfile.TemporaryDirectory() as holder:
            repository = SyntheticRepository(Path(holder))
            commit = repository.commit("empty")
            changes = extract_git_changes(resolve_revision_pair(repository.path, commit, commit))
            self.assertEqual(changes.status, "complete")
            self.assertEqual(changes.file_changes, ())

    def test_type_change_raw_record_is_supported(self):
        old = "1" * 40
        new = "2" * 40
        raw = (
            f":100644 120000 {old} {new} T".encode("ascii")
            + b"\0link\0"
        )
        parsed = _parse_raw(raw)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0].change_kind, "type_changed")


class ChangedCodeDocumentTests(unittest.TestCase):
    def test_canonical_analysis_complexity_formatting_and_callable_overlap(self):
        with tempfile.TemporaryDirectory() as holder:
            root = Path(holder)
            repository = SyntheticRepository(root)
            repository.write(
                "app.py",
                "def classify(value):\n    return value\n",
            )
            repository.write("format.py", "def stable():\n    return 1\n")
            base = repository.commit("base")
            repository.write(
                "app.py",
                "def classify(value):\n    if value:\n        return 1\n    return 0\n",
            )
            repository.write("format.py", "def stable():\n\n    return 1\n")
            head = repository.commit("head")
            pair = resolve_revision_pair(repository.path, base, head)
            changes = extract_git_changes(pair)
            workspace = root / "archlens-workspace"
            config = AnalysisConfig.from_env(
                workspace=workspace,
                output_root=workspace / "output",
                cache_root=workspace / "cache",
                temporary_directory=workspace / "temporary",
            )
            analyzed = analyze_revision_pair(pair, config)
            document = build_document(changes, analyzed)

            equal_pair = resolve_revision_pair(repository.path, base, base)
            equal_document = build_document(
                extract_git_changes(equal_pair),
                analyze_revision_pair(equal_pair, config),
            )
            self.assertEqual(equal_document["status"], "complete")
            self.assertEqual(equal_document["counts"]["git_changed_files"], 0)
            self.assertEqual(equal_document["file_changes"], [])

            self.assertEqual(document["status"], "complete")
            self.assertEqual(document["counts"]["changed_code_files"], 2)
            repository_source_files = next(
                item
                for item in document["repository_metric_evidence"]["base"]["core_metrics"]
                if item["metric"] == "source_files"
            )
            self.assertEqual(repository_source_files["status"], "complete")
            by_path = {
                unit["head_path"]: unit for unit in document["file_changes"]
            }
            unit = by_path["app.py"]
            self.assertEqual(unit["change_kind"], "modified")
            observations = unit["evidence"]["metric_observations"]
            cyclomatic = next(
                item for item in observations
                if item["metric"] == "cyclomatic_complexity_total"
            )
            self.assertGreater(cyclomatic["delta"], 0)
            self.assertTrue(unit["affected_callables"]["base"]["observations"])
            self.assertTrue(unit["affected_callables"]["head"]["observations"])
            self.assertIn("not matched", unit["affected_callables"]["identity_boundary"])

            formatting = by_path["format.py"]
            formatting_deltas = [
                item["delta"]
                for item in formatting["evidence"]["metric_observations"]
                if item["delta"] is not None
            ]
            self.assertTrue(formatting_deltas)
            self.assertEqual(set(formatting_deltas), {0})
            self.assertIn(
                "no measured metric delta observed", render_text(document)
            )

            unavailable_hunks = GitChangeSet(
                status="complete",
                file_changes=tuple(
                    replace(
                        item,
                        hunk_status="unavailable",
                        hunk_unavailable_reason="binary_content",
                        hunks=(),
                    ) if item.head_path == "app.py" else item
                    for item in changes.file_changes
                ),
            )
            partial = build_document(unavailable_hunks, analyzed)
            self.assertEqual(partial["status"], "partial")
            self.assertIn("binary_content", partial["reasons"])

            rendered = canonical_json(document)
            self.assertEqual(rendered, canonical_json(document))
            self.assertNotIn(str(repository.path), rendered)
            self.assertNotIn("timestamp", rendered.casefold())
            self.assertIn("no cross-revision callable matching", render_text(document))

    def test_missing_metric_evidence_never_becomes_zero(self):
        unavailable = {
            "core_metrics": [{
                "metric": "lines_of_code", "value": None,
                "status": "unavailable", "contract_version": "3.0.0",
            }],
            "structural_complexity": [],
            "cognitive_complexity": [],
        }
        measured = {
            "core_metrics": [{
                "metric": "lines_of_code", "value": 4,
                "status": "complete", "contract_version": "3.0.0",
            }],
            "structural_complexity": [],
            "cognitive_complexity": [],
        }
        observation = _metric_deltas(unavailable, measured, paired_file=True)[0]
        self.assertIsNone(observation["base"]["value"])
        self.assertIsNone(observation["delta"])
        self.assertEqual(observation["not_evaluable_reason"], "measurement_unavailable")


class ChangedCodeCommandTests(unittest.TestCase):
    def test_missing_head_emits_unavailable_document_and_exit_three(self):
        with tempfile.TemporaryDirectory() as holder:
            root = Path(holder)
            repository = SyntheticRepository(root)
            base = repository.commit("base")
            arguments = Namespace(
                source=repository.path,
                base=base,
                head="does-not-exist",
                format="json",
                output=None,
                overwrite=False,
                timings=False,
            )
            config = AnalysisConfig.from_env(workspace=root / "workspace")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exit_code = changed_command.handle(arguments, config)
            document = json.loads(output.getvalue())
            self.assertEqual(exit_code, changed_command.EXIT_UNAVAILABLE)
            self.assertEqual(document["status"], "unavailable")
            self.assertIsNone(document["counts"])
            self.assertIsNone(document["file_changes"])
            self.assertEqual(document["reasons"], ["head_revision_unavailable"])

    def test_output_overwrite_guard_is_usage_error(self):
        with tempfile.TemporaryDirectory() as holder:
            root = Path(holder)
            destination = root / "result.json"
            destination.write_text("keep", encoding="utf-8")
            arguments = Namespace(
                source=root,
                base="a",
                head="b",
                format="json",
                output=destination,
                overwrite=False,
                timings=False,
            )
            errors = io.StringIO()
            with contextlib.redirect_stderr(errors):
                exit_code = changed_command.handle(arguments, AnalysisConfig())
            self.assertEqual(exit_code, changed_command.EXIT_USAGE)
            self.assertEqual(destination.read_text(encoding="utf-8"), "keep")


if __name__ == "__main__":
    unittest.main()
