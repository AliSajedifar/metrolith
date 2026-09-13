"""ArchLens 4.0 policy contract evolution: policy_document 2.1, check_result 1.1.

The two contracts were published by the schema-evolution phase and ACTIVATED by
Hotspot H1. Duplication DP1 has since superseded both with 2.2.0 / 1.2.0, so the
producers no longer emit these two -- but 2.1.0 and 1.1.0 are still PUBLISHED
contracts, real documents still declare them, and everything this module asserts
about them must stay true forever. Every reference below therefore resolves them
through their HISTORICAL registry names rather than through the plain names,
which by convention track whatever the producer currently emits.

The module polices three things across the 2.0 -> 2.1 / 1.0 -> 1.1 move: that
the predecessors are still untouched, that the successors are additive, and that
both compatibility directions hold -- a 2.0.0 document still loads, and it still
cannot name a 2.1 metric. DP1 equivalents live in
tests/test_duplication_policy_core_dp1.py.

Claims of the form "this never changes again" are pinned LITERALLY, not
reconstructed with `git show HEAD:`. A HEAD-relative comparison is valid for
exactly one commit -- the moment the change lands, HEAD is the after state --
and a transition is not an invariant.
"""

from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import unittest
from pathlib import Path

from modules.admission import ADMISSION_STATES
from modules.policy import evidence as evidence_module
from modules.policy import metrics as metric_module
from modules.policy.check import CHECK_RESULT_FORMAT_VERSION
from modules.policy.document import PolicyDocumentInvalid
from modules.policy.document_v2 import (
    POLICY_DOCUMENT_V2_FORMAT_VERSION,
    load_any_policy,
)
from modules.ratchet.check_service import RATCHET_CHECK_RESULT_FORMAT_VERSION
from validation.artifact_io.schema_store import (
    DOCUMENT_FORMAT_SCHEMAS,
    DOCUMENT_FORMAT_VERSION_FIELD,
    SCHEMA_REGISTRY,
    UnsupportedDocumentFormat,
    check_all_schemas,
    load_schema,
    schema_filename,
    schema_name_for_document,
    schema_name_for_format,
    schema_version,
    validate_document,
)

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = ROOT / "validation" / "resources" / "schemas"
MANIFEST = (
    ROOT / "validation" / "acceptance_env_20260807" / "frozen-schema-hashes.json"
)

#: The approved vocabulary. Pinned here so a twelfth hotspot metric cannot
#: appear in the schema or the allowlist without a test saying so.
APPROVED_HOTSPOT_METRICS = frozenset({
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
})

HOTSPOT_SCOPE = "hotspot_file"

#: The only evidence kind `check_result` 1.1.0 publishes, and the only one it
#: will ever publish. The admission layer already supported two when 1.1 was
#: written; duplication Policy integration was not yet approved, so the contract
#: did not declare it. DP1 added it in `check_result` 1.2.0, which is a new
#: version surface and not an edit to this one.
PUBLISHED_EVIDENCE_KIND = "hotspots"


def committed_bytes(relative: str) -> bytes:
    # Published fixture hashes keep this guard usable without private Git history.
    identities = json.loads((ROOT / "tests/fixtures/resource_hashes.json").read_text(encoding="utf-8"))
    data = (ROOT / relative).read_bytes()
    assert hashlib.sha256(data).hexdigest() == identities[relative], relative
    return data


def policy_document(version: str, metric: str, **rule) -> dict:
    return {
        "policy_document_format_version": version,
        "name": "test policy",
        "metric_rules": [
            {"id": "r1", "metric": metric, "operator": "gt", "threshold": 1, **rule}
        ],
    }


# --------------------------------------------------------------------------
# The predecessors are untouched
# --------------------------------------------------------------------------

class PredecessorsUnchangedTests(unittest.TestCase):
    PREDECESSORS = (
        "policy_document-2.0.schema.json",
        "check_result-1.0.schema.json",
    )

    def test_predecessor_schema_bytes_are_identical_to_the_committed_ones(self):
        for filename in self.PREDECESSORS:
            with self.subTest(schema=filename):
                relative = f"validation/resources/schemas/{filename}"
                self.assertEqual(
                    (SCHEMA_DIR / filename).read_bytes(),
                    committed_bytes(relative),
                    f"{filename} is frozen; the change belongs in a new version",
                )

    def test_no_recorded_schema_identity_is_ever_lost_or_altered(self):
        """The real invariant, stated so it survives every future activation.

        The previous form compared every manifest ROW against HEAD. That holds
        only while no activation is in flight: an activation RENAMES rows, so
        the row-keyed comparison fails for a legitimate change and would have to
        be relaxed once per phase -- which is how a guard gets relaxed into
        uselessness.

        What must never happen is that a published (filename, version, digest)
        triple disappears or that its digest moves. A rename carries the triple
        to a new key and is invisible here, exactly as it should be; editing a
        frozen schema's bytes, or dropping a published contract, is not.
        """
        recorded = json.loads(MANIFEST.read_text(encoding="utf-8"))["schemas"]
        previous = json.loads(
            committed_bytes(
                "validation/acceptance_env_20260807/frozen-schema-hashes.json"
            ).decode("utf-8")
        )["schemas"]
        current = {
            (entry["filename"], entry["format_version"], entry["sha256"])
            for entry in recorded.values()
        }
        for name, entry in previous.items():
            with self.subTest(schema=name):
                triple = (
                    entry["filename"], entry["format_version"], entry["sha256"]
                )
                self.assertIn(
                    triple, current,
                    f"{name} was published as {entry['filename']} "
                    f"{entry['format_version']} and that identity is gone or "
                    f"its digest moved; a frozen schema's bytes never change",
                )

    #: The one successor file activation was allowed to correct, and why.
    #:
    #: `check_result-1.1` was published by the previous phase with NO PRODUCER
    #: -- that was its whole point. Activating it revealed that
    #: `policy.evaluated_as_format_version` still pinned `2.0.0` while the
    #: evaluator now applies 2.1.0, so a 1.1 result could never have validated
    #: against its own contract. Correcting a contract before its first producer
    #: ships is the right time to correct it; the D-6 freeze protects published
    #: contracts that real documents claim, and no document ever claimed this
    #: one. `check_result-1.0` and `policy_document-2.0` are untouched, which is
    #: the requirement that actually binds.
    #: The digest each frozen predecessor has always had, pinned literally.
    #:
    #: This replaced a pair of tests that reconstructed the pre-activation state
    #: with `git show HEAD:` and compared before against after. That works for
    #: exactly one commit: the moment the activation lands, HEAD *is* the after
    #: state, there is no before to find, and the comparison becomes vacuous or
    #: fails. A transition is not an invariant, and only invariants belong in a
    #: suite that runs forever.
    #:
    #: What is invariant is that these two files never change again. A literal
    #: pin says exactly that, with no dependence on where HEAD happens to be,
    #: and it fails loudly if either file is ever edited.
    FROZEN_PREDECESSOR_DIGESTS = {
        "policy_document-2.0.schema.json":
            "84fd6b02feb8eaf330b20ed130192cbf850d44473dc3037500ee88b6bf8b9e82",
        "check_result-1.0.schema.json":
            "09fc8197f424a7db2c24ae00b54fcb678b1138d4939b6f96daa601c47c3d4f7f",
    }

    def test_the_frozen_predecessor_digests_never_move(self):
        for filename in self.PREDECESSORS:
            with self.subTest(schema=filename):
                self.assertEqual(
                    hashlib.sha256(
                        (SCHEMA_DIR / filename).read_bytes()
                    ).hexdigest(),
                    self.FROZEN_PREDECESSOR_DIGESTS[filename],
                    f"{filename} is frozen; a change belongs in a new version",
                )

    def test_each_predecessor_is_registered_under_a_historical_name(self):
        """The activation renamed rather than replaced: both remain reachable."""
        for name, filename, version in (
            ("policy_document_v2_2_0_historical",
             "policy_document-2.0.schema.json", "2.0.0"),
            ("check_result_output_1_0_historical",
             "check_result-1.0.schema.json", "1.0.0"),
        ):
            with self.subTest(schema=name):
                self.assertEqual(SCHEMA_REGISTRY[name], (filename, version))
                recorded = json.loads(
                    MANIFEST.read_text(encoding="utf-8")
                )["schemas"][name]
                self.assertEqual(
                    recorded["sha256"],
                    hashlib.sha256(
                        (SCHEMA_DIR / filename).read_bytes()
                    ).hexdigest(),
                )

    def test_every_manifest_row_still_matches_its_file_and_registry_entry(self):
        recorded = json.loads(MANIFEST.read_text(encoding="utf-8"))["schemas"]
        self.assertEqual(sorted(recorded), sorted(SCHEMA_REGISTRY))
        for name, entry in sorted(recorded.items()):
            with self.subTest(schema=name):
                raw = (SCHEMA_DIR / entry["filename"]).read_bytes()
                self.assertEqual(hashlib.sha256(raw).hexdigest(), entry["sha256"])
                self.assertEqual(
                    (entry["filename"], entry["format_version"]),
                    SCHEMA_REGISTRY[name],
                )

    def test_the_h1_contracts_are_still_published_and_selectable(self):
        """H1 activated 2.1/1.1; DP1 superseded them with 2.2/1.2.

        What this asserts is not who the producer is -- that moves every phase
        -- but that the H1 pair is still a registered, selectable contract, so a
        document declaring 2.1.0 or 1.1.0 is still validated against the schema
        it names rather than against a newer one.
        """
        self.assertEqual(
            schema_version("policy_document_v2_2_1_historical"), "2.1.0"
        )
        self.assertEqual(
            schema_version("check_result_output_1_1_historical"), "1.1.0"
        )
        self.assertEqual(
            schema_name_for_format("policy_document_v2", "2.1.0"),
            "policy_document_v2_2_1_historical",
        )
        self.assertEqual(
            schema_name_for_format("check_result_output", "1.1.0"),
            "check_result_output_1_1_historical",
        )
        # The producers have moved past this pair and must never move back.
        self.assertGreater(POLICY_DOCUMENT_V2_FORMAT_VERSION, "2.1.0")
        self.assertGreater(CHECK_RESULT_FORMAT_VERSION, "1.1.0")

    def test_the_allowlist_gained_exactly_the_approved_vocabulary(self):
        from modules.policy import metrics as metrics

        exposed = {
            item.identifier for item in metrics.METRICS
            if item.family == metrics.FAMILY_HOTSPOTS
        }
        self.assertEqual(exposed, set(APPROVED_HOTSPOT_METRICS))
        self.assertIn(HOTSPOT_SCOPE, metric_module.SCOPES)


# --------------------------------------------------------------------------
# Registration
# --------------------------------------------------------------------------

class RegistrationTests(unittest.TestCase):
    def test_both_new_schemas_are_registered_with_their_versions(self):
        for name, filename, version in (
            (
                "policy_document_v2_2_1_historical",
                "policy_document-2.1.schema.json", "2.1.0",
            ),
            (
                "check_result_output_1_1_historical",
                "check_result-1.1.schema.json", "1.1.0",
            ),
        ):
            with self.subTest(schema=name):
                self.assertIn(name, SCHEMA_REGISTRY)
                self.assertEqual(schema_filename(name), filename)
                self.assertEqual(schema_version(name), version)

    def test_the_plain_names_point_at_the_latest_produced_contract(self):
        """The plain name tracks the configured ratchet producer's 1.3 output."""
        self.assertEqual(
            schema_version("policy_document_v2"),
            POLICY_DOCUMENT_V2_FORMAT_VERSION,
        )
        self.assertEqual(
            schema_version("check_result_output"),
            RATCHET_CHECK_RESULT_FORMAT_VERSION,
        )
        # Every superseded version keeps its own name and its own version.
        for name, version in (
            ("policy_document_v2_2_1_historical", "2.1.0"),
            ("policy_document_v2_2_0_historical", "2.0.0"),
            ("check_result_output_1_1_historical", "1.1.0"),
            ("check_result_output_1_0_historical", "1.0.0"),
        ):
            with self.subTest(schema=name):
                self.assertEqual(schema_version(name), version)

    def test_every_packaged_schema_still_passes_the_metaschema(self):
        self.assertEqual([str(item) for item in check_all_schemas()], [])

    def test_the_new_schemas_carry_their_own_identity(self):
        for name, filename in (
            (
                "policy_document_v2_2_1_historical",
                "policy_document-2.1.schema.json",
            ),
            (
                "check_result_output_1_1_historical",
                "check_result-1.1.schema.json",
            ),
        ):
            with self.subTest(schema=name):
                self.assertEqual(
                    load_schema(name)["$id"],
                    f"https://archlens.dev/schemas/{filename}",
                )

    def test_the_new_schemas_are_exported(self):
        import tempfile

        from validation.artifact_io.schema_store import export_schemas

        with tempfile.TemporaryDirectory() as directory:
            written = export_schemas(Path(directory))
        self.assertIn("policy_document-2.1.schema.json", written)
        self.assertIn("check_result-1.1.schema.json", written)


# --------------------------------------------------------------------------
# policy_document 2.1 is additive over 2.0
# --------------------------------------------------------------------------

class PolicyDocument21Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.old = load_schema("policy_document_v2_2_0_historical")
        # 2.1 by its historical name: the plain name has moved on to 2.2.
        self.new = load_schema("policy_document_v2_2_1_historical")

    def test_the_metric_enum_is_2_0_plus_exactly_the_approved_vocabulary(self):
        old = set(self.old["$defs"]["metricRule"]["properties"]["metric"]["enum"])
        new = set(self.new["$defs"]["metricRule"]["properties"]["metric"]["enum"])
        self.assertEqual(new - old, set(APPROVED_HOTSPOT_METRICS))
        self.assertEqual(old - new, set(), "2.1 removed a 2.0 metric")

    def test_the_metric_enum_stays_sorted(self):
        enum = self.new["$defs"]["metricRule"]["properties"]["metric"]["enum"]
        self.assertEqual(enum, sorted(enum))
        self.assertEqual(len(enum), len(set(enum)))

    def test_the_scope_enum_gained_only_hotspot_file(self):
        old = self.old["$defs"]["metricRule"]["properties"]["scope"]["enum"]
        new = self.new["$defs"]["metricRule"]["properties"]["scope"]["enum"]
        self.assertEqual(new, [*old, HOTSPOT_SCOPE])

    def test_nothing_else_in_the_document_shape_moved(self):
        """Additive means additive: same keys, same requirements, same strictness."""
        self.assertEqual(
            sorted(self.new["properties"]), sorted(self.old["properties"])
        )
        self.assertEqual(sorted(self.new["required"]), sorted(self.old["required"]))
        self.assertEqual(
            self.new["additionalProperties"], self.old["additionalProperties"]
        )
        old_rule = self.old["$defs"]["metricRule"]
        new_rule = self.new["$defs"]["metricRule"]
        self.assertEqual(
            sorted(new_rule["properties"]), sorted(old_rule["properties"])
        )
        self.assertEqual(sorted(new_rule["required"]), sorted(old_rule["required"]))
        self.assertEqual(sorted(self.new["$defs"]), sorted(self.old["$defs"]))

    def test_the_version_constant_is_the_only_declared_version(self):
        self.assertEqual(
            self.new["properties"]["policy_document_format_version"],
            {"const": "2.1.0"},
        )

    def test_a_2_0_document_still_validates_against_2_0(self):
        document = policy_document("2.0.0", "repository.lines_of_code")
        self.assertEqual(
            validate_document("policy_document_v2_2_0_historical", document, "policy.json"), []
        )

    def test_every_2_0_metric_still_validates_under_2_1(self):
        """Backward compatibility: 2.1 accepts everything 2.0 accepted.

        The population is 2.0's OWN enum, not every metric this build knows.
        Those were the same set when 2.1 was the newest contract, and the
        difference did not matter; once DP1 published 2.2 it did, because 2.1
        closes its enum and correctly refuses a 2.2 metric. Iterating the live
        allowlist would have turned this backward-compatibility test into an
        assertion that 2.1 accepts the future.
        """
        for metric in sorted(
            self.old["$defs"]["metricRule"]["properties"]["metric"]["enum"]
        ):
            with self.subTest(metric=metric):
                document = policy_document("2.1.0", metric)
                self.assertEqual(
                    validate_document(
                        "policy_document_v2_2_1_historical", document, "p.json"
                    ),
                    [],
                )

    def test_a_2_0_document_rejects_a_2_1_only_metric(self):
        """The load-bearing refusal: 2.0 closes its enum and must keep refusing."""
        for metric in sorted(APPROVED_HOTSPOT_METRICS):
            with self.subTest(metric=metric):
                document = policy_document("2.0.0", metric)
                violations = validate_document(
                    "policy_document_v2_2_0_historical", document, "policy.json"
                )
                self.assertTrue(violations, f"2.0 accepted {metric}")
                self.assertIn("/metric_rules/0/metric", str(violations[0]))

    def test_a_2_0_document_rejects_the_hotspot_file_scope(self):
        document = policy_document(
            "2.0.0", "repository.lines_of_code", scope=HOTSPOT_SCOPE
        )
        self.assertTrue(
            validate_document("policy_document_v2_2_0_historical", document, "policy.json")
        )

    def test_a_2_1_document_accepts_the_hotspot_vocabulary(self):
        for metric in sorted(APPROVED_HOTSPOT_METRICS):
            with self.subTest(metric=metric):
                document = policy_document("2.1.0", metric)
                self.assertEqual(
                    validate_document(
                        "policy_document_v2_2_1_historical", document, "p.json"
                    ),
                    [],
                )

    def test_a_2_1_document_accepts_hotspot_file_scope_with_path_filters(self):
        document = policy_document(
            "2.1.0", "hotspot_file.churn_commits",
            scope=HOTSPOT_SCOPE, paths=["src/*"], exclude_paths=["src/vendor/*"],
        )
        self.assertEqual(
            validate_document(
                "policy_document_v2_2_1_historical", document, "policy.json"
            ),
            [],
        )

    def test_a_2_1_document_still_refuses_an_unknown_metric(self):
        document = policy_document("2.1.0", "repository.invented_score")
        self.assertTrue(
            validate_document(
                "policy_document_v2_2_1_historical", document, "policy.json"
            )
        )

    def test_the_declared_version_must_match_the_schema(self):
        for name, version in (
            ("policy_document_v2_2_0_historical", "2.1.0"),
            ("policy_document_v2", "2.0.0"),
        ):
            with self.subTest(schema=name, version=version):
                document = policy_document(version, "repository.lines_of_code")
                self.assertTrue(validate_document(name, document, "policy.json"))

    def test_no_score_or_composite_vocabulary_entered_the_schema(self):
        """The Complexity campaign's refusals still hold for the new names."""
        forbidden = (
            "quality_score", "maintainability", "composite", "normalized",
            "rating", "grade", "score", "index", "percentile", "p95", "p90",
            "defect", "bug", "risk",
        )
        for metric in APPROVED_HOTSPOT_METRICS:
            for term in forbidden:
                with self.subTest(metric=metric, term=term):
                    self.assertNotIn(term, metric.casefold())

    def test_the_loader_accepts_both_v2_versions(self):
        for version in ("2.0.0", "2.1.0"):
            with self.subTest(version=version):
                loaded = load_any_policy(
                    policy_document(version, "repository.lines_of_code")
                )
                self.assertEqual(loaded.source_format_version, version)

    def test_a_2_0_document_is_never_restamped_as_2_1(self):
        """Round-tripping must not claim the author wrote a version they did not."""
        loaded = load_any_policy(
            policy_document("2.0.0", "repository.lines_of_code")
        )
        self.assertEqual(
            loaded.as_dict()["policy_document_format_version"], "2.0.0"
        )

    def test_the_loader_enforces_the_same_vocabulary_rule_as_the_schema(self):
        """`check` never schema-validates a policy file, so the loader must agree.

        Otherwise a 2.0.0 document could gate on a vocabulary its own declared
        contract does not contain.
        """
        for metric in sorted(APPROVED_HOTSPOT_METRICS):
            with self.subTest(metric=metric):
                with self.assertRaises(PolicyDocumentInvalid) as caught:
                    load_any_policy(policy_document("2.0.0", metric))
                self.assertIn("2.1.0", str(caught.exception))
                load_any_policy(policy_document("2.1.0", metric))

    def test_an_unknown_v2_version_is_still_refused(self):
        # 2.2.0 was the example here until DP1 made it real. A version number
        # chosen because "nothing will ever use it" is a bad pin; 9.9.9 is
        # outside the numbering scheme rather than merely ahead of it.
        with self.assertRaises(PolicyDocumentInvalid):
            load_any_policy(
                policy_document("9.9.9", "repository.lines_of_code")
            )


# --------------------------------------------------------------------------
# check_result 1.1 is additive over 1.0
# --------------------------------------------------------------------------

class CheckResult11Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.old = load_schema("check_result_output_1_0_historical")
        # 1.1 by its historical name: the plain name has moved on to 1.2.
        self.new = load_schema("check_result_output_1_1_historical")

    @staticmethod
    def _result(version: str) -> dict:
        """A real check-result document, adapted to the requested version.

        The producer has moved past both versions this class tests, so the
        fixture downgrades a real current result. Two fields move together and
        must be set together: `check_result_format_version` and the v2 semantics
        the result was evaluated under. That coupling is the contract -- each
        check-result version pins exactly one policy-document version -- so a
        fixture that changed only one would build a document no contract ever
        described.
        """
        from modules.policy.check import failure_result

        payload = failure_result(
            kind="usage", message="usage error",
            run_directory=Path("run"), policy_name="policy",
        )
        payload["check_result_format_version"] = version
        payload["policy"]["evaluated_as_format_version"] = {
            "1.0.0": "2.0.0", "1.1.0": "2.1.0",
        }[version]
        # Check Result 1.3 adds the ratchet summary. Historical 1.0/1.1
        # documents predate that additive block, just as 1.0 predates evidence.
        payload.pop("ratchet", None)
        payload.pop("evaluated_input_provenance", None)
        payload["run"]["run_directory"] = "run"
        if version == "1.0.0":
            # The `evidence` block arrived in 1.1; a 1.0.0 document is this one
            # without it.
            payload.pop("evidence", None)
        return payload

    @staticmethod
    def _evidence_record() -> dict:
        record = evidence_module.not_supplied(
            evidence_module.EVIDENCE_HOTSPOTS
        ).as_dict()
        record["used_by_rules"] = []
        for key in (
            "document_sha256", "trust_state", "producer_identity", "attestation"
        ):
            record.pop(key, None)
        return record

    def test_only_the_evaluated_as_const_changed_outside_the_additions(self):
        """Everything else in the two documents is identical, key for key."""
        import copy as _copy

        trimmed = _copy.deepcopy(self.new)
        trimmed["properties"]["policy"]["properties"][
            "evaluated_as_format_version"] = {"const": "2.0.0"}
        trimmed["properties"].pop("evidence")
        trimmed["required"] = [
            item for item in trimmed["required"] if item != "evidence"
        ]
        trimmed["$defs"].pop("evidenceRecord")
        trimmed["$defs"].pop("evidenceBinding")
        trimmed["$defs"]["finding"]["properties"]["scope"]["enum"] = (
            self.old["$defs"]["finding"]["properties"]["scope"]["enum"]
        )
        for key in ("$id", "title", "description"):
            trimmed[key] = self.old[key]
        trimmed["$defs"]["finding"]["properties"]["evidence"]["description"] = (
            self.old["$defs"]["finding"]["properties"]["evidence"]["description"]
        )
        trimmed["properties"]["check_result_format_version"] = (
            self.old["properties"]["check_result_format_version"]
        )
        self.assertEqual(trimmed, self.old)

    def test_the_root_gained_only_the_evidence_block(self):
        self.assertEqual(
            set(self.new["properties"]) - set(self.old["properties"]),
            {"evidence"},
        )
        self.assertEqual(
            set(self.old["properties"]) - set(self.new["properties"]), set()
        )
        self.assertEqual(
            set(self.new["required"]) - set(self.old["required"]), {"evidence"}
        )

    def test_the_finding_scope_gained_only_hotspot_file(self):
        old = self.old["$defs"]["finding"]["properties"]["scope"]["enum"]
        new = self.new["$defs"]["finding"]["properties"]["scope"]["enum"]
        self.assertEqual(new, [*old, HOTSPOT_SCOPE])

    def test_the_finding_shape_is_otherwise_unchanged(self):
        old_finding = self.old["$defs"]["finding"]
        new_finding = self.new["$defs"]["finding"]
        self.assertEqual(
            sorted(new_finding["properties"]), sorted(old_finding["properties"])
        )
        self.assertEqual(
            sorted(new_finding["required"]), sorted(old_finding["required"])
        )

    def test_the_evaluated_as_version_follows_the_policy_document(self):
        """1.0 pinned 2.0.0; 1.1 pins 2.1.0, because the evaluator moved.

        The coupling is deliberate and stays loud: if the policy document
        version moves again while this contract does not, this fails rather
        than a result quietly claiming semantics it did not apply.
        """
        policy = self.new["properties"]["policy"]["properties"]
        # Pinned literally. 1.1 evaluates as 2.1.0 for as long as 1.1 exists;
        # sourcing this from the producer constant would make the assertion
        # follow the build and silently stop checking anything.
        self.assertEqual(
            policy["evaluated_as_format_version"], {"const": "2.1.0"},
        )
        self.assertEqual(
            self.old["properties"]["policy"]["properties"][
                "evaluated_as_format_version"],
            {"const": "2.0.0"},
        )

    def test_the_status_vocabulary_did_not_move(self):
        self.assertEqual(
            self.new["$defs"]["status"]["enum"], self.old["$defs"]["status"]["enum"]
        )

    def test_a_1_0_result_validates_against_1_0(self):
        self.assertEqual(
            validate_document(
                "check_result_output_1_0_historical", self._result("1.0.0"),
                "check_result.json",
            ),
            [],
        )

    def test_a_1_0_result_is_refused_by_1_1(self):
        violations = validate_document(
            "check_result_output", self._result("1.0.0"), "check_result.json"
        )
        self.assertTrue(violations)

    def test_a_1_1_result_validates_against_1_1(self):
        payload = self._result("1.1.0")
        payload["evidence"] = {"hotspots": self._evidence_record()}
        self.assertEqual(
            validate_document(
                "check_result_output_1_1_historical", payload,
                "check_result.json",
            ),
            [],
        )

    def test_an_empty_evidence_block_is_valid(self):
        """A failed evaluation considered no evidence, and says so in shape."""
        payload = self._result("1.1.0")
        payload["evidence"] = {}
        self.assertEqual(
            validate_document(
                "check_result_output_1_1_historical", payload,
                "check_result.json",
            ),
            [],
        )

    def test_a_1_1_result_without_the_evidence_block_is_refused(self):
        payload = self._result("1.1.0")
        payload.pop("evidence")
        violations = validate_document(
            "check_result_output_1_1_historical", payload,
            "check_result.json",
        )
        self.assertTrue(violations)
        self.assertIn("evidence", str(violations[0]))

    def test_the_evidence_record_schema_matches_the_admission_layer(self):
        """The contract and the code that will fill it must not drift apart."""
        declared = set(self.new["$defs"]["evidenceRecord"]["properties"])
        produced = set(self._evidence_record())
        self.assertEqual(declared, produced)
        self.assertEqual(
            set(self.new["$defs"]["evidenceRecord"]["required"]), produced
        )

    def test_the_evidence_binding_schema_matches_the_admission_layer(self):
        declared = set(self.new["$defs"]["evidenceBinding"]["properties"])
        produced = set(self._evidence_record()["binding"])
        # `candidate_subject_keys` appears only on an ambiguous binding, so the
        # declared set is the produced set plus that optional key.
        self.assertEqual(declared - produced, {"candidate_subject_keys"})
        self.assertEqual(produced - declared, set())

    def test_the_evidence_vocabularies_come_from_the_admission_layer(self):
        self.assertEqual(
            self.new["$defs"]["evidenceRecord"]["properties"]["admission"]["enum"],
            list(ADMISSION_STATES),
        )
        self.assertEqual(
            self.new["$defs"]["evidenceBinding"]["properties"]["state"]["enum"],
            list(evidence_module.BINDING_STATES),
        )

    def test_the_contract_publishes_only_the_approved_evidence_kind(self):
        """Not `evidence.EVIDENCE_KINDS`, and the difference is the point.

        The admission layer can already admit a duplication document, but
        duplication Policy integration is not approved. A schema enumerating
        `duplication` would declare a capability the product does not have --
        and would put the word into a registered schema file, which
        `tests/test_duplication_d4.py` forbids because duplication is a
        standalone contract with no schema surface. Adding it is a
        `check_result` 1.2 change.
        """
        self.assertEqual(
            self.new["properties"]["evidence"]["propertyNames"]["enum"],
            [PUBLISHED_EVIDENCE_KIND],
        )
        self.assertEqual(
            self.new["$defs"]["evidenceRecord"]["properties"]["kind"]["enum"],
            [PUBLISHED_EVIDENCE_KIND],
        )
        self.assertIn(PUBLISHED_EVIDENCE_KIND, evidence_module.EVIDENCE_KINDS)

    def test_no_new_schema_mentions_duplication(self):
        """The D4 boundary, restated where a schema author will trip over it."""
        for name in ("policy_document-2.1.schema.json", "check_result-1.1.schema.json"):
            with self.subTest(schema=name):
                self.assertNotIn(
                    "duplication",
                    (SCHEMA_DIR / name).read_text(encoding="utf-8").lower(),
                )

    def test_an_unknown_evidence_kind_is_refused(self):
        for kind in ("telemetry", "duplication"):
            with self.subTest(kind=kind):
                payload = self._result("1.1.0")
                payload["evidence"] = {kind: self._evidence_record()}
                self.assertTrue(
                    validate_document(
                        "check_result_output", payload, "check_result.json"
                    )
                )

    def test_an_evidence_record_missing_a_field_is_refused(self):
        record = self._evidence_record()
        del record["admission_meaning"]
        payload = self._result("1.1.0")
        payload["evidence"] = {"hotspots": record}
        self.assertTrue(
            validate_document(
                "check_result_output", payload, "check_result.json"
            )
        )

    def test_an_admitted_record_may_not_smuggle_the_document(self):
        """The record states admissibility; it never carries the figures."""
        record = self._evidence_record()
        record["document"] = {"hotspots": [{"file": "a.py"}]}
        payload = self._result("1.1.0")
        payload["evidence"] = {"hotspots": record}
        self.assertTrue(
            validate_document(
                "check_result_output", payload, "check_result.json"
            )
        )


# --------------------------------------------------------------------------
# Version selection
# --------------------------------------------------------------------------

class VersionSelectionTests(unittest.TestCase):
    def test_each_declared_version_selects_its_own_schema(self):
        for family, table in DOCUMENT_FORMAT_SCHEMAS.items():
            for version, name in table.items():
                with self.subTest(family=family, version=version):
                    self.assertEqual(
                        schema_name_for_format(family, version), name
                    )
                    self.assertEqual(schema_version(name), version)

    def test_selection_reads_the_version_the_document_declares(self):
        for family, field in DOCUMENT_FORMAT_VERSION_FIELD.items():
            for version, name in DOCUMENT_FORMAT_SCHEMAS[family].items():
                with self.subTest(family=family, version=version):
                    self.assertEqual(
                        schema_name_for_document(family, {field: version}), name
                    )

    def test_every_selectable_name_is_registered(self):
        for table in DOCUMENT_FORMAT_SCHEMAS.values():
            for name in table.values():
                with self.subTest(schema=name):
                    self.assertIn(name, SCHEMA_REGISTRY)

    def test_the_two_tables_describe_the_same_families(self):
        self.assertEqual(
            set(DOCUMENT_FORMAT_SCHEMAS), set(DOCUMENT_FORMAT_VERSION_FIELD)
        )

    def test_an_unknown_version_fails_loudly(self):
        # "2.2.0" was in this list until DP1 published it. Replaced with a
        # version outside the numbering scheme, which stays unknown.
        for version in ("9.9.9", "1.0.0", "", "latest", None, 3, ["2.1.0"]):
            with self.subTest(version=version):
                with self.assertRaises(UnsupportedDocumentFormat) as caught:
                    schema_name_for_format("policy_document_v2", version)
                self.assertIn("supported versions", str(caught.exception))

    def test_there_is_no_silent_fallback_to_the_newest_schema(self):
        """A guessed contract is worse than a refused one."""
        with self.assertRaises(UnsupportedDocumentFormat):
            schema_name_for_format("check_result_output", "9.9.9")
        with self.assertRaises(UnsupportedDocumentFormat):
            schema_name_for_document("check_result_output", {})

    def test_an_unknown_family_fails_loudly(self):
        for lookup in (schema_name_for_format, schema_name_for_document):
            with self.subTest(function=lookup.__name__):
                with self.assertRaises(UnsupportedDocumentFormat) as caught:
                    lookup("run_manifest", "1.11.0")
                self.assertIn("known families", str(caught.exception))

    def test_a_non_mapping_document_fails_loudly(self):
        for document in (None, "2.1.0", ["2.1.0"], 3):
            with self.subTest(document=document):
                with self.assertRaises(UnsupportedDocumentFormat):
                    schema_name_for_document("policy_document_v2", document)

    def test_selection_is_deterministic(self):
        first = {
            family: {
                version: schema_name_for_format(family, version)
                for version in table
            }
            for family, table in DOCUMENT_FORMAT_SCHEMAS.items()
        }
        second = copy.deepcopy(first)
        self.assertEqual(first, second)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
