"""Closed, migration-specific guards for the Metrolith 4.0 public rename."""

from __future__ import annotations

import hashlib
import io
import re
import subprocess
import tomllib
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

import pytest

import pipeline
from modules import config as config_module
from modules.config import (
    AnalysisConfig,
    PROGRAM_VERSION,
    resolve_environment_value,
    resolve_legacy_workspace_state,
)


ROOT = Path(__file__).resolve().parents[1]
STARTING_COMMIT = "949366b92542a07dbb93b93e1b6e1796bc196b35"
REPORT_VIEW_SCHEMA_SHA256 = (
    "15d4f5b247541e7a81cd8703ad04fe4b8ef26545b388fb8889bfbac659a1e709"
)


@pytest.fixture(autouse=True)
def _reset_environment_warning_registry():
    config_module._ENVIRONMENT_WARNINGS_EMITTED.clear()
    yield
    config_module._ENVIRONMENT_WARNINGS_EMITTED.clear()


def test_canonical_product_distribution_parser_and_version(capsys):
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert PROGRAM_VERSION == metadata["project"]["version"] == "4.0.0"
    assert metadata["project"]["name"] == "metrolith"
    assert metadata["project"]["scripts"] == {
        "metrolith": "pipeline:main",
        "archlens": "pipeline:archlens_compat_main",
        "arch-bench": "pipeline:deprecated_main",
    }

    parser = pipeline.build_cli()
    assert parser.prog == "metrolith"
    assert parser.format_usage().startswith("usage: metrolith ")
    assert "Metrolith" in parser.description
    with pytest.raises(SystemExit) as exited:
        parser.parse_args(["--version"])
    assert exited.value.code == 0
    assert capsys.readouterr().out == "Metrolith 4.0.0\n"


def test_compatibility_aliases_warn_once_and_share_canonical_main():
    stderr = io.StringIO()
    with patch.object(pipeline, "main", return_value=37) as canonical:
        with redirect_stderr(stderr):
            archlens_exit = pipeline.archlens_compat_main()
            arch_bench_exit = pipeline.deprecated_main()
    assert (archlens_exit, arch_bench_exit) == (37, 37)
    assert canonical.call_count == 2
    assert stderr.getvalue().splitlines() == [
        "`archlens` is a deprecated compatibility alias; use `metrolith`.",
        "WARNING: 'arch-bench' is deprecated; use 'metrolith' instead.",
    ]


def test_environment_canonical_fallback_agreement_conflict_and_secrecy(capsys):
    secret_a = "owner-secret-alpha"
    secret_b = "owner-secret-beta"

    assert resolve_environment_value(
        "METROLITH_TOKEN", "ARCHLENS_TOKEN", "ARCH_BENCH_TOKEN",
        environment={"METROLITH_TOKEN": secret_a},
    ) == secret_a
    assert capsys.readouterr().err == ""

    assert resolve_environment_value(
        "METROLITH_TOKEN", "ARCHLENS_TOKEN", "ARCH_BENCH_TOKEN",
        environment={"ARCHLENS_TOKEN": secret_a},
    ) == secret_a
    first_warning = capsys.readouterr().err
    assert first_warning == (
        "WARNING: deprecated environment variable(s) ARCHLENS_TOKEN supplied; "
        "use METROLITH_TOKEN.\n"
    )
    assert secret_a not in first_warning

    # The warning is bounded even across repeated resolution of the same setting.
    assert resolve_environment_value(
        "METROLITH_TOKEN", "ARCHLENS_TOKEN", "ARCH_BENCH_TOKEN",
        environment={"METROLITH_TOKEN": secret_a, "ARCHLENS_TOKEN": secret_a},
    ) == secret_a
    assert capsys.readouterr().err == ""

    config_module._ENVIRONMENT_WARNINGS_EMITTED.clear()
    with pytest.raises(ValueError, match=(
        "conflicting environment variables: METROLITH_TOKEN, ARCHLENS_TOKEN"
    )) as refused:
        resolve_environment_value(
            "METROLITH_TOKEN", "ARCHLENS_TOKEN", "ARCH_BENCH_TOKEN",
            environment={"METROLITH_TOKEN": secret_a, "ARCHLENS_TOKEN": secret_b},
        )
    message = str(refused.value)
    assert secret_a not in message and secret_b not in message
    assert capsys.readouterr().err == ""


def test_existing_older_environment_fallback_is_preserved(capsys, tmp_path):
    config = AnalysisConfig.from_env(
        environment={"ARCH_BENCH_WORKERS": "3"}, cwd=tmp_path
    )
    assert config.workers == 3
    warning = capsys.readouterr().err
    assert "ARCH_BENCH_WORKERS" in warning
    assert "METROLITH_WORKERS" in warning


def test_new_workspace_writes_use_only_canonical_defaults(tmp_path):
    config = AnalysisConfig.from_env(environment={}, cwd=tmp_path)
    assert config.workspace_root == tmp_path.resolve()
    assert config.cache_root == (tmp_path / ".metrolith/cache/git").resolve()
    assert config.temporary_directory == (tmp_path / ".metrolith/worktrees").resolve()
    assert config.output_root == (tmp_path / "metrolith-output").resolve()
    config.prepare_paths()
    assert config.cache_root.is_dir()
    assert config.temporary_directory.is_dir()
    assert config.output_root.is_dir()
    assert not (tmp_path / ".archlens").exists()
    assert not (tmp_path / "archlens-output").exists()


def test_legacy_workspace_state_is_read_only_bounded_and_never_merged(
    capsys, tmp_path
):
    canonical = tmp_path / ".metrolith"
    legacy = tmp_path / ".archlens"
    assert resolve_legacy_workspace_state(canonical, legacy) is None

    legacy.mkdir()
    assert resolve_legacy_workspace_state(canonical, legacy) == legacy.resolve()
    warning = capsys.readouterr().err
    assert "ARCHLENS_WORKSPACE_STATE" in warning
    assert str(legacy) not in warning

    canonical.mkdir()
    with pytest.raises(
        ValueError,
        match=r"both canonical \.metrolith and legacy \.archlens state exist",
    ):
        resolve_legacy_workspace_state(canonical, legacy)


def test_readme_primary_surface_is_metrolith_only():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    primary = readme.split("## Migration from ArchLens", 1)[0]
    assert primary.startswith("# Metrolith\n")
    assert "python -m pip install ." in primary
    assert "metrolith analyze ." in primary
    assert "Metrolith analyzes Java, JavaScript/TypeScript, Python and Go source" in primary
    assert "pip install archlens" not in primary
    assert "ArchLens" not in primary
    assert not re.search(r"(?<![\w./-])archlens(?=\s)", primary, re.IGNORECASE)


_APPROVED_TECHNICAL_IDENTITIES = {
    "archlens-analysis",
    "archlens-callable-ledger",
    "archlens-catalog",
    "archlens-changed-code",
    "archlens-check-result",
    "archlens-default-integrity-v1",
    "archlens-dossier",
    "archlens-duplication",
    "archlens-duplication-fingerprint",
    "archlens-duplication-group",
    "archlens-duplication-occurrence",
    "archlens-environment",
    "archlens-error-ledger",
    "archlens-file-inventory",
    "archlens-hotspots",
    "archlens-language-metrics",
    "archlens-ratchet-baseline",
    "archlens-recovery-ledger",
    "archlens-release-verification",
    "archlens-report-view",
    "archlens-report-view-builder",
    "archlens-run-manifest",
    "archlens-run-status",
    "archlens-sheet-metrics",
    "archlens-trusted-evidence-receipt",
}


def _active_source_files() -> list[Path]:
    files = [ROOT / "pipeline.py", ROOT / "pyproject.toml"]
    for directory in ("modules", "tools", "config", "examples", ".github"):
        files.extend(
            path
            for path in (ROOT / directory).rglob("*")
            if path.is_file() and path.suffix in {".py", ".toml", ".yml", ".yaml"}
        )
    return sorted(set(files))


def _unclassified_archlens_spelling(path: Path, line: str) -> str | None:
    working = line
    # Uppercase symbols are deprecated environment names or read-only legacy
    # marker constants. Keep this case-sensitive so the lowercase console alias
    # cannot be accidentally admitted by the same category.
    working = re.sub(r"ARCHLENS_[A-Z0-9_]*", "", working)
    for identity in sorted(_APPROVED_TECHNICAL_IDENTITIES, key=len, reverse=True):
        working = re.sub(re.escape(identity), "", working, flags=re.IGNORECASE)
    for pattern in (
        r"https://archlens\.dev/schemas/[A-Za-z0-9._/-]+",
        r"\barchlensFindingId/v1\b",
        r"\barchlens_json\b",
        r"\barchlens_version\b",
        r"\barchlens_cache\b",
        r"\.archlens_archive\.json",
        r"\.archlens-cache-complete\.json",
        r"\.archlens\b",
    ):
        working = re.sub(pattern, "", working, flags=re.IGNORECASE)

    relative = path.relative_to(ROOT).as_posix()
    if relative == "pipeline.py":
        working = working.replace("archlens_compat_main", "")
        working = working.replace("`archlens` is a deprecated compatibility alias", "")
        working = working.replace('"archlens_*"', "")
    elif relative == "pyproject.toml":
        working = working.replace('archlens = "pipeline:archlens_compat_main"', "")
    elif relative == "modules/summary.py":
        working = working.replace("(?:Metrolith|ArchLens)", "")
    elif relative == "modules/policy/sarif.py":
        working = re.sub(r'(?<![A-Za-z0-9_])archlens(?![A-Za-z0-9_])', "", working)
    elif relative == ".github/actions/metrolith-check/metrolith_action.py":
        working = working.replace('properties.get("archlens")', "")

    match = re.search("archlens", working, re.IGNORECASE)
    return line.strip() if match else None


def test_active_archlens_spellings_have_a_closed_compatibility_classification():
    unclassified: list[str] = []
    for path in _active_source_files():
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "archlens" not in line.casefold():
                continue
            if offending := _unclassified_archlens_spelling(path, line):
                relative = path.relative_to(ROOT).as_posix()
                unclassified.append(f"{relative}:{number}: {offending}")
    assert unclassified == []


def test_no_active_workflow_references_removed_action_path():
    surfaces = [ROOT / "README.md", ROOT / "tools"]
    surfaces.extend((ROOT / ".github").rglob("*"))
    surfaces.extend((ROOT / "docs/examples").rglob("*"))
    offenders = []
    for surface in surfaces:
        if not surface.is_file():
            continue
        text = surface.read_text(encoding="utf-8")
        if ".github/actions/archlens-check" in text:
            offenders.append(surface.relative_to(ROOT).as_posix())
    assert offenders == []
    assert (ROOT / ".github/actions/metrolith-check/action.yml").is_file()
    assert not (ROOT / ".github/actions/archlens-check").exists()




def test_historical_namespace_was_not_replaced_or_reissued():
    schema_root = ROOT / "validation/resources/schemas"
    schema_text = "\n".join(
        path.read_text(encoding="utf-8") for path in schema_root.glob("*.json")
    )
    assert "https://archlens.dev/schemas/" in schema_text
    assert "https://metrolith" not in schema_text
    assert "metrolith.dev" not in schema_text


def test_frozen_public_resources_match_recorded_byte_identities():
    import json
    identities = json.loads((ROOT / "tests/fixtures/resource_hashes.json").read_text(encoding="utf-8"))
    assert identities
    for relative, expected in identities.items():
        assert hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() == expected, relative
    assert hashlib.sha256((ROOT / "validation/resources/schemas/report_view-1.0.schema.json").read_bytes()).hexdigest() == REPORT_VIEW_SCHEMA_SHA256
