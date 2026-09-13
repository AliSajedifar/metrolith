"""C4 Layer-C2 harness corrections.

Three failures happened once and all three were SILENT. These regressions exist
so none of them can recur quietly:

1. an empty scope produced a clean all-zero comparison table instead of an error;
2. passing every path on the command line died with an opaque WinError 206 at
   ~250 files, and would have at 1419;
3. the Go adapter emitted `"callables": null` for a file with no callables, so a
   consumer that did not special-case it crashed instead of reading zero.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from validation.differential.reference.complexity_drivers import (
    GO_ADAPTER,
    REFERENCE_ROOT,
    ScopeRefused,
    resolve_scope,
    write_listing,
)

GO = REFERENCE_ROOT / "go/bin/go.exe"


class EmptyScopeRefusalTests(unittest.TestCase):
    """A zero-file scope is an input error, never a zero-observation result."""

    def test_zero_files_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ScopeRefused) as caught:
                resolve_scope("Go", Path(directory), [])
        self.assertIn("ZERO selected files", str(caught.exception))

    def test_the_refusal_names_the_workspace_mistake(self):
        """Reproduces the original error: an ArchLens WORKSPACE, not a checkout.

        A workspace holds cache/, runs/ and temp/ and contains no measurable
        source, so it selects zero files while looking like a valid path.
        """
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "layer2-go-shop"
            for child in ("cache", "runs", "temp"):
                (workspace / child).mkdir(parents=True)
            (workspace / "latest_run.json").write_text("{}", encoding="utf-8")

            with self.assertRaises(ScopeRefused) as caught:
                resolve_scope("Go", workspace, [])
        message = str(caught.exception)
        self.assertIn("SOURCE CHECKOUT", message)
        self.assertIn("workspace", message)

    def test_a_missing_root_is_refused(self):
        with self.assertRaises(ScopeRefused):
            resolve_scope("Go", Path("D:/definitely/not/here"), ["a.go"])

    def test_a_non_empty_scope_records_its_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            scope = resolve_scope("Go", Path(directory), ["a.go", "b/c.go"])
        evidence = scope.as_dict()
        self.assertEqual(evidence["selected_file_count"], 2)
        self.assertEqual(evidence["extension_summary"], {".go": 2})
        self.assertIn("subject_root", evidence)

    def test_a_scope_of_the_wrong_language_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ScopeRefused):
                resolve_scope("Go", Path(directory), ["a.go", "b.py"])


class ListingFileTests(unittest.TestCase):
    """Path input must not depend on the command-line length limit."""

    def test_listing_survives_a_path_set_that_exceeds_the_command_line_limit(self):
        # Windows caps a command line near 32767 characters. This set is far
        # past it, and is exactly the shape that killed the first Layer C2 run.
        long_root = "D:/synthetic-long-path/source/python-project/" + (
            "deeply/nested/package/" * 6
        )
        paths = [f"{long_root}module_{index:05d}.py" for index in range(1500)]
        joined = len(" ".join(paths))
        self.assertGreater(
            joined, 32767,
            "the fixture must actually exceed the limit, or it proves nothing",
        )

        with tempfile.TemporaryDirectory() as directory:
            listing = write_listing(paths, Path(directory) / "listing.txt")
            recovered = [
                line.strip()
                for line in listing.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        self.assertEqual(recovered, paths, "every path must survive verbatim")

    def test_listing_is_utf8_and_lf_terminated(self):
        with tempfile.TemporaryDirectory() as directory:
            listing = write_listing(["a/é.py", "b/ünïcode.py"], Path(directory) / "l.txt")
            raw = listing.read_bytes()
        self.assertTrue(raw.endswith(b"\n"))
        self.assertNotIn(b"\r\n", raw, "no shell or platform newline translation")
        self.assertIn("é", raw.decode("utf-8"))

    def test_every_adapter_advertises_the_list_flag(self):
        """All four primary adapters must accept the same input convention."""
        adapters = {
            "python": "validation/differential/reference/python/reference_complexity.py",
            "go": "validation/differential/reference/gosrc/reference_complexity.go",
            "node": "validation/differential/reference/node/reference_complexity.js",
        }
        root = Path(__file__).resolve().parent.parent
        for name, relative in adapters.items():
            with self.subTest(adapter=name):
                source = (root / relative).read_text(encoding="utf-8")
                self.assertIn("--list", source)
        # Java already consumed a listing file from its first version.
        java = (root / "validation/differential/reference/java/ReferenceComplexity.java"
                ).read_text(encoding="utf-8")
        self.assertIn("readAllLines", java)


@unittest.skipUnless(GO.is_file(), "reference Go toolchain not provisioned")
class GoEmptyOutputTests(unittest.TestCase):
    """A valid file with no canonical callable emits [], never null."""

    _binary: Path | None = None

    @classmethod
    def binary(cls) -> Path:
        if cls._binary is None:
            target = Path(tempfile.mkdtemp()) / "reference_complexity.exe"
            subprocess.run(
                [str(GO), "build", "-o", str(target), str(GO_ADAPTER)],
                capture_output=True, text=True, check=True,
            )
            cls._binary = target
        return cls._binary

    def _analyze(self, source: str, use_listing: bool = False) -> dict:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.go"
            path.write_text(source, encoding="utf-8", newline="")
            if use_listing:
                listing = write_listing([str(path)], Path(directory) / "l.txt")
                command = [str(self.binary()), "--list", str(listing)]
            else:
                command = [str(self.binary()), str(path)]
            completed = subprocess.run(
                command, capture_output=True, text=True, check=True
            )
        return json.loads(completed.stdout)["files"][0]

    def test_a_file_with_no_canonical_callable_emits_an_empty_list(self):
        entry = self._analyze("package p\n\ntype S struct{ A int }\n")
        self.assertEqual(
            entry["callables"], [],
            "a nil slice serializes as null and forces every consumer to "
            "special-case a legitimately empty result",
        )
        self.assertIsNotNone(entry["callables"])

    def test_a_bodyless_declaration_only_file_emits_an_empty_list(self):
        entry = self._analyze("package p\n\nfunc Stub(a int) int\n")
        self.assertEqual(entry["callables"], [])
        self.assertEqual(entry["excluded_bodyless_declarations"], 1)

    def test_the_listing_flag_produces_the_same_result_as_a_bare_path(self):
        source = "package p\n\nfunc F(a int) int {\n\tif a > 0 {\n\t\treturn a\n\t}\n\treturn 0\n}\n"
        self.assertEqual(
            self._analyze(source, use_listing=False)["callables"],
            self._analyze(source, use_listing=True)["callables"],
        )

    def test_the_go_adapter_version_records_the_contract_correction(self):
        entry = self._analyze("package p\n")
        self.assertEqual(
            entry["adapter_version"], "2.1.0",
            "the serialized contract changed, so the adapter version moves "
            "independently of its sibling adapters and of the Go toolchain",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
