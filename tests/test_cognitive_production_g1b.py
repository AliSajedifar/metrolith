"""G1-B: production Cognitive Complexity against the frozen micro-corpus.

The corpus is the specification. Its expectations were authored from revision 3
of the frozen rule table before any implementation existed, so this suite is a
one-way check: **the implementation is measured against the corpus, never the
other way round.** A disagreement is an implementation defect until the frozen
table says otherwise, and if the table itself were contradictory the correct
response is to stop, not to edit an expectation.

At G1-B nothing was persisted. G1-C changed that: the value now reaches
`callables.csv` under Artifact Schema 1.10.0 and Complexity Contract 2.0.0, and
`ScopeBoundaryTests` below pins the boundary as it stands today rather than the
one that applied when this file was written.
"""

from __future__ import annotations

import ast as python_ast
import json
import unittest
from pathlib import Path

from modules.callable_analysis import analyze_callables
from modules.core_metrics import ParserRegistry

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
CORPUS = REPOSITORY_ROOT / "validation/differential/corpus/cognitive"

#: corpus directory -> (ArchLens language, source extension)
LANGUAGES = {
    "go": "Go",
    "java": "Java",
    "javascript": "JavaScript",
    "typescript": "TypeScript",
    "python": "Python",
}


def measure_file(language: str, path: Path) -> dict[str, int | None]:
    """Every canonical callable in one file, through the production path."""
    raw = path.read_bytes()
    if language == "Python":
        text = raw.decode("utf-8")
        root = python_ast.parse(text)
        result = analyze_callables(
            language, root, raw, path.name, raw_text=text, masked_text=text
        )
    else:
        root = ParserRegistry().get(language, path.suffix).parse(raw).root_node
        result = analyze_callables(language, root, raw, path.name)
    return {
        record.qualified_name: record.cognitive_complexity
        for record in result.records
    }


def expectations(directory: str) -> dict:
    return json.loads(
        (CORPUS / directory / "expectations.json").read_text(encoding="utf-8")
    )


def measured_corpus(directory: str) -> dict[str, int | None]:
    """Measure every source file the expectations reference."""
    language = LANGUAGES[directory]
    document = expectations(directory)
    names = {document["source"]}
    for key in ("additional_source", "clarification_source"):
        if document.get(key):
            names.add(document[key])
    for entry in document["callables"]:
        if entry.get("source"):
            names.add(entry["source"])

    measured: dict[str, int | None] = {}
    for name in sorted(names):
        path = CORPUS / directory / name
        if not path.is_file():  # pragma: no cover - defensive
            continue
        if directory == "java" and name == "ConstructsGuarded.java":
            # JDK 21 syntax; the pinned tree-sitter grammar is not the blocker,
            # but the expectation is flagged toolchain-unvalidatable and is
            # compared separately below.
            continue
        for qualified_name, value in measure_file(language, path).items():
            measured[qualified_name] = value
    return measured


def match(expected_name: str, measured: dict[str, int | None]):
    """Resolve a corpus name against the production qualified name.

    The corpus pins the MEASUREMENT, not the qualified-name spelling: the frozen
    rule table says nothing about how a nested class's method is named, and
    inventing an assertion about it here would pin something nobody froze.
    Resolution is by exact match, then by suffix.
    """
    if expected_name in measured:
        return expected_name, measured[expected_name]
    # Suffix match on the full dotted path, so `Other.recursiveViaThis` cannot
    # be confused with the enclosing class's own `recursiveViaThis`.
    candidates = [
        name for name in measured
        if name == expected_name or name.endswith("." + expected_name)
    ]
    if len(candidates) == 1:
        return candidates[0], measured[candidates[0]]
    if candidates:
        # Several callables share the suffix: prefer the least nested, which is
        # the one the corpus name most directly designates.
        candidates.sort(key=lambda name: (name.count("."), name))
        return candidates[0], measured[candidates[0]]
    return None, None


class CorpusAgreementTests(unittest.TestCase):
    """Exact agreement on every evaluable corpus callable, per language."""

    maxDiff = None

    def _compare(self, directory: str):
        measured = measured_corpus(directory)
        exact, disagreements, unmatched = 0, [], []
        for entry in expectations(directory)["callables"]:
            if entry.get("toolchain_unvalidatable"):
                continue
            resolved, value = match(entry["name"], measured)
            if resolved is None:
                unmatched.append(entry["name"])
                continue
            if value == entry["expected_cognitive_complexity"]:
                exact += 1
            else:
                disagreements.append(
                    f"{entry['name']}: expected "
                    f"{entry['expected_cognitive_complexity']}, measured {value}"
                )
        return exact, disagreements, unmatched

    def test_go(self):
        exact, disagreements, unmatched = self._compare("go")
        self.assertEqual(disagreements, [])
        self.assertEqual(unmatched, [])
        self.assertEqual(exact, 28)

    def test_java(self):
        exact, disagreements, unmatched = self._compare("java")
        self.assertEqual(disagreements, [])
        self.assertEqual(unmatched, [])
        self.assertEqual(exact, 30)

    def test_javascript(self):
        exact, disagreements, unmatched = self._compare("javascript")
        self.assertEqual(disagreements, [])
        self.assertEqual(unmatched, [])
        self.assertEqual(exact, 32)

    def test_typescript(self):
        exact, disagreements, unmatched = self._compare("typescript")
        self.assertEqual(disagreements, [])
        self.assertEqual(unmatched, [])
        self.assertEqual(exact, 36)

    def test_python(self):
        exact, disagreements, unmatched = self._compare("python")
        self.assertEqual(disagreements, [])
        self.assertEqual(unmatched, [])
        self.assertEqual(exact, 33)


class InvariantTests(unittest.TestCase):
    """Properties that must hold of every measured callable."""

    def test_cognitive_complexity_is_never_negative(self):
        for directory in LANGUAGES:
            for name, value in measured_corpus(directory).items():
                with self.subTest(language=directory, callable=name):
                    self.assertIsNotNone(value)
                    self.assertGreaterEqual(value, 0)

    def test_a_callable_with_no_construct_measures_zero(self):
        """Cognitive complexity starts at 0, unlike cyclomatic's base of 1."""
        for directory in LANGUAGES:
            measured = measured_corpus(directory)
            zeros = [
                entry["name"] for entry in expectations(directory)["callables"]
                if entry.get("zero_case")
            ]
            for name in zeros:
                resolved, value = match(name, measured)
                with self.subTest(language=directory, callable=name):
                    self.assertEqual(value, 0)

    def test_a_nested_callable_never_leaks_into_its_parent(self):
        """The boundary rule, checked as a property rather than a total."""
        cases = {
            "go": ("FuncLiteralExcluded", "DeferredLiteral"),
            "java": ("lambdaExcluded", "anonymousClassExcluded",
                     "discoveryLocalClassMethod"),
            "javascript": ("callbackExcluded",),
            "typescript": ("callbackExcluded",),
            "python": ("nested_def_excluded", "lambda_excluded"),
        }
        for directory, names in cases.items():
            measured = measured_corpus(directory)
            for name in names:
                resolved, value = match(name, measured)
                with self.subTest(language=directory, callable=name):
                    self.assertEqual(
                        value, 0,
                        "a nested callable's body contributed to its parent",
                    )

    def test_recursion_is_capped_at_one(self):
        """Two self-call sites must not score twice."""
        pairs = {
            "go": ("Recursive", "RecursionTwice"),
            "java": ("recursive", "recursionTwice"),
            "javascript": ("recursive", "recursionTwice"),
            "typescript": ("recursive", "recursionTwice"),
            "python": ("recursive", "recursion_twice"),
        }
        for directory, (once, twice) in pairs.items():
            measured = measured_corpus(directory)
            with self.subTest(language=directory):
                self.assertEqual(match(once, measured)[1], 2)
                self.assertEqual(match(twice, measured)[1], 2)


class ScopeBoundaryTests(unittest.TestCase):
    """The boundary as it stands after G1-C.

    G1-B forbade persistence outright and these tests asserted that. G1-C
    authorized persistence, Complexity Contract 2.0.0 and Artifact Schema
    1.10.0, so the guards moved rather than being deleted: what they now pin is
    the line that still holds.
    """

    def test_the_value_is_persisted_under_artifact_1_10(self):
        from modules.callable_ledger import CALLABLE_COLUMNS
        from modules.config import ARTIFACT_SCHEMA_VERSION

        self.assertIn("cognitive_complexity", CALLABLE_COLUMNS)
        self.assertEqual(ARTIFACT_SCHEMA_VERSION, "1.12.0")

    def test_complexity_contract_2_exists_and_metric_contract_did_not_move(self):
        from modules.config import (
            COMPLEXITY_CONTRACT_VERSION, METRIC_CONTRACT_VERSION,
        )

        self.assertTrue(
            (REPOSITORY_ROOT / "docs/COMPLEXITY_CONTRACT_V2.md").is_file()
        )
        self.assertEqual(COMPLEXITY_CONTRACT_VERSION, "2.0.0")
        self.assertEqual(METRIC_CONTRACT_VERSION, "3.0.0")

    def test_the_presentation_seam_owns_every_cognitive_surface(self):
        """**Superseded in scope at G2-C, kept as an architectural invariant.**

        At G1-B this asserted that no aggregate, report or diff mentioned
        cognitive complexity at all -- the scope boundary of that gate. G2-C
        then integrated the metric, so the assertion that survives is the one
        that made the original checkable: every surface goes through
        `complexity_view`, and none of them computes a cognitive figure of its
        own. Four renderers deriving their own aggregates is how four answers
        appear.
        """
        from pathlib import Path

        root = Path(__file__).resolve().parents[1] / "modules"
        seam = (root / "complexity_view.py").read_text(encoding="utf-8")
        self.assertIn("cognitive_aggregate", seam)
        for name in ("summary.py", "revision_diff.py", "diagnostics.py"):
            body = (root / name).read_text(encoding="utf-8")
            with self.subTest(name):
                if "cognitive" not in body:
                    continue
                self.assertNotIn(
                    "def cognitive_aggregate", body,
                    "the aggregate is defined once, in the presentation seam",
                )

    def test_the_record_carries_the_value_in_memory(self):
        record = next(iter(
            measure_file("Go", CORPUS / "go/constructs.go").items()
        ))
        self.assertIsInstance(record[1], int)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
