"""Benchmark qualification: registry loading, derivation, and reconciliation.

Repository representativeness and benchmark admission are dimensions SEPARATE
from measurement completeness. `metric_status=complete` says the selected
supported scope was measured completely; it has never said the selected scope
represents the repository, and R0 does not change that. Pitstop is the standing
proof: 3 files / 36 LOC / 0 classes / 0 callables is a *correct* measurement of
three incidental JavaScript files belonging to a C#/.NET multi-service
application. Nothing here may repair that by touching a number.

The separation is enforced structurally, not by convention:

* every function in this module takes measurement results as ``Mapping`` and
  returns new objects; none mutates a result, an inventory, a contribution or a
  callable row;
* the registry SCHEMA refuses to carry `usable_metric_families`,
  `benchmark_usability` or `repository_level_comparison_eligible`, so a hand-
  edited registry cannot supply a second opinion about facts `analysis.json`
  already owns;
* manual input can only ever make admission *more* restrictive
  (:func:`derive_benchmark_usability`), never more permissive.

**No automatic rule may produce a non-UNRESOLVED verdict.** Extension counts,
byte ratios, GitHub language labels, directory names, project markers and
service counts are evidence that can prepare or rank an adjudication. They can
never replace one. This is expressed in the registry contract itself: the
`adjudication.mode` enum has no member for an automatic rule.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from modules.config import QUALIFICATION_PROFILE

QUALIFICATION_ARTIFACT_PATH = "benchmark_qualification.json"
QUALIFICATION_SCHEMA_VERSION = "1.0.0"
REGISTRY_SCHEMA_VERSION = "1.0.0"

MODE_BENCHMARK_QUALIFIED = "benchmark_qualified"
MODE_NOT_REQUESTED = "not_requested"

#: Fixed family -> authoritative status address, in the fixed emission order.
#: The order is part of the contract: a set would make two equivalent records
#: serialize differently and break byte determinism.
METRIC_FAMILY_STATUS_SOURCES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("source_files", ("metrics", "aggregate", "source_files_status")),
    ("lines_of_code", ("metrics", "aggregate", "loc_status")),
    ("classes_structs", ("metrics", "aggregate", "classes_structs_status")),
    ("methods_functions", ("metrics", "aggregate", "methods_functions_status")),
    ("callable_metrics", ("metrics", "complexity", "status")),
)

#: The persisted value each family's status governs. Used only to detect a
#: CONTRADICTION between a status and the value it describes; never to infer a
#: status from a value.
METRIC_FAMILY_VALUE_SOURCES: Mapping[str, tuple[str, ...]] = {
    "source_files": ("metrics", "aggregate", "source_files"),
    "lines_of_code": ("metrics", "aggregate", "lines_of_code"),
    "classes_structs": ("metrics", "aggregate", "classes_structs"),
    "methods_functions": ("metrics", "aggregate", "methods_functions"),
    "callable_metrics": ("metrics", "complexity", "aggregate"),
}

USABLE_STATUSES = frozenset({"complete", "partial"})

REPRESENTATIVENESS_VALUES = frozenset(
    {"ADEQUATE", "MATERIAL_MIX", "NON_REPRESENTATIVE", "UNRESOLVED"}
)
ADMISSION_RESTRICTIONS = frozenset({"none", "analyzed_scope_only", "unsupported_scope"})

REPRESENTATIVENESS_REASON_CODES = frozenset(
    {
        "no_unsupported_first_party_code",
        "supported_scope_adequately_representative",
        "unsupported_first_party_material_by_files",
        "unsupported_first_party_material_by_bytes",
        "unsupported_first_party_structurally_distinct",
        "unsupported_first_party_dominant",
        "evidence_missing",
        "evidence_identity_mismatch",
        "evidence_conflict",
    }
)
USABILITY_REASON_CODES = frozenset(
    {
        "measurement_complete_and_representative",
        "representativeness_limits_to_analyzed_scope",
        "measurement_partial",
        "measurement_failed",
        "manual_admission_restricted_to_analyzed_scope",
        "supported_scope_incidental_to_application",
    }
)


class QualificationError(RuntimeError):
    """A fatal qualification input or integrity failure.

    Raised only where continuing would publish a wrong decision: an unreadable
    or schema-invalid registry, a duplicate primary binding, or a reconciliation
    mismatch. A *missing* decision is not fatal — that is an ``unresolved``
    record, which is a recorded state rather than an exception.
    """


# ----------------------------------------------------------------- helpers ---


def canonical_json(value: Any) -> str:
    """The one canonical serialization used for every hash in this module.

    Sorted keys, no insignificant whitespace, UTF-8, `ensure_ascii=False`.
    Documented and centralized because a hash whose construction is spelled
    slightly differently in two places is a hash that eventually disagrees with
    itself.
    """
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _at(source: Mapping[str, Any], path: Sequence[str]) -> Any:
    node: Any = source
    for key in path:
        if not isinstance(node, Mapping):
            return None
        node = node.get(key)
    return node


def subject_of(result: Mapping[str, Any]) -> str:
    return str(result.get("subject_key") or result.get("repository_url") or "")


def binding_of(result: Mapping[str, Any]) -> dict[str, Any]:
    """The primary binding of one measurement result.

    `filesystem_manifest_hash` is deliberately absent. The current contract
    describes it as broader filesystem evidence rather than comparability
    identity, so admitting it would let a decision match or fail to match for
    reasons unrelated to what was measured. It may be copied as optional
    verification evidence elsewhere; no match, decision or gate may depend on it.
    """
    return {
        "qualification_profile": QUALIFICATION_PROFILE,
        "subject_key": subject_of(result),
        "analyzed_commit_sha": (result.get("acquisition") or {}).get(
            "analyzed_commit_sha"
        ),
        "analysis_scope_hash": result.get("analysis_scope_hash"),
        "analysis_scope_hash_version": result.get("analysis_scope_hash_version"),
    }


def binding_key(binding: Mapping[str, Any]) -> tuple:
    """Exact lookup key. Every component participates; none is optional."""
    return (
        binding.get("qualification_profile"),
        binding.get("subject_key"),
        binding.get("analyzed_commit_sha"),
        binding.get("analysis_scope_hash"),
        binding.get("analysis_scope_hash_version"),
    )


# ---------------------------------------------------------------- registry ---


@dataclass(frozen=True)
class QualificationRegistry:
    """An accepted registry, validated and indexed by exact primary binding."""

    source_id: str
    registry_schema_version: str
    qualification_profile: str
    registry_sha256: str
    records: tuple[Mapping[str, Any], ...]
    _index: Mapping[tuple, Mapping[str, Any]] = field(repr=False, default_factory=dict)
    consumed: set = field(default_factory=set, repr=False, compare=False)

    def lookup(self, binding: Mapping[str, Any]) -> Mapping[str, Any] | None:
        """Exact-match lookup. No fuzzy fallback, ever.

        A near miss — right subject, wrong revision or wrong scope — is a MISS.
        Returning the near match would silently apply a decision made about
        different bytes, which is precisely the stale-evidence failure that
        `unresolved` exists to make visible.
        """
        key = binding_key(binding)
        found = self._index.get(key)
        if found is not None:
            self.consumed.add(key)
        return found

    @property
    def unused_record_count(self) -> int:
        return len(self._index) - len(self.consumed)

    def provenance(self) -> dict[str, Any]:
        return {
            "registry_schema_version": self.registry_schema_version,
            "source_id": self.source_id,
            "registry_sha256": self.registry_sha256,
        }


def load_registry(path: str | Path) -> QualificationRegistry:
    """Load, strictly validate, hash and index an accepted registry.

    The hash is taken over the EXACT FILE BYTES, not over a re-serialization of
    the parsed document. Re-serializing would produce a digest for a document
    nobody ever wrote, so a reproduction could carry a hash that matches no file
    on disk.
    """
    from validation.artifact_io.schema_store import validate_document

    source = Path(path)
    try:
        raw = source.read_bytes()
    except OSError as exc:
        raise QualificationError(
            f"qualification registry {source} could not be read: {exc}"
        ) from exc

    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise QualificationError(
            f"qualification registry {source} is not valid UTF-8 JSON: {exc}"
        ) from exc

    violations = validate_document(
        "benchmark_qualification_registry", document, source.name
    )
    if violations:
        detail = "; ".join(
            f"{error.location}: {error.message}" for error in violations[:5]
        )
        raise QualificationError(
            f"qualification registry {source} violates its schema "
            f"({len(violations)} violation(s)): {detail}"
        )

    profile = str(document["qualification_profile"])
    if profile != QUALIFICATION_PROFILE:
        raise QualificationError(
            f"qualification registry {source} declares profile {profile!r}, but "
            f"this build qualifies under {QUALIFICATION_PROFILE!r}. A verdict is "
            f"only meaningful relative to the question its profile asks."
        )

    index: dict[tuple, Mapping[str, Any]] = {}
    for record in document["records"]:
        binding = record["binding"]
        if binding.get("qualification_profile") != profile:
            raise QualificationError(
                f"registry record {record['record_id']!r} binds profile "
                f"{binding.get('qualification_profile')!r} inside a "
                f"{profile!r} registry"
            )
        key = binding_key(binding)
        if key in index:
            # Fatal, deliberately. Two accepted decisions about the exact same
            # analyzed bytes is an unresolvable contradiction in the input, and
            # picking either one — first, last, or "most restrictive" — would
            # publish an adjudication nobody made.
            raise QualificationError(
                f"qualification registry {source} contains duplicate records for "
                f"binding {key}; a duplicate primary binding is a fatal registry "
                f"error, not a merge"
            )
        _validate_record_semantics(record, source)
        index[key] = record

    return QualificationRegistry(
        source_id=str(document["source_id"]),
        registry_schema_version=str(document["registry_schema_version"]),
        qualification_profile=profile,
        registry_sha256=hashlib.sha256(raw).hexdigest(),
        records=tuple(document["records"]),
        _index=index,
    )


def _validate_record_semantics(record: Mapping[str, Any], source: Path) -> None:
    """Rules the schema cannot express, because they are conditional."""
    identifier = record.get("record_id")
    verdict = record["repository_representativeness"]
    codes = record["representativeness_reason_codes"]

    unknown = sorted(set(codes) - REPRESENTATIVENESS_REASON_CODES)
    if unknown:
        raise QualificationError(
            f"registry record {identifier!r} in {source} uses unknown "
            f"representativeness reason code(s): {', '.join(unknown)}"
        )
    unknown = sorted(
        set(record.get("manual_admission_reason_codes") or ()) - USABILITY_REASON_CODES
    )
    if unknown:
        raise QualificationError(
            f"registry record {identifier!r} in {source} uses unknown admission "
            f"reason code(s): {', '.join(unknown)}"
        )

    if verdict != "UNRESOLVED":
        # Section 6.4: a decision without its evidence is an opinion.
        if not codes:
            raise QualificationError(
                f"registry record {identifier!r} in {source} states {verdict} "
                f"with no stable reason code; every non-UNRESOLVED decision must "
                f"carry at least one"
            )
        evidence = record["evidence_summary"]
        if (
            evidence["unsupported_first_party_files"] > 0
            and not evidence["unsupported_language_groups"]
        ):
            raise QualificationError(
                f"registry record {identifier!r} in {source} reports "
                f"{evidence['unsupported_first_party_files']} unsupported "
                f"first-party file(s) but no unsupported-language evidence"
            )
        if record["adjudication"]["mode"] not in {
            "manual_review",
            "accepted_forensic_audit",
        }:
            raise QualificationError(
                f"registry record {identifier!r} in {source} states {verdict} "
                f"without accepted human or forensic-audit provenance"
            )

    if record["manual_admission_restriction"] not in ADMISSION_RESTRICTIONS:
        raise QualificationError(
            f"registry record {identifier!r} in {source} uses unknown admission "
            f"restriction {record['manual_admission_restriction']!r}"
        )


# ------------------------------------------------------------- derivations ---


def derive_usable_metric_families(
    result: Mapping[str, Any],
) -> tuple[list[str], list[str]]:
    """Families usable for this record, derived from existing statuses alone.

    Returns ``(families, contradictions)``. A family is included when its
    authoritative status is ``complete`` or ``partial``, and excluded when the
    status is ``failed``, ``not_applicable``, absent or unrecognized.

    Membership NEVER upgrades a partial observation to a complete one. The
    original status stays the authority on completeness; this list only answers
    "may this family be used at all", which is a different question.

    A status that claims a measurement while the value it governs is absent is
    reported as a contradiction, not silently resolved in either direction. That
    is a semantic validation failure for the run, not a qualification choice.
    """
    families: list[str] = []
    contradictions: list[str] = []
    for family, status_path in METRIC_FAMILY_STATUS_SOURCES:
        status = _at(result, status_path)
        if status not in USABLE_STATUSES:
            continue
        families.append(family)
        value = _at(result, METRIC_FAMILY_VALUE_SOURCES[family])
        if value is None:
            contradictions.append(
                f"{subject_of(result)}: {family} status is {status!r} but the "
                f"value it governs is absent"
            )
    return families, contradictions


def aggregate_metric_status(result: Mapping[str, Any]) -> str | None:
    value = _at(result, ("metrics", "aggregate", "metric_status"))
    return None if value is None else str(value)


def derive_benchmark_usability(
    *,
    metric_status: str | None,
    representativeness: str,
    manual_restriction: str,
    usable_families: Sequence[str],
) -> tuple[str | None, list[str]]:
    """Final admission verdict and its stable reason codes (plan section 5.4).

    Evaluated in a fixed order, and the order carries meaning:

    1. a failed measurement yields ``None``, never ``unsupported_scope`` —
       mapping failure onto scope would conflate "we could not measure" with
       "this subject is the wrong thing to measure";
    2. an explicit manual ``unsupported_scope`` ceiling wins next;
    3. partial measurement yields ``partial_but_usable`` when any family
       survives, and ``None`` when none does;
    4. complete measurement is then admitted according to representativeness and
       any remaining manual ceiling.

    Manual input appears only in steps 2 and 4, and only ever LOWERS the result.
    There is no branch in which a registry value raises admission above what the
    measurement state and representativeness already allow.
    """
    if metric_status == "failed":
        return None, ["measurement_failed"]

    if manual_restriction == "unsupported_scope":
        return "unsupported_scope", ["supported_scope_incidental_to_application"]

    if metric_status == "partial":
        if not usable_families:
            return None, ["measurement_partial"]
        return "partial_but_usable", ["measurement_partial"]

    if metric_status == "complete":
        if manual_restriction == "analyzed_scope_only":
            return "analyzed_scope_only", [
                "manual_admission_restricted_to_analyzed_scope"
            ]
        if representativeness == "ADEQUATE":
            return "repository_level_usable", [
                "measurement_complete_and_representative"
            ]
        # MATERIAL_MIX, NON_REPRESENTATIVE and UNRESOLVED all restrict to the
        # analyzed scope. UNRESOLVED lands here too: absent evidence must never
        # be read as adequate.
        return "analyzed_scope_only", ["representativeness_limits_to_analyzed_scope"]

    # An unrecognized or absent aggregate status cannot support any admission.
    return None, ["measurement_failed"]


def derive_eligibility(
    *,
    mode: str,
    qualification_status: str,
    representativeness: str,
    usability: str | None,
    metric_status: str | None,
    manual_restriction: str,
) -> bool:
    """Strict repository-level comparison eligibility (plan section 10).

    True only when every condition holds. Never an input: a persisted value that
    disagrees with this predicate is a semantic validation failure, not an
    override.

    Run-level readiness is deliberately NOT folded in. Final unrestricted
    benchmark-of-record permission is this predicate AND the run's readiness
    verdict, kept separate so an exploratory run can hold correctly qualified
    records without being misreported as a benchmark of record.
    """
    return (
        mode == MODE_BENCHMARK_QUALIFIED
        and qualification_status == "adjudicated"
        and representativeness == "ADEQUATE"
        and usability == "repository_level_usable"
        and metric_status == "complete"
        and manual_restriction == "none"
    )


# ---------------------------------------------------- artifact construction ---


def build_records(
    results: Sequence[Mapping[str, Any]], registry: QualificationRegistry
) -> tuple[list[dict[str, Any]], list[str]]:
    """One qualification record per executed result, in deterministic order.

    Returns ``(records, contradictions)``. Cardinality is preserved even when a
    decision is missing: a subject with no exact registry match still gets a
    record, marked ``unresolved``, with no fabricated adjudicator. Dropping it
    instead would turn a readiness blocker into a silent absence.
    """
    records: list[dict[str, Any]] = []
    contradictions: list[str] = []

    for result in sorted(results, key=lambda item: subject_of(item).casefold()):
        binding = binding_of(result)
        accepted = registry.lookup(binding)
        metric_status = aggregate_metric_status(result)
        families, family_problems = derive_usable_metric_families(result)
        contradictions.extend(family_problems)

        if accepted is None:
            qualification_status = "unresolved"
            representativeness = "UNRESOLVED"
            representativeness_codes = ["evidence_missing"]
            manual_restriction = "none"
            manual_codes: list[str] = []
            evidence_summary = None
            adjudication = None
            representativeness_basis = None
            usability_basis = None
            record_id = None
            record_sha256 = None
        else:
            qualification_status = "adjudicated"
            representativeness = str(accepted["repository_representativeness"])
            representativeness_codes = sorted(
                accepted["representativeness_reason_codes"]
            )
            manual_restriction = str(accepted["manual_admission_restriction"])
            manual_codes = sorted(accepted.get("manual_admission_reason_codes") or ())
            evidence_summary = accepted["evidence_summary"]
            adjudication = dict(accepted["adjudication"])
            representativeness_basis = accepted.get("representativeness_basis")
            usability_basis = accepted.get("usability_basis")
            record_id = str(accepted["record_id"])
            record_sha256 = canonical_sha256(accepted)
            adjudication["registry_record_sha256"] = record_sha256

        usability, usability_codes = derive_benchmark_usability(
            metric_status=metric_status,
            representativeness=representativeness,
            manual_restriction=manual_restriction,
            usable_families=families,
        )
        eligible = derive_eligibility(
            mode=MODE_BENCHMARK_QUALIFIED,
            qualification_status=qualification_status,
            representativeness=representativeness,
            usability=usability,
            metric_status=metric_status,
            manual_restriction=manual_restriction,
        )

        records.append(
            {
                "binding": binding,
                "qualification_status": qualification_status,
                "repository_representativeness": representativeness,
                "representativeness_reason_codes": representativeness_codes,
                "manual_admission_restriction": manual_restriction,
                "manual_admission_reason_codes": manual_codes,
                "usable_metric_families": families,
                "benchmark_usability": usability,
                "usability_reason_codes": sorted(set(usability_codes) | set(manual_codes)),
                "repository_level_comparison_eligible": eligible,
                "evidence_summary": evidence_summary,
                "adjudication": adjudication,
                "representativeness_basis": representativeness_basis,
                "usability_basis": usability_basis,
                # Not part of the artifact schema's required set; carried for the
                # checked projections so a CSV cell can name its source record.
                "qualification_record_id": record_id,
                "qualification_record_sha256": record_sha256,
            }
        )
    return records, contradictions


#: Keys carried on an in-memory record for the projections but not part of the
#: persisted artifact contract. Stripped before serialization so the artifact
#: matches its schema exactly under `additionalProperties: false`.
_PROJECTION_ONLY_KEYS = ("qualification_record_id", "qualification_record_sha256")


def build_artifact(
    records: Sequence[Mapping[str, Any]], registry: QualificationRegistry
) -> dict[str, Any]:
    """The persisted artifact document, ordered deterministically by subject."""
    return {
        "qualification_schema_version": QUALIFICATION_SCHEMA_VERSION,
        "qualification_profile": registry.qualification_profile,
        "registry_provenance": registry.provenance(),
        "records": [
            {
                key: value
                for key, value in record.items()
                if key not in _PROJECTION_ONLY_KEYS
            }
            for record in records
        ],
    }


# --------------------------------------------------------- reconciliation ---


def reconcile(
    artifact: Mapping[str, Any], results: Sequence[Mapping[str, Any]]
) -> list[str]:
    """Reconcile a qualification artifact against authoritative measurement.

    Checks cardinality, exact one-to-one identity, and every derived field
    against a fresh derivation from the measurement result. Re-deriving rather
    than trusting the persisted value is the point: this is what makes a
    hand-edited or corrupted artifact fail instead of being believed.

    Returns human-readable problems; empty means reconciled.
    """
    problems: list[str] = []
    records = list(artifact.get("records") or ())

    by_key: dict[tuple, Mapping[str, Any]] = {}
    for record in records:
        key = binding_key(record.get("binding") or {})
        if key in by_key:
            problems.append(f"duplicate qualification record for binding {key}")
            continue
        by_key[key] = record

    measured = {binding_key(binding_of(result)): result for result in results}

    if len(records) != len(results):
        problems.append(
            f"qualification artifact holds {len(records)} record(s) but the run "
            f"executed {len(results)} repository/ies"
        )

    for key, result in measured.items():
        record = by_key.get(key)
        if record is None:
            problems.append(
                f"{subject_of(result)}: no qualification record exactly binds this "
                f"analyzed revision and scope"
            )
            continue

        metric_status = aggregate_metric_status(result)
        families, _ = derive_usable_metric_families(result)
        if list(record.get("usable_metric_families") or ()) != families:
            problems.append(
                f"{subject_of(result)}: persisted usable_metric_families "
                f"{record.get('usable_metric_families')!r} does not equal the "
                f"derivation {families!r}"
            )

        usability, _codes = derive_benchmark_usability(
            metric_status=metric_status,
            representativeness=str(record.get("repository_representativeness")),
            manual_restriction=str(record.get("manual_admission_restriction")),
            usable_families=families,
        )
        if record.get("benchmark_usability") != usability:
            problems.append(
                f"{subject_of(result)}: persisted benchmark_usability "
                f"{record.get('benchmark_usability')!r} does not equal the "
                f"derivation {usability!r}"
            )

        eligible = derive_eligibility(
            mode=MODE_BENCHMARK_QUALIFIED,
            qualification_status=str(record.get("qualification_status")),
            representativeness=str(record.get("repository_representativeness")),
            usability=usability,
            metric_status=metric_status,
            manual_restriction=str(record.get("manual_admission_restriction")),
        )
        if bool(record.get("repository_level_comparison_eligible")) != eligible:
            problems.append(
                f"{subject_of(result)}: persisted "
                f"repository_level_comparison_eligible "
                f"{record.get('repository_level_comparison_eligible')!r} does not "
                f"equal the derivation {eligible!r}"
            )

        # An `unresolved` record must never carry provenance, and an
        # `adjudicated` one must always carry it. Both directions are checked:
        # a fabricated adjudicator is as wrong as a missing one.
        status = record.get("qualification_status")
        if status == "unresolved":
            if record.get("adjudication") is not None:
                problems.append(
                    f"{subject_of(result)}: an unresolved record carries "
                    f"adjudication provenance; no adjudicator may be synthesized"
                )
            if record.get("repository_representativeness") != "UNRESOLVED":
                problems.append(
                    f"{subject_of(result)}: unresolved record states "
                    f"representativeness "
                    f"{record.get('repository_representativeness')!r}"
                )
        elif status == "adjudicated" and record.get("adjudication") is None:
            problems.append(
                f"{subject_of(result)}: an adjudicated record carries no "
                f"adjudication provenance"
            )

    for key in sorted(set(by_key) - set(measured), key=str):
        problems.append(
            f"qualification record binds {key}, which no executed analysis result "
            f"matches"
        )
    return problems


# --------------------------------------------------------------- readiness ---

#: Conditions checked in a fixed order so a NOT_READY verdict reads the same way
#: every time. Readiness is INDEPENDENT of metric correctness: none of these
#: inspects a metric value, and a run may hold entirely valid measurements and
#: still be NOT_READY.
def evaluate_readiness(
    *,
    manifest: Mapping[str, Any],
    mode: str,
    artifact: Mapping[str, Any] | None,
    binding: Mapping[str, Any] | None,
    registry_provenance: Mapping[str, Any] | None,
    executed_count: int,
    integrity_status: str,
    self_validation_passed: bool,
    projection_problems: Sequence[str] = (),
) -> dict[str, Any]:
    """The persisted benchmark-of-record readiness verdict (plan section 13).

    A partial measurement does NOT by itself block readiness. Partial rows carry
    their own `partial_but_usable` restriction and are excluded from unrestricted
    comparison by the eligibility predicate; treating them as a provenance defect
    would conflate two independent dimensions. Conversely `READY` proves no
    metric value correct and never changes a `metric_status`.
    """
    blockers: list[str] = []

    if integrity_status not in {"completed", "completed_with_errors"}:
        blockers.append(f"terminal run integrity is {integrity_status!r}")
    if not self_validation_passed:
        blockers.append("terminal self-validation did not pass")

    if not manifest.get("profiler_git_commit_sha"):
        blockers.append("profiler Git commit SHA is absent")
    if manifest.get("profiler_git_dirty") is not False:
        blockers.append("profiler working tree is not exactly clean")

    policy_sha = manifest.get("exclusion_policy_sha256")
    if not isinstance(policy_sha, str) or len(policy_sha) != 64:
        blockers.append("exclusion policy SHA-256 is absent or malformed")
    for name in (
        "program_version",
        "package_distribution_version",
        "artifact_schema_version",
        "metric_contract_version",
        "complexity_contract_version",
        "inventory_schema_version",
        "exclusion_policy_version",
    ):
        if not manifest.get(name):
            blockers.append(f"contract provenance field {name} is absent")
    if manifest.get("package_distribution_version") != manifest.get("program_version"):
        blockers.append("package distribution version differs from program version")
    if not manifest.get("input_file_hash"):
        blockers.append("accepted input provenance hash is absent")

    unresolved = 0
    qualified = 0
    if mode != MODE_BENCHMARK_QUALIFIED:
        blockers.append(
            "run is not benchmark-qualified; a benchmark of record requires an "
            "accepted qualification registry"
        )
    else:
        if not registry_provenance or not registry_provenance.get("registry_sha256"):
            blockers.append("qualification registry identity/hash is absent")
        if artifact is None or binding is None:
            blockers.append("qualification artifact is absent or unbound")
        else:
            records = list(artifact.get("records") or ())
            qualified = len(records)
            unresolved = sum(
                1
                for record in records
                if record.get("repository_representativeness") == "UNRESOLVED"
                or record.get("qualification_status") == "unresolved"
            )
            if qualified != executed_count:
                blockers.append(
                    f"qualification holds {qualified} record(s) for "
                    f"{executed_count} executed repository/ies"
                )
            if binding.get("record_count") != qualified:
                blockers.append(
                    "manifest record_count does not match the qualification "
                    "artifact"
                )
            if unresolved:
                blockers.append(
                    f"{unresolved} repository/ies have unresolved qualification"
                )

    blockers.extend(projection_problems)

    return {
        "status": "READY" if not blockers else "NOT_READY",
        "blockers": sorted(blockers),
        "qualification_profile": (
            str(artifact.get("qualification_profile")) if artifact else None
        ),
        "qualification_artifact_sha256": (
            str(binding.get("sha256")) if binding else None
        ),
        "qualified_repository_count": qualified,
        "unresolved_repository_count": unresolved,
    }


# -------------------------------------------------------------- projection ---

#: Column order for the qualification block appended to the checked rows.
SHEET_QUALIFICATION_COLUMNS = (
    "analysis_scope_hash",
    "analysis_scope_hash_version",
    "qualification_mode",
    "qualification_profile",
    "qualification_status",
    "repository_representativeness",
    "representativeness_reason_codes",
    "benchmark_usability",
    "usability_reason_codes",
    "usable_metric_families",
    "repository_level_comparison_eligible",
    "qualification_record_id",
    "qualification_record_sha256",
    "representativeness_basis",
    "usability_basis",
)

LANGUAGE_QUALIFICATION_COLUMNS = (
    "analysis_scope_hash",
    "analysis_scope_hash_version",
    "qualification_profile",
    "qualification_status",
    "repository_representativeness",
    "representativeness_reason_codes",
    "benchmark_usability",
    "usability_reason_codes",
    "repository_level_comparison_eligible",
    "qualification_record_id",
)


def unqualified_cells(result: Mapping[str, Any], columns: Iterable[str]) -> dict[str, Any]:
    """Qualification cells for a generic (`not_requested`) run.

    Explicit nulls with eligibility false, never omitted columns: "no
    qualification was requested" is a positive fact about the run, and a reader
    that finds a missing column has to guess which it is.
    """
    cells = {
        "analysis_scope_hash": result.get("analysis_scope_hash"),
        "analysis_scope_hash_version": result.get("analysis_scope_hash_version"),
        "qualification_mode": MODE_NOT_REQUESTED,
        "qualification_status": None,
        "qualification_profile": None,
        "repository_representativeness": None,
        "representativeness_reason_codes": None,
        "benchmark_usability": None,
        "usability_reason_codes": None,
        "usable_metric_families": None,
        "repository_level_comparison_eligible": False,
        "qualification_record_id": None,
        "qualification_record_sha256": None,
        "representativeness_basis": None,
        "usability_basis": None,
    }
    return {name: cells[name] for name in columns}


def qualified_cells(
    record: Mapping[str, Any], profile: str, columns: Iterable[str]
) -> dict[str, Any]:
    """Qualification cells projected from one authoritative record."""
    binding = record.get("binding") or {}
    cells = {
        "analysis_scope_hash": binding.get("analysis_scope_hash"),
        "analysis_scope_hash_version": binding.get("analysis_scope_hash_version"),
        "qualification_mode": MODE_BENCHMARK_QUALIFIED,
        "qualification_profile": profile,
        "qualification_status": record.get("qualification_status"),
        "repository_representativeness": record.get("repository_representativeness"),
        "representativeness_reason_codes": list(
            record.get("representativeness_reason_codes") or ()
        ),
        "benchmark_usability": record.get("benchmark_usability"),
        "usability_reason_codes": list(record.get("usability_reason_codes") or ()),
        "usable_metric_families": list(record.get("usable_metric_families") or ()),
        "repository_level_comparison_eligible": bool(
            record.get("repository_level_comparison_eligible")
        ),
        "qualification_record_id": record.get("qualification_record_id"),
        "qualification_record_sha256": record.get("qualification_record_sha256"),
        "representativeness_basis": record.get("representativeness_basis"),
        "usability_basis": record.get("usability_basis"),
    }
    return {name: cells[name] for name in columns}
