"""Small trusted-boundary fixtures shared by Ratchet contract regressions."""

from __future__ import annotations

import json
from pathlib import Path

from modules.ratchet import (
    BaselineObservation,
    CoordinateManifestEntry,
    CoordinateStatus,
    MeasurementSemantics,
    ProducerIdentity,
    RatchetBaseline,
    RatchetRule,
    SourceRunBinding,
    VersionBinding,
    require_v1_metric,
)


ANALYSIS_SHA = "e" * 64
CALLABLES_SHA = "d" * 64


def producer_identity(
    metric_version: str = "3.0.0",
    complexity_version: str = "2.0.0",
) -> ProducerIdentity:
    return ProducerIdentity(
        program_name="Metrolith",
        program_version="4.0.0",
        evaluator_source_identity="git:" + ("f" * 40) + ":clean",
        metric_contract_version=metric_version,
        complexity_contract_version=complexity_version,
    )


def measurement_semantics(
    metric_version: str = "3.0.0",
    complexity_version: str = "2.0.0",
) -> MeasurementSemantics:
    return MeasurementSemantics(
        metric_contract_version=metric_version,
        complexity_contract_version=complexity_version,
        exclusion_policy_version="1.5.0",
        exclusion_policy_sha256="1" * 64,
        effective_exclusion_configuration_sha256="2" * 64,
        parser_configuration_sha256="3" * 64,
        metric_options_sha256="4" * 64,
        grammar_versions=(VersionBinding("tree-sitter-python", "0.25.0"),),
        runtime_versions=(
            VersionBinding("python", "3.13.9"),
            VersionBinding("tree-sitter", "0.25.2"),
        ),
    )


def source_binding(
    *,
    run_id: str = "baseline-run",
    manifest_sha: str = "c" * 64,
) -> SourceRunBinding:
    return SourceRunBinding(
        run_id=run_id,
        run_manifest_sha256=manifest_sha,
        analysis_sha256=ANALYSIS_SHA,
        producer_version="4.0.0",
    )


def mark_run_producer_clean(run_directory: Path) -> None:
    """Make a generated fixture represent one clean exact evaluator commit."""

    run = Path(run_directory)
    manifest_path = run / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["profiler_git_dirty"] = False
    manifest["benchmark_environment"]["profiler_git_dirty"] = False
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    environment_path = run / "environment.json"
    environment = json.loads(environment_path.read_text(encoding="utf-8"))
    environment["profiler_git_dirty"] = False
    environment_path.write_text(
        json.dumps(environment, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _versions(subjects) -> tuple[str, str]:
    maps = [subject.contract_versions for subject in subjects]
    metrics = {item.get("metrics") for item in maps if item.get("metrics")}
    complexity = {item.get("complexity") for item in maps if item.get("complexity")}
    if len(metrics) > 1 or len(complexity) > 1:
        raise AssertionError("fixture subjects must use one version per contract")
    return (
        next(iter(metrics), "3.0.0"),
        next(iter(complexity), "2.0.0"),
    )


def coordinate_manifest(
    rules: tuple[RatchetRule, ...],
    subjects,
    observations: tuple[BaselineObservation, ...],
) -> tuple[CoordinateManifestEntry, ...]:
    by_rule = {rule.rule_id: rule for rule in rules}
    entries: list[CoordinateManifestEntry] = []
    observed_keys: set[tuple[str, str, str]] = set()
    for observation in observations:
        rule = by_rule[observation.rule_id]
        definition = require_v1_metric(rule.metric, rule.scope)
        cognitive = definition.family.value == "cognitive_complexity"
        entries.append(
            CoordinateManifestEntry(
                rule_id=rule.rule_id,
                subject_key=observation.subject_key,
                language=observation.language,
                metric=rule.metric,
                scope=rule.scope,
                status=CoordinateStatus.OBSERVED,
                required=True,
                source_artifact=(
                    "callables-ledger" if cognitive else "analysis.json"
                ),
                source_artifact_sha256=(
                    CALLABLES_SHA if cognitive else ANALYSIS_SHA
                ),
            )
        )
        observed_keys.add(observation.key)
    for rule in rules:
        languages = sorted(
            {
                str(item.language)
                for item in observations
                if item.rule_id == rule.rule_id and item.language is not None
            }
        )
        expected_languages: tuple[str | None, ...] = (
            (None,) if rule.scope.value == "repository" else tuple(languages)
        )
        for subject in subjects:
            for language in expected_languages:
                key = (rule.rule_id, subject.subject_key, language or "")
                if key in observed_keys:
                    continue
                definition = require_v1_metric(rule.metric, rule.scope)
                cognitive = definition.family.value == "cognitive_complexity"
                entries.append(
                    CoordinateManifestEntry(
                        rule_id=rule.rule_id,
                        subject_key=subject.subject_key,
                        language=language,
                        metric=rule.metric,
                        scope=rule.scope,
                        status=CoordinateStatus.INTENTIONAL_EXCLUSION,
                        required=False,
                        source_artifact=(
                            "callables-ledger" if cognitive else "analysis.json"
                        ),
                        source_artifact_sha256=(
                            CALLABLES_SHA if cognitive else ANALYSIS_SHA
                        ),
                    )
                )
    return tuple(entries)


def ratchet_baseline(
    *,
    source_run: SourceRunBinding,
    subjects,
    rules: tuple[RatchetRule, ...],
    observations: tuple[BaselineObservation, ...],
    coordinate_entries: tuple[CoordinateManifestEntry, ...] | None = None,
) -> RatchetBaseline:
    subjects = tuple(subjects)
    metric_version, complexity_version = _versions(subjects)
    return RatchetBaseline(
        source_run=source_run,
        producer=producer_identity(metric_version, complexity_version),
        measurement_semantics=measurement_semantics(
            metric_version, complexity_version
        ),
        subjects=subjects,
        rules=rules,
        coordinate_manifest=(
            coordinate_manifest(rules, subjects, observations)
            if coordinate_entries is None
            else coordinate_entries
        ),
        observations=observations,
    )


__all__ = [
    "ANALYSIS_SHA",
    "CALLABLES_SHA",
    "coordinate_manifest",
    "measurement_semantics",
    "mark_run_producer_clean",
    "producer_identity",
    "ratchet_baseline",
    "source_binding",
]
