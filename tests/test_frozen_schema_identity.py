"""Artifact Schema 1.5.0 is frozen: its schema bytes must never change again.

Decision D-6. Two facts make in-place amendment unsafe rather than merely
untidy:

* the 1.5.0 schemas already ship inside a built ArchLens 3.5.0 wheel, and
* real 1.5.0 run artifacts already exist on disk.

Adding an ``enum`` is a *restrictive* validation-contract change even when the
current producer only emits values inside that enum, because it retroactively
invalidates any artifact carrying another value. Two different schema byte sets
must never both identify themselves as Artifact Schema 1.5.0.

So every new restriction — closed enums, normalized status vocabularies,
``subject_key``, nullable source locators, ``source_mode`` — belongs in 1.6.0,
never in an edit to these files.

This test is the mechanical detector for accidental mutation. If it fails, the
question to ask is *"should this have been a new schema version?"*, and the
answer is almost always yes. Regenerating the manifest to make the test pass is
the one response that defeats its purpose.
"""

from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path

from validation.artifact_io.schema_store import SCHEMA_REGISTRY

REPOSITORY = Path(__file__).resolve().parent.parent
SCHEMA_DIR = REPOSITORY / "validation" / "resources" / "schemas"
MANIFEST = (
    REPOSITORY
    / "validation"
    / "acceptance_env_20260807"
    / "frozen-schema-hashes.json"
)


def _manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


class FrozenSchemaIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        # A missing manifest is a failure, never a skip: silently skipping would
        # remove the only guard against the mutation this test exists to catch.
        self.assertTrue(
            MANIFEST.is_file(), f"frozen schema manifest is missing: {MANIFEST}"
        )
        self.manifest = _manifest()

    def test_every_registered_schema_matches_its_recorded_digest(self):
        recorded = self.manifest["schemas"]
        for name, (filename, version) in sorted(SCHEMA_REGISTRY.items()):
            with self.subTest(schema=name):
                self.assertIn(name, recorded, f"{name} is not in the frozen manifest")
                entry = recorded[name]
                self.assertEqual(entry["filename"], filename)
                self.assertEqual(entry["format_version"], version)
                raw = (SCHEMA_DIR / filename).read_bytes()
                self.assertEqual(
                    hashlib.sha256(raw).hexdigest(),
                    entry["sha256"],
                    f"{filename} changed. Artifact Schema 1.5.0 is frozen (D-6); "
                    f"put the change in a new schema version instead of editing "
                    f"this file.",
                )

    def test_registry_and_manifest_describe_the_same_schema_set(self):
        self.assertEqual(
            sorted(self.manifest["schemas"]),
            sorted(SCHEMA_REGISTRY),
            "the frozen manifest and SCHEMA_REGISTRY have diverged",
        )

    def test_no_unregistered_schema_file_is_present(self):
        registered = {filename for filename, _ in SCHEMA_REGISTRY.values()}
        found = {path.name for path in SCHEMA_DIR.glob("*.json")}
        self.assertEqual(
            sorted(found - registered),
            [],
            "a schema file exists that no registry entry publishes",
        )


if __name__ == "__main__":
    unittest.main()
