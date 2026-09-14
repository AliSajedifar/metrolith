"""Mechanical guards for the shipped command and release-documentation surface."""

from __future__ import annotations

import argparse
import datetime
import re
import tomllib
import unittest
from pathlib import Path

import pipeline


REPOSITORY = Path(__file__).resolve().parent.parent


class ReleaseDocumentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.readme = (REPOSITORY / "README.md").read_text(encoding="utf-8")
        cls.usage = (REPOSITORY / "docs/USAGE.md").read_text(encoding="utf-8")
        cls.changelog = (REPOSITORY / "CHANGELOG.md").read_text(encoding="utf-8")
        cls.checklist = (
            REPOSITORY / "docs" / "PUBLIC_RELEASE_CHECKLIST.md"
        ).read_text(encoding="utf-8")
        cls.reproducibility = (
            REPOSITORY / "docs" / "REPRODUCIBILITY.md"
        ).read_text(encoding="utf-8")

    def test_usage_command_table_covers_the_actual_root_parser(self):
        parser = pipeline.build_cli()
        subparsers = next(
            action for action in parser._actions
            if isinstance(action, argparse._SubParsersAction)
        )
        for command in sorted(subparsers.choices):
            with self.subTest(command=command):
                self.assertIn(f"| `metrolith {command}` |", self.usage)

    def test_current_commands_are_not_denied_or_omitted(self):
        self.assertNotIn("There is no `metrolith analyze` command", self.readme)
        for command in ("analyze", "diff", "duplication", "changed"):
            with self.subTest(command=command):
                self.assertIn(f"metrolith {command}", self.readme + self.usage)

    def test_changelog_preserves_38_history_and_names_the_40_migration(self):
        # Authenticated first production upload date (UTC); no network or clock dependency.
        date = "2026-09-13"
        released = re.findall(r"^## 4\.0\.0 - (\d{4}-\d{2}-\d{2})$", self.changelog, re.M)
        self.assertEqual(released, [date])
        self.assertEqual(datetime.date.fromisoformat(released[0]).isoformat(), date)
        self.assertNotIn("## 4.0.0 - unreleased", self.changelog)
        citation = (REPOSITORY / "CITATION.cff").read_text(encoding="utf-8")
        project = tomllib.loads((REPOSITORY / "pyproject.toml").read_text(encoding="utf-8"))["project"]
        self.assertEqual(project["version"], "4.0.1")
        self.assertRegex(citation, r'(?m)^version: "4\.0\.1"$')
        self.assertNotIn("date-released:", citation)  # Record the actual upload date after publication.
        self.assertNotIn("unreleased software", citation)
        publishing = (REPOSITORY / "docs/PYPI_PUBLISHING.md").read_text(encoding="utf-8")
        for doc in (self.changelog, publishing, self.checklist):
            self.assertIn(date, doc)
            self.assertIn("34788631105", doc)
            self.assertIn("0fba1c9d0b57fa16524ca8bb9ac4315430442f95", doc)
        self.assertIn("## 3.8.0 - unreleased", self.changelog)
        self.assertIn("Make `metrolith` the canonical console command", self.changelog)
        for command in ("archlens diff", "archlens duplication", "archlens changed"):
            with self.subTest(command=command):
                self.assertIn(command, self.changelog)

    def test_readme_documents_the_completed_policy_evidence_surface(self):
        self.assertNotIn(
            "not added to a run bundle, Policy, SARIF", self.readme + self.usage + self.changelog
        )
        for phrase in (
            "--hotspots hotspots.json --duplication duplication.json",
            "--hotspots",
            "--duplication",
            "matching optional",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.readme + self.usage + self.changelog)

    def test_changelog_summarizes_completed_policy_evidence_integration(self):
        for phrase in (
            "### Hotspot and Duplication Policy integration",
            "through `--hotspots` and `--duplication`",
            "Policy 2.1",
            "Policy 2.2",
            "matching optional `hotspots`",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.changelog)

    def test_release_checklist_has_no_obsolete_34_or_initial_commit_instruction(self):
        self.assertNotIn("3.4.0", self.checklist)
        self.assertNotIn("v3.4.0", self.checklist)
        self.assertNotIn("initial clean Git commit", self.checklist)

    def test_shipped_docs_use_the_single_release_verification_entry_point(self):
        for document in (self.readme, self.checklist, self.reproducibility):
            with self.subTest(document=document[:40]):
                self.assertIn("tools/release_verify.py", document)
        self.assertIn("requirements/release-verification.lock", self.readme)
        self.assertIn("requirements/release-verification.lock", self.reproducibility)

    def test_reproducibility_matrix_covers_every_required_surface_and_boundary(self):
        for phrase in (
            "Canonical run bundle",
            "Measurement-semantic projection",
            "SARIF projection",
            "Duplication JSON",
            "Changed-Code JSON",
            "Hotspots JSON",
            "Semantic equivalence does not imply byte identity",
            "Environment reproducibility does not imply measurement equivalence",
            "absolute workspace/cache/input/output paths",
            "UUID",
            "timestamps",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.reproducibility)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
