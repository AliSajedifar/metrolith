from __future__ import annotations

import argparse
import ast
import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from modules import changed_code, hotspots
from modules.config import PROGRAM_VERSION
from modules.duplication import output as duplication_output
from modules.policy.document_v2 import POLICY_DOCUMENT_V2_FORMAT_VERSION
from modules.standalone_contracts import (
    CHANGED_CODE_FORMAT,
    CHANGED_CODE_FORMAT_VERSION,
    DUPLICATION_CONTRACT_VERSION,
    DUPLICATION_FORMAT,
    DUPLICATION_FORMAT_VERSION,
    EXACT_VERSION_COMPATIBILITY,
    HOTSPOT_FORMAT,
    HOTSPOT_FORMAT_VERSION,
    STANDALONE_CONTRACTS,
    StandaloneContract,
    StandaloneContractError,
    UnsupportedStandaloneContract,
    build_contract_registry,
    compatibility_status,
    resolve_validator,
    validate_contract_registry,
)
from pipeline import build_cli


ROOT = Path(__file__).resolve().parents[1]


class StandaloneContractRegistryTests(unittest.TestCase):
    def test_registry_has_the_three_active_standalone_surfaces(self):
        self.assertEqual(
            set(STANDALONE_CONTRACTS),
            {CHANGED_CODE_FORMAT, DUPLICATION_FORMAT, HOTSPOT_FORMAT},
        )
        for contract in STANDALONE_CONTRACTS.values():
            self.assertEqual(contract.compatibility_policy, EXACT_VERSION_COMPATIBILITY)

    def test_active_producers_resolve_identity_from_the_registry(self):
        self.assertEqual(
            (changed_code.FORMAT, changed_code.FORMAT_VERSION),
            (CHANGED_CODE_FORMAT, CHANGED_CODE_FORMAT_VERSION),
        )
        self.assertEqual(
            (
                duplication_output.DUPLICATION_FORMAT,
                duplication_output.DUPLICATION_FORMAT_VERSION,
                duplication_output.DUPLICATION_CONTRACT_VERSION,
            ),
            (
                DUPLICATION_FORMAT,
                DUPLICATION_FORMAT_VERSION,
                DUPLICATION_CONTRACT_VERSION,
            ),
        )
        self.assertEqual(
            (hotspots.HOTSPOT_FORMAT, hotspots.HOTSPOT_FORMAT_VERSION),
            (HOTSPOT_FORMAT, HOTSPOT_FORMAT_VERSION),
        )

        forbidden_assignments = {
            "modules/changed_code.py": {"FORMAT", "FORMAT_VERSION"},
            "modules/duplication/output.py": {
                "DUPLICATION_FORMAT",
                "DUPLICATION_FORMAT_VERSION",
                "DUPLICATION_CONTRACT_VERSION",
            },
            "modules/hotspots.py": {"HOTSPOT_FORMAT", "HOTSPOT_FORMAT_VERSION"},
        }
        for relative, forbidden in forbidden_assignments.items():
            tree = ast.parse((ROOT / relative).read_text(encoding="utf-8"))
            assigned = {
                target.id
                for node in ast.walk(tree)
                if isinstance(node, ast.Assign)
                for target in node.targets
                if isinstance(target, ast.Name)
            }
            self.assertFalse(assigned & forbidden, relative)

    def test_validator_discovery_resolves_existing_validators(self):
        self.assertIs(
            resolve_validator(CHANGED_CODE_FORMAT, CHANGED_CODE_FORMAT_VERSION),
            changed_code.validate_document,
        )
        self.assertIs(
            resolve_validator(DUPLICATION_FORMAT, DUPLICATION_FORMAT_VERSION),
            duplication_output.validate_duplication_document,
        )
        self.assertIs(
            resolve_validator(HOTSPOT_FORMAT, HOTSPOT_FORMAT_VERSION),
            hotspots.validate_hotspot_document,
        )

    def test_compatibility_is_exact_and_closed(self):
        self.assertEqual(
            compatibility_status(CHANGED_CODE_FORMAT, CHANGED_CODE_FORMAT_VERSION),
            "compatible",
        )
        self.assertEqual(
            compatibility_status(CHANGED_CODE_FORMAT, "1.0.1"),
            "unsupported_version",
        )
        self.assertEqual(compatibility_status("unknown", "1.0.0"), "unknown_format")
        self.assertEqual(
            compatibility_status(CHANGED_CODE_FORMAT, "not-semver"),
            "invalid_identity",
        )
        with self.assertRaises(UnsupportedStandaloneContract):
            resolve_validator(CHANGED_CODE_FORMAT, "1.0.1")

    def test_registry_construction_fails_on_collision(self):
        contract = StandaloneContract(
            "archlens-example", "1.0.0", "example.module:validate"
        )
        with self.assertRaisesRegex(StandaloneContractError, "duplicate active"):
            build_contract_registry((contract, contract))

    def test_release_registry_check_resolves_every_validator(self):
        rows = validate_contract_registry()
        self.assertEqual([row["format_name"] for row in rows], sorted(STANDALONE_CONTRACTS))
        self.assertTrue(all(row["validator"] for row in rows))

        from tools.release_verify import ReleaseVerifier

        detail = ReleaseVerifier(ROOT).verify_standalone_contracts()
        self.assertEqual(detail["contract_count"], 3)
        self.assertEqual(detail["contracts"], rows)


class FinalCliSurfaceTests(unittest.TestCase):
    EXIT_CODES = {
        "run": (0, 1, 2, 3),
        "analyze": (0, 1, 2, 3),
        "diff": (0, 1, 2, 3, 4),
        "check": (0, 1, 2),
        "hotspots": (0, 1, 2, 3),
        "duplication": (0, 1, 2),
        "changed": (0, 1, 2, 3),
        "doctor": (0, 1, 2),
        "validate": (0, 1, 2),
        "report": (0, 1, 2, 3),
        "schema": (0, 1, 2),
    }

    @staticmethod
    def _root_commands() -> dict[str, argparse.ArgumentParser]:
        parser = build_cli()
        action = next(
            item
            for item in parser._actions
            if isinstance(item, argparse._SubParsersAction)
        )
        return action.choices

    def test_each_audited_command_help_documents_its_exit_contract(self):
        commands = self._root_commands()
        for name, codes in self.EXIT_CODES.items():
            description = commands[name].description or ""
            for code in codes:
                self.assertIn(f"{code} =", description, (name, code, description))

    def test_root_version_uses_the_program_authority(self):
        stdout = io.StringIO()
        with self.assertRaises(SystemExit) as raised, redirect_stdout(stdout):
            build_cli().parse_args(["--version"])
        self.assertEqual(raised.exception.code, 0)
        self.assertEqual(stdout.getvalue(), f"Metrolith {PROGRAM_VERSION}\n")

    def test_policy_metrics_listing_uses_the_policy_v2_authority(self):
        from modules.cli.check_command import render_metric_listing

        self.assertEqual(
            render_metric_listing()["policy_document_format_version"],
            POLICY_DOCUMENT_V2_FORMAT_VERSION,
        )

    def test_readme_links_to_reference_covering_the_audited_commands(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        usage = (ROOT / "docs/USAGE.md").read_text(encoding="utf-8")
        self.assertIn("docs/USAGE.md", readme)
        for name in self.EXIT_CODES:
            self.assertIn(f"| `metrolith {name}` |", usage)

    def test_public_reproducibility_claims_keep_the_three_boundaries(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        reproducibility = (ROOT / "docs" / "REPRODUCIBILITY.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("docs/REPRODUCIBILITY.md", readme)
        self.assertIn("Measurement-semantic", reproducibility)
        self.assertIn("Environment equivalence", " ".join(reproducibility.split()))
        self.assertGreaterEqual(reproducibility.lower().count("byte-identical only"), 3)
        self.assertIn("Semantic equivalence does not imply byte identity", reproducibility)
        self.assertIn(
            "Environment reproducibility does not imply measurement equivalence",
            reproducibility,
        )
        self.assertIn("Standalone output contracts", reproducibility)



if __name__ == "__main__":
    unittest.main()
