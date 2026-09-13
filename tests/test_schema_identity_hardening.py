"""Release hardening for packaged JSON Schema resource identities."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from validation.artifact_io import schema_store
from validation.artifact_io.errors import ArtifactStructureError, StructuralErrorCode


REPOSITORY = Path(__file__).resolve().parent.parent
SCHEMAS = REPOSITORY / "validation" / "resources" / "schemas"


class SchemaIdentityHardeningTests(unittest.TestCase):
    def tearDown(self) -> None:
        schema_store._registry.cache_clear()

    def test_effective_ids_are_unique_per_packaged_resource(self):
        owners: dict[str, set[str]] = {}
        for name in schema_store.schema_names():
            filename = schema_store.schema_filename(name)
            identifier = schema_store.load_schema(name)["$id"]
            owners.setdefault(identifier, set()).add(filename)
            self.assertEqual(
                identifier,
                f"{schema_store.SCHEMA_BASE_URI}{filename}",
            )
        self.assertEqual(
            {identifier: files for identifier, files in owners.items() if len(files) > 1},
            {},
        )
        self.assertEqual(schema_store.schema_identity_problems(), [])

    def test_stale_frozen_id_is_corrected_only_in_the_loaded_view(self):
        path = SCHEMAS / "analysis-1.10.schema.json"
        raw = json.loads(path.read_text(encoding="utf-8"))
        self.assertTrue(raw["$id"].endswith("analysis-1.9.schema.json"))
        self.assertEqual(
            schema_store.FROZEN_SCHEMA_ID_OVERRIDES[path.name], raw["$id"]
        )
        self.assertTrue(
            schema_store.load_schema("analysis")["$id"].endswith(
                "analysis-1.10.schema.json"
            )
        )
        self.assertEqual(
            json.loads(path.read_text(encoding="utf-8")), raw,
            "identity hardening must not rewrite frozen schema bytes",
        )

    def test_unreviewed_declared_id_collision_is_detected(self):
        original = schema_store._load_schema_file

        def colliding(filename: str) -> dict:
            document = dict(original(filename))
            if filename == "analysis-1.10.schema.json":
                document["$id"] = (
                    f"{schema_store.SCHEMA_BASE_URI}"
                    "repository_document-1.10.schema.json"
                )
            return document

        with patch.object(schema_store, "_load_schema_file", side_effect=colliding):
            problems = schema_store.schema_declaration_problems()
        self.assertEqual(
            [problem.artifact for problem in problems],
            ["analysis-1.10.schema.json"],
        )
        self.assertIn("expected", problems[0].message)

    def test_every_raw_id_is_canonical_or_an_exact_frozen_override(self):
        self.assertEqual(schema_store.schema_declaration_problems(), [])

    def test_registry_construction_fails_closed_on_an_identity_collision(self):
        identifier = f"{schema_store.SCHEMA_BASE_URI}duplicate.schema.json"
        resources = (
            (identifier, "first.schema.json", {"$id": identifier}),
            (identifier, "second.schema.json", {"$id": identifier}),
        )
        schema_store._registry.cache_clear()
        with patch.object(schema_store, "_schema_resources", return_value=resources):
            with self.assertRaises(ArtifactStructureError) as caught:
                schema_store._registry()
        self.assertIs(caught.exception.code, StructuralErrorCode.SCHEMA_INVALID)
        self.assertIn("first.schema.json", str(caught.exception))
        self.assertIn("second.schema.json", str(caught.exception))

    def test_registry_resolves_every_active_schema_to_the_correct_resource(self):
        registry = schema_store._registry()
        for name in schema_store.schema_names():
            if name.endswith("_historical"):
                continue
            with self.subTest(schema=name):
                identifier = schema_store.schema_identity(name)
                resource = registry.get(identifier)
                self.assertIsNotNone(resource)
                self.assertEqual(resource.contents["$id"], identifier)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
