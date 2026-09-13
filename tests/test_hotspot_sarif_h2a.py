"""ArchLens 4.0 Hotspot SARIF projection, H2-A.

A `hotspot_file` finding becomes a SARIF result with a repository-relative
location, no invented line region, and the evidence the EVALUATOR already
attached. The projector learns nothing about hotspots to do it: the same
uniform path carries an integrity rule's evidence and a collapsed row count.

The architecture under test is the boundary itself -- SARIF projects, it never
evaluates. Every assertion below runs over a check result that
`modules.policy.check` produced.
"""

from __future__ import annotations

import ast
import json
import re
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from modules.config import PROGRAM_VERSION
from modules.policy import findings as finding_module
from modules.policy import sarif as sarif_module

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_hotspot_policy_core_h1 import (  # noqa: E402
    evaluate,
    hotspot_document,
    hotspot_row,
    policy,
)

ROOT = Path(__file__).resolve().parents[1]


def hotspot_result(rows=None, *, metric="hotspot_file.churn_commits",
                   operator="gt", threshold=5, **rule):
    """One real check result carrying at least one hotspot finding."""
    return evaluate(
        policy(metric, operator=operator, threshold=threshold, **rule),
        hotspot_document(rows if rows is not None else [
            hotspot_row("src/a.py", commits=90),
            hotspot_row("src/b.py", commits=80, complexity_signal="low",
                        churn_signal="high"),
        ]),
    )


def results_of(document) -> list[dict]:
    return document["runs"][0]["results"]


def archlens(result) -> dict:
    return result["properties"]["archlens"]


# --------------------------------------------------------------------------
# The projection
# --------------------------------------------------------------------------

class HotspotResultProjectionTests(unittest.TestCase):
    def test_a_hotspot_finding_becomes_a_sarif_result(self):
        document = sarif_module.project_sarif(hotspot_result())
        results = results_of(document)
        self.assertEqual(len(results), 2)
        for item in results:
            with self.subTest(rule=item["ruleId"]):
                self.assertEqual(item["ruleId"], "hot.rule")
                self.assertEqual(item["level"], "error")
                self.assertTrue(item["message"]["text"])
                self.assertEqual(
                    archlens(item)["scope"], "hotspot_file"
                )

    def test_the_location_is_the_repository_relative_path(self):
        document = sarif_module.project_sarif(hotspot_result())
        uris = [
            item["locations"][0]["physicalLocation"]["artifactLocation"]["uri"]
            for item in results_of(document)
        ]
        self.assertEqual(sorted(uris), ["src/a.py", "src/b.py"])

    def test_no_line_region_is_invented(self):
        """A hotspot is a file-level statement; a region would imply otherwise."""
        for item in results_of(sarif_module.project_sarif(hotspot_result())):
            with self.subTest(uri=item["locations"][0]["physicalLocation"]):
                physical = item["locations"][0]["physicalLocation"]
                self.assertNotIn("region", physical)
                self.assertEqual(set(physical), {"artifactLocation"})

    def test_the_canonical_identity_is_the_partial_fingerprint(self):
        result = hotspot_result()
        document = sarif_module.project_sarif(result)
        projected = {
            item["partialFingerprints"]["archlensFindingId/v1"]
            for item in results_of(document)
        }
        canonical = {
            item["finding_id"] for item in result["findings"]
            if item["status"] == finding_module.STATUS_VIOLATED
        }
        self.assertEqual(projected, canonical)

    def test_a_non_evaluable_hotspot_rule_is_a_notification_not_a_result(self):
        """The rule did not run. It must never appear as a numeric result."""
        result = evaluate(
            policy("hotspot_file.churn_commits", operator="gt", threshold=5),
            None,
        )
        document = sarif_module.project_sarif(result)
        self.assertEqual(results_of(document), [])
        notifications = (
            document["runs"][0]["invocations"][0]["toolExecutionNotifications"]
        )
        self.assertEqual(len(notifications), 1)
        properties = notifications[0]["properties"]["archlens"]
        self.assertEqual(properties["status"], "not_evaluable")
        self.assertIsNone(properties["observedValue"])
        self.assertEqual(
            properties["reason"], finding_module.REASON_EVIDENCE_NOT_SUPPLIED
        )


# --------------------------------------------------------------------------
# Evidence
# --------------------------------------------------------------------------

class EvidenceProjectionTests(unittest.TestCase):
    def test_the_documents_own_evidence_is_preserved(self):
        result = hotspot_result(rows=[hotspot_row("src/a.py", commits=90)])
        canonical = next(
            item for item in result["findings"]
            if item["status"] == finding_module.STATUS_VIOLATED
        )["evidence"]
        projected = archlens(
            results_of(sarif_module.project_sarif(result))[0]
        )["evidence"]
        self.assertEqual(projected, canonical)

    def test_classification_and_both_signals_survive(self):
        result = hotspot_result(rows=[
            hotspot_row("src/a.py", commits=90, complexity_signal="high",
                        churn_signal="high"),
        ])
        evidence = archlens(
            results_of(sarif_module.project_sarif(result))[0]
        )["evidence"]
        self.assertEqual(evidence["hotspot_classification"], "high_attention")
        self.assertEqual(evidence["hotspot_complexity_signal"], "high")
        self.assertEqual(evidence["hotspot_churn_signal"], "high")
        self.assertEqual(evidence["hotspot_reasons"], ["fixture row"])
        note = evidence["hotspot_note"].lower()
        self.assertIn("predicts no defect", note)
        self.assertIn("produces no score", note)

    def test_the_source_evidence_provenance_reaches_the_run(self):
        document = sarif_module.project_sarif(hotspot_result())
        evidence = document["runs"][0]["properties"]["archlens"]["evidence"]
        self.assertEqual(evidence["hotspots"]["admission"], "admitted")
        self.assertTrue(evidence["hotspots"]["supplied"])
        self.assertEqual(evidence["hotspots"]["used_by_rules"], ["hot.rule"])
        self.assertEqual(evidence["hotspots"]["binding"]["state"], "bound")
        self.assertEqual(
            evidence["hotspots"]["document_format"], "archlens-hotspots"
        )

    def test_unsupplied_evidence_is_visible_in_the_run(self):
        """A gate never given its evidence must not look like one that was."""
        document = sarif_module.project_sarif(evaluate(
            policy("hotspot_file.churn_commits", operator="gt", threshold=5),
            None,
        ))
        evidence = document["runs"][0]["properties"]["archlens"]["evidence"]
        self.assertEqual(evidence["hotspots"]["admission"], "not_supplied")
        self.assertFalse(evidence["hotspots"]["supplied"])

    def test_a_refused_document_is_visible_in_the_run(self):
        document = sarif_module.project_sarif(evaluate(
            policy("hotspot_file.churn_commits", operator="gt", threshold=5),
            hotspot_document(run_id="run-OTHER"),
        ))
        evidence = document["runs"][0]["properties"]["archlens"]["evidence"]
        self.assertEqual(evidence["hotspots"]["admission"], "provenance_mismatch")

    def test_the_run_scope_listing_is_not_copied_into_sarif(self):
        """A subset by design: SARIF is not the place to re-audit an admission."""
        document = sarif_module.project_sarif(hotspot_result())
        evidence = document["runs"][0]["properties"]["archlens"]["evidence"]
        self.assertNotIn("provenance_evidence", evidence["hotspots"])
        self.assertNotIn("run_analyzed_scopes", json.dumps(document))

    def test_integrity_evidence_projects_through_the_same_path(self):
        """No hotspot-specific branch: one uniform projection for every family."""
        finding = {
            "finding_id": "alf1:" + ("0" * 32),
            "rule_id": "run.integrity_failed", "kind": "integrity",
            "severity": "violation", "status": "violated", "scope": "run",
            "subject_key": "s", "message": "m", "metric": None,
            "operator": None, "threshold": None, "observed_value": None,
            "language": None, "path": None, "callable_row_id": None,
            "callable_qualified_name": None, "data_completeness": "complete",
            "value_status": None, "value_status_field": None, "reason": None,
            "evidence": {"error_category": "parser", "count": 3},
            "provenance": {"run_id": "run-1"},
        }
        document = sarif_module.project_sarif({
            "check_result_format_version": "1.1.0",
            "archlens_version": PROGRAM_VERSION, "verdict": "fail",
            "exit_code": 1, "failure_kind": None,
            "policy": {}, "run": {}, "counts": {},
            "rules": [{"rule_id": "run.integrity_failed", "kind": "integrity",
                       "severity": "violation", "scope": "run"}],
            "findings": [finding], "waived_findings": [], "provenance": {},
        })
        self.assertEqual(
            archlens(results_of(document)[0])["evidence"],
            {"count": 3, "error_category": "parser"},
        )


# --------------------------------------------------------------------------
# Portability
# --------------------------------------------------------------------------

class PortabilityTests(unittest.TestCase):
    NON_PORTABLE = (
        r"C:\\private\\checkout\\src\\a.py",
        "/home/runner/work/repo/src/a.py",
        "file:///C:/repo/src/a.py",
        "../outside/a.py",
        "/absolute/a.py",
    )

    def _result_with_path(self, path: str) -> dict:
        """A real check result whose hotspot finding carries an unusable path.

        The path is substituted AFTER evaluation. The producer's own validator
        refuses such a path, so this is the shape a corrupted or hand-edited
        document would take -- exactly the case the projector must survive.
        """
        result = hotspot_result(rows=[hotspot_row("src/a.py", commits=90)])
        for item in result["findings"]:
            if item["status"] == finding_module.STATUS_VIOLATED:
                item["path"] = path
        return result

    def test_a_non_portable_path_omits_the_location_and_says_so(self):
        for path in self.NON_PORTABLE:
            with self.subTest(path=path):
                document = sarif_module.project_sarif(self._result_with_path(path))
                result = results_of(document)[0]
                self.assertNotIn("locations", result)
                self.assertEqual(
                    archlens(result)["locationOmitted"],
                    "path_is_not_repository_relative",
                )

    def test_a_non_portable_path_never_drops_the_finding(self):
        """Losing a location is a loss; losing the finding would hide a problem."""
        for path in self.NON_PORTABLE:
            with self.subTest(path=path):
                document = sarif_module.project_sarif(self._result_with_path(path))
                self.assertEqual(len(results_of(document)), 1)
                self.assertTrue(results_of(document)[0]["message"]["text"])

    def test_a_non_portable_path_never_leaks_into_the_document(self):
        for path in self.NON_PORTABLE:
            with self.subTest(path=path):
                payload = sarif_module.serialize_sarif(
                    sarif_module.project_sarif(self._result_with_path(path))
                )
                self.assertNotIn(path.replace("\\", "\\\\"), payload)
                self.assertNotIn("private", payload)
                self.assertNotIn("/home/runner", payload)

    def test_an_unlocated_finding_records_no_omission(self):
        """A repository-scope finding names no path; that is not an omission."""
        document = sarif_module.project_sarif(hotspot_result(
            metric="repository.hotspot_high_attention_file_count",
            operator="gt", threshold=0,
        ))
        for item in results_of(document):
            with self.subTest(rule=item["ruleId"]):
                self.assertNotIn("locations", item)
                self.assertNotIn("locationOmitted", archlens(item))

    def test_the_projection_carries_no_machine_path_anywhere(self):
        """Judged by the projector's own detector, not by a second regex.

        A hand-rolled pattern here would either drift from the boundary the
        product actually enforces or, as a looser one did, flag the `https://`
        inside the SARIF schema URI.
        """
        payload = sarif_module.serialize_sarif(
            sarif_module.project_sarif(hotspot_result())
        )
        self.assertFalse(sarif_module._contains_machine_path(payload))

    def test_the_subset_validator_accepts_the_hotspot_projection(self):
        document = sarif_module.project_sarif(hotspot_result())
        self.assertEqual(sarif_module.validate_sarif_subset(document), [])


# --------------------------------------------------------------------------
# Determinism and the forbidden shapes
# --------------------------------------------------------------------------

class DeterminismTests(unittest.TestCase):
    def test_repeated_projection_is_byte_identical(self):
        result = hotspot_result()
        first = sarif_module.serialize_sarif(sarif_module.project_sarif(result))
        second = sarif_module.serialize_sarif(sarif_module.project_sarif(result))
        self.assertEqual(first, second)

    def test_reversing_the_finding_order_keeps_exact_bytes(self):
        result = hotspot_result()
        reversed_result = dict(result)
        reversed_result["findings"] = list(reversed(result["findings"]))
        self.assertEqual(
            sarif_module.serialize_sarif(sarif_module.project_sarif(result)),
            sarif_module.serialize_sarif(
                sarif_module.project_sarif(reversed_result)
            ),
        )

    def test_no_timestamp_or_baseline_state_is_emitted(self):
        payload = sarif_module.serialize_sarif(
            sarif_module.project_sarif(hotspot_result())
        )
        self.assertNotIn("baselineState", payload)
        self.assertIsNone(re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:", payload))

    def test_no_score_rank_or_defect_field_appears(self):
        """The vocabulary the Hotspot model refuses, refused again downstream."""
        payload = sarif_module.serialize_sarif(
            sarif_module.project_sarif(hotspot_result())
        ).casefold()
        for term in (
            '"score"', '"rank"', '"ranking"', '"defect', '"bug_probability"',
            '"risk', '"quality_score"', '"severity_score"', '"hotspotscore"',
        ):
            with self.subTest(term=term):
                self.assertNotIn(term, payload)

    def test_the_ordinal_signals_are_evidence_and_never_a_level(self):
        """`high` may be read from the evidence; it must not become a severity."""
        document = sarif_module.project_sarif(hotspot_result())
        for item in results_of(document):
            with self.subTest(rule=item["ruleId"]):
                self.assertEqual(item["level"], "error")
                self.assertNotEqual(
                    item["level"],
                    archlens(item)["evidence"]["hotspot_complexity_signal"],
                )

    def test_severity_still_comes_only_from_the_rule(self):
        for severity, level in (
            ("violation", "error"), ("warning", "warning"), ("info", "note"),
        ):
            with self.subTest(severity=severity):
                document = sarif_module.project_sarif(hotspot_result(
                    severity=severity
                ))
                for item in results_of(document):
                    self.assertEqual(item["level"], level)


# --------------------------------------------------------------------------
# The boundary: SARIF projects, it never evaluates
# --------------------------------------------------------------------------

class ProjectionBoundaryTests(unittest.TestCase):
    def test_the_projector_never_invokes_the_comparator(self):
        # The result is built OUTSIDE the patch: evaluation legitimately
        # compares, and only the projection must not.
        result = hotspot_result()
        with patch.object(
            finding_module, "compare",
            side_effect=AssertionError("second evaluator"),
        ) as comparator:
            sarif_module.project_sarif(result)
        comparator.assert_not_called()

    def test_the_projector_reads_no_hotspot_module_and_no_evaluator(self):
        """Checked as imports and calls, not as text.

        The module docstring names `modules.policy.check` to say the projector
        is downstream of it; a substring search would read that sentence as a
        dependency and forbid explaining the architecture.
        """
        tree = ast.parse(
            (ROOT / "modules/policy/sarif.py").read_text(encoding="utf-8")
        )
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
        called = {
            node.func.attr if isinstance(node.func, ast.Attribute)
            else getattr(node.func, "id", None)
            for node in ast.walk(tree) if isinstance(node, ast.Call)
        }
        for forbidden in (
            "modules.hotspots", "modules.policy.metrics", "modules.policy.check",
            "modules.policy.evidence",
        ):
            with self.subTest(imported=forbidden):
                self.assertNotIn(forbidden, imported)
        for forbidden in (
            "open_run", "evaluate_check", "attention_class_counts",
            "classify_attention", "read_hotspot_file",
        ):
            with self.subTest(called=forbidden):
                self.assertNotIn(forbidden, called)

    def test_the_projector_has_no_hotspot_specific_branch(self):
        """One uniform path. A family name in a conditional is a second generator."""
        tree = ast.parse((ROOT / "modules/policy/sarif.py").read_text(
            encoding="utf-8"
        ))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.If, ast.IfExp)):
                continue
            rendered = ast.dump(node.test)
            for term in ("hotspot", "hotspots"):
                with self.subTest(term=term):
                    self.assertNotIn(
                        term, rendered.casefold(),
                        "the projector branches on a hotspot concept",
                    )


# --------------------------------------------------------------------------
# Old behavior, and both result contracts
# --------------------------------------------------------------------------

class BackwardCompatibilityTests(unittest.TestCase):
    def test_a_1_0_result_still_projects_without_an_evidence_block(self):
        """A 1.0.0 result has no evidence concept; nothing is invented for it."""
        from modules.policy.check import failure_result

        result = failure_result(
            kind="usage", message="usage error",
            run_directory=Path("run"), policy_name="policy",
        )
        result["check_result_format_version"] = "1.0.0"
        result.pop("evidence")
        result.pop("ratchet", None)
        result.pop("evaluated_input_provenance", None)
        result["run"]["run_directory"] = "run"
        document = sarif_module.project_sarif(result)
        self.assertNotIn(
            "evidence", document["runs"][0]["properties"]["archlens"]
        )
        self.assertEqual(sarif_module.validate_sarif_subset(document), [])

    def test_a_current_result_validates_against_its_schema_and_projects(self):
        """Renamed from `..._a_1_1_result_...` when DP1 activated 1.2.0.

        The version this asserted was hard-coded, so the test failed on the next
        activation for the one reason it does not care about. What it is
        actually pinning is that whatever the producer emits validates against
        the schema selected for it and projects to a valid SARIF subset.
        """
        from modules.policy.check import CHECK_RESULT_FORMAT_VERSION
        from validation.artifact_io.schema_store import (
            schema_name_for_document,
            validate_document,
        )

        result = hotspot_result()
        self.assertEqual(
            result["check_result_format_version"], CHECK_RESULT_FORMAT_VERSION
        )
        name = schema_name_for_document("check_result_output", result)
        self.assertEqual(
            [str(item) for item in validate_document(
                name, result, "check_result.json"
            )],
            [],
        )
        self.assertEqual(
            sarif_module.validate_sarif_subset(
                sarif_module.project_sarif(result)
            ),
            [],
        )

    def test_a_1_1_result_still_validates_and_projects(self):
        """The backward-compatibility half, which the pinned version conflated.

        A 1.1.0 result is still a published document. It selects its OWN schema
        rather than the newest, and it still projects. The two coupled version
        fields move together, because each check-result version pins exactly one
        policy-document version.
        """
        from validation.artifact_io.schema_store import (
            schema_name_for_document,
            validate_document,
        )

        result = hotspot_result()
        result["check_result_format_version"] = "1.1.0"
        result["policy"]["evaluated_as_format_version"] = "2.1.0"
        result.pop("ratchet", None)
        result.pop("evaluated_input_provenance", None)
        result["run"]["run_directory"] = "run"
        # 1.1 publishes exactly one evidence kind. A 1.1.0 document therefore
        # cannot carry a duplication record, and dropping it is what makes this
        # a real 1.1.0 document rather than a 1.2.0 one wearing a lower version.
        result["evidence"].pop("duplication", None)
        for record in result["evidence"].values():
            for key in (
                "document_sha256", "trust_state", "producer_identity", "attestation"
            ):
                record.pop(key, None)
        name = schema_name_for_document("check_result_output", result)
        self.assertEqual(name, "check_result_output_1_1_historical")
        self.assertEqual(
            [str(item) for item in validate_document(
                name, result, "check_result.json"
            )],
            [],
        )
        self.assertEqual(
            sarif_module.validate_sarif_subset(
                sarif_module.project_sarif(result)
            ),
            [],
        )

    def test_a_finding_with_empty_evidence_gains_no_evidence_property(self):
        """Old findings keep their old shape; the key appears only when used."""
        result = hotspot_result(
            metric="repository.hotspot_high_attention_file_count",
            operator="gt", threshold=0,
        )
        for item in results_of(sarif_module.project_sarif(result)):
            with self.subTest(rule=item["ruleId"]):
                self.assertNotIn("evidence", archlens(item))

    def test_the_existing_callable_projection_is_unchanged(self):
        """A callable finding still carries its span and its row identity."""
        finding = {
            "finding_id": "alf1:" + ("1" * 32),
            "rule_id": "cx.max", "kind": "metric", "severity": "violation",
            "status": "violated", "scope": "callable", "subject_key": "s",
            "message": "m", "metric": "callable.cyclomatic_complexity",
            "operator": "gt", "threshold": 10, "observed_value": 12,
            "language": "Python", "path": "src/a.py", "start_line": 4,
            "end_line": 9, "callable_row_id": "row-1",
            "callable_qualified_name": "pkg.run",
            "data_completeness": "complete", "value_status": "complete",
            "value_status_field": "structural_complexity_status",
            "reason": None, "evidence": {}, "provenance": {"run_id": "run-1"},
        }
        document = sarif_module.project_sarif({
            "check_result_format_version": "1.1.0",
            "archlens_version": PROGRAM_VERSION, "verdict": "fail",
            "exit_code": 1, "failure_kind": None,
            "policy": {}, "run": {}, "counts": {},
            "rules": [{"rule_id": "cx.max", "kind": "metric",
                       "severity": "violation", "scope": "callable"}],
            "findings": [finding], "waived_findings": [], "provenance": {},
        })
        result = results_of(document)[0]
        region = result["locations"][0]["physicalLocation"]["region"]
        self.assertEqual(region, {"startLine": 4, "endLine": 9})
        self.assertEqual(archlens(result)["callableRowId"], "row-1")
        self.assertNotIn("evidence", archlens(result))
        self.assertNotIn("locationOmitted", archlens(result))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
