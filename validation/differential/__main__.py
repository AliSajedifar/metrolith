"""``python -m validation.differential`` — run the differential-validation study.

    python -m validation.differential layer1 --output <dir>
    python -m validation.differential subject <path> --subject-key K --output <dir>
    python -m validation.differential coverage
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from validation.differential import definitions, records, study

LAYER1_ROOT = Path(__file__).resolve().parent / "corpus" / "layer1"

#: Layer-1 cases and the language each one exercises.
LAYER1_CASES = {
    "python": "Python",
    "java": "Java",
    "javascript": "JavaScript",
    "typescript": "TypeScript",
    "go": "Go",
    # Exercises Track B specifically: one measurable file surrounded by test,
    # vendored, dependency, build-output and generated code. The per-language
    # cases above are single files and so agree on selection trivially.
    "selection": "Python",
}


def _run_subject(root: Path, subject_key: str, language: str, layer: str,
                 workspace: Path) -> list[records.DifferentialRecord]:
    run_directory, summary = study.analyze_subject(
        root, workspace / subject_key, subject_key=subject_key,
        expected_language=language,
    )
    if summary["status"] == "failed":
        raise RuntimeError(
            f"{subject_key}: ArchLens did not finalize ({run_directory}); "
            f"a validation record over an unpublished run would be meaningless"
        )
    context = study.context_for(run_directory, subject_key, layer)
    return study.run_both_tracks(
        root, run_directory, context, languages_present=[language]
    )


def cognitive_definitions_languages() -> tuple[str, ...]:
    """The five languages the Cognitive study covers, from the mappings."""
    from validation.differential.cognitive_definitions import LANGUAGES

    return LANGUAGES


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m validation.differential")
    sub = parser.add_subparsers(dest="command", required=True)

    layer1 = sub.add_parser("layer1", help="Run the synthetic micro-case layer")
    layer1.add_argument("--output", type=Path, required=True)
    layer1.add_argument("--workspace", type=Path)

    subject = sub.add_parser("subject", help="Run both tracks over one local subject")
    subject.add_argument("path", type=Path)
    subject.add_argument("--subject-key", required=True)
    subject.add_argument("--language", required=True)
    subject.add_argument(
        "--layer", default="layer2_selected_real", choices=list(records.CORPUS_LAYERS)
    )
    subject.add_argument("--output", type=Path, required=True)
    subject.add_argument("--workspace", type=Path)

    layer2_parser = sub.add_parser(
        "layer2", help="Run selected real repositories from the benchmark cache"
    )
    layer2_parser.add_argument("--output", type=Path, required=True)
    layer2_parser.add_argument("--workspace", type=Path)
    layer2_parser.add_argument(
        "--only", action="append",
        help="Restrict to these subject keys; repeatable",
    )

    sub.add_parser("coverage", help="Report what this study can and cannot validate")
    sub.add_parser("environment", help="Report the pinned reference environment")

    # Cognitive Complexity, G2-A. Both are READ-ONLY reports over the synthetic
    # probes; neither starts a real-subject campaign.
    sub.add_parser(
        "cognitive-coverage",
        help="Report the Cognitive Complexity definition mappings and their limits",
    )
    capability = sub.add_parser(
        "cognitive-capability",
        help="Re-measure every Cognitive reference's zero behaviour on the probes",
    )
    capability.add_argument("--workspace", type=Path, dest="capability_workspace")

    args = parser.parse_args(argv)

    if args.command == "coverage":
        print(json.dumps(definitions.coverage_report(), indent=2, sort_keys=True))
        return 0

    if args.command == "cognitive-coverage":
        from validation.differential import cognitive_definitions

        print(json.dumps(cognitive_definitions.coverage_report(), indent=2))
        return 0

    if args.command == "cognitive-capability":
        from validation.differential import cognitive_zero_suppression
        from validation.differential.reference import cognitive_drivers

        with tempfile.TemporaryDirectory(prefix="archlens_cg_probe_") as scratch:
            work = args.capability_workspace or Path(scratch)
            live = {
                language: cognitive_drivers.probe_zero_suppression(
                    language, Path(work) / language
                )
                for language in cognitive_definitions_languages()
            }
            # Licensing is a separate call from measuring, deliberately: a
            # measurement must never authorize its own inference.
            for probe in live.values():
                cognitive_zero_suppression.prove_suppression(probe)
            payload = {
                "gocognit_provenance": (
                    cognitive_drivers.verify_gocognit_provenance().as_dict()
                ),
                "availability": cognitive_drivers.availability(),
                **cognitive_zero_suppression.capability_report(live),
            }
        print(json.dumps(payload, indent=2))
        return 0

    if args.command == "environment":
        from validation.differential.reference import environment

        print(json.dumps(environment.manifest(), indent=2, sort_keys=True))
        return 0

    temporary = None
    workspace = args.workspace
    if workspace is None:
        temporary = tempfile.TemporaryDirectory(prefix="archlens_diffval_")
        workspace = Path(temporary.name)
    try:
        collected: list[records.DifferentialRecord] = []
        extra: dict = {}
        if args.command == "layer1":
            for name, language in sorted(LAYER1_CASES.items()):
                root = LAYER1_ROOT / name
                if not root.is_dir():
                    continue
                collected.extend(_run_subject(
                    root, f"layer1-{name}", language, "layer1_synthetic",
                    Path(workspace),
                ))
        elif args.command == "layer2":
            from validation.differential import layer2

            wanted = set(args.only or [])
            skipped: list[dict[str, str]] = []
            for subject in layer2.available_subjects():
                if wanted and subject.subject_key not in wanted:
                    continue
                try:
                    revision = layer2.resolve_revision(subject)
                    source = Path(workspace) / "src" / subject.subject_key
                    layer2.materialize(subject, source, revision)
                except layer2.SubjectUnavailable as exc:
                    # Recorded, never silently dropped: a subject missing from
                    # the study has to be visible as a subject that was
                    # attempted, with the reason it could not be measured.
                    skipped.append({
                        "subject_key": subject.subject_key,
                        "language": subject.language,
                        "reason": str(exc),
                    })
                    print(f"[layer2] SKIPPED {subject.subject_key}: {exc}", flush=True)
                    continue
                print(
                    f"[layer2] {subject.subject_key} ({subject.language}) "
                    f"@ {revision[:12]}", flush=True,
                )
                try:
                    collected.extend(_run_subject(
                        source, subject.subject_key, subject.language,
                        "layer2_selected_real", Path(workspace),
                    ))
                except Exception as exc:  # noqa: BLE001
                    # One subject must never cost the whole study. The
                    # failure is recorded, with its reason, and the run
                    # continues.
                    skipped.append({
                        "subject_key": subject.subject_key,
                        "language": subject.language,
                        "reason": f"{type(exc).__name__}: {exc}",
                    })
                    print(
                        f"[layer2] FAILED {subject.subject_key}: "
                        f"{type(exc).__name__}: {exc}", flush=True,
                    )
            extra["layer2_selection"] = layer2.selection_manifest()
            extra["layer2_skipped"] = skipped
            extra["layer2_materialization"] = layer2.materialization_notes()
            extra["layer2_acquisition_provenance"] = layer2.acquisition_provenance()
            extra["layer2_not_executed"] = [
                {**item, "status": "not_executed_missing_pinned_revision"}
                for item in skipped
                if "pinned revision" in item["reason"]
            ]
        else:
            collected.extend(_run_subject(
                args.path, args.subject_key, args.language, args.layer,
                Path(workspace),
            ))

        destination = records.write_records(
            collected, Path(args.output) / "differential_records.json",
            study_metadata=study.study_metadata(extra),
        )
    finally:
        if temporary is not None:
            temporary.cleanup()

    summary = records.summarize(collected)
    print(json.dumps(summary, indent=2, sort_keys=True))
    print(f"records written to {destination}")
    # Exit code reports whether every disagreement is classified, which is the
    # actual success criterion. It is deliberately not an agreement-rate gate.
    return 0 if summary["all_disagreements_classified"] else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
