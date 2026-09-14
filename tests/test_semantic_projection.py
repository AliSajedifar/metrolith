"""The semantic-equivalence oracle, tested adversarially.

A comparison oracle is only worth having if it can fail. Every test here is
paired: something that must **not** change the hash, and something that must.
An oracle that ignored too much would pass the first half and quietly destroy
the second, so the mutation tests below are the real subject.

Runs are built by driving the **real** production pipeline and artifact writer
over a synthetic Python source tree, with acquisition patched to hand back a
local directory. That keeps these tests hermetic and offline, and — because
Python is parsed by the standard library's ``ast`` rather than tree-sitter —
runnable even in an environment with no grammars installed. The pattern is
borrowed from ``tests/test_finalization_self_validation_v35.py``.
"""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from modules.acquisition import AcquiredRepository, AcquisitionRecord
from modules.benchmark_runner import run_benchmark
from modules.config import AnalysisConfig
from validation.scripts.semantic_projection import (
    ABSENT_EQUALS_NULL,
    NONSEMANTIC_RUNTIME_FIELDS,
    ORDERED_LISTS,
    UNORDERED_LISTS,
    RunNotComparable,
    UnclassifiedListField,
    hash_measurement_projection,
    measurement_semantic_hash,
    semantic_projection,
)

ANALYZED_SHA = "a" * 40
OTHER_SHA = "b" * 40


class RunFixture(unittest.TestCase):
    """Builds a real finalized run from a synthetic Python tree."""

    SOURCE = "class App:\n    def run(self):\n        return 1\n"

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repo = self.root / "fixture"
        self.repo.mkdir()
        (self.repo / "app.py").write_text(self.SOURCE, encoding="utf-8")
        self.input = self.root / "repositories.csv"
        self.input.write_text(
            "url,architecture_type,expected_language,commit_sha,enabled,notes\n"
            f"https://github.com/acme/mono,monolith,Python,{ANALYZED_SHA},true,one\n",
            encoding="utf-8",
        )
        self._sha = ANALYZED_SHA

    @contextmanager
    def _acquire(self, spec, config, mode="latest", progress=None):
        del config, mode, progress
        yield AcquiredRepository(
            self.repo,
            AcquisitionRecord(
                repository_url=spec.url,
                repository_owner=spec.owner,
                repository_name=spec.repository_name,
                requested_commit_sha=spec.commit_sha,
                analyzed_commit_sha=self._sha,
                resolved_ref="refs/heads/main",
                default_branch="main",
                acquisition_mode="offline",
                cache_status="reused",
                remote_checked=False,
                fetch_timestamp=None,
                checkout_timestamp="2026-08-01T00:00:00Z",
                commit_verification_status="verified",
                fetch_method="offline_cache",
            ),
        )

    def build_run(self, output="output") -> Path:
        config = AnalysisConfig.from_env(
            output_root=self.root / output,
            cache_root=self.root / "cache",
            temporary_directory=self.root / "temp",
            workers=1,
        )
        with (
            patch("modules.benchmark_runner.acquire_repository", self._acquire),
            patch("modules.benchmark_runner._distribution_version", return_value="4.0.1"),
        ):
            summary = run_benchmark(
                [self.input], config, "offline", command_line_arguments=["test"]
            )
        return Path(summary["run_directory"])


class ProjectionShapeTests(RunFixture):
    def test_a_real_run_projects_and_hashes(self):
        run = self.build_run()
        projection = semantic_projection(run)
        from validation.scripts.semantic_projection import (
            SEMANTIC_PROJECTION_VERSION,
        )

        self.assertEqual(
            projection["semantic_projection_version"],
            SEMANTIC_PROJECTION_VERSION,
        )
        self.assertEqual(len(projection["semantic"]["repositories"]), 1)
        self.assertRegex(measurement_semantic_hash(run), r"^[0-9a-f]{64}$")

    def test_the_same_run_hashes_identically_when_reprojected(self):
        run = self.build_run()
        self.assertEqual(measurement_semantic_hash(run), measurement_semantic_hash(run))

    def test_environment_evidence_is_retained_but_not_hashed(self):
        # Provenance is never discarded merely because it varies; it is simply
        # not part of the equality contract.
        run = self.build_run()
        projection = semantic_projection(run)
        self.assertIn("environment", projection)
        self.assertIn("python_version_exact", projection["environment"])
        self.assertNotIn("environment", hash_measurement_projection(projection))

        mutated = copy.deepcopy(projection)
        mutated["environment"]["python_version_exact"] = "9.9.9"
        self.assertEqual(hash_measurement_projection(projection), hash_measurement_projection(mutated))

    def test_two_independent_runs_of_the_same_source_are_equivalent(self):
        """The property every later phase depends on."""
        first = self.build_run("out_a")
        second = self.build_run("out_b")
        self.assertEqual(measurement_semantic_hash(first), measurement_semantic_hash(second))
        self.assertEqual(
            semantic_projection(first)["semantic"],
            semantic_projection(second)["semantic"],
        )


class MutationTests(RunFixture):
    """Paired: what must not move the hash, and what must."""

    def setUp(self):
        super().setUp()
        self.run = self.build_run()
        self.baseline = measurement_semantic_hash(self.run)

    def _rehash_after(self, mutate) -> str:
        analysis = json.loads((self.run / "analysis.json").read_text(encoding="utf-8"))
        mutate(analysis)
        (self.run / "analysis.json").write_text(
            json.dumps(analysis, indent=2), encoding="utf-8"
        )
        return measurement_semantic_hash(self.run)

    # -- must NOT change the hash -------------------------------------------

    def test_changing_a_timestamp_does_not_change_the_hash(self):
        def mutate(analysis):
            analysis[0]["repository_start_timestamp"] = "2099-01-01T00:00:00Z"
            analysis[0]["repository_end_timestamp"] = "2099-01-01T00:00:09Z"

        self.assertEqual(self._rehash_after(mutate), self.baseline)

    def test_changing_a_duration_does_not_change_the_hash(self):
        def mutate(analysis):
            analysis[0]["repository_duration_seconds"] = 999.123456
            analysis[0]["timings"] = {"total_repository_seconds": 999.123456}

        self.assertEqual(self._rehash_after(mutate), self.baseline)

    def test_changing_cache_warmth_does_not_change_the_hash(self):
        def mutate(analysis):
            acquisition = analysis[0].setdefault("acquisition", {})
            acquisition["cache_hit"] = not acquisition.get("cache_hit", False)
            acquisition["network_contacted"] = True

        self.assertEqual(self._rehash_after(mutate), self.baseline)

    def test_reordering_object_keys_does_not_change_the_hash(self):
        def mutate(analysis):
            analysis[0] = dict(reversed(list(analysis[0].items())))

        self.assertEqual(self._rehash_after(mutate), self.baseline)

    # -- MUST change the hash -----------------------------------------------

    def test_changing_lines_of_code_changes_the_hash(self):
        def mutate(analysis):
            aggregate = analysis[0]["metrics"]["aggregate"]
            aggregate["lines_of_code"] = (aggregate.get("lines_of_code") or 0) + 1

        self.assertNotEqual(self._rehash_after(mutate), self.baseline)

    def test_changing_an_entity_count_changes_the_hash(self):
        def mutate(analysis):
            aggregate = analysis[0]["metrics"]["aggregate"]
            aggregate["methods_functions"] = (
                aggregate.get("methods_functions") or 0
            ) + 1

        self.assertNotEqual(self._rehash_after(mutate), self.baseline)

    def test_changing_the_analyzed_revision_changes_the_hash(self):
        def mutate(analysis):
            analysis[0].setdefault("acquisition", {})["analyzed_commit_sha"] = OTHER_SHA

        self.assertNotEqual(self._rehash_after(mutate), self.baseline)

    def test_changing_measurement_status_changes_the_hash(self):
        def mutate(analysis):
            analysis[0]["analysis_status"] = "partial"

        self.assertNotEqual(self._rehash_after(mutate), self.baseline)

    def test_changing_a_metric_status_changes_the_hash(self):
        def mutate(analysis):
            analysis[0]["metrics"]["aggregate"]["loc_status"] = "partial"

        self.assertNotEqual(self._rehash_after(mutate), self.baseline)

    def test_introducing_an_authoritative_diagnostic_changes_the_hash(self):
        def mutate(analysis):
            analysis[0]["metrics"]["parse_errors"] = [
                {
                    "file_path": "app.py",
                    "error_category": "parser_execution_failure",
                    "message": "injected",
                }
            ]

        self.assertNotEqual(self._rehash_after(mutate), self.baseline)

    def test_changing_repository_identity_changes_the_hash(self):
        def mutate(analysis):
            analysis[0]["architecture_type"] = "microservices"

        self.assertNotEqual(self._rehash_after(mutate), self.baseline)

    def test_changing_an_integer_to_a_float_changes_the_hash(self):
        # A metric that changed type changed meaning: 1 and 1.0 are not equal.
        def mutate(analysis):
            aggregate = analysis[0]["metrics"]["aggregate"]
            aggregate["lines_of_code"] = float(aggregate.get("lines_of_code") or 0)

        self.assertNotEqual(self._rehash_after(mutate), self.baseline)


class RepositoryOrderingTests(RunFixture):
    """Cohort order is not semantic and must not cause false inequality."""

    def setUp(self):
        super().setUp()
        self.input.write_text(
            "url,architecture_type,expected_language,commit_sha,enabled,notes\n"
            f"https://github.com/acme/alpha,monolith,Python,{ANALYZED_SHA},true,one\n"
            f"https://github.com/acme/beta,monolith,Python,{ANALYZED_SHA},true,two\n",
            encoding="utf-8",
        )

    def test_reversing_repository_order_does_not_change_the_hash(self):
        run = self.build_run()
        baseline = measurement_semantic_hash(run)
        analysis = json.loads((run / "analysis.json").read_text(encoding="utf-8"))
        self.assertEqual(len(analysis), 2, "fixture must produce two repositories")
        (run / "analysis.json").write_text(
            json.dumps(list(reversed(analysis)), indent=2), encoding="utf-8"
        )
        self.assertEqual(measurement_semantic_hash(run), baseline)

    def test_dropping_a_repository_does_change_the_hash(self):
        # Guards the test above against passing because ordering is ignored by
        # ignoring the repositories themselves.
        run = self.build_run()
        baseline = measurement_semantic_hash(run)
        analysis = json.loads((run / "analysis.json").read_text(encoding="utf-8"))
        (run / "analysis.json").write_text(
            json.dumps(analysis[:1], indent=2), encoding="utf-8"
        )
        self.assertNotEqual(measurement_semantic_hash(run), baseline)


class RefusalTests(RunFixture):
    """A malformed run is never comparable, however projectable it looks."""

    def test_a_malformed_eager_artifact_is_refused(self):
        run = self.build_run()
        (run / "analysis.json").write_text("{ not json", encoding="utf-8")
        with self.assertRaises(RunNotComparable):
            semantic_projection(run)

    def test_a_malformed_optional_inventory_is_refused(self):
        """The case that motivates checking errors *after* forcing the reads.

        A malformed inventory leaves the lifecycle at ``finalized_valid`` by
        design, and its fault is recorded only when the inventory is read. A
        projection that trusted the lifecycle alone would happily compare the
        readable half of this run.
        """
        run = self.build_run()
        inventories = sorted((run / "file_inventory").glob("*.json"))
        self.assertTrue(inventories, "fixture must produce an inventory")
        inventories[0].write_text("{ not json", encoding="utf-8")
        with self.assertRaises(RunNotComparable):
            semantic_projection(run)

    def test_a_missing_run_is_refused(self):
        with self.assertRaises(RunNotComparable):
            semantic_projection(self.root / "no-such-run")

    def test_refusal_is_not_a_falsy_projection(self):
        # Callers must never be able to treat a refusal as "empty but equal".
        run = self.build_run()
        (run / "analysis.json").write_text("{ not json", encoding="utf-8")
        try:
            semantic_projection(run)
        except RunNotComparable as exc:
            self.assertIn(str(run), str(exc))
        else:  # pragma: no cover
            self.fail("a malformed run was projected")


class CanonicalizationContractTests(unittest.TestCase):
    """The serialization rules, exercised without touching the filesystem."""

    @staticmethod
    def _wrap(semantic):
        return {"semantic_projection_version": "1.1.0", "semantic": semantic}

    def test_key_order_is_not_the_contract(self):
        left = self._wrap({"a": 1, "b": 2})
        right = self._wrap({"b": 2, "a": 1})
        self.assertEqual(hash_measurement_projection(left), hash_measurement_projection(right))

    def test_int_and_float_are_distinct(self):
        self.assertNotEqual(
            hash_measurement_projection(self._wrap({"loc": 1})),
            hash_measurement_projection(self._wrap({"loc": 1.0})),
        )

    def test_null_and_absent_are_equal_only_through_the_allowlist(self):
        # The projection normalizes absent to null; at the hash layer they are
        # genuinely different structures, which is why normalization happens in
        # `_select` rather than being assumed here.
        self.assertNotEqual(
            hash_measurement_projection(self._wrap({"a": None})),
            hash_measurement_projection(self._wrap({})),
        )

    def test_unicode_is_not_escaped_away(self):
        self.assertNotEqual(
            hash_measurement_projection(self._wrap({"path": "café.py"})),
            hash_measurement_projection(self._wrap({"path": "cafe.py"})),
        )


class LegacyPayloadDriftTests(RunFixture):
    """The new oracle must not become a second, divergent field list.

    ``validation.scripts.validate_outputs.semantic_payload`` still backs
    ``archlens compare`` and is deliberately left alone in this phase. Two
    independent allowlists over the same artifacts is exactly the drift that
    produced the ``git_mode_map_status`` defect, so the overlap is pinned here
    until the legacy payload is retired.
    """

    def test_projection_covers_every_legacy_repository_field(self):
        from validation.scripts.validate_outputs import normalized_repository

        run = self.build_run()
        analysis = json.loads((run / "analysis.json").read_text(encoding="utf-8"))
        legacy = normalized_repository(analysis[0], None)

        projected = semantic_projection(run)["semantic"]["repositories"][0]
        covered = set()
        for section in ("identity", "measurement", "git_evidence"):
            covered |= set(projected[section])

        missing = sorted(set(legacy) - covered - {"inventory"})
        self.assertEqual(
            missing,
            [],
            f"the legacy semantic payload carries {missing}, which the canonical "
            f"projection does not; the oracle would miss changes compare catches",
        )

    def test_projection_covers_every_legacy_inventory_record_field(self):
        from validation.scripts.validate_outputs import normalized_repository
        from validation.scripts.semantic_projection import INVENTORY_RECORD_FIELDS

        record = {"relative_path": "app.py"}
        legacy = normalized_repository(
            {"repository_url": "u", "architecture_type": "monolith", "metrics": {}},
            {"files": [record]},
        )
        legacy_fields = set(legacy["inventory"]["files"][0])
        missing = sorted(legacy_fields - set(INVENTORY_RECORD_FIELDS))
        self.assertEqual(missing, [], f"inventory field drift: {missing}")

    def test_both_selections_carry_analysis_status(self):
        """Was a real defect in the production comparison path, now fixed.

        See ``ProductionCompareStatusTests`` for the behavioural regression.
        """
        from validation.scripts.validate_outputs import normalized_repository

        run = self.build_run()
        analysis = json.loads((run / "analysis.json").read_text(encoding="utf-8"))
        self.assertIn("analysis_status", normalized_repository(analysis[0], None))
        projected = semantic_projection(run)["semantic"]["repositories"][0]
        self.assertIn("analysis_status", projected["measurement"])


class ProductionCompareStatusTests(RunFixture):
    """`archlens compare` must see a repository change terminal status.

    This exercises the production comparison path exactly as ``pipeline.py``
    assembles it — ``validate_outputs.semantic_payload`` fed to
    ``compare_semantic_runs.differences`` — and deliberately does **not** import
    the canonical projection. Production must not depend on
    ``semantic_projection`` to be correct; the layering boundary is the point.
    """

    @staticmethod
    def _compare(left: Path, right: Path):
        from validation.scripts.compare_semantic_runs import differences
        from validation.scripts.validate_outputs import semantic_payload

        return differences(semantic_payload(left), semantic_payload(right))

    def _run_with_status(self, output: str, status: str) -> Path:
        run = self.build_run(output)
        path = run / "analysis.json"
        analysis = json.loads(path.read_text(encoding="utf-8"))
        analysis[0]["analysis_status"] = status
        path.write_text(json.dumps(analysis, indent=2), encoding="utf-8")
        return run

    def test_complete_to_partial_is_detected(self):
        complete = self._run_with_status("out_complete", "complete")
        partial = self._run_with_status("out_partial", "partial")
        found = self._compare(complete, partial)
        self.assertTrue(found, "compare reported no difference for complete -> partial")
        self.assertTrue(
            any("analysis_status" in item["path"] for item in found),
            f"the difference was not attributed to analysis_status: {found}",
        )

    def test_partial_to_complete_is_detected(self):
        partial = self._run_with_status("out_partial2", "partial")
        complete = self._run_with_status("out_complete2", "complete")
        found = self._compare(partial, complete)
        self.assertTrue(found, "compare reported no difference for partial -> complete")
        self.assertTrue(
            any("analysis_status" in item["path"] for item in found),
            f"the difference was not attributed to analysis_status: {found}",
        )

    def test_identical_status_still_compares_equal(self):
        # Guards the two tests above against passing because everything differs.
        left = self._run_with_status("out_same_a", "complete")
        right = self._run_with_status("out_same_b", "complete")
        self.assertEqual(self._compare(left, right), [])

    def test_production_compare_does_not_import_the_canonical_projection(self):
        """The minimal fix must not have been a layering shortcut."""
        import subprocess
        import sys

        script = (
            "import json,sys;"
            "import validation.scripts.validate_outputs;"
            "import validation.scripts.compare_semantic_runs;"
            "print(json.dumps(sorted(sys.modules)))"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=str(Path(__file__).resolve().parent.parent),
            capture_output=True,
            text=True,
            check=True,
        )
        loaded = set(json.loads(result.stdout))
        self.assertNotIn("validation.scripts.semantic_projection", loaded)
        self.assertEqual(
            sorted(name for name in loaded if name.startswith("modules")), []
        )


class AbsenceTests(RunFixture):
    """Absent and explicitly-null are different facts."""

    def test_absent_and_null_do_not_hash_alike(self):
        run = self.build_run()
        baseline = measurement_semantic_hash(run)
        path = run / "analysis.json"
        analysis = json.loads(path.read_text(encoding="utf-8"))

        analysis[0]["git_mode_map_status"] = None
        path.write_text(json.dumps(analysis, indent=2), encoding="utf-8")
        explicit_null = measurement_semantic_hash(run)

        del analysis[0]["git_mode_map_status"]
        path.write_text(json.dumps(analysis, indent=2), encoding="utf-8")
        absent = measurement_semantic_hash(run)

        # Absent means "never attempted"; null means "attempted, no value".
        self.assertNotEqual(explicit_null, absent)
        self.assertNotEqual(baseline, absent)

    def test_absence_equivalence_is_claimed_for_no_field(self):
        """Adding a field here is a claim about that field's contract.

        The audit behind this found the opposite of equivalence: the
        ``git_mode_map_*`` fields are absent on results that never reached
        inventory and null once it ran. If this set ever becomes non-empty, each
        member needs a cited contract reason and its own test.
        """
        self.assertEqual(ABSENT_EQUALS_NULL, frozenset())

    def test_a_field_listed_as_equivalent_actually_collapses(self):
        # Proves the mechanism works, so that populating the set later is a
        # one-line change rather than a redesign.
        from validation.scripts import semantic_projection as module

        source_with = {"a": None}
        source_without: dict[str, object] = {}
        with patch.object(module, "ABSENT_EQUALS_NULL", frozenset({"a"})):
            self.assertEqual(
                module._select(source_with, ["a"]),
                module._select(source_without, ["a"]),
            )


class ListClassificationTests(RunFixture):
    """No list may enter the projection without declared ordering semantics."""

    def test_every_list_field_is_classified(self):
        run = self.build_run()
        projection = semantic_projection(run)

        unclassified: set[str] = set()

        def walk(value, field=None):
            if isinstance(value, dict):
                for key, item in value.items():
                    walk(item, key)
            elif isinstance(value, list):
                if field not in ORDERED_LISTS and field not in UNORDERED_LISTS:
                    unclassified.add(str(field))
                for item in value:
                    walk(item, field)

        walk(projection)
        self.assertEqual(sorted(unclassified), [])

    def test_the_two_classifications_are_disjoint(self):
        self.assertEqual(ORDERED_LISTS & UNORDERED_LISTS, frozenset())

    def test_an_unclassified_list_field_is_refused(self):
        from validation.scripts import semantic_projection as module

        with self.assertRaises(UnclassifiedListField):
            module._canonical([1, 2], field="newly_added_list")

    def test_ordered_lists_keep_their_order(self):
        from validation.scripts import semantic_projection as module

        # Application order composes; reversing it is a different parse.
        forward = module._canonical(["a", "b"], field="fallback_strategies")
        backward = module._canonical(["b", "a"], field="fallback_strategies")
        self.assertEqual(forward, ["a", "b"])
        self.assertNotEqual(forward, backward)

    def test_unordered_lists_are_normalized(self):
        from validation.scripts import semantic_projection as module

        self.assertEqual(
            module._canonical(["b", "a"], field="affected_metrics"),
            module._canonical(["a", "b"], field="affected_metrics"),
        )

    def test_nul_positions_and_contexts_stay_index_aligned(self):
        from validation.scripts import semantic_projection as module

        positions = module._canonical([9, 1, 5], field="nul_positions")
        contexts = module._canonical(
            ["code", "quoted_string", "comment"], field="nul_contexts"
        )
        self.assertEqual(positions, [9, 1, 5])
        self.assertEqual(contexts, ["code", "quoted_string", "comment"])


class CrossRootRegenerationTests(unittest.TestCase):
    """Same source, two unrelated roots: the measurement digest must match.

    Run ids, timestamps, durations and every temporary path differ naturally
    between the two executions. The synthetic tree includes a file that fails to
    parse, so diagnostic *message* strings are produced too — absolute paths
    leak through message text far more easily than through the explicit path
    fields, and only a genuinely different root can catch that.
    """

    SOURCES = {
        "app.py": "class App:\n    def run(self):\n        return 1\n",
        "broken.py": "def oops(:\n",
    }

    def _build(self, label: str) -> tuple[Path, Path]:
        temporary = tempfile.TemporaryDirectory(prefix=f"archlens_{label}_")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        repo = root / f"src_{label}"
        repo.mkdir()
        for name, text in self.SOURCES.items():
            (repo / name).write_text(text, encoding="utf-8")
        source_csv = root / "repositories.csv"
        source_csv.write_text(
            "url,architecture_type,expected_language,commit_sha,enabled,notes\n"
            f"https://github.com/acme/mono,monolith,Python,{ANALYZED_SHA},true,one\n",
            encoding="utf-8",
        )

        @contextmanager
        def acquire(spec, config, mode="latest", progress=None):
            del config, mode, progress
            yield AcquiredRepository(
                repo,
                AcquisitionRecord(
                    repository_url=spec.url,
                    repository_owner=spec.owner,
                    repository_name=spec.repository_name,
                    requested_commit_sha=spec.commit_sha,
                    analyzed_commit_sha=ANALYZED_SHA,
                    resolved_ref="refs/heads/main",
                    default_branch="main",
                    acquisition_mode="offline",
                    cache_status="reused",
                    remote_checked=False,
                    fetch_timestamp=None,
                    checkout_timestamp="2026-08-01T00:00:00Z",
                    commit_verification_status="verified",
                    fetch_method="offline_cache",
                ),
            )

        config = AnalysisConfig.from_env(
            output_root=root / "output",
            cache_root=root / "cache",
            temporary_directory=root / "temp",
            workers=1,
        )
        with (
            patch("modules.benchmark_runner.acquire_repository", acquire),
            patch("modules.benchmark_runner._distribution_version", return_value="4.0.1"),
        ):
            summary = run_benchmark(
                [source_csv], config, "offline", command_line_arguments=["test"]
            )
        return Path(summary["run_directory"]), root

    def test_two_roots_produce_the_same_measurement_digest(self):
        first, first_root = self._build("alpha")
        second, second_root = self._build("beta_longer_name")
        self.assertNotEqual(str(first_root), str(second_root))
        self.assertNotEqual(first.name, second.name, "run ids must differ naturally")

        self.assertEqual(
            measurement_semantic_hash(first), measurement_semantic_hash(second)
        )

    def test_no_absolute_path_survives_into_the_hashed_section(self):
        run, root = self._build("gamma")
        semantic = semantic_projection(run)["semantic"]
        rendered = json.dumps(semantic)
        for leaked in (str(root), str(root).replace("\\", "\\\\"), root.name):
            with self.subTest(fragment=leaked):
                self.assertNotIn(leaked, rendered)

    def test_the_fixture_actually_produces_a_diagnostic(self):
        """Otherwise the leak test above proves nothing about message strings."""
        run, _ = self._build("delta")
        analysis = json.loads((run / "analysis.json").read_text(encoding="utf-8"))
        metrics = analysis[0]["metrics"]
        recorded = (metrics.get("parse_errors") or []) + (
            metrics.get("parser_diagnostics") or []
        )
        self.assertTrue(recorded, "fixture produced no diagnostic to inspect")


class ExclusionDocumentationTests(unittest.TestCase):
    """Every dropped field is documented, not merely omitted."""

    def test_each_excluded_runtime_field_states_why(self):
        for field, reason in NONSEMANTIC_RUNTIME_FIELDS.items():
            with self.subTest(field=field):
                self.assertTrue(reason.strip(), f"{field} has no stated reason")

    def test_timing_and_path_fields_are_covered(self):
        for field in (
            "run_started_at",
            "repository_duration_seconds",
            "measurement_wall_seconds",
            "git_commands",
            "workspace_root",
        ):
            self.assertIn(field, NONSEMANTIC_RUNTIME_FIELDS)


class ScopeHashConstructionVersionTests(unittest.TestCase):
    """The construction algorithm is part of the meaning of the digest.

    Projection 2.1.0 carries `analysis_scope_hash_version` beside
    `analysis_scope_hash`. Without it, two artifacts produced by different
    constructions could be compared as though their digests were the same kind
    of value — which they are not, because construction 2.0.0 changed what the
    digest covers (docs/ANALYSIS_SCOPE_HASH_V2.md).

    These drive `_repository_projection` directly rather than through a full
    run, because the property under test is what the projection *carries*, and
    a real run can only ever produce the current construction version.
    """

    @staticmethod
    def _repository(**overrides):
        base = {
            "subject_key": "github.com/acme/app",
            "subject_key_basis": "remote_locator",
            "source_mode": "remote_git_revision",
            "analysis_scope_hash": "sha256:" + "a" * 64,
            "analysis_scope_hash_version": "2.0.0",
            "repository_url": "https://github.com/acme/app",
            "repository_owner": "acme",
            "repository_name": "app",
            "architecture_type": "monolith",
            "expected_language": "Python",
            "analysis_status": "complete",
            "metrics": {"aggregate": {"lines_of_code": 10}},
            "acquisition": {"analyzed_commit_sha": "b" * 40},
        }
        base.update(overrides)
        return base

    def _identity(self, **overrides):
        from validation.scripts.semantic_projection import _repository_projection

        return _repository_projection(self._repository(**overrides), None)["identity"]

    def test_the_construction_version_is_visible_in_the_projection(self):
        self.assertEqual(
            self._identity()["analysis_scope_hash_version"], "2.0.0"
        )

    def test_the_same_digest_under_different_constructions_is_not_equivalent(self):
        """The property that motivated the change.

        Two artifacts can carry byte-identical digest strings produced by
        different constructions. Treating those as equal scope evidence would
        be a false equality, so the projections must differ.
        """
        old = self._identity(analysis_scope_hash_version="1.0.0")
        new = self._identity(analysis_scope_hash_version="2.0.0")
        self.assertEqual(
            old["analysis_scope_hash"], new["analysis_scope_hash"],
            "the fixture must hold the digest constant for this to be a test "
            "about the construction version",
        )
        self.assertNotEqual(old, new)

    def test_identical_version_and_digest_remain_equal(self):
        self.assertEqual(self._identity(), self._identity())

    def test_a_missing_construction_version_is_not_collapsed_into_a_present_one(self):
        """Absence is its own state, never `1.0.0` and never null-as-declared.

        A historical artifact predating the field recorded no construction
        identity at all. Projecting that as an explicit null would make it
        indistinguishable from a producer that declared null deliberately.
        """
        recorded = self._identity(analysis_scope_hash_version="2.0.0")
        payload = self._repository()
        del payload["analysis_scope_hash_version"]

        from validation.scripts.semantic_projection import _repository_projection

        absent = _repository_projection(payload, None)["identity"]
        self.assertNotIn("analysis_scope_hash_version", absent)
        self.assertNotEqual(absent, recorded)

        explicit_null = self._identity(analysis_scope_hash_version=None)
        self.assertIn("analysis_scope_hash_version", explicit_null)
        self.assertNotEqual(
            absent, explicit_null,
            "absent and explicitly-null must not canonicalize to one value",
        )

    def test_the_version_change_was_recorded_on_the_projection(self):
        """Adding the field moved the projection version, and only that.

        The artifact contract later moved to 1.8.0 for an unrelated
        correction-only reason (RECOVERY-SHA), so pinning an artifact version
        here would conflate two independent version lines. The property under
        test is that the projection carries its own version and bumped it.
        """
        from validation.scripts.semantic_projection import (
            REPOSITORY_PROVENANCE_FIELDS,
            SEMANTIC_PROJECTION_VERSION,
        )

        self.assertEqual(SEMANTIC_PROJECTION_VERSION, "2.3.0")
        self.assertIn("analysis_scope_hash_version", REPOSITORY_PROVENANCE_FIELDS)

    def test_the_projected_version_reaches_the_measurement_digest(self):
        """It must be inside the hashed payload, not merely reported."""
        from validation.scripts.semantic_projection import (
            SEMANTIC_PROJECTION_VERSION,
            hash_measurement_projection,
        )

        def projection(version):
            return {
                "semantic_projection_version": SEMANTIC_PROJECTION_VERSION,
                "semantic": {
                    "run": {},
                    "repositories": [{"identity": self._identity(
                        analysis_scope_hash_version=version
                    )}],
                },
            }

        self.assertNotEqual(
            hash_measurement_projection(projection("1.0.0")),
            hash_measurement_projection(projection("2.0.0")),
        )


if __name__ == "__main__":
    unittest.main()
