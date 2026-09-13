"""Adjudicate the 43 retained Layer-2 disagreements without rewriting history.

The raw pre-correction and post-correction study documents remain immutable
inputs.  This module writes separate adjudicated documents and a finding
register.  Paths below identify study observations, not counting exceptions:
no production or reference result is changed here.

Adapter findings are inferred from the construct evidence emitted by the
corrected independent adapter.  The six retained numeric disagreements are
listed explicitly because manual adjudication is necessarily attached to the
concrete evidence paths inspected by the reviewer.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

from validation.differential import records


ADJUDICATION_VERSION = "1.0.0"

FINDINGS: dict[str, dict[str, str]] = {
    "DVL2-REF-OWNER-001": {
        "classification": "reference_adapter_defect",
        "title": "Indirect or property-assigned object members treated as directly owned",
        "root_cause": (
            "The TypeScript-API adapter accepted any immediate PropertyAssignment "
            "or property-target BinaryExpression as a named owner. That admitted "
            "members of nested objects, anonymous call-argument objects, and "
            "property-assigned objects, although the reviewed mapping counts only "
            "direct members of an object literal bound to a module-scope identifier."
        ),
        "minimal_reproduction": (
            "const Named = { direct() {}, nested: { excluded() {} } }; "
            "Thing.config = { excluded() {} };"
        ),
    },
    "DVL2-REF-LOCAL-CLASS-002": {
        "classification": "reference_adapter_defect",
        "title": "Direct method of a named local class treated as nested",
        "root_cause": (
            "The adapter searched beyond the class owner, found the enclosing "
            "function, and reclassified a direct local-class method as nested."
        ),
        "minimal_reproduction": (
            "function outer() { class Local { method() {} } return Local; }"
        ),
    },
    "DVL2-REF-ANON-DEFAULT-003": {
        "classification": "reference_adapter_defect",
        "title": "Anonymous default function declaration treated as named",
        "root_cause": (
            "The adapter counted every FunctionDeclaration with a body and did "
            "not enforce the reviewed named-implementation requirement."
        ),
        "minimal_reproduction": (
            "export default function () {}; export function named() {}"
        ),
    },
    "DVL2-LOC-REGEX-COMMENT-004": {
        "classification": "reference_tool_limitation",
        "title": "Comment delimiter inside a regular-expression literal",
        "root_cause": (
            "The independent character scanner has no JavaScript regular-expression "
            "state. It reads /* inside a regex as a block-comment opener and masks "
            "real code until a later */ sequence."
        ),
        "minimal_reproduction": (
            "const pattern = /(\\/*)/g;\nconst code = 1;\nconst marker = '*/';"
        ),
    },
    "DVL2-LOC-REGEX-BACKTICK-005": {
        "classification": "reference_tool_limitation",
        "title": "Backticks inside a regular-expression literal",
        "root_cause": (
            "Without regular-expression state, the character scanner interprets "
            "backticks in a regex as template delimiters and later preserves real "
            "line comments as string content."
        ),
        "minimal_reproduction": "const pattern = /`[^`]*`/g;\n// comment only",
    },
    "DVL2-LOC-TEMPLATE-006": {
        "classification": "reference_tool_limitation",
        "title": "Comment inside a JavaScript template interpolation",
        "root_cause": (
            "The character scanner consumes a template literal as one opaque string "
            "and does not lex ${...} as JavaScript, so a real comment inside the "
            "interpolation is retained as code."
        ),
        "minimal_reproduction": (
            "const query = sqls`SELECT ${[\n// comment only\nvalue\n]}`;"
        ),
    },
    "DVL2-PARSER-JSX-007": {
        "classification": "parser_limitation",
        "title": "Pinned JavaScript grammar loses a JSX arrow around a protocol-relative raw ampersand URL",
        "root_cause": (
            "tree-sitter-javascript 0.25.0 represents the entire arrow declaration "
            "as ERROR for the minimal accepted JSX form. The compatibility retry "
            "cannot identify an ampersand node inside that collapsed ERROR range, "
            "so no reliable arrow_function node is available to count."
        ),
        "minimal_reproduction": (
            "const View = () => <link href=\"//x/a&display=swap\" />;"
        ),
    },
}


LIMITATION_FINDINGS: dict[tuple[str, str, str], str] = {
    (
        "layer2-javascript-obojobo",
        "methods_functions",
        "packages/app/obojobo-repository/shared/components/layouts/default.jsx",
    ): "DVL2-PARSER-JSX-007",
    (
        "layer2-typescript-magda",
        "lines_of_code",
        "magda-registry-api/src/main/resources/swagger/swagger-ui-bundle.js",
    ): "DVL2-LOC-REGEX-COMMENT-004",
    (
        "layer2-typescript-magda",
        "lines_of_code",
        "magda-registry-api/src/main/resources/swagger/swagger-ui.js",
    ): "DVL2-LOC-REGEX-COMMENT-004",
    (
        "layer2-typescript-magda",
        "lines_of_code",
        "magda-web-client/src/Components/Chatbot/MarkdownChunkStream.ts",
    ): "DVL2-LOC-REGEX-BACKTICK-005",
    (
        "layer2-typescript-magda",
        "lines_of_code",
        "magda-web-client/src/Components/SQLConsole/SimpleMathTextBox.tsx",
    ): "DVL2-LOC-REGEX-BACKTICK-005",
    (
        "layer2-typescript-magda",
        "lines_of_code",
        "magda-authorization-api/src/Database.ts",
    ): "DVL2-LOC-TEMPLATE-006",
}


def _path(record: dict[str, Any]) -> str:
    paths = record.get("evidence_paths") or []
    return str(paths[0]) if paths else ""


def _key(record: dict[str, Any]) -> tuple[str, str, str]:
    return str(record["subject_key"]), str(record["metric"]), _path(record)


def _construct_evidence(note: str | None) -> dict[str, int]:
    return {
        name: int(value)
        for name, value in re.findall(r"([a-z_]+)=(\d+)", note or "")
    }


def adapter_finding(
    before: dict[str, Any], after: dict[str, Any]
) -> str:
    """Classify one adapter-resolved record from independent construct evidence."""
    if after.get("agreement_status") != records.AGREEMENT_EXACT:
        raise ValueError("an adapter finding requires post-correction exact agreement")
    if before.get("archlens_result") != after.get("archlens_result"):
        raise ValueError("ArchLens changed while adjudicating a reference-only correction")

    old_reference = int(before["reference_result"])
    new_reference = int(after["reference_result"])
    removed = old_reference - new_reference
    evidence = _construct_evidence(after.get("adjudication_note"))

    if removed < 0:
        # The only under-count exposed by the owner review: a direct member of
        # a named class declared inside a function. The corrected reference
        # adds it while the ArchLens value stays unchanged.
        if removed == -1:
            return "DVL2-REF-LOCAL-CLASS-002"
    elif removed > 0:
        if evidence.get("anonymous_container_members", 0) >= removed:
            return "DVL2-REF-OWNER-001"
        if evidence.get("anonymous_functions", 0) >= removed:
            return "DVL2-REF-ANON-DEFAULT-003"

    raise ValueError(
        "corrected adapter equality is not explained by retained construct "
        f"evidence: removed={removed}, evidence={evidence}, key={_key(before)}"
    )


def _cause(finding_id: str) -> str:
    classification = FINDINGS[finding_id]["classification"]
    return {
        "reference_adapter_defect": records.CAUSE_ADAPTER_DEFECT,
        "reference_tool_limitation": records.CAUSE_REFERENCE_DEFECT,
        "parser_limitation": records.CAUSE_PARSER_LIMITATION,
    }[classification]


def _summary(materialized: Iterable[dict[str, Any]]) -> dict[str, Any]:
    items = list(materialized)
    by_status = Counter(str(item["agreement_status"]) for item in items)
    by_cause = Counter(
        str(item["disagreement_cause"])
        for item in items
        if item["agreement_status"] == records.AGREEMENT_DISAGREEMENT
    )
    return {
        "record_count": len(items),
        "by_agreement_status": dict(sorted(by_status.items())),
        "disagreements_by_cause": dict(sorted(by_cause.items())),
        "unresolved_disagreements": by_cause.get(records.CAUSE_UNRESOLVED, 0),
        "all_disagreements_classified": by_cause.get(records.CAUSE_UNRESOLVED, 0) == 0,
        "note": (
            "Agreement rate is not the success criterion. The original raw "
            "documents are retained; this document adds manual causal adjudication."
        ),
    }


def _load(path: Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write(document: dict[str, Any], path: Path) -> None:
    violations = records.validate_document(document)
    if violations:
        raise ValueError(f"adjudicated document violates its schema: {violations[:5]}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def adjudicate(
    baseline_paths: list[Path], final_path: Path, output_directory: Path
) -> dict[str, Any]:
    baseline_documents = [_load(path) for path in baseline_paths]
    final_raw = _load(final_path)
    initial_records = [
        copy.deepcopy(item)
        for document in baseline_documents
        for item in document["records"]
    ]
    final_records = [copy.deepcopy(item) for item in final_raw["records"]]
    final_by_key = {_key(item): item for item in final_records}

    original = [
        item
        for item in initial_records
        if item.get("agreement_status") == records.AGREEMENT_DISAGREEMENT
        and item.get("disagreement_cause") == records.CAUSE_UNRESOLVED
    ]
    if len(original) != 43:
        raise ValueError(f"expected the retained 43 unresolved records, found {len(original)}")

    finding_by_key: dict[tuple[str, str, str], str] = {}
    observations: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for before in original:
        key = _key(before)
        after = final_by_key.get(key)
        if after is None:
            raise ValueError(f"final study has no matching record for {key}")
        finding_id = LIMITATION_FINDINGS.get(key)
        if finding_id is None:
            finding_id = adapter_finding(before, after)
        finding_by_key[key] = finding_id
        observations[finding_id].append({
            "subject_key": before["subject_key"],
            "language": before["language"],
            "metric": before["metric"],
            "evidence_path": _path(before),
            "initial_archlens_result": before["archlens_result"],
            "initial_reference_result": before["reference_result"],
            "final_archlens_result": after["archlens_result"],
            "final_reference_result": after["reference_result"],
            "final_agreement_status": after["agreement_status"],
        })

        before["disagreement_cause"] = _cause(finding_id)
        before["adjudication_status"] = records.ADJUDICATION_MANUAL_SINGLE_REVIEWER
        before["adjudication_note"] = (
            f"{finding_id}: {FINDINGS[finding_id]['root_cause']} "
            "The original unresolved numeric observation is intentionally retained."
        )

    for item in final_records:
        finding_id = finding_by_key.get(_key(item))
        if finding_id is None:
            continue
        item["adjudication_status"] = records.ADJUDICATION_MANUAL_SINGLE_REVIEWER
        history = (
            f"{finding_id}: this path was one of the original 43 unresolved "
            "Layer-2 observations. "
        )
        if item["agreement_status"] == records.AGREEMENT_EXACT:
            history += (
                "Equality follows the independently implemented adapter 1.1.0 "
                "correction; ArchLens production output did not change. "
            )
        else:
            item["disagreement_cause"] = _cause(finding_id)
            history += "The numeric disagreement is retained and causally classified. "
        existing = item.get("adjudication_note")
        item["adjudication_note"] = history + (existing or FINDINGS[finding_id]["root_cause"])

    classification_counts = Counter(
        FINDINGS[finding_id]["classification"]
        for finding_id in finding_by_key.values()
    )
    expected = Counter({
        "reference_adapter_defect": 37,
        "reference_tool_limitation": 5,
        "parser_limitation": 1,
    })
    if classification_counts != expected:
        raise ValueError(
            f"unexpected initial classification distribution: {classification_counts}"
        )

    initial_document = {
        "differential_record_format_version": records.DIFFERENTIAL_RECORD_FORMAT_VERSION,
        "study_metadata": {
            "adjudication_version": ADJUDICATION_VERSION,
            "stage": "initial_observations_after_manual_adjudication",
            "raw_source_documents": [str(Path(path)) for path in baseline_paths],
            "history_preservation": (
                "Raw baseline documents remain unchanged beside this adjudicated copy."
            ),
        },
        "summary": _summary(initial_records),
        "records": initial_records,
    }
    final_document = {
        "differential_record_format_version": records.DIFFERENTIAL_RECORD_FORMAT_VERSION,
        "study_metadata": {
            **copy.deepcopy(final_raw.get("study_metadata") or {}),
            "adjudication_version": ADJUDICATION_VERSION,
            "stage": "post_adapter_correction_and_manual_adjudication",
            "raw_source_document": str(Path(final_path)),
            "history_preservation": (
                "The corrected raw run and all pre-correction raw records remain unchanged."
            ),
        },
        "summary": _summary(final_records),
        "records": final_records,
    }

    if initial_document["summary"]["unresolved_disagreements"] != 0:
        raise ValueError("initial adjudication left an unresolved record")
    if final_document["summary"]["unresolved_disagreements"] != 0:
        raise ValueError("final adjudication left an unresolved record")
    retained_target_disagreements = sum(
        1
        for item in final_records
        if _key(item) in finding_by_key
        and item["agreement_status"] == records.AGREEMENT_DISAGREEMENT
    )
    if retained_target_disagreements != 6:
        raise ValueError(
            "expected six retained disagreements from the original 43 after "
            f"adapter correction, found {retained_target_disagreements}"
        )

    finding_register = {
        "adjudication_version": ADJUDICATION_VERSION,
        "initial_unresolved_count": 43,
        "final_unresolved_count": 0,
        "classification_counts": dict(sorted(classification_counts.items())),
        "findings": {
            finding_id: {
                **finding,
                "affected_record_count": len(observations.get(finding_id, [])),
                "observations": sorted(
                    observations.get(finding_id, []),
                    key=lambda item: (item["subject_key"], item["evidence_path"]),
                ),
            }
            for finding_id, finding in FINDINGS.items()
        },
    }

    output_directory = Path(output_directory)
    _write(initial_document, output_directory / "initial_adjudicated_records.json")
    _write(final_document, output_directory / "final_adjudicated_records.json")
    (output_directory / "findings.json").write_text(
        json.dumps(finding_register, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return finding_register


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, action="append", required=True)
    parser.add_argument("--final", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    register = adjudicate(args.baseline, args.final, args.output)
    print(json.dumps({
        "initial_unresolved_count": register["initial_unresolved_count"],
        "final_unresolved_count": register["final_unresolved_count"],
        "classification_counts": register["classification_counts"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
