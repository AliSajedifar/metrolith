import json
import unittest
from pathlib import Path

from modules.core_metrics import compute_repository_metrics
from modules.inventory import RepositoryInventory


class FrozenSemanticCorpusV3Tests(unittest.TestCase):
    def test_frozen_language_and_mixed_repository_contract(self):
        root = Path(__file__).parent / "fixtures" / "semantic_v3"
        expected = json.loads((root / "expected.json").read_text(encoding="utf-8"))
        status_fields = expected["statuses"]
        for name in ("java", "javascript", "typescript", "python", "go", "mixed"):
            with self.subTest(repository=name):
                inventory = RepositoryInventory(root / name)
                metrics = compute_repository_metrics(inventory)
                aggregate = metrics["aggregate"]
                fixture = expected[name]
                self.assertEqual(
                    [record.relative_path for record in inventory if record.included_in_metrics],
                    fixture["included_paths"],
                )
                for field in (
                    "lines_of_code", "source_files", "classes_structs", "methods_functions"
                ):
                    self.assertEqual(aggregate[field], fixture[field])
                self.assertEqual(metrics["primary_language_name"], fixture["primary_language"])
                for field, value in status_fields.items():
                    self.assertEqual(aggregate[field], value)
