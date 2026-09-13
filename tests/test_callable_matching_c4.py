"""C4 rigor correction 1: deterministic callable matching.

A wrong pairing is worse than no pairing. It does not lose an observation, it
invents a disagreement and sends adjudication after a defect nobody has, so
every test here is about refusing to guess.
"""

from __future__ import annotations

import unittest

from validation.differential.callable_matching import (
    AMBIGUOUS,
    MATCH_EXACT_SIGNATURE,
    MATCH_NAME_AND_SPAN,
    MATCH_SPAN_ONLY,
    match_callables,
)


def row(path, name=None, signature=None, start=None, end=None):
    return {
        "relative_path": path,
        "qualified_name": name,
        "signature_discriminator": signature,
        "start_line": start,
        "end_line": end,
    }


class MatchingTests(unittest.TestCase):
    def test_exact_signature_match_is_the_strongest_tier(self):
        left = [row("a.java", "S.m", "(int)", 10, 12)]
        right = [row("a.java", "S.m", "(int)", 10, 12)]
        result = match_callables(left, right)
        self.assertEqual(result.comparable_count, 1)
        self.assertEqual(result.matched[0][2], MATCH_EXACT_SIGNATURE)

    def test_overloads_are_separated_by_signature_not_by_position(self):
        left = [
            row("a.java", "S.m", "(int)", 10, 12),
            row("a.java", "S.m", "(String)", 14, 16),
        ]
        # Reference reports them in the OPPOSITE order.
        right = [
            row("a.java", "S.m", "(String)", 14, 16),
            row("a.java", "S.m", "(int)", 10, 12),
        ]
        result = match_callables(left, right)
        self.assertEqual(result.comparable_count, 2)
        for archlens, reference, tier in result.matched:
            self.assertEqual(archlens.signature_discriminator,
                             reference.signature_discriminator)
            self.assertEqual(tier, MATCH_EXACT_SIGNATURE)

    def test_a_name_match_needs_an_overlapping_span(self):
        left = [row("a.js", "render", None, 10, 20)]
        right = [row("a.js", "render", None, 100, 110)]
        result = match_callables(left, right)
        self.assertEqual(result.comparable_count, 0)
        self.assertEqual(len(result.unmatched_archlens), 1)
        self.assertEqual(len(result.unmatched_reference), 1)

    def test_a_shifted_span_still_matches_because_tools_disagree_on_start(self):
        """A reference that excludes decorators starts lower; overlap suffices."""
        left = [row("a.ts", "W.decorated", None, 100, 104)]
        right = [row("a.ts", "W.decorated", None, 102, 104)]
        result = match_callables(left, right)
        self.assertEqual(result.comparable_count, 1)
        self.assertEqual(result.matched[0][2], MATCH_NAME_AND_SPAN)

    def test_same_name_twice_in_one_file_is_ambiguous_not_guessed(self):
        left = [row("a.js", "handler", None, 10, 30)]
        right = [
            row("a.js", "handler", None, 12, 20),
            row("a.js", "handler", None, 22, 28),
        ]
        result = match_callables(left, right)
        self.assertEqual(result.comparable_count, 0)
        self.assertEqual(len(result.ambiguous), 1)
        _key, candidates = result.ambiguous[0]
        self.assertEqual(len(candidates), 2)

    def test_a_differently_named_reference_matches_on_span(self):
        """`Constructs::trivial` versus `Constructs.trivial`."""
        left = [row("a.java", "Constructs.trivial", None, 14, 16)]
        right = [row("a.java", "Constructs::trivial", None, 14, 16)]
        result = match_callables(left, right)
        self.assertEqual(result.comparable_count, 1)
        self.assertEqual(result.matched[0][2], MATCH_SPAN_ONLY)

    def test_paths_never_cross(self):
        left = [row("a.js", "f", None, 1, 5)]
        right = [row("b.js", "f", None, 1, 5)]
        result = match_callables(left, right)
        self.assertEqual(result.comparable_count, 0)

    def test_windows_separators_normalize(self):
        left = [row("src\\a.js", "f", None, 1, 5)]
        right = [row("src/a.js", "f", None, 1, 5)]
        self.assertEqual(match_callables(left, right).comparable_count, 1)

    def test_population_differences_are_reported_not_dropped(self):
        """Lizard measures callables ArchLens deliberately excludes."""
        left = [row("a.go", "F", None, 10, 20)]
        right = [
            row("a.go", "F", None, 10, 20),
            row("a.go", "F.func1", None, 12, 14),  # the func literal
        ]
        result = match_callables(left, right)
        self.assertEqual(result.comparable_count, 1)
        self.assertEqual(
            len(result.unmatched_reference), 1,
            "a systematic population difference is itself a finding and must "
            "survive into the record",
        )

    def test_a_reference_row_is_consumed_once(self):
        left = [row("a.js", "f", None, 1, 10), row("a.js", "g", None, 1, 10)]
        right = [row("a.js", "f", None, 1, 10)]
        result = match_callables(left, right)
        self.assertEqual(result.comparable_count, 1)
        self.assertEqual(len(result.unmatched_archlens), 1)

    def test_summary_counts_every_row_exactly_once(self):
        left = [row("a.js", "f", None, 1, 10), row("a.js", "z", None, 40, 50)]
        right = [row("a.js", "f", None, 1, 10), row("a.js", "y", None, 60, 70)]
        result = match_callables(left, right)
        summary = result.summary()
        self.assertEqual(
            summary["matched"] + summary["unmatched_archlens"]
            + len(result.ambiguous),
            len(left),
        )
        self.assertEqual(summary["unmatched_reference"], 1)

    def test_matching_never_reads_a_metric(self):
        """A matcher that could see values could be tuned to pair agreeing rows."""
        import ast
        import pathlib

        source = (
            pathlib.Path(__file__).resolve().parent.parent
            / "validation/differential/callable_matching.py"
        ).read_text(encoding="utf-8")
        tree = ast.parse(source)
        literals = {
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        for metric in (
            "cyclomatic_complexity", "decision_point_count",
            "boolean_operator_count", "max_condition_operator_count",
            "max_nesting_depth", "nloc", "formal_parameter_count",
        ):
            self.assertNotIn(
                metric, literals,
                f"the matcher references {metric}; matching and comparison must "
                f"stay separate steps",
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
