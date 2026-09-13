"""C4 Layer-C2 campaign harness: build freshness and external CC references.

Two failure classes are pinned here, and both were observed rather than
imagined:

1. **A compiled adapter reused because it exists.** The Layer-C2 smoke run
   reused a Go binary built before the ``--list`` change and failed against the
   *old* contract while looking like a current run. Existence is not freshness.
2. **An external tool turning an unreadable file into a zero.**
   ``lizard.analyze_file`` swallows ``IOError`` and ``UnicodeDecodeError`` and
   returns an empty function list, which is indistinguishable from a file that
   genuinely holds no function.

Skips here are **capability statements**: the reference toolchain is not
provisioned. A skip never means the harness is fine.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from validation.differential.reference import complexity_drivers as drivers
from validation.differential.reference.complexity_drivers import (
    BUILD_STAMP,
    EXTERNAL_CC_TOOL,
    ensure_go_adapter,
    ensure_java_adapter,
    resolve_scope,
    run_external_cc,
)

GO = drivers.GO
JAVAC = drivers.JDK / "javac.exe"
PYREF = drivers.PYREF
NODE = drivers.NODE
ESLINT = drivers.NODE_MODULES / "eslint"

CORPUS = Path(__file__).resolve().parent.parent / (
    "validation/differential/corpus/complexity"
)


class BuildFreshnessRuleTests(unittest.TestCase):
    """The freshness decision itself, with no toolchain required."""

    def test_a_missing_artifact_is_never_fresh(self):
        with tempfile.TemporaryDirectory() as directory:
            reason = drivers._stale_reason(
                Path(directory) / BUILD_STAMP, "digest", "toolchain",
                Path(directory) / "absent.exe",
            )
        self.assertEqual(reason, "no built artifact on disk")

    def test_an_artifact_without_a_stamp_is_never_fresh(self):
        """The exact smoke-run failure: a binary that exists but is stale."""
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "reference.exe"
            artifact.write_bytes(b"a binary built against the old contract")
            reason = drivers._stale_reason(
                Path(directory) / BUILD_STAMP, "digest", "toolchain", artifact,
            )
        self.assertIsNotNone(reason)
        self.assertIn("existence alone is never freshness", reason)

    def test_a_changed_source_forces_a_rebuild(self):
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "reference.exe"
            artifact.write_bytes(b"built")
            stamp = Path(directory) / BUILD_STAMP
            drivers._write_stamp(stamp, "digest-of-v1", "toolchain", artifact)
            reason = drivers._stale_reason(
                stamp, "digest-of-v2", "toolchain", artifact,
            )
        self.assertEqual(reason, "adapter source changed since the recorded build")

    def test_a_changed_toolchain_forces_a_rebuild(self):
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "reference.exe"
            artifact.write_bytes(b"built")
            stamp = Path(directory) / BUILD_STAMP
            drivers._write_stamp(stamp, "digest", "go1.23", artifact)
            reason = drivers._stale_reason(stamp, "digest", "go1.24", artifact)
        self.assertEqual(
            reason, "reference toolchain changed since the recorded build"
        )

    def test_a_replaced_binary_forces_a_rebuild(self):
        """A stamp that no longer describes the bytes on disk is not evidence."""
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "reference.exe"
            artifact.write_bytes(b"built")
            stamp = Path(directory) / BUILD_STAMP
            drivers._write_stamp(stamp, "digest", "toolchain", artifact)
            artifact.write_bytes(b"something else entirely")
            reason = drivers._stale_reason(stamp, "digest", "toolchain", artifact)
        self.assertEqual(
            reason, "built artifact on disk is not the one that was recorded"
        )

    def test_an_unreadable_stamp_forces_a_rebuild(self):
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "reference.exe"
            artifact.write_bytes(b"built")
            stamp = Path(directory) / BUILD_STAMP
            stamp.write_text("{not json", encoding="utf-8")
            reason = drivers._stale_reason(stamp, "digest", "toolchain", artifact)
        self.assertIn("unreadable build stamp", reason)

    def test_a_matching_stamp_permits_reuse(self):
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "reference.exe"
            artifact.write_bytes(b"built")
            stamp = Path(directory) / BUILD_STAMP
            drivers._write_stamp(stamp, "digest", "toolchain", artifact)
            self.assertIsNone(
                drivers._stale_reason(stamp, "digest", "toolchain", artifact)
            )

    def test_the_runner_cannot_be_handed_a_prebuilt_binary(self):
        """The signature is the guard: no caller can supply a stale path."""
        import inspect

        parameters = inspect.signature(drivers.run_adapter).parameters
        self.assertNotIn("go_binary", parameters)
        self.assertNotIn("java_classes", parameters)


@unittest.skipUnless(GO.is_file(), "reference Go toolchain not provisioned")
class GoBuildDurabilityTests(unittest.TestCase):
    def test_the_first_build_rebuilds_and_the_second_reuses(self):
        with tempfile.TemporaryDirectory() as directory:
            first = ensure_go_adapter(Path(directory))
            self.assertTrue(first.rebuilt)
            self.assertTrue(first.artifact.is_file())

            second = ensure_go_adapter(Path(directory))
            self.assertFalse(second.rebuilt)
            self.assertEqual(first.artifact_digest, second.artifact_digest)

    def test_an_edited_adapter_source_forces_a_rebuild(self):
        """The smoke-run failure, reproduced: source moved, binary did not."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            built = ensure_go_adapter(root)
            stamp_path = built.artifact.parent / BUILD_STAMP
            stamp = json.loads(stamp_path.read_text(encoding="utf-8"))
            stamp["source_digest"] = "a digest from an older adapter"
            stamp_path.write_text(json.dumps(stamp), encoding="utf-8")

            rebuilt = ensure_go_adapter(root)
        self.assertTrue(rebuilt.rebuilt)
        self.assertIn("source changed", rebuilt.reason)


@unittest.skipUnless(JAVAC.is_file(), "pinned JDK not provisioned")
class JavaBuildDurabilityTests(unittest.TestCase):
    def test_the_first_build_rebuilds_and_the_second_reuses(self):
        with tempfile.TemporaryDirectory() as directory:
            first = ensure_java_adapter(Path(directory))
            self.assertTrue(first.rebuilt)
            self.assertTrue(first.artifact.is_dir())
            self.assertTrue(any(first.artifact.rglob("*.class")))

            second = ensure_java_adapter(Path(directory))
            self.assertFalse(second.rebuilt)

    def test_a_deleted_class_file_forces_a_rebuild(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = ensure_java_adapter(root)
            next(iter(sorted(first.artifact.rglob("*.class")))).unlink()
            second = ensure_java_adapter(root)
        self.assertTrue(second.rebuilt)
        self.assertIn("not the one that was recorded", second.reason)


@unittest.skipUnless(PYREF.is_file(), "reference Python interpreter not provisioned")
class LizardExternalReferenceTests(unittest.TestCase):
    """Lizard is a second opinion on cyclomatic complexity, never ground truth."""

    def _observe(self, language: str, relative: str):
        with tempfile.TemporaryDirectory() as directory:
            scope = resolve_scope(language, CORPUS / relative.split("/")[0],
                                  [relative.split("/", 1)[1]])
            return run_external_cc(scope, Path(directory))

    def test_go_corpus_is_read_and_reports_cyclomatic_values(self):
        observation = self._observe("Go", "go/constructs.go")
        self.assertEqual(observation.tool, "lizard")
        self.assertEqual(observation.unreadable_files, ())
        self.assertTrue(observation.records)
        for record in observation.records:
            self.assertIsInstance(record["cyclomatic_complexity"], int)
            self.assertEqual(record["relative_path"], "constructs.go")

    def test_an_unreadable_file_is_refused_rather_than_read_as_zero(self):
        """lizard.analyze_file would have returned an empty function list."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "present.go").write_text(
                "package p\n\nfunc F() int { return 1 }\n",
                encoding="utf-8", newline="\n",
            )
            scope = resolve_scope("Go", root, ["present.go", "absent.go"])
            observation = run_external_cc(scope, root)

        self.assertEqual(len(observation.unreadable_files), 1)
        self.assertEqual(
            observation.unreadable_files[0]["relative_path"], "absent.go"
        )
        self.assertIn("read failed", observation.unreadable_files[0]["reason"])
        self.assertEqual(len(observation.records), 1)


@unittest.skipUnless(
    NODE.is_file() and ESLINT.is_dir(), "ESLint reference not provisioned"
)
class EslintExternalReferenceTests(unittest.TestCase):
    """ESLint substitutes for Lizard on TypeScript, and only there."""

    def test_typescript_uses_eslint_not_lizard(self):
        self.assertEqual(EXTERNAL_CC_TOOL["TypeScript"], "eslint")
        for language in ("Go", "Java", "JavaScript", "Python"):
            self.assertEqual(EXTERNAL_CC_TOOL[language], "lizard")

    def test_the_typescript_corpus_reports_every_function(self):
        with tempfile.TemporaryDirectory() as directory:
            scope = resolve_scope(
                "TypeScript", CORPUS / "typescript", ["constructs.ts"]
            )
            observation = run_external_cc(scope, Path(directory))

        self.assertEqual(observation.tool, "eslint")
        self.assertEqual(observation.unreadable_files, ())
        # Threshold 0 makes the rule report every function, not only violations.
        self.assertGreaterEqual(len(observation.records), 16)
        for record in observation.records:
            self.assertIsInstance(record["cyclomatic_complexity"], int)

    def test_a_file_eslint_cannot_parse_is_refused_rather_than_read_as_zero(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "broken.ts").write_text(
                "export function f(: {\n", encoding="utf-8", newline="\n"
            )
            scope = resolve_scope("TypeScript", root, ["broken.ts"])
            observation = run_external_cc(scope, root)

        self.assertEqual(observation.records, ())
        self.assertEqual(len(observation.unreadable_files), 1)
        self.assertIn("parse error", observation.unreadable_files[0]["reason"])

    def test_a_file_outside_the_process_directory_is_still_linted(self):
        """A flat config matches `files` against ESLint's base directory.

        Linting an absolute path outside that base matched no configuration and
        returned no result at all -- a selected file silently contributing
        nothing. Measured on ESLint 9.15.0 before the base was derived from the
        inputs.
        """
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "outside.ts").write_text(
                "export function g(a: number): number { return a > 0 ? 1 : 2; }\n",
                encoding="utf-8", newline="\n",
            )
            scope = resolve_scope("TypeScript", root, ["outside.ts"])
            observation = run_external_cc(scope, root)

        self.assertEqual(observation.unreadable_files, ())
        self.assertEqual(len(observation.records), 1)
        self.assertEqual(observation.records[0]["qualified_name"], "g")
        self.assertEqual(observation.records[0]["cyclomatic_complexity"], 2)


class CampaignSeparationTests(unittest.TestCase):
    """Identity before values, raw before adjudication, counts not percentages."""

    @staticmethod
    def _row(name, start, end, **metrics):
        row = {
            "relative_path": "pkg/a.go",
            "qualified_name": name,
            "start_line": start,
            "end_line": end,
            "structural_complexity_status": "complete",
            "nloc_status": "complete",
        }
        row.update(metrics)
        return row

    def _pair(self, archlens, reference, metrics=("cyclomatic_complexity",)):
        from validation.differential import callable_matching
        from validation.differential import complexity_layer2 as campaign

        match = callable_matching.match_callables(archlens, reference)
        return match, campaign.compare_pairs(
            subject_key="s", language="Go",
            reference_tier=campaign.TIER_PRIMARY, reference_name="ref",
            reference_version="2.1.0", metrics=metrics, match=match,
            archlens_rows=archlens, reference_rows=reference,
        )

    def test_metric_values_cannot_change_the_pairing(self):
        """The property, not the intention: values vary, pairings do not."""
        archlens = [self._row("A", 1, 10, cyclomatic_complexity=3),
                    self._row("B", 20, 30, cyclomatic_complexity=5)]
        agreeing = [self._row("A", 1, 10, cyclomatic_complexity=3),
                    self._row("B", 20, 30, cyclomatic_complexity=5)]
        disagreeing = [self._row("A", 1, 10, cyclomatic_complexity=99),
                       self._row("B", 20, 30, cyclomatic_complexity=-7)]

        first, _ = self._pair(archlens, agreeing)
        second, _ = self._pair(archlens, disagreeing)
        self.assertEqual(
            [(left.qualified_name, right.qualified_name, tier)
             for left, right, tier in first.matched],
            [(left.qualified_name, right.qualified_name, tier)
             for left, right, tier in second.matched],
        )

    def test_a_disagreement_leaves_the_comparison_unresolved(self):
        """Raw is raw: classification is a later, separate pass."""
        from validation.differential import complexity_layer2 as campaign

        _, observations = self._pair(
            [self._row("A", 1, 10, cyclomatic_complexity=3)],
            [self._row("A", 1, 10, cyclomatic_complexity=4)],
        )
        self.assertEqual(len(observations), 1)
        self.assertEqual(observations[0].agreement_status, "disagreement")
        self.assertEqual(observations[0].cause, campaign.CAUSE_UNRESOLVED)
        self.assertEqual(observations[0].difference, 1)

    def test_an_absent_value_is_never_read_as_zero(self):
        archlens = [self._row("A", 1, 10, cyclomatic_complexity=3)]
        reference = [dict(self._row("A", 1, 10), cyclomatic_complexity=None,
                          not_evaluable_reason="parso_grammar_cannot_parse_construct")]
        _, observations = self._pair(archlens, reference)
        self.assertEqual(observations[0].agreement_status, "not_evaluable")
        self.assertIsNone(observations[0].difference)
        self.assertEqual(
            observations[0].not_evaluable_reason,
            "parso_grammar_cannot_parse_construct",
        )

    def test_an_ambiguous_pairing_is_emitted_rather_than_dropped(self):
        from validation.differential import complexity_layer2 as campaign

        archlens = [self._row("Outer", 1, 20, cyclomatic_complexity=2)]
        # A reference that names callables its own way and also measures a
        # literal nested inside the outer span: two span-only candidates.
        reference = [self._row("outer", 1, 20, cyclomatic_complexity=2),
                     self._row("(anonymous)", 5, 8, cyclomatic_complexity=1)]
        match, observations = self._pair(archlens, reference)
        self.assertEqual(match.matched, [])
        self.assertEqual(len(observations), 1)
        self.assertEqual(observations[0].metric, "identity")
        self.assertEqual(observations[0].cause, campaign.CAUSE_MATCHING_AMBIGUITY)

    def test_the_taxonomy_carries_all_eight_required_classes(self):
        from validation.differential import complexity_layer2 as campaign

        self.assertEqual(
            set(campaign.C4_CAUSES),
            {
                "archlens_defect", "adapter_defect", "reference_tool_limitation",
                "metric_definition_mismatch", "parser_limitation",
                "matching_ambiguity", "unsupported_construct", "unresolved",
            },
        )

    def test_no_accuracy_percentage_is_computed_anywhere(self):
        """The reporting rule, enforced mechanically rather than remembered."""
        import ast

        source = Path(
            "validation/differential/complexity_layer2.py"
        ).resolve()
        if not source.is_file():
            source = (
                Path(__file__).resolve().parent.parent
                / "validation/differential/complexity_layer2.py"
            )
        text = source.read_text(encoding="utf-8")
        tree = ast.parse(text)

        def counts_something(node: ast.AST) -> bool:
            # `Path(a) / "b"` is also a Div, so the guard looks at the operands:
            # a ratio over counts is what turns raw observations into a rate.
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                return node.func.id in {"len", "sum"}
            if isinstance(node, ast.Constant):
                return isinstance(node.value, (int, float))
            if isinstance(node, ast.Name):
                return node.id.endswith(("count", "counts", "total", "matched"))
            if isinstance(node, ast.Attribute):
                return node.attr.endswith(("count", "counts", "total", "matched"))
            return False

        for node in ast.walk(tree):
            if not isinstance(node, ast.BinOp):
                continue
            if not isinstance(node.op, (ast.Div, ast.FloorDiv)):
                continue
            if counts_something(node.left) or counts_something(node.right):
                self.fail(
                    "a ratio over observation counts is how an accuracy "
                    "percentage gets computed; a reference is a triangulation "
                    "mechanism, not ground truth"
                )
        # Identifiers only. Prose is not the target -- the module says in words
        # that it computes no rate, and a substring scan cannot tell a
        # prohibition from a violation. A rate has to be NAMED to be produced,
        # and the emitted field names are covered by the artifact test.
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                names.add(node.id.lower())
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(node.name.lower())
            elif isinstance(node, ast.Attribute):
                names.add(node.attr.lower())
        for word in ("percent", "accuracy", "pass_rate", "agreement_rate"):
            for name in names:
                self.assertNotIn(word, name, f"a rate is named: {name[:60]!r}")

    def test_persisted_raw_declares_its_stage(self):
        from validation.differential import complexity_layer2 as campaign

        _, observations = self._pair(
            [self._row("A", 1, 10, cyclomatic_complexity=3)],
            [self._row("A", 1, 10, cyclomatic_complexity=4)],
        )
        result = campaign.ComparisonResult(
            subject_key="s", language="Go",
            reference_tier=campaign.TIER_PRIMARY, reference_name="ref",
            reference_version="2.1.0", scope={},
            population=campaign.PopulationAccounting(),
            observations=observations,
        )
        with tempfile.TemporaryDirectory() as directory:
            written = campaign.persist_raw([result], Path(directory) / "raw.json")
            payload = json.loads(written.read_text(encoding="utf-8"))
        self.assertEqual(payload["stage"], "raw_observations_before_adjudication")
        self.assertEqual(
            payload["comparisons"][0]["observations"][0]["cause"], "unresolved"
        )




if __name__ == "__main__":  # pragma: no cover
    unittest.main()
