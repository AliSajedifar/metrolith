import json
import subprocess
import tempfile
import tomllib
from pathlib import Path

import pipeline
from modules.cli import check_command, policy_command
from modules.config import ARTIFACT_SCHEMA_VERSION, PROGRAM_VERSION
from modules.policy.document_v2 import (
    POLICY_DOCUMENT_V2_FORMAT_VERSION,
    load_policy_v2,
)
from modules.presentation import (
    LOCAL_UNPROTECTED_LABEL,
    PROTECTED_LABEL,
    boolean,
    measurement,
    scalar,
    subject_display,
    trust_label,
)
from modules.run_artifacts import artifact_slug
from modules.revision_source import resolve_revision_pair
from validation.scripts.validate_outputs import inventory_slug


ROOT = Path(__file__).resolve().parent.parent


def test_version_authorities_are_consistent():
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert metadata["project"]["version"] == PROGRAM_VERSION == "4.0.1"
    assert POLICY_DOCUMENT_V2_FORMAT_VERSION == "2.2.0"
    assert ARTIFACT_SCHEMA_VERSION == "1.12.0"


def test_shared_human_presentation_keeps_absence_zero_and_trust_distinct():
    identity = subject_display(
        {"subject_key": "example:local", "repository_url": None}
    )
    assert identity.name == "Local repository"
    assert identity.subject_key == "example:local"
    assert identity.repository_locator is None
    assert scalar(0) == "0"
    assert scalar(None) == "not supplied"
    assert boolean(True) == "yes"
    assert boolean(False) == "no"
    assert measurement(None, "not_applicable") == "not applicable"
    assert measurement(None, "unavailable") == "unavailable"
    assert trust_label({"mode": "local_unprotected"}) == LOCAL_UNPROTECTED_LABEL
    assert trust_label(
        {"mode": "protected_required", "protected_gate": True}
    ) == PROTECTED_LABEL


def test_check_human_output_labels_local_and_protected_trust():
    base = {
        "check_result_format_version": "1.4.0",
        "archlens_version": "3.8.0",
        "failure_kind": "policy_invalid",
        "failure_message": "invalid document",
        "verdict": "error",
        "exit_code": 2,
        "findings": [],
        "evidence": {},
        "policy": {},
        "run": {},
    }
    local = check_command.render_text(
        {**base, "evaluated_input_provenance": {"mode": "local_unprotected"}}
    )
    protected = check_command.render_text(
        {
            **base,
            "evaluated_input_provenance": {
                "mode": "protected_required",
                "protected_gate": True,
            },
        }
    )
    assert LOCAL_UNPROTECTED_LABEL in local
    assert PROTECTED_LABEL in protected
    assert "Was evaluation performed? no" in local
    assert "Next: metrolith policy validate POLICY" in local


def test_policy_init_requires_explicit_rule_and_creates_current_valid_json(capsys):
    parser = pipeline.build_cli()
    missing = parser.parse_args(["policy", "init", "--output", "unused.json"])
    assert policy_command.handle(missing) == policy_command.EXIT_USAGE
    assert "will not invent a threshold" in capsys.readouterr().out

    with tempfile.TemporaryDirectory() as directory:
        output = Path(directory) / "policy.json"
        args = parser.parse_args(
            [
                "policy",
                "init",
                "--output",
                str(output),
                "--metric",
                "repository.source_files",
                "--operator",
                "gt",
                "--threshold",
                "12",
                "--severity",
                "warning",
            ]
        )
        assert policy_command.handle(args) == 0
        raw = json.loads(output.read_text(encoding="utf-8"))
        assert raw["policy_document_format_version"] == "2.2.0"
        assert load_policy_v2(raw).metric_rules[0].threshold == 12


def test_cli_additions_preserve_machine_defaults_and_expose_local_tutorial():
    parser = pipeline.build_cli()
    hotspots = parser.parse_args(["hotspots", "run"])
    doctor = parser.parse_args(["doctor"])
    validate = parser.parse_args(["validate", "run"])
    example = parser.parse_args(["example", "run", "--local"])
    baseline = parser.parse_args(
        ["baseline", "capture", "run", "--rules", "rules.json", "--output", "b.json"]
    )
    assert hotspots.format == doctor.format == validate.format == "json"
    assert example.local is True
    assert baseline.baseline_command == "capture"


def test_local_subject_artifact_families_share_one_portable_slug():
    subject = {
        "repository_owner": "local",
        "repository_name": "Space / punctuation: example",
    }
    assert inventory_slug(subject) == artifact_slug(subject)
    assert inventory_slug(subject) == "local__Space___punctuation__example"


def test_revision_ancestry_accepts_the_existing_bare_cache_shape(tmp_path):
    source = tmp_path / "source"
    bare = tmp_path / "cache.git"
    source.mkdir()
    subprocess.run(["git", "init"], cwd=source, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "archlens@example.invalid"],
        cwd=source, check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "ArchLens Test"], cwd=source, check=True
    )
    (source / "app.py").write_text("value = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "app.py"], cwd=source, check=True)
    subprocess.run(
        ["git", "commit", "-m", "fixture"], cwd=source, check=True,
        capture_output=True,
    )
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=source, check=True,
        capture_output=True, text=True,
    ).stdout.strip()
    subprocess.run(
        ["git", "clone", "--bare", str(source), str(bare)],
        check=True, capture_output=True,
    )
    pair = resolve_revision_pair(bare, sha, sha)
    assert pair.ancestry == "ancestor"
    assert pair.base.sha == pair.head.sha == sha


def test_readme_above_fold_is_local_and_does_not_claim_deferred_products():
    top = (ROOT / "README.md").read_text(encoding="utf-8")[:7000]
    assert "Evidence, not scores." in top
    assert "metrolith example run --local" in top
    assert "LOCAL — UNPROTECTED" in top
    assert "Artifact Schema | 1.12.0" in top
    assert "Offline Explorer" not in top
