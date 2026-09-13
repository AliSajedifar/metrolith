"""G2-A: the Cognitive Complexity differential-validation harness, pinned.

Synthetic and fixture-driven only. **No real-subject campaign runs from here**,
and a test asserts that the harness has not quietly acquired one.

The two cases the whole gate exists for are
:class:`GenuineZeroVersusMissingCallableTests`. They look identical in the data
-- a matched ArchLens callable with no reference row -- and must never be
classified the same way. Getting that wrong in one direction discards most of
the corpus; getting it wrong in the other manufactures agreement out of silence.
"""

from __future__ import annotations

import unittest

from validation.differential import callable_matching, cognitive_layer2
from validation.differential.cognitive_definitions import (
    CLASSIFICATIONS,
    COGNITIVE_REFERENCE,
    COGNITIVE_REFERENCE_VERSIONS,
    DIVERGENCES,
    LANGUAGES,
    NEVER_ISSUED,
    NOT_COMPARABLE_CELLS,
    all_mappings,
    coverage_report,
    mapping_for,
)
from validation.differential.cognitive_zero_suppression import (
    ABSENCE_INFERRED_ZERO,
    ABSENCE_NOT_EVALUABLE,
    ABSENCE_REFERENCE_MISSING,
    RECORDED_PROBES,
    SuppressionNotProven,
    SuppressionProbe,
    ZERO_OBSERVABLE_DIRECTLY,
    ZERO_SUPPRESSED_PROVEN,
    capability_report,
    interpret_absence,
    prove_suppression,
)
from validation.differential.reference import cognitive_drivers


def _probe(language: str) -> SuppressionProbe:
    return RECORDED_PROBES[language]


def _archlens_row(name: str, line: int, value: int | None, path: str = "a.java"):
    return {
        "relative_path": path,
        "qualified_name": name,
        "start_line": line,
        "end_line": line + 4,
        "cognitive_complexity": value,
        "structural_complexity_status": "complete",
    }


# ---------------------------------------------------------------------------
# Task 1 -- pinned versions
# ---------------------------------------------------------------------------


class PinnedVersionTests(unittest.TestCase):
    def test_every_language_has_a_pinned_reference(self):
        for language in LANGUAGES:
            with self.subTest(language):
                self.assertIn(language, COGNITIVE_REFERENCE)
                self.assertTrue(mapping_for(language).reference_version.strip())

    def test_pins_are_exact_never_ranges(self):
        """A caret range is not a pin. `nodepkgs-g0/package.json` carries
        `^4.2.0`, so the study records the RESOLVED versions instead."""
        for name, version in COGNITIVE_REFERENCE_VERSIONS.items():
            with self.subTest(name):
                for marker in ("^", "~", ">=", "*", "latest"):
                    self.assertNotIn(
                        marker, version, f"{name} pin {version!r} is a range"
                    )

    def test_the_five_reference_tools_are_the_g0_ones(self):
        self.assertEqual(COGNITIVE_REFERENCE_VERSIONS["pmd"], "7.7.0")
        self.assertEqual(COGNITIVE_REFERENCE_VERSIONS["gocognit"], "1.2.1")
        self.assertEqual(
            COGNITIVE_REFERENCE_VERSIONS["eslint-plugin-sonarjs"], "4.2.0"
        )
        self.assertEqual(
            COGNITIVE_REFERENCE_VERSIONS["cognitive_complexity"], "1.3.0"
        )


# ---------------------------------------------------------------------------
# Task 2 -- the Go toolchain decision
# ---------------------------------------------------------------------------


class GoToolchainTests(unittest.TestCase):
    """Silent escalation must REFUSE the run, not warn about it."""

    def test_two_toolchains_are_pinned_and_different(self):
        self.assertEqual(cognitive_drivers.GO_MEASUREMENT_TOOLCHAIN, "1.23.12")
        self.assertEqual(cognitive_drivers.GO_GOCOGNIT_BUILD_TOOLCHAIN, "1.25.12")
        self.assertNotEqual(
            cognitive_drivers.GO_MEASUREMENT_TOOLCHAIN,
            cognitive_drivers.GO_GOCOGNIT_BUILD_TOOLCHAIN,
            "the escalation is real; recording one toolchain would hide it",
        )

    def test_gotoolchain_auto_is_refused(self):
        for value in ("auto", "path", "", None):
            with self.subTest(value):
                with self.assertRaises(cognitive_drivers.ToolchainRefused):
                    cognitive_drivers.refuse_silent_escalation(
                        {"GOTOOLCHAIN": value, "GOPROXY": "off"}
                    )

    def test_a_non_exact_toolchain_is_refused(self):
        with self.assertRaises(cognitive_drivers.ToolchainRefused):
            cognitive_drivers.refuse_silent_escalation(
                {"GOTOOLCHAIN": "1.25.12", "GOPROXY": "off"}
            )

    def test_a_reachable_proxy_is_refused(self):
        with self.assertRaises(cognitive_drivers.ToolchainRefused):
            cognitive_drivers.refuse_silent_escalation(
                {"GOTOOLCHAIN": "go1.25.12", "GOPROXY": "https://proxy.golang.org"}
            )

    def test_the_pinned_build_environment_passes_its_own_check(self):
        environment = cognitive_drivers.go_build_environment()
        self.assertEqual(environment["GOTOOLCHAIN"], "go1.25.12")
        self.assertEqual(environment["GOPROXY"], "off")

    def test_provenance_is_read_from_the_binary_not_the_environment(self):
        """An env var states an intention; embedded build info states what
        happened. Only the second is evidence."""
        if not cognitive_drivers.GOCOGNIT.is_file():
            self.skipTest("gocognit is not provisioned on this host")
        provenance = cognitive_drivers.require_pinned_gocognit()
        self.assertEqual(provenance.build_toolchain, "go1.25.12")
        self.assertEqual(provenance.module_version, "v1.2.1")
        self.assertEqual(
            provenance.dependencies.get("golang.org/x/tools"), "v0.42.0"
        )
        self.assertTrue(provenance.matches_pins, provenance.detail)


# ---------------------------------------------------------------------------
# Task 3 -- zero-suppression semantics
# ---------------------------------------------------------------------------


class ZeroSuppressionVerdictTests(unittest.TestCase):
    def test_measured_verdicts(self):
        expected = {
            "Go": ZERO_OBSERVABLE_DIRECTLY,
            "Python": ZERO_OBSERVABLE_DIRECTLY,
            "Java": ZERO_SUPPRESSED_PROVEN,
            "JavaScript": ZERO_SUPPRESSED_PROVEN,
            "TypeScript": ZERO_SUPPRESSED_PROVEN,
        }
        for language, verdict in expected.items():
            with self.subTest(language):
                self.assertEqual(_probe(language).verdict, verdict)

    def test_three_references_suppress_zero_not_four(self):
        """G0 said three, G1-A N3 said four, G2-A measured three. gocognit
        reports a zero under `-over -1`, which N3 had not tried."""
        report = capability_report()
        self.assertEqual(
            sorted(report["zero_suppressed_proven"]),
            ["Java", "JavaScript", "TypeScript"],
        )
        self.assertEqual(
            sorted(report["zero_observable_directly"]), ["Go", "Python"]
        )

    def test_a_probe_without_its_control_proves_nothing(self):
        """The control is the probe's validity check: without it, an absent
        zero is indistinguishable from a tool that never ran."""
        broken = SuppressionProbe(
            language="Java", reference="PMD", reference_version="7.7.0",
            zero_callable_value=None, control_callable_value=None,
            mechanism="x", invocation="y",
        )
        self.assertNotEqual(broken.verdict, ZERO_SUPPRESSED_PROVEN)
        with self.assertRaises(SuppressionNotProven):
            prove_suppression(broken)

    def test_drift_from_the_recorded_verdict_is_refused(self):
        drifted = SuppressionProbe(
            language="Java", reference="PMD", reference_version="7.7.0",
            zero_callable_value=0, control_callable_value=1,
            mechanism="x", invocation="y",
        )
        with self.assertRaises(SuppressionNotProven):
            prove_suppression(drifted)

    def test_a_matching_probe_is_licensed(self):
        for language in LANGUAGES:
            with self.subTest(language):
                self.assertIs(prove_suppression(_probe(language)), _probe(language))

    def test_every_suppressing_language_has_an_enumerator(self):
        from validation.differential.cognitive_zero_suppression import (
            ENUMERATORS, NO_ENUMERATOR_NEEDED,
        )
        for language in LANGUAGES:
            with self.subTest(language):
                if _probe(language).verdict == ZERO_SUPPRESSED_PROVEN:
                    self.assertIn(language, ENUMERATORS)
                else:
                    self.assertIn(language, NO_ENUMERATOR_NEEDED)


class GenuineZeroVersusMissingCallableTests(unittest.TestCase):
    """The two cases that look identical in the data. THE gate requirement."""

    def test_genuine_zero_is_inferred_when_enumerated_and_suppression_proven(self):
        verdict = interpret_absence(
            language="Java", identity_established=True,
            suppression=_probe("Java"),
        )
        self.assertEqual(verdict.verdict, ABSENCE_INFERRED_ZERO)
        self.assertEqual(verdict.reference_value, 0)

    def test_genuinely_missing_callable_is_never_a_zero(self):
        verdict = interpret_absence(
            language="Java", identity_established=False,
            suppression=_probe("Java"),
        )
        self.assertEqual(verdict.verdict, ABSENCE_REFERENCE_MISSING)
        self.assertIsNone(verdict.reference_value)

    def test_absence_without_a_suppression_proof_is_never_a_zero(self):
        verdict = interpret_absence(
            language="Java", identity_established=True, suppression=None,
        )
        self.assertEqual(verdict.verdict, ABSENCE_NOT_EVALUABLE)
        self.assertIsNone(verdict.reference_value)

    def test_absence_from_a_zero_reporting_tool_is_never_a_zero(self):
        """gocognit prints zeros. An absent row is a callable it never saw, and
        reading it as 0 would invent a measurement the tool would have printed."""
        for language in ("Go", "Python"):
            with self.subTest(language):
                verdict = interpret_absence(
                    language=language, identity_established=True,
                    suppression=_probe(language),
                )
                self.assertEqual(verdict.verdict, ABSENCE_REFERENCE_MISSING)
                self.assertIsNone(verdict.reference_value)

    def test_a_proof_cannot_license_another_language(self):
        with self.assertRaises(SuppressionNotProven):
            interpret_absence(
                language="JavaScript", identity_established=True,
                suppression=_probe("Java"),
            )


# ---------------------------------------------------------------------------
# Task 4 -- identity-only matching
# ---------------------------------------------------------------------------


class IdentityOnlyMatchingTests(unittest.TestCase):
    def test_the_existing_matcher_is_reused_unchanged(self):
        self.assertIs(
            cognitive_layer2.callable_matching, callable_matching,
            "G2-A must reuse the C4 identity matcher, not fork it",
        )

    def test_metric_values_cannot_reach_the_matcher(self):
        """The matcher's key carries no metric field, in either direction."""
        key = callable_matching.key_from_row(
            {
                "relative_path": "a.java", "qualified_name": "f",
                "start_line": 1, "end_line": 3,
                "cognitive_complexity": 7, "value": 9,
            }
        )
        self.assertNotIn("cognitive_complexity", key.__dict__)
        self.assertNotIn("value", key.__dict__)

    def test_pairing_is_unchanged_when_every_value_changes(self):
        """The decisive property: identical identity, wildly different values,
        identical pairing. A matcher that could see values could be tuned."""
        left = [_archlens_row("f", 10, 1), _archlens_row("g", 20, 2)]
        right = [
            {"relative_path": "a.java", "qualified_name": "f",
             "start_line": 10, "end_line": 14, "value": 1},
            {"relative_path": "a.java", "qualified_name": "g",
             "start_line": 20, "end_line": 24, "value": 2},
        ]
        baseline = callable_matching.match_callables(left, right)

        skewed_left = [_archlens_row("f", 10, 99), _archlens_row("g", 20, 0)]
        skewed_right = [dict(row, value=1000) for row in right]
        skewed = callable_matching.match_callables(skewed_left, skewed_right)

        self.assertEqual(baseline.summary(), skewed.summary())
        self.assertEqual(
            [(a.qualified_name, b.qualified_name) for a, b, _ in baseline.matched],
            [(a.qualified_name, b.qualified_name) for a, b, _ in skewed.matched],
        )

    def test_an_enumeration_derived_from_metric_rows_is_refused(self):
        rows = [{"relative_path": "a.java", "qualified_name": "f",
                 "start_line": 1, "value": 3}]
        with self.assertRaises(cognitive_layer2.IdentityLeak):
            cognitive_layer2.build_reference_side(
                metric_rows=rows, enumerated_rows=list(rows)
            )

    def test_enumeration_carries_callables_the_metric_run_omitted(self):
        combined = cognitive_layer2.build_reference_side(
            metric_rows=[
                {"relative_path": "a.java", "qualified_name": "nonZero",
                 "start_line": 14, "value": 1},
            ],
            enumerated_rows=[
                {"relative_path": "a.java", "qualified_name": "zero", "start_line": 8},
                {"relative_path": "a.java", "qualified_name": "nonZero", "start_line": 14},
            ],
        )
        self.assertEqual(len(combined), 2)
        by_name = {row["qualified_name"]: row for row in combined}
        self.assertFalse(by_name["zero"]["value_present_in_metric_output"])
        self.assertTrue(by_name["nonZero"]["value_present_in_metric_output"])


# ---------------------------------------------------------------------------
# Task 5 -- DefinitionMappings
# ---------------------------------------------------------------------------


class DefinitionMappingTests(unittest.TestCase):
    def test_every_language_is_mapped(self):
        report = coverage_report()
        self.assertTrue(report["every_language_mapped"])
        self.assertEqual(report["cells"], len(LANGUAGES))

    def test_every_required_field_is_stated(self):
        required = (
            "callable_population", "zero_suppression_behaviour",
            "nesting_semantics", "boolean_sequence_semantics",
            "recursion_semantics", "nested_callable_treatment",
        )
        for mapping in all_mappings():
            for name in required:
                with self.subTest(f"{mapping.language}/{name}"):
                    self.assertTrue(
                        getattr(mapping, name).strip(),
                        f"{name} must be stated, never blank",
                    )
            self.assertIn(mapping.classification, CLASSIFICATIONS)
            self.assertTrue(mapping.known_divergences)
            self.assertTrue(mapping.limitations)

    def test_the_divergence_register_is_not_contiguous_and_says_so(self):
        """D20 and D21 were never issued. Claiming 'D1-D27' without recording
        the holes asserts 27 divergences where 25 exist."""
        self.assertEqual(NEVER_ISSUED, ("D20", "D21"))
        for name in NEVER_ISSUED:
            self.assertNotIn(name, DIVERGENCES)
        self.assertEqual(len(DIVERGENCES), 31, "25 issued through G2-A, plus D28-D33 found by G2-B")
        numbers = sorted(int(name[1:]) for name in DIVERGENCES)
        self.assertEqual(numbers[0], 1)
        self.assertEqual(numbers[-1], 33)

    def test_every_issued_divergence_is_cited_by_some_mapping(self):
        report = coverage_report()
        self.assertEqual(
            report["divergences_not_cited_by_any_mapping"], [],
            "an issued divergence no mapping cites is a divergence nobody will "
            "classify when it appears",
        )

    def test_a_mapping_cannot_cite_a_divergence_that_was_never_issued(self):
        from validation.differential.cognitive_definitions import CognitiveMapping
        with self.assertRaises(ValueError):
            CognitiveMapping(
                language="Go", reference="x", reference_version="1",
                unit_compared="x", callable_population="x",
                zero_suppression_behaviour="x", nesting_semantics="x",
                boolean_sequence_semantics="x", recursion_semantics="x",
                nested_callable_treatment="x",
                classification=CLASSIFICATIONS[0],
                known_divergences=("D20",), limitations=("x",),
            )

    def test_n2_and_n3_limitations_are_recorded_where_they_bite(self):
        java = mapping_for("Java")
        self.assertTrue(any("N2" in item for item in java.limitations))
        for language in ("Java", "JavaScript", "TypeScript"):
            with self.subTest(language):
                self.assertTrue(
                    any("N3" in item for item in mapping_for(language).limitations)
                )

    def test_python_records_that_it_is_not_parser_independent(self):
        limitations = " ".join(mapping_for("Python").limitations)
        self.assertIn("ast", limitations)
        self.assertIn("independent", limitations)

    def test_the_non_comparable_cells_are_named(self):
        constructs = {item["construct"] for item in NOT_COMPARABLE_CELLS}
        self.assertTrue(any("guard" in item.lower() for item in constructs))
        self.assertTrue(any("switch" in item.lower() for item in constructs))
        self.assertTrue(any("match" in item.lower() for item in constructs))

    def test_no_mapping_claims_sonar_compatibility(self):
        """G0's naming discipline, enforced mechanically."""
        for mapping in all_mappings():
            blob = " ".join(str(value) for value in mapping.as_dict().values()).lower()
            with self.subTest(mapping.language):
                self.assertNotIn("sonar-compatible", blob)
                self.assertNotIn("sonar compatible", blob)

    def test_there_is_no_primary_adapter_tier(self):
        self.assertIn("NO primary independent adapter", coverage_report()["single_tier_note"])


# ---------------------------------------------------------------------------
# Task 6 -- the raw-result format
# ---------------------------------------------------------------------------


class RawResultFormatTests(unittest.TestCase):
    def test_the_eight_distinctions_are_named(self):
        self.assertEqual(
            set(cognitive_layer2.RESULT_STATES),
            {
                "matched", "archlens_only", "reference_only", "ambiguous",
                "reported_zero", "inferred_suppressed_zero", "not_evaluable",
                "numerical_disagreement",
            },
        )

    def test_every_state_is_reachable_from_a_real_observation(self):
        """A vocabulary nothing can produce is documentation, not a format."""
        produced = {item.state for item in self._all_states()}
        self.assertEqual(produced, set(cognitive_layer2.RESULT_STATES))

    def _all_states(self):
        java_rows = [
            _archlens_row("agree", 10, 3),
            _archlens_row("disagree", 20, 5),
            _archlens_row("suppressedZero", 30, 0),
            _archlens_row("archlensOnly", 40, 2),
            _archlens_row("noValue", 50, None),
            _archlens_row("dup", 60, 1),
        ]
        metric = [
            {"relative_path": "a.java", "qualified_name": "agree",
             "start_line": 10, "end_line": 14, "value": 3},
            {"relative_path": "a.java", "qualified_name": "disagree",
             "start_line": 20, "end_line": 24, "value": 9},
            {"relative_path": "a.java", "qualified_name": "noValue",
             "start_line": 50, "end_line": 54, "value": 0},
            {"relative_path": "a.java", "qualified_name": "referenceOnly",
             "start_line": 70, "end_line": 74, "value": 4},
            {"relative_path": "a.java", "qualified_name": "dup",
             "start_line": 60, "end_line": 64, "value": 1},
            {"relative_path": "a.java", "qualified_name": "dup",
             "start_line": 61, "end_line": 63, "value": 2},
        ]
        enumerated = [
            {key: value for key, value in row.items() if key != "value"}
            for row in metric
        ] + [
            {"relative_path": "a.java", "qualified_name": "suppressedZero",
             "start_line": 30, "end_line": 34},
        ]
        reference = cognitive_layer2.build_reference_side(
            metric_rows=metric, enumerated_rows=enumerated
        )
        match = callable_matching.match_callables(java_rows, reference)
        observations = cognitive_layer2.compare_pairs(
            subject_key="synthetic", language="Java", match=match,
            archlens_rows=java_rows, reference_rows=reference,
            suppression=_probe("Java"),
        )
        # `reported_zero` needs a tool that prints zeros, so it comes from Go.
        go_rows = [
            {"relative_path": "a.go", "qualified_name": "Zero",
             "start_line": 5, "end_line": 9, "cognitive_complexity": 0,
             "structural_complexity_status": "complete"},
        ]
        go_reference = [
            {"relative_path": "a.go", "qualified_name": "Zero",
             "start_line": 5, "end_line": 9, "value": 0},
        ]
        observations.extend(
            cognitive_layer2.compare_pairs(
                subject_key="synthetic", language="Go",
                match=callable_matching.match_callables(go_rows, go_reference),
                archlens_rows=go_rows, reference_rows=go_reference,
                suppression=_probe("Go"),
            )
        )
        return observations

    def test_reported_zero_and_inferred_zero_are_different_records(self):
        states = {item.qualified_name: item for item in self._all_states()}
        inferred = states["suppressedZero"]
        self.assertEqual(inferred.state, "inferred_suppressed_zero")
        self.assertEqual(inferred.reference_value, 0)
        self.assertEqual(
            inferred.reference_value_source,
            cognitive_layer2.VALUE_INFERRED_SUPPRESSED_ZERO,
        )
        reported = states["Zero"]
        self.assertEqual(reported.state, "reported_zero")
        self.assertEqual(reported.reference_value_source, cognitive_layer2.VALUE_REPORTED)
        self.assertNotEqual(inferred.state, reported.state)

    def test_an_absent_value_may_never_carry_a_number(self):
        with self.assertRaises(ValueError):
            cognitive_layer2.CognitiveObservation(
                subject_key="s", language="Java", reference_name="PMD",
                reference_version="7.7.0", relative_path="a.java",
                qualified_name="f", start_line=1, end_line=2,
                pairing=cognitive_layer2.PAIRING_MATCHED, match_tier="t",
                archlens_value=1, reference_value=0,
                reference_value_source=cognitive_layer2.VALUE_ABSENT,
                difference=None, outcome=cognitive_layer2.OUTCOME_NOT_EVALUABLE,
                cause=cognitive_layer2.CAUSE_NONE,
            )

    def test_an_inferred_zero_that_is_not_zero_is_refused(self):
        with self.assertRaises(ValueError):
            cognitive_layer2.CognitiveObservation(
                subject_key="s", language="Java", reference_name="PMD",
                reference_version="7.7.0", relative_path="a.java",
                qualified_name="f", start_line=1, end_line=2,
                pairing=cognitive_layer2.PAIRING_MATCHED, match_tier="t",
                archlens_value=1, reference_value=4,
                reference_value_source=cognitive_layer2.VALUE_INFERRED_SUPPRESSED_ZERO,
                difference=3, outcome=cognitive_layer2.OUTCOME_DISAGREEMENT,
                cause=cognitive_layer2.CAUSE_UNRESOLVED,
            )

    def test_a_disagreement_must_carry_a_cause(self):
        with self.assertRaises(ValueError):
            cognitive_layer2.CognitiveObservation(
                subject_key="s", language="Java", reference_name="PMD",
                reference_version="7.7.0", relative_path="a.java",
                qualified_name="f", start_line=1, end_line=2,
                pairing=cognitive_layer2.PAIRING_MATCHED, match_tier="t",
                archlens_value=1, reference_value=4,
                reference_value_source=cognitive_layer2.VALUE_REPORTED,
                difference=3, outcome=cognitive_layer2.OUTCOME_DISAGREEMENT,
                cause=cognitive_layer2.CAUSE_NONE,
            )

    def test_an_unsupported_match_is_not_comparable_and_discards_the_zero(self):
        rows = [{"relative_path": "a.py", "qualified_name": "handler",
                 "start_line": 1, "end_line": 9, "cognitive_complexity": 4,
                 "structural_complexity_status": "complete"}]
        reference = [{"relative_path": "a.py", "qualified_name": "handler",
                      "start_line": 1, "end_line": 9, "value": 0,
                      "contains_unsupported_match": True}]
        observations = cognitive_layer2.compare_pairs(
            subject_key="s", language="Python",
            match=callable_matching.match_callables(rows, reference),
            archlens_rows=rows, reference_rows=reference,
            suppression=_probe("Python"),
        )
        self.assertEqual(len(observations), 1)
        self.assertEqual(observations[0].outcome, cognitive_layer2.OUTCOME_NOT_COMPARABLE)
        self.assertIsNone(observations[0].reference_value)
        self.assertIn("D12", observations[0].reason)

    def test_summary_reports_counts_and_no_percentage(self):
        summary = cognitive_layer2.summarize(self._all_states())
        self.assertNotIn("%", str(summary))
        self.assertEqual(set(summary["by_state"]), set(cognitive_layer2.RESULT_STATES))
        self.assertEqual(sum(summary["by_state"].values()), summary["observations"])
        # The only permitted mention of accuracy is the rule forbidding it.
        self.assertIn("No accuracy percentage", summary["reporting_rule"])

    def test_raw_document_round_trips(self):
        import json
        import tempfile
        from pathlib import Path

        observations = self._all_states()
        population = cognitive_layer2.account(
            callable_matching.MatchResult(), [], [], [], observations
        )
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "raw.json"
            cognitive_layer2.persist_raw(observations, population, destination)
            payload = json.loads(destination.read_text(encoding="utf-8"))
        self.assertEqual(payload["stage"], "raw_observations_before_adjudication")
        self.assertEqual(payload["result_states"], list(cognitive_layer2.RESULT_STATES))
        self.assertEqual(len(payload["observations"]), len(observations))
        self.assertTrue(all("state" in item for item in payload["observations"]))


# ---------------------------------------------------------------------------
# Task 7 -- scope boundary
# ---------------------------------------------------------------------------


class ScopeBoundaryTests(unittest.TestCase):
    def test_an_empty_scope_is_refused_never_reported_as_zero_rows(self):
        with self.assertRaises(cognitive_drivers.ScopeRefused):
            cognitive_drivers.resolve_scope(
                "Go", cognitive_drivers.PROBE_ROOT, []
            )

    def test_a_wrong_language_scope_is_refused(self):
        with self.assertRaises(cognitive_drivers.ScopeRefused):
            cognitive_drivers.resolve_scope(
                "Go", cognitive_drivers.PROBE_ROOT, ["zeroprobe.py"]
            )

    def test_the_record_format_module_holds_no_campaign(self):
        """**Superseded in scope, kept as an invariant.**

        At G2-A this asserted that no real-subject campaign existed anywhere.
        G2-B was then authorized and the campaign lives in
        `cognitive_campaign`. What survives is the separation that made the
        original assertion checkable: `cognitive_layer2` is the record FORMAT
        and comparison logic, with no subject list, no run root and no entry
        point that reaches a repository. A format that can start a campaign is
        a format nobody can exercise in isolation.
        """
        for name in (
            "run_campaign", "INITIAL_SUBJECTS", "DEFAULT_SUBJECT_ROOT",
            "DEFAULT_RUN_ROOT",
        ):
            self.assertFalse(
                hasattr(cognitive_layer2, name),
                f"{name} belongs to the campaign driver, not the record format",
            )

    def test_the_campaign_never_reads_the_c4_evidence_root(self):
        """C4/CX evidence must stay untouched, and a 1.9 run carries no
        cognitive column at all -- pointing here would produce an all-null
        table that reads exactly like a clean result."""
        from validation.differential import cognitive_campaign

        self.assertNotIn("l2c4", str(cognitive_campaign.DEFAULT_RUN_ROOT))
        self.assertEqual(cognitive_campaign.DEFAULT_RUN_ROOT.name, "l2cg")

    def test_the_probe_corpus_carries_a_zero_and_a_control_in_one_file(self):
        for language, (filename, zero, control) in (
            cognitive_drivers.PROBE_CALLABLES.items()
        ):
            with self.subTest(language):
                source = (cognitive_drivers.PROBE_ROOT / filename).read_text(
                    encoding="utf-8"
                )
                self.assertIn(zero, source)
                self.assertIn(control, source)


# ---------------------------------------------------------------------------
# Task 7 -- the drivers, executed against the synthetic probes
# ---------------------------------------------------------------------------


def _provisioned() -> bool:
    return cognitive_drivers.availability()["all_provisioned"]


@unittest.skipUnless(_provisioned(), "the CG reference environment is not provisioned")
class LiveReferenceHarnessTests(unittest.TestCase):
    """The drivers actually run, and reproduce the recorded zero behaviour.

    Synthetic probes only. These are what make the recorded verdicts a
    measurement rather than a memory: a reference whose zero handling changed
    under an unchanged version string fails here rather than silently altering
    what every absence in a future campaign means.
    """

    @classmethod
    def setUpClass(cls):
        import tempfile
        from pathlib import Path

        cls._directory = tempfile.TemporaryDirectory()
        cls.work = Path(cls._directory.name)

    @classmethod
    def tearDownClass(cls):
        cls._directory.cleanup()

    def test_every_reference_reproduces_its_recorded_zero_verdict(self):
        for language in LANGUAGES:
            with self.subTest(language):
                live = cognitive_drivers.probe_zero_suppression(
                    language, self.work / f"probe_{language}"
                )
                self.assertTrue(
                    live.control_reported,
                    "the non-zero control must be reported, or the probe proves "
                    "nothing about the absent zero",
                )
                self.assertEqual(live.verdict, _probe(language).verdict)
                prove_suppression(live)

    def test_gocognit_reports_a_genuine_zero_under_a_negative_threshold(self):
        """The G2-A correction, executed. `-over N` is strictly-greater-than, so
        the default hides zeros; G1-A's N3 concluded gocognit cannot report one
        at all, and it can."""
        run = cognitive_drivers.run_gocognit(
            cognitive_drivers.PROBE_ROOT, ["zeroprobe.go"]
        )
        values = {row["qualified_name"]: row["value"] for row in run.rows}
        self.assertEqual(values.get("ZeroCallable"), 0)
        self.assertEqual(values.get("NonZeroCallable"), 1)

    def test_pmd_enumerates_the_callable_its_metric_run_suppresses(self):
        """The Java protocol end to end: the enumeration carries the zero the
        cognitive rule refuses to print."""
        run = cognitive_drivers.run_pmd(
            cognitive_drivers.PROBE_ROOT, ["ZeroProbe.java"], self.work / "pmd"
        )
        measured = {row["qualified_name"] for row in run.rows}
        enumerated = {row["qualified_name"] for row in run.enumerated}
        self.assertEqual(measured, {"nonZeroCallable"})
        self.assertEqual(enumerated, {"zeroCallable", "nonZeroCallable"})
        self.assertTrue(enumerated - measured, "nothing to infer a zero from")

    def test_sonarjs_enumerates_the_callable_its_metric_run_suppresses(self):
        for language, filename in (
            ("JavaScript", "zeroprobe.js"), ("TypeScript", "zeroprobe.ts")
        ):
            with self.subTest(language):
                run = cognitive_drivers.run_sonarjs(
                    language, cognitive_drivers.PROBE_ROOT, [filename],
                    self.work / f"sonar_{language}",
                )
                self.assertEqual(len(run.rows), 1, "only the control scores >= 1")
                self.assertEqual(len(run.enumerated), 2, "both callables enumerated")

    def test_the_python_reference_returns_a_zero_rather_than_omitting_it(self):
        run = cognitive_drivers.run_python_reference(
            cognitive_drivers.PROBE_ROOT, ["zeroprobe.py"], self.work / "py"
        )
        values = {row["qualified_name"]: row["value"] for row in run.rows}
        self.assertEqual(values.get("zero_callable"), 0)
        self.assertEqual(values.get("non_zero_callable"), 1)
        self.assertEqual(run.enumerated, [], "a zero-reporting tool needs no enumerator")

    def test_the_java_protocol_produces_an_inferred_zero_end_to_end(self):
        """Driver output, real matcher, real protocol: the one path that turns
        silence into a number."""
        run = cognitive_drivers.run_pmd(
            cognitive_drivers.PROBE_ROOT, ["ZeroProbe.java"], self.work / "pmd_e2e"
        )
        reference = cognitive_layer2.build_reference_side(
            metric_rows=run.rows, enumerated_rows=run.enumerated
        )
        archlens = [
            {
                "relative_path": "ZeroProbe.java", "qualified_name": "zeroCallable",
                "signature_discriminator": "int",
                "start_line": 7, "end_line": 11, "cognitive_complexity": 0,
                "structural_complexity_status": "complete",
            },
            {
                "relative_path": "ZeroProbe.java", "qualified_name": "nonZeroCallable",
                "signature_discriminator": "int",
                "start_line": 13, "end_line": 18, "cognitive_complexity": 1,
                "structural_complexity_status": "complete",
            },
        ]
        live = cognitive_drivers.probe_zero_suppression(
            "Java", self.work / "probe_e2e"
        )
        observations = cognitive_layer2.compare_pairs(
            subject_key="probe", language="Java",
            match=callable_matching.match_callables(archlens, reference),
            archlens_rows=archlens, reference_rows=reference,
            suppression=prove_suppression(live),
        )
        by_name = {item.qualified_name: item for item in observations}
        self.assertEqual(by_name["zeroCallable"].state, "inferred_suppressed_zero")
        self.assertEqual(by_name["zeroCallable"].reference_value, 0)
        self.assertEqual(by_name["zeroCallable"].difference, 0)
        self.assertEqual(by_name["nonZeroCallable"].state, "matched")
        self.assertEqual(
            by_name["nonZeroCallable"].reference_value_source,
            cognitive_layer2.VALUE_REPORTED,
        )

    def test_a_callable_absent_from_the_enumeration_stays_uninferred(self):
        """The counterpart, on real driver output: an ArchLens callable PMD's
        own parser never enumerated must not become a zero."""
        run = cognitive_drivers.run_pmd(
            cognitive_drivers.PROBE_ROOT, ["ZeroProbe.java"], self.work / "pmd_miss"
        )
        reference = cognitive_layer2.build_reference_side(
            metric_rows=run.rows, enumerated_rows=run.enumerated
        )
        archlens = [
            {
                "relative_path": "ZeroProbe.java", "qualified_name": "neverSeen",
                "signature_discriminator": "int",
                "start_line": 40, "end_line": 44, "cognitive_complexity": 0,
                "structural_complexity_status": "complete",
            },
        ]
        observations = cognitive_layer2.compare_pairs(
            subject_key="probe", language="Java",
            match=callable_matching.match_callables(archlens, reference),
            archlens_rows=archlens, reference_rows=reference,
            suppression=_probe("Java"),
        )
        self.assertEqual(observations[0].state, "archlens_only")
        self.assertIsNone(observations[0].reference_value)
        self.assertEqual(
            observations[0].reference_value_source, cognitive_layer2.VALUE_ABSENT
        )


if __name__ == "__main__":
    unittest.main()
