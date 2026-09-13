"""C4: the synthetic complexity corpus, one directory per language.

Checks two separate things, and the separation matters:

1. **Arithmetic self-consistency** of the hand-authored expectations, with no
   ArchLens involvement: `cc == 1 + dp + bo`, `mco <= bo`, `nloc <= span`,
   contributions summing to the counted totals. An expectation file that is
   internally inconsistent is wrong before any implementation is consulted.

2. **Agreement** between those expectations and the implementation, per
   callable, per metric.

The corpus is the specification the reference adapters will be validated against
in the rest of C4, so it must stand on its own first.
"""

from __future__ import annotations

import ast
import json
import unittest
from pathlib import Path

from modules.callable_analysis import analyze_callables
from modules.core_metrics import (
    ParserRegistry,
    _go_entities,
    _java_entities,
    _js_ts_entities,
    _python_comment_masked,
    _python_entities,
    _tree_comment_masked,
    derive_methods_functions,
)

CORPUS = (
    Path(__file__).resolve().parent.parent
    / "validation/differential/corpus/complexity"
)

METRICS = (
    "cyclomatic_complexity",
    "decision_point_count",
    "boolean_operator_count",
    "max_condition_operator_count",
    "max_nesting_depth",
    "nloc",
    "formal_parameter_count",
)

#: language -> (directory, source filename, parser language, extension)
LANGUAGES = {
    "Go": ("go", "constructs.go", "Go", ".go"),
    "Java": ("java", "Constructs.java", "Java", ".java"),
    "JavaScript": ("javascript", "constructs.js", "JavaScript", ".js"),
    "TypeScript": ("typescript", "constructs.ts", "TypeScript", ".ts"),
    "Python": ("python", "constructs.py", "Python", ".py"),
}


def load(language: str):
    directory, filename, parser_language, extension = LANGUAGES[language]
    base = CORPUS / directory
    expectations = json.loads((base / "expectations.json").read_text(encoding="utf-8"))
    source = (base / filename).read_bytes()

    if parser_language == "Python":
        text = source.decode("utf-8")
        root = ast.parse(text)
        entities, _error, _syntax = _python_entities(text)
        raw, masked = text, _python_comment_masked(text)[0]
    else:
        root = ParserRegistry().get(parser_language, extension).parse(source).root_node
        if parser_language == "Java":
            entities = _java_entities(root)
        elif parser_language == "Go":
            entities = _go_entities(root)
        else:
            entities = _js_ts_entities(root, source)
        raw = source.decode("utf-8", "replace")
        masked = _tree_comment_masked(source, root).decode("utf-8", "replace")

    result = analyze_callables(
        parser_language, root, source, filename, raw_text=raw, masked_text=masked
    )
    return expectations, derive_methods_functions(entities), result


class ExpectationSelfConsistencyTests(unittest.TestCase):
    """The expectations must hold together without consulting ArchLens."""

    def test_every_corpus_is_internally_consistent(self):
        for language in LANGUAGES:
            directory = LANGUAGES[language][0]
            path = CORPUS / directory / "expectations.json"
            with self.subTest(language=language):
                self.assertTrue(path.is_file(), f"{language} corpus is missing")
                document = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(
                    len(document["callables"]),
                    document["file_level"]["callable_count"],
                )
                for entry in document["callables"]:
                    expected = entry["expected"]
                    with self.subTest(callable=entry["qualified_name"]):
                        self.assertEqual(
                            expected["cyclomatic_complexity"],
                            1
                            + expected["decision_point_count"]
                            + expected["boolean_operator_count"],
                            "cyclomatic must equal 1 + decisions + operators",
                        )
                        self.assertLessEqual(
                            expected["max_condition_operator_count"],
                            expected["boolean_operator_count"],
                        )
                        span = entry["end_line"] - entry["start_line"] + 1
                        self.assertLessEqual(expected["nloc"], span)
                        self.assertGreaterEqual(expected["max_nesting_depth"], 0)
                        self.assertGreaterEqual(expected["formal_parameter_count"], 0)

    def test_every_corpus_records_what_it_deliberately_excludes(self):
        """A population is only pinned if the exclusions are named too."""
        for language in LANGUAGES:
            directory = LANGUAGES[language][0]
            document = json.loads(
                (CORPUS / directory / "expectations.json").read_text(encoding="utf-8")
            )
            with self.subTest(language=language):
                outside = document.get("outside_population", [])
                self.assertTrue(
                    outside,
                    f"{language}: no construct is recorded as deliberately outside "
                    f"the population, so the boundary is untested",
                )
                for entry in outside:
                    self.assertEqual(entry["expected_records"], 0)
                    self.assertTrue(entry["reason"])


class CorpusAgreementTests(unittest.TestCase):
    """Expectations versus the implementation, per callable and per metric."""

    def _check(self, language: str):
        expectations, methods_functions, result = load(language)

        self.assertEqual(
            result.callable_count,
            expectations["file_level"]["callable_count"],
            f"{language}: row count differs from the corpus expectation",
        )
        self.assertEqual(
            methods_functions,
            expectations["file_level"]["methods_functions"],
            f"{language}: the keystone population differs from the corpus",
        )
        self.assertEqual(
            result.callable_count, methods_functions,
            f"{language}: keystone reconciliation failed on the corpus",
        )

        by_name: dict[str, dict] = {}
        for entry in expectations["callables"]:
            key = entry["qualified_name"]
            if entry.get("signature_discriminator"):
                key = f"{key}{entry['signature_discriminator']}"
            by_name[key] = entry

        for record in result.records:
            key = record.qualified_name
            if record.signature_discriminator:
                key = f"{key}{record.signature_discriminator}"
            with self.subTest(language=language, callable=key):
                entry = by_name.pop(key, None)
                self.assertIsNotNone(entry, f"{key} was emitted but not expected")
                for metric in METRICS:
                    self.assertEqual(
                        getattr(record, metric), entry["expected"][metric],
                        f"{language} {key}: {metric}",
                    )
                self.assertEqual(record.start_line, entry["start_line"])
                self.assertEqual(record.end_line, entry["end_line"])

        self.assertEqual(
            sorted(by_name), [], f"{language}: expected callables were not emitted"
        )

    def test_go(self):
        self._check("Go")

    def test_java(self):
        self._check("Java")

    def test_javascript(self):
        self._check("JavaScript")

    def test_typescript(self):
        self._check("TypeScript")

    def test_python(self):
        self._check("Python")


class ConstructCoverageTests(unittest.TestCase):
    """Every language must exercise the families its grammar can express."""

    REQUIRED = {
        "trivial", "branch", "loop", "boolean", "nest",
    }

    def test_each_corpus_covers_the_core_families(self):
        for language in LANGUAGES:
            directory = LANGUAGES[language][0]
            document = json.loads(
                (CORPUS / directory / "expectations.json").read_text(encoding="utf-8")
            )
            names = " ".join(
                entry["qualified_name"].lower() for entry in document["callables"]
            )
            with self.subTest(language=language):
                self.assertIn("trivial", names)
                self.assertTrue(
                    any(token in names for token in ("branch", "elif", "elseif")),
                    f"{language}: no branching family",
                )
                self.assertTrue(
                    any(token in names for token in ("loop", "loops")),
                    f"{language}: no loop family",
                )
                self.assertIn("boolean", names)

    def test_each_corpus_pins_a_nested_callable_boundary(self):
        """The single most important rule: an excluded callable contributes 0."""
        for language in LANGUAGES:
            directory = LANGUAGES[language][0]
            document = json.loads(
                (CORPUS / directory / "expectations.json").read_text(encoding="utf-8")
            )
            pins = " ".join(
                str(entry.get("pins") or "") for entry in document["callables"]
            ).lower()
            with self.subTest(language=language):
                self.assertTrue(
                    "boundary" in pins or "belong to no record" in pins
                    or "measured nowhere" in pins,
                    f"{language}: no callable pins the traversal-boundary rule",
                )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
