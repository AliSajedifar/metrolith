"""ArchLens 4.0 Duplication Policy integration, DP1: core evaluation.

A Duplication document reaches Policy through the admission boundary and nowhere
else, its published counts become metric observations, and a rule over them
produces findings. Duplication semantics do not move: nothing here re-derives a
group, sums the two kinds, aggregates an NLOC, or reads a categorical as a
number.

Two properties are load-bearing and both are easy to lose.

**Missing is not zero.** Seven different absences each get their own typed
reason, and none of them is ever a pass.

**An empty group list is not a measured absence.** Under `not_requested` and
under `failed` the document carries an EMPTY `lexical_groups` / `structural_groups`
-- by length alone indistinguishable from a subject that genuinely has no
clones. Every decision about whether a kind was measured reads
`counts.<kind>.status`, and `KindStatusIsTheSourceOfTruthTests` exists to prove
it stays that way.

**Fixtures are produced by the real analyzer**, then mutated only for the
negative cases, and every mutated document is re-validated against its own
contract inside the test. A negative case must fail for the reason under test
and no other.
"""

from __future__ import annotations

import copy
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from modules.config import PROGRAM_VERSION
from modules.duplication.grouping import occurrence_identity
from modules.duplication.output import (
    DuplicationOutputError,
    _coordinate,
    _expected_group_id,
    analyze_duplication_snapshot,
    validate_duplication_document,
)
from modules.local_source import prepare_local_source
from modules.policy import check as check_module
from modules.policy import evidence as evidence_module
from modules.policy import findings as finding_module
from modules.policy import metrics as metric_module
from modules.policy.check import CHECK_RESULT_FORMAT_VERSION
from modules.policy.document import PolicyDocumentInvalid
from modules.policy.document_v2 import (
    POLICY_DOCUMENT_V2_FORMAT_VERSION,
    load_any_policy,
)
from modules.ratchet.check_service import RATCHET_CHECK_RESULT_FORMAT_VERSION

SUBJECT = "local:demo"
OTHER_SUBJECT = "local:other"

#: Nine statements, comfortably past every candidate-eligibility threshold.
CLONE_BODY = """\
def {name}(seed):
    one = seed + 1
    two = one * 2
    three = two - 3
    four = three / 4
    five = four + one
    six = five * two
    seven = six - three
    eight = seven + four
    return eight
"""

#: Distinct bodies, so a "no clones anywhere" document is a MEASURED zero.
UNIQUE_BODY = """\
def {name}(seed):
    value = seed + {offset}
    for index in range({offset}):
        value = value * 2 - index
    if value > {offset}:
        value = value - {offset}
    return value
"""


# --------------------------------------------------------------------------
# Fixtures, produced by the real analyzer
# --------------------------------------------------------------------------

def _build(sources: dict[str, str], requested: tuple[str, ...]) -> dict:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary) / "repo"
        for relative, text in sources.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8", newline="\n")

        def git(*arguments: str) -> str:
            return subprocess.run(
                ["git", "-C", str(root), *arguments],
                check=True, capture_output=True, text=True,
            ).stdout.strip()

        git("init", "--quiet")
        git("config", "user.email", "duplication@archlens.invalid")
        git("config", "user.name", "Duplication DP1")
        git("add", ".")
        git("commit", "--quiet", "-m", "fixture")
        with prepare_local_source(root) as snapshot:
            document = analyze_duplication_snapshot(
                snapshot, requested_kinds=requested
            ).document
    validate_duplication_document(document)
    return document


_CLONED = {
    "src/a.py": CLONE_BODY.format(name="alpha"),
    "src/b.py": CLONE_BODY.format(name="beta"),
    "src/c.py": CLONE_BODY.format(name="gamma"),
}
_UNIQUE = {
    "src/a.py": UNIQUE_BODY.format(name="alpha", offset=3),
    "src/b.py": UNIQUE_BODY.format(name="beta", offset=5),
}

#: Built once. Every test that mutates takes a deep copy.
_CACHE: dict[tuple, dict] = {}


def document(
    sources: dict[str, str] | None = None,
    requested: tuple[str, ...] = ("lexical", "structural"),
) -> dict:
    key = (
        tuple(sorted((sources or _CLONED).items())),
        requested,
    )
    if key not in _CACHE:
        _CACHE[key] = _build(sources or _CLONED, requested)
    return copy.deepcopy(_CACHE[key])


def clean_document() -> dict:
    """A document from a repository with no clones at all: a MEASURED zero."""
    return document(_UNIQUE)


def failed_structural_document() -> dict:
    """A real document mutated so structural measurement FAILED.

    The mutation is applied at every level the contract reconciles -- per-file
    status and reason, the kind counts, the group list and the overall status --
    and the result is re-validated, so this document fails for exactly one
    reason and is otherwise a document the producer could have written.
    """
    payload = document()
    for item in payload["files"]:
        item["structural_status"] = "failed"
        item["structural_reason"] = "structural_canonicalization_failed"
    payload["structural_groups"] = []
    payload["counts"]["structural"] = {
        "status": "failed",
        "initial_group_count": None,
        "suppressed_group_count": None,
        "retained_group_count": None,
        "occurrence_count": None,
    }
    payload["status"] = "failed"
    validate_duplication_document(payload)
    return payload


def partial_lexical_document() -> dict:
    """A real document mutated so lexical measurement is PARTIAL.

    One of the three files failed its lexical stage, so the kind status
    reconciles to `partial`: some of the population was measured and some was
    not. The failed file is dropped from the clone group -- the contract refuses
    a group that references an incomplete file -- and every derived count and id
    is recomputed, so this stays a document the producer could have written.
    """
    payload = document()
    dropped = "src/c.py"
    for item in payload["files"]:
        if item["path"] == dropped:
            item["lexical_status"] = "failed"
            item["lexical_reason"] = "lexical_canonicalization_failed"
    group = payload["lexical_groups"][0]
    group["occurrences"] = [
        occurrence for occurrence in group["occurrences"]
        if occurrence["path"] != dropped
    ]
    group["occurrence_count"] = len(group["occurrences"])
    group["file_count"] = len({
        occurrence["path"] for occurrence in group["occurrences"]
    })
    group["distribution"] = "cross_file"
    group["group_id"] = _expected_group_id(group)
    payload["counts"]["lexical"] = {
        "status": "partial",
        "group_count": 1,
        "occurrence_count": group["occurrence_count"],
    }
    payload["status"] = "partial"
    validate_duplication_document(payload)
    return payload


def lexical_only_document() -> dict:
    """`--kind lexical`: structural is `not_requested`, never attempted."""
    return document(requested=("lexical",))


# --------------------------------------------------------------------------
# The run side
# --------------------------------------------------------------------------

class _FakeView:
    """The narrow slice of the run view the evaluator reads.

    A stand-in rather than a real bundle: DP1 changes what Policy does with an
    evidence document, not how a run is read, and every real-bundle path is
    already covered by `tests/test_policy_v2_check.py`.

    The view is built FROM the document, so the positive cases use a document
    the producer wrote and never touch its `source` block. Binding is inferential
    for duplication -- commit plus analysis scope hash -- so faking the run side
    is the only way to keep the evidence side authentic.
    """

    def __init__(
        self,
        payload: dict | None = None,
        subjects: tuple[str, ...] = (SUBJECT,),
        run_id: str = "run-1",
        scope_hash: str | None = None,
        commit: str | None = None,
    ) -> None:
        source = (payload or document())["source"]
        self.run_id = run_id
        self.manifest = {
            "artifact_schema_version": "1.11.0",
            "program_version": PROGRAM_VERSION,
        }
        self.repositories = [
            {
                "subject_key": key,
                "repository_url": None,
                "analysis_scope_hash": (
                    scope_hash if scope_hash is not None
                    else source["analysis_scope_hash"]
                ),
                "acquisition": {
                    "analyzed_commit_sha": (
                        commit if commit is not None
                        else source["resolved_revision"]
                    )
                },
                "analysis_status": "complete",
                "core_metric_status": "complete",
                "metrics": {"aggregate": {}, "by_language": {}},
            }
            for key in subjects
        ]
        self.has_callable_artifact = False
        self.qualification_mode = None
        self.benchmark_qualification = None
        self.benchmark_of_record_readiness = None
        self.lifecycle = None

    def stream_callables(self):
        return iter(())


def policy(
    metric: str,
    *,
    operator: str = "gt",
    threshold: int = 0,
    version: str = "2.2.0",
    **rule,
):
    return load_any_policy({
        "policy_document_format_version": version,
        "name": "duplication policy",
        "metric_rules": [{
            "id": "dup.rule", "metric": metric, "operator": operator,
            "threshold": threshold, **rule,
        }],
    })


def evaluate(policy_document, payload, *, view=None) -> dict:
    """Run the evaluator over one policy and one in-memory duplication document."""
    view = view or _FakeView(payload)
    evaluation = check_module.CheckEvaluation(Path("run"), policy_document)
    evaluation.view = view
    evaluation.manifest = dict(view.manifest)
    evaluation.status = {"status": "completed"}
    evaluation._build_subjects()
    scopes = evidence_module.analyzed_scopes(view.repositories)
    evaluation.evidence[evidence_module.EVIDENCE_DUPLICATION] = (
        evidence_module.admit_document(
            evidence_module.EVIDENCE_DUPLICATION, payload,
            run_id=view.run_id, scopes=scopes,
        )
        if payload is not None
        else evidence_module.not_supplied(evidence_module.EVIDENCE_DUPLICATION)
    )
    evaluation.evidence[evidence_module.EVIDENCE_HOTSPOTS] = (
        evidence_module.not_supplied(evidence_module.EVIDENCE_HOTSPOTS)
    )
    evaluation._index_duplication_evidence()
    evaluation.evaluate_metrics()
    return evaluation.result()


def only(result: dict) -> dict:
    found = result["findings"]
    assert len(found) == 1, f"expected one finding, got {len(found)}: {found}"
    return found[0]


DUPLICATION_METRICS = tuple(
    item.identifier for item in metric_module.METRICS
    if item.family == metric_module.FAMILY_DUPLICATION
)
REPOSITORY_METRICS = tuple(
    item for item in DUPLICATION_METRICS if item.startswith("repository.")
)
GROUP_METRICS = tuple(
    item for item in DUPLICATION_METRICS
    if item.startswith("duplication_group.")
)


# --------------------------------------------------------------------------
# The allowlist
# --------------------------------------------------------------------------

class AllowlistTests(unittest.TestCase):
    #: Pinned exactly, so a sixteenth duplication metric cannot appear without
    #: review. A naming ban would have been weaker: it forbids a word, this
    #: forbids an unreviewed addition.
    APPROVED = frozenset({
        "repository.duplication_eligible_file_count",
        "repository.duplication_candidate_complete_file_count",
        "repository.duplication_candidate_unavailable_file_count",
        "repository.duplication_observed_candidate_count",
        "repository.duplication_lexical_group_count",
        "repository.duplication_lexical_occurrence_count",
        "repository.duplication_structural_initial_group_count",
        "repository.duplication_structural_suppressed_group_count",
        "repository.duplication_structural_retained_group_count",
        "repository.duplication_structural_occurrence_count",
        "duplication_group.lexical_occurrence_count",
        "duplication_group.lexical_file_count",
        "duplication_group.structural_occurrence_count",
        "duplication_group.structural_file_count",
        "duplication_group.structural_source_span_line_count",
    })

    def test_duplication_metrics_are_exactly_the_approved_fifteen(self):
        self.assertEqual(set(DUPLICATION_METRICS), self.APPROVED)
        self.assertEqual(len(DUPLICATION_METRICS), 15)

    def test_every_duplication_metric_is_an_integer_count(self):
        for identifier in DUPLICATION_METRICS:
            with self.subTest(metric=identifier):
                definition = metric_module.METRICS_BY_ID[identifier]
                self.assertEqual(definition.value_type, "integer")
                self.assertEqual(
                    definition.requires_evidence, "archlens-duplication"
                )
                self.assertEqual(definition.minimum_document_version, "2.2.0")
                self.assertTrue(definition.source)
                self.assertTrue(definition.definition)

    def test_no_forbidden_duplication_metric_shape_exists(self):
        """No score, no ranking, no severity, no cross-kind total, no NLOC."""
        for identifier in DUPLICATION_METRICS:
            with self.subTest(metric=identifier):
                for word in (
                    "score", "rank", "signal", "risk", "defect", "bug",
                    "severity", "quality", "distribution", "duplicated_nloc",
                ):
                    self.assertNotIn(word, identifier)

    def test_no_metric_merges_the_two_kinds(self):
        """Every clone metric names exactly one kind; none spans both.

        The document never sums lexical and structural, and summing would be a
        new number: the two are different definitions, structural passes through
        a suppression stage lexical does not, and one clone can be counted by
        both.
        """
        for identifier in DUPLICATION_METRICS:
            with self.subTest(metric=identifier):
                lexical = "lexical" in identifier
                structural = "structural" in identifier
                self.assertFalse(
                    lexical and structural, "a metric spans both kinds"
                )
                if identifier.startswith("duplication_group."):
                    self.assertTrue(
                        lexical or structural,
                        "a group metric must name its kind",
                    )
                elif not (lexical or structural):
                    # The four file-population counts belong to neither kind:
                    # they count FILES, not clones, which is why they survive a
                    # kind failure. Their table entry records no kind at all.
                    self.assertIsNone(
                        metric_module.DUPLICATION_REPOSITORY_KIND[identifier]
                    )

    def test_the_group_scope_is_not_path_bearing(self):
        self.assertNotIn(
            metric_module.SCOPE_DUPLICATION_GROUP,
            metric_module.PATH_BEARING_SCOPES,
        )

    def test_the_new_scope_was_appended_not_inserted(self):
        """SCOPE_RANK is a finding sort key; inserting would reorder history."""
        self.assertEqual(
            metric_module.SCOPES[-1], metric_module.SCOPE_DUPLICATION_GROUP
        )
        self.assertEqual(
            metric_module.SCOPES[:4],
            ("repository", "language", "callable", "hotspot_file"),
        )


# --------------------------------------------------------------------------
# Completeness mapping
# --------------------------------------------------------------------------

class CompletenessMappingTests(unittest.TestCase):
    def test_the_five_statuses_map_as_specified(self):
        for status, expected in (
            ("complete", metric_module.COMPLETENESS_COMPLETE),
            ("partial", metric_module.COMPLETENESS_PARTIAL),
            ("failed", metric_module.COMPLETENESS_UNAVAILABLE),
            ("not_applicable", metric_module.COMPLETENESS_NOT_APPLICABLE),
            ("not_requested", metric_module.COMPLETENESS_UNAVAILABLE),
        ):
            with self.subTest(status=status):
                self.assertEqual(
                    metric_module.duplication_completeness_of_status(status),
                    expected,
                )

    def test_an_unrecognized_status_is_never_complete(self):
        for status in ("measured", "", None, 0, "COMPLETE"):
            with self.subTest(status=status):
                self.assertEqual(
                    metric_module.duplication_completeness_of_status(status),
                    metric_module.COMPLETENESS_UNAVAILABLE,
                )

    def test_not_requested_has_its_own_published_reason(self):
        self.assertIn(
            finding_module.REASON_EVIDENCE_KIND_NOT_REQUESTED,
            finding_module.REASON_MEANINGS,
        )
        meaning = finding_module.REASON_MEANINGS[
            finding_module.REASON_EVIDENCE_KIND_NOT_REQUESTED
        ]
        self.assertIn("not an absence of duplicates", meaning)


# --------------------------------------------------------------------------
# Admission
# --------------------------------------------------------------------------

class AdmissionTests(unittest.TestCase):
    def test_a_matching_document_is_admitted_and_bound_to_one_subject(self):
        payload = document()
        result = evaluate(
            policy("repository.duplication_lexical_group_count"), payload
        )
        record = result["evidence"]["duplication"]
        self.assertEqual(record["admission"], "admitted")
        self.assertEqual(record["binding"]["state"], "bound")
        self.assertEqual(record["binding"]["subject_keys"], [SUBJECT])
        self.assertEqual(record["used_by_rules"], ["dup.rule"])

    def test_a_document_from_another_revision_is_refused(self):
        payload = document()
        view = _FakeView(payload, commit="b" * 40)
        result = evaluate(
            policy("repository.duplication_lexical_group_count"), payload,
            view=view,
        )
        record = result["evidence"]["duplication"]
        self.assertEqual(record["admission"], "provenance_mismatch")
        self.assertEqual(
            record["reason"], evidence_module.REASON_NO_SUBJECT_AT_COMMIT
        )
        self.assertNotEqual(result["verdict"], "pass")
        finding = only(result)
        self.assertEqual(finding["status"], "not_evaluable")
        self.assertEqual(
            finding["reason"], finding_module.REASON_EVIDENCE_NOT_ADMITTED
        )
        self.assertIsNone(finding["observed_value"])

    def test_a_document_from_another_scope_is_refused(self):
        payload = document()
        view = _FakeView(payload, scope_hash="sha256:" + ("9" * 64))
        result = evaluate(
            policy("repository.duplication_lexical_group_count"), payload,
            view=view,
        )
        record = result["evidence"]["duplication"]
        self.assertEqual(record["admission"], "provenance_mismatch")
        self.assertEqual(
            record["reason"], evidence_module.REASON_SCOPE_HASH_MISMATCH
        )
        # Distinguishable from the wrong-revision case by reason alone: the
        # commit matched and the analyzed file set did not.
        self.assertEqual(
            record["provenance_evidence"]["subjects_at_commit"], [SUBJECT]
        )
        self.assertEqual(only(result)["status"], "not_evaluable")

    def test_an_ambiguous_binding_is_refused_never_resolved(self):
        """Two subjects at one commit and one scope hash: refuse, do not pick."""
        payload = document()
        view = _FakeView(payload, subjects=(SUBJECT, OTHER_SUBJECT))
        result = evaluate(
            policy("repository.duplication_lexical_group_count"), payload,
            view=view,
        )
        record = result["evidence"]["duplication"]
        self.assertEqual(record["admission"], "provenance_mismatch")
        self.assertEqual(
            record["reason"], evidence_module.REASON_AMBIGUOUS_BINDING
        )
        self.assertEqual(record["binding"]["state"], "ambiguous")
        self.assertEqual(
            sorted(record["binding"]["candidate_subject_keys"]),
            sorted([SUBJECT, OTHER_SUBJECT]),
        )
        # Never resolved to the first match: NEITHER subject evaluates.
        for finding in result["findings"]:
            self.assertEqual(finding["status"], "not_evaluable")

    def test_the_evaluator_never_imports_the_duplication_validator(self):
        """Admission through the registry is the only door."""
        import ast

        source = Path(check_module.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                self.assertNotEqual(node.module, "modules.duplication.output")
                for alias in node.names:
                    self.assertNotEqual(
                        alias.name, "validate_duplication_document"
                    )
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertFalse(
                        alias.name.startswith("modules.duplication")
                    )

    def test_no_duplication_module_imports_policy(self):
        """The dependency edge is one-way, and it is asserted, not assumed."""
        import ast

        root = Path(check_module.__file__).resolve().parents[2]
        for path in sorted((root / "modules" / "duplication").glob("*.py")):
            with self.subTest(module=path.name):
                tree = ast.parse(path.read_text(encoding="utf-8"))
                for node in ast.walk(tree):
                    names = []
                    if isinstance(node, ast.ImportFrom):
                        names.append(node.module or "")
                    elif isinstance(node, ast.Import):
                        names.extend(alias.name for alias in node.names)
                    for name in names:
                        self.assertFalse(
                            name.startswith("modules.policy"), f"{path}: {name}"
                        )


# --------------------------------------------------------------------------
# Multi-subject runs
# --------------------------------------------------------------------------

class MultiSubjectTests(unittest.TestCase):
    """One document describes one snapshot, so it speaks for one subject."""

    def _view(self, payload: dict) -> _FakeView:
        view = _FakeView(payload, subjects=(SUBJECT, OTHER_SUBJECT))
        # Separate the two subjects, so binding is unambiguous rather than
        # refused: the second subject was analyzed at a different revision.
        view.repositories[1]["acquisition"]["analyzed_commit_sha"] = "c" * 40
        view.repositories[1]["analysis_scope_hash"] = "sha256:" + ("2" * 64)
        return view

    def test_the_bound_subject_evaluates_and_the_others_do_not(self):
        payload = document()
        result = evaluate(
            policy("repository.duplication_lexical_group_count", threshold=0),
            payload, view=self._view(payload),
        )
        by_subject = {item["subject_key"]: item for item in result["findings"]}
        self.assertEqual(by_subject[SUBJECT]["status"], "violated")
        self.assertEqual(by_subject[SUBJECT]["observed_value"], 1)
        other = by_subject[OTHER_SUBJECT]
        self.assertEqual(other["status"], "not_evaluable")
        self.assertEqual(
            other["reason"],
            finding_module.REASON_EVIDENCE_DOES_NOT_COVER_SUBJECT,
        )
        self.assertIsNone(other["observed_value"])

    def test_an_uncovered_subject_is_never_a_pass_at_group_scope(self):
        payload = document()
        result = evaluate(
            policy("duplication_group.lexical_occurrence_count", threshold=99),
            payload, view=self._view(payload),
        )
        other = [
            item for item in result["findings"]
            if item["subject_key"] == OTHER_SUBJECT
        ]
        self.assertEqual(len(other), 1)
        self.assertEqual(other[0]["status"], "not_evaluable")
        self.assertEqual(
            other[0]["reason"],
            finding_module.REASON_EVIDENCE_DOES_NOT_COVER_SUBJECT,
        )
        self.assertEqual(other[0]["scope"], "duplication_group")


# --------------------------------------------------------------------------
# The kind status is the source of truth
# --------------------------------------------------------------------------

class KindStatusIsTheSourceOfTruthTests(unittest.TestCase):
    """An empty group list means three different things. Only the status knows."""

    def test_all_three_empty_group_cases_are_told_apart(self):
        cases = (
            ("not_requested", lexical_only_document(),
             finding_module.REASON_EVIDENCE_KIND_NOT_REQUESTED, "not_evaluable"),
            ("failed", failed_structural_document(),
             finding_module.REASON_MEASUREMENT_UNAVAILABLE, "not_evaluable"),
            ("complete", clean_document(),
             finding_module.REASON_NOTHING_TO_MEASURE, "not_applicable"),
        )
        for label, payload, reason, status in cases:
            with self.subTest(case=label):
                # In every one of the three, the group list is EMPTY.
                self.assertEqual(payload["structural_groups"], [])
                result = evaluate(
                    policy(
                        "duplication_group.structural_occurrence_count",
                        threshold=0,
                    ),
                    payload,
                )
                finding = only(result)
                self.assertEqual(finding["status"], status)
                self.assertEqual(finding["reason"], reason)
                self.assertIsNone(finding["observed_value"])
                self.assertNotEqual(finding["status"], "passed")

    def test_a_not_requested_kind_is_never_a_pass_at_repository_scope(self):
        payload = lexical_only_document()
        self.assertEqual(payload["counts"]["structural"]["status"],
                         "not_requested")
        for metric in (
            "repository.duplication_structural_retained_group_count",
            "repository.duplication_structural_occurrence_count",
            "repository.duplication_structural_initial_group_count",
            "repository.duplication_structural_suppressed_group_count",
        ):
            with self.subTest(metric=metric):
                finding = only(evaluate(policy(metric, threshold=0), payload))
                self.assertEqual(finding["status"], "not_evaluable")
                self.assertEqual(
                    finding["reason"],
                    finding_module.REASON_EVIDENCE_KIND_NOT_REQUESTED,
                )
                self.assertIsNone(finding["observed_value"])

    def test_a_not_requested_kind_does_not_disturb_the_other_kind(self):
        payload = lexical_only_document()
        finding = only(evaluate(
            policy("repository.duplication_lexical_group_count", threshold=0),
            payload,
        ))
        self.assertEqual(finding["status"], "violated")
        self.assertEqual(finding["observed_value"], 1)
        self.assertEqual(finding["data_completeness"], "complete")

    def test_a_failed_kind_reports_a_failed_measurement_not_a_missing_kind(self):
        payload = failed_structural_document()
        finding = only(evaluate(
            policy(
                "repository.duplication_structural_retained_group_count",
                threshold=0,
            ),
            payload,
        ))
        self.assertEqual(finding["status"], "not_evaluable")
        self.assertEqual(
            finding["reason"], finding_module.REASON_MEASUREMENT_UNAVAILABLE
        )
        self.assertEqual(finding["value_status"], "failed")
        self.assertEqual(finding["value_status_field"], "counts.structural.status")

    def test_the_file_population_counts_survive_a_failed_kind(self):
        """`candidate_unavailable_file_count` must not vanish when a kind fails.

        It is the honest count of what the analysis could not look at, so gating
        it on a detection kind's status would make it disappear at exactly the
        moment it matters.
        """
        payload = failed_structural_document()
        for metric, expected in (
            ("repository.duplication_eligible_file_count", 3),
            ("repository.duplication_candidate_complete_file_count", 3),
            ("repository.duplication_candidate_unavailable_file_count", 0),
        ):
            with self.subTest(metric=metric):
                result = evaluate(
                    policy(metric, operator="gte", threshold=0), payload
                )
                finding = only(result)
                self.assertEqual(finding["status"], "violated")
                self.assertEqual(finding["observed_value"], expected)
                self.assertEqual(finding["data_completeness"], "complete")
                self.assertEqual(
                    finding["value_status_field"],
                    "(duplication evidence coverage)",
                )


# --------------------------------------------------------------------------
# Measured zero
# --------------------------------------------------------------------------

class MeasuredZeroTests(unittest.TestCase):
    def test_a_measured_zero_is_reported_as_a_number(self):
        payload = clean_document()
        self.assertEqual(payload["counts"]["lexical"]["status"], "complete")
        self.assertEqual(payload["counts"]["lexical"]["group_count"], 0)
        finding = only(evaluate(
            policy(
                "repository.duplication_lexical_group_count",
                operator="gte", threshold=0,
            ),
            payload,
        ))
        self.assertEqual(finding["status"], "violated")
        self.assertEqual(finding["observed_value"], 0)
        self.assertEqual(finding["data_completeness"], "complete")

    def test_a_measured_zero_passes_a_threshold_it_should_pass(self):
        result = evaluate(
            policy("repository.duplication_lexical_group_count", threshold=0),
            clean_document(),
        )
        self.assertEqual(result["findings"], [])
        self.assertEqual(result["verdict"], "pass")

    def test_a_measured_empty_population_is_not_applicable_at_group_scope(self):
        """No group to evaluate, after a COMPLETE measurement: a full answer."""
        finding = only(evaluate(
            policy("duplication_group.lexical_occurrence_count", threshold=0),
            clean_document(),
        ))
        self.assertEqual(finding["status"], "not_applicable")
        self.assertEqual(
            finding["reason"], finding_module.REASON_NOTHING_TO_MEASURE
        )
        self.assertEqual(finding["data_completeness"], "not_applicable")


# --------------------------------------------------------------------------
# Missing is never zero
# --------------------------------------------------------------------------

class MissingIsNeverZeroTests(unittest.TestCase):
    def _assert_never_zero(self, result: dict, reason: str) -> None:
        self.assertTrue(result["findings"])
        for finding in result["findings"]:
            self.assertIn(
                finding["status"], ("not_evaluable", "not_applicable")
            )
            self.assertNotEqual(finding["status"], "passed")
            self.assertIsNone(finding["observed_value"])
            self.assertEqual(finding["reason"], reason)

    def test_no_document_supplied_is_not_a_count_of_zero(self):
        for metric in DUPLICATION_METRICS:
            with self.subTest(metric=metric):
                result = evaluate(policy(metric, threshold=0), None)
                self._assert_never_zero(
                    result, finding_module.REASON_EVIDENCE_NOT_SUPPLIED
                )

    def test_a_refused_document_is_not_a_count_of_zero(self):
        payload = document()
        view = _FakeView(payload, commit="d" * 40)
        for metric in DUPLICATION_METRICS:
            with self.subTest(metric=metric):
                result = evaluate(
                    policy(metric, threshold=0), payload, view=view
                )
                self._assert_never_zero(
                    result, finding_module.REASON_EVIDENCE_NOT_ADMITTED
                )

    def test_a_not_requested_kind_is_not_a_count_of_zero(self):
        payload = lexical_only_document()
        structural = [
            metric for metric in DUPLICATION_METRICS if "structural" in metric
        ]
        self.assertTrue(structural)
        for metric in structural:
            with self.subTest(metric=metric):
                result = evaluate(policy(metric, threshold=0), payload)
                self._assert_never_zero(
                    result,
                    finding_module.REASON_EVIDENCE_KIND_NOT_REQUESTED,
                )

    def test_a_null_observed_candidate_count_is_unavailable(self):
        """The one nullable count in the contract, exercised end to end."""
        payload = document()
        payload["counts"]["observed_candidate_count"] = None
        for item in payload["files"]:
            item["candidate_status"] = "unavailable"
            item["candidate_reason"] = "candidate_extraction_failed"
            item["candidate_count"] = None
        payload["counts"]["candidate_complete_file_count"] = 0
        payload["counts"]["candidate_unavailable_file_count"] = 3
        for kind in ("lexical", "structural"):
            for item in payload["files"]:
                item[f"{kind}_status"] = "failed"
                item[f"{kind}_reason"] = "candidate_extraction_failed"
        payload["lexical_groups"] = []
        payload["structural_groups"] = []
        payload["counts"]["lexical"] = {
            "status": "failed", "group_count": None, "occurrence_count": None,
        }
        payload["counts"]["structural"] = {
            "status": "failed", "initial_group_count": None,
            "suppressed_group_count": None, "retained_group_count": None,
            "occurrence_count": None,
        }
        payload["status"] = "failed"
        validate_duplication_document(payload)

        finding = only(evaluate(
            policy(
                "repository.duplication_observed_candidate_count",
                operator="gte", threshold=0,
            ),
            payload,
        ))
        self.assertEqual(finding["status"], "not_evaluable")
        self.assertIsNone(finding["observed_value"])
        self.assertEqual(
            finding["reason"], finding_module.REASON_MEASUREMENT_UNAVAILABLE
        )

    def test_the_unavailable_file_count_is_still_readable_when_everything_failed(self):
        """The complement of the above, and the point of the separate gate."""
        payload = document()
        payload["counts"]["observed_candidate_count"] = None
        for item in payload["files"]:
            item["candidate_status"] = "unavailable"
            item["candidate_reason"] = "candidate_extraction_failed"
            item["candidate_count"] = None
            for kind in ("lexical", "structural"):
                item[f"{kind}_status"] = "failed"
                item[f"{kind}_reason"] = "candidate_extraction_failed"
        payload["counts"]["candidate_complete_file_count"] = 0
        payload["counts"]["candidate_unavailable_file_count"] = 3
        payload["lexical_groups"] = []
        payload["structural_groups"] = []
        payload["counts"]["lexical"] = {
            "status": "failed", "group_count": None, "occurrence_count": None,
        }
        payload["counts"]["structural"] = {
            "status": "failed", "initial_group_count": None,
            "suppressed_group_count": None, "retained_group_count": None,
            "occurrence_count": None,
        }
        payload["status"] = "failed"
        validate_duplication_document(payload)

        finding = only(evaluate(
            policy(
                "repository.duplication_candidate_unavailable_file_count",
                threshold=0,
            ),
            payload,
        ))
        self.assertEqual(finding["status"], "violated")
        self.assertEqual(finding["observed_value"], 3)


# --------------------------------------------------------------------------
# Findings
# --------------------------------------------------------------------------

class GroupFindingTests(unittest.TestCase):
    def _violation(self, metric: str = "duplication_group.lexical_occurrence_count"):
        payload = document()
        result = evaluate(policy(metric, threshold=2), payload)
        return payload, only(result)

    def test_a_group_finding_points_at_the_first_occurrence(self):
        payload, finding = self._violation()
        group = payload["lexical_groups"][0]
        first = group["occurrences"][0]
        self.assertEqual(finding["status"], "violated")
        self.assertEqual(finding["observed_value"], 3)
        self.assertEqual(finding["scope"], "duplication_group")
        self.assertEqual(finding["path"], first["path"])
        self.assertEqual(finding["start_line"], first["start_line"])
        self.assertEqual(finding["end_line"], first["end_line"])

    def test_the_evidence_carries_the_whole_group(self):
        payload, finding = self._violation()
        group = payload["lexical_groups"][0]
        evidence = finding["evidence"]
        self.assertEqual(evidence["duplication_kind"], "lexical")
        self.assertEqual(evidence["duplication_group_id"], group["group_id"])
        self.assertEqual(
            evidence["duplication_fingerprint"], group["fingerprint"]
        )
        self.assertEqual(
            evidence["duplication_fingerprint_version"],
            group["fingerprint_version"],
        )
        self.assertEqual(
            evidence["duplication_distribution"], group["distribution"]
        )
        self.assertEqual(evidence["duplication_occurrence_count"], 3)
        self.assertEqual(evidence["duplication_file_count"], 3)
        self.assertEqual(len(evidence["duplication_occurrences"]), 3)
        self.assertFalse(evidence["duplication_occurrences_truncated"])
        self.assertIn("not a defect", evidence["duplication_note"])

    def test_a_structural_finding_carries_the_merged_span_length(self):
        payload = document()
        finding = only(evaluate(
            policy(
                "duplication_group.structural_source_span_line_count",
                threshold=1,
            ),
            payload,
        ))
        self.assertEqual(
            finding["observed_value"],
            payload["structural_groups"][0]["source_span_line_count"],
        )
        self.assertEqual(
            finding["evidence"]["duplication_source_span_line_count"],
            payload["structural_groups"][0]["source_span_line_count"],
        )

    def test_the_occurrence_list_is_capped_and_the_true_count_survives(self):
        payload = document()
        group = payload["lexical_groups"][0]
        template = dict(group["occurrences"][0])
        # A pathological group: far more occurrences than the evidence cap.
        cap = check_module.DUPLICATION_OCCURRENCE_EVIDENCE_CAP
        group["occurrences"] = [
            {**template, "path": f"src/gen{index}.py"}
            for index in range(cap + 7)
        ]
        group["occurrence_count"] = cap + 7
        group["file_count"] = cap + 7
        evidence = check_module._duplication_group_evidence(group, "lexical")
        self.assertEqual(len(evidence["duplication_occurrences"]), cap)
        self.assertTrue(evidence["duplication_occurrences_truncated"])
        # The EXACT count, not the capped length.
        self.assertEqual(evidence["duplication_occurrence_count"], cap + 7)

    def test_a_lexical_group_has_no_span_length_in_its_evidence(self):
        payload = document()
        evidence = check_module._duplication_group_evidence(
            payload["lexical_groups"][0], "lexical"
        )
        self.assertNotIn("duplication_source_span_line_count", evidence)


class IdentityTests(unittest.TestCase):
    def _identity(self, payload: dict, metric: str, threshold: int = 2) -> str:
        return only(evaluate(policy(metric, threshold=threshold), payload))[
            "finding_id"
        ]

    def test_identity_uses_the_fingerprint_not_the_group_id(self):
        payload = document()
        finding = only(evaluate(
            policy("duplication_group.lexical_occurrence_count", threshold=2),
            payload,
        ))
        fingerprint = payload["lexical_groups"][0]["fingerprint"]
        self.assertEqual(
            finding["evidence"]["duplication_fingerprint"], fingerprint
        )

        # Shift every occurrence down by ten lines, exactly as an unrelated edit
        # above the clone would. `group_id` and every `occurrence_id` MOVE,
        # because both digest coordinates; the finding id must not, because it
        # digests the fingerprint.
        shifted = document()
        group = shifted["lexical_groups"][0]
        for occurrence in group["occurrences"]:
            occurrence["start_line"] += 10
            occurrence["end_line"] += 10
            occurrence["occurrence_id"] = occurrence_identity(
                _coordinate(occurrence)
            )
        moved = _expected_group_id(group)
        self.assertNotEqual(moved, payload["lexical_groups"][0]["group_id"])
        group["group_id"] = moved
        validate_duplication_document(shifted)

        self.assertEqual(
            self._identity(payload, "duplication_group.lexical_occurrence_count"),
            self._identity(shifted, "duplication_group.lexical_occurrence_count"),
        )

    def test_identity_does_not_move_when_the_threshold_moves(self):
        payload = document()
        self.assertEqual(
            self._identity(
                payload, "duplication_group.lexical_occurrence_count", 1
            ),
            self._identity(
                payload, "duplication_group.lexical_occurrence_count", 2
            ),
        )

    def test_identity_moves_when_the_clone_body_changes(self):
        payload = document()
        changed = document()
        group = changed["lexical_groups"][0]
        group["fingerprint"] = "sha256:" + ("7" * 64)
        # `group_id` digests the fingerprint as well as the coordinates, so it
        # moves with it for the document to stay self-consistent. That is
        # exactly why `group_id` is unusable as a finding identity and the
        # fingerprint is not: one of the two moves for reasons the finding does
        # not care about.
        group["group_id"] = _expected_group_id(group)
        validate_duplication_document(changed)
        self.assertNotEqual(
            self._identity(payload, "duplication_group.lexical_occurrence_count"),
            self._identity(changed, "duplication_group.lexical_occurrence_count"),
        )

    def test_path_is_not_an_identity_component(self):
        """Renaming the canonically-first file must not orphan the finding.

        The rename is chosen to sort the file LAST, so after the document is
        re-canonicalized a different occurrence becomes the group's first one
        and the finding's reported location changes. If `path` were an identity
        component the id would change with it, and every waiver and every piece
        of history attached to this clone would be lost to a rename that did not
        touch the code.
        """
        payload = document()
        metric = "duplication_group.lexical_occurrence_count"
        before = self._identity(payload, metric)
        before_path = only(
            evaluate(policy(metric, threshold=2), payload)
        )["path"]

        renamed = document()
        original = renamed["lexical_groups"][0]["occurrences"][0]["path"]
        for item in renamed["files"]:
            if item["path"] == original:
                item["path"] = "src/zz_renamed.py"
        # The rename is a fact about the whole snapshot, so it applies to every
        # group that references the file, not only the one under test.
        for key in ("lexical_groups", "structural_groups"):
            for item in renamed[key]:
                for occurrence in item["occurrences"]:
                    if occurrence["path"] == original:
                        occurrence["path"] = "src/zz_renamed.py"
                    occurrence["occurrence_id"] = occurrence_identity(
                        _coordinate(occurrence)
                    )
                # Occurrences are canonically ordered by coordinate, so the
                # renamed one moves to the end of the group.
                item["occurrences"].sort(key=_coordinate)
                item["group_id"] = _expected_group_id(item)
                if "source_span_union" in item:
                    item["source_span_union"] = sorted(
                        (
                            {
                                "path": occurrence["path"],
                                "start_line": occurrence["start_line"],
                                "end_line": occurrence["end_line"],
                            }
                            for occurrence in item["occurrences"]
                        ),
                        key=lambda span: (
                            span["path"], span["start_line"], span["end_line"]
                        ),
                    )
        renamed["files"] = sorted(renamed["files"], key=lambda row: row["path"])
        validate_duplication_document(renamed)

        after = only(evaluate(policy(metric, threshold=2), renamed))
        # The location moved...
        self.assertNotEqual(after["path"], before_path)
        # ...and the identity did not.
        self.assertEqual(before, after["finding_id"])


class DeterminismTests(unittest.TestCase):
    def test_two_evaluations_are_byte_identical(self):
        payload = document()
        rule = policy("duplication_group.lexical_occurrence_count", threshold=1)
        first = evaluate(rule, payload)
        second = evaluate(rule, document())
        for result in (first, second):
            result.pop("evaluated_at")
        self.assertEqual(
            json.dumps(first, sort_keys=True), json.dumps(second, sort_keys=True)
        )

    def test_group_findings_sort_deterministically(self):
        payload = document()
        result = evaluate(
            policy("duplication_group.lexical_occurrence_count", threshold=0),
            payload,
        )
        identifiers = [item["finding_id"] for item in result["findings"]]
        self.assertEqual(identifiers, sorted(set(identifiers)) if len(
            identifiers
        ) == len(set(identifiers)) else identifiers)
        self.assertEqual(len(identifiers), len(set(identifiers)))


# --------------------------------------------------------------------------
# Policy document compatibility
# --------------------------------------------------------------------------

class ContractCompatibilityTests(unittest.TestCase):
    def test_a_2_1_policy_may_not_name_a_duplication_metric(self):
        for metric in DUPLICATION_METRICS:
            with self.subTest(metric=metric):
                with self.assertRaises(PolicyDocumentInvalid) as caught:
                    policy(metric, version="2.1.0")
                self.assertIn("2.2.0", str(caught.exception))

    def test_a_2_0_policy_may_not_name_a_duplication_metric(self):
        with self.assertRaises(PolicyDocumentInvalid):
            policy(DUPLICATION_METRICS[0], version="2.0.0")

    def test_a_2_2_policy_may_name_them_all(self):
        for metric in DUPLICATION_METRICS:
            with self.subTest(metric=metric):
                loaded = policy(metric)
                self.assertEqual(loaded.source_format_version, "2.2.0")

    def test_older_policies_still_load_and_are_never_restamped(self):
        for version in ("2.0.0", "2.1.0", "2.2.0"):
            with self.subTest(version=version):
                loaded = load_any_policy({
                    "policy_document_format_version": version,
                    "name": "legacy",
                    "metric_rules": [{
                        "id": "loc", "metric": "repository.lines_of_code",
                        "operator": "gt", "threshold": 1,
                    }],
                })
                self.assertEqual(loaded.source_format_version, version)
                self.assertEqual(
                    loaded.as_dict()["policy_document_format_version"], version
                )

    def test_policy_2_2_remains_active_under_additive_check_result_1_3(self):
        self.assertEqual(POLICY_DOCUMENT_V2_FORMAT_VERSION, "2.2.0")
        self.assertEqual(CHECK_RESULT_FORMAT_VERSION, "1.4.0")
        self.assertEqual(RATCHET_CHECK_RESULT_FORMAT_VERSION, "1.4.0")

    def test_path_filters_are_refused_at_group_scope(self):
        """A clone group spans N files; a path filter would need a quantifier."""
        for key in ("paths", "exclude_paths"):
            with self.subTest(filter=key):
                with self.assertRaises(PolicyDocumentInvalid) as caught:
                    policy(
                        "duplication_group.lexical_occurrence_count",
                        **{key: ["src/*"]},
                    )
                self.assertIn("per-file location", str(caught.exception))

    def test_languages_narrows_a_group_rule(self):
        payload = document()
        matched = evaluate(
            policy(
                "duplication_group.lexical_occurrence_count", threshold=2,
                languages=["Python"],
            ),
            payload,
        )
        self.assertEqual(only(matched)["status"], "violated")
        missed = evaluate(
            policy(
                "duplication_group.lexical_occurrence_count", threshold=2,
                languages=["Java"],
            ),
            payload,
        )
        self.assertEqual(only(missed)["status"], "not_applicable")


class SchemaTests(unittest.TestCase):
    def test_every_result_validates_against_the_activated_schema(self):
        from validation.artifact_io.schema_store import (
            schema_name_for_document,
            validate_document,
        )

        cases = (
            ("admitted", document(), None),
            ("not supplied", None, None),
            ("refused", document(), "commit"),
            ("kind not requested", lexical_only_document(), None),
        )
        for label, payload, mutate in cases:
            with self.subTest(case=label):
                view = (
                    _FakeView(payload, commit="e" * 40)
                    if mutate else (_FakeView(payload) if payload else None)
                )
                result = evaluate(
                    policy(
                        "repository.duplication_structural_retained_group_count",
                        threshold=0,
                    ),
                    payload, view=view,
                )
                name = schema_name_for_document("check_result_output", result)
                self.assertEqual(name, "check_result_output")
                self.assertEqual(
                    [str(item) for item in validate_document(
                        name, result, "check_result.json"
                    )],
                    [],
                )

    def test_the_group_finding_scope_validates(self):
        from validation.artifact_io.schema_store import (
            schema_name_for_document,
            validate_document,
        )

        result = evaluate(
            policy("duplication_group.lexical_occurrence_count", threshold=1),
            document(),
        )
        self.assertEqual(only(result)["scope"], "duplication_group")
        name = schema_name_for_document("check_result_output", result)
        self.assertEqual(
            [str(item) for item in validate_document(
                name, result, "check_result.json"
            )],
            [],
        )


# --------------------------------------------------------------------------
# Text output
# --------------------------------------------------------------------------

class TextOutputTests(unittest.TestCase):
    def _render(self, result: dict) -> str:
        from modules.cli.check_command import render_text

        return render_text(result)

    def test_the_evidence_block_reaches_text_output(self):
        result = evaluate(
            policy("repository.duplication_lexical_group_count", threshold=99),
            document(),
        )
        rendered = self._render(result)
        self.assertIn("evidence:", rendered)
        self.assertIn("duplication", rendered)
        self.assertIn("admitted", rendered)
        self.assertIn(SUBJECT, rendered)

    def test_text_and_json_agree_about_admission(self):
        for label, payload, view in (
            ("admitted", document(), None),
            ("not supplied", None, None),
            ("refused", document(), _FakeView(document(), commit="f" * 40)),
        ):
            with self.subTest(case=label):
                result = evaluate(
                    policy(
                        "repository.duplication_lexical_group_count",
                        threshold=99,
                    ),
                    payload, view=view,
                )
                rendered = self._render(result)
                admission = result["evidence"]["duplication"]["admission"]
                self.assertIn(admission, rendered)

    def test_a_gate_with_no_evidence_says_so(self):
        result = evaluate(
            policy("repository.duplication_lexical_group_count", threshold=99),
            None,
        )
        rendered = self._render(result)
        self.assertIn("not_supplied", rendered)
        self.assertIn("NOT evaluable", rendered)

    def test_the_failure_path_reports_that_no_evidence_was_considered(self):
        from modules.policy.check import failure_result

        payload = failure_result(
            kind="usage", message="usage error",
            run_directory=Path("run"), policy_name="policy",
        )
        rendered = self._render(payload)
        self.assertIn("none considered", rendered)
        self.assertIn("This is not a pass.", rendered)


class MetricListingTests(unittest.TestCase):
    def test_the_listing_publishes_the_new_family_and_scope(self):
        from modules.cli.check_command import render_metric_listing

        listing = render_metric_listing()
        self.assertEqual(
            listing["policy_document_format_version"], "2.2.0"
        )
        self.assertIn("duplication_group", listing["scopes"])
        exposed = {
            item["metric"] for item in listing["metrics"]
            if item["family"] == "duplication"
        }
        self.assertEqual(exposed, set(DUPLICATION_METRICS))
        for item in listing["metrics"]:
            if item["family"] == "duplication":
                self.assertEqual(
                    item["requires_evidence"], "archlens-duplication"
                )
                self.assertEqual(item["minimum_document_version"], "2.2.0")


# --------------------------------------------------------------------------
# Partial observations
# --------------------------------------------------------------------------

class PartialDataTests(unittest.TestCase):
    """`partial` is evaluable, but it says so -- and the policy may refuse it."""

    def _policy(self, metric: str, partial_data: str, threshold: int = 0):
        return load_any_policy({
            "policy_document_format_version": "2.2.0",
            "name": "duplication policy",
            "options": {"partial_data": partial_data},
            "metric_rules": [{
                "id": "dup.rule", "metric": metric, "operator": "gt",
                "threshold": threshold,
            }],
        })

    def test_a_partial_kind_evaluates_and_says_so(self):
        payload = partial_lexical_document()
        self.assertEqual(payload["counts"]["lexical"]["status"], "partial")
        finding = only(evaluate(
            self._policy("repository.duplication_lexical_group_count", "evaluate"),
            payload,
        ))
        self.assertEqual(finding["status"], "violated")
        self.assertEqual(finding["observed_value"], 1)
        self.assertEqual(finding["data_completeness"], "partial")
        self.assertIn("partial observation", finding["message"])

    def test_a_partial_kind_is_refused_when_the_policy_says_so(self):
        finding = only(evaluate(
            self._policy(
                "repository.duplication_lexical_group_count", "not_evaluable"
            ),
            partial_lexical_document(),
        ))
        self.assertEqual(finding["status"], "not_evaluable")
        self.assertEqual(
            finding["reason"], finding_module.REASON_PARTIAL_DATA_REFUSED
        )
        self.assertIsNone(finding["observed_value"])

    def test_a_partial_kind_carries_partial_into_group_findings(self):
        finding = only(evaluate(
            self._policy(
                "duplication_group.lexical_occurrence_count", "evaluate",
                threshold=1,
            ),
            partial_lexical_document(),
        ))
        self.assertEqual(finding["status"], "violated")
        self.assertEqual(finding["scope"], "duplication_group")
        self.assertEqual(finding["data_completeness"], "partial")

    def test_a_refused_partial_group_collapses_rather_than_passing(self):
        result = evaluate(
            self._policy(
                "duplication_group.lexical_occurrence_count", "not_evaluable",
                threshold=1,
            ),
            partial_lexical_document(),
        )
        finding = only(result)
        self.assertEqual(finding["status"], "not_evaluable")
        self.assertEqual(
            finding["reason"], finding_module.REASON_PARTIAL_DATA_REFUSED
        )
        self.assertEqual(finding["evidence"]["collapsed_duplication_groups"], 1)


class FixtureIntegrityTests(unittest.TestCase):
    """Every fixture is a document the producer's own validator accepts."""

    def test_every_fixture_validates(self):
        for label, payload in (
            ("both kinds", document()),
            ("lexical only", lexical_only_document()),
            ("failed structural", failed_structural_document()),
            ("partial lexical", partial_lexical_document()),
            ("no clones", clean_document()),
        ):
            with self.subTest(fixture=label):
                validate_duplication_document(payload)

    def test_a_broken_fixture_would_be_caught(self):
        payload = document()
        payload["counts"]["lexical"]["group_count"] = 99
        with self.assertRaises(DuplicationOutputError):
            validate_duplication_document(payload)


if __name__ == "__main__":
    unittest.main()
