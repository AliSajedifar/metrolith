"""Unified offline analysis dossier — a DERIVED composition (3.9).

One offline document that puts a run's measurements beside the standalone
analyses a reader has already produced. It measures nothing. Every number in it
was computed by something else, and the dossier's whole job is to say which
authority produced each one and to refuse to combine sources that do not belong
together.

**Two authorities, never merged.**

* *Authoritative*: the run artifacts read through `validation.artifact_io`, and
  a supplied standalone document that passed its OWN validator.
* *Derived*: everything this module produces — the summaries, the orderings,
  the composed sections. A derived figure is never written back into a
  measurement artifact and is never presented as one.

**Admission is two independent gates, and both must pass.**

1. *Contract compatibility*: the document's `format`/`format_version` must be an
   active exact-version identity in `modules.standalone_contracts`, and its own
   validator must accept it. An unknown format or an unsupported version is not
   "close enough".
2. *Provenance compatibility*: the document must describe the SAME thing the run
   describes — the same run id, or the same analyzed revision and analysis
   scope. A duplication report for a different commit is a valid document and a
   wrong input, and those are different failures with different messages.

A supplement that fails either gate is reported as supplied-but-not-admitted,
with the reason and the compared values, and **its numbers are not shown**.
Showing them under a caveat is how a reader ends up quoting a figure that
describes another revision.

**The dossier is not a schema for anything.** It has its own versioned output
identity, deliberately kept OUT of the standalone-contract registry: that
registry lists analyses that may be admitted as evidence, and a derived
presentation document must never be admissible as evidence for another one.
"""

from __future__ import annotations

import json
from typing import Any, Iterable, Mapping, Sequence

from modules.admission import (
    ADMISSION_MEANINGS,
    ADMITTED,
    CONTRACT_INCOMPATIBLE,
    NOT_SUPPLIED,
    PROVENANCE_MISMATCH,
    PROVENANCE_RULES,
    UNREADABLE,
    VALIDATOR_REJECTED,
)
from modules.config import PROGRAM_VERSION
from modules.standalone_contracts import (
    CHANGED_CODE_FORMAT,
    DUPLICATION_FORMAT,
    HOTSPOT_FORMAT,
    compatibility_status,
    resolve_validator,
)

#: This document's own identity. Versioned independently of every measurement
#: contract, because a change to how evidence is PRESENTED must never look like
#: a change to what was MEASURED.
DOSSIER_FORMAT = "archlens-dossier"
DOSSIER_FORMAT_VERSION = "1.0.0"

#: Supplement admission outcomes and their published meanings now live in
#: :mod:`modules.admission`, because the policy gate asks the same question of
#: the same documents and two copies of this vocabulary would eventually
#: disagree. They are re-exported here unchanged: every name below was part of
#: this module's public surface before the move, and the strings this document
#: embeds are byte-for-byte what they were.

AUTHORITY_STATEMENT = (
    "This dossier is a DERIVED presentation artifact and is not authoritative "
    "for any measurement. Authoritative sources are the run artifacts "
    "(`run_status.json` for run integrity, `analysis.json` for repository "
    "results) and each supplied standalone document validated under its own "
    "contract. Every summary, ordering and composition below is derived from "
    "those and must not be written back into a measurement artifact, quoted as "
    "one, or used to reconstruct one."
)

COMPOSITION_STATEMENT = (
    "Sections are composed, not merged. A standalone analysis keeps its own "
    "contract, its own status vocabulary and its own provenance; the dossier "
    "reports them beside the run's measurements without reconciling them into "
    "a single status, a single population or a single number. No cross-source "
    "total is computed anywhere in this document."
)

NO_SCORE_STATEMENT = (
    "There is no dossier verdict. No composite, no repository rating, no "
    "health figure and no ranking across sources is produced: combining a "
    "duplication count, a complexity distribution and a churn signal into one "
    "number would invent an authority none of the three has."
)

#: What each supplement kind must agree with, and the fields compared, is stated
#: as data in :mod:`modules.admission` so the rule is inspectable and identical
#: in every renderer -- and in the policy gate, which compares the same fields.


class DossierError(ValueError):
    """The dossier could not be composed from the inputs supplied."""


def _text(value: Any) -> str | None:
    return None if value is None else str(value)


def _run_identity(view) -> dict[str, Any]:
    """The run's identity, with no host path in it.

    `run_directory` is deliberately absent. The check result records one and it
    is an absolute local path; reproducing it here would put the operator's
    filesystem into a document meant to be shared.
    """
    manifest = view.manifest
    return {
        "run_id": _text(view.run_id),
        "program_version": _text(manifest.get("program_version")),
        "artifact_schema_version": _text(manifest.get("artifact_schema_version")),
        "metric_contract_version": _text(manifest.get("metric_contract_version")),
        "complexity_contract_version": _text(
            manifest.get("complexity_contract_version")
        ),
        "exclusion_policy_version": _text(manifest.get("exclusion_policy_version")),
        "inventory_schema_version": _text(manifest.get("inventory_schema_version")),
        "run_integrity_status": _text(view.integrity_status),
        "lifecycle": _text(getattr(view.lifecycle, "value", None)),
        "artifact_compatibility": _text(
            getattr(view.compatibility.state, "value", None)
        ),
    }


def _analyzed_scopes(results: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Every (subject, revision, scope) this run measured, for provenance checks.

    `subject_key_of` is handed the reader's `mappingproxy` directly. It reads any
    Mapping, so the `dict(...)` copies at older call sites are not repeated here.
    """
    from modules.subject import subject_key_of

    scopes: list[dict[str, Any]] = []
    for result in results:
        acquisition = result.get("acquisition") or {}
        scopes.append({
            "subject_key": _text(subject_key_of(result)),
            "repository_url": _text(result.get("repository_url")),
            "analyzed_commit_sha": _text(acquisition.get("analyzed_commit_sha")),
            "analysis_scope_hash": _text(result.get("analysis_scope_hash")),
        })
    return scopes


def _matches_scope(
    scopes: Sequence[Mapping[str, Any]],
    *,
    commit_sha: str | None,
    scope_hash: str | None,
) -> tuple[bool, dict[str, Any]]:
    """Does a supplement's revision correspond to something this run measured?

    The commit SHA must match. The scope hash must match too WHEN BOTH SIDES
    HAVE ONE — a document that records no scope hash is not thereby declared
    mismatched, but neither is it declared matched on the strength of a SHA
    alone without saying so.
    """
    evidence = {
        "document_analyzed_commit_sha": commit_sha,
        "document_analysis_scope_hash": scope_hash,
        "run_analyzed_scopes": list(scopes),
    }
    if commit_sha is None:
        return False, {**evidence, "reason": "document records no analyzed commit"}
    for scope in scopes:
        if scope["analyzed_commit_sha"] != commit_sha:
            continue
        if (
            scope_hash is not None
            and scope["analysis_scope_hash"] is not None
            and scope_hash != scope["analysis_scope_hash"]
        ):
            return False, {
                **evidence,
                "reason": (
                    "the analyzed commit matches but the analysis scope hash "
                    "does not; the two analyses did not look at the same files"
                ),
            }
        return True, {
            **evidence,
            "matched_subject_key": scope["subject_key"],
            "scope_hash_compared": (
                scope_hash is not None and scope["analysis_scope_hash"] is not None
            ),
        }
    return False, {
        **evidence,
        "reason": "no repository in this run was analyzed at that commit",
    }


def _duplication_provenance(document, scopes):
    source = document.get("source") or {}
    return _matches_scope(
        scopes,
        commit_sha=_text(source.get("resolved_revision")),
        scope_hash=_text(source.get("analysis_scope_hash")),
    )


def _changed_provenance(document, scopes):
    head = document.get("head") or {}
    matched, evidence = _matches_scope(
        scopes,
        commit_sha=_text(head.get("analyzed_commit_sha")),
        scope_hash=_text(head.get("analysis_scope_hash")),
    )
    return matched, {
        **evidence,
        "base_analyzed_commit_sha": _text(
            (document.get("base") or {}).get("analyzed_commit_sha")
        ),
        "note": (
            "Only the HEAD side is checked against this run. The base side is a "
            "different revision by construction and is never expected to match."
        ),
    }


def _run_id_provenance(document_run_id: str | None, run_id: str | None):
    evidence = {"document_run_id": document_run_id, "run_id": run_id}
    if document_run_id is None or run_id is None:
        return False, {
            **evidence,
            "reason": "one side records no run id, so identity cannot be established",
        }
    if document_run_id != run_id:
        return False, {**evidence, "reason": "the document was produced from a different run"}
    return True, evidence


def _hotspot_provenance(document, run_id):
    return _run_id_provenance(
        _text((document.get("source_run") or {}).get("run_id")), run_id
    )


def _check_provenance(document, run_id):
    return _run_id_provenance(
        _text((document.get("run") or {}).get("run_id")), run_id
    )


def admit_supplement(
    document: Mapping[str, Any] | None,
    *,
    kind: str,
    expected_format: str | None,
    provenance_check,
    read_error: str | None = None,
) -> dict[str, Any]:
    """Run both admission gates and report the outcome without the numbers.

    Order matters and is fixed: identity, then the document's own validator,
    then provenance. Checking provenance first would mean reading fields out of
    a document nothing has yet accepted as well-formed.
    """
    record: dict[str, Any] = {
        "kind": kind,
        "expected_format": expected_format,
        "provenance_rule": PROVENANCE_RULES.get(expected_format or "", None),
    }
    if read_error is not None:
        return {**record, "admission": UNREADABLE, "detail": read_error}
    if document is None:
        return {**record, "admission": NOT_SUPPLIED, "detail": None}

    format_name = document.get("format") or expected_format
    format_version = document.get("format_version")
    record["document_format"] = _text(format_name)
    record["document_format_version"] = _text(format_version)

    # The check result is versioned by its own field rather than by the
    # standalone registry, because `metrolith check` is not a standalone
    # analysis: it evaluates a policy against a run it does not own.
    if expected_format == "archlens-check-result":
        version = _text(document.get("check_result_format_version"))
        record["document_format"] = "archlens-check-result"
        record["document_format_version"] = version
        if version is None:
            return {
                **record,
                "admission": CONTRACT_INCOMPATIBLE,
                "detail": "no check_result_format_version recorded",
            }
    else:
        if format_name != expected_format:
            return {
                **record,
                "admission": CONTRACT_INCOMPATIBLE,
                "detail": (
                    f"expected format {expected_format!r}, "
                    f"document declares {_text(format_name)!r}"
                ),
            }
        status = compatibility_status(format_name, format_version)
        record["contract_compatibility"] = status
        if status != "compatible":
            return {
                **record,
                "admission": CONTRACT_INCOMPATIBLE,
                "detail": f"standalone contract status: {status}",
            }
        try:
            resolve_validator(format_name, format_version)(document)
        except Exception as error:  # noqa: BLE001 - reported, never raised onward
            return {
                **record,
                "admission": VALIDATOR_REJECTED,
                "detail": f"{type(error).__name__}: {error}",
            }

    matched, evidence = provenance_check(document)
    record["provenance_evidence"] = evidence
    if not matched:
        return {**record, "admission": PROVENANCE_MISMATCH, "detail": evidence.get("reason")}
    return {**record, "admission": ADMITTED, "detail": None}


def _duplication_summary(document: Mapping[str, Any]) -> dict[str, Any]:
    """Counts as the duplication contract reports them. Nothing is recomputed."""
    counts = document.get("counts") or {}
    lexical = counts.get("lexical") or {}
    structural = counts.get("structural") or {}
    return {
        "status": _text(document.get("status")),
        "duplication_contract_version": _text(
            document.get("duplication_contract_version")
        ),
        "requested_kinds": list(document.get("requested_kinds") or ()),
        "eligible_file_count": counts.get("eligible_file_count"),
        "candidate_complete_file_count": counts.get("candidate_complete_file_count"),
        "candidate_unavailable_file_count": counts.get(
            "candidate_unavailable_file_count"
        ),
        "lexical": {
            "status": _text(lexical.get("status")),
            "group_count": lexical.get("group_count"),
            "occurrence_count": lexical.get("occurrence_count"),
        },
        "structural": {
            "status": _text(structural.get("status")),
            "initial_group_count": structural.get("initial_group_count"),
            "retained_group_count": structural.get("retained_group_count"),
            "suppressed_group_count": structural.get("suppressed_group_count"),
        },
    }


def _hotspot_summary(document: Mapping[str, Any]) -> dict[str, Any]:
    """Attention-class counts, taken as classified. No class is re-derived.

    The counting itself belongs to `modules.hotspots.attention_class_counts`,
    which is also what the Policy evaluator reads. One word, one number, one
    definition: a private tally here would be the second implementation that
    eventually disagrees with the gate about how many high-attention files a
    repository has.

    Only labels with a non-zero count are presented, which is what this section
    has always shown. The shared function returns a total tally over the closed
    vocabulary, zeros included; suppressing them is a presentation choice and
    stays here, where presentation choices belong.
    """
    from modules.hotspots import attention_class_counts

    tally = {
        label: count
        for label, count in attention_class_counts(document).items()
        if count
    }
    return {
        "hotspot_row_count": len(document.get("hotspots") or ()),
        "repository_count": len(document.get("repositories") or ()),
        "by_attention_class": dict(sorted(tally.items())),
        "purpose": _text(document.get("purpose")),
        "ordering": _text(document.get("ordering")),
    }


def _changed_summary(document: Mapping[str, Any]) -> dict[str, Any]:
    """The changed-code counts, with the identity boundary carried along."""
    from modules.changed_code import IDENTITY_BOUNDARY_STATEMENT

    counts = document.get("counts") or {}
    return {
        "status": _text(document.get("status")),
        "reasons": [str(reason) for reason in document.get("reasons") or ()],
        "base_resolved_commit_sha": _text(
            (document.get("base") or {}).get("resolved_commit_sha")
        ),
        "head_resolved_commit_sha": _text(
            (document.get("head") or {}).get("resolved_commit_sha")
        ),
        "git_changed_files": counts.get("git_changed_files"),
        "changed_code_files": counts.get("changed_code_files"),
        "non_code_files": counts.get("non_code_files"),
        "added_diff_lines": counts.get("added_diff_lines"),
        "deleted_diff_lines": counts.get("deleted_diff_lines"),
        "by_change_kind": dict(sorted((counts.get("by_change_kind") or {}).items())),
        "identity_boundary": IDENTITY_BOUNDARY_STATEMENT,
    }


def _policy_summary(document: Mapping[str, Any]) -> dict[str, Any]:
    """The policy verdict as `check` recorded it.

    The verdict is reported, never recomputed, and never softened: a dossier
    that reported a failing policy as anything other than failing would be
    editing a gate result.
    """
    counts = document.get("counts") or {}
    policy = document.get("policy") or {}
    return {
        "check_result_format_version": _text(
            document.get("check_result_format_version")
        ),
        "verdict": _text(document.get("verdict")),
        "exit_code": document.get("exit_code"),
        "failure_kind": _text(document.get("failure_kind")),
        "evaluated_at": _text(document.get("evaluated_at")),
        "policy_document_format_version": _text(
            policy.get("policy_document_format_version")
        ),
        "findings": counts.get("findings"),
        "failing": counts.get("failing"),
        "waived": counts.get("waived"),
        "rules_evaluated": counts.get("rules_evaluated"),
        "not_evaluable": len(document.get("not_evaluable") or ()),
        "expired_waivers": len(document.get("expired_waivers") or ()),
    }


_SUMMARIZERS = {
    "duplication": _duplication_summary,
    "hotspots": _hotspot_summary,
    "changed_code": _changed_summary,
    "policy": _policy_summary,
}

# Dossier 1.0 keeps its public/private callable names for compatibility, but
# all active admission and subject-binding decisions are delegated to the
# neutral service shared with Report View.  The assignments are intentionally
# below the historical helper definitions: callers that imported these names
# continue to receive the same functions and records while there is only one
# active decision boundary.
from modules import report_composition as _report_composition

_text = _report_composition.text
_analyzed_scopes = _report_composition.analyzed_scopes
_matches_scope = _report_composition.matches_scope
_duplication_provenance = _report_composition.duplication_provenance
_changed_provenance = _report_composition.changed_provenance
_run_id_provenance = _report_composition.run_id_provenance
_hotspot_provenance = _report_composition.hotspot_provenance
_check_provenance = _report_composition.check_provenance
admit_supplement = _report_composition.admit_supplement


def _repository_sections(
    view, results: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """Per-repository overview, composition and distribution, from the run only."""
    from modules import complexity_distribution, complexity_view, source_composition
    from modules.subject import subject_key_of

    rows_by_subject: dict[str, list[Mapping[str, Any]]] = {}
    if view.has_callable_artifact:
        for row in view.stream_callables():
            rows_by_subject.setdefault(str(row.get("subject_key") or ""), []).append(row)

    sections: list[dict[str, Any]] = []
    for result in sorted(
        results, key=lambda item: subject_key_of(item).casefold()
    ):
        key = subject_key_of(result)
        rows = rows_by_subject.get(key, [])
        aggregate = (result.get("metrics") or {}).get("aggregate") or {}
        sections.append({
            "subject_key": _text(key),
            "repository_url": _text(result.get("repository_url")),
            "analysis_status": _text(result.get("analysis_status")),
            "core_metric_status": _text(result.get("core_metric_status")),
            "primary_language": _text(
                (result.get("metrics") or {}).get("primary_language_name")
            ),
            "overview": {
                "source_files": aggregate.get("source_files"),
                "lines_of_code": aggregate.get("lines_of_code"),
                "classes_structs": aggregate.get("classes_structs"),
                "methods_functions": aggregate.get("methods_functions"),
            },
            "source_composition": source_composition.presentation(result),
            "source_composition_provenance": source_composition.provenance(
                view.manifest, result
            ),
            "complexity": complexity_view.presentation(result),
            "cognitive_complexity": complexity_view.cognitive_presentation(result, rows),
            "complexity_distribution": complexity_distribution.presentation(result, rows),
            "complexity_distribution_provenance": complexity_distribution.provenance(
                view.manifest, result
            ),
        })
    return sections


def build_dossier(
    view,
    *,
    duplication: Mapping[str, Any] | None = None,
    hotspots: Mapping[str, Any] | None = None,
    changed: Mapping[str, Any] | None = None,
    policy: Mapping[str, Any] | None = None,
    read_errors: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Compose one dossier from an authoritative run and optional supplements.

    Every supplement is optional and its absence is reported as an absence of
    INPUT, never as a measured absence of findings. A supplement that fails
    admission contributes no numbers at all.
    """
    results = list(view.repositories)
    scopes = _analyzed_scopes(results)
    run_id = _text(view.run_id)
    errors = dict(read_errors or {})

    supplements: list[dict[str, Any]] = []
    for kind, document, expected_format, check in (
        (
            "duplication", duplication, DUPLICATION_FORMAT,
            lambda item: _duplication_provenance(item, scopes),
        ),
        (
            "hotspots", hotspots, HOTSPOT_FORMAT,
            lambda item: _hotspot_provenance(item, run_id),
        ),
        (
            "changed_code", changed, CHANGED_CODE_FORMAT,
            lambda item: _changed_provenance(item, scopes),
        ),
        (
            "policy", policy, "archlens-check-result",
            lambda item: _check_provenance(item, run_id),
        ),
    ):
        record = admit_supplement(
            document,
            kind=kind,
            expected_format=expected_format,
            provenance_check=check,
            read_error=errors.get(kind),
        )
        record["admission_meaning"] = ADMISSION_MEANINGS[record["admission"]]
        record["summary"] = (
            _SUMMARIZERS[kind](document)
            if record["admission"] == ADMITTED and document is not None
            else None
        )
        supplements.append(record)

    return {
        "format": DOSSIER_FORMAT,
        "format_version": DOSSIER_FORMAT_VERSION,
        "program_version": PROGRAM_VERSION,
        "authority": {
            "this_document": "derived",
            "statement": AUTHORITY_STATEMENT,
            "composition": COMPOSITION_STATEMENT,
            "no_composite": NO_SCORE_STATEMENT,
            "authoritative_sources": [
                "run_status.json (run integrity)",
                "analysis.json (repository results)",
                "each supplied standalone document, under its own contract",
            ],
            "derived_content": [
                "every section of this dossier",
                "supplement summaries",
                "orderings",
            ],
        },
        "run": _run_identity(view),
        "analyzed_scopes": scopes,
        "repositories": _repository_sections(view, results),
        "supplements": supplements,
        "admission_meanings": dict(ADMISSION_MEANINGS),
        "provenance_rules": dict(PROVENANCE_RULES),
    }


def validate_dossier(document: Mapping[str, Any]) -> None:
    """Structural invariants of a dossier document.

    The one that matters: a supplement that was not admitted carries no summary.
    Checked rather than trusted, because that single field is the difference
    between composing evidence and laundering it.
    """
    if document.get("format") != DOSSIER_FORMAT:
        raise DossierError(f"not a dossier document: {document.get('format')!r}")
    if document.get("format_version") != DOSSIER_FORMAT_VERSION:
        raise DossierError(
            f"unsupported dossier version: {document.get('format_version')!r}"
        )
    if document.get("authority", {}).get("this_document") != "derived":
        raise DossierError("a dossier must declare itself derived")
    for record in document.get("supplements") or ():
        admission = record.get("admission")
        if admission not in ADMISSION_MEANINGS:
            raise DossierError(f"unknown admission outcome: {admission!r}")
        if admission != ADMITTED and record.get("summary") is not None:
            raise DossierError(
                f"supplement {record.get('kind')!r} was not admitted "
                f"({admission}) but carries a summary"
            )
        if admission == ADMITTED and record.get("summary") is None:
            raise DossierError(
                f"supplement {record.get('kind')!r} was admitted but carries "
                "no summary"
            )


def canonical_json(document: Mapping[str, Any]) -> str:
    validate_dossier(document)
    return json.dumps(
        document, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False
    ) + "\n"


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------


def _row(cells: Iterable[Any]) -> str:
    return "| " + " | ".join(str(cell) for cell in cells) + " |"


def _markdown_lines(document: Mapping[str, Any]) -> list[str]:
    from modules.summary import escape_markdown as escape
    from modules.presentation import scalar

    def display(value: Any) -> str:
        return escape(scalar(value, absent="unavailable"))

    lines: list[str] = ["# Metrolith analysis dossier", ""]
    lines.append(
        f"Format `{document['format']}` {escape(document['format_version'])}, "
        f"composed by Metrolith {escape(document['program_version'])}."
    )
    lines.extend(["", "## Authority", ""])
    lines.append(f"**{escape(document['authority']['statement'])}**")
    lines.extend(["", document["authority"]["composition"], ""])
    lines.append(document["authority"]["no_composite"])
    lines.extend(["", "Authoritative in this document:", ""])
    for item in document["authority"]["authoritative_sources"]:
        lines.append(f"- {escape(item)}")
    lines.extend(["", "Derived in this document:", ""])
    for item in document["authority"]["derived_content"]:
        lines.append(f"- {escape(item)}")

    run = document["run"]
    lines.extend(["", "## Run", "", "| Field | Value |", "|---|---|"])
    for label, key in (
        ("Run id", "run_id"),
        ("Program version", "program_version"),
        ("Artifact Schema", "artifact_schema_version"),
        ("Metric Contract", "metric_contract_version"),
        ("Complexity Contract", "complexity_contract_version"),
        ("Exclusion Policy", "exclusion_policy_version"),
        ("Inventory Schema", "inventory_schema_version"),
        ("Run integrity status", "run_integrity_status"),
        ("Lifecycle", "lifecycle"),
        ("Artifact compatibility", "artifact_compatibility"),
    ):
        lines.append(_row([label, display(run.get(key))]))

    lines.extend(_supplement_lines(document, display))
    lines.extend(_repository_lines(document, display))

    lines.extend(["", "## Limitations", ""])
    lines.append(
        "- This dossier is derived and non-authoritative; nothing here may be "
        "parsed back as a measurement."
    )
    lines.append(
        "- A supplement that was not admitted contributes no numbers. Its "
        "absence from a section is an absence of admitted input, never a "
        "measured zero."
    )
    lines.append(
        "- Sections are composed side by side. No status, population or total "
        "is combined across sources."
    )
    lines.append(
        "- Figures are not measurement-equivalent across languages, and no "
        "cross-language composite is produced."
    )
    return lines


def _supplement_lines(document: Mapping[str, Any], escape) -> list[str]:
    """Admission first, numbers second — and only for admitted supplements."""
    lines: list[str] = ["", "## Supplements", ""]
    lines.append(
        "Each supplied document is admitted only if its format identity is an "
        "active exact-version contract, its own validator accepts it, AND its "
        "provenance matches this run. A document failing any gate is listed "
        "with the reason and contributes no figures."
    )
    lines.extend([
        "",
        "| Supplement | Admission | Format | Version | Detail |",
        "|---|---|---|---|---|",
    ])
    for record in document["supplements"]:
        lines.append(_row([
            escape(record["kind"]),
            f"**{escape(record['admission'])}**",
            escape(record.get("document_format")),
            escape(record.get("document_format_version")),
            escape(record.get("detail") or ""),
        ]))
    lines.extend(["", "Admission outcomes:", ""])
    for name, meaning in sorted(document["admission_meanings"].items()):
        lines.append(f"- `{escape(name)}`: {meaning}")

    for record in document["supplements"]:
        summary = record.get("summary")
        if summary is None:
            continue
        lines.extend([
            "", f"### {escape(record['kind'])}", "",
            "| Field | Value |", "|---|---|",
        ])
        for key, value in sorted(summary.items()):
            if isinstance(value, Mapping):
                for inner_key, inner in sorted(value.items()):
                    lines.append(_row([
                        f"{escape(key)}.{escape(inner_key)}",
                        escape("unavailable" if inner is None else inner),
                    ]))
            elif isinstance(value, list):
                from modules.presentation import scalar

                lines.append(_row([
                    escape(key),
                    escape(", ".join(scalar(item, absent="unavailable") for item in value)) or "none",
                ]))
            else:
                lines.append(_row([
                    escape(key), escape("unavailable" if value is None else value)
                ]))
    return lines


def _repository_lines(document: Mapping[str, Any], escape) -> list[str]:
    """Run-measured evidence per repository: overview, composition, shape."""
    from modules import complexity_distribution, source_composition

    lines: list[str] = ["", "## Repositories", ""]
    if not document["repositories"]:
        lines.append(
            "This run recorded no repository result. That is an absence of "
            "measurement, not a measured empty repository."
        )
        return lines

    for section in document["repositories"]:
        from modules.presentation import subject_display

        name = subject_display(section).name
        lines.extend(["", f"### {escape(name)}", ""])
        lines.append(f"- Subject key: `{escape(section['subject_key'])}`")
        lines.append(f"- Analysis status: `{escape(section['analysis_status'])}`")
        lines.append(f"- Primary language: `{escape(section['primary_language'])}`")
        overview = section["overview"]
        lines.extend(["", "| Metric | Value |", "|---|---:|"])
        for label, key in (
            ("Source files", "source_files"),
            ("Lines of code", "lines_of_code"),
            ("Classes / structs", "classes_structs"),
            ("Methods / functions", "methods_functions"),
        ):
            value = overview.get(key)
            lines.append(_row([
                label, "unavailable" if value is None else value
            ]))

        composition = section["source_composition"]
        lines.extend(["", "#### Source composition", ""])
        lines.append(f"State: `{escape(composition['state'])}` — "
                     f"{escape(composition['state_meaning'])}")
        lines.extend(["", "| Figure | Value |", "|---|---:|"])
        for entry in composition["counts"]:
            lines.append(_row([escape(entry["label"]), escape(entry["rendered"])]))
        for entry in composition["ratios"]:
            lines.append(_row([
                f"{escape(entry['label'])} (÷ {escape(entry['denominator_field'])})",
                escape(entry["rendered"]),
            ]))
        lines.append("")
        lines.append(source_composition.DESCRIPTIVE_ONLY)

        distribution = section["complexity_distribution"]
        lines.extend(["", "#### Complexity distribution", ""])
        lines.append(f"State: `{escape(distribution['state'])}` — "
                     f"{escape(distribution['state_meaning'])}")
        if not distribution["evaluable"] or distribution["combined"] is None:
            lines.extend([
                "",
                "No evaluable complexity measurement, so no distribution is "
                "reported. An unavailable measurement has no shape.",
            ])
        else:
            positions = distribution["combined"]["percentile_positions"]
            header = " | ".join(f"p{position}" for position in positions)
            lines.extend([
                "",
                f"| Metric | Measured | Min | {header} | Max |",
                "|---|---:|---:|" + "---:|" * len(positions) + "---:|",
            ])
            for entry in distribution["combined"]["distributions"]:
                cells = " | ".join(
                    escape(entry["rendered"][f"p{position}"]) for position in positions
                )
                lines.append(
                    f"| {escape(entry['label'])} | {entry['measured_count']} "
                    f"| {escape(entry['rendered']['min'])} | {cells} "
                    f"| {escape(entry['rendered']['max'])} |"
                )
            lines.append("")
            lines.append(complexity_distribution.PERCENTILE_METHOD)
        lines.append("")
        lines.append(complexity_distribution.DESCRIPTIVE_ONLY)
    return lines


def render_markdown(document: Mapping[str, Any]) -> str:
    """Render a dossier as deterministic Markdown.

    Same inputs, same bytes. The dossier adds no timestamp of its own and emits
    no host path; where an input document records one — a policy evaluation
    date, for instance — it is reproduced as that document's recorded evidence
    and nothing is generated here.
    """
    validate_dossier(document)
    return "\n".join(_markdown_lines(document)) + "\n"


__all__ = [
    "ADMISSION_MEANINGS",
    "ADMITTED",
    "AUTHORITY_STATEMENT",
    "COMPOSITION_STATEMENT",
    "CONTRACT_INCOMPATIBLE",
    "DOSSIER_FORMAT",
    "DOSSIER_FORMAT_VERSION",
    "DossierError",
    "NOT_SUPPLIED",
    "NO_SCORE_STATEMENT",
    "PROVENANCE_MISMATCH",
    "PROVENANCE_RULES",
    "UNREADABLE",
    "VALIDATOR_REJECTED",
    "admit_supplement",
    "build_dossier",
    "canonical_json",
    "render_markdown",
    "validate_dossier",
]
