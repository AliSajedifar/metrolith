"""Neutral admission/composition service for derived presentation documents.

This module answers one question for Dossier and Report View: may an optional
document speak for the validated run supplied by the caller?  It does not
project measurements, render output, evaluate Policy, compare a Ratchet, or
invent trust.  Those concerns remain with their existing authorities.

The legacy admission record returned by :func:`admit_supplement` is deliberately
byte-compatible with Dossier 1.0.  Report View normalizes that record into its
own closed vocabulary, including an explicit ``ambiguous`` state when more than
one run subject could match a supplement.  Dossier 1.0 represents that newer
distinction as ``provenance_mismatch`` because widening its released vocabulary
would be a Dossier contract change.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping, Sequence

from modules.admission import (
    CONTRACT_INCOMPATIBLE,
    NOT_SUPPLIED,
    PROVENANCE_MISMATCH,
    PROVENANCE_RULES,
    UNREADABLE,
    VALIDATOR_REJECTED,
)
from modules.standalone_contracts import compatibility_status, resolve_validator


CHECK_RESULT_FORMAT = "archlens-check-result"
CHECK_RESULT_SCHEMA = "check_result_output"


def text(value: Any) -> str | None:
    return None if value is None else str(value)


def analyzed_scopes(results: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Return every authoritative subject/revision/scope coordinate."""

    from modules.subject import subject_key_of

    scopes: list[dict[str, Any]] = []
    for result in results:
        acquisition = result.get("acquisition") or {}
        scopes.append({
            "subject_key": text(subject_key_of(result)),
            "repository_url": text(result.get("repository_url")),
            "analyzed_commit_sha": text(acquisition.get("analyzed_commit_sha")),
            "analysis_scope_hash": text(result.get("analysis_scope_hash")),
        })
    return sorted(scopes, key=lambda item: str(item["subject_key"]).casefold())


def matches_scope(
    scopes: Sequence[Mapping[str, Any]],
    *,
    commit_sha: str | None,
    scope_hash: str | None,
) -> tuple[bool, dict[str, Any]]:
    """Match without choosing the first of multiple possible subjects."""

    evidence: dict[str, Any] = {
        "document_analyzed_commit_sha": commit_sha,
        "document_analysis_scope_hash": scope_hash,
        "run_analyzed_scopes": list(scopes),
    }
    if commit_sha is None:
        return False, {**evidence, "reason": "document records no analyzed commit"}

    revision_matches = [
        scope for scope in scopes if scope.get("analyzed_commit_sha") == commit_sha
    ]
    compatible = [
        scope
        for scope in revision_matches
        if not (
            scope_hash is not None
            and scope.get("analysis_scope_hash") is not None
            and scope_hash != scope.get("analysis_scope_hash")
        )
    ]
    if len(compatible) > 1:
        candidates = sorted(
            str(scope.get("subject_key")) for scope in compatible
        )
        return False, {
            **evidence,
            "ambiguous": True,
            "candidate_subject_keys": candidates,
            "reason": (
                "more than one subject in this run matches the supplement; "
                "no first-match subject was selected"
            ),
        }
    if len(compatible) == 1:
        matched = compatible[0]
        return True, {
            **evidence,
            "matched_subject_key": matched.get("subject_key"),
            "scope_hash_compared": (
                scope_hash is not None
                and matched.get("analysis_scope_hash") is not None
            ),
        }
    if revision_matches:
        return False, {
            **evidence,
            "reason": (
                "the analyzed commit matches but the analysis scope hash does "
                "not; the two analyses did not look at the same files"
            ),
        }
    return False, {
        **evidence,
        "reason": "no repository in this run was analyzed at that commit",
    }


def duplication_provenance(document: Mapping[str, Any], scopes):
    source = document.get("source") or {}
    return matches_scope(
        scopes,
        commit_sha=text(source.get("resolved_revision")),
        scope_hash=text(source.get("analysis_scope_hash")),
    )


def changed_provenance(document: Mapping[str, Any], scopes):
    head = document.get("head") or {}
    matched, evidence = matches_scope(
        scopes,
        commit_sha=text(head.get("analyzed_commit_sha")),
        scope_hash=text(head.get("analysis_scope_hash")),
    )
    return matched, {
        **evidence,
        "base_analyzed_commit_sha": text(
            (document.get("base") or {}).get("analyzed_commit_sha")
        ),
        "note": (
            "Only the HEAD side is checked against this run. The base side is a "
            "different revision by construction and is never expected to match."
        ),
    }


def run_id_provenance(document_run_id: str | None, run_id: str | None):
    evidence = {"document_run_id": document_run_id, "run_id": run_id}
    if document_run_id is None or run_id is None:
        return False, {
            **evidence,
            "reason": "one side records no run id, so identity cannot be established",
        }
    if document_run_id != run_id:
        return False, {
            **evidence,
            "reason": "the document was produced from a different run",
        }
    return True, evidence


def hotspot_provenance(document: Mapping[str, Any], run_id: str | None):
    return run_id_provenance(
        text((document.get("source_run") or {}).get("run_id")), run_id
    )


def check_provenance(document: Mapping[str, Any], run_id: str | None):
    return run_id_provenance(
        text((document.get("run") or {}).get("run_id")), run_id
    )


def _validate_check_result(document: Mapping[str, Any]) -> None:
    """Use the registered Check Result schema; never create a second validator."""

    from validation.artifact_io.schema_store import validate_document

    problems = validate_document(CHECK_RESULT_SCHEMA, document, "policy_result")
    if problems:
        first = problems[0]
        raise ValueError(
            f"Check Result structural validation failed at {first.location}: "
            f"{first.message}"
        )


def admit_supplement(
    document: Mapping[str, Any] | None,
    *,
    kind: str,
    expected_format: str | None,
    provenance_check: Callable[[Mapping[str, Any]], tuple[bool, dict[str, Any]]],
    read_error: str | None = None,
) -> dict[str, Any]:
    """Apply strict identity, existing validation, then provenance admission."""

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
    record["document_format"] = text(format_name)
    record["document_format_version"] = text(format_version)

    if expected_format == CHECK_RESULT_FORMAT:
        version = text(document.get("check_result_format_version"))
        record["document_format"] = CHECK_RESULT_FORMAT
        record["document_format_version"] = version
        if version is None:
            return {
                **record,
                "admission": CONTRACT_INCOMPATIBLE,
                "detail": "no check_result_format_version recorded",
            }
        try:
            _validate_check_result(document)
        except Exception as error:  # noqa: BLE001 - refusal is data
            return {
                **record,
                "admission": VALIDATOR_REJECTED,
                "detail": f"{type(error).__name__}: {error}",
            }
    else:
        if format_name != expected_format:
            return {
                **record,
                "admission": CONTRACT_INCOMPATIBLE,
                "detail": (
                    f"expected format {expected_format!r}, "
                    f"document declares {text(format_name)!r}"
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
        except Exception as error:  # noqa: BLE001 - refusal is data
            return {
                **record,
                "admission": VALIDATOR_REJECTED,
                "detail": f"{type(error).__name__}: {error}",
            }

    matched, evidence = provenance_check(document)
    record["provenance_evidence"] = evidence
    if not matched:
        return {
            **record,
            "admission": PROVENANCE_MISMATCH,
            "detail": evidence.get("reason"),
        }
    return {**record, "admission": "admitted", "detail": None}


def report_view_admission_state(record: Mapping[str, Any]) -> str:
    """Translate legacy admission into the Report View 1.0 vocabulary."""

    state = record.get("admission")
    if state == "contract_incompatible":
        return "incompatible"
    if state == "validator_rejected":
        return "refused"
    if state == "provenance_mismatch" and (
        (record.get("provenance_evidence") or {}).get("ambiguous") is True
    ):
        return "ambiguous"
    if state in {
        "admitted", "not_supplied", "unreadable", "provenance_mismatch"
    }:
        return str(state)
    return "refused"


__all__ = [
    "CHECK_RESULT_FORMAT",
    "admit_supplement",
    "analyzed_scopes",
    "changed_provenance",
    "check_provenance",
    "duplication_provenance",
    "hotspot_provenance",
    "matches_scope",
    "report_view_admission_state",
    "run_id_provenance",
    "text",
]
