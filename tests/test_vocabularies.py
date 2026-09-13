"""Status vocabularies have exactly one definition, and producers obey it.

The defect this guards against is not hypothetical. ``modules/inventory.py``
emitted ``git_mode_map_status = "unavailable_not_git"``; the consumer's
``KNOWN_GIT_MODE_STATES`` did not contain it; and so a **complete** analysis, of
a directory whose only unusual property is that it is not a Git checkout,
reported::

    unknown_categories = ("git_mode_map_status='unavailable_not_git'",)

Nothing was wrong with that run. The producer and the consumer had simply
drifted, because the vocabulary was written down in both places.

``test_every_git_mode_map_status_a_producer_can_emit_is_recognized`` is the test
that matters most here: it reads the producer's own source and fails if any
value it assigns is one the consumer would not recognize. A test that only
pinned today's five strings would have passed just as happily *before* the fix.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from modules.diagnostics import project_repository
from modules.vocabularies import (
    GIT_MODE_MAP_NOT_GIT_WIRE_VALUE,
    KNOWN_ANALYSIS_STATUSES,
    KNOWN_GIT_MODE_STATES,
    KNOWN_METRIC_STATUSES,
    KNOWN_PARTIAL_ORIGINS,
    AnalysisStatus,
    GitModeMapReason,
    GitModeMapStatus,
    MetricStatus,
    PartialOrigin,
    normalize_git_mode_map_state,
)

REPOSITORY = Path(__file__).resolve().parent.parent

COMPLETE_AGGREGATE = {
    name: MetricStatus.COMPLETE.value
    for name in (
        "inventory_status",
        "source_files_status",
        "loc_status",
        "classes_structs_status",
        "methods_functions_status",
    )
}


def healthy_result(git_mode_map_status: str | None) -> dict:
    """A repository result that is complete in every dimension."""
    return {
        "repository_url": "local://plain-directory",
        "analysis_status": AnalysisStatus.COMPLETE.value,
        "core_metric_status": MetricStatus.COMPLETE.value,
        "partial_origin": PartialOrigin.NONE.value,
        "expected_language_family_status": MetricStatus.COMPLETE.value,
        "git_mode_map_status": git_mode_map_status,
        "metrics": {"aggregate": dict(COMPLETE_AGGREGATE)},
        "errors": [],
    }


def projected(result: dict):
    return project_repository(
        result, has_inventory=True, has_contribution_ledger=True
    )


class NonGitDirectoryRegressionTests(unittest.TestCase):
    """The reported defect, pinned so it cannot come back."""

    def test_healthy_non_git_directory_reports_no_unknown_category(self):
        diagnostic = projected(healthy_result(GIT_MODE_MAP_NOT_GIT_WIRE_VALUE))
        self.assertEqual(diagnostic.unknown_categories, ())
        self.assertEqual(
            diagnostic.analysis_status, AnalysisStatus.COMPLETE.value
        )

    def test_every_recognized_state_is_free_of_unknown_categories(self):
        for value in sorted(KNOWN_GIT_MODE_STATES):
            with self.subTest(git_mode_map_status=value):
                self.assertEqual(projected(healthy_result(value)).unknown_categories, ())

    def test_an_absent_state_is_not_an_unknown_category(self):
        # None means "not recorded", which is not the same as unrecognized.
        self.assertEqual(projected(healthy_result(None)).unknown_categories, ())

    def test_a_genuinely_unrecognized_state_is_still_surfaced(self):
        # The fix must not be "widen the set until the symptom stops".
        diagnostic = projected(healthy_result("no_such_state"))
        self.assertEqual(
            diagnostic.unknown_categories,
            ("git_mode_map_status='no_such_state'",),
        )


class ProducerConsumerAgreementTests(unittest.TestCase):
    """Read the producer's source; refuse values the consumer cannot name."""

    @staticmethod
    def _assigned_values(attribute: str = "git_mode_map_status") -> set[str]:
        """Every string literal assigned to ``self.<attribute>`` in the producer.

        Source-level rather than behavioural because reaching each branch needs
        a real Git failure, a timeout, and a non-repository on disk. The AST is
        the cheap, complete enumeration.
        """
        tree = ast.parse(
            (REPOSITORY / "modules" / "inventory.py").read_text(encoding="utf-8")
        )
        found: set[str] = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            if not (
                isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            ):
                continue
            for target in node.targets:
                if isinstance(target, ast.Attribute) and target.attr == attribute:
                    found.add(node.value.value)
        return found

    @classmethod
    def _assigned_git_mode_map_values(cls) -> set[str]:
        return cls._assigned_values()

    def test_every_git_mode_map_status_a_producer_can_emit_is_recognized(self):
        emitted = self._assigned_git_mode_map_values()
        self.assertTrue(emitted, "found no git_mode_map_status assignments to check")
        unrecognized = sorted(emitted - KNOWN_GIT_MODE_STATES)
        self.assertEqual(
            unrecognized,
            [],
            f"modules/inventory.py emits {unrecognized}, which the diagnostic "
            f"consumer does not recognize; add it to modules/vocabularies.py",
        )

    def test_the_non_git_branch_is_visible_to_the_source_scan(self):
        """Guards the test above against silently checking nothing.

        If the producer were refactored to build these values indirectly, the
        AST scan would find no literals and the agreement test would pass
        vacuously. So the branch that caused the original drift — a directory
        that is simply not a Git checkout — must stay literally discoverable.

        It is now discoverable as a *pair*: Artifact 1.7 normalizes state and
        cause into `git_mode_map_status` + `git_mode_map_reason` instead of the
        compound `unavailable_not_git` wire value.
        """
        self.assertIn(GitModeMapStatus.UNAVAILABLE.value, self._assigned_values())
        self.assertIn(
            GitModeMapReason.NOT_GIT_REPOSITORY.value,
            self._assigned_values("git_mode_map_reason"),
        )

    def test_the_producer_no_longer_emits_the_compound_wire_value(self):
        """Normalization happens on write; historical bytes are never rewritten.

        `GIT_MODE_MAP_NOT_GIT_WIRE_VALUE` must remain in the *consumer*
        vocabulary, because preserved 1.5/1.6 artifacts still carry it and are
        normalized at read time. It must not come back out of a producer.
        """
        self.assertNotIn(
            GIT_MODE_MAP_NOT_GIT_WIRE_VALUE, self._assigned_git_mode_map_values()
        )
        self.assertIn(GIT_MODE_MAP_NOT_GIT_WIRE_VALUE, KNOWN_GIT_MODE_STATES)


class NormalizationTests(unittest.TestCase):
    """State and cause are separated without changing the frozen 1.5.0 wire."""

    def test_compound_value_splits_into_state_and_reason(self):
        self.assertEqual(
            normalize_git_mode_map_state(GIT_MODE_MAP_NOT_GIT_WIRE_VALUE),
            (GitModeMapStatus.UNAVAILABLE, GitModeMapReason.NOT_GIT_REPOSITORY),
        )

    def test_plain_states_normalize_to_themselves_with_no_reason(self):
        for member in GitModeMapStatus:
            with self.subTest(state=member.value):
                self.assertEqual(
                    normalize_git_mode_map_state(member.value), (member, None)
                )

    def test_absent_and_unrecognized_values_are_never_guessed(self):
        self.assertEqual(normalize_git_mode_map_state(None), (None, None))
        self.assertEqual(normalize_git_mode_map_state("no_such_state"), (None, None))

    def test_every_reason_has_a_producing_condition(self):
        # An aspirational reason vocabulary would list causes ArchLens cannot
        # actually distinguish. Today exactly one condition is detected.
        self.assertEqual(
            [member.value for member in GitModeMapReason], ["not_git_repository"]
        )


class DerivedSetTests(unittest.TestCase):
    """The frozensets are projections of the enums, not second copies."""

    def test_sets_match_their_enums(self):
        for known, enum_type in (
            (KNOWN_ANALYSIS_STATUSES, AnalysisStatus),
            (KNOWN_METRIC_STATUSES, MetricStatus),
            (KNOWN_PARTIAL_ORIGINS, PartialOrigin),
        ):
            with self.subTest(enum=enum_type.__name__):
                self.assertEqual(known, {member.value for member in enum_type})

    def test_git_mode_states_are_the_enum_plus_the_legacy_wire_value(self):
        self.assertEqual(
            KNOWN_GIT_MODE_STATES,
            {member.value for member in GitModeMapStatus}
            | {GIT_MODE_MAP_NOT_GIT_WIRE_VALUE},
        )

    def test_diagnostics_reexports_the_same_objects(self):
        # Not merely equal: the same object, so a future edit cannot reintroduce
        # a second definition that happens to agree today.
        from modules import diagnostics

        self.assertIs(diagnostics.KNOWN_GIT_MODE_STATES, KNOWN_GIT_MODE_STATES)
        self.assertIs(diagnostics.KNOWN_ANALYSIS_STATUSES, KNOWN_ANALYSIS_STATUSES)
        self.assertIs(diagnostics.KNOWN_METRIC_STATUSES, KNOWN_METRIC_STATUSES)
        self.assertIs(diagnostics.KNOWN_PARTIAL_ORIGINS, KNOWN_PARTIAL_ORIGINS)


if __name__ == "__main__":
    unittest.main()
