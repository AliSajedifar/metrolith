"""ArchLens 4.0 evidence admission layer.

These tests cover the trusted boundary only: whether a standalone Duplication or
Hotspot document may speak for a run. No metric is read, no aggregate computed
and no finding emitted anywhere below, because none of that exists yet.

Every fixture is a document the product itself would accept -- each builder ends
by calling the real validator. A hand-written document that its own validator
would reject for an unrelated reason would make the negative cases pass for the
wrong reason, which is the failure mode these tests exist to prevent.
"""

from __future__ import annotations

import ast
import json
import sys
import unittest
from pathlib import Path

from modules import admission
from modules.config import AnalysisConfig, PROGRAM_VERSION
from modules.policy import evidence
from modules.standalone_contracts import (
    DUPLICATION_FORMAT,
    HOTSPOT_FORMAT,
    STANDALONE_CONTRACTS,
    StandaloneContract,
    StandaloneContractError,
    build_contract_registry,
)

ROOT = Path(__file__).resolve().parents[1]

COMMIT_A = "a" * 40
COMMIT_B = "b" * 40
SCOPE_A = "sha256:" + ("1" * 64)
SCOPE_B = "sha256:" + ("2" * 64)


# --------------------------------------------------------------------------
# Fixtures: real documents, built from the product's own constants
# --------------------------------------------------------------------------

def duplication_document(
    *,
    commit: str | None = COMMIT_A,
    scope_hash: str = SCOPE_A,
    validate: bool = True,
) -> dict:
    """One duplication document describing an empty analyzed scope.

    `files: []` is the smallest genuinely producible document: every count
    reconciles at zero and both kind statuses are `not_applicable`. Admission
    never looks at groups, so nothing is lost by keeping the body empty -- and
    the constants come from the producer so the fixture cannot drift from it.
    """
    from modules.duplication.model import (
        MIN_DUPLICATED_NLOC,
        MIN_IMMEDIATE_STATEMENTS,
        MIN_SIGNIFICANT_TOKENS,
    )
    from modules.duplication.lexical import LEXICAL_FINGERPRINT_VERSION
    from modules.duplication.output import (
        DUPLICATION_CONTRACT_VERSION,
        DUPLICATION_FORMAT_VERSION,
        validate_duplication_document,
    )
    from modules.duplication.structural import STRUCTURAL_FINGERPRINT_VERSION

    config = AnalysisConfig()
    document = {
        "format": DUPLICATION_FORMAT,
        "format_version": DUPLICATION_FORMAT_VERSION,
        "duplication_contract_version": DUPLICATION_CONTRACT_VERSION,
        "program_version": PROGRAM_VERSION,
        "source": {
            "mode": "local_git_revision",
            "resolved_revision": commit,
            "tracked_only": False,
            "working_tree_state": "not_applicable",
            "analysis_scope_hash": scope_hash,
        },
        "provenance": {
            "inventory_schema_version": config.inventory_schema_version,
            "exclusion_policy_version": config.exclusion_policy_version,
            "exclusion_policy_sha256": f"sha256:{config.exclusion_policy_sha256}",
            "maximum_source_file_size_bytes": config.max_source_file_size_bytes,
            "candidate_thresholds": {
                "minimum_immediate_statements": MIN_IMMEDIATE_STATEMENTS,
                "minimum_significant_lexical_tokens": MIN_SIGNIFICANT_TOKENS,
                "minimum_duplicated_nloc": MIN_DUPLICATED_NLOC,
            },
            "fingerprint_versions": {
                "lexical": LEXICAL_FINGERPRINT_VERSION,
                "structural": STRUCTURAL_FINGERPRINT_VERSION,
            },
            "grammars": [],
        },
        "requested_kinds": ["lexical", "structural"],
        "status": "not_applicable",
        "counts": {
            "eligible_file_count": 0,
            "candidate_complete_file_count": 0,
            "candidate_unavailable_file_count": 0,
            "observed_candidate_count": 0,
            "lexical": {
                "status": "not_applicable",
                "group_count": 0,
                "occurrence_count": 0,
            },
            "structural": {
                "status": "not_applicable",
                "initial_group_count": 0,
                "suppressed_group_count": 0,
                "retained_group_count": 0,
                "occurrence_count": 0,
            },
        },
        "files": [],
        "lexical_groups": [],
        "structural_groups": [],
    }
    if validate:
        validate_duplication_document(document)
    return document


def hotspot_document(
    *, run_id: str = "run-1", subject_key: str = "local:demo", validate: bool = True
) -> dict:
    """One hotspot document naming one subject, built to satisfy the validator."""
    from modules.hotspots import classify_attention, validate_hotspot_document

    def signal(value):
        return {
            "status": "measured",
            "signal": value,
            "cohort": {"rank": 1, "distinct_values": 2},
        }

    def row(path, complexity_signal, churn_signal):
        return {
            "subject_key": subject_key,
            "file": path,
            "complexity": {"status": "measured", "cognitive_complexity_total": 5},
            "churn": {"status": "measured", "commits": 3, "touched_lines": 40},
            "complexity_signal": signal(complexity_signal),
            "churn_signal": signal(churn_signal),
            "classification": classify_attention(complexity_signal, churn_signal),
            "reasons": ["both signals measured"],
        }

    document = {
        "format": HOTSPOT_FORMAT,
        "format_version": "1.0.0",
        "product_name": "ArchLens",
        "program_version": PROGRAM_VERSION,
        "source_run": {
            "run_id": run_id,
            "program_version": PROGRAM_VERSION,
            "artifact_schema_version": "1.11.0",
        },
        "purpose": "maintenance attention",
        "classification_model": {"score": None, "thresholds": None},
        "git_semantics": {},
        "ordering": "attention class",
        "repositories": [
            {
                "subject_key": subject_key,
                "repository_url": None,
                "analyzed_commit_sha": COMMIT_A,
                "history": {"status": "measured"},
                "file_count": 2,
                "classified_file_count": 2,
            }
        ],
        "hotspots": [
            row("a.py", "high", "high"),
            row("b.py", "low", "low"),
        ],
    }
    if validate:
        validate_hotspot_document(document)
    return document


def repository(
    *,
    subject_key: str = "local:demo",
    commit: str | None = COMMIT_A,
    scope_hash: str | None = SCOPE_A,
    repository_url: str | None = None,
) -> dict:
    """One repository result, as `analysis.json` records it."""
    return {
        "subject_key": subject_key,
        "repository_url": repository_url,
        "analysis_scope_hash": scope_hash,
        "acquisition": {"analyzed_commit_sha": commit},
    }


def scopes(*results: dict) -> tuple[dict, ...]:
    return evidence.analyzed_scopes(results or (repository(),))


# --------------------------------------------------------------------------
# The shared vocabulary
# --------------------------------------------------------------------------

class SharedAdmissionVocabularyTests(unittest.TestCase):
    def test_the_state_set_is_closed_and_complete(self):
        self.assertEqual(
            set(admission.ADMISSION_STATES),
            {
                "admitted",
                "not_supplied",
                "unreadable",
                "contract_incompatible",
                "validator_rejected",
                "provenance_mismatch",
            },
        )
        self.assertEqual(
            set(admission.ADMISSION_MEANINGS), set(admission.ADMISSION_STATES)
        )
        for state in admission.ADMISSION_STATES:
            with self.subTest(state=state):
                self.assertTrue(admission.admission_meaning(state).strip())

    def test_an_unknown_state_raises_instead_of_defaulting(self):
        with self.assertRaises(admission.UnknownAdmissionState):
            admission.admission_meaning("probably_fine")

    def test_only_admitted_permits_use(self):
        for state in admission.ADMISSION_STATES:
            with self.subTest(state=state):
                self.assertEqual(
                    admission.is_admitted(state), state == admission.ADMITTED
                )

    def test_the_vocabulary_is_immutable(self):
        with self.assertRaises(TypeError):
            admission.ADMISSION_MEANINGS["admitted"] = "something else"

    #: The published meanings, frozen at dossier document format 1.0.0.
    #:
    #: A literal copy, and deliberately so. These strings are EMBEDDED in every
    #: dossier document `build_dossier` emits, under `admission_meanings`, so
    #: they are released contract text rather than an implementation detail.
    #: Pinning them here is the same discipline a golden file provides: an edit
    #: that would change the bytes of a published document has to change this
    #: test too, and that is the moment to ask whether the format version
    #: should move.
    #:
    #: An earlier version of this test compared against `git show HEAD:` to
    #: prove the extraction into `modules.admission` changed nothing. That was
    #: the right check at the moment of the move and only at that moment: once
    #: the move was committed, HEAD no longer contained the definitions and the
    #: comparison silently had nothing to compare. A contract pin does not have
    #: that lifetime problem.
    FROZEN_MEANINGS = {
        "admitted": (
            "the document passed its own validator and describes the same "
            "analyzed scope as this run; its summary is shown"
        ),
        "not_supplied": (
            "no document of this kind was supplied; this is an absence of "
            "input, not a measured absence of findings"
        ),
        "unreadable": "the supplied file could not be read or parsed as JSON",
        "contract_incompatible": (
            "the document's format identity is not an active exact-version "
            "standalone contract"
        ),
        "validator_rejected": (
            "the document carries an active identity but its own validator "
            "rejected its contents"
        ),
        "provenance_mismatch": (
            "the document is valid but describes a different run, revision or "
            "analysis scope; its numbers are NOT shown, because a figure "
            "measured elsewhere would be read as a figure measured here"
        ),
    }

    FROZEN_PROVENANCE_RULES = {
        DUPLICATION_FORMAT: (
            "the analyzed commit SHA and analysis scope hash must match a "
            "repository measured in this run"
        ),
        HOTSPOT_FORMAT: "the source run id must be this run's id",
        "archlens-changed-code": (
            "the HEAD side's analyzed commit SHA must match a repository "
            "measured in this run; the base side is a different revision by "
            "construction"
        ),
        "archlens-check-result": "the evaluated run id must be this run's id",
    }

    def test_the_published_meanings_are_the_frozen_contract_text(self):
        self.assertEqual(dict(admission.ADMISSION_MEANINGS), self.FROZEN_MEANINGS)
        self.assertEqual(
            list(admission.ADMISSION_MEANINGS), list(self.FROZEN_MEANINGS)
        )

    def test_the_published_provenance_rules_are_frozen_contract_text(self):
        self.assertEqual(
            dict(admission.PROVENANCE_RULES), self.FROZEN_PROVENANCE_RULES
        )

    def test_the_dossier_still_exports_every_moved_name(self):
        """The move kept `modules.dossier`'s public surface intact."""
        from modules import dossier

        for name in (
            "ADMITTED", "NOT_SUPPLIED", "UNREADABLE", "CONTRACT_INCOMPATIBLE",
            "VALIDATOR_REJECTED", "PROVENANCE_MISMATCH", "ADMISSION_MEANINGS",
            "PROVENANCE_RULES",
        ):
            with self.subTest(name=name):
                self.assertTrue(hasattr(dossier, name))
                self.assertIn(name, dossier.__all__)
                self.assertIs(
                    getattr(dossier, name), getattr(admission, name),
                    "the dossier must re-export, never redefine",
                )

    def test_the_meanings_have_exactly_one_definition(self):
        """No consumer may keep a private copy of the vocabulary."""
        for relative in ("modules/dossier.py", "modules/policy/evidence.py"):
            with self.subTest(module=relative):
                tree = ast.parse((ROOT / relative).read_text(encoding="utf-8"))
                assigned = {
                    target.id
                    for node in ast.walk(tree)
                    if isinstance(node, ast.Assign)
                    for target in node.targets
                    if isinstance(target, ast.Name)
                }
                self.assertNotIn("ADMISSION_MEANINGS", assigned)
                self.assertNotIn("PROVENANCE_RULES", assigned)


# --------------------------------------------------------------------------
# The registry tightening
# --------------------------------------------------------------------------

class AnalysisContractDeclarationTests(unittest.TestCase):
    def test_duplication_declares_the_field_carrying_its_contract_version(self):
        contract = STANDALONE_CONTRACTS[DUPLICATION_FORMAT]
        self.assertEqual(
            contract.analysis_contract_field, "duplication_contract_version"
        )
        self.assertEqual(contract.analysis_contract_version, "1.0.0")

    def test_a_format_without_an_analysis_contract_declares_neither_half(self):
        contract = STANDALONE_CONTRACTS[HOTSPOT_FORMAT]
        self.assertIsNone(contract.analysis_contract_version)
        self.assertIsNone(contract.analysis_contract_field)

    def test_half_a_declaration_is_refused(self):
        for version, field_name in (("1.0.0", None), (None, "some_version")):
            with self.subTest(version=version, field=field_name):
                with self.assertRaisesRegex(
                    StandaloneContractError, "declared together"
                ):
                    build_contract_registry((
                        StandaloneContract(
                            "archlens-example", "1.0.0", "example:validate",
                            analysis_contract_version=version,
                            analysis_contract_field=field_name,
                        ),
                    ))


# --------------------------------------------------------------------------
# Valid admission
# --------------------------------------------------------------------------

class ValidAdmissionTests(unittest.TestCase):
    def test_a_matching_duplication_document_is_admitted_and_bound(self):
        record = evidence.admit_document(
            evidence.EVIDENCE_DUPLICATION,
            duplication_document(),
            run_id="run-1",
            scopes=scopes(),
        )
        self.assertEqual(record.admission, admission.ADMITTED)
        self.assertTrue(record.admitted)
        self.assertEqual(record.subject_keys, ("local:demo",))
        self.assertEqual(record.binding["state"], evidence.BINDING_BOUND)
        self.assertTrue(record.binding["scope_hash_compared"])
        self.assertTrue(record.binding["require_scope_hash"])
        self.assertIsNone(record.reason)
        self.assertIsNotNone(record.document)
        self.assertEqual(record.contract_compatibility, "compatible")
        self.assertEqual(record.document_analysis_contract_version, "1.0.0")

    def test_a_matching_hotspot_document_is_admitted_and_bound(self):
        record = evidence.admit_document(
            evidence.EVIDENCE_HOTSPOTS,
            hotspot_document(),
            run_id="run-1",
            scopes=scopes(),
        )
        self.assertEqual(record.admission, admission.ADMITTED)
        self.assertEqual(record.subject_keys, ("local:demo",))
        # The run-id binding is exact, so no scope hash was involved and the
        # record says so rather than reporting a misleading `false`.
        self.assertIsNone(record.binding["scope_hash_compared"])

    def test_a_hotspot_document_binds_to_every_subject_it_covers(self):
        document = hotspot_document()
        document["repositories"].append({
            "subject_key": "local:other",
            "repository_url": None,
            "analyzed_commit_sha": COMMIT_B,
            "history": {"status": "measured"},
            "file_count": 0,
            "classified_file_count": 0,
        })
        record = evidence.admit_document(
            evidence.EVIDENCE_HOTSPOTS,
            document,
            run_id="run-1",
            scopes=scopes(
                repository(),
                repository(subject_key="local:other", commit=COMMIT_B, scope_hash=SCOPE_B),
            ),
        )
        self.assertEqual(record.admission, admission.ADMITTED)
        self.assertEqual(record.subject_keys, ("local:demo", "local:other"))

    def test_the_admitted_document_is_the_object_supplied(self):
        document = duplication_document()
        record = evidence.admit_document(
            evidence.EVIDENCE_DUPLICATION, document,
            run_id="run-1", scopes=scopes(),
        )
        self.assertIs(record.document, document)

    def test_the_record_never_serializes_the_document(self):
        record = evidence.admit_document(
            evidence.EVIDENCE_DUPLICATION, duplication_document(),
            run_id="run-1", scopes=scopes(),
        )
        payload = record.as_dict()
        self.assertNotIn("document", payload)
        self.assertNotIn("counts", json.dumps(payload))


# --------------------------------------------------------------------------
# Contract incompatibility
# --------------------------------------------------------------------------

class ContractCompatibilityTests(unittest.TestCase):
    def _admit(self, document, kind=evidence.EVIDENCE_DUPLICATION):
        return evidence.admit_document(
            kind, document, run_id="run-1", scopes=scopes()
        )

    def test_an_unknown_format_is_contract_incompatible(self):
        document = duplication_document()
        document["format"] = "archlens-something-else"
        record = self._admit(document)
        self.assertEqual(record.admission, admission.CONTRACT_INCOMPATIBLE)
        self.assertEqual(record.reason, evidence.REASON_FORMAT_UNEXPECTED)
        self.assertIsNone(record.document)

    def test_a_document_supplied_under_the_wrong_kind_is_refused(self):
        """A hotspot file passed as duplication is an identity error, not a match."""
        record = self._admit(hotspot_document())
        self.assertEqual(record.admission, admission.CONTRACT_INCOMPATIBLE)
        self.assertEqual(record.reason, evidence.REASON_FORMAT_UNEXPECTED)
        self.assertEqual(record.document_format, HOTSPOT_FORMAT)

    def test_an_unsupported_format_version_is_refused(self):
        document = duplication_document()
        document["format_version"] = "1.0.1"
        record = self._admit(document)
        self.assertEqual(record.admission, admission.CONTRACT_INCOMPATIBLE)
        self.assertEqual(record.reason, evidence.REASON_CONTRACT_STATUS)
        self.assertEqual(record.contract_compatibility, "unsupported_version")

    def test_a_non_semver_version_is_an_invalid_identity(self):
        document = duplication_document()
        document["format_version"] = "one-point-oh"
        record = self._admit(document)
        self.assertEqual(record.admission, admission.CONTRACT_INCOMPATIBLE)
        self.assertEqual(record.contract_compatibility, "invalid_identity")

    def test_an_unreadable_analysis_contract_version_is_an_identity_failure(self):
        """Not a validator failure: the two send an operator to different places."""
        document = duplication_document(validate=False)
        document["duplication_contract_version"] = "2.0.0"
        record = self._admit(document)
        self.assertEqual(record.admission, admission.CONTRACT_INCOMPATIBLE)
        self.assertEqual(record.reason, evidence.REASON_ANALYSIS_CONTRACT)
        self.assertEqual(record.document_analysis_contract_version, "2.0.0")
        self.assertEqual(record.expected_analysis_contract_version, "1.0.0")

    def test_a_json_array_presents_no_identity(self):
        record = self._admit([1, 2, 3])
        self.assertEqual(record.admission, admission.CONTRACT_INCOMPATIBLE)
        self.assertEqual(record.reason, evidence.REASON_NOT_A_DOCUMENT)

    def test_an_unknown_evidence_kind_raises(self):
        with self.assertRaises(evidence.EvidenceKindUnknown):
            evidence.admit_document(
                "changed_code", {}, run_id="run-1", scopes=scopes()
            )


# --------------------------------------------------------------------------
# Validator failure
# --------------------------------------------------------------------------

class ValidatorRejectionTests(unittest.TestCase):
    def test_a_document_its_own_validator_refuses_is_not_admitted(self):
        document = duplication_document(validate=False)
        document["counts"]["eligible_file_count"] = 7
        record = evidence.admit_document(
            evidence.EVIDENCE_DUPLICATION, document,
            run_id="run-1", scopes=scopes(),
        )
        self.assertEqual(record.admission, admission.VALIDATOR_REJECTED)
        self.assertEqual(record.reason, evidence.REASON_VALIDATOR_REJECTED)
        self.assertIn("DuplicationOutputError", record.detail)
        self.assertIsNone(record.document)

    def test_a_hotspot_document_its_validator_refuses_is_not_admitted(self):
        document = hotspot_document(validate=False)
        document["hotspots"][0]["classification"] = "high_attention"
        document["hotspots"][1]["classification"] = "high_attention"
        record = evidence.admit_document(
            evidence.EVIDENCE_HOTSPOTS, document,
            run_id="run-1", scopes=scopes(),
        )
        self.assertEqual(record.admission, admission.VALIDATOR_REJECTED)
        self.assertIsNone(record.document)

    def test_a_validator_failure_never_escapes_as_an_exception(self):
        document = duplication_document(validate=False)
        document.pop("counts")
        record = evidence.admit_document(
            evidence.EVIDENCE_DUPLICATION, document,
            run_id="run-1", scopes=scopes(),
        )
        self.assertEqual(record.admission, admission.VALIDATOR_REJECTED)


# --------------------------------------------------------------------------
# Provenance: revision, scope hash, run identity
# --------------------------------------------------------------------------

class ProvenanceTests(unittest.TestCase):
    def test_a_document_from_another_revision_is_refused(self):
        record = evidence.admit_document(
            evidence.EVIDENCE_DUPLICATION,
            duplication_document(commit=COMMIT_B),
            run_id="run-1",
            scopes=scopes(),
        )
        self.assertEqual(record.admission, admission.PROVENANCE_MISMATCH)
        self.assertEqual(record.reason, evidence.REASON_NO_SUBJECT_AT_COMMIT)
        self.assertEqual(record.binding["state"], evidence.BINDING_UNBOUND)
        self.assertIsNone(record.document)

    def test_a_document_recording_no_revision_is_refused(self):
        record = evidence.admit_document(
            evidence.EVIDENCE_DUPLICATION,
            duplication_document(commit=None),
            run_id="run-1",
            scopes=scopes(),
        )
        self.assertEqual(record.admission, admission.PROVENANCE_MISMATCH)
        self.assertEqual(record.reason, evidence.REASON_NO_ANALYZED_COMMIT)

    def test_the_same_commit_with_a_different_scope_hash_is_refused(self):
        record = evidence.admit_document(
            evidence.EVIDENCE_DUPLICATION,
            duplication_document(scope_hash=SCOPE_B),
            run_id="run-1",
            scopes=scopes(),
        )
        self.assertEqual(record.admission, admission.PROVENANCE_MISMATCH)
        self.assertEqual(record.reason, evidence.REASON_SCOPE_HASH_MISMATCH)
        self.assertEqual(
            record.provenance_evidence["document_analysis_scope_hash"], SCOPE_B
        )

    def test_a_hotspot_document_from_another_run_is_refused(self):
        record = evidence.admit_document(
            evidence.EVIDENCE_HOTSPOTS,
            hotspot_document(run_id="run-OTHER"),
            run_id="run-1",
            scopes=scopes(),
        )
        self.assertEqual(record.admission, admission.PROVENANCE_MISMATCH)
        self.assertEqual(record.reason, evidence.REASON_RUN_MISMATCH)
        self.assertIsNone(record.document)

    def test_an_unestablished_run_identity_is_refused_on_either_side(self):
        for document_run_id, run_id in ((None, "run-1"), ("run-1", None)):
            with self.subTest(document=document_run_id, run=run_id):
                document = hotspot_document()
                document["source_run"]["run_id"] = document_run_id
                record = evidence.admit_document(
                    evidence.EVIDENCE_HOTSPOTS, document,
                    run_id=run_id, scopes=scopes(),
                )
                self.assertEqual(record.admission, admission.PROVENANCE_MISMATCH)
                self.assertEqual(
                    record.reason, evidence.REASON_RUN_IDENTITY_UNKNOWN
                )

    def test_a_hotspot_document_naming_no_subject_of_this_run_is_refused(self):
        record = evidence.admit_document(
            evidence.EVIDENCE_HOTSPOTS,
            hotspot_document(subject_key="local:elsewhere"),
            run_id="run-1",
            scopes=scopes(),
        )
        self.assertEqual(record.admission, admission.PROVENANCE_MISMATCH)
        self.assertEqual(record.reason, evidence.REASON_NO_COVERED_SUBJECT)

    def test_the_compared_values_travel_with_the_refusal(self):
        record = evidence.admit_document(
            evidence.EVIDENCE_DUPLICATION,
            duplication_document(commit=COMMIT_B),
            run_id="run-1",
            scopes=scopes(),
        )
        compared = record.provenance_evidence
        self.assertEqual(compared["document_analyzed_commit_sha"], COMMIT_B)
        self.assertEqual(
            [scope["analyzed_commit_sha"] for scope in compared["run_analyzed_scopes"]],
            [COMMIT_A],
        )


# --------------------------------------------------------------------------
# The scope-hash decision
# --------------------------------------------------------------------------

class ScopeHashPolicyTests(unittest.TestCase):
    def test_require_scope_hash_is_the_default(self):
        self.assertTrue(evidence.REQUIRE_SCOPE_HASH_DEFAULT)

    def test_a_missing_document_side_hash_is_not_admitted(self):
        document = duplication_document(validate=False)
        document["source"]["analysis_scope_hash"] = None
        record = evidence.admit_document(
            evidence.EVIDENCE_DUPLICATION, document,
            run_id="run-1", scopes=scopes(),
        )
        # The producer's own validator refuses a null hash before provenance is
        # reached, which is the stronger refusal; either way it is not admitted.
        self.assertNotEqual(record.admission, admission.ADMITTED)
        self.assertIsNone(record.document)

    def test_a_missing_run_side_hash_is_not_admitted(self):
        record = evidence.admit_document(
            evidence.EVIDENCE_DUPLICATION,
            duplication_document(),
            run_id="run-1",
            scopes=scopes(repository(scope_hash=None)),
        )
        self.assertEqual(record.admission, admission.PROVENANCE_MISMATCH)
        self.assertEqual(record.reason, evidence.REASON_SCOPE_HASH_MISSING)
        self.assertEqual(
            record.provenance_evidence["missing_scope_hash_side"], "run"
        )
        self.assertIsNone(record.document)

    def test_relaxing_the_requirement_admits_on_the_commit_alone(self):
        """The documented escape hatch, and it must say the hash was not compared."""
        record = evidence.admit_document(
            evidence.EVIDENCE_DUPLICATION,
            duplication_document(),
            run_id="run-1",
            scopes=scopes(repository(scope_hash=None)),
            require_scope_hash=False,
        )
        self.assertEqual(record.admission, admission.ADMITTED)
        self.assertFalse(record.binding["scope_hash_compared"])
        self.assertFalse(record.binding["require_scope_hash"])

    def test_relaxing_the_requirement_still_refuses_a_hash_that_disagrees(self):
        record = evidence.admit_document(
            evidence.EVIDENCE_DUPLICATION,
            duplication_document(scope_hash=SCOPE_B),
            run_id="run-1",
            scopes=scopes(),
            require_scope_hash=False,
        )
        self.assertEqual(record.admission, admission.PROVENANCE_MISMATCH)
        self.assertEqual(record.reason, evidence.REASON_SCOPE_HASH_MISMATCH)

    def test_the_scope_hash_policy_does_not_apply_to_hotspots(self):
        record = evidence.admit_document(
            evidence.EVIDENCE_HOTSPOTS,
            hotspot_document(),
            run_id="run-1",
            scopes=scopes(repository(scope_hash=None)),
        )
        self.assertEqual(record.admission, admission.ADMITTED)


# --------------------------------------------------------------------------
# Ambiguous binding
# --------------------------------------------------------------------------

class AmbiguousBindingTests(unittest.TestCase):
    def test_two_subjects_at_one_commit_and_scope_are_refused(self):
        record = evidence.admit_document(
            evidence.EVIDENCE_DUPLICATION,
            duplication_document(),
            run_id="run-1",
            scopes=scopes(
                repository(subject_key="local:one"),
                repository(subject_key="local:two"),
            ),
        )
        self.assertEqual(record.admission, admission.PROVENANCE_MISMATCH)
        self.assertEqual(record.reason, evidence.REASON_AMBIGUOUS_BINDING)
        self.assertEqual(record.binding["state"], evidence.BINDING_AMBIGUOUS)
        self.assertEqual(
            record.binding["candidate_subject_keys"], ["local:one", "local:two"]
        )

    def test_an_ambiguous_binding_never_resolves_to_a_subject(self):
        """The prohibition, stated as a test: no first match, in either order."""
        forward = scopes(
            repository(subject_key="local:one"),
            repository(subject_key="local:two"),
        )
        reversed_order = scopes(
            repository(subject_key="local:two"),
            repository(subject_key="local:one"),
        )
        for order, label in ((forward, "forward"), (reversed_order, "reversed")):
            with self.subTest(order=label):
                record = evidence.admit_document(
                    evidence.EVIDENCE_DUPLICATION, duplication_document(),
                    run_id="run-1", scopes=order,
                )
                self.assertEqual(record.subject_keys, ())
                self.assertIsNone(record.document)

    def test_a_distinguishing_scope_hash_removes_the_ambiguity(self):
        record = evidence.admit_document(
            evidence.EVIDENCE_DUPLICATION,
            duplication_document(scope_hash=SCOPE_B),
            run_id="run-1",
            scopes=scopes(
                repository(subject_key="local:one", scope_hash=SCOPE_A),
                repository(subject_key="local:two", scope_hash=SCOPE_B),
            ),
        )
        self.assertEqual(record.admission, admission.ADMITTED)
        self.assertEqual(record.subject_keys, ("local:two",))

    def test_one_subject_recorded_twice_is_not_ambiguous(self):
        """Ambiguity is about subjects, not rows: one subject is one subject."""
        record = evidence.admit_document(
            evidence.EVIDENCE_DUPLICATION,
            duplication_document(),
            run_id="run-1",
            scopes=scopes(repository(), repository()),
        )
        self.assertEqual(record.admission, admission.ADMITTED)
        self.assertEqual(record.subject_keys, ("local:demo",))

    def test_relaxing_the_scope_hash_can_reintroduce_ambiguity(self):
        record = evidence.admit_document(
            evidence.EVIDENCE_DUPLICATION,
            duplication_document(),
            run_id="run-1",
            scopes=scopes(
                repository(subject_key="local:one", scope_hash=None),
                repository(subject_key="local:two", scope_hash=None),
            ),
            require_scope_hash=False,
        )
        self.assertEqual(record.reason, evidence.REASON_AMBIGUOUS_BINDING)


# --------------------------------------------------------------------------
# Missing evidence, and the refusal to read it as zero
# --------------------------------------------------------------------------

class MissingEvidenceTests(unittest.TestCase):
    def test_unsupplied_evidence_is_an_absence_of_input(self):
        for kind in evidence.EVIDENCE_KINDS:
            with self.subTest(kind=kind):
                record = evidence.not_supplied(kind)
                self.assertEqual(record.admission, admission.NOT_SUPPLIED)
                self.assertFalse(record.supplied)
                self.assertIsNone(record.document)
                self.assertEqual(record.subject_keys, ())
                meaning = record.as_dict()["admission_meaning"]
                self.assertIn("absence of input", meaning)
                self.assertIn("not a measured absence of findings", meaning)

    def test_a_missing_file_is_unreadable_and_not_a_crash(self):
        record = evidence.admit_evidence_file(
            evidence.EVIDENCE_DUPLICATION,
            ROOT / "no-such-evidence-file.json",
            run_id="run-1",
            scopes=scopes(),
        )
        self.assertEqual(record.admission, admission.UNREADABLE)
        self.assertIsNone(record.document)
        self.assertTrue(record.supplied)

    def test_malformed_json_is_unreadable(self):
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "evidence.json"
            path.write_bytes(b"{ not json")
            record = evidence.admit_evidence_file(
                evidence.EVIDENCE_DUPLICATION, path,
                run_id="run-1", scopes=scopes(),
            )
        self.assertEqual(record.admission, admission.UNREADABLE)
        self.assertIn("JSONDecodeError", record.detail)

    def test_a_none_path_is_not_supplied_rather_than_unreadable(self):
        record = evidence.admit_evidence_file(
            evidence.EVIDENCE_DUPLICATION, None,
            run_id="run-1", scopes=scopes(),
        )
        self.assertEqual(record.admission, admission.NOT_SUPPLIED)
        self.assertFalse(record.supplied)

    def test_a_real_file_round_trips_to_admission(self):
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "duplication.json"
            path.write_bytes(
                json.dumps(duplication_document()).encode("utf-8")
            )
            record = evidence.admit_evidence_file(
                evidence.EVIDENCE_DUPLICATION, path,
                run_id="run-1", scopes=scopes(),
            )
        self.assertEqual(record.admission, admission.ADMITTED)

    def test_no_rejected_record_can_ever_carry_a_document(self):
        """The structural guarantee that keeps missing from becoming zero."""
        rejected = [
            evidence.not_supplied(evidence.EVIDENCE_DUPLICATION),
            evidence.admit_document(
                evidence.EVIDENCE_DUPLICATION, duplication_document(commit=COMMIT_B),
                run_id="run-1", scopes=scopes(),
            ),
            evidence.admit_document(
                evidence.EVIDENCE_DUPLICATION, hotspot_document(),
                run_id="run-1", scopes=scopes(),
            ),
            evidence.admit_document(
                evidence.EVIDENCE_DUPLICATION, None,
                run_id="run-1", scopes=scopes(), read_error="boom",
            ),
        ]
        for record in rejected:
            with self.subTest(admission=record.admission):
                self.assertNotEqual(record.admission, admission.ADMITTED)
                self.assertIsNone(record.document)
                self.assertFalse(record.admitted)
                self.assertEqual(record.subject_keys, ())

    def test_the_pairing_cannot_be_constructed_by_hand(self):
        with self.assertRaisesRegex(ValueError, "must carry no document"):
            evidence.EvidenceRecord(
                kind=evidence.EVIDENCE_DUPLICATION,
                expected_format=DUPLICATION_FORMAT,
                admission=admission.PROVENANCE_MISMATCH,
                supplied=True,
                document={"format": DUPLICATION_FORMAT},
            )
        with self.assertRaisesRegex(ValueError, "must carry the document"):
            evidence.EvidenceRecord(
                kind=evidence.EVIDENCE_DUPLICATION,
                expected_format=DUPLICATION_FORMAT,
                admission=admission.ADMITTED,
                supplied=True,
            )

    def test_an_outcome_outside_the_closed_set_is_refused(self):
        with self.assertRaisesRegex(ValueError, "is not one of"):
            evidence.EvidenceRecord(
                kind=evidence.EVIDENCE_DUPLICATION,
                expected_format=DUPLICATION_FORMAT,
                admission="probably_fine",
                supplied=True,
            )


# --------------------------------------------------------------------------
# Determinism
# --------------------------------------------------------------------------

class DeterminismTests(unittest.TestCase):
    CASES = (
        ("admitted", duplication_document, {}),
        ("wrong revision", lambda: duplication_document(commit=COMMIT_B), {}),
        ("wrong scope", lambda: duplication_document(scope_hash=SCOPE_B), {}),
    )

    def test_the_same_inputs_produce_the_same_record_every_time(self):
        for label, build, kwargs in self.CASES:
            with self.subTest(case=label):
                first = evidence.admit_document(
                    evidence.EVIDENCE_DUPLICATION, build(),
                    run_id="run-1", scopes=scopes(), **kwargs,
                )
                second = evidence.admit_document(
                    evidence.EVIDENCE_DUPLICATION, build(),
                    run_id="run-1", scopes=scopes(), **kwargs,
                )
                self.assertEqual(first.as_dict(), second.as_dict())
                self.assertEqual(
                    json.dumps(first.as_dict(), sort_keys=True),
                    json.dumps(second.as_dict(), sort_keys=True),
                )

    def test_the_record_is_json_serializable(self):
        for kind, document in (
            (evidence.EVIDENCE_DUPLICATION, duplication_document()),
            (evidence.EVIDENCE_HOTSPOTS, hotspot_document()),
        ):
            with self.subTest(kind=kind):
                record = evidence.admit_document(
                    kind, document, run_id="run-1", scopes=scopes()
                )
                json.dumps(record.as_dict(), sort_keys=True, allow_nan=False)

    def test_run_scope_ordering_does_not_change_the_outcome(self):
        forward = scopes(
            repository(subject_key="local:one", scope_hash=SCOPE_A),
            repository(subject_key="local:two", scope_hash=SCOPE_B),
        )
        backward = scopes(
            repository(subject_key="local:two", scope_hash=SCOPE_B),
            repository(subject_key="local:one", scope_hash=SCOPE_A),
        )
        self.assertEqual(forward, backward)
        records = [
            evidence.admit_document(
                evidence.EVIDENCE_DUPLICATION, duplication_document(),
                run_id="run-1", scopes=order,
            ).as_dict()
            for order in (forward, backward)
        ]
        self.assertEqual(records[0], records[1])
        self.assertEqual(records[0]["binding"]["subject_keys"], ["local:one"])

    def test_every_reason_the_module_can_emit_has_a_published_meaning(self):
        tree = ast.parse(
            (ROOT / "modules/policy/evidence.py").read_text(encoding="utf-8")
        )
        declared = {
            node.value.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
            for target in node.targets
            if isinstance(target, ast.Name) and target.id.startswith("REASON_")
        }
        self.assertTrue(declared)
        self.assertEqual(declared, set(evidence.REASON_MEANINGS))

    def test_every_binding_state_has_a_published_meaning(self):
        self.assertEqual(
            set(evidence.BINDING_STATES), set(evidence.BINDING_MEANINGS)
        )


# --------------------------------------------------------------------------
# The import boundary
# --------------------------------------------------------------------------

class ImportBoundaryTests(unittest.TestCase):
    FORBIDDEN_PREFIXES = (
        "modules.duplication",
        "modules.hotspots",
        "modules.policy.sarif",
    )

    def test_the_admission_layer_imports_no_analysis_or_sarif_internals(self):
        tree = ast.parse(
            (ROOT / "modules/policy/evidence.py").read_text(encoding="utf-8")
        )
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        for name in sorted(imported):
            for prefix in self.FORBIDDEN_PREFIXES:
                with self.subTest(imported=name, forbidden=prefix):
                    self.assertFalse(
                        name == prefix or name.startswith(prefix + "."),
                        f"{name} is an internal the admission layer must not import",
                    )

    def test_the_validators_are_reached_only_through_the_registry(self):
        source = (ROOT / "modules/policy/evidence.py").read_text(encoding="utf-8")
        self.assertIn("resolve_validator", source)
        self.assertNotIn("validate_duplication_document", source)
        self.assertNotIn("validate_hotspot_document", source)

    def test_the_analysis_modules_do_not_import_policy(self):
        for relative in (
            "modules/hotspots.py",
            "modules/duplication/output.py",
            "modules/duplication/model.py",
            "modules/duplication/grouping.py",
        ):
            with self.subTest(module=relative):
                tree = ast.parse((ROOT / relative).read_text(encoding="utf-8"))
                for node in ast.walk(tree):
                    module = None
                    if isinstance(node, ast.ImportFrom):
                        module = node.module
                    elif isinstance(node, ast.Import):
                        module = node.names[0].name
                    if module:
                        self.assertFalse(
                            module.startswith("modules.policy"),
                            f"{relative} imports {module}",
                        )


if __name__ == "__main__":  # pragma: no cover
    sys.exit(not unittest.main(exit=False).result.wasSuccessful())
