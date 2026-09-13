"""Sprint 2 adversarial tests for the protected required-gate trust chain."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import shutil
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from modules.duplication.output import (
    canonical_json as duplication_json,
    validate_duplication_document,
)
from modules.hotspots import (
    canonical_json as hotspot_json,
    validate_hotspot_document,
)
from modules.policy import evidence as evidence_module
from modules.policy import trust as trust_module
from modules.policy.check import evaluate_check
from modules.policy.document import PolicyDocumentInvalid
from modules.policy.document_v2 import load_any_policy_bytes
from modules.policy.sarif import render_sarif
from tests.test_duplication_policy_core_dp1 import (
    clean_document as clean_duplication_document,
    document as duplication_document,
)
from tests.test_hotspot_policy_core_h1 import hotspot_document, hotspot_row
from tests.test_policy_v2_check import RunBuilder
from validation.artifact_io.reader import open_run
from validation.artifact_io.schema_store import (
    schema_name_for_format,
    validate_document,
)


REPOSITORY = Path(__file__).resolve().parents[1]
ACTION_RUNNER = REPOSITORY / ".github" / "actions" / "metrolith-check" / "metrolith_action.py"
ACTION_DIRECTORY = ACTION_RUNNER.parent

spec = importlib.util.spec_from_file_location("archlens_sprint2_action", ACTION_RUNNER)
assert spec is not None and spec.loader is not None
action = importlib.util.module_from_spec(spec)
spec.loader.exec_module(action)

EVALUATOR_REPOSITORY = "owner/archlens"
EVALUATOR_REVISION = "1" * 40
EVALUATOR_SOURCE = "2" * 64
TODAY = date(2026, 8, 24)


def policy_bytes(metric: str = "repository.lines_of_code", threshold: int = 10**9) -> bytes:
    return (
        json.dumps(
            {
                "policy_document_format_version": "2.2.0",
                "name": "protected sprint-2 policy",
                "metric_rules": [{
                    "id": "gate.rule",
                    "metric": metric,
                    "operator": "gt",
                    "threshold": threshold,
                }],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def canonical_evidence(kind: str, document: dict) -> bytes:
    rendered = hotspot_json(document) if kind.startswith("hotspots") else duplication_json(document)
    return rendered.encode("utf-8")


def evaluator() -> trust_module.EvaluatorIdentity:
    return trust_module.EvaluatorIdentity.protected(
        repository=EVALUATOR_REPOSITORY,
        revision=EVALUATOR_REVISION,
        source_sha256=EVALUATOR_SOURCE,
        installed_version="3.8.0",
    )


def receipt_and_trust(
    run: Path,
    policy_payload: bytes,
    documents: dict[str, dict],
    *,
    mutate_receipt=None,
) -> tuple[trust_module.CheckTrustContext, bytes]:
    view = open_run(run)
    scopes = evidence_module.analyzed_scopes(view.repositories)
    scope_by_key = {item["subject_key"]: item for item in scopes}
    manifest_sha = hashlib.sha256(
        view.reader.document_bytes("run_manifest.json")
    ).hexdigest()
    entries = []
    for kind in sorted(documents):
        document = documents[kind]
        payload = canonical_evidence(kind, document)
        record = evidence_module.admit_document(
            kind,
            document,
            run_id=view.run_id,
            scopes=scopes,
            document_sha256=hashlib.sha256(payload).hexdigest(),
        )
        if not record.admitted:
            raise AssertionError(f"fixture was not structurally admitted: {record.as_dict()}")
        subjects = []
        for key in sorted(record.subject_keys):
            scope = scope_by_key[key]
            subjects.append({
                "subject_key": key,
                "analyzed_revision": scope["analyzed_commit_sha"],
                "analysis_scope_hash": scope["analysis_scope_hash"],
            })
        entries.append({
            "kind": kind,
            "document_sha256": hashlib.sha256(payload).hexdigest(),
            "producer": evaluator().receipt_dict(),
            "run_manifest_sha256": manifest_sha,
            "run_id": view.run_id,
            "document_format": record.document_format,
            "document_format_version": record.document_format_version,
            "analysis_contract_version": (
                record.document_analysis_contract_version
                or record.document_format_version
            ),
            "subjects": subjects,
        })
    receipt_document = {
        "receipt_format": trust_module.RECEIPT_FORMAT,
        "receipt_format_version": trust_module.RECEIPT_FORMAT_VERSION,
        "evaluator": evaluator().receipt_dict(),
        "run_manifest_sha256": manifest_sha,
        "evidence": entries,
    }
    if mutate_receipt is not None:
        mutate_receipt(receipt_document)
    receipt_payload = trust_module.canonical_receipt_bytes(receipt_document)
    receipt = trust_module.parse_receipt_bytes(
        receipt_payload,
        verified_sha256=hashlib.sha256(receipt_payload).hexdigest(),
    )
    context = trust_module.CheckTrustContext.protected_required(
        evaluator=evaluator(),
        policy_payload=policy_payload,
        expected_policy_sha256=hashlib.sha256(policy_payload).hexdigest(),
        receipt=receipt,
    )
    return context, receipt_payload


class ProtectedCoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._temporary = tempfile.TemporaryDirectory(prefix="archlens_sprint2_")
        cls.root = Path(cls._temporary.name)
        cls.run_bundle = RunBuilder.build(cls.root / "fixture")
        cls.view = open_run(cls.run_bundle)
        cls.subject = cls.view.repositories[0]["subject_key"]
        cls.run_id = cls.view.run_id
        cls.scope = cls.view.repositories[0]["analysis_scope_hash"]
        cls.commit = cls.view.repositories[0]["acquisition"]["analyzed_commit_sha"]

    @classmethod
    def tearDownClass(cls) -> None:
        cls._temporary.cleanup()

    def scratch(self) -> Path:
        path = Path(tempfile.mkdtemp(prefix="case_", dir=self.root))
        self.addCleanup(lambda: shutil.rmtree(path, ignore_errors=True))
        return path

    def hotspot(self, *, forged: bool = False) -> dict:
        rows = (
            [
                hotspot_row(
                    "src/a.py",
                    subject_key=self.subject,
                    complexity_signal="low",
                    churn_signal="low",
                    cognitive=1,
                    commits=1,
                )
            ]
            if forged
            else [hotspot_row("src/a.py", subject_key=self.subject)]
        )
        document = hotspot_document(
            rows=rows,
            run_id=self.run_id,
            subjects=(self.subject,),
            validate=False,
        )
        document["repositories"][0]["analyzed_commit_sha"] = self.commit
        validate_hotspot_document(document)
        return document

    def duplication(self, *, forged: bool = False) -> dict:
        document = (
            clean_duplication_document() if forged else duplication_document()
        )
        document["source"]["resolved_revision"] = self.commit
        document["source"]["analysis_scope_hash"] = self.scope
        validate_duplication_document(document)
        return document

    def write_document(self, directory: Path, kind: str, document: dict) -> Path:
        path = directory / f"{kind}.json"
        path.write_bytes(canonical_evidence(kind, document))
        return path

    def evaluate(
        self,
        payload: bytes,
        *,
        trust: trust_module.CheckTrustContext | None,
        hotspots: Path | None = None,
        duplication: Path | None = None,
        run: Path | None = None,
    ) -> dict:
        policy = load_any_policy_bytes(payload)
        return evaluate_check(
            run or self.run_bundle,
            policy,
            today=TODAY,
            hotspots=hotspots,
            duplication=duplication,
            trust=trust,
        )

    def test_policy_digest_is_external_and_exact_bytes_are_parsed(self):
        payload = policy_bytes()
        with self.assertRaisesRegex(
            trust_module.TrustAdmissionError, "policy_sha256_required"
        ):
            trust_module.CheckTrustContext.protected_required(
                evaluator=evaluator(),
                policy_payload=payload,
                expected_policy_sha256=None,
            )
        with self.assertRaisesRegex(
            trust_module.TrustAdmissionError, "policy_sha256_mismatch"
        ):
            trust_module.CheckTrustContext.protected_required(
                evaluator=evaluator(),
                policy_payload=payload + b" ",
                expected_policy_sha256=hashlib.sha256(payload).hexdigest(),
            )
        context, _ = receipt_and_trust(self.run_bundle, payload, {})
        result = self.evaluate(payload, trust=context)
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(
            result["evaluated_input_provenance"]["policy"]["sha256"],
            hashlib.sha256(payload).hexdigest(),
        )

    def test_local_mode_remains_available_and_is_honestly_labelled(self):
        payload = policy_bytes()
        local = trust_module.CheckTrustContext.local(
            evaluator_version="3.8.0",
            policy_sha256=hashlib.sha256(payload).hexdigest(),
        )
        result = self.evaluate(payload, trust=local)
        provenance = result["evaluated_input_provenance"]
        self.assertEqual(provenance["mode"], "local_unprotected")
        self.assertFalse(provenance["protected_gate"])
        self.assertEqual(
            provenance["evaluator"]["trust"], "locally_trusted_only"
        )
        self.assertEqual(
            {kind: item["state"] for kind, item in provenance["evidence"].items()},
            {"duplication": "absent", "hotspots": "absent"},
        )

    def test_strict_policy_duplicate_keys_and_nonfinite_numbers_stay_refused(self):
        for bad in (
            b'{"policy_document_format_version":"2.2.0","name":"a","name":"b","metric_rules":[]}',
            b'{"policy_document_format_version":"2.2.0","name":"a","metric_rules":[{"id":"r","metric":"repository.lines_of_code","operator":"gt","threshold":NaN}]}',
        ):
            with self.subTest(payload=bad):
                with self.assertRaises(PolicyDocumentInvalid):
                    load_any_policy_bytes(bad)

    def test_hotspot_historical_honest_fail_forged_pass_is_refused_protected(self):
        directory = self.scratch()
        honest = self.hotspot()
        forged = self.hotspot(forged=True)
        honest_path = self.write_document(directory, "hotspots-honest", honest)
        forged_path = self.write_document(directory, "hotspots-forged", forged)
        payload = policy_bytes("repository.hotspot_high_attention_file_count", 0)
        local = trust_module.CheckTrustContext.local(
            evaluator_version="3.8.0",
            policy_sha256=hashlib.sha256(payload).hexdigest(),
        )
        self.assertEqual(self.evaluate(payload, trust=local, hotspots=honest_path)["exit_code"], 1)
        self.assertEqual(self.evaluate(payload, trust=local, hotspots=forged_path)["exit_code"], 0)

        context, _ = receipt_and_trust(self.run_bundle, payload, {"hotspots": honest})
        refused = self.evaluate(payload, trust=context, hotspots=forged_path)
        self.assertEqual(refused["exit_code"], 2)
        self.assertEqual(refused["failure_kind"], "trust_admission_failed")
        self.assertEqual(refused["evidence"]["hotspots"]["trust_state"], "refused")

    def test_duplication_historical_honest_fail_forged_empty_pass_is_refused(self):
        directory = self.scratch()
        honest = self.duplication()
        forged = self.duplication(forged=True)
        honest_path = self.write_document(directory, "duplication-honest", honest)
        forged_path = self.write_document(directory, "duplication-forged", forged)
        payload = policy_bytes("repository.duplication_lexical_group_count", 0)
        local = trust_module.CheckTrustContext.local(
            evaluator_version="3.8.0",
            policy_sha256=hashlib.sha256(payload).hexdigest(),
        )
        self.assertEqual(self.evaluate(payload, trust=local, duplication=honest_path)["exit_code"], 1)
        self.assertEqual(self.evaluate(payload, trust=local, duplication=forged_path)["exit_code"], 0)

        context, _ = receipt_and_trust(self.run_bundle, payload, {"duplication": honest})
        refused = self.evaluate(payload, trust=context, duplication=forged_path)
        self.assertEqual(refused["exit_code"], 2)
        self.assertEqual(refused["evidence"]["duplication"]["trust_state"], "refused")

    def test_protected_evidence_and_clean_pass_expose_all_bounded_provenance(self):
        directory = self.scratch()
        hot = self.hotspot()
        duplicate = self.duplication()
        hot_path = self.write_document(directory, "hotspots", hot)
        duplicate_path = self.write_document(directory, "duplication", duplicate)
        payload = policy_bytes()
        context, receipt_payload = receipt_and_trust(
            self.run_bundle, payload, {"hotspots": hot, "duplication": duplicate}
        )
        result = self.evaluate(
            payload,
            trust=context,
            hotspots=hot_path,
            duplication=duplicate_path,
        )
        self.assertEqual(result["exit_code"], 0)
        provenance = result["evaluated_input_provenance"]
        self.assertTrue(provenance["protected_gate"])
        self.assertEqual(provenance["mode"], "protected_required")
        self.assertEqual(provenance["evaluator"]["revision"], EVALUATOR_REVISION)
        self.assertRegex(provenance["run_manifest"]["sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(provenance["rule_conditions"]["sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(provenance["rule_conditions"]["count"], 1)
        self.assertEqual(
            provenance["trusted_evidence_receipt"]["sha256"],
            hashlib.sha256(receipt_payload).hexdigest(),
        )
        self.assertEqual(
            {kind: item["state"] for kind, item in provenance["evidence"].items()},
            {"duplication": "trusted", "hotspots": "trusted"},
        )
        self.assertEqual(
            validate_document("check_result_output", result, "check_result.json"),
            [],
        )
        self.assertEqual(
            validate_document(
                "trusted_evidence_receipt",
                json.loads(receipt_payload),
                "trusted-evidence-receipt.json",
            ),
            [],
        )
        sarif = json.loads(render_sarif(result))
        projected = sarif["runs"][0]["properties"]["archlens"][
            "evaluatedInputProvenance"
        ]
        self.assertEqual(projected, provenance)
        self.assertNotIn(str(directory), json.dumps(sarif))

    def test_wrong_receipt_bindings_and_producer_are_refused(self):
        directory = self.scratch()
        hot = self.hotspot()
        hot_path = self.write_document(directory, "hotspots", hot)
        payload = policy_bytes()

        mutations = {
            "manifest": lambda doc: doc.update(run_manifest_sha256="f" * 64),
            "run": lambda doc: doc["evidence"][0].update(run_id="other-run"),
            "subject": lambda doc: doc["evidence"][0]["subjects"][0].update(subject_key="other"),
            "revision": lambda doc: doc["evidence"][0]["subjects"][0].update(analyzed_revision="f" * 40),
            "scope": lambda doc: doc["evidence"][0]["subjects"][0].update(analysis_scope_hash="sha256:" + "f" * 64),
            "producer": lambda doc: doc["evidence"][0]["producer"].update(revision="f" * 40),
            "contract": lambda doc: doc["evidence"][0].update(analysis_contract_version="9.9.9"),
        }
        for name, mutation in mutations.items():
            with self.subTest(binding=name):
                context, _ = receipt_and_trust(
                    self.run_bundle, payload, {"hotspots": hot}, mutate_receipt=mutation
                )
                result = self.evaluate(payload, trust=context, hotspots=hot_path)
                self.assertEqual(result["exit_code"], 2)
                self.assertEqual(result["evidence"]["hotspots"]["trust_state"], "refused")

    def test_receipt_is_strict_and_canonical(self):
        payload = policy_bytes()
        context, receipt_payload = receipt_and_trust(
            self.run_bundle, payload, {}
        )
        self.assertIsNotNone(context.receipt)
        receipt = json.loads(receipt_payload)
        receipt["unknown"] = True
        unknown_payload = trust_module.canonical_receipt_bytes(receipt)
        with self.assertRaisesRegex(
            trust_module.TrustAdmissionError, "receipt_contract_invalid"
        ):
            trust_module.parse_receipt_bytes(
                unknown_payload,
                verified_sha256=hashlib.sha256(unknown_payload).hexdigest(),
            )
        noncanonical = json.dumps(
            json.loads(receipt_payload), separators=(",", ":")
        ).encode("utf-8")
        with self.assertRaisesRegex(
            trust_module.TrustAdmissionError, "receipt_not_canonical"
        ):
            trust_module.parse_receipt_bytes(
                noncanonical,
                verified_sha256=hashlib.sha256(noncanonical).hexdigest(),
            )

    def test_moving_same_run_does_not_change_result_or_sarif_bytes(self):
        directory = self.scratch()
        first = directory / "first" / "run"
        second = directory / "second with spaces" / "run"
        shutil.copytree(self.run_bundle, first)
        shutil.copytree(self.run_bundle, second)
        payload = policy_bytes()
        first_trust, _ = receipt_and_trust(first, payload, {})
        second_trust, _ = receipt_and_trust(second, payload, {})
        result_a = self.evaluate(payload, trust=first_trust, run=first)
        result_b = self.evaluate(payload, trust=second_trust, run=second)
        self.assertEqual(result_a, result_b)
        self.assertEqual(render_sarif(result_a), render_sarif(result_b))
        serialized = json.dumps(result_a)
        self.assertNotIn(str(first), serialized)
        self.assertNotIn(str(second), serialized)
        self.assertNotIn("run_directory", result_a["run"])

    def test_old_check_result_schemas_remain_selectable(self):
        self.assertEqual(
            schema_name_for_format("check_result_output", "1.3.0"),
            "check_result_output_1_3_historical",
        )
        self.assertEqual(
            schema_name_for_format("check_result_output", "1.4.0"),
            "check_result_output",
        )


class ProtectedActionTests(unittest.TestCase):
    def expected_environment(self, workspace: Path) -> dict[str, str]:
        source = action._source_identity(REPOSITORY)
        return {
            "GITHUB_WORKSPACE": str(workspace),
            "GITHUB_ACTION_PATH": str(ACTION_DIRECTORY),
            "METROLITH_PROTECTED_REQUIRED": "true",
            "METROLITH_ACTION_REPOSITORY": EVALUATOR_REPOSITORY,
            "METROLITH_ACTION_REF": EVALUATOR_REVISION,
            "METROLITH_EXPECTED_EVALUATOR_REPOSITORY": EVALUATOR_REPOSITORY,
            "METROLITH_EXPECTED_EVALUATOR_REVISION": EVALUATOR_REVISION,
            "METROLITH_EXPECTED_EVALUATOR_SOURCE_SHA256": source,
        }

    def test_candidate_local_action_is_refused_even_with_a_full_sha_claim(self):
        env = self.expected_environment(REPOSITORY)
        with patch.dict(os.environ, env, clear=False):
            with self.assertRaisesRegex(action.IntegrationError, "candidate-local"):
                action._protected_evaluator_identity()

    def test_absent_floating_partial_wrong_and_source_mismatch_are_refused(self):
        with tempfile.TemporaryDirectory(prefix="candidate_workspace_") as temporary:
            workspace = Path(temporary)
            cases = {
                "absent": ("METROLITH_ACTION_REF", ""),
                "branch": ("METROLITH_ACTION_REF", "main"),
                "partial": ("METROLITH_ACTION_REF", EVALUATOR_REVISION[:12]),
                "wrong_full": ("METROLITH_ACTION_REF", "f" * 40),
                "source": ("METROLITH_EXPECTED_EVALUATOR_SOURCE_SHA256", "0" * 64),
            }
            for name, (key, value) in cases.items():
                with self.subTest(case=name):
                    env = self.expected_environment(workspace)
                    env[key] = value
                    with patch.dict(os.environ, env, clear=False):
                        with self.assertRaises(action.IntegrationError):
                            action._protected_evaluator_identity()

    def test_simulated_external_full_sha_action_and_installed_source_are_accepted(self):
        with tempfile.TemporaryDirectory(prefix="candidate_workspace_") as temporary:
            env = self.expected_environment(Path(temporary))
            with patch.dict(os.environ, env, clear=False):
                identity = action._protected_evaluator_identity()
            self.assertEqual(identity["revision"], EVALUATOR_REVISION)
            self.assertEqual(identity["source_sha256"], action._source_identity(REPOSITORY))
            action._verify_installed_evaluator(REPOSITORY, {
                **identity,
                "source_sha256": action._source_identity(REPOSITORY),
            })
            with self.assertRaisesRegex(
                action.IntegrationError, "source changed after admission"
            ):
                action._verify_installed_evaluator(REPOSITORY, {
                    **identity,
                    "source_sha256": "0" * 64,
                })

    def test_action_runtime_is_exact_locked_and_shell_free(self):
        metadata = json.loads((ACTION_DIRECTORY / "action.yml").read_text(encoding="utf-8"))
        self.assertEqual(metadata["runs"]["steps"][0]["with"]["python-version"], "3.13.9")
        source = ACTION_RUNNER.read_text(encoding="utf-8")
        self.assertIn('"--require-hashes"', source)
        self.assertIn('"--no-build-isolation"', source)
        self.assertIn('"--no-deps"', source)
        self.assertNotIn('"3.13"', (ACTION_DIRECTORY / "action.yml").read_text(encoding="utf-8"))
        self.assertNotIn("shell=True", source)
        lock = (REPOSITORY / "requirements" / "action-runtime.lock").read_text(encoding="utf-8")
        self.assertIn("--only-binary=:all:", lock)
        self.assertNotIn(">=", lock)


if __name__ == "__main__":
    unittest.main()
