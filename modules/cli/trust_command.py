"""Supported producer for canonical Trusted Evidence Receipt 1.0 bytes.

The command in this module does not perform Hotspot or Duplication analysis.
It admits the exact canonical documents emitted by those existing producers,
binds them to one finalized run, and writes the receipt consumed by
``metrolith check --gate-mode protected_required``.

The receipt digest printed by this command is a custody handoff value.  It is
never accepted implicitly by ``check``: the expected digest must still arrive
through an owner-controlled channel outside the receipt and evidence bytes.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from archlens_json import (
    EVIDENCE_JSON_LIMITS,
    StrictJsonError,
    dumps_strict,
    loads_bytes,
    read_bounded_bytes,
)
from modules.policy import evidence as evidence_module
from modules.policy import trust as trust_module
from modules.run_artifacts import atomic_write_text


EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2

_PLACEHOLDER = re.compile(
    r"(?:<[^>]+>|\b(?:FULL_SHA|SHA256|OWNER|METROLITH_REPOSITORY|REPLACE_ME|PLACEHOLDER)\b)"
)


class ReceiptProductionError(ValueError):
    """A bounded, path-free reason a receipt could not be produced."""

    def __init__(self, code: str, detail: str | None = None) -> None:
        self.code = code
        self.detail = detail
        super().__init__(code if detail is None else f"{code}: {detail}")


@dataclass(frozen=True, slots=True)
class ReceiptProduction:
    payload: bytes
    sha256: str
    run_manifest_sha256: str
    evidence_sha256: Mapping[str, str]

    def summary(self) -> dict[str, Any]:
        return {
            "receipt_format": trust_module.RECEIPT_FORMAT,
            "receipt_format_version": trust_module.RECEIPT_FORMAT_VERSION,
            "receipt_sha256": self.sha256,
            "run_manifest_sha256": self.run_manifest_sha256,
            "evidence_sha256": dict(sorted(self.evidence_sha256.items())),
            "custody_note": (
                "pin receipt_sha256 in an owner-controlled channel before a "
                "protected gate consumes these bytes; this output does not "
                "self-authenticate the receipt"
            ),
        }


def add_parser(subparsers) -> None:
    trust = subparsers.add_parser(
        "trust",
        help="Create protected-workflow trust material",
        description=(
            "Bounded protected-workflow utilities. These commands do not turn "
            "locally produced bytes into a protected gate by themselves."
        ),
    )
    trust_sub = trust.add_subparsers(dest="trust_command", required=True)
    receipt = trust_sub.add_parser(
        "receipt",
        help="Operate on Trusted Evidence Receipt documents",
    )
    receipt_sub = receipt.add_subparsers(dest="receipt_command", required=True)
    create = receipt_sub.add_parser(
        "create",
        help="Create canonical Trusted Evidence Receipt 1.0 bytes",
        description=(
            "Admit exact canonical Hotspot and/or Duplication documents against "
            "one finalized run and bind their bytes to a protected evaluator."
        ),
    )
    create.add_argument("run_directory", type=Path)
    create.add_argument("--evaluator-repository", required=True)
    create.add_argument("--evaluator-revision", required=True)
    create.add_argument("--evaluator-source-sha256", required=True)
    create.add_argument("--hotspots", type=Path)
    create.add_argument("--duplication", type=Path)
    create.add_argument("--output", type=Path, required=True)
    create.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace an existing receipt only after the caller explicitly opts in",
    )


def _reject_placeholder(value: str, label: str) -> None:
    if _PLACEHOLDER.search(value):
        raise ReceiptProductionError("placeholder_trust_configuration", label)


def _canonical_evidence(
    kind: str,
    path: Path,
    *,
    run_id: str,
    scopes: tuple[dict[str, Any], ...],
) -> tuple[evidence_module.EvidenceRecord, bytes, str]:
    try:
        payload = read_bounded_bytes(
            path,
            source=f"{kind} evidence",
            limits=EVIDENCE_JSON_LIMITS,
        )
        document = loads_bytes(
            payload,
            source=f"{kind} evidence",
            limits=EVIDENCE_JSON_LIMITS,
            expect=dict,
        )
    except StrictJsonError as exc:
        raise ReceiptProductionError("evidence_unreadable", f"{kind}:{exc.code}") from exc

    canonical = dumps_strict(
        document,
        source=f"canonical {kind} evidence",
        limits=EVIDENCE_JSON_LIMITS,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
        trailing_newline=True,
    ).encode("utf-8")
    if canonical != payload:
        raise ReceiptProductionError("evidence_not_canonical", kind)

    digest = hashlib.sha256(payload).hexdigest()
    record = evidence_module.admit_document(
        kind,
        document,
        run_id=run_id,
        scopes=scopes,
        document_sha256=digest,
    )
    if not record.admitted:
        reason = record.reason or record.admission
        raise ReceiptProductionError("evidence_not_admitted", f"{kind}:{reason}")
    return record, payload, digest


def create_receipt(
    run_directory: Path,
    *,
    evaluator_repository: str,
    evaluator_revision: str,
    evaluator_source_sha256: str,
    hotspots: Path | None = None,
    duplication: Path | None = None,
) -> ReceiptProduction:
    """Create and self-validate canonical receipt bytes without writing them."""

    for value, label in (
        (evaluator_repository, "evaluator_repository"),
        (evaluator_revision, "evaluator_revision"),
        (evaluator_source_sha256, "evaluator_source_sha256"),
    ):
        _reject_placeholder(value, label)

    evaluator = trust_module.EvaluatorIdentity.protected(
        repository=evaluator_repository,
        revision=evaluator_revision,
        source_sha256=evaluator_source_sha256,
        installed_version="receipt-producer-bound-by-source",
    )

    from validation.artifact_io.compatibility import CompatibilityState
    from validation.artifact_io.errors import ArtifactStructureError
    from validation.artifact_io.reader import open_run

    try:
        view = open_run(Path(run_directory))
    except (ArtifactStructureError, OSError) as exc:
        raise ReceiptProductionError("run_unreadable", type(exc).__name__) from exc
    if view.compatibility.state is not CompatibilityState.SUPPORTED:
        raise ReceiptProductionError("run_contract_unsupported")
    if not view.succeeded:
        raise ReceiptProductionError("run_not_successful")

    manifest_payload = view.reader.document_bytes("run_manifest.json")
    manifest_sha256 = hashlib.sha256(manifest_payload).hexdigest()
    scopes = evidence_module.analyzed_scopes(view.repositories)
    scope_by_key = {str(item["subject_key"]): item for item in scopes}

    entries: list[dict[str, Any]] = []
    evidence_digests: dict[str, str] = {}
    requested = {
        evidence_module.EVIDENCE_HOTSPOTS: hotspots,
        evidence_module.EVIDENCE_DUPLICATION: duplication,
    }
    for kind in sorted(requested):
        path = requested[kind]
        if path is None:
            continue
        record, _payload, document_sha256 = _canonical_evidence(
            kind,
            path,
            run_id=view.run_id,
            scopes=scopes,
        )
        subjects: list[dict[str, str]] = []
        for subject_key in sorted(record.subject_keys):
            scope = scope_by_key.get(subject_key)
            if scope is None:
                raise ReceiptProductionError("subject_binding_missing", kind)
            revision = scope.get("analyzed_commit_sha")
            scope_hash = scope.get("analysis_scope_hash")
            if not isinstance(revision, str) or not revision:
                raise ReceiptProductionError("subject_revision_missing", kind)
            if not isinstance(scope_hash, str) or not scope_hash:
                raise ReceiptProductionError("subject_scope_missing", kind)
            subjects.append(
                {
                    "subject_key": subject_key,
                    "analyzed_revision": revision,
                    "analysis_scope_hash": scope_hash,
                }
            )
        analysis_contract = (
            record.document_analysis_contract_version
            or record.document_format_version
        )
        entries.append(
            {
                "kind": kind,
                "document_sha256": document_sha256,
                "producer": evaluator.receipt_dict(),
                "run_manifest_sha256": manifest_sha256,
                "run_id": view.run_id,
                "document_format": record.document_format,
                "document_format_version": record.document_format_version,
                "analysis_contract_version": analysis_contract,
                "subjects": subjects,
            }
        )
        evidence_digests[kind] = document_sha256

    document = {
        "receipt_format": trust_module.RECEIPT_FORMAT,
        "receipt_format_version": trust_module.RECEIPT_FORMAT_VERSION,
        "evaluator": evaluator.receipt_dict(),
        "run_manifest_sha256": manifest_sha256,
        "evidence": entries,
    }
    payload = trust_module.canonical_receipt_bytes(document)
    digest = hashlib.sha256(payload).hexdigest()
    trust_module.parse_receipt_bytes(payload, verified_sha256=digest)
    return ReceiptProduction(
        payload=payload,
        sha256=digest,
        run_manifest_sha256=manifest_sha256,
        evidence_sha256=evidence_digests,
    )


def handle(args) -> int:
    if getattr(args, "trust_command", None) != "receipt" or getattr(
        args, "receipt_command", None
    ) != "create":
        print("[ERROR] unsupported trust command", file=sys.stderr)
        return EXIT_USAGE

    output = Path(args.output)
    if output.exists() and not args.overwrite:
        print("[ERROR] receipt output already exists; pass --overwrite", file=sys.stderr)
        return EXIT_USAGE
    try:
        produced = create_receipt(
            Path(args.run_directory),
            evaluator_repository=args.evaluator_repository,
            evaluator_revision=args.evaluator_revision,
            evaluator_source_sha256=args.evaluator_source_sha256,
            hotspots=args.hotspots,
            duplication=args.duplication,
        )
        atomic_write_text(output, produced.payload.decode("utf-8"))
    except (ReceiptProductionError, OSError, trust_module.TrustAdmissionError) as exc:
        detail = exc.code if isinstance(exc, ReceiptProductionError) else type(exc).__name__
        print(f"[ERROR] trusted receipt creation refused: {detail}", file=sys.stderr)
        return EXIT_ERROR

    sys.stdout.write(json.dumps(produced.summary(), sort_keys=True) + "\n")
    return EXIT_OK


__all__ = [
    "EXIT_ERROR",
    "EXIT_OK",
    "EXIT_USAGE",
    "ReceiptProduction",
    "ReceiptProductionError",
    "add_parser",
    "create_receipt",
    "handle",
]
