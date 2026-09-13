"""G2-B: the real-subject campaign, its adjudication, and the defect it found.

:class:`JavaRecursionReceiverTests` is the regression for the one confirmed
ArchLens defect of this gate. It was written to FAIL before the fix, and it is
derived from `FROZEN_RULE_TABLE.md` revision 3 sections 4.2 and 4.3 rather than
from any reference tool's output -- PMD disagreeing is what made us look, and it
is deliberately not what makes the case.

The five-language sweep is the useful part: the same rule is checked in every
language, so the fix cannot silently regress the four that were already right,
and the blast radius of the defect is a measured fact rather than an assumption.
"""

from __future__ import annotations

import os

import ast as python_ast
import json
import unittest
from pathlib import Path

from modules.callable_analysis import analyze_callables
from modules.core_metrics import ParserRegistry
from validation.differential import cognitive_adjudication as adjudication
from validation.differential import cognitive_campaign, cognitive_layer2

REDUCTIONS = (
    Path(__file__).resolve().parents[1]
    / "validation" / "complexity_g2b_20260812" / "reductions"
)


def measure(language: str, path: Path) -> dict[str, int | None]:
    """Every canonical callable in one file, through the production path."""
    raw = path.read_bytes()
    if language == "Python":
        text = raw.decode("utf-8")
        root = python_ast.parse(text)
        result = analyze_callables(
            language, root, raw, path.name, raw_text=text, masked_text=text
        )
    else:
        root = ParserRegistry().get(language, path.suffix).parse(raw).root_node
        result = analyze_callables(language, root, raw, path.name)
    return {
        record.qualified_name: record.cognitive_complexity
        for record in result.records
    }


class JavaRecursionReceiverTests(unittest.TestCase):
    """`F-RECURSION` must read the receiver, not only the member name.

    Frozen rule table 4.2 B recognizes a member self-call only when the receiver
    is the explicit current receiver (`this`) or the enclosing type name. 4.3 is
    explicit that `arbitraryObject.sameName()` is NOT recursion merely because
    the member name matches -- it is the false-positive class Amendment 002
    narrowed the rule to remove.

    Java's `method_invocation` keeps its receiver in a SIBLING `object` field
    rather than wrapping the callee in a selector node, so reading the `name`
    field alone made every same-named call look unqualified.
    """

    @classmethod
    def setUpClass(cls):
        cls.values = measure("Java", REDUCTIONS / "JavaRecursionReceiver.java")

    def value(self, name: str) -> int | None:
        for qualified, value in self.values.items():
            if qualified == f"JavaRecursionReceiver.{name}":
                return value
        raise AssertionError(f"{name} was not measured; got {sorted(self.values)}")

    def test_bare_self_name_call_is_recursion(self):
        self.assertEqual(self.value("bareSelfCall"), 1, "rule table 4.2 A")

    def test_this_qualified_self_call_is_recursion(self):
        self.assertEqual(self.value("thisSelfCall"), 1, "rule table 4.2 B")

    def test_enclosing_type_qualified_self_call_is_recursion(self):
        self.assertEqual(self.value("typeSelfCall"), 1, "rule table 4.2 B")

    def test_same_name_on_a_field_receiver_is_not_recursion(self):
        self.assertEqual(
            self.value("fieldReceiverSameName"), 0,
            "rule table 4.3: a call on a receiver outside 4.2 B is ignored",
        )

    def test_same_name_on_another_type_is_not_recursion(self):
        self.assertEqual(
            self.value("staticOtherTypeSameName"), 0,
            "rule table 4.3: the member name matching is not enough",
        )


class RecursionReceiverAcrossLanguagesTests(unittest.TestCase):
    """The same rule, in the four languages that were already correct.

    Kept so the Java fix cannot regress them, and so the defect's blast radius
    stays a measured fact. Go, Python and TypeScript wrap a qualified callee in
    a selector/member/attribute node, so their receiver was always visible.
    """

    def test_go(self):
        values = measure("Go", REDUCTIONS / "recv.go")
        self.assertEqual(values.get("BareSelf"), 1)
        self.assertEqual(values.get("holder.RecvSelf"), 1)
        self.assertEqual(values.get("holder.SameName"), 0)

    def test_python(self):
        values = measure("Python", REDUCTIONS / "recv.py")
        self.assertEqual(values.get("Holder.bare_self"), 1)
        self.assertEqual(values.get("Holder.recv_self"), 1)
        self.assertEqual(values.get("Holder.same_name"), 0)
        self.assertEqual(values.get("free_self"), 1)

    def test_typescript(self):
        values = measure("TypeScript", REDUCTIONS / "recv.ts")
        self.assertEqual(values.get("Holder.recvSelf"), 1)
        self.assertEqual(values.get("Holder.sameName"), 0)
        self.assertEqual(values.get("freeSelf"), 1)


class ElseIfBodyNestingTests(unittest.TestCase):
    """`F-ELSEIF` must raise nesting for its own body, in every language.

    Frozen rule table 2.2: "`F-ELSEIF` and `F-ELSE` raise nesting **for their
    own body only**, not for their siblings". So
    `if a / else if b / if c` measures `S-IF@0` 1 + `F-ELSEIF` 1 +
    `S-IF@1` 2 = **4** everywhere.

    JavaScript and TypeScript scored 3. The increment sat on the `else_clause`
    while the consequence belonged to the nested `if_statement`, and the engine
    computes `body_children` only for a node that scored -- so nothing deepened
    the else-if's body. Go and Java were already correct because they score the
    `if_statement` through their `alternative` field, and Python because it
    separates the two forms by column.

    The G1-A corpus pins `if / else if / else` chains but never a construct
    INSIDE an else-if body, which is why this survived G1-B. Every language is
    checked here so the asymmetry cannot come back in one of them.
    """

    EXPECTED = 4

    def test_javascript(self):
        values = measure("JavaScript", REDUCTIONS / "elseif.js")
        self.assertEqual(values.get("elseIfBodyNesting"), self.EXPECTED)

    def test_typescript(self):
        values = measure("TypeScript", REDUCTIONS / "elseif.ts")
        self.assertEqual(values.get("elseIfBodyNesting"), self.EXPECTED)

    def test_go(self):
        values = measure("Go", REDUCTIONS / "elseif.go")
        self.assertEqual(values.get("ElseIfBodyNesting"), self.EXPECTED)

    def test_java(self):
        values = measure("Java", REDUCTIONS / "ElseIf.java")
        self.assertEqual(values.get("ElseIf.elseIfBodyNesting"), self.EXPECTED)

    def test_python(self):
        values = measure("Python", REDUCTIONS / "py_elif.py")
        self.assertEqual(values.get("elif_body_nesting"), self.EXPECTED)

    def test_a_plain_else_still_scores_once_and_deepens_once(self):
        """The fix moved an increment between nodes; it must not drop one.

        A bare `else` holding an `if` is `F-ELSE` 1 + `S-IF@1` 2, plus the
        leading `S-IF@0` 1 = 4.
        """
        values = measure("Python", REDUCTIONS / "py_elif.py")
        self.assertEqual(values.get("else_body_nesting"), 4)


class AsyncLoopTests(unittest.TestCase):
    """`async for` is a loop. Rule table 2.1 `S-LOOP` covers `for` in all forms.

    The closure analysis found the Python reference scores `async for` at 0 AND
    raises no nesting for it (D32), which made every construct inside an async
    loop look one level shallower and accounted for the whole -6 on
    `OllamaProvider.chat_stream`. ArchLens was correct; this pins that it stays
    correct, and that `with` / `async with` keep raising no nesting (`Z-WITH`).
    """

    @classmethod
    def setUpClass(cls):
        cls.values = measure("Python", REDUCTIONS / "py_async.py")

    def test_sync_and_async_for_agree_with_the_rule_table(self):
        self.assertEqual(self.values.get("sync_for"), 3)
        self.assertEqual(
            self.values.get("async_for"), 3,
            "an `async for` is a loop: S-LOOP@0 1 + S-IF@1 2",
        )

    def test_with_forms_raise_no_nesting(self):
        for name in ("sync_with", "async_with"):
            with self.subTest(name):
                self.assertEqual(
                    self.values.get(name), 1,
                    "`Z-WITH`: no increment and no nesting, so the inner `if` "
                    "sits at level 0",
                )


class HeaderPositionTests(unittest.TestCase):
    """D33 — a construct in a HEADER sits at the enclosing construct's level.

    Rule table 2.1 raises nesting for the BODY only, so a ternary in a `for`
    iterable or an `if`/`while` test contributes `1 + n`, not `1 + n + 1`. The
    reference scores all three one deeper because `process_child_nodes` hands
    the raised `increment_by` to every child, and the test/iterable are
    children.

    Pinned in all four positions so the body case (which agrees) cannot drift
    into the header cases (which do not).
    """

    @classmethod
    def setUpClass(cls):
        cls.values = measure("Python", REDUCTIONS / "py_header_position.py")

    def test_header_constructs_sit_at_the_enclosing_level(self):
        for name in (
            "ternary_in_loop_iterable", "ternary_in_if_test",
            "ternary_in_while_test",
        ):
            with self.subTest(name):
                self.assertEqual(self.values.get(name), 2)

    def test_a_body_construct_is_one_deeper(self):
        self.assertEqual(self.values.get("ternary_in_loop_body"), 3)


class D28MatrixTests(unittest.TestCase):
    """The 132-probe chain matrix, against expectations frozen from the table.

    `d28_matrix_expected.json` was derived from `FROZEN_RULE_TABLE.md` sections
    2.1/2.2 before the reference was run and without consulting ArchLens
    output. ArchLens matching it on every probe is what licenses calling the
    reference's shortfall a divergence rather than an ArchLens defect.
    """

    def test_archlens_matches_the_frozen_rule_table_on_every_probe(self):
        expected = json.loads(
            (REDUCTIONS / "d28_matrix_expected.json").read_text(encoding="utf-8")
        )["expected_archlens"]
        values = measure("Python", REDUCTIONS / "d28_matrix.py")
        self.assertEqual(len(expected), 132)
        mismatched = {
            name: (values.get(name), want)
            for name, want in expected.items()
            if values.get(name) != want
        }
        self.assertEqual(mismatched, {}, "ArchLens vs frozen rule table")


class ClosureDisciplineTests(unittest.TestCase):
    """The closure analysis may only close a case on evidence it actually has."""

    def test_a_neutralizer_that_must_not_move_archlens_is_declared(self):
        from validation.differential import cognitive_closure as closure

        for name in ("nested_callables", "chain_branch_position", "async_for"):
            self.assertIn(
                name, closure.ARCHLENS_INVARIANT,
                "this neutralizer removes or moves syntax ArchLens does not "
                "attribute, so a movement in ArchLens means it changed "
                "something else and its evidence is void",
            )

    def test_the_withdrawn_d28_rate_is_not_reachable(self):
        """A model that failed a test cannot license a closure.

        `-1 per structural construct in a non-final branch` fit three chain
        shapes and was falsified by a fourth (trailing bare `else`: two sites,
        one point). It was removed rather than kept for the cases it happened
        to fit.
        """
        from validation.differential import cognitive_closure as closure

        self.assertNotIn("chain_branch_position_rate", closure.ORDER)
        self.assertNotIn(
            "chain_branch_position_rate", closure.NEUTRALIZER_DIVERGENCES
        )

    def test_every_neutralizer_names_the_divergence_it_evidences(self):
        from validation.differential import cognitive_closure as closure
        from validation.differential.cognitive_definitions import DIVERGENCES

        self.assertEqual(
            set(closure.ORDER), set(closure.NEUTRALIZER_DIVERGENCES),
            "a neutralizer that runs without a declared divergence would "
            "produce an uncitable closure",
        )
        for names in closure.NEUTRALIZER_DIVERGENCES.values():
            for name in names:
                self.assertIn(name, DIVERGENCES)


class AdjudicationDisciplineTests(unittest.TestCase):
    """The classifier must not be able to conclude the one thing it cannot know."""

    def test_archlens_defect_is_never_assigned_automatically(self):
        self.assertNotIn(
            adjudication.FAMILY_ARCHLENS_DEFECT,
            adjudication.AUTOMATABLE_FAMILIES,
            "an ArchLens defect requires a hand derivation from the frozen "
            "rule table, never a reference disagreement",
        )

    def test_all_eight_families_are_declared(self):
        self.assertEqual(len(adjudication.FAMILIES), 8)
        self.assertIn(adjudication.FAMILY_ARCHLENS_DEFECT, adjudication.FAMILIES)

    def test_a_candidate_must_point_the_same_way_as_the_difference(self):
        """A construct whose divergence points the other way is evidence
        AGAINST that explanation, never for it."""
        observation = {
            "language": "Python", "difference": 3, "relative_path": "x.py",
            "start_line": None, "end_line": None, "qualified_name": "f",
        }
        result = adjudication.classify(observation, REDUCTIONS)
        self.assertEqual(result.family, adjudication.FAMILY_UNRESOLVED)

    def test_adjudication_may_not_be_written_over_the_raw_baseline(self):
        with self.assertRaises(ValueError):
            adjudication.persist([], REDUCTIONS / "cognitive_raw_observations.json")


class CampaignScopeTests(unittest.TestCase):
    def test_only_the_already_selected_initial_subjects_are_reachable(self):
        self.assertEqual(
            [key for key, _ in cognitive_campaign.INITIAL_SUBJECTS],
            [
                "layer2-go-shop", "layer2-java-demo",
                "layer2-javascript-monolith", "layer2-python-ralph",
                "layer2-typescript-securo",
            ],
        )
        # The larger subject must not be reachable from any PATH constant.
        # Searching the whole file would also match the docstring that explains
        # it is unreachable, which is prose rather than reach.
        for name in ("DEFAULT_SUBJECT_ROOT", "DEFAULT_RUN_ROOT"):
            self.assertNotIn(
                "l2big", str(getattr(cognitive_campaign, name)),
                "expansion requires a recorded trigger",
            )

    def test_the_c4_evidence_root_is_never_read(self):
        self.assertNotIn("l2c4", str(cognitive_campaign.DEFAULT_RUN_ROOT))

    def test_a_pre_1_10_run_is_refused_rather_than_compared(self):
        """A 1.9 document has no cognitive column, so comparing against it
        yields an all-null table that reads exactly like a clean result."""
        c4 = Path(os.environ.get("ARCHLENS_DIFFVAL_REFS", ".metrolith-reference")) / 'l2c4/layer2-go-shop'
        if not (c4 / "latest_run.json").is_file():
            self.skipTest("the C4 evidence root is not present on this host")
        with self.assertRaises(cognitive_campaign.RunUnusable):
            cognitive_campaign.archlens_callables(
                cognitive_campaign.latest_run(c4)
            )




if __name__ == "__main__":
    unittest.main()
