"""Policy v2 and ``archlens check``: contract, evaluation, gate, output.

Four properties matter more than any individual assertion, and each has tests
written to fail loudly if it ever stops holding:

* **No metric is invented for a policy.** The allowlist is closed and every
  entry names a persisted ArchLens field. A test enumerates the forbidden
  shapes — branch counts, percentiles, composite scores, maintainability
  indices — and asserts none exists.
* **Missing is never zero.** There is one coercion path, and a mutation test
  asserts that making it return ``0`` instead of ``None`` breaks the suite.
* **A gap is never a pass.** A rule whose measurement is unavailable is
  reported `not_evaluable`, is listed separately, and fails the build under the
  default option. A rule whose scope had nothing to measure is `not_applicable`
  and never fails. The two are never merged.
* **Policy v1 still means exactly what it meant.** A v1 document loads
  unchanged, `archlens policy evaluate` is untouched, and the integrity
  findings `check` reports for a v1 document are the same set that command
  reports for the same document and run.
"""

from __future__ import annotations

import csv
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager, redirect_stdout
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from modules.acquisition import AcquiredRepository, AcquisitionRecord
from modules.benchmark_qualification import (
    QUALIFICATION_PROFILE as _QUALIFICATION_PROFILE,
)
from modules.benchmark_runner import run_benchmark
from modules.config import AnalysisConfig
from modules.policy import check as check_module
from modules.policy import findings as finding_module
from modules.policy import metrics as metric_module
from modules.policy import rules as rule_module
from modules.policy.document import PolicyDocumentInvalid, load_policy
from modules.policy.document_v2 import (
    POLICY_DOCUMENT_V2_FORMAT_VERSION,
    MetricRule,
    PolicyOptions,
    PolicyV2Document,
    adapt_v1_document,
    load_any_policy,
    load_any_policy_file,
    load_metric_rule,
    load_policy_v2,
)
from modules.ratchet.check_service import (
    FAILURE_RATCHET_ADMISSION,
    FAILURE_RATCHET_EVALUATION,
    RATCHET_CHECK_RESULT_FORMAT_VERSION,
)
from tests import historical_fixtures
from validation.artifact_io.schema_store import (
    load_schema,
    schema_name_for_document,
    validate_document,
)

ANALYZED_SHA = "a" * 40
REPOSITORY = Path(__file__).resolve().parent.parent
#: Imported rather than restated: a profile literal that drifted from the
#: producer would make the qualified-run fixture silently unbuildable.
QUALIFICATION_PROFILE = _QUALIFICATION_PROFILE


def _validate_check_result(result: dict) -> list:
    return validate_document(
        schema_name_for_document("check_result_output", result),
        result,
        "check_result.json",
    )

#: One Python file and one JavaScript file, chosen so every scope has something
#: to say: two languages, a class method with real nesting, a trivial function,
#: and a JavaScript callable that a `*.py` exclusion must keep.
SOURCE_PY = (
    "class App:\n"
    "    def run(self, a, b):\n"
    "        if a:\n"
    "            for i in range(b):\n"
    "                if i > 2:\n"
    "                    return i\n"
    "        return 1\n"
    "\n"
    "def helper(x):\n"
    "    return x\n"
)
SOURCE_JS = "function f(a) { if (a) { return 1; } return 0; }\n"


def _policy(*rules: dict, **document) -> PolicyV2Document:
    """Build a v2 policy through the real loader, never by hand.

    Constructing the dataclass directly would let a test exercise a rule the
    loader would have refused, which is the one thing these tests must not do.
    """
    payload = {
        "policy_document_format_version": POLICY_DOCUMENT_V2_FORMAT_VERSION,
        "name": "test-policy",
        "metric_rules": list(rules),
    }
    payload.update(document)
    return load_policy_v2(payload)


def _rule(identifier: str, metric: str, operator: str, threshold, **extra) -> dict:
    return {
        "id": identifier, "metric": metric, "operator": operator,
        "threshold": threshold, **extra,
    }


class RunBuilder:
    """Builds one real run bundle, once, and hands out disposable copies.

    Building costs a couple of seconds and every evaluation test needs the same
    bundle, so it is built once per class and copied for the tests that mutate
    it. A test that mutated the shared bundle would make every later test depend
    on execution order.
    """

    _source: Path | None = None

    @classmethod
    def build(cls, root: Path, registry: Path | None = None) -> Path:
        repo = root / "source"
        repo.mkdir(parents=True, exist_ok=True)
        (repo / "app.py").write_text(SOURCE_PY, encoding="utf-8", newline="\n")
        (repo / "util.js").write_text(SOURCE_JS, encoding="utf-8", newline="\n")
        source_csv = root / "repositories.csv"
        source_csv.write_text(
            "url,architecture_type,expected_language,commit_sha,enabled,notes\n"
            f"https://github.com/acme/mono,monolith,Python,{ANALYZED_SHA},true,x\n",
            encoding="utf-8",
        )
        config = AnalysisConfig.from_env(
            workspace=root, output_root=root / "output", cache_root=root / "cache",
            temporary_directory=root / "worktrees", workers=1,
        )

        @contextmanager
        def _acquired(spec):
            yield AcquiredRepository(
                repo,
                AcquisitionRecord(
                    repository_url=spec.url, repository_owner=spec.owner,
                    repository_name=spec.repository_name,
                    requested_commit_sha=spec.commit_sha,
                    analyzed_commit_sha=ANALYZED_SHA,
                    resolved_ref="refs/heads/main", default_branch="main",
                    acquisition_mode="offline", cache_status="reused",
                    remote_checked=False, fetch_timestamp=None,
                    checkout_timestamp="2026-08-01T00:00:00Z",
                    commit_verification_status="verified",
                    fetch_method="offline_cache",
                ),
            )

        def acquire(spec, config_, mode="latest", progress=None):
            del config_, mode, progress
            return _acquired(spec)

        with (
            patch("modules.benchmark_runner.acquire_repository", acquire),
            patch(
                "modules.benchmark_runner._distribution_version",
                return_value="4.0.0",
            ),
        ):
            summary = run_benchmark(
                [source_csv], config, "offline",
                command_line_arguments=["policy-v2-test"],
                qualification_registry_path=registry,
            )
        return Path(summary["run_directory"])


class RunFixture(unittest.TestCase):
    """One shared, immutable run bundle plus a copy-on-demand helper."""

    shared_run: Path
    _shared_root: tempfile.TemporaryDirectory

    @classmethod
    def setUpClass(cls) -> None:
        cls._shared_root = tempfile.TemporaryDirectory(prefix="archlens_pv2_shared_")
        cls.shared_run = RunBuilder.build(Path(cls._shared_root.name))

    @classmethod
    def tearDownClass(cls) -> None:
        cls._shared_root.cleanup()

    def copy_run(self) -> Path:
        """A private copy of the shared bundle, safe to mutate."""
        directory = tempfile.TemporaryDirectory(prefix="archlens_pv2_copy_")
        self.addCleanup(directory.cleanup)
        destination = Path(directory.name) / "run"
        shutil.copytree(self.shared_run, destination)
        return destination

    def scratch(self) -> Path:
        directory = tempfile.TemporaryDirectory(prefix="archlens_pv2_scratch_")
        self.addCleanup(directory.cleanup)
        return Path(directory.name)

    # -- convenience readers ------------------------------------------------

    @staticmethod
    def analysis(run: Path) -> list[dict]:
        return json.loads((run / "analysis.json").read_text(encoding="utf-8"))

    @staticmethod
    def write_analysis(run: Path, payload) -> None:
        (run / "analysis.json").write_text(
            json.dumps(payload, indent=2), encoding="utf-8", newline="\n"
        )

    def aggregate(self, run: Path | None = None) -> dict:
        return self.analysis(run or self.shared_run)[0]["metrics"]["aggregate"]

    def complexity(self, run: Path | None = None) -> dict:
        return self.analysis(run or self.shared_run)[0]["metrics"]["complexity"]

    def evaluate(self, policy: PolicyV2Document, run: Path | None = None) -> dict:
        return check_module.evaluate_check(
            run or self.shared_run, policy, today=date(2026, 8, 13)
        )

    @staticmethod
    def statuses(result: dict, rule_id: str) -> list[str]:
        return [
            item["status"] for item in result["findings"]
            if item["rule_id"] == rule_id
        ]

    @staticmethod
    def rule_summary(result: dict, rule_id: str) -> dict:
        return next(
            item for item in result["rules"] if item["rule_id"] == rule_id
        )


# ==========================================================================
# The allowlist
# ==========================================================================

class AllowlistTests(unittest.TestCase):
    """Policy v2 gates on persisted fields only. Nothing may be invented."""

    def test_no_metric_outside_the_persisted_families_exists(self):
        allowed = {
            metric_module.FAMILY_CORE,
            metric_module.FAMILY_STRUCTURAL_COMPLEXITY,
            metric_module.FAMILY_COGNITIVE_COMPLEXITY,
            # Read from an ADMITTED standalone Hotspot document, never measured
            # here. The family is allowlisted; its members are pinned exactly by
            # `test_hotspot_metrics_are_exactly_the_approved_eleven` below.
            metric_module.FAMILY_HOTSPOTS,
            # Read from an ADMITTED standalone Duplication document, never
            # measured here. Members pinned exactly by
            # `test_duplication_metrics_are_exactly_the_approved_fifteen` in
            # tests/test_duplication_policy_core_dp1.py.
            metric_module.FAMILY_DUPLICATION,
        }
        for definition in metric_module.METRICS:
            with self.subTest(metric=definition.identifier):
                self.assertIn(definition.family, allowed)
                self.assertIn(definition.scope, metric_module.SCOPES)
                self.assertTrue(definition.source, "a metric must name its source")
                self.assertTrue(
                    definition.definition, "a metric must state what it means"
                )

    def test_no_forbidden_metric_shape_was_introduced(self):
        """The shapes this campaign explicitly refused to create.

        `hotspot` was on this list, and is deliberately no longer. The guard
        existed to stop Policy v2 INVENTING a hotspot metric; the approved
        metrics invent nothing, they read a validated standalone analysis
        through an admission boundary. The guard's intent is preserved by
        `test_hotspot_metrics_are_exactly_the_approved_eleven`, which is
        stricter than a substring ban: it forbids a twelfth hotspot metric
        appearing without review, not merely a word.

        Every other term stays, and applies to the new names too.
        """
        forbidden = (
            "branch_count", "complex_condition", "condition_count", "p95", "p90",
            "percentile", "quality_score", "maintainability", "composite",
            "normalized", "rating", "grade", "score", "index",
            "defect", "bug", "risk", "rank",
        )
        for definition in metric_module.METRICS:
            haystack = definition.identifier.casefold()
            for term in forbidden:
                with self.subTest(metric=definition.identifier, term=term):
                    self.assertNotIn(term, haystack)

    def test_hotspot_metrics_are_exactly_the_approved_eleven(self):
        """The vocabulary the owner approved, and not one identifier more."""
        approved = {
            "repository.hotspot_file_count",
            "repository.hotspot_classified_file_count",
            "repository.hotspot_high_attention_file_count",
            "repository.hotspot_moderate_attention_file_count",
            "repository.hotspot_low_attention_file_count",
            "repository.hotspot_unclassified_file_count",
            "hotspot_file.cognitive_complexity_total",
            "hotspot_file.cyclomatic_complexity_total",
            "hotspot_file.max_nesting_depth_max",
            "hotspot_file.churn_commits",
            "hotspot_file.churn_touched_lines",
        }
        exposed = {
            item.identifier for item in metric_module.METRICS
            if item.family == metric_module.FAMILY_HOTSPOTS
        }
        self.assertEqual(exposed, approved)

    def test_no_hotspot_metric_exposes_an_ordinal_signal_or_class(self):
        """Ordinals travel as evidence; no rule may compare one as a number."""
        for item in metric_module.METRICS:
            if item.family != metric_module.FAMILY_HOTSPOTS:
                continue
            with self.subTest(metric=item.identifier):
                for term in ("signal", "classification", "attention_class"):
                    self.assertNotIn(term, item.identifier.casefold())
                self.assertEqual(item.value_type, "integer")

    def test_metric_ids_are_unique(self):
        identifiers = [item.identifier for item in metric_module.METRICS]
        self.assertEqual(len(identifiers), len(set(identifiers)))

    def test_structural_aggregates_are_exactly_the_approved_eleven(self):
        """The allowlist may not widen the frozen Complexity Contract surface."""
        from modules import complexity_view

        approved = set(complexity_view.AGGREGATE_FIELDS)
        for scope, prefix in (
            (metric_module.SCOPE_REPOSITORY, "repository."),
            (metric_module.SCOPE_LANGUAGE, "language."),
        ):
            exposed = {
                item.identifier[len(prefix):]
                for item in metric_module.metrics_for_scope(scope)
                if item.family == metric_module.FAMILY_STRUCTURAL_COMPLEXITY
            }
            with self.subTest(scope=scope):
                self.assertEqual(exposed, approved)

    def test_cognitive_aggregates_are_exactly_the_existing_five(self):
        """Policy v2 defines no cognitive aggregate of its own."""
        from modules import complexity_view

        approved = set(complexity_view.COGNITIVE_AGGREGATE_FIELDS)
        exposed = {
            item.identifier.split(".", 1)[1]
            for item in metric_module.metrics_for_scope(
                metric_module.SCOPE_REPOSITORY
            )
            if item.family == metric_module.FAMILY_COGNITIVE_COMPLEXITY
        }
        self.assertEqual(exposed, approved)

    def test_core_metrics_are_exactly_the_four_contract_metrics(self):
        exposed = {
            item.identifier.split(".", 1)[1]
            for item in metric_module.METRICS
            if item.family == metric_module.FAMILY_CORE
        }
        self.assertEqual(
            exposed,
            {"lines_of_code", "source_files", "classes_structs", "methods_functions"},
        )

    def test_every_core_metric_has_a_status_gate(self):
        for definition in metric_module.METRICS:
            if definition.family != metric_module.FAMILY_CORE:
                continue
            with self.subTest(metric=definition.identifier):
                self.assertIn(definition.field, metric_module.CORE_STATUS_FIELD)

    def test_the_callable_cognitive_metric_is_gated_on_nullability_not_a_status(self):
        """0 is an ordinary measured value for cognitive complexity."""
        self.assertIsNone(
            metric_module.CALLABLE_STATUS_COLUMN["callable.cognitive_complexity"]
        )
        for identifier, column in metric_module.CALLABLE_STATUS_COLUMN.items():
            if identifier == "callable.cognitive_complexity":
                continue
            with self.subTest(metric=identifier):
                self.assertIsNotNone(column)


class NumericCoercionTests(unittest.TestCase):
    """Missing is never zero. This is the only place a cell becomes a number."""

    def test_absent_and_empty_values_are_none_not_zero(self):
        for raw in (None, "", "   "):
            with self.subTest(raw=raw):
                self.assertIsNone(metric_module.numeric_value(raw))

    def test_a_bool_is_not_a_number(self):
        """`True` is an `int` subclass and would otherwise arrive as 1."""
        self.assertIsNone(metric_module.numeric_value(True))
        self.assertIsNone(metric_module.numeric_value(False))

    def test_non_finite_floats_are_refused(self):
        for raw in (float("nan"), float("inf"), float("-inf"), "nan", "inf"):
            with self.subTest(raw=raw):
                self.assertIsNone(metric_module.numeric_value(raw))

    def test_a_non_numeric_string_is_none(self):
        for raw in ("unavailable", "n/a", "-", "1.2.3", "0x10"):
            with self.subTest(raw=raw):
                self.assertIsNone(metric_module.numeric_value(raw))

    def test_measured_zero_survives(self):
        """The mirror image: a real 0 must not be mistaken for absence."""
        self.assertEqual(metric_module.numeric_value(0), 0)
        self.assertEqual(metric_module.numeric_value("0"), 0)
        self.assertEqual(metric_module.numeric_value(0.0), 0.0)

    def test_integers_and_floats_both_round_trip(self):
        self.assertEqual(metric_module.numeric_value("42"), 42)
        self.assertIsInstance(metric_module.numeric_value("42"), int)
        self.assertEqual(metric_module.numeric_value("2.5"), 2.5)
        self.assertEqual(metric_module.numeric_value(2.3333), 2.3333)

    def test_an_unknown_status_is_unavailable_never_complete(self):
        self.assertEqual(
            metric_module.completeness_of_status("something_new"),
            metric_module.COMPLETENESS_UNAVAILABLE,
        )
        self.assertEqual(
            metric_module.completeness_of_status(None),
            metric_module.COMPLETENESS_UNAVAILABLE,
        )

    def test_a_complete_status_beside_a_null_value_is_unavailable(self):
        """A status cannot conjure a number nobody wrote."""
        definition = metric_module.METRICS_BY_ID["repository.lines_of_code"]
        observation = metric_module.read_core(
            {"lines_of_code": None, "loc_status": "complete"}, definition
        )
        self.assertEqual(
            observation.completeness, metric_module.COMPLETENESS_UNAVAILABLE
        )
        self.assertIsNone(observation.value)


# ==========================================================================
# The document contract
# ==========================================================================

class DocumentRejectionTests(unittest.TestCase):
    """Nothing malformed is skipped. Every rejection below raises."""

    def test_an_unknown_metric_is_refused(self):
        with self.assertRaises(PolicyDocumentInvalid) as caught:
            _policy(_rule("a", "repository.invented_metric", "gt", 1))
        self.assertIn("unknown metric", str(caught.exception))

    def test_an_invented_metric_is_refused_even_when_plausible(self):
        for metric in (
            "repository.branch_count", "repository.cyclomatic_complexity_p95",
            "repository.maintainability_index", "repository.quality_score",
        ):
            with self.subTest(metric=metric):
                with self.assertRaises(PolicyDocumentInvalid):
                    _policy(_rule("a", metric, "gt", 1))

    def test_an_invalid_operator_is_refused(self):
        for operator in ("greater", ">", "GT", "between", "matches", "~=", None, 1):
            with self.subTest(operator=operator):
                with self.assertRaises(PolicyDocumentInvalid) as caught:
                    _policy(_rule("a", "repository.lines_of_code", operator, 1))
                self.assertIn("invalid operator", str(caught.exception))

    def test_a_duplicate_rule_id_is_refused(self):
        with self.assertRaises(PolicyDocumentInvalid) as caught:
            _policy(
                _rule("same", "repository.lines_of_code", "gt", 1),
                _rule("same", "repository.source_files", "gt", 1),
            )
        self.assertIn("duplicate rule id", str(caught.exception))

    def test_a_metric_rule_may_not_reuse_an_integrity_rule_id(self):
        with self.assertRaises(PolicyDocumentInvalid) as caught:
            _policy(
                _rule("run.integrity_failed", "repository.lines_of_code", "gt", 1),
                integrity_rules={"run.integrity_failed": "violation"},
            )
        self.assertIn("already an enabled integrity rule", str(caught.exception))

    def test_a_non_finite_threshold_is_refused(self):
        for threshold in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(threshold=threshold):
                with self.assertRaises(PolicyDocumentInvalid) as caught:
                    _policy(_rule("a", "repository.lines_of_code", "gt", threshold))
                self.assertIn("finite", str(caught.exception))

    def test_a_non_numeric_threshold_is_refused(self):
        for threshold in ("100", None, True, [], {}, "1e5"):
            with self.subTest(threshold=threshold):
                with self.assertRaises(PolicyDocumentInvalid):
                    _policy(_rule("a", "repository.lines_of_code", "gt", threshold))

    def test_an_impossible_scope_metric_pairing_is_refused(self):
        with self.assertRaises(PolicyDocumentInvalid) as caught:
            _policy(_rule(
                "a", "repository.lines_of_code", "gt", 1, scope="callable"
            ))
        self.assertIn("cannot be evaluated at", str(caught.exception))

    def test_language_scoping_on_a_repository_metric_is_refused(self):
        with self.assertRaises(PolicyDocumentInvalid) as caught:
            _policy(_rule(
                "a", "repository.lines_of_code", "gt", 1, languages=["Python"]
            ))
        self.assertIn("languages", str(caught.exception))

    def test_path_scoping_outside_callable_scope_is_refused(self):
        for metric in ("repository.lines_of_code", "language.lines_of_code"):
            for key in ("paths", "exclude_paths"):
                with self.subTest(metric=metric, key=key):
                    with self.assertRaises(PolicyDocumentInvalid) as caught:
                        _policy(_rule("a", metric, "gt", 1, **{key: ["*.py"]}))
                    self.assertIn("per-file location", str(caught.exception))

    def test_an_unknown_rule_key_is_refused_not_ignored(self):
        with self.assertRaises(PolicyDocumentInvalid) as caught:
            _policy(_rule(
                "a", "repository.lines_of_code", "gt", 1, sevirity="violation"
            ))
        self.assertIn("unknown key", str(caught.exception))

    def test_an_unknown_document_key_is_refused_not_ignored(self):
        with self.assertRaises(PolicyDocumentInvalid) as caught:
            load_policy_v2({
                "policy_document_format_version": "2.0.0", "name": "x",
                "metric_rules": [], "integrity_rules": {"run.integrity_failed": "violation"},
                "rules": {"run.integrity_failed": "violation"},
            })
        self.assertIn("unknown key", str(caught.exception))

    def test_an_unknown_option_is_refused(self):
        with self.assertRaises(PolicyDocumentInvalid):
            _policy(
                _rule("a", "repository.lines_of_code", "gt", 1),
                options={"on_not_evalable": "fail"},
            )

    def test_there_is_no_ignore_setting_for_non_evaluable_rules(self):
        """A policy must never report PASS merely because data was absent."""
        with self.assertRaises(PolicyDocumentInvalid) as caught:
            _policy(
                _rule("a", "repository.lines_of_code", "gt", 1),
                options={"on_not_evaluable": "ignore"},
            )
        self.assertIn("no 'ignore'", str(caught.exception))

    def test_an_empty_policy_is_refused(self):
        with self.assertRaises(PolicyDocumentInvalid) as caught:
            load_policy_v2({
                "policy_document_format_version": "2.0.0", "name": "empty",
            })
        self.assertIn("at least one", str(caught.exception))

    def test_an_empty_filter_array_is_refused(self):
        """Omitting a filter means "no filter"; an empty one is ambiguous."""
        with self.assertRaises(PolicyDocumentInvalid):
            _policy(_rule(
                "a", "callable.cyclomatic_complexity", "gt", 1, paths=[]
            ))

    def test_a_bad_rule_id_is_refused(self):
        for identifier in ("", "   ", "has space", "has/slash", "a" * 129, 7, None):
            with self.subTest(identifier=identifier):
                with self.assertRaises(PolicyDocumentInvalid):
                    load_metric_rule(
                        {
                            "id": identifier, "metric": "repository.lines_of_code",
                            "operator": "gt", "threshold": 1,
                        },
                        "metric_rules[0]",
                    )

    def test_an_unknown_integrity_rule_is_refused(self):
        with self.assertRaises(PolicyDocumentInvalid):
            load_policy_v2({
                "policy_document_format_version": "2.0.0", "name": "x",
                "integrity_rules": {"no.such.rule": "violation"},
            })

    def test_an_unknown_severity_is_refused(self):
        with self.assertRaises(PolicyDocumentInvalid):
            _policy(_rule(
                "a", "repository.lines_of_code", "gt", 1, severity="fatal"
            ))

    def test_a_waiver_naming_no_enabled_rule_is_refused(self):
        with self.assertRaises(PolicyDocumentInvalid) as caught:
            _policy(
                _rule("a", "repository.lines_of_code", "gt", 1),
                waivers=[{
                    "rule_id": "not-enabled", "reason": "x",
                    "expires_on": "2099-01-01",
                }],
            )
        self.assertIn("unknown rule", str(caught.exception))

    def test_a_waiver_still_needs_a_reason_and_an_expiry(self):
        base = _rule("a", "repository.lines_of_code", "gt", 1)
        with self.assertRaises(PolicyDocumentInvalid) as caught:
            _policy(base, waivers=[{"rule_id": "a", "expires_on": "2099-01-01"}])
        self.assertIn("reason", str(caught.exception))
        with self.assertRaises(PolicyDocumentInvalid) as caught:
            _policy(base, waivers=[{"rule_id": "a", "reason": "x"}])
        self.assertIn("expiry", str(caught.exception))

    def test_a_wrong_format_version_is_refused(self):
        for declared in ("1.0.0", "3.0.0", "2.0", 2, None):
            with self.subTest(declared=declared):
                with self.assertRaises(PolicyDocumentInvalid):
                    load_policy_v2({
                        "policy_document_format_version": declared, "name": "x",
                        "integrity_rules": {"run.integrity_failed": "violation"},
                    })


class HostilePolicyTests(unittest.TestCase):
    """Malformed input from an untrusted file must raise, never crash oddly."""

    HOSTILE = (
        None, [], "a string", 42, True,
        {"policy_document_format_version": "2.0.0"},
        {"policy_document_format_version": "2.0.0", "name": ""},
        {"policy_document_format_version": "2.0.0", "name": "x",
         "metric_rules": "not-an-array"},
        {"policy_document_format_version": "2.0.0", "name": "x",
         "metric_rules": {"id": "a"}},
        {"policy_document_format_version": "2.0.0", "name": "x",
         "metric_rules": [None]},
        {"policy_document_format_version": "2.0.0", "name": "x",
         "metric_rules": ["a string"]},
        {"policy_document_format_version": "2.0.0", "name": "x",
         "integrity_rules": "not-an-object"},
        {"policy_document_format_version": "2.0.0", "name": "x",
         "integrity_rules": {"run.integrity_failed": "violation"},
         "waivers": "not-an-array"},
        {"policy_document_format_version": "2.0.0", "name": "x",
         "integrity_rules": {"run.integrity_failed": "violation"},
         "waivers": [{"rule_id": "run.integrity_failed", "reason": "x",
                      "expires_on": "not-a-date"}]},
        {"policy_document_format_version": "2.0.0", "name": "x",
         "integrity_rules": {"run.integrity_failed": "violation"},
         "expected_contracts": "not-an-object"},
        {"policy_document_format_version": "2.0.0", "name": "x",
         "integrity_rules": {"run.integrity_failed": "violation"},
         "options": "not-an-object"},
        {"policy_document_format_version": "2.0.0", "name": "x",
         "metric_rules": [{"id": "a", "metric": "repository.lines_of_code",
                           "operator": "gt", "threshold": 1,
                           "languages": "Python"}]},
        {"policy_document_format_version": "2.0.0", "name": "x",
         "metric_rules": [{"id": "a", "metric": "repository.lines_of_code",
                           "operator": "gt", "threshold": 1,
                           "metadata": "not-an-object"}]},
        {"policy_document_format_version": "2.0.0", "name": "x",
         "metric_rules": [{"id": "a", "metric": "repository.lines_of_code",
                           "operator": "gt", "threshold": 1, "message": ""}]},
    )

    def test_every_hostile_document_raises_the_typed_error(self):
        for index, payload in enumerate(self.HOSTILE):
            with self.subTest(index=index, payload=repr(payload)[:70]):
                with self.assertRaises(PolicyDocumentInvalid):
                    load_any_policy(payload)

    def test_a_file_that_is_not_json_raises_the_typed_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            path.write_text("{not json", encoding="utf-8")
            with self.assertRaises(PolicyDocumentInvalid) as caught:
                load_any_policy_file(path)
            self.assertIn("not valid JSON", str(caught.exception))

    def test_a_missing_file_raises_the_typed_error(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(PolicyDocumentInvalid) as caught:
                load_any_policy_file(Path(directory) / "absent.json")
            self.assertIn("cannot read policy", str(caught.exception))

    def test_dispatch_is_on_the_declared_version_alone(self):
        """A typo in the version must not be guessed around from the keys."""
        with self.assertRaises(PolicyDocumentInvalid) as caught:
            load_any_policy({
                "policy_document_format_version": "2.0",
                "name": "x", "metric_rules": [],
                "integrity_rules": {"run.integrity_failed": "violation"},
            })
        self.assertIn("policy_document_format_version", str(caught.exception))


# ==========================================================================
# Policy v1 compatibility
# ==========================================================================

class V1CompatibilityTests(RunFixture):
    """v1 keeps meaning exactly what it meant."""

    V1 = {
        "policy_document_format_version": "1.0.0",
        "name": "v1-policy",
        "rules": {
            "artifact.schema_invalid": "violation",
            "run.integrity_failed": "violation",
            "diagnostics.unknown_category": "violation",
            "measurement.metric_unavailable": "warning",
        },
    }

    def test_the_v1_loader_is_unchanged_and_still_accepts_v1(self):
        document = load_policy(self.V1)
        self.assertEqual(document.name, "v1-policy")
        self.assertEqual(document.severity_for("run.integrity_failed"), "violation")

    def test_check_accepts_a_v1_document(self):
        policy = load_any_policy(self.V1)
        self.assertEqual(policy.source_format_version, "1.0.0")
        self.assertEqual(policy.metric_rules, ())
        self.assertEqual(len(policy.integrity_rules), 4)

    def test_the_result_never_claims_a_v1_file_was_a_v2_policy(self):
        result = self.evaluate(load_any_policy(self.V1))
        self.assertEqual(
            result["policy"]["policy_document_format_version"], "1.0.0"
        )

    def test_check_and_policy_evaluate_agree_on_integrity_findings(self):
        """The adaptation must not reinterpret a single rule.

        Driven against a successful run mutated to fire several non-lifecycle
        rules at once. Non-successful runs are now refused at Check admission,
        before either integrity or metric rules may evaluate.
        """
        from modules.policy.engine import evaluate as evaluate_v1

        run = self.copy_run()
        analysis = self.analysis(run)
        analysis[0]["unknown_categories"] = ["mystery"]
        analysis[0]["metrics"]["aggregate"]["lines_of_code"] = None
        self.write_analysis(run, analysis)

        document = load_policy(self.V1)
        v1_result = evaluate_v1(run, document, today=date(2026, 8, 13))
        v2_result = check_module.evaluate_check(
            run, adapt_v1_document(document), today=date(2026, 8, 13)
        )

        expected = {
            (item["rule_id"], item["severity"], item["subject_key"] or "")
            for item in v1_result["findings"]
        }
        observed = {
            (item["rule_id"], item["severity"], item["subject_key"])
            for item in v2_result["findings"]
        }
        self.assertEqual(observed, expected)
        self.assertTrue(expected, "the mutated run must actually fire rules")

    def test_policy_evaluate_refuses_a_v2_document(self):
        """It refuses rather than silently ignoring `metric_rules`."""
        with self.assertRaises(PolicyDocumentInvalid):
            load_policy({
                "policy_document_format_version": "2.0.0", "name": "x",
                "metric_rules": [],
            })

    def test_v1_waivers_and_pins_survive_the_adaptation(self):
        document = load_policy({
            **self.V1,
            "expected_contracts": {"metric_contract_version": "3.0.0"},
            "waivers": [{
                "rule_id": "run.integrity_failed", "reason": "tracked",
                "expires_on": "2099-01-01", "issue_id": "ISSUE-9",
            }],
        })
        adapted = adapt_v1_document(document)
        self.assertEqual(
            adapted.expected_contracts, {"metric_contract_version": "3.0.0"}
        )
        self.assertEqual(len(adapted.waivers), 1)
        self.assertEqual(adapted.waivers[0].issue_id, "ISSUE-9")

    def test_the_v1_schemas_are_untouched(self):
        """v1's frozen contracts keep their exact declared versions."""
        self.assertEqual(load_schema("policy_document")["properties"][
            "policy_document_format_version"]["const"], "1.0.0")
        self.assertEqual(load_schema("policy_result_output")["properties"][
            "policy_result_format_version"]["const"], "1.0.0")

    def test_a_v2_policy_may_carry_integrity_rules_and_they_use_the_v1_engine(self):
        run = self.copy_run()
        analysis = self.analysis(run)
        analysis[0]["unknown_categories"] = ["mystery"]
        self.write_analysis(run, analysis)
        policy = _policy(
            _rule("loc", "repository.lines_of_code", "gt", 1_000_000),
            integrity_rules={"diagnostics.unknown_category": "violation"},
        )
        result = self.evaluate(policy, run)
        integrity = [
            item for item in result["findings"]
            if item["kind"] == finding_module.KIND_INTEGRITY
        ]
        self.assertEqual(len(integrity), 1)
        self.assertEqual(integrity[0]["rule_id"], "diagnostics.unknown_category")
        self.assertEqual(integrity[0]["domain"], rule_module.DOMAIN_DIAGNOSTICS)
        self.assertIsNone(integrity[0]["metric"])
        self.assertIsNone(integrity[0]["threshold"])


class AdmissionTests(RunFixture):
    """Only successful authoritative runs may reach rule evaluation."""

    def _with_status(self, status_value: str | None) -> Path:
        run = self.copy_run()
        status_path = run / "run_status.json"
        if status_value is None:
            status_path.unlink()
        else:
            status = json.loads(status_path.read_text(encoding="utf-8"))
            status["status"] = status_value
            status_path.write_text(
                json.dumps(status, indent=2), encoding="utf-8", newline="\n"
            )
        return run

    def test_failed_running_and_missing_status_are_refused_before_rules(self):
        policy = _policy(
            _rule("loc", "repository.lines_of_code", "gt", 1_000_000)
        )
        for status_value in ("failed", "running", None):
            with self.subTest(status=status_value):
                run = self._with_status(status_value)
                with patch(
                    "modules.cli.validate_command.schema_only_report",
                    side_effect=AssertionError("full schema validation must stay lazy"),
                ), self.assertRaises(check_module.CheckFailed) as caught:
                    self.evaluate(policy, run)
                self.assertEqual(
                    caught.exception.kind,
                    check_module.FAILURE_RUN_UNREADABLE,
                )

    def test_successful_metric_only_run_behavior_is_unchanged(self):
        result = self.evaluate(
            _policy(_rule("loc", "repository.lines_of_code", "gt", 1_000_000))
        )
        self.assertEqual(result["verdict"], check_module.VERDICT_PASS)
        self.assertEqual(result["exit_code"], check_module.EXIT_PASS)
        self.assertIsNone(result["run"]["schema_validation_result"])


# ==========================================================================
# Finding identity and ordering
# ==========================================================================

class FindingIdentityTests(unittest.TestCase):
    def test_identity_is_stable_across_calls(self):
        first = finding_module.finding_identity(
            rule_id="r", metric="repository.lines_of_code", scope="repository",
            subject_key="github.com/acme/mono",
        )
        second = finding_module.finding_identity(
            rule_id="r", metric="repository.lines_of_code", scope="repository",
            subject_key="github.com/acme/mono",
        )
        self.assertEqual(first, second)
        self.assertRegex(first, r"^alf1:[0-9a-f]{32}$")

    def test_identity_ignores_the_observed_value_and_the_threshold(self):
        """The same problem keeps one identity as the number drifts."""
        arguments = dict(
            rule_id="r", metric="repository.lines_of_code", scope="repository",
            subject_key="s",
        )
        # There is no parameter for either, which is the guarantee. This test
        # exists so adding one is a deliberate, visible change.
        import inspect

        parameters = set(
            inspect.signature(finding_module.finding_identity).parameters
        )
        self.assertNotIn("observed_value", parameters)
        self.assertNotIn("threshold", parameters)
        self.assertEqual(
            finding_module.finding_identity(**arguments),
            finding_module.finding_identity(**arguments),
        )

    def test_each_coordinate_changes_the_identity(self):
        base = dict(
            rule_id="r", metric="repository.lines_of_code", scope="repository",
            subject_key="s", language=None, path=None, callable_row_id=None,
            discriminator=None,
        )
        baseline = finding_module.finding_identity(**base)
        for key, value in (
            ("rule_id", "other"), ("metric", "repository.source_files"),
            ("scope", "language"), ("subject_key", "other"),
            ("language", "Python"), ("path", "a.py"),
            ("callable_row_id", "sha256:" + "0" * 64), ("discriminator", "x"),
        ):
            with self.subTest(coordinate=key):
                self.assertNotEqual(
                    finding_module.finding_identity(**{**base, key: value}),
                    baseline,
                )

    def test_coordinates_cannot_collide_across_field_boundaries(self):
        """NUL joining, so ("a.b","c") and ("a","b.c") are different ids."""
        self.assertNotEqual(
            finding_module.finding_identity(
                rule_id="a.b", metric="c", scope="repository", subject_key="s"
            ),
            finding_module.finding_identity(
                rule_id="a", metric="b.c", scope="repository", subject_key="s"
            ),
        )

    def test_language_case_does_not_split_one_identity(self):
        self.assertEqual(
            finding_module.finding_identity(
                rule_id="r", metric="language.lines_of_code", scope="language",
                subject_key="s", language="Python",
            ),
            finding_module.finding_identity(
                rule_id="r", metric="language.lines_of_code", scope="language",
                subject_key="s", language="python",
            ),
        )

    def test_the_sort_key_is_total_and_deterministic(self):
        def make(**overrides):
            payload = dict(
                finding_id="alf1:" + "0" * 32, rule_id="r", severity="violation",
                status=finding_module.STATUS_VIOLATED, scope="callable",
                subject_key="s", message="m",
            )
            payload.update(overrides)
            return finding_module.Finding(**payload)

        items = [
            make(rule_id="b"), make(rule_id="a"),
            make(rule_id="a", severity="warning"),
            make(rule_id="a", path="z.py"), make(rule_id="a", path="a.py"),
            make(rule_id="a", path="a.py", start_line=9),
            make(rule_id="a", path="a.py", start_line=2),
        ]
        first = [item.finding_id for item in finding_module.sort_findings(items)]
        second = [
            item.finding_id
            for item in finding_module.sort_findings(list(reversed(items)))
        ]
        self.assertEqual(first, second)
        ordered = finding_module.sort_findings(items)
        self.assertEqual(ordered[-1].severity, "warning", "severity leads")

    def test_only_problem_statuses_are_reported(self):
        self.assertNotIn(finding_module.STATUS_PASSED, finding_module.REPORTED_STATUSES)
        for status in (
            finding_module.STATUS_VIOLATED, finding_module.STATUS_NOT_EVALUABLE,
            finding_module.STATUS_NOT_APPLICABLE,
            finding_module.STATUS_EVALUATION_ERROR,
        ):
            self.assertIn(status, finding_module.REPORTED_STATUSES)


class ComparisonTests(unittest.TestCase):
    """The rule states its failing condition. Exact boundaries, no tolerance."""

    def test_each_operator_at_the_boundary_just_below_and_just_above(self):
        cases = {
            "gt": (False, False, True),
            "gte": (False, True, True),
            "lt": (True, False, False),
            "lte": (True, True, False),
            "eq": (False, True, False),
            "ne": (True, False, True),
        }
        for operator, (below, equal, above) in cases.items():
            with self.subTest(operator=operator):
                self.assertIs(finding_module.compare(9, operator, 10), below)
                self.assertIs(finding_module.compare(10, operator, 10), equal)
                self.assertIs(finding_module.compare(11, operator, 10), above)

    def test_float_boundaries_are_exact(self):
        self.assertFalse(finding_module.compare(2.5, "gt", 2.5))
        self.assertTrue(finding_module.compare(2.5000001, "gt", 2.5))
        self.assertFalse(finding_module.compare(2.4999999, "gt", 2.5))

    def test_an_integer_observation_compares_against_a_float_threshold(self):
        self.assertTrue(finding_module.compare(3, "gt", 2.5))
        self.assertFalse(finding_module.compare(2, "gt", 2.5))

    def test_an_unsupported_operator_raises_rather_than_defaulting(self):
        with self.assertRaises(ValueError):
            finding_module.compare(1, "approximately", 1)


# ==========================================================================
# Evaluation over a real run
# ==========================================================================

class RepositoryScopeTests(RunFixture):
    def test_boundary_behaviour_on_a_real_persisted_integer(self):
        observed = self.aggregate()["lines_of_code"]
        self.assertIsInstance(observed, int)
        for threshold, expected in (
            (observed - 1, finding_module.STATUS_VIOLATED),
            (observed, finding_module.STATUS_PASSED),
            (observed + 1, finding_module.STATUS_PASSED),
        ):
            with self.subTest(threshold=threshold, operator="gt"):
                result = self.evaluate(
                    _policy(_rule("r", "repository.lines_of_code", "gt", threshold))
                )
                statuses = self.statuses(result, "r")
                if expected == finding_module.STATUS_PASSED:
                    self.assertEqual(statuses, [])
                    self.assertEqual(self.rule_summary(result, "r")["passed"], 1)
                else:
                    self.assertEqual(statuses, [finding_module.STATUS_VIOLATED])

    def test_gte_fires_exactly_at_the_threshold(self):
        observed = self.aggregate()["lines_of_code"]
        fired = self.evaluate(
            _policy(_rule("r", "repository.lines_of_code", "gte", observed))
        )
        self.assertEqual(self.statuses(fired, "r"), [finding_module.STATUS_VIOLATED])
        quiet = self.evaluate(
            _policy(_rule("r", "repository.lines_of_code", "gte", observed + 1))
        )
        self.assertEqual(self.statuses(quiet, "r"), [])

    def test_a_fractional_aggregate_is_compared_as_written(self):
        observed = self.complexity()["aggregate"]["cyclomatic_complexity_mean"]
        self.assertIsInstance(observed, float)
        result = self.evaluate(_policy(
            _rule("mean", "repository.cyclomatic_complexity_mean", "gte", observed)
        ))
        finding = next(
            item for item in result["findings"] if item["rule_id"] == "mean"
        )
        self.assertEqual(finding["observed_value"], observed)
        self.assertEqual(finding["status"], finding_module.STATUS_VIOLATED)

    def test_the_observed_value_matches_the_persisted_field(self):
        aggregate = self.aggregate()
        complexity = self.complexity()["aggregate"]
        expectations = {
            "repository.lines_of_code": aggregate["lines_of_code"],
            "repository.source_files": aggregate["source_files"],
            "repository.classes_structs": aggregate["classes_structs"],
            "repository.methods_functions": aggregate["methods_functions"],
            "repository.callable_count": complexity["callable_count"],
            "repository.cyclomatic_complexity_max":
                complexity["cyclomatic_complexity_max"],
            "repository.nloc_max": complexity["nloc_max"],
        }
        rules = [
            _rule(metric, metric, "gte", 0) for metric in expectations
        ]
        result = self.evaluate(_policy(*rules))
        observed = {
            item["rule_id"]: item["observed_value"] for item in result["findings"]
        }
        self.assertEqual(observed, expectations)

    def test_a_cognitive_aggregate_uses_the_existing_definition(self):
        """Derived from the persisted column by `complexity_view`, not here."""
        import csv

        from modules import complexity_view

        with (self.shared_run / "callables.csv").open(
            newline="", encoding="utf-8"
        ) as handle:
            rows = list(csv.DictReader(handle))
        expected = complexity_view.cognitive_aggregate(rows)

        rules = [
            _rule(field, f"repository.{field}", "gte", 0)
            for field in complexity_view.COGNITIVE_AGGREGATE_FIELDS
        ]
        result = self.evaluate(_policy(*rules))
        observed = {
            item["rule_id"]: item["observed_value"] for item in result["findings"]
        }
        self.assertEqual(observed, dict(expected))

    def test_a_subject_filter_selects_the_named_subject(self):
        subject = self.analysis(self.shared_run)[0]["subject_key"]
        result = self.evaluate(_policy(_rule(
            "r", "repository.lines_of_code", "gte", 0, subjects=[subject]
        )))
        self.assertEqual(self.statuses(result, "r"), [finding_module.STATUS_VIOLATED])

    def test_a_subject_filter_matching_nothing_fails_closed(self):
        """A mistyped repository identity is malformed required state."""
        with self.assertRaises(check_module.CheckFailed) as caught:
            self.evaluate(_policy(_rule(
                "r", "repository.lines_of_code", "gt", 0,
                subjects=["github.com/acme/not-in-this-run"],
            )))
        self.assertEqual(caught.exception.kind, check_module.FAILURE_EVALUATION_ERROR)
        self.assertIn("unknown repository subject", caught.exception.message)

    def test_a_callable_rule_with_an_unmatched_subject_filter_fails_closed(self):
        with self.assertRaises(check_module.CheckFailed) as caught:
            self.evaluate(_policy(_rule(
                "cx", "callable.cyclomatic_complexity", "gt", 0,
                subjects=["github.com/acme/not-in-this-run"],
            )))
        self.assertEqual(caught.exception.kind, check_module.FAILURE_EVALUATION_ERROR)

    def test_a_passing_rule_is_counted_and_never_enumerated(self):
        result = self.evaluate(_policy(
            _rule("r", "repository.lines_of_code", "gt", 1_000_000)
        ))
        self.assertEqual(result["findings"], [])
        summary = self.rule_summary(result, "r")
        self.assertEqual(summary["passed"], 1)
        self.assertEqual(summary["units_evaluated"], 1)
        self.assertEqual(result["counts"]["units"]["passed"], 1)
        self.assertEqual(result["counts"]["by_status"]["passed"], 0)


class LanguageScopeTests(RunFixture):
    def test_a_language_rule_evaluates_every_recorded_language(self):
        result = self.evaluate(_policy(
            _rule("r", "language.lines_of_code", "gte", 0)
        ))
        languages = {item["language"] for item in result["findings"]}
        self.assertIn("python", {name.casefold() for name in languages})
        self.assertIn("javascript", {name.casefold() for name in languages})

    def test_language_scoping_narrows_to_the_named_languages(self):
        result = self.evaluate(_policy(
            _rule("r", "language.lines_of_code", "gte", 0, languages=["Python"])
        ))
        languages = {
            item["language"].casefold() for item in result["findings"]
        }
        self.assertEqual(languages, {"python"})

    def test_language_matching_is_case_insensitive(self):
        for spelling in ("python", "Python", "PYTHON", "PyThOn"):
            with self.subTest(spelling=spelling):
                result = self.evaluate(_policy(_rule(
                    "r", "language.lines_of_code", "gte", 0, languages=[spelling]
                )))
                self.assertEqual(len(result["findings"]), 1)

    def test_a_language_with_no_source_is_not_applicable_never_zero(self):
        """A Java rule on a Python repository must not fail the build."""
        result = self.evaluate(_policy(_rule(
            "java", "language.lines_of_code", "gt", 0, languages=["Java"]
        )))
        findings = [item for item in result["findings"] if item["rule_id"] == "java"]
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["status"], finding_module.STATUS_NOT_APPLICABLE)
        self.assertIsNone(findings[0]["observed_value"])
        self.assertEqual(result["verdict"], check_module.VERDICT_PASS)
        self.assertEqual(result["exit_code"], check_module.EXIT_PASS)

    def test_a_language_absent_from_the_artifact_is_still_reported(self):
        """Silence is indistinguishable from a rule that ran and passed."""
        result = self.evaluate(_policy(_rule(
            "cobol", "language.lines_of_code", "gt", 0, languages=["COBOL"]
        )))
        findings = [item for item in result["findings"] if item["rule_id"] == "cobol"]
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["status"], finding_module.STATUS_NOT_APPLICABLE)

    def test_the_two_by_language_spellings_do_not_produce_two_units(self):
        """Core metrics are keyed lowercase, complexity in display case."""
        result = self.evaluate(_policy(
            _rule("core", "language.lines_of_code", "gte", 0),
            _rule("cx", "language.callable_count", "gte", 0),
        ))
        for rule_id in ("core", "cx"):
            findings = [
                item for item in result["findings"] if item["rule_id"] == rule_id
            ]
            folded = [item["language"].casefold() for item in findings]
            with self.subTest(rule=rule_id):
                self.assertEqual(len(folded), len(set(folded)))

    def test_a_language_observation_matches_the_persisted_row(self):
        by_language = self.analysis(self.shared_run)[0]["metrics"]["by_language"]
        result = self.evaluate(_policy(
            _rule("r", "language.methods_functions", "gte", 0)
        ))
        observed = {
            item["language"].casefold(): item["observed_value"]
            for item in result["findings"]
        }
        for language, values in by_language.items():
            if values.get("methods_functions_status") == "not_applicable":
                continue
            with self.subTest(language=language):
                self.assertEqual(
                    observed[language.casefold()], values["methods_functions"]
                )


class CallableScopeTests(RunFixture):
    def _rows(self, run: Path | None = None) -> list[dict]:
        import csv

        with ((run or self.shared_run) / "callables.csv").open(
            newline="", encoding="utf-8"
        ) as handle:
            return list(csv.DictReader(handle))

    def test_one_finding_per_violating_callable(self):
        rows = self._rows()
        threshold = min(int(row["cyclomatic_complexity"]) for row in rows)
        expected = sum(
            1 for row in rows if int(row["cyclomatic_complexity"]) > threshold
        )
        result = self.evaluate(_policy(_rule(
            "cx", "callable.cyclomatic_complexity", "gt", threshold
        )))
        violated = [
            item for item in result["findings"]
            if item["status"] == finding_module.STATUS_VIOLATED
        ]
        self.assertEqual(len(violated), expected)
        self.assertTrue(expected, "the fixture must have a violating callable")

    def test_a_callable_finding_carries_its_location(self):
        result = self.evaluate(_policy(_rule(
            "cx", "callable.cyclomatic_complexity", "gte", 1
        )))
        finding = next(
            item for item in result["findings"]
            if item["status"] == finding_module.STATUS_VIOLATED
        )
        self.assertTrue(finding["path"])
        self.assertRegex(finding["callable_row_id"], r"^sha256:[0-9a-f]{64}$")
        self.assertTrue(finding["callable_qualified_name"])
        self.assertIsInstance(finding["start_line"], int)
        self.assertIsInstance(finding["end_line"], int)

    def test_path_scoping_includes_only_matching_files(self):
        result = self.evaluate(_policy(_rule(
            "cx", "callable.cyclomatic_complexity", "gte", 1, paths=["*.py"]
        )))
        paths = {
            item["path"] for item in result["findings"]
            if item["status"] == finding_module.STATUS_VIOLATED
        }
        self.assertTrue(paths)
        self.assertTrue(all(path.endswith(".py") for path in paths))

    def test_exclude_paths_removes_matching_files(self):
        result = self.evaluate(_policy(_rule(
            "cx", "callable.cyclomatic_complexity", "gte", 1,
            exclude_paths=["*.py"],
        )))
        paths = {
            item["path"] for item in result["findings"]
            if item["status"] == finding_module.STATUS_VIOLATED
        }
        self.assertTrue(paths)
        self.assertFalse(any(path.endswith(".py") for path in paths))

    def test_exclude_wins_over_include(self):
        result = self.evaluate(_policy(_rule(
            "cx", "callable.cyclomatic_complexity", "gte", 1,
            paths=["*.py"], exclude_paths=["app.py"],
        )))
        violated = [
            item for item in result["findings"]
            if item["status"] == finding_module.STATUS_VIOLATED
        ]
        self.assertEqual(violated, [])

    def test_a_path_filter_matching_nothing_is_reported_not_silent(self):
        """A typo'd glob must be visible, without failing the build."""
        result = self.evaluate(_policy(_rule(
            "cx", "callable.cyclomatic_complexity", "gt", 0, paths=["*.rs"]
        )))
        findings = [item for item in result["findings"] if item["rule_id"] == "cx"]
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["status"], finding_module.STATUS_NOT_APPLICABLE)
        self.assertEqual(findings[0]["evidence"]["matched_callable_rows"], 0)
        self.assertEqual(result["exit_code"], check_module.EXIT_PASS)

    def test_language_scoping_applies_at_callable_scope(self):
        result = self.evaluate(_policy(_rule(
            "cx", "callable.cyclomatic_complexity", "gte", 1,
            languages=["JavaScript"],
        )))
        languages = {
            item["language"] for item in result["findings"]
            if item["status"] == finding_module.STATUS_VIOLATED
        }
        self.assertEqual(languages, {"JavaScript"})

    def test_a_measured_zero_cognitive_value_is_evaluated_not_skipped(self):
        """0 is the commonest cognitive measurement, and it is a measurement."""
        rows = self._rows()
        self.assertIn(
            "0", {row["cognitive_complexity"] for row in rows},
            "the fixture must contain a measured cognitive zero",
        )
        result = self.evaluate(_policy(_rule(
            "cog", "callable.cognitive_complexity", "gte", 0
        )))
        violated = [
            item for item in result["findings"]
            if item["status"] == finding_module.STATUS_VIOLATED
        ]
        self.assertEqual(len(violated), len(rows))
        self.assertIn(0, [item["observed_value"] for item in violated])

    def test_a_null_cognitive_value_is_unavailable_not_zero(self):
        run = self.copy_run()
        text = (run / "callables.csv").read_text(encoding="utf-8")
        rows = text.splitlines()
        header = rows[0].split(",")
        index = header.index("cognitive_complexity")
        blanked = [rows[0]]
        for row in rows[1:]:
            cells = row.split(",")
            cells[index] = ""
            blanked.append(",".join(cells))
        (run / "callables.csv").write_text(
            "\n".join(blanked) + "\n", encoding="utf-8", newline="\n"
        )

        result = self.evaluate(
            _policy(_rule("cog", "callable.cognitive_complexity", "gte", 0)), run
        )
        findings = [item for item in result["findings"] if item["rule_id"] == "cog"]
        self.assertEqual(len(findings), 1, "non-evaluable rows collapse to one")
        self.assertEqual(findings[0]["status"], finding_module.STATUS_NOT_EVALUABLE)
        self.assertEqual(
            findings[0]["reason"], finding_module.REASON_MEASUREMENT_UNAVAILABLE
        )
        self.assertGreater(findings[0]["evidence"]["collapsed_callable_rows"], 0)
        self.assertEqual(result["exit_code"], check_module.EXIT_VIOLATION)

    def test_non_evaluable_callables_collapse_to_one_finding_per_reason(self):
        run = self.copy_run()
        text = (run / "callables.csv").read_text(encoding="utf-8")
        rows = text.splitlines()
        header = rows[0].split(",")
        status_index = header.index("structural_complexity_status")
        value_index = header.index("cyclomatic_complexity")
        broken = [rows[0]]
        for row in rows[1:]:
            cells = row.split(",")
            cells[status_index] = "failed"
            cells[value_index] = ""
            broken.append(",".join(cells))
        (run / "callables.csv").write_text(
            "\n".join(broken) + "\n", encoding="utf-8", newline="\n"
        )

        result = self.evaluate(
            _policy(_rule("cx", "callable.cyclomatic_complexity", "gt", 0)), run
        )
        findings = [item for item in result["findings"] if item["rule_id"] == "cx"]
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["status"], finding_module.STATUS_NOT_EVALUABLE)
        self.assertEqual(
            findings[0]["evidence"]["collapsed_callable_rows"], len(rows) - 1
        )

    def test_a_missing_callable_ledger_is_not_evaluable_not_a_pass(self):
        run = self.copy_run()
        (run / "callables.csv").unlink()
        shutil.rmtree(run / "callables")
        result = self.evaluate(
            _policy(_rule("cx", "callable.cyclomatic_complexity", "gt", 0)), run
        )
        findings = [item for item in result["findings"] if item["rule_id"] == "cx"]
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["status"], finding_module.STATUS_NOT_EVALUABLE)
        self.assertEqual(
            findings[0]["reason"], finding_module.REASON_CALLABLE_ARTIFACT_ABSENT
        )
        self.assertEqual(result["exit_code"], check_module.EXIT_VIOLATION)

    def test_a_missing_ledger_makes_cognitive_aggregates_unavailable(self):
        """Not `not_applicable`: the column the aggregate derives from is gone."""
        run = self.copy_run()
        (run / "callables.csv").unlink()
        shutil.rmtree(run / "callables")
        result = self.evaluate(
            _policy(_rule("cog", "repository.cognitive_complexity_max", "gt", 0)),
            run,
        )
        findings = [item for item in result["findings"] if item["rule_id"] == "cog"]
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["status"], finding_module.STATUS_NOT_EVALUABLE)
        self.assertEqual(
            findings[0]["reason"], finding_module.REASON_MEASUREMENT_UNAVAILABLE
        )
        self.assertIsNone(findings[0]["observed_value"])


# ==========================================================================
# Status semantics
# ==========================================================================

class StatusSemanticsTests(RunFixture):
    """metric_status is read, never redefined, and never becomes a number."""

    def _with_status(self, status: str, value=None) -> Path:
        run = self.copy_run()
        analysis = self.analysis(run)
        aggregate = analysis[0]["metrics"]["aggregate"]
        aggregate["loc_status"] = status
        if value is not None or status in ("failed", "not_applicable"):
            aggregate["lines_of_code"] = value
        self.write_analysis(run, analysis)
        return run

    def test_complete_is_evaluated(self):
        result = self.evaluate(
            _policy(_rule("r", "repository.lines_of_code", "gte", 0))
        )
        finding = next(item for item in result["findings"] if item["rule_id"] == "r")
        self.assertEqual(finding["status"], finding_module.STATUS_VIOLATED)
        self.assertEqual(
            finding["data_completeness"], metric_module.COMPLETENESS_COMPLETE
        )

    def test_partial_is_evaluated_and_stamped_partial_by_default(self):
        run = self._with_status("partial", value=5)
        result = self.evaluate(
            _policy(_rule("r", "repository.lines_of_code", "gt", 1)), run
        )
        finding = next(item for item in result["findings"] if item["rule_id"] == "r")
        self.assertEqual(finding["status"], finding_module.STATUS_VIOLATED)
        self.assertEqual(
            finding["data_completeness"], metric_module.COMPLETENESS_PARTIAL
        )
        self.assertIn("partial observation", finding["message"])

    def test_partial_can_be_refused_by_the_policy(self):
        run = self._with_status("partial", value=5)
        result = self.evaluate(
            _policy(
                _rule("r", "repository.lines_of_code", "gt", 1),
                options={"partial_data": "not_evaluable"},
            ),
            run,
        )
        finding = next(item for item in result["findings"] if item["rule_id"] == "r")
        self.assertEqual(finding["status"], finding_module.STATUS_NOT_EVALUABLE)
        self.assertEqual(
            finding["reason"], finding_module.REASON_PARTIAL_DATA_REFUSED
        )

    def test_failed_is_not_evaluable_and_never_zero(self):
        run = self._with_status("failed")
        result = self.evaluate(
            _policy(_rule("r", "repository.lines_of_code", "gt", 0)), run
        )
        finding = next(item for item in result["findings"] if item["rule_id"] == "r")
        self.assertEqual(finding["status"], finding_module.STATUS_NOT_EVALUABLE)
        self.assertIsNone(finding["observed_value"])
        # The sentence must say the rule did not run AND deny that this is a
        # pass. A reader who sees only the message must not infer success.
        self.assertIn("did not run", finding["message"])
        self.assertIn("not a pass", finding["message"])

    def test_not_applicable_is_not_a_failure(self):
        run = self._with_status("not_applicable")
        result = self.evaluate(
            _policy(_rule("r", "repository.lines_of_code", "gt", 0)), run
        )
        finding = next(item for item in result["findings"] if item["rule_id"] == "r")
        self.assertEqual(finding["status"], finding_module.STATUS_NOT_APPLICABLE)
        self.assertEqual(result["exit_code"], check_module.EXIT_PASS)

    def test_a_missing_metric_is_not_zero(self):
        """The defect this whole status model exists to prevent."""
        run = self.copy_run()
        analysis = self.analysis(run)
        analysis[0]["metrics"]["aggregate"].pop("lines_of_code")
        self.write_analysis(run, analysis)
        result = self.evaluate(
            _policy(_rule("r", "repository.lines_of_code", "lte", 0)), run
        )
        finding = next(item for item in result["findings"] if item["rule_id"] == "r")
        self.assertEqual(finding["status"], finding_module.STATUS_NOT_EVALUABLE)
        self.assertIsNone(finding["observed_value"])

    def test_an_absent_complexity_block_is_absent_not_failed(self):
        run = self.copy_run()
        analysis = self.analysis(run)
        analysis[0]["metrics"].pop("complexity")
        self.write_analysis(run, analysis)
        result = self.evaluate(
            _policy(_rule("cx", "repository.cyclomatic_complexity_max", "gt", 0)),
            run,
        )
        finding = next(item for item in result["findings"] if item["rule_id"] == "cx")
        self.assertEqual(finding["status"], finding_module.STATUS_NOT_EVALUABLE)
        self.assertEqual(
            finding["reason"], finding_module.REASON_MEASUREMENT_ABSENT
        )
        self.assertEqual(
            finding["data_completeness"], metric_module.COMPLETENESS_ABSENT
        )

    def test_a_non_evaluable_violation_rule_fails_the_build_by_default(self):
        run = self._with_status("failed")
        result = self.evaluate(
            _policy(_rule("r", "repository.lines_of_code", "gt", 0)), run
        )
        self.assertEqual(result["verdict"], check_module.VERDICT_FAIL)
        self.assertEqual(result["exit_code"], check_module.EXIT_VIOLATION)
        self.assertTrue(result["not_evaluable"][0]["fails_the_build"])

    def test_warn_mode_reports_without_moving_the_exit_code(self):
        run = self._with_status("failed")
        result = self.evaluate(
            _policy(
                _rule("r", "repository.lines_of_code", "gt", 0),
                options={"on_not_evaluable": "warn"},
            ),
            run,
        )
        self.assertEqual(result["exit_code"], check_module.EXIT_PASS)
        self.assertEqual(len(result["not_evaluable"]), 1)
        self.assertFalse(result["not_evaluable"][0]["fails_the_build"])

    def test_a_warning_severity_rule_never_fails_on_non_evaluability(self):
        run = self._with_status("failed")
        result = self.evaluate(
            _policy(_rule(
                "r", "repository.lines_of_code", "gt", 0, severity="warning"
            )),
            run,
        )
        self.assertEqual(result["exit_code"], check_module.EXIT_PASS)

    def test_metric_status_itself_is_never_modified(self):
        """Policy evaluation reads the artifact; it must not write to it."""
        run = self.copy_run()
        before = (run / "analysis.json").read_bytes()
        self.evaluate(
            _policy(
                _rule("a", "repository.lines_of_code", "gt", 0),
                _rule("b", "callable.cyclomatic_complexity", "gt", 0),
                _rule("c", "language.methods_functions", "gt", 0),
            ),
            run,
        )
        self.assertEqual((run / "analysis.json").read_bytes(), before)

    def test_the_persisted_status_travels_with_the_finding(self):
        result = self.evaluate(
            _policy(_rule("r", "repository.lines_of_code", "gte", 0))
        )
        finding = next(item for item in result["findings"] if item["rule_id"] == "r")
        self.assertEqual(finding["value_status"], "complete")
        self.assertEqual(finding["value_status_field"], "loc_status")


# ==========================================================================
# Determinism and output contract
# ==========================================================================

class DeterminismTests(RunFixture):
    POLICY = (
        _rule("loc", "repository.lines_of_code", "gt", 1),
        _rule("lang", "language.methods_functions", "gte", 0),
        _rule("cx", "callable.cyclomatic_complexity", "gte", 1),
        _rule("cog", "repository.cognitive_complexity_max", "gte", 0),
        _rule("absent", "language.lines_of_code", "gt", 0, languages=["Java"]),
    )

    def test_two_evaluations_produce_identical_bytes(self):
        policy = _policy(*self.POLICY)
        first = json.dumps(self.evaluate(policy), sort_keys=True, indent=2)
        second = json.dumps(self.evaluate(policy), sort_keys=True, indent=2)
        self.assertEqual(first, second)

    def test_rule_order_in_the_document_does_not_change_the_findings(self):
        forward = self.evaluate(_policy(*self.POLICY))
        reverse = self.evaluate(_policy(*reversed(self.POLICY)))
        self.assertEqual(
            [item["finding_id"] for item in forward["findings"]],
            [item["finding_id"] for item in reverse["findings"]],
        )

    def test_every_finding_id_in_one_result_is_unique(self):
        result = self.evaluate(_policy(*self.POLICY))
        identifiers = [item["finding_id"] for item in result["findings"]]
        self.assertEqual(len(identifiers), len(set(identifiers)))

    def test_integrity_finding_ids_are_unique_even_when_a_rule_fires_twice(self):
        """Two v1 rules legitimately emit more than one finding per subject."""
        run = self.copy_run()
        policy = load_any_policy({
            "policy_document_format_version": "2.0.0",
            "name": "pins",
            "integrity_rules": {"contract.incompatible": "violation"},
            "expected_contracts": {
                "metric_contract_version": "99.0.0",
                "artifact_schema_version": "99.0.0",
            },
        })
        result = self.evaluate(policy, run)
        findings = [
            item for item in result["findings"]
            if item["rule_id"] == "contract.incompatible"
        ]
        self.assertEqual(len(findings), 2)
        self.assertEqual(
            len({item["finding_id"] for item in findings}), 2,
            "a rule firing twice must not produce one id twice",
        )

    def test_the_finding_id_does_not_move_when_the_observed_value_does(self):
        run = self.copy_run()
        policy = _policy(_rule("loc", "repository.lines_of_code", "gt", 1))
        before = self.evaluate(policy, run)["findings"][0]

        analysis = self.analysis(run)
        analysis[0]["metrics"]["aggregate"]["lines_of_code"] += 500
        self.write_analysis(run, analysis)
        after = self.evaluate(policy, run)["findings"][0]

        self.assertNotEqual(before["observed_value"], after["observed_value"])
        self.assertEqual(before["finding_id"], after["finding_id"])

    def test_the_finding_id_does_not_move_when_the_threshold_is_retuned(self):
        run = self.copy_run()
        loose = self.evaluate(
            _policy(_rule("loc", "repository.lines_of_code", "gt", 1)), run
        )["findings"][0]
        tight = self.evaluate(
            _policy(_rule("loc", "repository.lines_of_code", "gt", 2)), run
        )["findings"][0]
        self.assertNotEqual(loose["threshold"], tight["threshold"])
        self.assertEqual(loose["finding_id"], tight["finding_id"])


class OutputContractTests(RunFixture):
    """Producer-to-schema contract, written with the schema."""

    RICH = (
        _rule("loc", "repository.lines_of_code", "gt", 1),
        _rule("lang", "language.methods_functions", "gte", 0),
        _rule("cx", "callable.cyclomatic_complexity", "gte", 1),
        _rule("absent", "language.lines_of_code", "gt", 0, languages=["Java"]),
    )

    def test_a_rich_result_validates_against_the_registered_schema(self):
        result = self.evaluate(_policy(*self.RICH))
        self.assertTrue(result["findings"], "the fixture must produce findings")
        violations = _validate_check_result(result)
        self.assertEqual([str(item) for item in violations], [])

    def test_a_clean_pass_validates(self):
        result = self.evaluate(
            _policy(_rule("r", "repository.lines_of_code", "gt", 1_000_000))
        )
        violations = _validate_check_result(result)
        self.assertEqual([str(item) for item in violations], [])

    def test_generated_complexity_versions_use_the_single_authority(self):
        from modules.config import COMPLEXITY_CONTRACT_VERSION

        manifest = json.loads(
            (self.shared_run / "run_manifest.json").read_text(encoding="utf-8")
        )
        analysis = self.analysis(self.shared_run)
        with (self.shared_run / "callables.csv").open(
            encoding="utf-8", newline=""
        ) as handle:
            callable_versions = {
                row["complexity_contract_version"] for row in csv.DictReader(handle)
            }

        self.assertEqual(
            manifest["complexity_contract_version"],
            COMPLEXITY_CONTRACT_VERSION,
        )
        self.assertEqual(
            analysis[0]["metrics"]["complexity"]["complexity_contract_version"],
            COMPLEXITY_CONTRACT_VERSION,
        )
        self.assertEqual(callable_versions, {COMPLEXITY_CONTRACT_VERSION})

    def test_a_waived_result_validates(self):
        policy = _policy(
            _rule("loc", "repository.lines_of_code", "gt", 1),
            waivers=[{
                "rule_id": "loc", "reason": "tracked in ISSUE-4",
                "expires_on": (date.today() + timedelta(days=30)).isoformat(),
            }],
        )
        result = self.evaluate(policy)
        self.assertEqual(len(result["waived_findings"]), 1)
        violations = _validate_check_result(result)
        self.assertEqual([str(item) for item in violations], [])

    def test_every_failure_kind_produces_a_valid_document(self):
        for kind in check_module.FAILURE_KINDS:
            with self.subTest(kind=kind):
                document = check_module.failure_result(
                    kind=kind, message="why", run_directory=Path("run"),
                    policy_name="p", today=date(2026, 8, 13),
                )
                violations = _validate_check_result(document)
                self.assertEqual([str(item) for item in violations], [])
                self.assertEqual(document["verdict"], check_module.VERDICT_ERROR)
                self.assertEqual(document["exit_code"], check_module.EXIT_ERROR)

    def test_an_undeclared_property_is_rejected(self):
        result = self.evaluate(
            _policy(_rule("r", "repository.lines_of_code", "gt", 1))
        )
        result["invented"] = True
        self.assertTrue(_validate_check_result(result))

    def test_both_new_schemas_forbid_undeclared_properties(self):
        for name in ("policy_document_v2", "check_result_output"):
            with self.subTest(schema=name):
                self.assertFalse(load_schema(name)["additionalProperties"])

    def test_the_document_schema_enumerates_exactly_the_allowlist(self):
        declared = set(
            load_schema("policy_document_v2")["$defs"]["metricRule"][
                "properties"]["metric"]["enum"]
        )
        self.assertEqual(declared, set(metric_module.METRICS_BY_ID))

    def test_the_document_schema_enumerates_exactly_the_v1_rules(self):
        declared = set(
            load_schema("policy_document_v2")["$defs"]["v1RuleId"]["enum"]
        )
        self.assertEqual(declared, set(rule_module.RULES_BY_ID))

    def test_the_document_schema_enumerates_exactly_the_operators(self):
        from modules.policy.document_v2 import OPERATORS

        declared = set(
            load_schema("policy_document_v2")["$defs"]["metricRule"][
                "properties"]["operator"]["enum"]
        )
        self.assertEqual(declared, set(OPERATORS))

    def test_the_result_schema_enumerates_exactly_the_statuses(self):
        declared = set(load_schema("check_result_output")["$defs"]["status"]["enum"])
        self.assertEqual(declared, set(finding_module.STATUSES))

    def test_the_result_schema_enumerates_exactly_the_failure_kinds(self):
        declared = set(
            load_schema("check_result_output")["properties"]["failure_kind"][
                "oneOf"][0]["enum"]
        )
        self.assertEqual(
            declared,
            set(check_module.FAILURE_KINDS)
            | {FAILURE_RATCHET_ADMISSION, FAILURE_RATCHET_EVALUATION},
        )

    def test_a_real_policy_file_validates_against_the_document_schema(self):
        # The schema is selected by the version the DOCUMENT declares, which is
        # the whole point of `schema_name_for_document`: a 2.0.0 file is judged
        # against 2.0.0 even though the build now also publishes 2.1.0.
        payload = {
            "policy_document_format_version": "2.0.0",
            "name": "example",
            "description": "example",
            "integrity_rules": {"run.integrity_failed": "violation"},
            "metric_rules": [
                {
                    "id": "cx.max", "metric": "callable.cyclomatic_complexity",
                    "operator": "gt", "threshold": 15, "severity": "violation",
                    "paths": ["src/*"], "exclude_paths": ["*_test.py"],
                    "languages": ["Python"], "message": "too complex",
                    "metadata": {"owner": "platform"},
                },
            ],
            "options": {"on_not_evaluable": "warn", "partial_data": "evaluate"},
            "waivers": [{
                "rule_id": "cx.max", "reason": "tracked", "expires_on": "2099-01-01",
                "subject_key": "github.com/acme/mono", "issue_id": "I-1",
            }],
            "expected_contracts": {"metric_contract_version": "3.0.0"},
        }
        from validation.artifact_io.schema_store import schema_name_for_document

        violations = validate_document(
            schema_name_for_document("policy_document_v2", payload),
            payload, "policy.json",
        )
        self.assertEqual([str(item) for item in violations], [])
        # And the loader accepts exactly what the schema accepts.
        self.assertEqual(len(load_policy_v2(payload).metric_rules), 1)

    def test_the_result_states_that_thresholds_are_user_supplied(self):
        result = self.evaluate(
            _policy(_rule("r", "repository.lines_of_code", "gt", 1))
        )
        self.assertTrue(result["policy"]["thresholds_are_user_supplied"])
        self.assertIn("USER-SUPPLIED POLICY", result["scope_note"])
        self.assertIn("no default threshold", result["scope_note"])

    def test_the_result_does_not_duplicate_the_analysis(self):
        """Provenance references the run; it does not copy it."""
        result = self.evaluate(
            _policy(_rule("r", "repository.lines_of_code", "gt", 1))
        )
        for subject in result["run"]["subjects"]:
            self.assertEqual(
                set(subject),
                {"subject_key", "repository_url", "analysis_status",
                 "core_metric_status"},
            )


class WaiverTests(RunFixture):
    def _waived_policy(self, expires: date, **extra):
        return _policy(
            _rule("loc", "repository.lines_of_code", "gt", 1),
            waivers=[{
                "rule_id": "loc", "reason": "tracked in ISSUE-4",
                "expires_on": expires.isoformat(), **extra,
            }],
        )

    def test_a_live_waiver_moves_a_finding_without_hiding_it(self):
        result = self.evaluate(self._waived_policy(date(2099, 1, 1)))
        self.assertEqual(result["findings"], [])
        self.assertEqual(len(result["waived_findings"]), 1)
        self.assertEqual(
            result["waived_findings"][0]["waiver"]["reason"], "tracked in ISSUE-4"
        )
        self.assertEqual(result["exit_code"], check_module.EXIT_PASS)

    def test_an_expired_waiver_stops_suppressing_and_is_reported(self):
        result = self.evaluate(self._waived_policy(date(2020, 1, 1)))
        self.assertEqual(len(result["findings"]), 1)
        self.assertEqual(len(result["expired_waivers"]), 1)
        self.assertEqual(result["exit_code"], check_module.EXIT_VIOLATION)

    def test_a_subject_scoped_waiver_does_not_leak(self):
        result = self.evaluate(
            self._waived_policy(date(2099, 1, 1), subject_key="github.com/other")
        )
        self.assertEqual(len(result["findings"]), 1)
        self.assertEqual(result["waived_findings"], [])

    def test_a_waiver_never_suppresses_a_non_evaluable_finding(self):
        """Waiving a rule that did not run would hide that it did not run."""
        run = self.copy_run()
        analysis = self.analysis(run)
        analysis[0]["metrics"]["aggregate"]["loc_status"] = "failed"
        analysis[0]["metrics"]["aggregate"]["lines_of_code"] = None
        self.write_analysis(run, analysis)
        result = self.evaluate(self._waived_policy(date(2099, 1, 1)), run)
        self.assertEqual(result["waived_findings"], [])
        self.assertEqual(len(result["not_evaluable"]), 1)
        self.assertEqual(result["exit_code"], check_module.EXIT_VIOLATION)


# ==========================================================================
# The command
# ==========================================================================

class CommandTests(RunFixture):
    class _Args:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    def _policy_file(self, payload: dict) -> Path:
        path = self.scratch() / "policy.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def _args(self, **overrides):
        values = dict(
            run_directory=self.shared_run, policy=None, format="text", output=None
        )
        values.update(overrides)
        return self._Args(**values)

    def _run_with_status(self, status_value: str | None) -> Path:
        run = self.copy_run()
        status_path = run / "run_status.json"
        if status_value is None:
            status_path.unlink()
        else:
            status = json.loads(status_path.read_text(encoding="utf-8"))
            status["status"] = status_value
            status_path.write_text(
                json.dumps(status, indent=2), encoding="utf-8", newline="\n"
            )
        return run

    def test_a_clean_pass_exits_zero(self):
        from modules.cli import check_command

        policy = self._policy_file({
            "policy_document_format_version": "2.0.0", "name": "p",
            "metric_rules": [
                _rule("r", "repository.lines_of_code", "gt", 1_000_000)
            ],
        })
        self.assertEqual(
            check_command.handle(self._args(policy=policy)), check_module.EXIT_PASS
        )

    def test_a_violation_exits_one(self):
        from modules.cli import check_command

        policy = self._policy_file({
            "policy_document_format_version": "2.0.0", "name": "p",
            "metric_rules": [_rule("r", "repository.lines_of_code", "gt", 1)],
        })
        self.assertEqual(
            check_command.handle(self._args(policy=policy)),
            check_module.EXIT_VIOLATION,
        )

    def test_a_warning_only_violation_exits_zero(self):
        from modules.cli import check_command

        policy = self._policy_file({
            "policy_document_format_version": "2.0.0", "name": "p",
            "metric_rules": [_rule(
                "r", "repository.lines_of_code", "gt", 1, severity="warning"
            )],
        })
        self.assertEqual(
            check_command.handle(self._args(policy=policy)), check_module.EXIT_PASS
        )

    def test_an_invalid_policy_exits_two(self):
        from modules.cli import check_command

        policy = self._policy_file({
            "policy_document_format_version": "2.0.0", "name": "p",
            "metric_rules": [_rule("r", "repository.nope", "gt", 1)],
        })
        self.assertEqual(
            check_command.handle(self._args(policy=policy)), check_module.EXIT_ERROR
        )

    def test_a_missing_run_exits_two(self):
        from modules.cli import check_command

        policy = self._policy_file({
            "policy_document_format_version": "2.0.0", "name": "p",
            "metric_rules": [_rule("r", "repository.lines_of_code", "gt", 1)],
        })
        self.assertEqual(
            check_command.handle(self._args(
                policy=policy, run_directory=self.scratch() / "absent"
            )),
            check_module.EXIT_ERROR,
        )

    def test_non_successful_runs_exit_two_and_write_error_json(self):
        from modules.cli import check_command

        policy = self._policy_file({
            "policy_document_format_version": "2.0.0", "name": "p",
            "metric_rules": [
                _rule("r", "repository.lines_of_code", "gt", 1_000_000)
            ],
        })
        for label, status_value in (
            ("failed", "failed"), ("running", "running"), ("missing", None)
        ):
            with self.subTest(status=label):
                destination = self.scratch() / f"{label}.json"
                with redirect_stdout(io.StringIO()):
                    exit_code = check_command.handle(self._args(
                        policy=policy,
                        run_directory=self._run_with_status(status_value),
                        format="json",
                        output=destination,
                    ))
                result = json.loads(destination.read_text(encoding="utf-8"))
                self.assertEqual(exit_code, check_module.EXIT_ERROR)
                self.assertEqual(result["verdict"], check_module.VERDICT_ERROR)
                self.assertEqual(result["exit_code"], check_module.EXIT_ERROR)
                self.assertEqual(
                    result["failure_kind"],
                    check_module.FAILURE_RUN_UNREADABLE,
                )

    def test_failed_run_sarif_is_an_unsuccessful_exit_two_projection(self):
        from modules.cli import check_command

        policy = self._policy_file({
            "policy_document_format_version": "2.0.0", "name": "p",
            "metric_rules": [
                _rule("r", "repository.lines_of_code", "gt", 1_000_000)
            ],
        })
        destination = self.scratch() / "failed.sarif"
        with redirect_stdout(io.StringIO()):
            exit_code = check_command.handle(self._args(
                policy=policy,
                run_directory=self._run_with_status("failed"),
                format="sarif",
                output=destination,
            ))
        document = json.loads(destination.read_text(encoding="utf-8"))
        sarif_run = document["runs"][0]
        self.assertEqual(exit_code, check_module.EXIT_ERROR)
        self.assertFalse(sarif_run["invocations"][0]["executionSuccessful"])
        self.assertEqual(
            sarif_run["properties"]["archlens"]["failureKind"],
            check_module.FAILURE_RUN_UNREADABLE,
        )

    def test_a_missing_policy_argument_exits_two(self):
        from modules.cli import check_command

        self.assertEqual(
            check_command.handle(self._args(policy=None)), check_module.EXIT_ERROR
        )

    def test_the_result_file_is_written_when_requested(self):
        from modules.cli import check_command

        policy = self._policy_file({
            "policy_document_format_version": "2.0.0", "name": "p",
            "metric_rules": [_rule("r", "repository.lines_of_code", "gt", 1)],
        })
        destination = self.scratch() / "reports" / "check.json"
        check_command.handle(
            self._args(policy=policy, output=destination, format="json")
        )
        self.assertTrue(destination.is_file())
        written = json.loads(destination.read_text(encoding="utf-8"))
        self.assertEqual(
            written["check_result_format_version"],
            check_module.CHECK_RESULT_FORMAT_VERSION,
        )
        self.assertEqual(written["exit_code"], check_module.EXIT_VIOLATION)

    def test_a_failed_evaluation_still_writes_a_parseable_document(self):
        """CI must never confuse "no output" with "clean pass"."""
        from modules.cli import check_command

        policy = self._policy_file({
            "policy_document_format_version": "2.0.0", "name": "p",
            "metric_rules": [_rule("r", "repository.nope", "gt", 1)],
        })
        destination = self.scratch() / "check.json"
        check_command.handle(
            self._args(policy=policy, output=destination, format="json")
        )
        written = json.loads(destination.read_text(encoding="utf-8"))
        self.assertEqual(written["verdict"], check_module.VERDICT_ERROR)
        self.assertEqual(written["failure_kind"], check_module.FAILURE_POLICY_INVALID)

    def test_rendered_output_is_ascii_except_authoritative_trust_separator(self):
        """Only the documented UTF-8 trust label may be non-ASCII in CI text."""
        from modules.cli import check_command

        result = self.evaluate(_policy(
            _rule("loc", "repository.lines_of_code", "gt", 1),
            _rule("java", "language.lines_of_code", "gt", 0, languages=["Java"]),
        ))
        rendered = check_command.render_text(result)
        self.assertIn("LOCAL — UNPROTECTED", rendered)
        self.assertTrue(
            rendered.replace("LOCAL — UNPROTECTED", "LOCAL - UNPROTECTED").isascii(),
            "the explicit trust label must be the only non-ASCII text",
        )

    def test_the_rendering_states_the_scope_limit_and_never_claims_quality(self):
        from modules.cli import check_command

        rendered = check_command.render_text(
            self.evaluate(_policy(_rule("r", "repository.lines_of_code", "gt", 1)))
        )
        self.assertIn("USER-SUPPLIED POLICY", rendered)
        for forbidden in ("quality score", "maintainability", "grade", "rating"):
            self.assertNotIn(forbidden, rendered.casefold())

    def test_a_non_evaluable_rule_is_rendered_as_not_a_pass(self):
        from modules.cli import check_command

        run = self.copy_run()
        analysis = self.analysis(run)
        analysis[0]["metrics"]["aggregate"]["loc_status"] = "failed"
        analysis[0]["metrics"]["aggregate"]["lines_of_code"] = None
        self.write_analysis(run, analysis)
        rendered = check_command.render_text(
            self.evaluate(_policy(_rule("r", "repository.lines_of_code", "gt", 1)), run)
        )
        self.assertIn("did NOT run", rendered)
        self.assertIn("fails the build", rendered)

    def test_the_metric_listing_is_machine_readable(self):
        from modules.cli import check_command

        listing = check_command.render_metric_listing()
        self.assertEqual(
            {item["metric"] for item in listing["metrics"]},
            set(metric_module.METRICS_BY_ID),
        )
        self.assertIn("USER-SUPPLIED POLICY", listing["threshold_note"])
        json.dumps(listing)

    def test_policy_metrics_subcommand_exits_zero(self):
        from modules.cli import policy_command

        code = policy_command.handle(self._Args(policy_command="metrics"))
        self.assertEqual(code, 0)


class CommandLineTests(RunFixture):
    """The real process, so the parser wiring and exit codes are proven."""

    def _run(self, *arguments: str) -> subprocess.CompletedProcess:
        environment = {
            **__import__("os").environ,
            "PYTHONPATH": str(REPOSITORY),
            "PYTHONIOENCODING": "utf-8",
        }
        return subprocess.run(
            [sys.executable, "-m", "pipeline", *arguments],
            capture_output=True, text=True, cwd=str(REPOSITORY), env=environment,
        )

    def test_the_command_is_registered_and_documented(self):
        completed = self._run("check", "--help")
        self.assertEqual(completed.returncode, 0)
        self.assertIn("--policy", completed.stdout)
        self.assertIn("user-supplied policy", completed.stdout.casefold())

    def test_policy_is_required_by_the_parser(self):
        completed = self._run("check", str(self.shared_run))
        self.assertEqual(completed.returncode, 2)

    def test_exit_codes_end_to_end(self):
        directory = self.scratch()
        clean = directory / "clean.json"
        clean.write_text(json.dumps({
            "policy_document_format_version": "2.0.0", "name": "p",
            "metric_rules": [
                _rule("r", "repository.lines_of_code", "gt", 1_000_000)
            ],
        }), encoding="utf-8")
        failing = directory / "failing.json"
        failing.write_text(json.dumps({
            "policy_document_format_version": "2.0.0", "name": "p",
            "metric_rules": [_rule("r", "repository.lines_of_code", "gt", 1)],
        }), encoding="utf-8")
        broken = directory / "broken.json"
        broken.write_text("{", encoding="utf-8")

        for policy, expected in (
            (clean, 0), (failing, 1), (broken, 2),
        ):
            with self.subTest(policy=policy.name):
                completed = self._run(
                    "check", str(self.shared_run), "--policy", str(policy)
                )
                self.assertEqual(completed.returncode, expected, completed.stdout)


# ==========================================================================
# Benchmark-qualification separation, historical artifacts, packaging
# ==========================================================================

class QualificationSeparationTests(unittest.TestCase):
    """`check` is generic. Qualification is provenance and nothing more."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._root = tempfile.TemporaryDirectory(prefix="archlens_pv2_qual_")
        root = Path(cls._root.name)
        cls.generic = RunBuilder.build(root / "generic")
        qualified_root = root / "qualified"
        qualified_root.mkdir(parents=True)
        # The registry binds to the exact analyzed scope, so the generic run is
        # built first and its identity is reused.
        cls.qualified = RunBuilder.build(
            qualified_root, cls._registry(cls.generic, qualified_root)
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls._root.cleanup()

    @staticmethod
    def _registry(reference_run: Path, destination: Path) -> Path:
        result = json.loads(
            (reference_run / "analysis.json").read_text(encoding="utf-8")
        )[0]
        record = {
            "record_id": "rec-1",
            "binding": {
                "qualification_profile": QUALIFICATION_PROFILE,
                "subject_key": result["subject_key"],
                "analyzed_commit_sha": result["acquisition"]["analyzed_commit_sha"],
                "analysis_scope_hash": result["analysis_scope_hash"],
                "analysis_scope_hash_version": result["analysis_scope_hash_version"],
            },
            "repository_representativeness": "ADEQUATE",
            "representativeness_reason_codes": ["no_unsupported_first_party_code"],
            "manual_admission_restriction": "none",
            "manual_admission_reason_codes": [],
            "evidence_summary": {
                "supported_first_party_files": 2,
                "supported_first_party_bytes": 200,
                "unsupported_first_party_files": 0,
                "unsupported_first_party_bytes": 0,
                "unsupported_language_groups": [],
                "project_or_service_structure_evidence_count": 1,
                "entrypoint_evidence_count": 1,
                "definition_mapped_code_line_evidence": [],
                "full_tree_evidence_sha256": "0" * 64,
                "evidence_source_ids": ["test"],
                "evidence_bundle_sha256": "1" * 64,
            },
            "adjudication": {
                "mode": "accepted_forensic_audit",
                "record_id": "rec-1",
                "adjudicator_id": "test",
                "adjudicated_at": "2026-08-12T00:00:00Z",
                "decision_source_revision": "test",
            },
            "representativeness_basis": "test",
            "usability_basis": None,
        }
        path = destination / "registry.json"
        path.write_text(json.dumps({
            "registry_schema_version": "1.0.0",
            "source_id": "test.v1",
            "qualification_profile": QUALIFICATION_PROFILE,
            "records": [record],
        }, indent=2), encoding="utf-8")
        return path

    POLICY = (
        _rule("loc", "repository.lines_of_code", "gt", 1),
        _rule("lang", "language.methods_functions", "gte", 0),
        _rule("cx", "callable.cyclomatic_complexity", "gte", 1),
        _rule("cog", "repository.cognitive_complexity_max", "gte", 0),
    )

    def _evaluate(self, run: Path) -> dict:
        return check_module.evaluate_check(
            run, _policy(*self.POLICY), today=date(2026, 8, 13)
        )

    def test_the_generic_run_carries_no_qualification_artifact(self):
        self.assertFalse((self.generic / "benchmark_qualification.json").exists())

    def test_the_qualified_run_carries_one(self):
        self.assertTrue((self.qualified / "benchmark_qualification.json").exists())

    def test_check_works_without_any_qualification_artifact(self):
        result = self._evaluate(self.generic)
        self.assertNotEqual(result["verdict"], check_module.VERDICT_ERROR)
        self.assertTrue(result["findings"])

    def test_qualification_changes_no_finding(self):
        """The whole separation, asserted on the findings themselves."""
        generic = self._evaluate(self.generic)
        qualified = self._evaluate(self.qualified)

        def projection(result):
            return [
                (
                    item["rule_id"], item["status"], item["metric"],
                    item["observed_value"], item["language"], item["path"],
                    item["data_completeness"],
                )
                for item in result["findings"]
            ]

        self.assertEqual(projection(generic), projection(qualified))
        self.assertEqual(generic["verdict"], qualified["verdict"])
        self.assertEqual(generic["exit_code"], qualified["exit_code"])

    def test_qualification_is_reported_as_contextual_provenance_only(self):
        generic = self._evaluate(self.generic)["run"]["benchmark_qualification"]
        qualified = self._evaluate(self.qualified)["run"]["benchmark_qualification"]
        self.assertEqual(generic["qualification_mode"], "not_requested")
        self.assertFalse(generic["qualification_artifact_present"])
        self.assertEqual(qualified["qualification_mode"], "benchmark_qualified")
        self.assertTrue(qualified["qualification_artifact_present"])
        for payload in (generic, qualified):
            self.assertIn("Contextual provenance only", payload["note"])

    def test_no_allowlisted_metric_reads_a_qualification_field(self):
        forbidden = (
            "qualification", "representativeness", "usability", "benchmark",
            "admission",
        )
        for definition in metric_module.METRICS:
            haystack = f"{definition.identifier} {definition.source}".casefold()
            for term in forbidden:
                with self.subTest(metric=definition.identifier, term=term):
                    self.assertNotIn(term, haystack)


class HistoricalCompatibilityTests(unittest.TestCase):
    """Older supported generations evaluate honestly, not falsely."""

    def _policy(self):
        return _policy(
            _rule("loc", "repository.lines_of_code", "gte", 0),
            _rule("cx", "repository.cyclomatic_complexity_max", "gte", 0),
            _rule("cog", "repository.cognitive_complexity_max", "gte", 0),
        )

    def test_every_declared_historical_generation_is_evaluable(self):
        for generation in historical_fixtures.generations():
            with self.subTest(generation=generation):
                run = historical_fixtures.run_for(generation)
                result = check_module.evaluate_check(
                    run, self._policy(), today=date(2026, 8, 13)
                )
                self.assertNotEqual(
                    result["verdict"], check_module.VERDICT_ERROR,
                    f"Artifact {generation} must be evaluable",
                )
                violations = _validate_check_result(result)
                self.assertEqual([str(item) for item in violations], [])

    def test_a_pre_complexity_generation_reports_absent_not_zero(self):
        """A 1.3/1.4 artifact predates the complexity contract entirely."""
        run = historical_fixtures.run_for("1.3.0")
        result = check_module.evaluate_check(
            run, self._policy(), today=date(2026, 8, 13)
        )
        for rule_id in ("cx", "cog"):
            findings = [
                item for item in result["findings"] if item["rule_id"] == rule_id
            ]
            with self.subTest(rule=rule_id):
                self.assertTrue(findings)
                for item in findings:
                    self.assertEqual(
                        item["status"], finding_module.STATUS_NOT_EVALUABLE
                    )
                    self.assertIsNone(item["observed_value"])

    def test_a_core_metric_still_evaluates_on_a_historical_generation(self):
        run = historical_fixtures.run_for("1.3.0")
        result = check_module.evaluate_check(
            run, _policy(_rule("loc", "repository.lines_of_code", "gte", 0)),
            today=date(2026, 8, 13),
        )
        findings = [item for item in result["findings"] if item["rule_id"] == "loc"]
        self.assertTrue(findings)
        self.assertEqual(findings[0]["status"], finding_module.STATUS_VIOLATED)
        self.assertIsInstance(findings[0]["observed_value"], int)

    def test_an_unsupported_generation_is_refused_not_evaluated(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory) / "run"
            shutil.copytree(historical_fixtures.run_for("1.3.0"), run)
            manifest = json.loads(
                (run / "run_manifest.json").read_text(encoding="utf-8")
            )
            manifest["artifact_schema_version"] = "1.0.0"
            (run / "run_manifest.json").write_text(
                json.dumps(manifest, indent=2), encoding="utf-8"
            )
            with self.assertRaises(check_module.CheckFailed) as caught:
                check_module.evaluate_check(run, self._policy())
            self.assertEqual(
                caught.exception.kind, check_module.FAILURE_CONTRACT_INCOMPATIBLE
            )


class PackagingTests(unittest.TestCase):
    """The schemas must resolve as package resources, not as checkout paths."""

    def test_both_new_schemas_are_registered(self):
        from validation.artifact_io.schema_store import SCHEMA_REGISTRY

        for name in ("policy_document_v2", "check_result_output"):
            with self.subTest(schema=name):
                self.assertIn(name, SCHEMA_REGISTRY)

    def test_both_new_schemas_load_from_package_resources(self):
        # The plain names track what the PRODUCER emits, so they are sourced
        # from the producer constants rather than pinned: pinning them here
        # would mean this test failing on every activation for the one reason it
        # does not care about. The historical names ARE pinned, because a
        # superseded contract's version must never move.
        for name, version in (
            ("policy_document_v2", POLICY_DOCUMENT_V2_FORMAT_VERSION),
            ("check_result_output", RATCHET_CHECK_RESULT_FORMAT_VERSION),
            ("policy_document_v2_2_1_historical", "2.1.0"),
            ("policy_document_v2_2_0_historical", "2.0.0"),
            ("check_result_output_1_1_historical", "1.1.0"),
            ("check_result_output_1_0_historical", "1.0.0"),
        ):
            with self.subTest(schema=name):
                from validation.artifact_io.schema_store import schema_version

                document = load_schema(name)
                self.assertEqual(document["$schema"], (
                    "https://json-schema.org/draft/2020-12/schema"
                ))
                self.assertEqual(schema_version(name), version)

    def test_every_packaged_schema_still_passes_the_metaschema(self):
        from validation.artifact_io.schema_store import check_all_schemas

        self.assertEqual([str(item) for item in check_all_schemas()], [])

    def test_the_new_modules_live_in_already_packaged_packages(self):
        """No new top-level namespace; a missing package entry is invisible."""
        import tomllib

        packages = set(tomllib.loads(
            (REPOSITORY / "pyproject.toml").read_text(encoding="utf-8")
        )["tool"]["setuptools"]["packages"])
        for module in (
            "modules.policy.metrics", "modules.policy.document_v2",
            "modules.policy.findings", "modules.policy.check",
            "modules.cli.check_command",
        ):
            with self.subTest(module=module):
                self.assertIn(module.rsplit(".", 1)[0], packages)

    def test_the_schemas_are_exported(self):
        from validation.artifact_io.schema_store import export_schemas

        with tempfile.TemporaryDirectory() as directory:
            written = export_schemas(Path(directory))
            self.assertIn("policy_document-2.0.schema.json", written)
            self.assertIn("check_result-1.0.schema.json", written)


# ==========================================================================
# Mutation-style guards
# ==========================================================================

class EvaluatorGuardMutationTests(RunFixture):
    """Each guard is broken on purpose; the suite must notice.

    A guard nothing exercises is a comment. These tests replace one evaluator
    behaviour with the plausible wrong one and assert the result changes, so a
    future refactor that quietly removes the guard fails here rather than in
    production.
    """

    def test_missing_cannot_be_mutated_into_zero(self):
        run = self.copy_run()
        analysis = self.analysis(run)
        analysis[0]["metrics"]["aggregate"]["loc_status"] = "failed"
        analysis[0]["metrics"]["aggregate"]["lines_of_code"] = None
        self.write_analysis(run, analysis)
        policy = _policy(_rule("r", "repository.lines_of_code", "gt", 5))

        honest = self.evaluate(policy, run)
        self.assertEqual(
            self.statuses(honest, "r"), [finding_module.STATUS_NOT_EVALUABLE]
        )

        with patch.object(
            metric_module, "completeness_of_status",
            lambda status: metric_module.COMPLETENESS_COMPLETE,
        ), patch.object(metric_module, "numeric_value", lambda raw: 0):
            mutated = self.evaluate(policy, run)
        self.assertEqual(
            self.statuses(mutated, "r"),
            [finding_module.STATUS_NOT_EVALUABLE],
            "the consumed-field guard must reject a missing cell before any "
            "numeric coercion seam can manufacture zero",
        )
        self.assertEqual(honest["verdict"], mutated["verdict"])

    def test_treating_not_applicable_as_unavailable_would_fail_the_build(self):
        policy = _policy(_rule(
            "java", "language.lines_of_code", "gt", 0, languages=["Java"]
        ))
        honest = self.evaluate(policy)
        self.assertEqual(honest["exit_code"], check_module.EXIT_PASS)

        with patch.dict(
            metric_module._STATUS_TO_COMPLETENESS,
            {"not_applicable": metric_module.COMPLETENESS_UNAVAILABLE},
        ):
            mutated = self.evaluate(policy)
        self.assertEqual(mutated["exit_code"], check_module.EXIT_VIOLATION)

    def test_dropping_the_partial_stamp_would_lose_the_caveat(self):
        run = self.copy_run()
        analysis = self.analysis(run)
        analysis[0]["metrics"]["aggregate"]["loc_status"] = "partial"
        self.write_analysis(run, analysis)
        policy = _policy(_rule("r", "repository.lines_of_code", "gt", 1))

        honest = next(
            item for item in self.evaluate(policy, run)["findings"]
            if item["rule_id"] == "r"
        )
        self.assertIn("partial observation", honest["message"])

        with patch.dict(
            metric_module._STATUS_TO_COMPLETENESS,
            {"partial": metric_module.COMPLETENESS_COMPLETE},
        ):
            mutated = next(
                item for item in self.evaluate(policy, run)["findings"]
                if item["rule_id"] == "r"
            )
        self.assertNotIn("partial observation", mutated["message"])

    def test_inverting_an_operator_would_change_which_rules_fire(self):
        observed = self.aggregate()["lines_of_code"]
        firing = self.evaluate(
            _policy(_rule("r", "repository.lines_of_code", "gt", observed - 1))
        )
        quiet = self.evaluate(
            _policy(_rule("r", "repository.lines_of_code", "lt", observed - 1))
        )
        self.assertEqual(
            self.statuses(firing, "r"), [finding_module.STATUS_VIOLATED]
        )
        self.assertEqual(self.statuses(quiet, "r"), [])

    def test_the_ledger_is_not_read_when_no_rule_needs_it(self):
        """A repository-scope policy must not pay for a ledger pass."""
        policy = _policy(_rule("r", "repository.lines_of_code", "gt", 1))
        evaluation = check_module.CheckEvaluation(
            self.shared_run, policy, today=date(2026, 8, 13)
        )
        evaluation.open()
        with patch.object(
            check_module.CheckEvaluation, "_load_ledger",
            side_effect=AssertionError("the ledger must not be read"),
            autospec=True,
        ):
            evaluation.evaluate_metrics()
        self.assertFalse(evaluation._ledger_loaded)

    def test_an_evaluator_failure_exits_two_and_never_one(self):
        """"The gate found a problem" and "the gate broke" are not one signal."""
        import dataclasses

        def explode(_context):
            raise RuntimeError("boom")

        broken = tuple(
            dataclasses.replace(rule, evaluate=explode)
            if rule.identifier == "run.integrity_failed" else rule
            for rule in rule_module.RULES
        )
        policy = _policy(
            _rule("r", "repository.lines_of_code", "gt", 1),
            integrity_rules={"run.integrity_failed": "violation"},
        )
        with patch.object(rule_module, "RULES", broken):
            result = self.evaluate(policy)

        self.assertEqual(result["verdict"], check_module.VERDICT_ERROR)
        self.assertEqual(result["exit_code"], check_module.EXIT_ERROR)
        self.assertEqual(
            result["failure_kind"], check_module.FAILURE_EVALUATION_ERROR
        )
        self.assertIn("run.integrity_failed", result["failure_message"])
        errored = [
            item for item in result["findings"]
            if item["status"] == finding_module.STATUS_EVALUATION_ERROR
        ]
        self.assertEqual(len(errored), 1)
        self.assertEqual(errored[0]["evidence"]["error"], "boom")
        violations = _validate_check_result(result)
        self.assertEqual([str(item) for item in violations], [])

        # The rendering must lead with the failure. A reader who scrolled past
        # it would draw the wrong conclusion from everything below.
        from modules.cli import check_command

        rendered = check_command.render_text(result)
        self.assertIn("EVALUATION ERRORS", rendered)
        self.assertNotIn("No rule fired.", rendered)
        self.assertIn("LOCAL — UNPROTECTED", rendered)
        self.assertTrue(
            rendered.replace("LOCAL — UNPROTECTED", "LOCAL - UNPROTECTED").isascii()
        )

    def test_the_schema_report_is_not_computed_when_no_rule_reads_it(self):
        """A metric-only policy must not pay for full bundle validation.

        On the benchmark of record that means validating a 358 MB
        `analysis.json` and 96 repository documents, which dwarfs everything the
        evaluator does.
        """
        policy = _policy(
            _rule("r", "repository.lines_of_code", "gt", 1),
            integrity_rules={"run.integrity_failed": "violation"},
        )
        with patch(
            "modules.cli.validate_command.schema_only_report",
            side_effect=AssertionError("the schema report must not be computed"),
        ):
            result = self.evaluate(policy)
        self.assertIsNone(result["run"]["schema_validation_result"])
        self.assertTrue(result["findings"])

    def test_the_schema_report_is_computed_when_a_rule_reads_it(self):
        policy = _policy(
            _rule("r", "repository.lines_of_code", "gt", 1),
            integrity_rules={"artifact.schema_invalid": "violation"},
        )
        result = self.evaluate(policy)
        self.assertEqual(result["run"]["schema_validation_result"], "schema_valid")

    def test_the_schema_report_rule_set_matches_the_rules_that_read_it(self):
        """A rule added later must not silently stop receiving its report."""
        import ast

        source = (REPOSITORY / "modules" / "policy" / "rules.py").read_text(
            encoding="utf-8"
        )
        tree = ast.parse(source)
        readers: set[str] = set()
        functions = {
            node.name: node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
        }
        for rule in rule_module.RULES:
            node = functions.get(rule.evaluate.__name__)
            self.assertIsNotNone(node, rule.identifier)
            uses = any(
                isinstance(child, ast.Attribute) and child.attr == "schema_report"
                for child in ast.walk(node)
            )
            if uses:
                readers.add(rule.identifier)
        self.assertEqual(readers, set(check_module.SCHEMA_REPORT_RULES))

    def test_a_schema_invalid_bundle_is_still_caught(self):
        """The laziness must not weaken the rule it defers."""
        run = self.copy_run()
        analysis = self.analysis(run)
        analysis[0].pop("subject_key", None)
        self.write_analysis(run, analysis)
        result = self.evaluate(
            _policy(
                _rule("r", "repository.lines_of_code", "gt", 1_000_000),
                integrity_rules={"artifact.schema_invalid": "violation"},
            ),
            run,
        )
        self.assertIn(
            "artifact.schema_invalid",
            {item["rule_id"] for item in result["findings"]},
        )
        self.assertEqual(result["exit_code"], check_module.EXIT_VIOLATION)

    def test_the_ledger_is_read_when_a_cognitive_rule_needs_it(self):
        policy = _policy(_rule("r", "repository.cognitive_complexity_max", "gt", 0))
        evaluation = check_module.CheckEvaluation(
            self.shared_run, policy, today=date(2026, 8, 13)
        )
        evaluation.open()
        evaluation.evaluate_metrics()
        self.assertTrue(evaluation._ledger_loaded)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
