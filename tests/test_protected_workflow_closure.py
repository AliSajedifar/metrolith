"""Adversarial closure evidence for the operational protected workflow."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from modules.duplication.output import canonical_json as duplication_json
from modules.duplication.output import validate_duplication_document
from modules.hotspots import canonical_json as hotspot_json
from modules.hotspots import validate_hotspot_document
from modules.policy import trust as trust_module
from modules.cli.trust_command import ReceiptProductionError, create_receipt
from tests.test_duplication_policy_core_dp1 import clean_document
from tests.test_hotspot_policy_core_h1 import hotspot_document, hotspot_row
from tests.test_policy_v2_check import RunBuilder
from validation.artifact_io.reader import open_run


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPOSITORY_ROOT / ".github" / "workflows" / "metrolith-protected-required.yml"
CONSUMER = REPOSITORY_ROOT / "docs" / "examples" / "metrolith-protected-required-consumer.yml"
LOCAL = REPOSITORY_ROOT / "docs" / "examples" / "metrolith-local-unprotected.yml"
HELPER = REPOSITORY_ROOT / "tools" / "protected_workflow.py"

spec = importlib.util.spec_from_file_location("archlens_protected_workflow_test", HELPER)
assert spec is not None and spec.loader is not None
protected = importlib.util.module_from_spec(spec)
spec.loader.exec_module(protected)


def _trust_environment() -> dict[str, str]:
    return {
        "GITHUB_EVENT_NAME": "pull_request",
        "METROLITH_CANDIDATE_REPOSITORY": "candidate/project",
        "METROLITH_CANDIDATE_REVISION": "a" * 40,
        "METROLITH_EXPECTED_EVALUATOR_REPOSITORY": "protected/evaluator",
        "METROLITH_EXPECTED_EVALUATOR_REVISION": "b" * 40,
        "METROLITH_EXPECTED_EVALUATOR_SOURCE_SHA256": "c" * 64,
        "METROLITH_CUSTODY_REPOSITORY": "candidate/project",
        "METROLITH_CUSTODY_REVISION": "d" * 40,
        "METROLITH_EXPECTED_PROGRAM_VERSION": "3.8.0",
        "METROLITH_EXPECTED_RUNTIME_LOCK_SHA256": "e" * 64,
        "METROLITH_POLICY_SHA256": "f" * 64,
        "METROLITH_RUN_MANIFEST_SHA256": "1" * 64,
        "METROLITH_EVIDENCE_RECEIPT_SHA256": "2" * 64,
    }


class ProtectedWorkflowStaticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = WORKFLOW.read_text(encoding="utf-8")
        cls.consumer = CONSUMER.read_text(encoding="utf-8")
        cls.local = LOCAL.read_text(encoding="utf-8")

    def test_workflow_supports_pull_request_merge_group_and_reuse(self):
        self.assertIn("  pull_request:\n", self.workflow)
        self.assertIn("  merge_group:\n", self.workflow)
        self.assertIn("  workflow_call:\n", self.workflow)
        self.assertNotIn("pull_request_target", self.workflow)

    def test_protected_workflow_uses_no_candidate_local_action_or_workflow(self):
        self.assertNotIn("uses: ./", self.workflow)
        self.assertIn("Checkout candidate source as data only", self.workflow)
        self.assertIn(".metrolith-protected/candidate", self.workflow)
        self.assertNotIn("cwd: .metrolith-protected/candidate", self.workflow)

    def test_external_actions_are_full_sha_pinned(self):
        references = []
        for line in self.workflow.splitlines():
            stripped = line.strip()
            if stripped.startswith("uses: "):
                references.append(stripped.removeprefix("uses: "))
        self.assertTrue(references)
        for reference in references:
            with self.subTest(reference=reference):
                _coordinate, revision = reference.rsplit("@", 1)
                self.assertRegex(revision, r"^[0-9a-f]{40}$")

    def test_gate_invokes_check_once_and_preserves_sarif_exit_propagation(self):
        check_invocations = [
            line for line in self.workflow.splitlines()
            if "metrolith_action.py\" check" in line
        ]
        self.assertEqual(len(check_invocations), 1)
        self.assertIn("continue-on-error: true", self.workflow)
        self.assertIn("steps.check.outputs.upload-eligible == 'true'", self.workflow)
        self.assertIn("if: ${{ always() }}", self.workflow)
        self.assertIn("metrolith_action.py\" propagate", self.workflow)

    def test_consumer_is_external_full_sha_placeholder_and_not_local(self):
        self.assertIn(
            "uses: <OWNER>/<METROLITH_REPOSITORY>/.github/workflows/"
            "metrolith-protected-required.yml@<FULL_40_HEX_COMMIT_SHA>",
            self.consumer,
        )
        self.assertNotIn("uses: ./.github/actions/metrolith-check", self.consumer)
        self.assertIn("merge_group:", self.consumer)
        self.assertIn("Fork pull requests", self.consumer)
        self.assertIn("Require workflows to pass before merging", self.consumer)

    def test_local_example_is_explicitly_unprotected_and_never_required(self):
        self.assertIn("LOCAL DEVELOPMENT EXAMPLE — UNPROTECTED", self.local)
        self.assertIn("NEVER A REQUIRED BRANCH GATE", self.local)
        self.assertIn("uses: ./.github/actions/metrolith-check", self.local)
        self.assertIn('protected-required: "false"', self.local)


class ProtectedConfigurationRefusalTests(unittest.TestCase):
    def test_pull_request_target_is_refused_before_configuration(self):
        with patch.dict(os.environ, {"GITHUB_EVENT_NAME": "pull_request_target"}, clear=True):
            with self.assertRaisesRegex(
                protected.ProtectedWorkflowError,
                "pull_request_target_refused",
            ):
                protected.validate_configuration()

    def test_same_repository_evaluator_is_refused_before_checkout_use(self):
        environment = _trust_environment()
        environment["METROLITH_EXPECTED_EVALUATOR_REPOSITORY"] = environment[
            "METROLITH_CANDIDATE_REPOSITORY"
        ]
        with patch.dict(os.environ, environment, clear=True):
            with self.assertRaisesRegex(
                protected.ProtectedWorkflowError,
                "candidate_local_evaluator_refused",
            ):
                protected.validate_configuration()

    def test_branch_tag_partial_sha_and_placeholders_are_refused(self):
        for name, value, reason in (
            ("METROLITH_EXPECTED_EVALUATOR_REVISION", "main", "full_sha_required"),
            ("METROLITH_EXPECTED_EVALUATOR_REVISION", "a" * 12, "full_sha_required"),
            ("METROLITH_EXPECTED_EVALUATOR_REVISION", "v3.8.0", "full_sha_required"),
            (
                "METROLITH_EXPECTED_EVALUATOR_REPOSITORY",
                "<OWNER>/<METROLITH_REPOSITORY>",
                "placeholder_configuration",
            ),
        ):
            with self.subTest(value=value):
                environment = _trust_environment()
                environment[name] = value
                with patch.dict(os.environ, environment, clear=True):
                    with self.assertRaisesRegex(protected.ProtectedWorkflowError, reason):
                        protected.validate_configuration()

    def test_candidate_policy_bytes_are_not_selected_from_candidate_root(self):
        with tempfile.TemporaryDirectory(prefix="archlens_custody_") as temporary:
            root = Path(temporary)
            candidate = root / "candidate"
            custody = root / "custody"
            candidate.mkdir()
            custody.mkdir()
            (candidate / "policy.json").write_text("candidate", encoding="utf-8")
            (custody / "policy.json").write_text("owner", encoding="utf-8")
            selected = protected._inside(custody, "policy.json", "policy")
            self.assertEqual(selected.read_text(encoding="utf-8"), "owner")
            (candidate / "policy.json").write_text("mutated", encoding="utf-8")
            self.assertEqual(selected.read_text(encoding="utf-8"), "owner")


class TrustedReceiptProducerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._temporary = tempfile.TemporaryDirectory(prefix="archlens_receipt_producer_")
        cls.root = Path(cls._temporary.name)
        cls.run_bundle = RunBuilder.build(cls.root / "run")
        cls.view = open_run(cls.run_bundle)
        repository = cls.view.repositories[0]
        cls.subject = str(repository["subject_key"])
        cls.revision = str(repository["acquisition"]["analyzed_commit_sha"])
        cls.scope = str(repository["analysis_scope_hash"])

        hotspot = hotspot_document(
            rows=[hotspot_row("src/a.py", subject_key=cls.subject)],
            run_id=cls.view.run_id,
            subjects=(cls.subject,),
        )
        hotspot["repositories"][0]["analyzed_commit_sha"] = cls.revision
        validate_hotspot_document(hotspot)
        cls.hotspot_path = cls.root / "hotspots.json"
        cls.hotspot_path.write_text(hotspot_json(hotspot), encoding="utf-8", newline="")

        duplication = clean_document()
        duplication["source"]["resolved_revision"] = cls.revision
        duplication["source"]["analysis_scope_hash"] = cls.scope
        validate_duplication_document(duplication)
        cls.duplication_path = cls.root / "duplication.json"
        cls.duplication_path.write_text(
            duplication_json(duplication), encoding="utf-8", newline=""
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls._temporary.cleanup()

    def _create(self, **evidence):
        return create_receipt(
            self.run_bundle,
            evaluator_repository="protected/evaluator",
            evaluator_revision="b" * 40,
            evaluator_source_sha256="c" * 64,
            **evidence,
        )

    def test_supports_neither_hotspots_only_duplication_only_and_both(self):
        cases = (
            ({}, []),
            ({"hotspots": self.hotspot_path}, ["hotspots"]),
            ({"duplication": self.duplication_path}, ["duplication"]),
            (
                {
                    "hotspots": self.hotspot_path,
                    "duplication": self.duplication_path,
                },
                ["duplication", "hotspots"],
            ),
        )
        for supplied, kinds in cases:
            with self.subTest(kinds=kinds):
                produced = self._create(**supplied)
                document = json.loads(produced.payload)
                self.assertEqual([item["kind"] for item in document["evidence"]], kinds)
                self.assertEqual(
                    hashlib.sha256(produced.payload).hexdigest(), produced.sha256
                )
                trust_module.parse_receipt_bytes(
                    produced.payload,
                    verified_sha256=produced.sha256,
                )

    def test_supported_cli_writes_canonical_receipt_and_custody_summary(self):
        output = self.root / "cli-receipt.json"
        completed = subprocess.run(
            [
                sys.executable,
                str(REPOSITORY_ROOT / "pipeline.py"),
                "trust",
                "receipt",
                "create",
                str(self.run_bundle),
                "--evaluator-repository",
                "protected/evaluator",
                "--evaluator-revision",
                "b" * 40,
                "--evaluator-source-sha256",
                "c" * 64,
                "--hotspots",
                str(self.hotspot_path),
                "--output",
                str(output),
            ],
            check=False,
            shell=False,
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        summary = json.loads(completed.stdout)
        self.assertEqual(
            summary["receipt_sha256"], hashlib.sha256(output.read_bytes()).hexdigest()
        )
        self.assertIn("does not self-authenticate", summary["custody_note"])
        trust_module.parse_receipt_bytes(
            output.read_bytes(),
            verified_sha256=summary["receipt_sha256"],
        )

    def test_hashes_exact_evidence_bytes_and_binds_run_subject_revision_scope(self):
        produced = self._create(hotspots=self.hotspot_path)
        document = json.loads(produced.payload)
        entry = document["evidence"][0]
        self.assertEqual(
            entry["document_sha256"],
            hashlib.sha256(self.hotspot_path.read_bytes()).hexdigest(),
        )
        self.assertEqual(entry["run_id"], self.view.run_id)
        self.assertEqual(
            entry["subjects"],
            [
                {
                    "subject_key": self.subject,
                    "analyzed_revision": self.revision,
                    "analysis_scope_hash": self.scope,
                }
            ],
        )

    def test_noncanonical_evidence_and_placeholder_identity_are_refused(self):
        noncanonical = self.root / "noncanonical.json"
        document = json.loads(self.hotspot_path.read_text(encoding="utf-8"))
        noncanonical.write_text(json.dumps(document), encoding="utf-8")
        with self.assertRaisesRegex(ReceiptProductionError, "evidence_not_canonical"):
            self._create(hotspots=noncanonical)
        with self.assertRaisesRegex(
            ReceiptProductionError,
            "placeholder_trust_configuration",
        ):
            create_receipt(
                self.run_bundle,
                evaluator_repository="<OWNER>/<METROLITH_REPOSITORY>",
                evaluator_revision="b" * 40,
                evaluator_source_sha256="c" * 64,
            )

    def test_receipt_and_summary_contain_no_machine_local_path(self):
        produced = self._create(
            hotspots=self.hotspot_path,
            duplication=self.duplication_path,
        )
        combined = produced.payload.decode("utf-8") + json.dumps(produced.summary())
        self.assertNotIn(str(self.root), combined)
        self.assertNotIn(self.root.as_posix(), combined)

    def test_receipt_mutation_and_self_digest_are_not_accepted(self):
        produced = self._create(hotspots=self.hotspot_path)
        mutated = bytearray(produced.payload)
        mutated[-2] = ord(" ")
        with self.assertRaises(trust_module.TrustAdmissionError):
            trust_module.parse_receipt_bytes(
                bytes(mutated),
                verified_sha256=produced.sha256,
            )
        self.assertIn("custody_note", produced.summary())
        self.assertIn("does not self-authenticate", produced.summary()["custody_note"])


if __name__ == "__main__":
    unittest.main()
