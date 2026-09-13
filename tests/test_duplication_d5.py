"""D5 product-surface, determinism, status, mutation, and CLI gates."""

from __future__ import annotations

import ast
import copy
import json
import re
import subprocess
from pathlib import Path

import pytest

import pipeline
from modules.cli import duplication_command
from modules.duplication.output import (
    COMPLETE,
    DUPLICATION_CONTRACT_VERSION,
    DUPLICATION_FORMAT,
    DUPLICATION_FORMAT_VERSION,
    FAILED,
    NOT_APPLICABLE,
    NOT_REQUESTED,
    PARTIAL,
    DuplicationOutputError,
    analyze_duplication_snapshot,
    canonical_json,
    render_text,
    validate_duplication_document,
)
from modules.local_source import prepare_local_source


ROOT = Path(__file__).resolve().parents[1]
CLONE_BODY = """\
def {name}(seed):
    one = seed + 1
    two = one * 2
    three = two - 3
    four = three / 4
    five = four + one
    six = five * two
    seven = six - three
    eight = seven + four
    return eight
"""


def _write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8", newline="\n")


def _clone_source(root: Path, *, unicode_paths: bool = False) -> None:
    if unicode_paths:
        first = root / "café" / "日本語_a.py"
        second = root / "café" / "日本語_b.py"
    else:
        first = root / "src" / "a.py"
        second = root / "src" / "b.py"
    _write(first, CLONE_BODY.format(name="alpha"))
    _write(second, CLONE_BODY.format(name="beta"))


def _document(root: Path, requested=("lexical", "structural")) -> dict:
    with prepare_local_source(root) as snapshot:
        return analyze_duplication_snapshot(
            snapshot, requested_kinds=requested
        ).document


def _git(repository: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _initialize_git(repository: Path) -> str:
    _git(repository, "init", "--quiet")
    _git(repository, "config", "user.email", "duplication@archlens.invalid")
    _git(repository, "config", "user.name", "Duplication D5")
    _git(repository, "add", ".")
    _git(repository, "commit", "--quiet", "-m", "fixture")
    return _git(repository, "rev-parse", "HEAD")


def test_cli_parser_exposes_exact_d5_surface_and_defaults(tmp_path):
    args = pipeline.build_cli().parse_args(["duplication", str(tmp_path)])
    assert args.command == "duplication"
    assert args.path == tmp_path
    assert args.revision is None
    assert args.tracked_only is False
    assert args.kind == "all"
    assert args.format == "text"
    assert args.output is None
    assert args.overwrite is False
    assert args.timings is False

    selected = pipeline.build_cli().parse_args(
        [
            "duplication",
            str(tmp_path),
            "--revision",
            "HEAD",
            "--kind",
            "structural",
            "--format",
            "json",
            "--output",
            "result.json",
            "--overwrite",
            "--timings",
        ]
    )
    assert selected.kind == "structural"
    assert selected.format == "json"
    assert selected.output == Path("result.json")
    assert selected.overwrite and selected.timings


def test_json_text_groups_ids_and_unicode_are_deterministic(tmp_path):
    source = tmp_path / "subject"
    source.mkdir()
    _clone_source(source, unicode_paths=True)

    first = _document(source)
    second = _document(source)
    first_json = canonical_json(first)
    second_json = canonical_json(second)
    assert first_json == second_json
    assert first_json.endswith("\n") and not first_json.endswith("\n\n")
    assert render_text(first) == render_text(second)
    assert first["format"] == DUPLICATION_FORMAT
    assert first["format_version"] == DUPLICATION_FORMAT_VERSION
    assert first["duplication_contract_version"] == DUPLICATION_CONTRACT_VERSION
    assert first["status"] == COMPLETE
    assert first["counts"]["lexical"] == {
        "status": COMPLETE,
        "group_count": 1,
        "occurrence_count": 2,
    }
    assert first["counts"]["structural"]["retained_group_count"] == 1
    assert len(first["lexical_groups"]) == len(first["structural_groups"]) == 1
    for group in (*first["lexical_groups"], *first["structural_groups"]):
        assert re.fullmatch(r"dg1:[0-9a-f]{64}", group["group_id"])
        assert group["occurrence_count"] == 2
        for occurrence in group["occurrences"]:
            assert re.fullmatch(r"do1:[0-9a-f]{64}", occurrence["occurrence_id"])
            assert "café/日本語_" in occurrence["path"]
    rendered = render_text(first)
    assert "[L001] dg1:" in rendered
    assert "[S001] dg1:" in rendered
    assert str(source.resolve()) not in first_json
    assert str(source.resolve()) not in rendered
    assert '"timing' not in first_json


def test_statuses_preserve_zero_partial_failed_not_applicable_and_not_requested(
    tmp_path,
):
    complete_zero = tmp_path / "complete_zero"
    complete_zero.mkdir()
    _write(complete_zero / "small.py", "def small():\n    return 1\n")
    complete = _document(complete_zero)
    assert complete["status"] == COMPLETE
    assert complete["counts"]["observed_candidate_count"] == 0
    assert complete["counts"]["lexical"]["group_count"] == 0
    assert complete["counts"]["structural"]["retained_group_count"] == 0

    partial_root = tmp_path / "partial"
    partial_root.mkdir()
    _write(partial_root / "good.py", "def good():\n    return 1\n")
    _write(partial_root / "broken.py", "def broken(:\n")
    partial = _document(partial_root)
    assert partial["status"] == PARTIAL
    assert partial["counts"]["lexical"]["status"] == PARTIAL
    assert partial["counts"]["lexical"]["group_count"] == 0
    assert partial["counts"]["candidate_unavailable_file_count"] == 1

    failed_root = tmp_path / "failed"
    failed_root.mkdir()
    _write(failed_root / "broken.py", "def broken(:\n")
    failed = _document(failed_root)
    assert failed["status"] == FAILED
    assert failed["counts"]["observed_candidate_count"] is None
    assert failed["counts"]["lexical"]["group_count"] is None
    assert failed["counts"]["structural"]["retained_group_count"] is None
    assert failed["lexical_groups"] == failed["structural_groups"] == []

    not_applicable_root = tmp_path / "not_applicable"
    not_applicable_root.mkdir()
    _write(not_applicable_root / "README.md", "no supported source\n")
    not_applicable = _document(not_applicable_root)
    assert not_applicable["status"] == NOT_APPLICABLE
    assert not_applicable["files"] == []
    assert not_applicable["counts"]["lexical"]["group_count"] == 0

    lexical_only = _document(complete_zero, requested=("lexical",))
    assert lexical_only["counts"]["lexical"]["status"] == COMPLETE
    assert lexical_only["counts"]["structural"] == {
        "status": NOT_REQUESTED,
        "initial_group_count": None,
        "suppressed_group_count": None,
        "retained_group_count": None,
        "occurrence_count": None,
    }
    assert lexical_only["files"][0]["structural_status"] == NOT_REQUESTED


def test_validator_mutations_reject_order_path_timing_random_id_and_failed_zero(
    tmp_path,
):
    source = tmp_path / "subject"
    source.mkdir()
    _clone_source(source)
    document = _document(source)

    unstable = copy.deepcopy(document)
    unstable["lexical_groups"][0]["occurrences"].reverse()
    with pytest.raises(DuplicationOutputError, match="ordering"):
        validate_duplication_document(unstable)

    leaked = copy.deepcopy(document)
    leaked["lexical_groups"][0]["occurrences"][0]["path"] = str(
        source / "src" / "a.py"
    )
    with pytest.raises(DuplicationOutputError, match="portable"):
        validate_duplication_document(leaked)

    timed = copy.deepcopy(document)
    timed["timings"] = {"total": 1.0}
    with pytest.raises(DuplicationOutputError, match="unexpected"):
        validate_duplication_document(timed)

    random_id = copy.deepcopy(document)
    random_id["lexical_groups"][0]["group_id"] = "dg1:" + "0" * 64
    with pytest.raises(DuplicationOutputError, match="membership"):
        validate_duplication_document(random_id)

    failed_root = tmp_path / "failed"
    failed_root.mkdir()
    _write(failed_root / "broken.py", "def broken(:\n")
    collapsed = _document(failed_root)
    collapsed["counts"]["lexical"]["group_count"] = 0
    with pytest.raises(DuplicationOutputError, match="lexical counts"):
        validate_duplication_document(collapsed)


def test_cli_output_overwrite_timings_and_stdout_separation(tmp_path, capsys):
    source = tmp_path / "subject"
    source.mkdir()
    _clone_source(source)
    destination = tmp_path / "result.json"
    parser = pipeline.build_cli()
    args = parser.parse_args(
        [
            "duplication",
            str(source),
            "--format",
            "json",
            "--output",
            str(destination),
            "--timings",
        ]
    )
    assert duplication_command.handle(args) == duplication_command.EXIT_OK
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "snapshot_seconds=" in captured.err
    saved = destination.read_text(encoding="utf-8")
    assert json.loads(saved)["counts"]["lexical"]["group_count"] == 1
    assert "seconds" not in saved

    assert duplication_command.handle(args) == duplication_command.EXIT_USAGE
    refused = capsys.readouterr()
    assert refused.out == ""
    assert "--overwrite" in refused.err

    overwrite = parser.parse_args(
        [
            "duplication",
            str(source),
            "--format",
            "json",
            "--output",
            str(destination),
            "--overwrite",
        ]
    )
    assert duplication_command.handle(overwrite) == duplication_command.EXIT_OK
    assert capsys.readouterr().out == ""


def test_cli_invalid_arguments_and_source_modes(tmp_path, capsys):
    parser = pipeline.build_cli()
    with pytest.raises(SystemExit) as invalid_kind:
        parser.parse_args(["duplication", str(tmp_path), "--kind", "approximate"])
    assert invalid_kind.value.code == 2

    incompatible = parser.parse_args(
        ["duplication", str(tmp_path), "--revision", "HEAD", "--tracked-only"]
    )
    assert duplication_command.handle(incompatible) == duplication_command.EXIT_USAGE
    assert "cannot be combined" in capsys.readouterr().err

    tracked_plain = parser.parse_args(
        ["duplication", str(tmp_path), "--tracked-only"]
    )
    assert duplication_command.handle(tracked_plain) == duplication_command.EXIT_USAGE
    assert "Git worktree" in capsys.readouterr().err

    missing = parser.parse_args(["duplication", str(tmp_path / "missing")])
    assert duplication_command.handle(missing) == duplication_command.EXIT_ERROR
    assert "could not be prepared" in capsys.readouterr().err


def test_cli_revision_and_tracked_only_use_existing_snapshot_semantics(tmp_path, capsys):
    repository = tmp_path / "repository"
    repository.mkdir()
    _write(repository / "src" / "a.py", CLONE_BODY.format(name="alpha"))
    revision = _initialize_git(repository)
    _write(repository / "src" / "b.py", CLONE_BODY.format(name="beta"))
    parser = pipeline.build_cli()

    revision_args = parser.parse_args(
        [
            "duplication",
            str(repository),
            "--revision",
            revision,
            "--format",
            "json",
        ]
    )
    assert duplication_command.handle(revision_args) == duplication_command.EXIT_OK
    revision_document = json.loads(capsys.readouterr().out)
    assert revision_document["source"]["mode"] == "local_git_revision"
    assert revision_document["source"]["resolved_revision"] == revision
    assert revision_document["counts"]["lexical"]["group_count"] == 0

    worktree_args = parser.parse_args(
        ["duplication", str(repository), "--format", "json"]
    )
    assert duplication_command.handle(worktree_args) == duplication_command.EXIT_OK
    worktree_document = json.loads(capsys.readouterr().out)
    assert worktree_document["counts"]["lexical"]["group_count"] == 1

    tracked_args = parser.parse_args(
        ["duplication", str(repository), "--tracked-only", "--format", "json"]
    )
    assert duplication_command.handle(tracked_args) == duplication_command.EXIT_OK
    tracked_document = json.loads(capsys.readouterr().out)
    assert tracked_document["source"]["tracked_only"] is True
    assert tracked_document["counts"]["lexical"]["group_count"] == 0


def test_git_worktree_snapshot_preserves_unicode_paths(tmp_path):
    repository = tmp_path / "repository"
    repository.mkdir()
    _clone_source(repository, unicode_paths=True)
    _initialize_git(repository)
    document = _document(repository)
    assert [item["path"] for item in document["files"]] == [
        "café/日本語_a.py",
        "café/日本語_b.py",
    ]
    assert document["counts"]["lexical"]["group_count"] == 1


def test_product_layer_does_not_import_deferred_integrations():
    paths = (
        ROOT / "modules" / "duplication" / "output.py",
        ROOT / "modules" / "cli" / "duplication_command.py",
    )
    imports: set[str] = set()
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports.update(
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        )
        imports.update(
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        )
    forbidden = (
        "modules.policy",
        "modules.hotspots",
        "validation.artifact_io",
    )
    assert not any(name.startswith(forbidden) for name in imports)
    assert not any(
        "duplication" in path.name.lower()
        for path in (ROOT / "validation" / "resources" / "schemas").glob("*.json")
    )
