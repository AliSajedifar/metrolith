"""R0: benchmark qualification as an authority separate from measurement.

The campaign this file belongs to closed a scope/interpretation defect, not an
arithmetic one: a complete measurement of a non-representative scope could be
presented as a repository-level result. So the assertions here are mostly about
what qualification is *forbidden* to do — change a metric, invent an
adjudicator, promote its own admission, or let a projection disagree with its
authority.

Several tests are **mutation tests**: they deliberately corrupt a value and
assert the guard rejects it. A guard that cannot be made to fail is not
evidence, and this campaign's own history contains a completeness layer that
silently switched itself off at a minor version bump and read exactly like a
clean result.
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
from modules.benchmark_qualification import (
    MODE_BENCHMARK_QUALIFIED,
    MODE_NOT_REQUESTED,
    QualificationError,
    build_artifact,
    build_records,
    canonical_json,
    canonical_sha256,
    derive_benchmark_usability,
    derive_eligibility,
    derive_usable_metric_families,
    evaluate_readiness,
    load_registry,
    reconcile,
)
from modules.benchmark_runner import run_benchmark
from modules.config import AnalysisConfig, QUALIFICATION_PROFILE
from validation.artifact_io.schema_store import validate_document

REPOSITORY = Path(__file__).resolve().parent.parent
ACCEPTED_REGISTRY = REPOSITORY / "tests" / "fixtures" / "benchmark_qualification.v1.json"
SHA = "a" * 40


def _result(
    *,
    subject="github.com/acme/widget",
    metric_status="complete",
    source_files_status="complete",
    loc_status="complete",
    classes_status="complete",
    methods_status="complete",
    complexity_status="complete",
    scope_hash="sha256:deadbeef",
    commit=SHA,
) -> dict:
    """A minimal measurement result carrying only what qualification reads."""
    return {
        "subject_key": subject,
        "repository_url": f"https://{subject}",
        "acquisition": {"analyzed_commit_sha": commit},
        "analysis_scope_hash": scope_hash,
        "analysis_scope_hash_version": "2.0.0",
        "metrics": {
            "aggregate": {
                "metric_status": metric_status,
                "source_files_status": source_files_status,
                "loc_status": loc_status,
                "classes_structs_status": classes_status,
                "methods_functions_status": methods_status,
                "source_files": 10,
                "lines_of_code": 100,
                "classes_structs": 2,
                "methods_functions": 5,
            },
            "complexity": {"status": complexity_status, "aggregate": {"callable_count": 5}},
        },
    }


def _registry_record(result: dict, **overrides) -> dict:
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
            "supported_first_party_files": 10,
            "supported_first_party_bytes": 1000,
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
    record.update(overrides)
    return record


def _registry_document(records) -> dict:
    return {
        "registry_schema_version": "1.0.0",
        "source_id": "test.v1",
        "qualification_profile": QUALIFICATION_PROFILE,
        "records": list(records),
    }


@contextmanager
def _written(document) -> Path:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "registry.json"
        path.write_text(json.dumps(document, indent=2), encoding="utf-8")
        yield path


# --------------------------------------------------------------- registry ---


class RegistryLoadingTests(unittest.TestCase):
    def test_duplicate_primary_binding_is_fatal(self):
        """Two accepted decisions about identical bytes cannot be merged.

        Not "last wins", not "most restrictive wins": either choice publishes an
        adjudication nobody made.
        """
        result = _result()
        document = _registry_document(
            [_registry_record(result), _registry_record(result, record_id="rec-2")]
        )
        with _written(document) as path:
            with self.assertRaises(QualificationError) as caught:
                load_registry(path)
        self.assertIn("duplicate", str(caught.exception).lower())

    def test_a_registry_supplying_a_derived_field_is_rejected(self):
        """The schema refuses derived values rather than ignoring them.

        Silently ignoring is indistinguishable from acceptance to whoever wrote
        the file, which is how a second authority for measurement facts gets
        established without anyone deciding to establish one.
        """
        for field in (
            "usable_metric_families",
            "benchmark_usability",
            "repository_level_comparison_eligible",
        ):
            with self.subTest(field=field):
                record = _registry_record(_result())
                record[field] = ["source_files"] if "families" in field else True
                with _written(_registry_document([record])) as path:
                    with self.assertRaises(QualificationError):
                        load_registry(path)

    def test_an_automatic_adjudication_mode_cannot_be_expressed(self):
        """No enum member exists for a rule-derived verdict."""
        record = _registry_record(_result())
        record["adjudication"]["mode"] = "automatic_extension_count_rule"
        with _written(_registry_document([record])) as path:
            with self.assertRaises(QualificationError):
                load_registry(path)

    def test_a_non_unresolved_verdict_requires_a_reason_code(self):
        record = _registry_record(_result(), representativeness_reason_codes=[])
        with _written(_registry_document([record])) as path:
            with self.assertRaises(QualificationError):
                load_registry(path)

    def test_unsupported_code_without_language_evidence_is_refused(self):
        record = _registry_record(_result())
        record["evidence_summary"]["unsupported_first_party_files"] = 12
        with _written(_registry_document([record])) as path:
            with self.assertRaises(QualificationError):
                load_registry(path)

    def test_a_foreign_profile_is_refused(self):
        document = _registry_document([_registry_record(_result())])
        document["qualification_profile"] = "some_other_profile_v9"
        with _written(document) as path:
            with self.assertRaises(QualificationError):
                load_registry(path)

    def test_the_registry_hash_is_over_the_exact_file_bytes(self):
        """Not over a re-serialization: a reproduction must be able to match a file."""
        import hashlib

        document = _registry_document([_registry_record(_result())])
        with _written(document) as path:
            registry = load_registry(path)
            self.assertEqual(
                registry.registry_sha256,
                hashlib.sha256(path.read_bytes()).hexdigest(),
            )

    def test_lookup_is_exact_on_every_component(self):
        """A near miss is a miss. Each of the five components is load-bearing."""
        result = _result()
        with _written(_registry_document([_registry_record(result)])) as path:
            registry = load_registry(path)
            self.assertIsNotNone(registry.lookup(
                {
                    "qualification_profile": QUALIFICATION_PROFILE,
                    "subject_key": result["subject_key"],
                    "analyzed_commit_sha": SHA,
                    "analysis_scope_hash": result["analysis_scope_hash"],
                    "analysis_scope_hash_version": "2.0.0",
                }
            ))
            for field, wrong in (
                ("subject_key", "github.com/acme/other"),
                ("analyzed_commit_sha", "b" * 40),
                ("analysis_scope_hash", "sha256:different"),
                ("analysis_scope_hash_version", "1.0.0"),
                ("qualification_profile", "other_profile"),
            ):
                with self.subTest(field=field):
                    binding = {
                        "qualification_profile": QUALIFICATION_PROFILE,
                        "subject_key": result["subject_key"],
                        "analyzed_commit_sha": SHA,
                        "analysis_scope_hash": result["analysis_scope_hash"],
                        "analysis_scope_hash_version": "2.0.0",
                    }
                    binding[field] = wrong
                    self.assertIsNone(registry.lookup(binding))

    def test_canonical_hashing_is_order_independent_and_stable(self):
        first = {"b": 2, "a": [1, {"z": 1, "y": 2}]}
        second = {"a": [1, {"y": 2, "z": 1}], "b": 2}
        self.assertEqual(canonical_sha256(first), canonical_sha256(second))
        self.assertEqual(canonical_json(first), canonical_json(second))


# ------------------------------------------------------------ derivations ---


class DerivationTests(unittest.TestCase):
    def test_family_membership_follows_status_and_preserves_order(self):
        families, problems = derive_usable_metric_families(_result())
        self.assertEqual(
            families,
            [
                "source_files",
                "lines_of_code",
                "classes_structs",
                "methods_functions",
                "callable_metrics",
            ],
        )
        self.assertEqual(problems, [])

    def test_failed_and_not_applicable_and_absent_statuses_exclude(self):
        for status in ("failed", "not_applicable", "nonsense", None):
            with self.subTest(status=status):
                result = _result(complexity_status=status)
                families, _ = derive_usable_metric_families(result)
                self.assertNotIn("callable_metrics", families)

    def test_membership_does_not_upgrade_a_partial_observation(self):
        """The family list answers "usable at all", not "complete"."""
        result = _result(metric_status="partial", classes_status="partial")
        families, _ = derive_usable_metric_families(result)
        self.assertIn("classes_structs", families)
        self.assertEqual(
            result["metrics"]["aggregate"]["classes_structs_status"], "partial"
        )

    def test_a_status_claiming_an_absent_value_is_a_contradiction(self):
        result = _result()
        result["metrics"]["aggregate"]["lines_of_code"] = None
        families, problems = derive_usable_metric_families(result)
        self.assertIn("lines_of_code", families)
        self.assertTrue(problems)

    def test_usability_derivation_covers_every_plan_branch(self):
        families = ["source_files"]
        cases = [
            # (metric_status, representativeness, restriction, families) -> usability
            (("failed", "ADEQUATE", "none", families), None),
            (("complete", "ADEQUATE", "unsupported_scope", families), "unsupported_scope"),
            (("partial", "ADEQUATE", "none", families), "partial_but_usable"),
            (("partial", "ADEQUATE", "none", []), None),
            (("complete", "ADEQUATE", "analyzed_scope_only", families), "analyzed_scope_only"),
            (("complete", "ADEQUATE", "none", families), "repository_level_usable"),
            (("complete", "MATERIAL_MIX", "none", families), "analyzed_scope_only"),
            (("complete", "NON_REPRESENTATIVE", "none", families), "analyzed_scope_only"),
            (("complete", "UNRESOLVED", "none", families), "analyzed_scope_only"),
        ]
        for (status, rep, restriction, fams), expected in cases:
            with self.subTest(status=status, rep=rep, restriction=restriction):
                usability, codes = derive_benchmark_usability(
                    metric_status=status,
                    representativeness=rep,
                    manual_restriction=restriction,
                    usable_families=fams,
                )
                self.assertEqual(usability, expected)
                self.assertTrue(codes, "every decision carries a reason code")

    def test_measurement_failure_never_becomes_unsupported_scope(self):
        """Conflating the two would say "wrong subject" when we mean "no data"."""
        usability, codes = derive_benchmark_usability(
            metric_status="failed",
            representativeness="NON_REPRESENTATIVE",
            manual_restriction="none",
            usable_families=[],
        )
        self.assertIsNone(usability)
        self.assertEqual(codes, ["measurement_failed"])

    def test_manual_input_can_only_restrict_never_promote(self):
        """No restriction value raises admission above the deterministic ceiling."""
        for restriction in ("none", "analyzed_scope_only", "unsupported_scope"):
            with self.subTest(restriction=restriction):
                usability, _ = derive_benchmark_usability(
                    metric_status="complete",
                    representativeness="MATERIAL_MIX",
                    manual_restriction=restriction,
                    usable_families=["source_files"],
                )
                self.assertNotEqual(
                    usability,
                    "repository_level_usable",
                    "a manual value promoted a non-representative record",
                )

    def test_unresolved_can_never_be_eligible(self):
        self.assertFalse(
            derive_eligibility(
                mode=MODE_BENCHMARK_QUALIFIED,
                qualification_status="unresolved",
                representativeness="UNRESOLVED",
                usability="analyzed_scope_only",
                metric_status="complete",
                manual_restriction="none",
            )
        )

    def test_eligibility_requires_every_condition(self):
        base = dict(
            mode=MODE_BENCHMARK_QUALIFIED,
            qualification_status="adjudicated",
            representativeness="ADEQUATE",
            usability="repository_level_usable",
            metric_status="complete",
            manual_restriction="none",
        )
        self.assertTrue(derive_eligibility(**base))
        for field, wrong in (
            ("mode", MODE_NOT_REQUESTED),
            ("qualification_status", "unresolved"),
            ("representativeness", "MATERIAL_MIX"),
            ("usability", "analyzed_scope_only"),
            ("metric_status", "partial"),
            ("manual_restriction", "analyzed_scope_only"),
        ):
            with self.subTest(field=field):
                self.assertFalse(derive_eligibility(**{**base, field: wrong}))


# ------------------------------------------------- records and unresolved ---


class RecordConstructionTests(unittest.TestCase):
    def test_a_missing_decision_produces_an_unresolved_record_not_a_gap(self):
        """Cardinality is preserved so the blocker stays visible."""
        results = [_result(), _result(subject="github.com/acme/other")]
        with _written(_registry_document([_registry_record(results[0])])) as path:
            registry = load_registry(path)
            records, _ = build_records(results, registry)
        self.assertEqual(len(records), 2)
        unresolved = [r for r in records if r["qualification_status"] == "unresolved"]
        self.assertEqual(len(unresolved), 1)
        self.assertEqual(unresolved[0]["repository_representativeness"], "UNRESOLVED")
        self.assertIsNone(unresolved[0]["adjudication"])
        self.assertFalse(unresolved[0]["repository_level_comparison_eligible"])

    def test_a_stale_decision_is_not_reused(self):
        """Right subject, wrong revision must not inherit the old verdict."""
        stale = _result(commit="b" * 40)
        current = _result(commit="c" * 40)
        with _written(_registry_document([_registry_record(stale)])) as path:
            registry = load_registry(path)
            records, _ = build_records([current], registry)
        self.assertEqual(records[0]["qualification_status"], "unresolved")

    def test_registry_order_does_not_change_the_output_bytes(self):
        results = [_result(subject=f"github.com/acme/r{i}") for i in range(5)]
        forward = [
            _registry_record(item, record_id=f"rec-{i}")
            for i, item in enumerate(results)
        ]
        outputs = []
        for ordering in (forward, list(reversed(forward))):
            with _written(_registry_document(ordering)) as path:
                registry = load_registry(path)
                records, _ = build_records(results, registry)
                outputs.append(canonical_json(build_artifact(records, registry)["records"]))
        self.assertEqual(outputs[0], outputs[1])

    def test_the_artifact_satisfies_its_schema(self):
        results = [_result()]
        with _written(_registry_document([_registry_record(results[0])])) as path:
            registry = load_registry(path)
            records, _ = build_records(results, registry)
            artifact = build_artifact(records, registry)
        self.assertEqual(
            validate_document("benchmark_qualification", artifact, "artifact"), []
        )

    def test_qualification_does_not_mutate_the_measurement_results(self):
        """The separation, asserted rather than assumed."""
        results = [_result()]
        before = copy.deepcopy(results)
        with _written(_registry_document([_registry_record(results[0])])) as path:
            registry = load_registry(path)
            records, _ = build_records(results, registry)
            build_artifact(records, registry)
        self.assertEqual(results, before)


# ------------------------------------------- reconciliation mutation tests ---


class ReconciliationMutationTests(unittest.TestCase):
    """Each test corrupts one field and asserts reconciliation refuses it.

    These exist because a reconciliation that always passes looks exactly like
    a reconciliation that works.
    """

    def _artifact(self):
        results = [_result()]
        with _written(_registry_document([_registry_record(results[0])])) as path:
            registry = load_registry(path)
            records, _ = build_records(results, registry)
            return build_artifact(records, registry), results

    def test_a_clean_artifact_reconciles(self):
        artifact, results = self._artifact()
        self.assertEqual(reconcile(artifact, results), [])

    def test_a_forged_eligibility_is_rejected(self):
        artifact, results = self._artifact()
        artifact["records"][0]["repository_level_comparison_eligible"] = False
        self.assertTrue(reconcile(artifact, results))

    def test_a_forged_usability_is_rejected(self):
        artifact, results = self._artifact()
        artifact["records"][0]["benchmark_usability"] = "repository_level_usable"
        artifact["records"][0]["repository_representativeness"] = "MATERIAL_MIX"
        self.assertTrue(reconcile(artifact, results))

    def test_a_forged_family_list_is_rejected(self):
        artifact, results = self._artifact()
        artifact["records"][0]["usable_metric_families"] = ["source_files"]
        self.assertTrue(reconcile(artifact, results))

    def test_a_synthesized_adjudicator_on_an_unresolved_record_is_rejected(self):
        artifact, results = self._artifact()
        record = artifact["records"][0]
        record["qualification_status"] = "unresolved"
        record["repository_representativeness"] = "UNRESOLVED"
        record["repository_level_comparison_eligible"] = False
        record["benchmark_usability"] = "analyzed_scope_only"
        problems = reconcile(artifact, results)
        self.assertTrue(
            any("adjudicat" in problem for problem in problems),
            f"a fabricated adjudicator survived reconciliation: {problems}",
        )

    def test_a_missing_record_is_rejected(self):
        artifact, results = self._artifact()
        artifact["records"] = []
        self.assertTrue(reconcile(artifact, results))

    def test_an_extra_record_binding_nothing_is_rejected(self):
        artifact, results = self._artifact()
        extra = copy.deepcopy(artifact["records"][0])
        extra["binding"]["subject_key"] = "github.com/acme/ghost"
        artifact["records"].append(extra)
        self.assertTrue(reconcile(artifact, results))

    def test_a_rebound_record_is_rejected(self):
        artifact, results = self._artifact()
        artifact["records"][0]["binding"]["analyzed_commit_sha"] = "f" * 40
        self.assertTrue(reconcile(artifact, results))


# ---------------------------------------------------------------readiness ---


class ReadinessTests(unittest.TestCase):
    def _manifest(self, **overrides):
        manifest = {
            "profiler_git_commit_sha": "c" * 40,
            "profiler_git_dirty": False,
            "exclusion_policy_sha256": "d" * 64,
            "program_version": "3.5.1",
            "package_distribution_version": "3.5.1",
            "artifact_schema_version": "1.11.0",
            "metric_contract_version": "3.0.0",
            "complexity_contract_version": "2.0.0",
            "inventory_schema_version": "1.7.0",
            "exclusion_policy_version": "1.5.0",
            "input_file_hash": "e" * 64,
        }
        manifest.update(overrides)
        return manifest

    def _ready(self, **overrides):
        artifact = {
            "qualification_profile": QUALIFICATION_PROFILE,
            "records": [
                {
                    "qualification_status": "adjudicated",
                    "repository_representativeness": "ADEQUATE",
                }
            ],
        }
        kwargs = dict(
            manifest=self._manifest(),
            mode=MODE_BENCHMARK_QUALIFIED,
            artifact=artifact,
            binding={"sha256": "f" * 64, "record_count": 1},
            registry_provenance={"registry_sha256": "a" * 64},
            executed_count=1,
            integrity_status="completed",
            self_validation_passed=True,
        )
        kwargs.update(overrides)
        return evaluate_readiness(**kwargs)

    def test_a_fully_provisioned_run_is_ready(self):
        self.assertEqual(self._ready()["status"], "READY")

    def test_a_partial_measurement_does_not_by_itself_block_readiness(self):
        """Dimension D is provenance, not completeness.

        `completed_with_errors` is what a run with partial rows records, and
        treating that as unready would conflate two independent dimensions.
        """
        verdict = self._ready(integrity_status="completed_with_errors")
        self.assertEqual(verdict["status"], "READY")

    def test_each_provenance_defect_blocks_independently(self):
        cases = {
            "profiler sha": {"manifest": self._manifest(profiler_git_commit_sha=None)},
            "dirty tree": {"manifest": self._manifest(profiler_git_dirty=True)},
            "policy hash": {"manifest": self._manifest(exclusion_policy_sha256=None)},
            "version skew": {
                "manifest": self._manifest(package_distribution_version="9.9.9")
            },
            "input hash": {"manifest": self._manifest(input_file_hash=None)},
            "generic mode": {"mode": MODE_NOT_REQUESTED},
            "no registry": {"registry_provenance": None},
            "self validation": {"self_validation_passed": False},
            "bad integrity": {"integrity_status": "failed"},
            "count mismatch": {"executed_count": 7},
            "projection": {"projection_problems": ["projection disagrees"]},
        }
        for label, override in cases.items():
            with self.subTest(case=label):
                self.assertEqual(self._ready(**override)["status"], "NOT_READY")

    def test_an_unresolved_record_blocks_readiness(self):
        verdict = self._ready(
            artifact={
                "qualification_profile": QUALIFICATION_PROFILE,
                "records": [
                    {
                        "qualification_status": "unresolved",
                        "repository_representativeness": "UNRESOLVED",
                    }
                ],
            }
        )
        self.assertEqual(verdict["status"], "NOT_READY")
        self.assertEqual(verdict["unresolved_repository_count"], 1)


# --------------------------------------------------- accepted 96-row corpus ---


class AcceptedCorpusTests(unittest.TestCase):
    """The accepted registry must keep saying what the audit decided."""

    @classmethod
    def setUpClass(cls):
        if not ACCEPTED_REGISTRY.is_file():
            raise unittest.SkipTest(f"accepted registry absent: {ACCEPTED_REGISTRY}")
        cls.registry = load_registry(ACCEPTED_REGISTRY)

    def test_the_accepted_registry_loads_and_holds_96_records(self):
        self.assertEqual(len(self.registry.records), 96)
        self.assertEqual(self.registry.qualification_profile, QUALIFICATION_PROFILE)

    def test_the_accepted_representativeness_distribution_is_exact(self):
        from collections import Counter

        counts = Counter(
            record["repository_representativeness"] for record in self.registry.records
        )
        self.assertEqual(counts["ADEQUATE"], 85)
        self.assertEqual(counts["MATERIAL_MIX"], 8)
        self.assertEqual(counts["NON_REPRESENTATIVE"], 3)
        self.assertEqual(counts.get("UNRESOLVED", 0), 0)

    def test_pitstop_alone_carries_the_unsupported_scope_ceiling(self):
        """NON_REPRESENTATIVE is not an alias for unsupported_scope."""
        restricted = {
            record["binding"]["subject_key"]
            for record in self.registry.records
            if record["manual_admission_restriction"] == "unsupported_scope"
        }
        self.assertEqual(restricted, {"github.com/edwinvw/pitstop"})

    def test_every_record_carries_accepted_forensic_provenance(self):
        for record in self.registry.records:
            with self.subTest(subject=record["binding"]["subject_key"]):
                self.assertEqual(
                    record["adjudication"]["mode"], "accepted_forensic_audit"
                )

    def test_no_accepted_record_supplies_a_derived_field(self):
        for record in self.registry.records:
            for field in (
                "usable_metric_families",
                "benchmark_usability",
                "repository_level_comparison_eligible",
            ):
                self.assertNotIn(field, record)


# ------------------------------------------------------ end-to-end writer ---


class QualifiedRunTests(unittest.TestCase):
    """Real runs through the real writer, in both modes."""

    @staticmethod
    @contextmanager
    def _acquire(repo, spec, config, mode="latest", progress=None):
        del config, mode, progress
        yield AcquiredRepository(
            repo,
            AcquisitionRecord(
                repository_url=spec.url, repository_owner=spec.owner,
                repository_name=spec.repository_name,
                requested_commit_sha=spec.commit_sha, analyzed_commit_sha=SHA,
                resolved_ref="refs/heads/main", default_branch="main",
                acquisition_mode="offline", cache_status="reused",
                remote_checked=False, fetch_timestamp=None,
                checkout_timestamp="2026-08-01T00:00:00Z",
                commit_verification_status="verified", fetch_method="offline_cache",
            ),
        )

    def _build(self, root: Path, registry: Path | None) -> Path:
        repo = root / "source"
        repo.mkdir(parents=True, exist_ok=True)
        (repo / "app.py").write_text(
            "class App:\n    def run(self):\n        return 1\n", encoding="utf-8"
        )
        source_csv = root / "repositories.csv"
        source_csv.write_text(
            "url,architecture_type,expected_language,commit_sha,enabled,notes\n"
            f"https://github.com/acme/python,monolith,Python,{SHA},true,x\n",
            encoding="utf-8",
        )
        config = AnalysisConfig.from_env(
            workspace=root, output_root=root / "output", cache_root=root / "cache",
            temporary_directory=root / "worktrees", workers=1,
        )

        def acquire(spec, config_, mode="latest", progress=None):
            return self._acquire(repo, spec, config_, mode, progress)

        with (
            patch("modules.benchmark_runner.acquire_repository", acquire),
            patch("modules.benchmark_runner._distribution_version", return_value="4.0.0"),
        ):
            summary = run_benchmark(
                [source_csv], config, "offline",
                command_line_arguments=["r0"],
                qualification_registry_path=registry,
            )
        return Path(summary["run_directory"])

    def _root(self, label: str) -> Path:
        directory = tempfile.TemporaryDirectory(prefix=f"archlens_r0_{label}_")
        self.addCleanup(directory.cleanup)
        return Path(directory.name)

    def _registry_for(self, run: Path, path: Path, **overrides) -> Path:
        result = json.loads((run / "analysis.json").read_text(encoding="utf-8"))[0]
        record = _registry_record(
            {
                "subject_key": result["subject_key"],
                "acquisition": result["acquisition"],
                "analysis_scope_hash": result["analysis_scope_hash"],
                "analysis_scope_hash_version": result["analysis_scope_hash_version"],
            },
            **overrides,
        )
        path.write_text(
            json.dumps(_registry_document([record]), indent=2), encoding="utf-8"
        )
        return path

    def test_generic_mode_carries_no_qualification_at_all(self):
        run = self._build(self._root("generic"), None)
        manifest = json.loads((run / "run_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["qualification_mode"], MODE_NOT_REQUESTED)
        self.assertIsNone(manifest["benchmark_qualification_artifact"])
        self.assertFalse((run / "benchmark_qualification.json").exists())
        self.assertFalse((run / "repository_level_metrics.csv").exists())
        self.assertTrue(manifest["self_validation"]["passed"])
        self.assertEqual(
            manifest["benchmark_of_record_readiness"]["status"], "NOT_READY"
        )

    def test_generic_projections_encode_explicit_not_requested_cells(self):
        import csv

        run = self._build(self._root("generic_cells"), None)
        with (run / "sheet_metrics.csv").open(encoding="utf-8", newline="") as handle:
            row = next(iter(csv.DictReader(handle)))
        self.assertEqual(row["qualification_mode"], MODE_NOT_REQUESTED)
        self.assertEqual(row["repository_level_comparison_eligible"], "False")
        for column in (
            "qualification_status",
            "repository_representativeness",
            "benchmark_usability",
            "usable_metric_families",
        ):
            self.assertEqual(row[column], "", f"{column} should be an explicit null")

    def test_qualified_mode_binds_the_artifact_by_hash_before_commit(self):
        import hashlib

        root = self._root("qualified")
        seed = self._build(root / "seed", None)
        registry = self._registry_for(seed, root / "registry.json")
        run = self._build(root / "run", registry)

        manifest = json.loads((run / "run_manifest.json").read_text(encoding="utf-8"))
        artifact_path = run / "benchmark_qualification.json"
        self.assertTrue(artifact_path.is_file())
        binding = manifest["benchmark_qualification_artifact"]
        raw = artifact_path.read_bytes()
        self.assertEqual(binding["sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(binding["size_bytes"], len(raw))
        self.assertEqual(binding["record_count"], 1)
        self.assertEqual(manifest["qualification_mode"], MODE_BENCHMARK_QUALIFIED)
        self.assertTrue(manifest["self_validation"]["passed"])
        self.assertFalse((run / "benchmark_qualification.json.candidate").exists())

    def test_the_unrestricted_projection_holds_exactly_the_eligible_rows(self):
        import csv

        root = self._root("projection")
        seed = self._build(root / "seed", None)
        registry = self._registry_for(seed, root / "registry.json")
        run = self._build(root / "run", registry)

        artifact = json.loads(
            (run / "benchmark_qualification.json").read_text(encoding="utf-8")
        )
        eligible = {
            record["binding"]["subject_key"]
            for record in artifact["records"]
            if record["repository_level_comparison_eligible"]
        }
        with (run / "repository_level_metrics.csv").open(
            encoding="utf-8", newline=""
        ) as handle:
            projected = {row["subject_key"] for row in csv.DictReader(handle)}
        self.assertEqual(projected, eligible)

    def test_a_restricted_record_never_reaches_the_unrestricted_projection(self):
        import csv

        root = self._root("restricted")
        seed = self._build(root / "seed", None)
        registry = self._registry_for(
            seed,
            root / "registry.json",
            repository_representativeness="NON_REPRESENTATIVE",
            representativeness_reason_codes=["unsupported_first_party_dominant"],
            manual_admission_restriction="unsupported_scope",
            manual_admission_reason_codes=["supported_scope_incidental_to_application"],
        )
        run = self._build(root / "run", registry)

        artifact = json.loads(
            (run / "benchmark_qualification.json").read_text(encoding="utf-8")
        )
        record = artifact["records"][0]
        self.assertEqual(record["benchmark_usability"], "unsupported_scope")
        self.assertFalse(record["repository_level_comparison_eligible"])
        with (run / "repository_level_metrics.csv").open(
            encoding="utf-8", newline=""
        ) as handle:
            self.assertEqual(list(csv.DictReader(handle)), [])

        # The measurement itself is untouched by the restriction.
        result = json.loads((run / "analysis.json").read_text(encoding="utf-8"))[0]
        self.assertEqual(result["metrics"]["aggregate"]["metric_status"], "complete")
        self.assertGreater(result["metrics"]["aggregate"]["lines_of_code"], 0)

    def test_qualification_leaves_the_measurement_digest_unchanged(self):
        """The load-bearing claim: qualification is observational.

        Two runs over identical sources, one generic and one qualified, must
        produce the same measurement semantic hash. If adding a qualification
        authority moved a measurement, this is where it shows.
        """
        from validation.scripts.semantic_projection import measurement_semantic_hash

        root = self._root("digest")
        generic = self._build(root / "generic", None)
        registry = self._registry_for(generic, root / "registry.json")
        qualified = self._build(root / "qualified", registry)
        self.assertEqual(
            measurement_semantic_hash(generic),
            measurement_semantic_hash(qualified),
        )

    def test_the_independent_validator_accepts_both_modes(self):
        from validation.scripts.validate_outputs import validate_run

        root = self._root("validator")
        generic = self._build(root / "generic", None)
        registry = self._registry_for(generic, root / "registry.json")
        qualified = self._build(root / "qualified", registry)
        for label, run in (("generic", generic), ("qualified", qualified)):
            with self.subTest(mode=label):
                report = validate_run(run)
                self.assertEqual(report["failures"], [])

    def test_a_hostile_qualification_artifact_is_refused_by_the_validator(self):
        """A post-hoc edit must not survive independent validation."""
        from validation.scripts.validate_outputs import validate_run

        root = self._root("hostile")
        seed = self._build(root / "seed", None)
        registry = self._registry_for(
            seed,
            root / "registry.json",
            repository_representativeness="MATERIAL_MIX",
            representativeness_reason_codes=["unsupported_first_party_dominant"],
        )
        run = self._build(root / "run", registry)
        self.assertEqual(validate_run(run)["failures"], [])

        artifact_path = run / "benchmark_qualification.json"
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
        artifact["records"][0]["repository_level_comparison_eligible"] = True
        artifact["records"][0]["benchmark_usability"] = "repository_level_usable"
        artifact_path.write_text(json.dumps(artifact, indent=2), encoding="utf-8")

        report = validate_run(run)
        self.assertTrue(
            report["failures"],
            "a forged qualification artifact passed independent validation",
        )

    def test_a_stale_registry_yields_unresolved_and_blocks_readiness(self):
        root = self._root("stale")
        seed = self._build(root / "seed", None)
        registry = self._registry_for(seed, root / "registry.json")
        document = json.loads(registry.read_text(encoding="utf-8"))
        document["records"][0]["binding"]["analyzed_commit_sha"] = "b" * 40
        registry.write_text(json.dumps(document, indent=2), encoding="utf-8")

        run = self._build(root / "run", registry)
        artifact = json.loads(
            (run / "benchmark_qualification.json").read_text(encoding="utf-8")
        )
        record = artifact["records"][0]
        self.assertEqual(record["qualification_status"], "unresolved")
        self.assertIsNone(record["adjudication"])

        manifest = json.loads((run / "run_manifest.json").read_text(encoding="utf-8"))
        readiness = manifest["benchmark_of_record_readiness"]
        self.assertEqual(readiness["status"], "NOT_READY")
        self.assertEqual(readiness["unresolved_repository_count"], 1)


class HistoricalCompatibilityTests(unittest.TestCase):
    """Artifact 1.10 stays readable and stays unqualified."""

    def test_1_10_through_1_12_are_supported_and_1_12_is_native(self):
        from validation.artifact_io.compatibility import (
            NATIVE_ARTIFACT_SCHEMA,
            CompatibilityState,
            classify_artifact_schema,
        )

        self.assertEqual(NATIVE_ARTIFACT_SCHEMA, (1, 12, 0))
        for declared in ("1.12.0", "1.11.0", "1.10.0", "1.9.0"):
            with self.subTest(declared=declared):
                self.assertIs(
                    classify_artifact_schema(declared).state,
                    CompatibilityState.SUPPORTED,
                )

    def test_every_supported_non_native_generation_has_a_schema_mapping(self):
        """A missing entry silently judges old bytes against the newest schema."""
        from validation.artifact_io.compatibility import SUPPORTED_ARTIFACT_SCHEMAS
        from validation.artifact_io.schema_store import HISTORICAL_SCHEMA_NAMES

        for key, entry in SUPPORTED_ARTIFACT_SCHEMAS.items():
            if entry["support"] == "native":
                continue
            with self.subTest(generation=key):
                self.assertIn(key, HISTORICAL_SCHEMA_NAMES)

    def test_a_1_10_run_resolves_the_1_7_row_contracts(self):
        from validation.artifact_io.schema_store import schema_name_for

        for logical in ("catalog_row", "sheet_metrics_row", "language_metrics_row"):
            with self.subTest(logical=logical):
                self.assertEqual(
                    schema_name_for(logical, "1.10.0"), f"{logical}_1_7_historical"
                )
        self.assertEqual(
            schema_name_for("run_manifest", "1.10.0"), "run_manifest_1_10_historical"
        )

    def test_measurement_documents_did_not_move_at_1_11(self):
        """R0 changes admission, not measurement: analysis stays on its 1.10 schema."""
        from validation.artifact_io.schema_store import SCHEMA_REGISTRY

        self.assertEqual(SCHEMA_REGISTRY["analysis"][1], "1.10.0")
        self.assertEqual(SCHEMA_REGISTRY["repository_document"][1], "1.10.0")

    def test_frozen_contracts_are_unchanged(self):
        from modules.config import (
            COMPLEXITY_CONTRACT_VERSION,
            INVENTORY_SCHEMA_VERSION,
            METRIC_CONTRACT_VERSION,
        )

        self.assertEqual(METRIC_CONTRACT_VERSION, "3.0.0")
        self.assertEqual(COMPLEXITY_CONTRACT_VERSION, "2.0.0")
        self.assertEqual(INVENTORY_SCHEMA_VERSION, "1.7.0")


class PackagingTests(unittest.TestCase):
    def test_every_new_schema_resource_resolves_through_package_resources(self):
        """Resolution through importlib.resources, as an installed wheel does."""
        from validation.artifact_io.schema_store import load_schema

        for name in ("benchmark_qualification", "benchmark_qualification_registry"):
            with self.subTest(schema=name):
                self.assertIn("$id", load_schema(name))
        expected_versions = {
            "run_manifest": "1.12",
            "catalog_row": "1.11",
            "sheet_metrics_row": "1.11",
            "language_metrics_row": "1.11",
        }
        for name, expected_version in expected_versions.items():
            with self.subTest(schema=name):
                document = load_schema(name)
                self.assertIn(expected_version, document["$id"])

    def test_the_new_schemas_declare_unique_ids(self):
        """The seven pre-existing $id collisions must not gain an eighth."""
        from validation.artifact_io.schema_store import SCHEMA_REGISTRY, load_schema

        new = {
            "benchmark_qualification",
            "benchmark_qualification_registry",
            "run_manifest",
            "catalog_row",
            "sheet_metrics_row",
            "language_metrics_row",
        }
        identifiers = {}
        for name in SCHEMA_REGISTRY:
            identifiers.setdefault(load_schema(name)["$id"], set()).add(
                SCHEMA_REGISTRY[name][0]
            )
        for name in new:
            filename, _ = SCHEMA_REGISTRY[name]
            identifier = load_schema(name)["$id"]
            with self.subTest(schema=name):
                self.assertEqual(identifier.rsplit("/", 1)[-1], filename)
                self.assertEqual(identifiers[identifier], {filename})


if __name__ == "__main__":
    unittest.main()
