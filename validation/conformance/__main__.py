"""``python -m validation.conformance run`` (plan section 9.1).

Runs offline. No network access and no repository acquisition occur.
"""

from __future__ import annotations

import argparse
import json
import sys

from .runner import CaseOutcome, load_manifest, run_cases

FAILING_OUTCOMES = frozenset({CaseOutcome.FAILED, CaseOutcome.ERROR, CaseOutcome.UNREVIEWED})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m validation.conformance",
        description="Run the ArchLens conformance corpus.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Run the corpus")
    run.add_argument("--case", action="append", dest="cases", help="Run one case by id")
    run.add_argument("--format", choices=("text", "json"), default="text")

    listing = sub.add_parser("list", help="List every case")
    listing.add_argument("--format", choices=("text", "json"), default="text")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    manifest = load_manifest()

    if args.command == "list":
        if args.format == "json":
            print(json.dumps({"cases": list(manifest.cases)}, indent=2, sort_keys=True))
        else:
            for case in manifest.cases:
                print(f"  {case['id']:<44} {case.get('review_status', 'unreviewed')}")
        return 0

    results = run_cases(args.cases)
    failed = [result for result in results if result.outcome in FAILING_OUTCOMES]
    passed = [result for result in results if result.outcome is CaseOutcome.PASSED]
    skipped = [
        result for result in results
        if result.outcome in {CaseOutcome.SKIPPED_PLATFORM, CaseOutcome.SKIPPED_CAPABILITY}
    ]

    if args.format == "json":
        payload = {
            "corpus_version": manifest.corpus_version,
            "metric_contract_version": manifest.metric_contract_version,
            "case_count": len(results),
            "passed_count": len(passed),
            "skipped_count": len(skipped),
            "failed_count": len(failed),
            "results": [result.as_dict() for result in results],
        }
        print(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False))
    else:
        for result in results:
            print(f"  [{result.outcome.value:<19}] {result.case_id}")
            for difference in result.differences:
                print(f"      {difference}")
            if result.message and not result.differences:
                print(f"      {result.message}")
        print()
        # Passed and skipped are reported separately. Folding a skipped case
        # into the passed count would claim coverage the run never had.
        print(
            f"{len(passed)} passed, {len(skipped)} skipped, {len(failed)} failed "
            f"of {len(results)} case(s)"
        )

    return 1 if failed else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
