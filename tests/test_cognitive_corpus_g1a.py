"""G1-A: the frozen Cognitive Complexity rule table and its micro-corpus.

**These tests check the corpus itself, not ArchLens.** They were written at
G1-A, when no production implementation existed; G1-B added computation and
G1-C added persistence, so the one scope guard they carry has moved with the
gate while everything else here is unchanged. Three things are checked:

1. **The arithmetic closes.** Every callable's listed contributions sum to its
   stated total under the frozen table's own formula. A corpus whose expected
   values do not follow from its own rules would let two offsetting authoring
   errors pass, which is exactly how a synthetic corpus stops being a
   specification.
2. **The citations are real.** Every contribution names a line, and that line
   must actually contain the text the contribution claims. This is what makes a
   hand-authored expectation auditable by someone who never runs a tool.
3. **The coverage is complete.** Every rule the frozen table marks applicable to
   a language is exercised by at least one case in that language, and every
   rule cited by a case exists in the table.

The whole point of authoring order is preserved by construction: these
expectations were written from the rule table before any reference tool was run
against the corpus, and no ArchLens cognitive output existed to consult.
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
CORPUS = REPOSITORY_ROOT / "validation/differential/corpus/cognitive"
RULE_TABLE = (
    REPOSITORY_ROOT / "validation/complexity_g1a_20260811/FROZEN_RULE_TABLE.md"
)

LANGUAGES = ("go", "java", "javascript", "typescript", "python")

#: How each kind contributes. The formula is the frozen table's, restated here
#: so the test fails if the corpus drifts from it rather than silently agreeing.
KIND_CONTRIBUTION = {
    "structural": lambda nesting: 1 + nesting,
    "flat": lambda nesting: 1,
    "nesting_only": lambda nesting: 0,
    "zero": lambda nesting: 0,
    # Corpus-only. `not_fired` names a rule that was evaluated at that site and
    # did NOT fire -- the way a negative case is pinned explicitly instead of
    # being inferred from an absence.
    "not_fired": lambda nesting: 0,
}


def load(language: str) -> dict:
    return json.loads(
        (CORPUS / language / "expectations.json").read_text(encoding="utf-8")
    )


def source_lines(language: str, filename: str) -> list[str]:
    return (CORPUS / language / filename).read_text(encoding="utf-8").splitlines()


def applicability() -> dict[str, set[str]]:
    """Parse the applicability matrix out of the frozen rule table.

    Read from the table rather than restated here: a second copy of the matrix
    would be a second thing to keep in step, and the one that drifts is always
    the copy nobody reads.
    """
    text = RULE_TABLE.read_text(encoding="utf-8")
    header = "| Rule | Java | Go | JavaScript | TypeScript | Python |"
    start = text.index(header)
    rows = text[start:].splitlines()[2:]
    order = ("java", "go", "javascript", "typescript", "python")
    found: dict[str, set[str]] = {name: set() for name in order}
    for row in rows:
        if not row.startswith("| `"):
            break
        cells = [cell.strip() for cell in row.strip("|").split("|")]
        rule = cells[0].strip("`")
        for language, cell in zip(order, cells[1:6]):
            if cell.startswith("•"):
                found[language].add(rule)
    return found


def declared_rules() -> set[str]:
    text = RULE_TABLE.read_text(encoding="utf-8")
    return set(re.findall(r"\| `([SFNZB]-[A-Z]+)` \|", text))


class RuleTableTests(unittest.TestCase):
    def test_the_table_declares_the_rules_the_matrix_uses(self):
        matrix_rules = set()
        for rules in applicability().values():
            matrix_rules |= rules
        self.assertTrue(matrix_rules)
        self.assertEqual(matrix_rules - declared_rules(), set())

    def test_every_declared_rule_is_applicable_somewhere(self):
        applicable = set()
        for rules in applicability().values():
            applicable |= rules
        orphans = declared_rules() - applicable
        self.assertEqual(
            orphans, set(), f"declared but applicable to no language: {orphans}"
        )

    def test_the_table_is_not_a_contract_document(self):
        """G1-A freezes rules. It does not create Complexity Contract 2.0.0."""
        text = RULE_TABLE.read_text(encoding="utf-8")
        self.assertIn("NOT a contract document", text)
        self.assertNotIn("COMPLEXITY_CONTRACT_V2", text)

    def test_the_gate_boundary_as_it_stands_after_g1_c(self):
        """This guard has moved twice, deliberately.

        Through G1-A no production module could mention cognitive complexity;
        G1-B authorized computation; G1-C authorized persistence, Complexity
        Contract 2.0.0 and Artifact Schema 1.10.0. What it pins now is the line
        that still holds -- the value is measured and stored, and nothing yet
        reports, diffs or scores it.
        """
        from modules.callable_ledger import AGGREGATE_FIELDS, CALLABLE_COLUMNS
        from modules.callable_analysis.model import CallableRecord

        self.assertIn("cognitive_complexity", CallableRecord.__dataclass_fields__)
        self.assertIn("cognitive_complexity", CALLABLE_COLUMNS)
        self.assertEqual(
            [name for name in AGGREGATE_FIELDS if "cognitive" in name], []
        )


class CorpusArithmeticTests(unittest.TestCase):
    """Each callable's total must follow from its own listed contributions."""

    def test_contributions_sum_to_the_stated_total(self):
        for language in LANGUAGES:
            document = load(language)
            for callable_entry in document["callables"]:
                with self.subTest(language=language, callable=callable_entry["name"]):
                    total = sum(
                        item["contribution"]
                        for item in callable_entry["contributions"]
                    )
                    self.assertEqual(
                        total,
                        callable_entry["expected_cognitive_complexity"],
                        "the listed contributions do not add up to the stated total",
                    )

    def test_each_contribution_matches_its_kind_formula(self):
        for language in LANGUAGES:
            document = load(language)
            for callable_entry in document["callables"]:
                for item in callable_entry["contributions"]:
                    with self.subTest(
                        language=language, callable=callable_entry["name"],
                        rule=item["rule"], line=item["line"],
                    ):
                        self.assertIn(item["kind"], KIND_CONTRIBUTION)
                        expected = KIND_CONTRIBUTION[item["kind"]](
                            item["nesting_level"]
                        )
                        self.assertEqual(
                            item["contribution"], expected,
                            f"a {item['kind']} increment at nesting "
                            f"{item['nesting_level']} must contribute {expected}",
                        )

    def test_a_flat_increment_never_scales_with_nesting(self):
        """The rule that separates flat from structural, checked directly."""
        seen_deep_flat = False
        for language in LANGUAGES:
            for callable_entry in load(language)["callables"]:
                for item in callable_entry["contributions"]:
                    if item["kind"] != "flat":
                        continue
                    self.assertEqual(item["contribution"], 1)
                    if item["nesting_level"] >= 2:
                        seen_deep_flat = True
        self.assertTrue(
            seen_deep_flat,
            "no corpus case places a flat increment at depth >= 2, so nothing "
            "would catch a flat rule that wrongly scaled with nesting",
        )

    def test_a_structural_increment_does_scale_with_nesting(self):
        depths = {
            item["nesting_level"]
            for language in LANGUAGES
            for callable_entry in load(language)["callables"]
            for item in callable_entry["contributions"]
            if item["kind"] == "structural"
        }
        self.assertTrue(
            {0, 1, 2} <= depths,
            f"structural increments are only pinned at depths {sorted(depths)}; "
            f"0, 1 and 2 are needed to pin the scaling itself",
        )

    def test_zero_cases_are_explicit_and_present_in_every_language(self):
        for language in LANGUAGES:
            with self.subTest(language=language):
                zeros = [
                    entry for entry in load(language)["callables"]
                    if entry.get("zero_case")
                ]
                self.assertTrue(
                    zeros, "a language with no explicit zero case cannot "
                    "distinguish a measured zero from an absent measurement"
                )
                for entry in zeros:
                    self.assertEqual(entry["expected_cognitive_complexity"], 0)


class CitationTests(unittest.TestCase):
    """Every cited line must really contain what the contribution claims."""

    def test_every_contribution_cites_a_line_that_contains_its_text(self):
        for language in LANGUAGES:
            document = load(language)
            default_source = document["source"]
            for callable_entry in document["callables"]:
                filename = callable_entry.get("source", default_source)
                lines = source_lines(language, filename)
                for item in callable_entry["contributions"]:
                    with self.subTest(
                        language=language, callable=callable_entry["name"],
                        line=item["line"], rule=item["rule"],
                    ):
                        self.assertLessEqual(item["line"], len(lines))
                        self.assertIn(
                            item["line_contains"], lines[item["line"] - 1],
                            f"line {item['line']} of {filename} does not contain "
                            f"the cited text",
                        )

    def test_every_callable_start_line_declares_that_callable(self):
        for language in LANGUAGES:
            document = load(language)
            default_source = document["source"]
            for callable_entry in document["callables"]:
                filename = callable_entry.get("source", default_source)
                lines = source_lines(language, filename)
                name = callable_entry["name"].split(".")[-1]
                with self.subTest(language=language, callable=name):
                    self.assertIn(name, lines[callable_entry["start_line"] - 1])


class CoverageTests(unittest.TestCase):
    """Every applicable rule is exercised; every cited rule exists."""

    def _cited(self, language: str) -> set[str]:
        return {
            item["rule"]
            for callable_entry in load(language)["callables"]
            for item in callable_entry["contributions"]
        }

    def test_every_applicable_rule_is_exercised(self):
        matrix = applicability()
        for language in LANGUAGES:
            with self.subTest(language=language):
                missing = matrix[language] - self._cited(language)
                self.assertEqual(
                    missing, set(),
                    f"{language}: the frozen table marks these rules applicable "
                    f"but no corpus case pins them: {sorted(missing)}",
                )

    def test_no_case_cites_a_rule_the_table_does_not_declare(self):
        for language in LANGUAGES:
            with self.subTest(language=language):
                self.assertEqual(self._cited(language) - declared_rules(), set())

    def test_no_case_cites_a_rule_the_table_marks_inapplicable(self):
        """The corpus must not invent a construct a language does not have."""
        matrix = applicability()
        for language in LANGUAGES:
            with self.subTest(language=language):
                invented = self._cited(language) - matrix[language]
                self.assertEqual(
                    invented, set(),
                    f"{language}: cites rules the table marks inapplicable: "
                    f"{sorted(invented)}",
                )


class DivergenceEnumerationTests(unittest.TestCase):
    """Every known reference divergence is represented by a corpus case."""

    #: From G0 plus the two found while authoring this corpus. A divergence that
    #: exists but has no case would be one the campaign cannot classify.
    REQUIRED = {
        "go": {"D16", "D17"},
        "java": {"D1", "D4", "D5", "D19"},
        "javascript": {"D6", "D7", "D8", "D10", "D3"},
        "typescript": {"D6", "D7", "D8", "D9", "D3"},
        "python": {"D11", "D12", "D13", "D14", "D18"},
    }

    def test_every_known_divergence_has_a_corpus_case(self):
        for language, required in self.REQUIRED.items():
            with self.subTest(language=language):
                present = {
                    entry["divergence_id"]
                    for entry in load(language)["predicted_reference_divergences"]
                }
                self.assertEqual(
                    required - present, set(),
                    f"{language}: no corpus case exhibits {sorted(required - present)}",
                )

    def test_every_divergence_names_a_callable_that_exists(self):
        for language in LANGUAGES:
            document = load(language)
            names = {entry["name"] for entry in document["callables"]}
            for entry in document["predicted_reference_divergences"]:
                with self.subTest(language=language, divergence=entry["divergence_id"]):
                    self.assertIn(entry["callable"], names)

    def test_every_divergence_states_a_reason_and_both_sides(self):
        for language in LANGUAGES:
            for entry in load(language)["predicted_reference_divergences"]:
                with self.subTest(language=language, divergence=entry["divergence_id"]):
                    self.assertTrue(entry["reason"])
                    self.assertTrue(entry["tool"])
                    self.assertIn("archlens_expected", entry)
                    # `null` is permitted, and means UNVERIFIED rather than zero.
                    self.assertIn("reference_predicted", entry)

    def test_predictions_were_authored_before_measurement(self):
        """The ordering claim, made checkable.

        Predicted values live in the corpus; observed values live in a separate
        file written later. If the two were ever merged, a prediction could be
        quietly corrected after the fact and nobody would see it.
        """
        for language in LANGUAGES:
            document = load(language)
            with self.subTest(language=language):
                # FIELD names, not prose: the authorship note is allowed to
                # mention measurement, and a substring scan cannot tell a
                # description from a value.
                fields = set(document)
                for entry in document["predicted_reference_divergences"]:
                    fields |= set(entry)
                for name in fields:
                    self.assertNotIn(
                        "observed", name,
                        "an observed value in the corpus would let a prediction "
                        "be corrected after the fact",
                    )
                self.assertIn("AFTER this file was written", document["authorship"])


class AmendmentHistoryTests(unittest.TestCase):
    """Frozen revisions stay frozen, and the log says what changed."""

    EVIDENCE = REPOSITORY_ROOT / "validation/complexity_g1a_20260811"

    def _digest(self, name: str) -> str:
        import hashlib

        return hashlib.sha256((self.EVIDENCE / name).read_bytes()).hexdigest()

    def test_superseded_revisions_are_preserved_byte_for_byte(self):
        expected = {
            "FROZEN_RULE_TABLE.r1.md":
                "89a82caf99f2a3193d0d6d95937b44299f52fdc7f43954621b0d3aba6bad5eb7",
            "FROZEN_RULE_TABLE.r2.md":
                "d50ba7fc8b6b7bfbb0a8dd1531bdf0dc81a3ab16ee94279b9229fe3856c7ff5b",
        }
        for name, digest in expected.items():
            with self.subTest(revision=name):
                self.assertTrue((self.EVIDENCE / name).is_file())
                self.assertEqual(self._digest(name), digest)

    def test_the_current_table_records_every_amendment(self):
        text = (self.EVIDENCE / "FROZEN_RULE_TABLE.md").read_text(encoding="utf-8")
        self.assertIn("Revision 3", text)
        for marker in ("| 001 |", "| 002 |"):
            self.assertIn(marker, text)

    def test_amendment_001_is_not_reverted(self):
        text = (self.EVIDENCE / "FROZEN_RULE_TABLE.md").read_text(encoding="utf-8")
        self.assertIn("`Z-FINALLY`", text)
        self.assertNotIn("| `N-FINALLY` |", text)
        self.assertIn("Z-FINALLY", str(declared_rules()))

    def test_the_nesting_only_kind_is_labelled_reserved_and_empty(self):
        text = (self.EVIDENCE / "FROZEN_RULE_TABLE.md").read_text(encoding="utf-8")
        self.assertIn("RESERVED AND EMPTY", text)
        self.assertEqual(
            [rule for rule in declared_rules() if rule.startswith("N-")], []
        )

    def test_the_unsupported_rule_count_is_corrected_not_erased(self):
        text = (self.EVIDENCE / "FROZEN_RULE_TABLE.md").read_text(encoding="utf-8")
        self.assertIn("withdrawn", text)
        self.assertIn("39", text, "the withdrawn number must be named, not hidden")
        # ...and revision 2 keeps its wrong number, unedited.
        r2 = (self.EVIDENCE / "FROZEN_RULE_TABLE.r2.md").read_text(encoding="utf-8")
        self.assertIn("39", r2)


class RuleCountTests(unittest.TestCase):
    """The count is enumerated, never asserted."""

    def test_unique_normative_rule_ids(self):
        self.assertEqual(len(declared_rules()), 27)

    def test_rules_by_kind(self):
        by_kind: dict[str, int] = {}
        for rule in declared_rules():
            by_kind[rule.split("-")[0]] = by_kind.get(rule.split("-")[0], 0) + 1
        self.assertEqual(by_kind, {"S": 7, "F": 9, "Z": 10, "B": 1})

    def test_applicability_cells(self):
        self.assertEqual(sum(len(rules) for rules in applicability().values()), 84)

    def test_every_prefix_matches_its_declared_kind(self):
        """A `Z-` rule that contributed nesting would make the taxonomy lie."""
        kinds = {"structural": "S", "flat": "F", "nesting_only": "N", "zero": "Z"}
        for language in LANGUAGES:
            for entry in load(language)["callables"]:
                for item in entry["contributions"]:
                    rule, kind = item["rule"], item["kind"]
                    with self.subTest(rule=rule, kind=kind):
                        if rule.startswith("B-") or kind == "not_fired":
                            # A boundary, or a rule recorded as NOT having
                            # fired: in both cases the prefix describes the
                            # rule, not this site's kind.
                            self.assertEqual(item["contribution"], 0)
                            continue
                        self.assertTrue(rule.startswith(kinds[kind] + "-"))


class Amendment002ClosureTests(unittest.TestCase):
    """The six owner-review blockers, each pinned by corpus cases."""

    def _by_name(self, language: str) -> dict:
        return {entry["name"]: entry for entry in load(language)["callables"]}

    def test_boolean_sequence_cases_are_pinned_in_every_language(self):
        """Parentheses do not split; changing operator kind does."""
        for language in LANGUAGES:
            entries = self._by_name(language)
            key = {"go": ("SeqParenSame", "SeqParenMixed", "SeqThreeRuns"),
                   "java": ("seqParenSame", "seqParenMixed", "seqThreeRuns"),
                   "javascript": ("seqParenSame", "seqParenMixed", "seqThreeRuns"),
                   "typescript": ("seqParenSame", "seqParenMixed", "seqThreeRuns"),
                   "python": ("seq_paren_same", "seq_paren_mixed", "seq_three_runs")}[language]
            same, mixed, runs = (entries[name] for name in key)
            with self.subTest(language=language):
                self.assertEqual(same["expected_cognitive_complexity"], 2)
                self.assertEqual(mixed["expected_cognitive_complexity"], 3)
                self.assertEqual(runs["expected_cognitive_complexity"], 4)
                sequences = [
                    item for item in same["contributions"] if item["rule"] == "F-BOOLSEQ"
                ]
                self.assertEqual(
                    len(sequences), 1, "parentheses must not split a same-operator run"
                )

    def test_nullish_and_logical_assignment_are_pinned_for_js_and_ts(self):
        for language in ("javascript", "typescript"):
            entries = self._by_name(language)
            with self.subTest(language=language):
                self.assertEqual(entries["seqNullishRun"]["expected_cognitive_complexity"], 1)
                self.assertEqual(entries["seqNullishThenOr"]["expected_cognitive_complexity"], 2)
                # The assignment operator itself is zero; its operand still counts.
                self.assertEqual(entries["logicalAssignAnd"]["expected_cognitive_complexity"], 1)
                self.assertEqual(entries["logicalAssignOr"]["expected_cognitive_complexity"], 0)
                self.assertEqual(entries["logicalAssignNullish"]["expected_cognitive_complexity"], 0)
                for name in ("logicalAssignAnd", "logicalAssignOr", "logicalAssignNullish"):
                    pinned = [
                        item for item in entries[name]["contributions"]
                        if item["kind"] == "not_fired" and item["rule"] == "F-BOOLSEQ"
                    ]
                    self.assertTrue(
                        pinned,
                        f"{name} must pin the logical-assignment operator as a rule "
                        f"that was evaluated and did NOT fire, so the zero is stated "
                        f"rather than inferred from an absence",
                    )

    def test_recursion_is_capped_at_one_per_callable(self):
        seen_two_call_sites = False
        for language in LANGUAGES:
            for entry in load(language)["callables"]:
                scored = [
                    item for item in entry["contributions"]
                    if item["rule"] == "F-RECURSION" and item["contribution"] > 0
                ]
                with self.subTest(language=language, callable=entry["name"]):
                    self.assertLessEqual(
                        len(scored), 1,
                        "F-RECURSION contributes at most +1 per callable",
                    )
                if any("two call sites" in (item.get("detail") or "")
                       or "TWO self-call" in (item.get("detail") or "")
                       for item in entry["contributions"]):
                    seen_two_call_sites = True
        self.assertTrue(
            seen_two_call_sites,
            "no case pins the cap, so nothing would catch per-call-site counting",
        )

    def test_a_same_name_call_on_another_receiver_is_not_recursion(self):
        for language in LANGUAGES:
            name = {"go": "receiverHolder.SameNameOtherReceiver",
                    "java": "sameNameOtherReceiver",
                    "javascript": "Holder.sameNameOtherReceiver",
                    "typescript": "Holder.sameNameOtherReceiver",
                    "python": "Holder.same_name_other_receiver"}[language]
            entry = self._by_name(language)[name]
            with self.subTest(language=language):
                self.assertEqual(entry["expected_cognitive_complexity"], 0)
                rules = [item["rule"] for item in entry["contributions"]]
                self.assertIn("F-RECURSION", rules)
                for item in entry["contributions"]:
                    if item["rule"] == "F-RECURSION":
                        self.assertEqual(item["contribution"], 0)

    def test_discovery_through_a_nested_scope_is_pinned(self):
        """Global discovery: the inner method is measured in its own right."""
        for language in ("python", "java", "javascript", "typescript"):
            entry = self._by_name(language)["Inner.measured"]
            with self.subTest(language=language):
                self.assertEqual(entry["expected_cognitive_complexity"], 1)
                self.assertIn("DISCOVERY", entry["note"])

    def test_the_enclosing_callable_absorbs_nothing_from_the_nested_one(self):
        for language, outer in (("python", "discovery_def_class_method"),
                                ("java", "discoveryLocalClassMethod"),
                                ("javascript", "discoveryFunctionClassMethod"),
                                ("typescript", "discoveryFunctionClassMethod")):
            entry = self._by_name(language)[outer]
            with self.subTest(language=language):
                boundary = [
                    item for item in entry["contributions"] if item["rule"] == "B-NESTED"
                ]
                self.assertTrue(boundary)
                self.assertEqual(boundary[0]["contribution"], 0)

    def test_default_traversal_is_pinned(self):
        """A construct with no rule is zero, but its children still count."""
        cases = {
            "go": ("ReturnTraversed", 1), "java": ("returnTraversed", 1),
            "javascript": ("returnTraversed", 1), "typescript": ("returnTraversed", 1),
            "python": ("return_traversed", 1),
        }
        for language, (name, total) in cases.items():
            with self.subTest(language=language):
                self.assertEqual(
                    self._by_name(language)[name]["expected_cognitive_complexity"], total
                )
        # `defer` carries no literal here, and its argument still counts.
        self.assertEqual(
            self._by_name("go")["DeferPlainCall"]["expected_cognitive_complexity"], 1
        )

    def test_python_comprehension_semantics_are_pinned(self):
        entries = self._by_name("python")
        expected = {
            "comp_single": 1,
            "comp_two_generators": 1,
            "comp_two_generators_filtered": 2,
            "comp_boolean_condition": 3,
            "comp_nested": 3,
        }
        for name, total in expected.items():
            with self.subTest(callable=name):
                self.assertEqual(
                    entries[name]["expected_cognitive_complexity"], total
                )
        # One comprehension, one structural increment, however many generators.
        for name in ("comp_single", "comp_two_generators"):
            structural = [
                item for item in entries[name]["contributions"]
                if item["rule"] == "S-COMPREHENSION"
            ]
            self.assertEqual(len(structural), 1)
        # The nested case must record both levels explicitly.
        nested = [
            item for item in entries["comp_nested"]["contributions"]
            if item["rule"] == "S-COMPREHENSION"
        ]
        self.assertEqual([item["nesting_level"] for item in nested], [0, 1])
        self.assertEqual([item["contribution"] for item in nested], [1, 2])


class DiscoveredDivergenceTests(unittest.TestCase):
    """Divergences the measurement found are recorded where measurements live.

    They are deliberately NOT written into the corpus expectations: those were
    authored before the tools ran, and back-filling a discovery into them would
    destroy the only evidence that the ordering was real.
    """

    MEASUREMENTS = (
        REPOSITORY_ROOT
        / "validation/complexity_g1a_20260811/amendment_002_measurements.json"
    )

    def test_every_new_divergence_is_recorded_with_evidence(self):
        payload = json.loads(self.MEASUREMENTS.read_text(encoding="utf-8"))
        found = payload["new_divergences"]
        self.assertEqual(
            set(found), {"D22", "D23", "D24", "D25", "D26", "D27"}
        )
        for identifier, entry in found.items():
            with self.subTest(divergence=identifier):
                self.assertTrue(entry["title"])
                self.assertTrue(entry["tools"])
                self.assertTrue(entry["evidence"])
                self.assertTrue(entry["archlens"])

    def test_the_measurement_file_carries_no_expectation_authority(self):
        """Expectations live in the corpus; this file only records what tools did."""
        payload = json.loads(self.MEASUREMENTS.read_text(encoding="utf-8"))
        self.assertIn("AFTER the expectations were frozen", payload["purpose"])

    def test_the_owner_pinned_boolean_cases_disagree_with_the_references(self):
        """The reason the seven expressions were pinned, kept visible.

        No two references agree on both parenthesis handling and run splitting,
        so the definition could not have been inferred from any of them.
        """
        payload = json.loads(self.MEASUREMENTS.read_text(encoding="utf-8"))
        self.assertEqual(payload["go"]["SeqParenSame"]["gocognit"], 3)
        self.assertEqual(payload["java"]["seqParenSame"]["pmd"], 2)
        self.assertEqual(payload["go"]["SeqThreeRuns"]["gocognit"], 4)
        self.assertEqual(payload["java"]["seqThreeRuns"]["pmd"], 3)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
