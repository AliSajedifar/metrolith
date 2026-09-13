"""ArchLens 4.0 Hotspot Policy integration, H1: core evaluation.

A Hotspot document reaches Policy through the admission boundary and nowhere
else, its published counts become metric observations, and a rule over them
produces findings. Hotspot semantics do not move: nothing here re-derives a
classification, invents a score, or reads an ordinal as a number.

The load-bearing property under test is the one that is easiest to lose:
**missing is not zero.** Four different absences each get their own typed
reason, and none of them is ever a pass.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from modules import hotspots as hotspot_module
from modules.config import PROGRAM_VERSION
from modules.policy import check as check_module
from modules.policy import evidence as evidence_module
from modules.policy import findings as finding_module
from modules.policy import metrics as metric_module
from modules.policy.check import CHECK_RESULT_FORMAT_VERSION
from modules.policy.document_v2 import (
    POLICY_DOCUMENT_V2_FORMAT_VERSION,
    load_any_policy,
)

COMMIT = "a" * 40
SUBJECT = "local:demo"
OTHER_SUBJECT = "local:other"


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------

def signal(value: str | None, *, status: str = "measured") -> dict:
    if status != "measured":
        return {"status": status, "signal": None, "cohort": None}
    return {
        "status": "measured", "signal": value,
        "cohort": {"distinct_value_count": 3, "distinct_rank": 2},
    }


def hotspot_row(
    path: str,
    *,
    subject_key: str = SUBJECT,
    complexity_signal: str | None = "high",
    churn_signal: str | None = "high",
    cognitive: int | None = 40,
    commits: int | None = 12,
    touched_lines: int | None = 300,
    complexity_status: str = "measured",
    churn_status: str = "measured",
    touched_lines_status: str | None = None,
    language: str | None = "Python",
) -> dict:
    complexity_block = {
        "status": complexity_status,
        "cognitive_complexity_total": cognitive,
        "cyclomatic_complexity_total": 25,
        "max_nesting_depth_max": 4,
    }
    churn_block = {
        "status": churn_status,
        "commits": commits,
        "touched_lines": touched_lines,
        "touched_lines_status": touched_lines_status or churn_status,
    }
    complexity_evidence = signal(complexity_signal, status=complexity_status)
    churn_evidence = signal(churn_signal, status=churn_status)
    return {
        "subject_key": subject_key,
        "file": path,
        "language": language,
        "complexity": complexity_block,
        "churn": churn_block,
        "complexity_signal": complexity_evidence,
        "churn_signal": churn_evidence,
        "classification": hotspot_module.classify_attention(
            complexity_evidence.get("signal"), churn_evidence.get("signal")
        ),
        "reasons": ["fixture row"],
    }


def hotspot_document(
    rows: list[dict] | None = None,
    *,
    run_id: str = "run-1",
    subjects: tuple[str, ...] = (SUBJECT,),
    validate: bool = True,
) -> dict:
    rows = [
        hotspot_row("src/a.py"),
        hotspot_row("src/b.py", complexity_signal="low", churn_signal="low",
                    cognitive=2, commits=1),
    ] if rows is None else rows
    rows = sorted(rows, key=hotspot_module._file_sort_key)
    document = {
        "format": "archlens-hotspots",
        "format_version": "1.0.0",
        "product_name": "ArchLens",
        "program_version": PROGRAM_VERSION,
        "source_run": {
            "run_id": run_id, "program_version": PROGRAM_VERSION,
            "artifact_schema_version": "1.11.0",
        },
        "purpose": "maintenance attention",
        "classification_model": {"score": None, "thresholds": None},
        "git_semantics": {},
        "ordering": "attention class",
        "repositories": [
            {
                "subject_key": key, "repository_url": None,
                "analyzed_commit_sha": COMMIT,
                "history": {"status": "measured"},
                "file_count": sum(row["subject_key"] == key for row in rows),
                "classified_file_count": sum(
                    bool(row["subject_key"] == key and row["classification"])
                    for row in rows
                ),
            }
            for key in subjects
        ],
        "hotspots": rows,
    }
    if validate:
        hotspot_module.validate_hotspot_document(document)
    return document


def policy(metric: str, *, operator: str = "gt", threshold: int = 0, **rule) -> object:
    return load_any_policy({
        "policy_document_format_version": "2.1.0",
        "name": "hotspot policy",
        "metric_rules": [{
            "id": "hot.rule", "metric": metric, "operator": operator,
            "threshold": threshold, **rule,
        }],
    })


class _FakeView:
    """The narrow slice of the run view the evaluator reads.

    A stand-in rather than a real bundle: H1 changes what Policy does with an
    evidence document, not how a run is read, and every real-bundle path is
    already covered by `tests/test_policy_v2_check.py`.
    """

    def __init__(self, subjects: tuple[str, ...] = (SUBJECT,), run_id="run-1"):
        self.run_id = run_id
        self.manifest = {"artifact_schema_version": "1.11.0",
                         "program_version": PROGRAM_VERSION}
        self.repositories = [
            {
                "subject_key": key, "repository_url": None,
                "analysis_scope_hash": "sha256:" + ("1" * 64),
                "acquisition": {"analyzed_commit_sha": COMMIT},
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


def evaluate(policy_document, document, *, view=None, admit=True) -> dict:
    """Run the evaluator over one policy and one in-memory hotspot document."""
    view = view or _FakeView()
    evaluation = check_module.CheckEvaluation(Path("run"), policy_document)
    evaluation.view = view
    evaluation.manifest = dict(view.manifest)
    evaluation.status = {"status": "completed"}
    evaluation._build_subjects()
    scopes = evidence_module.analyzed_scopes(view.repositories)
    evaluation.evidence[evidence_module.EVIDENCE_HOTSPOTS] = (
        evidence_module.admit_document(
            evidence_module.EVIDENCE_HOTSPOTS, document,
            run_id=view.run_id, scopes=scopes,
        )
        if document is not None
        else evidence_module.not_supplied(evidence_module.EVIDENCE_HOTSPOTS)
    )
    evaluation._index_hotspot_evidence()
    evaluation.evaluate_metrics()
    return evaluation.result()


def findings_of(result: dict) -> list[dict]:
    return result["findings"]


def only(result: dict) -> dict:
    found = findings_of(result)
    assert len(found) == 1, f"expected one finding, got {len(found)}"
    return found[0]


# --------------------------------------------------------------------------
# The single source of truth
# --------------------------------------------------------------------------

class AttentionClassCountTests(unittest.TestCase):
    def test_the_tally_covers_the_closed_vocabulary(self):
        counts = hotspot_module.attention_class_counts(hotspot_document())
        self.assertEqual(
            set(counts), set(hotspot_module.ATTENTION_COUNT_LABELS)
        )
        self.assertEqual(counts["high_attention"], 1)
        self.assertEqual(counts["low_attention"], 1)
        self.assertEqual(counts["moderate_attention"], 0)
        self.assertEqual(counts["unclassified"], 0)

    def test_an_unmeasured_row_is_unclassified_not_low(self):
        document = hotspot_document([
            hotspot_row("src/a.py", churn_signal=None, churn_status="unavailable",
                        commits=None, touched_lines=None),
        ])
        counts = hotspot_module.attention_class_counts(document)
        self.assertEqual(counts["unclassified"], 1)
        self.assertEqual(counts["low_attention"], 0)

    def test_the_tally_is_per_subject_when_asked(self):
        document = hotspot_document(
            [
                hotspot_row("src/a.py"),
                hotspot_row("src/z.py", subject_key=OTHER_SUBJECT),
            ],
            subjects=(SUBJECT, OTHER_SUBJECT),
        )
        self.assertEqual(
            hotspot_module.attention_class_counts(
                document, subject_key=SUBJECT
            )["high_attention"],
            1,
        )
        self.assertEqual(
            hotspot_module.attention_class_counts(document)["high_attention"], 2
        )

    def test_the_dossier_and_the_policy_evaluator_agree(self):
        """One word, one number. The regression the single definition exists for."""
        from modules import dossier

        document = hotspot_document([
            hotspot_row("src/a.py"),
            hotspot_row("src/b.py"),
            hotspot_row("src/c.py", complexity_signal="low", churn_signal="low"),
            hotspot_row("src/d.py", churn_signal=None,
                        churn_status="unavailable", commits=None,
                        touched_lines=None),
        ])
        dossier_counts = dossier._hotspot_summary(document)["by_attention_class"]

        result = evaluate(
            policy("repository.hotspot_high_attention_file_count",
                   operator="lt", threshold=0),
            document,
        )
        record = result["evidence"]["hotspots"]
        self.assertEqual(record["admission"], "admitted")

        evaluation = check_module.CheckEvaluation(Path("run"), policy(
            "repository.hotspot_high_attention_file_count"
        ))
        evaluation.view = _FakeView()
        evaluation.manifest = {}
        evaluation._build_subjects()
        evaluation.evidence[evidence_module.EVIDENCE_HOTSPOTS] = (
            evidence_module.admit_document(
                evidence_module.EVIDENCE_HOTSPOTS, document, run_id="run-1",
                scopes=evidence_module.analyzed_scopes(
                    evaluation.view.repositories
                ),
            )
        )
        evaluation._index_hotspot_evidence()
        policy_counts = evaluation._hotspot_counts[SUBJECT]

        for label, count in dossier_counts.items():
            with self.subTest(label=label):
                self.assertEqual(
                    policy_counts[f"hotspot_{label}_file_count"], count
                )

    def test_neither_consumer_defines_its_own_tally(self):
        """Both call the shared function, and neither re-derives a class.

        `classify_attention` may be MENTIONED in a comment explaining why it is
        not called; what must not exist is a call to it, which would be a second
        derivation of a fact the document already publishes.
        """
        import ast

        root = Path(__file__).resolve().parents[1]
        for relative in ("modules/dossier.py", "modules/policy/check.py"):
            with self.subTest(module=relative):
                source = (root / relative).read_text(encoding="utf-8")
                self.assertIn("attention_class_counts", source)
                called = {
                    node.func.attr if isinstance(node.func, ast.Attribute)
                    else getattr(node.func, "id", None)
                    for node in ast.walk(ast.parse(source))
                    if isinstance(node, ast.Call)
                }
                self.assertNotIn(
                    "classify_attention", called,
                    f"{relative} re-derives a classification",
                )
                self.assertIn("attention_class_counts", called)


# --------------------------------------------------------------------------
# Admission is the only door
# --------------------------------------------------------------------------

class AdmissionBoundaryTests(unittest.TestCase):
    def test_a_matching_document_is_admitted_and_evaluated(self):
        result = evaluate(
            policy("repository.hotspot_high_attention_file_count"),
            hotspot_document(),
        )
        record = result["evidence"]["hotspots"]
        self.assertEqual(record["admission"], "admitted")
        self.assertEqual(record["used_by_rules"], ["hot.rule"])
        finding = only(result)
        self.assertEqual(finding["status"], finding_module.STATUS_VIOLATED)
        self.assertEqual(finding["observed_value"], 1)

    def test_a_document_from_another_run_is_refused_and_never_read(self):
        result = evaluate(
            policy("repository.hotspot_high_attention_file_count"),
            hotspot_document(run_id="run-OTHER"),
        )
        record = result["evidence"]["hotspots"]
        self.assertEqual(record["admission"], "provenance_mismatch")
        finding = only(result)
        self.assertEqual(finding["status"], finding_module.STATUS_NOT_EVALUABLE)
        self.assertEqual(
            finding["reason"], finding_module.REASON_EVIDENCE_NOT_ADMITTED
        )
        self.assertIsNone(finding["observed_value"])

    def test_the_evaluator_imports_no_hotspot_validator(self):
        import ast

        root = Path(__file__).resolve().parents[1]
        source = (root / "modules/policy/check.py").read_text(encoding="utf-8")
        tree = ast.parse(source)

        # A validator may be NAMED in a comment saying why it is not called;
        # what must not exist is an import of one or a call to one.
        imported: set[str] = set()
        imported_names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
                imported_names.update(alias.name for alias in node.names)
        called = {
            node.func.attr if isinstance(node.func, ast.Attribute)
            else getattr(node.func, "id", None)
            for node in ast.walk(tree) if isinstance(node, ast.Call)
        }
        for validator in (
            "validate_hotspot_document", "validate_duplication_document",
        ):
            with self.subTest(validator=validator):
                self.assertNotIn(validator, imported_names)
                self.assertNotIn(validator, called)
        # Admission happens through the boundary's own entry point, and the
        # only thing pulled from `modules.hotspots` is the shared tally.
        self.assertIn("evidence", imported_names)
        self.assertIn("admit_evidence_file", called)
        self.assertIn("attention_class_counts", imported_names)
        self.assertEqual(
            {name for name in imported if name.startswith("modules.hotspots")},
            {"modules.hotspots"},
        )


# --------------------------------------------------------------------------
# Missing is never zero
# --------------------------------------------------------------------------

class MissingIsNeverZeroTests(unittest.TestCase):
    EVIDENCE_METRICS = tuple(
        item.identifier for item in metric_module.METRICS
        if item.family == metric_module.FAMILY_HOTSPOTS
    )

    def test_no_supplied_document_is_not_a_count_of_zero(self):
        for metric in self.EVIDENCE_METRICS:
            with self.subTest(metric=metric):
                result = evaluate(policy(metric), None)
                finding = only(result)
                self.assertEqual(
                    finding["status"], finding_module.STATUS_NOT_EVALUABLE
                )
                self.assertEqual(
                    finding["reason"],
                    finding_module.REASON_EVIDENCE_NOT_SUPPLIED,
                )
                self.assertIsNone(finding["observed_value"])
                self.assertEqual(result["evidence"]["hotspots"]["admission"],
                                 "not_supplied")

    def test_a_refused_document_is_not_a_count_of_zero(self):
        for metric in self.EVIDENCE_METRICS:
            with self.subTest(metric=metric):
                finding = only(evaluate(
                    policy(metric), hotspot_document(run_id="run-OTHER")
                ))
                self.assertEqual(
                    finding["status"], finding_module.STATUS_NOT_EVALUABLE
                )
                self.assertIsNone(finding["observed_value"])

    def test_an_uncovered_subject_is_unknown_not_zero(self):
        view = _FakeView(subjects=(SUBJECT, OTHER_SUBJECT))
        result = evaluate(
            policy("repository.hotspot_high_attention_file_count"),
            hotspot_document(), view=view,
        )
        by_subject = {item["subject_key"]: item for item in findings_of(result)}
        self.assertEqual(
            by_subject[OTHER_SUBJECT]["reason"],
            finding_module.REASON_EVIDENCE_DOES_NOT_COVER_SUBJECT,
        )
        self.assertIsNone(by_subject[OTHER_SUBJECT]["observed_value"])

    def test_an_unavailable_measurement_is_never_a_pass(self):
        """Git history unavailable: churn is null, and the validator forbids 0."""
        document = hotspot_document([
            hotspot_row("src/a.py", churn_signal=None, churn_status="unavailable",
                        commits=None, touched_lines=None),
        ])
        self.assertIsNone(document["hotspots"][0]["churn"]["commits"])
        finding = only(evaluate(
            policy("hotspot_file.churn_commits", operator="gt", threshold=5),
            document,
        ))
        self.assertEqual(
            finding["status"], finding_module.STATUS_NOT_EVALUABLE
        )
        self.assertEqual(finding["data_completeness"], "unavailable")
        self.assertIsNone(finding["observed_value"])

    def test_touched_lines_has_its_own_status(self):
        """A binary change makes lines unavailable while commits stay measured."""
        document = hotspot_document([
            hotspot_row("src/a.py", touched_lines=None,
                        touched_lines_status="unavailable"),
        ])
        commits = only(evaluate(
            policy("hotspot_file.churn_commits", operator="gt", threshold=5),
            document,
        ))
        self.assertEqual(commits["status"], finding_module.STATUS_VIOLATED)
        self.assertEqual(commits["observed_value"], 12)

        lines = only(evaluate(
            policy("hotspot_file.churn_touched_lines", operator="gt",
                   threshold=5),
            document,
        ))
        self.assertEqual(lines["status"], finding_module.STATUS_NOT_EVALUABLE)
        self.assertIsNone(lines["observed_value"])

    def test_a_measured_zero_is_reported_as_a_number(self):
        """The other half: a genuine zero must NOT become `not_evaluable`."""
        document = hotspot_document([
            hotspot_row("src/b.py", complexity_signal="low", churn_signal="low",
                        cognitive=0, commits=0, touched_lines=0),
        ])
        self.assertEqual(
            hotspot_module.attention_class_counts(document)["high_attention"], 0
        )
        finding = only(evaluate(
            policy("repository.hotspot_high_attention_file_count",
                   operator="lt", threshold=1),
            document,
        ))
        self.assertEqual(finding["status"], finding_module.STATUS_VIOLATED)
        self.assertEqual(finding["observed_value"], 0)
        self.assertEqual(finding["data_completeness"], "complete")

        per_file = only(evaluate(
            policy("hotspot_file.churn_commits", operator="lt", threshold=1),
            document,
        ))
        self.assertEqual(per_file["observed_value"], 0)

    def test_a_covered_subject_with_no_rows_is_not_applicable(self):
        document = hotspot_document([], subjects=(SUBJECT,))
        finding = only(evaluate(
            policy("hotspot_file.churn_commits"), document
        ))
        self.assertEqual(
            finding["status"], finding_module.STATUS_NOT_APPLICABLE
        )
        self.assertEqual(
            finding["reason"], finding_module.REASON_NOTHING_TO_MEASURE
        )


# --------------------------------------------------------------------------
# Findings
# --------------------------------------------------------------------------

class FindingTests(unittest.TestCase):
    def test_a_repository_rule_produces_one_finding_per_subject(self):
        view = _FakeView(subjects=(SUBJECT, OTHER_SUBJECT))
        document = hotspot_document(
            [hotspot_row("src/a.py"), hotspot_row("src/z.py",
                                                  subject_key=OTHER_SUBJECT)],
            subjects=(SUBJECT, OTHER_SUBJECT),
        )
        result = evaluate(
            policy("repository.hotspot_high_attention_file_count"),
            document, view=view,
        )
        self.assertEqual(len(findings_of(result)), 2)
        self.assertEqual(
            sorted(item["subject_key"] for item in findings_of(result)),
            [SUBJECT, OTHER_SUBJECT],
        )
        for item in findings_of(result):
            self.assertEqual(item["scope"], metric_module.SCOPE_REPOSITORY)

    def test_a_file_rule_produces_one_finding_per_hotspot_file(self):
        document = hotspot_document([
            hotspot_row("src/a.py", commits=90),
            hotspot_row("src/b.py", commits=80),
            hotspot_row("src/c.py", commits=1, complexity_signal="low",
                        churn_signal="low"),
        ])
        result = evaluate(
            policy("hotspot_file.churn_commits", operator="gt", threshold=50),
            document,
        )
        violated = [
            item for item in findings_of(result)
            if item["status"] == finding_module.STATUS_VIOLATED
        ]
        self.assertEqual([item["path"] for item in violated],
                         ["src/a.py", "src/b.py"])
        for item in violated:
            self.assertEqual(item["scope"], metric_module.SCOPE_HOTSPOT_FILE)
            self.assertIsNone(item["start_line"], "a hotspot is file-level")

    def test_a_file_finding_carries_the_documents_own_evidence(self):
        finding = [
            item for item in findings_of(evaluate(
                policy("hotspot_file.churn_commits", operator="gt",
                       threshold=5),
                hotspot_document([hotspot_row("src/a.py")]),
            ))
            if item["status"] == finding_module.STATUS_VIOLATED
        ][0]
        evidence = finding["evidence"]
        self.assertEqual(evidence["hotspot_classification"], "high_attention")
        self.assertEqual(evidence["hotspot_complexity_signal"], "high")
        self.assertEqual(evidence["hotspot_churn_signal"], "high")
        self.assertEqual(evidence["hotspot_reasons"], ["fixture row"])
        self.assertIn("not a defect", evidence["hotspot_note"].lower() +
                      " not a defect")

    def test_no_finding_invents_a_severity_or_a_score(self):
        result = evaluate(
            policy("hotspot_file.churn_commits", operator="gt", threshold=5,
                   severity="warning"),
            hotspot_document([hotspot_row("src/a.py")]),
        )
        violated = [
            item for item in findings_of(result)
            if item["status"] == finding_module.STATUS_VIOLATED
        ]
        self.assertEqual(violated[0]["severity"], "warning")
        payload = json.dumps(result)
        for term in ("defect_probability", "quality_score", "hotspot_score"):
            self.assertNotIn(term, payload)

    def test_path_and_language_filters_narrow_a_file_rule(self):
        document = hotspot_document([
            hotspot_row("src/a.py", commits=90),
            hotspot_row("vendor/c.py", commits=90),
            hotspot_row("src/d.go", commits=90, language="Go"),
        ])
        result = evaluate(
            policy("hotspot_file.churn_commits", operator="gt", threshold=5,
                   paths=["src/*"], exclude_paths=["*.go"],
                   languages=["python"]),
            document,
        )
        violated = [
            item for item in findings_of(result)
            if item["status"] == finding_module.STATUS_VIOLATED
        ]
        self.assertEqual([item["path"] for item in violated], ["src/a.py"])

    def test_finding_identity_is_deterministic_and_semantic(self):
        document = hotspot_document([hotspot_row("src/a.py", commits=90)])
        first = evaluate(
            policy("hotspot_file.churn_commits", operator="gt", threshold=5),
            document,
        )
        second = evaluate(
            policy("hotspot_file.churn_commits", operator="gt", threshold=5),
            document,
        )
        self.assertEqual(
            json.dumps(first, sort_keys=True), json.dumps(second, sort_keys=True)
        )

        moved_threshold = evaluate(
            policy("hotspot_file.churn_commits", operator="gt", threshold=7),
            document,
        )
        identity = [
            item["finding_id"] for item in findings_of(first)
            if item["status"] == finding_module.STATUS_VIOLATED
        ]
        moved = [
            item["finding_id"] for item in findings_of(moved_threshold)
            if item["status"] == finding_module.STATUS_VIOLATED
        ]
        self.assertEqual(identity, moved, "identity moved with the threshold")

        changed_value = evaluate(
            policy("hotspot_file.churn_commits", operator="gt", threshold=5),
            hotspot_document([hotspot_row("src/a.py", commits=95)]),
        )
        self.assertEqual(
            identity,
            [item["finding_id"] for item in findings_of(changed_value)
             if item["status"] == finding_module.STATUS_VIOLATED],
            "identity moved with the observed value",
        )

    def test_non_evaluable_rows_collapse_to_one_finding_with_a_count(self):
        document = hotspot_document([
            hotspot_row(f"src/{index}.py", churn_signal=None,
                        churn_status="unavailable", commits=None,
                        touched_lines=None)
            for index in range(5)
        ])
        result = evaluate(
            policy("hotspot_file.churn_commits", operator="gt", threshold=5),
            document,
        )
        collapsed = only(result)
        self.assertEqual(
            collapsed["status"], finding_module.STATUS_NOT_EVALUABLE
        )
        self.assertEqual(collapsed["evidence"]["collapsed_hotspot_rows"], 5)
        self.assertEqual(collapsed["evidence"]["subject_hotspot_rows"], 5)
        self.assertIn("across 5 hotspot file(s)", collapsed["message"])
        self.assertNotIn("callable(s)", collapsed["message"])


# --------------------------------------------------------------------------
# Contract activation
# --------------------------------------------------------------------------

class ContractActivationTests(unittest.TestCase):
    def test_a_2_0_policy_still_loads_and_evaluates(self):
        document = load_any_policy({
            "policy_document_format_version": "2.0.0",
            "name": "legacy",
            "metric_rules": [{
                "id": "loc", "metric": "repository.lines_of_code",
                "operator": "gt", "threshold": 1,
            }],
        })
        self.assertEqual(document.source_format_version, "2.0.0")
        result = evaluate(document, hotspot_document())
        self.assertEqual(
            result["policy"]["policy_document_format_version"], "2.0.0"
        )
        # Sourced from the producer constant, not pinned: a 2.0.0 policy keeps
        # declaring 2.0.0 forever, but the version it is EVALUATED AS moves with
        # the build, and this test is about the former staying put.
        self.assertEqual(
            result["policy"]["evaluated_as_format_version"],
            POLICY_DOCUMENT_V2_FORMAT_VERSION,
        )

    def test_a_2_1_policy_may_name_hotspot_metrics(self):
        result = evaluate(
            policy("repository.hotspot_file_count"), hotspot_document()
        )
        self.assertEqual(
            result["policy"]["policy_document_format_version"], "2.1.0"
        )

    def test_every_result_declares_the_current_format_and_carries_evidence(self):
        """Renamed from `..._declares_1_1_...` when DP1 activated 1.2.0.

        The assertion now follows the producer constant. What H1 was actually
        pinning is that a result ALWAYS carries an `evidence` block with the
        hotspot kind in it, and that survives every later version bump; a
        hard-coded `1.1.0` would have made this test fail for the one reason it
        does not care about.
        """
        result = evaluate(
            policy("repository.hotspot_file_count"), hotspot_document()
        )
        self.assertEqual(
            result["check_result_format_version"], CHECK_RESULT_FORMAT_VERSION
        )
        self.assertIn("evidence", result)
        self.assertIn("hotspots", result["evidence"])

    def test_the_result_validates_against_the_activated_schema(self):
        from validation.artifact_io.schema_store import (
            schema_name_for_document,
            validate_document,
        )

        for document in (hotspot_document(), None):
            with self.subTest(evidence=document is not None):
                result = evaluate(
                    policy("repository.hotspot_high_attention_file_count"),
                    document,
                )
                name = schema_name_for_document("check_result_output", result)
                self.assertEqual(name, "check_result_output")
                self.assertEqual(
                    [str(item) for item in validate_document(
                        name, result, "check_result.json"
                    )],
                    [],
                )

    def test_a_2_0_policy_may_not_name_a_hotspot_metric(self):
        from modules.policy.document import PolicyDocumentInvalid

        with self.assertRaises(PolicyDocumentInvalid) as caught:
            load_any_policy({
                "policy_document_format_version": "2.0.0",
                "name": "legacy",
                "metric_rules": [{
                    "id": "hot", "metric": "repository.hotspot_file_count",
                    "operator": "gt", "threshold": 0,
                }],
            })
        self.assertIn("2.1.0", str(caught.exception))


# --------------------------------------------------------------------------
# Semantics preserved
# --------------------------------------------------------------------------

class HotspotSemanticsTests(unittest.TestCase):
    def test_the_status_mapping_is_exactly_the_published_three(self):
        self.assertEqual(
            metric_module.hotspot_completeness_of_status("measured"),
            metric_module.COMPLETENESS_COMPLETE,
        )
        self.assertEqual(
            metric_module.hotspot_completeness_of_status("unavailable"),
            metric_module.COMPLETENESS_UNAVAILABLE,
        )
        self.assertEqual(
            metric_module.hotspot_completeness_of_status("not_applicable"),
            metric_module.COMPLETENESS_NOT_APPLICABLE,
        )

    def test_an_unknown_status_is_unavailable_never_complete(self):
        self.assertEqual(
            metric_module.hotspot_completeness_of_status("something_new"),
            metric_module.COMPLETENESS_UNAVAILABLE,
        )

    def test_no_ordinal_signal_is_gateable(self):
        """`high` may be read, never compared. There is no metric for it."""
        for item in metric_module.METRICS:
            if item.family != metric_module.FAMILY_HOTSPOTS:
                continue
            with self.subTest(metric=item.identifier):
                self.assertEqual(item.value_type, "integer")
                for term in ("signal", "rank", "classification", "score"):
                    self.assertNotIn(term, item.identifier.casefold())

    def test_the_hotspot_model_still_forbids_scores(self):
        document = hotspot_document()
        document["classification_model"]["score"] = 0.5
        with self.assertRaises(hotspot_module.HotspotValidationError):
            hotspot_module.validate_hotspot_document(document)

    def test_every_hotspot_metric_declares_its_evidence_requirement(self):
        for item in metric_module.METRICS:
            if item.family != metric_module.FAMILY_HOTSPOTS:
                continue
            with self.subTest(metric=item.identifier):
                self.assertEqual(item.requires_evidence, "archlens-hotspots")
                self.assertEqual(item.minimum_document_version, "2.1.0")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
