import csv
import tempfile
import unittest
from pathlib import Path

from modules.repository_input import (
    InputValidationError,
    load_legacy_txt,
    load_repositories_csv,
    migrate_legacy_inputs,
)


HEADER = "url,architecture_type,expected_language,commit_sha,enabled,notes\n"


class CanonicalRepositoryInputTests(unittest.TestCase):
    def _path(self, content, name="repositories.csv"):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / name
        path.write_text(content, encoding="utf-8")
        return path

    def test_parses_enabled_rows_and_preserves_labels(self):
        path = self._path(
            HEADER
            + "https://github.com/acme/app,monolith,Java,abcdef1,true,reviewed\n"
            + "https://github.com/acme/off,microservices,Go,,false,disabled\n"
        )
        rows = load_repositories_csv(path)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].architecture_type, "monolith")
        self.assertEqual(rows[0].expected_language, "Java")
        self.assertEqual(rows[0].commit_sha, "abcdef1")

    def test_include_disabled_is_explicit(self):
        path = self._path(
            HEADER + "https://github.com/acme/app,unknown,,,false,hold\n"
        )
        self.assertEqual(load_repositories_csv(path), [])
        self.assertFalse(load_repositories_csv(path, include_disabled=True)[0].enabled)

    def test_missing_columns_are_reported_before_analysis(self):
        path = self._path("architecture_type\nmonolith\n")
        with self.assertRaises(InputValidationError) as caught:
            load_repositories_csv(path)
        self.assertIn("missing required column", str(caught.exception))
        self.assertIn("url", str(caught.exception))

    def test_invalid_values_report_all_rows(self):
        path = self._path(
            HEADER
            + "not-a-url,wrong,Rust,xyz,maybe,x\n"
            + "https://gitlab.com/a/b,monolith,Java,,true,x\n"
        )
        with self.assertRaises(InputValidationError) as caught:
            load_repositories_csv(path)
        self.assertIn(":2:", str(caught.exception))
        self.assertIn(":3:", str(caught.exception))

    def test_exact_duplicates_are_safely_deduplicated(self):
        row = "https://github.com/acme/app,monolith,Python,,true,same\n"
        path = self._path(HEADER + row + row)
        self.assertEqual(len(load_repositories_csv(path)), 1)

    def test_conflicting_duplicate_is_rejected(self):
        path = self._path(
            HEADER
            + "https://github.com/acme/app,monolith,Java,,true,a\n"
            + "https://github.com/acme/app,microservices,Java,,true,a\n"
        )
        with self.assertRaises(InputValidationError) as caught:
            load_repositories_csv(path)
        self.assertIn("conflicting duplicate", str(caught.exception))

    def test_malformed_sha_is_rejected(self):
        path = self._path(
            HEADER + "https://github.com/acme/app,monolith,Java,not-a-sha,true,x\n"
        )
        with self.assertRaises(InputValidationError) as caught:
            load_repositories_csv(path)
        self.assertIn("7 to 40 hexadecimal", str(caught.exception))

    def test_legacy_migration_preserves_filename_architecture_and_language(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        old = root / "Go microservices_repos.txt"
        old.write_text("https://github.com/acme/service\n", encoding="utf-8")
        output = root / "repositories.csv"
        migrate_legacy_inputs([old], output)
        row = load_repositories_csv(output)[0]
        self.assertEqual(row.architecture_type, "microservices")
        self.assertEqual(row.expected_language, "Go")

    def test_legacy_cross_label_conflict_is_rejected(self):
        mono = self._path("https://github.com/acme/app\n", "monolith_repos.txt")
        micro = mono.parent / "microservices_repos.txt"
        micro.write_text("https://github.com/acme/app\n", encoding="utf-8")
        with self.assertRaises(InputValidationError):
            load_legacy_txt([mono, micro])

    def test_migration_emits_required_column_order(self):
        old = self._path("https://github.com/acme/app\n", "monolith_repos.txt")
        output = old.parent / "repositories.csv"
        migrate_legacy_inputs([old], output)
        with output.open(encoding="utf-8", newline="") as handle:
            self.assertEqual(
                next(csv.reader(handle)),
                ["url", "architecture_type", "expected_language", "commit_sha", "enabled", "notes"],
            )
