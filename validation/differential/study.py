"""Study orchestration: analyze a subject, then run both tracks over it.

Layered corpus, deliberately in order:

``layer1_synthetic``
    Small constructs with intentionally known structure and language edge cases.
    Layer 1 establishes that the adapters behave; it is **not** evidence of
    real-world validity and must never be presented as such.
``layer2_selected_real``
    Representatives across the four supported language families and across
    repository sizes and structures.
``layer3_benchmark_sample``
    Broader sampling, only once the adapters and the disagreement
    classification are stable.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

from validation.differential import definitions, records, track_a, track_b


def analyze_subject(
    root: Path, output_root: Path, *, subject_key: str | None = None,
    expected_language: str | None = None,
) -> tuple[Path, dict[str, Any]]:
    """Run one subject through the canonical ArchLens pipeline.

    Using ArchLens to produce the measurement under test is the point; the
    reference implementations that judge it are what must stay independent.
    """
    from modules.benchmark_runner import run_benchmark
    from modules.config import AnalysisConfig
    from modules.repository_input import RepositorySpec

    spec = RepositorySpec(
        url="", architecture_type="unknown", expected_language=expected_language,
        enabled=True, local_path=str(Path(root).resolve()), subject_key=subject_key,
    )
    config = AnalysisConfig.from_env(
        output_root=Path(output_root),
        cache_root=Path(output_root) / "cache",
        temporary_directory=Path(output_root) / "temp",
        workers=1,
    )
    summary = run_benchmark(
        repository_specs=[spec], config=config, acquisition_mode="offline",
        command_line_arguments=["differential-validation"], single_repository=True,
    )
    return Path(summary["run_directory"]), summary


def context_for(run_directory: Path, subject_key: str, corpus_layer: str) -> dict[str, Any]:
    """Everything a record needs about the ArchLens side, read from artifacts."""
    from modules.subject import subject_key_of
    from validation.artifact_io.reader import open_run

    view = open_run(run_directory)
    chosen = next(
        (
            item for item in view.repositories
            if subject_key_of(dict(item)) == subject_key
        ),
        None,
    )
    if chosen is None:
        raise KeyError(f"run {run_directory} records no subject {subject_key!r}")
    manifest = view.manifest
    return {
        "subject_key": subject_key,
        "analyzed_revision": (chosen.get("acquisition") or {}).get(
            "analyzed_commit_sha"
        ) or None,
        "analysis_scope_hash": chosen.get("analysis_scope_hash"),
        "corpus_layer": corpus_layer,
        "archlens_version": str(manifest.get("program_version")),
        "metric_contract_version": str(manifest.get("metric_contract_version")),
        "exclusion_policy_version": str(manifest.get("exclusion_policy_version")),
    }


def run_both_tracks(
    root: Path, run_directory: Path, context: dict[str, Any], *,
    languages_present: Sequence[str] = (),
) -> list[records.DifferentialRecord]:
    """Track A and Track B over one already-analyzed subject."""
    found: list[records.DifferentialRecord] = []
    found.extend(track_a.run_loc(root, run_directory, context))
    found.append(track_a.run_source_file_count(run_directory, context))
    found.extend(track_a.run_entities(root, run_directory, context))
    found.extend(
        track_a.unavailable_reference_records(languages_present, context)
    )
    found.extend(track_b.run(root, run_directory, context))
    return found


def study_metadata(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """Reproducibility metadata, including what the study could not cover."""
    import platform
    import sys

    from validation.differential.reference import environment

    payload = {
        "python_version": platform.python_version(),
        "python_version_exact": sys.version,
        "platform": platform.platform(),
        # Exact tool, version, executable path and provisioning source for every
        # reference, so any result can be traced to what produced it and the
        # environment can be rebuilt.
        "reference_environment": environment.manifest(),
        "reference_availability": {
            "line_classifier": {"available": True, "reference_version": "1.0.0"},
            "independent_selection": {"available": True, "reference_version": "1.0.0"},
        },
        "coverage": definitions.coverage_report(),
        "definition_mappings": [
            mapping.as_dict() for mapping in definitions.REGISTRY.values()
        ],
        "ground_truth_disclaimer": (
            "No reference implementation in this study is ground truth. A "
            "disagreement is a finding about both implementations until "
            "adjudicated."
        ),
        "layer1_disclaimer": (
            "Layer 1 results are synthetic micro-cases that establish adapter "
            "behaviour. They are not evidence of real-world validity."
        ),
    }
    if extra:
        payload.update(extra)
    return payload
