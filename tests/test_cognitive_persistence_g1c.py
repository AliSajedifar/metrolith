"""G1-C: Artifact Schema 1.10.0 persistence for ArchLens Cognitive Complexity.

Two integrity questions were settled before the rest of the gate was written,
and both are pinned here so neither can regress silently:

**Where the repository complexity summary lives.** Artifact 1.9's schema declares
it at the document's top level; the producer has always written it under
`metrics.complexity`, and `complexity_view` reads that path first. No fixture
anywhere carries a top-level block. That is a **pre-existing C3 schema/producer
location mismatch**, not a G1-C regression -- a preserved native 1.9 run behaves
identically -- and 1.10 corrects it by declaring the block where it is written.

**Whether cognitive needs its own per-file status.** It does not, and that is
proved rather than asserted: cognitive complexity is computed on the same call
path, from the same body node, in the same traversal as the structural metrics,
so no state exists in which structural is `complete` and cognitive is
unavailable.
"""

from __future__ import annotations

import os

import ast
import csv
import json
import shutil
import tempfile
import unittest
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
SCHEMAS = REPOSITORY_ROOT / "validation/resources/schemas"

#: A native 1.10 run and a preserved native 1.9 run, both produced by the real
#: pipeline. Skipped as a capability statement when the reference tree is absent.
NATIVE_1_10 = Path(os.environ.get("ARCHLENS_DIFFVAL_REFS", ".metrolith-reference")) / 'g1c/go-shop'
NATIVE_1_9 = Path(os.environ.get("ARCHLENS_DIFFVAL_REFS", ".metrolith-reference")) / 'l2c4/layer2-go-shop'


def latest(root: Path) -> Path:
    pointer = json.loads((root / "latest_run.json").read_text(encoding="utf-8"))
    return root / pointer["run_directory"]


def repository_documents(run: Path) -> list[dict]:
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted((run / "repositories").glob("*.json"))
    ]


class SchemaLocationTests(unittest.TestCase):
    """The schema must describe the block where the producer writes it."""

    def _load(self, name: str) -> dict:
        return json.loads((SCHEMAS / name).read_text(encoding="utf-8"))

    def test_1_10_declares_the_complexity_block_under_metrics(self):
        document = self._load("repository_document-1.10.schema.json")
        metrics = document["properties"]["metrics"]
        self.assertIn("complexity", metrics.get("properties", {}))
        self.assertNotIn(
            "complexity", document["properties"],
            "1.9 declared the block at the top level, where nothing writes it; "
            "1.10 must not inherit that location",
        )

    def test_the_1_10_block_carries_the_cognitive_state(self):
        document = self._load("repository_document-1.10.schema.json")
        block = document["properties"]["metrics"]["properties"]["complexity"]
        self.assertIn("cognitive_measurement_state", block["properties"])

    def test_1_9_keeps_its_own_declaration_untouched(self):
        """Frozen bytes stay frozen, wrong location and all."""
        document = self._load("repository_document-1.9.schema.json")
        self.assertIn("complexity", document["properties"])
        self.assertNotIn(
            "cognitive_measurement_state",
            document["properties"]["complexity"]["properties"],
        )

    def test_the_consumer_prefers_the_producer_location(self):
        from modules import complexity_view

        nested = {"metrics": {"complexity": {"status": "complete"}}}
        self.assertEqual(
            complexity_view.repository_block(nested), {"status": "complete"}
        )


@unittest.skipUnless(
    NATIVE_1_10.is_dir() and NATIVE_1_9.is_dir(),
    "reference runs not provisioned",
)
class PersistedBytesTests(unittest.TestCase):
    """Read the written bytes, never producer memory."""

    def test_the_1_9_run_and_the_1_10_run_agree_on_the_location(self):
        """The location question, answered by comparing real artifacts."""
        for label, root, expected_schema in (
            ("1.9", NATIVE_1_9, "1.9.0"), ("1.10", NATIVE_1_10, "1.10.0"),
        ):
            run = latest(root)
            manifest = json.loads(
                (run / "run_manifest.json").read_text(encoding="utf-8")
            )
            document = repository_documents(run)[0]
            with self.subTest(generation=label):
                self.assertEqual(
                    manifest["artifact_schema_version"], expected_schema
                )
                self.assertNotIn(
                    "complexity", document,
                    "no generation has ever written a top-level block",
                )
                self.assertIsInstance(document["metrics"]["complexity"], dict)

    def test_only_the_1_10_document_carries_the_cognitive_state(self):
        older = repository_documents(latest(NATIVE_1_9))[0]
        newer = repository_documents(latest(NATIVE_1_10))[0]
        self.assertNotIn(
            "cognitive_measurement_state", older["metrics"]["complexity"],
            "absence is the fact that marks a pre-1.10 run",
        )
        self.assertEqual(
            newer["metrics"]["complexity"]["cognitive_measurement_state"],
            "measured",
        )

    def test_the_persisted_rows_carry_measured_zeros_not_nulls(self):
        """0 is an ordinary cognitive value; a null would mean unavailable."""
        run = latest(NATIVE_1_10)
        with open(run / "callables.csv", encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertTrue(rows)
        self.assertIn("cognitive_complexity", rows[0])
        values = [row["cognitive_complexity"] for row in rows]
        self.assertEqual([value for value in values if value == ""], [])
        self.assertIn("0", values, "the corpus must exercise a measured zero")
        self.assertTrue(any(value not in ("0", "") for value in values))

    def test_the_1_9_run_has_no_cognitive_column_at_all(self):
        """Absent is a third state, distinct from null and from zero."""
        run = latest(NATIVE_1_9)
        with open(run / "callables.csv", encoding="utf-8", newline="") as handle:
            header = next(csv.reader(handle))
        self.assertNotIn("cognitive_complexity", header)

    def test_the_terminal_status_declares_the_run_level_state(self):
        status = json.loads(
            (latest(NATIVE_1_10) / "run_status.json").read_text(encoding="utf-8")
        )
        self.assertEqual(status["cognitive_measurement_state"], "measured")
        older = json.loads(
            (latest(NATIVE_1_9) / "run_status.json").read_text(encoding="utf-8")
        )
        self.assertNotIn("cognitive_measurement_state", older)

    def test_the_strict_reader_round_trips_both_generations(self):
        from validation.artifact_io.reader import open_run

        for label, root in (("1.9", NATIVE_1_9), ("1.10", NATIVE_1_10)):
            view = open_run(latest(root))
            with self.subTest(generation=label):
                self.assertTrue(view.has_callable_artifact)
                rows = list(view.stream_callables())
                self.assertTrue(rows)
                has_cognitive = "cognitive_complexity" in rows[0]
                self.assertEqual(has_cognitive, label == "1.10")


class FileStatusSufficiencyTests(unittest.TestCase):
    """Proof that cognitive evaluability is coupled to the structural status.

    The decision not to add a per-file cognitive status column is only sound if
    no state exists where structural is `complete` and cognitive is unavailable.
    That is established four ways: one call site, one body, no selective failure
    path, and measurement.
    """

    MODULES = ("go", "java", "js_ts", "python")

    def _assignments(self, name: str) -> list[ast.Assign]:
        tree = ast.parse(
            (REPOSITORY_ROOT / f"modules/callable_analysis/{name}.py")
            .read_text(encoding="utf-8")
        )
        return [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Assign)
            and ast.unparse(node.targets[0]).endswith("cognitive_complexity")
        ]

    def test_exactly_one_assignment_per_language(self):
        for name in self.MODULES:
            with self.subTest(language=name):
                self.assertEqual(
                    len(self._assignments(name)), 1,
                    "a second assignment would be a second failure mode",
                )

    def test_the_assignment_never_yields_none(self):
        """`0` is the only fallback, and 0 means measured.

        Checked on the expression TREE, not its text: `if body is not None` is
        a guard and contains the word, so a substring scan would fail an
        expression that is in fact correct.
        """
        for name in self.MODULES:
            value = self._assignments(name)[0].value
            with self.subTest(language=name):
                yielded = (
                    [value.body, value.orelse]
                    if isinstance(value, ast.IfExp) else [value]
                )
                for branch in yielded:
                    self.assertFalse(
                        isinstance(branch, ast.Constant) and branch.value is None,
                        "a None branch would make cognitive unavailable while "
                        "the structural metrics stayed complete",
                    )

    def test_no_try_block_can_downgrade_one_metric_alone(self):
        """A selective failure path is the only way the coupling could break."""
        for name in (*self.MODULES, "metrics", "cognitive", "__init__"):
            path = REPOSITORY_ROOT / f"modules/callable_analysis/{name}.py"
            if not path.is_file():
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Try):
                    with self.subTest(module=name, line=node.lineno):
                        self.assertNotIn(
                            "cognitive", ast.unparse(node),
                            "cognitive computation inside a try block would let "
                            "it fail while the structural metrics succeeded",
                        )

    def test_the_engine_is_typed_to_return_an_integer(self):
        import inspect

        from modules.callable_analysis.cognitive import measure_cognitive

        # `from __future__ import annotations` makes annotations strings, so
        # the resolved hint is what carries the guarantee.
        import typing

        self.assertIs(typing.get_type_hints(measure_cognitive)["return"], int)

    def test_measured_records_always_carry_a_value(self):
        """The empirical half: every complete record has a cognitive value."""
        from tests.test_cognitive_production_g1b import (
            CORPUS, LANGUAGES, measure_file,
        )
        import ast as python_ast

        from modules.callable_analysis import analyze_callables
        from modules.core_metrics import ParserRegistry

        seen = 0
        for directory, language in LANGUAGES.items():
            for path in sorted((CORPUS / directory).iterdir()):
                if path.suffix not in {".go", ".java", ".js", ".ts", ".tsx", ".py"}:
                    continue
                if path.name == "ConstructsGuarded.java":
                    continue
                raw = path.read_bytes()
                if language == "Python":
                    text = raw.decode("utf-8")
                    result = analyze_callables(
                        language, python_ast.parse(text), raw, path.name,
                        raw_text=text, masked_text=text,
                    )
                else:
                    root = ParserRegistry().get(
                        language, path.suffix
                    ).parse(raw).root_node
                    result = analyze_callables(language, root, raw, path.name)
                for record in result.records:
                    seen += 1
                    if record.structural_complexity_status == "complete":
                        self.assertIsNotNone(
                            record.cognitive_complexity,
                            f"{path.name}:{record.qualified_name} is structurally "
                            f"complete with no cognitive value",
                        )
        self.assertGreaterEqual(seen, 159)


@unittest.skipUnless(NATIVE_1_10.is_dir(), "native 1.10 run not provisioned")
class CognitiveCompletenessMutationTests(unittest.TestCase):
    """The four required mutations. Each must be REFUSED by the byte check."""

    @classmethod
    def setUpClass(cls):
        cls.source = latest(NATIVE_1_10)

    def _copy(self) -> Path:
        target = Path(self.enterContext(tempfile.TemporaryDirectory())) / "run"
        shutil.copytree(self.source, target)
        return target

    def _problems(self, run_dir: Path, state: str = "measured") -> list[str]:
        from modules.run_artifacts import RunArtifacts

        artifacts = RunArtifacts.__new__(RunArtifacts)
        artifacts.run_dir = run_dir
        return artifacts._cognitive_completeness_problems(state)

    def _rewrite_rows(self, run_dir: Path, mutate) -> None:
        path = run_dir / "callables.csv"
        with open(path, encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            fieldnames = list(reader.fieldnames or [])
            rows = [dict(row) for row in reader]
        fieldnames, rows = mutate(fieldnames, rows)
        with open(path, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

    def test_an_untouched_run_passes(self):
        self.assertEqual(self._problems(self._copy()), [])

    def test_claimed_complete_but_callable_artifact_missing(self):
        run_dir = self._copy()
        (run_dir / "callables.csv").unlink()
        if (run_dir / "callables").is_dir():
            shutil.rmtree(run_dir / "callables")
        problems = self._problems(run_dir)
        self.assertTrue(problems)
        self.assertIn("no callable artifact", " ".join(problems))

    def test_the_cognitive_column_removed_entirely(self):
        run_dir = self._copy()

        def drop(fieldnames, rows):
            fieldnames = [name for name in fieldnames if name != "cognitive_complexity"]
            for row in rows:
                row.pop("cognitive_complexity", None)
            return fieldnames, rows

        self._rewrite_rows(run_dir, drop)
        problems = self._problems(run_dir)
        self.assertTrue(problems)
        self.assertIn("no cognitive_complexity column", " ".join(problems))

    def test_an_unavailable_measurement_written_as_zero(self):
        """The sharpest trap: 0 is a legitimate value, so this must be caught."""
        run_dir = self._copy()

        def corrupt(fieldnames, rows):
            rows[0]["structural_complexity_status"] = "failed"
            rows[0]["cognitive_complexity"] = "0"
            return fieldnames, rows

        self._rewrite_rows(run_dir, corrupt)
        problems = self._problems(run_dir)
        self.assertTrue(problems)
        joined = " ".join(problems)
        self.assertIn("under a failed status", joined)
        self.assertIn("never 0", joined)

    def test_a_measured_callable_with_no_value(self):
        run_dir = self._copy()

        def blank(fieldnames, rows):
            rows[0]["cognitive_complexity"] = ""
            return fieldnames, rows

        self._rewrite_rows(run_dir, blank)
        problems = self._problems(run_dir)
        self.assertTrue(problems)
        self.assertIn("0 is a measured value", " ".join(problems))

    def test_a_complete_zero_callable_scope_is_a_verified_zero(self):
        """The fourth required case, and the one that must be ACCEPTED.

        A run over sources that genuinely contain no callable measured
        complexity and found none. C3 built that distinction deliberately, and
        an earlier draft of the cognitive layer contradicted it by reading "no
        rows" as "not measured" -- which failed a legitimate run at
        finalization. The artifact still exists and still reconciles; only the
        row count is zero.
        """
        run_dir = self._copy()

        def empty(fieldnames, rows):
            return fieldnames, []

        self._rewrite_rows(run_dir, empty)
        self.assertEqual(
            self._problems(run_dir), [],
            "a verified zero must not be refused as a missing measurement",
        )

    def test_a_verified_zero_is_still_distinguished_from_a_missing_artifact(self):
        """Zero rows is not the same fact as no artifact."""
        run_dir = self._copy()
        self._rewrite_rows(run_dir, lambda fieldnames, rows: (fieldnames, []))
        self.assertEqual(self._problems(run_dir), [])

        (run_dir / "callables.csv").unlink()
        if (run_dir / "callables").is_dir():
            shutil.rmtree(run_dir / "callables")
        self.assertTrue(self._problems(run_dir))


class ProjectionInvarianceTests(unittest.TestCase):
    """2.3.0 moves the version; it must not move a historical digest."""

    def test_the_version_moved(self):
        from validation.scripts.semantic_projection import (
            SEMANTIC_PROJECTION_VERSION,
        )

        self.assertEqual(SEMANTIC_PROJECTION_VERSION, "2.3.0")

    def test_absent_is_not_normalized_to_null(self):
        """A pre-1.10 row has no key; projecting it must not invent one."""
        from validation.scripts import semantic_projection as projection

        self.assertNotIn(
            "cognitive_complexity",
            getattr(projection, "ABSENT_EQUALS_NULL", ()) or (),
            "normalizing absence to null would erase the distinction the "
            "contract depends on",
        )

    def test_absent_null_and_zero_project_differently(self):
        """Three states, three canonical serializations.

        Exercised through the projection's own allowlist selector, which is
        where absence is either preserved or lost.
        """
        from validation.scripts import semantic_projection as projection

        fields = ("callable_row_id", "cognitive_complexity")
        absent = projection._select({"callable_row_id": "x"}, fields)
        null = projection._select(
            {"callable_row_id": "x", "cognitive_complexity": None}, fields
        )
        zero = projection._select(
            {"callable_row_id": "x", "cognitive_complexity": 0}, fields
        )
        self.assertNotIn("cognitive_complexity", absent)
        self.assertIn("cognitive_complexity", null)
        self.assertIsNone(null["cognitive_complexity"])
        self.assertEqual(zero["cognitive_complexity"], 0)
        serialized = {
            projection._serialize(item) for item in (absent, null, zero)
        }
        self.assertEqual(len(serialized), 3)

    def test_a_preserved_1_9_run_still_projects(self):
        """2.3.0 must not refuse or alter a pre-1.10 run."""
        if not NATIVE_1_9.is_dir():
            self.skipTest("preserved 1.9 run not provisioned")
        from validation.scripts.semantic_projection import (
            measurement_semantic_hash,
        )

        first = measurement_semantic_hash(latest(NATIVE_1_9))
        second = measurement_semantic_hash(latest(NATIVE_1_9))
        self.assertEqual(first, second)
        self.assertTrue(first)


class VersionTests(unittest.TestCase):
    def test_final_versions(self):
        from modules.config import (
            ARTIFACT_SCHEMA_VERSION, COMPLEXITY_CONTRACT_VERSION,
        )
        from validation.artifact_io.compatibility import NATIVE_ARTIFACT_SCHEMA

        self.assertEqual(ARTIFACT_SCHEMA_VERSION, "1.12.0")
        # Unchanged by Artifact 1.11: R0 added an admission authority beside
        # measurement and moved no complexity definition.
        self.assertEqual(COMPLEXITY_CONTRACT_VERSION, "2.0.0")
        self.assertEqual(NATIVE_ARTIFACT_SCHEMA, (1, 12, 0))

    def test_metric_contract_did_not_move(self):
        """Bumping it would make `archlens diff` refuse every historical run."""
        from modules.config import METRIC_CONTRACT_VERSION

        self.assertEqual(METRIC_CONTRACT_VERSION, "3.0.0")

    def test_complexity_contract_has_one_writable_authority(self):
        declarations: list[str] = []
        for path in sorted((REPOSITORY_ROOT / "modules").rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                targets = []
                if isinstance(node, (ast.Assign, ast.AnnAssign)):
                    targets = (
                        node.targets if isinstance(node, ast.Assign) else [node.target]
                    )
                if any(
                    isinstance(target, ast.Name)
                    and target.id == "COMPLEXITY_CONTRACT_VERSION"
                    for target in targets
                ):
                    declarations.append(
                        path.relative_to(REPOSITORY_ROOT).as_posix()
                    )
        self.assertEqual(declarations, ["modules/config.py"])

        from modules import callable_ledger, core_metrics
        from modules.callable_analysis import model
        from modules.config import COMPLEXITY_CONTRACT_VERSION

        self.assertEqual(COMPLEXITY_CONTRACT_VERSION, "2.0.0")
        self.assertIs(
            model.COMPLEXITY_CONTRACT_VERSION,
            COMPLEXITY_CONTRACT_VERSION,
        )
        self.assertIs(
            callable_ledger.COMPLEXITY_CONTRACT_VERSION,
            COMPLEXITY_CONTRACT_VERSION,
        )
        self.assertIs(
            core_metrics.COMPLEXITY_CONTRACT_VERSION,
            COMPLEXITY_CONTRACT_VERSION,
        )

    def test_1_9_remains_readable_through_an_adapter(self):
        from validation.artifact_io.compatibility import SUPPORTED_ARTIFACT_SCHEMAS

        self.assertEqual(SUPPORTED_ARTIFACT_SCHEMAS[(1, 9)]["support"], "adapter")
        # 1.10 became an adapter generation when Artifact 1.11 took the native
        # slot. It stays fully readable: the cognitive artifacts this campaign
        # persisted are unaffected by an admission-only version bump.
        self.assertEqual(SUPPORTED_ARTIFACT_SCHEMAS[(1, 10)]["support"], "adapter")
        self.assertEqual(SUPPORTED_ARTIFACT_SCHEMAS[(1, 11)]["support"], "adapter")
        self.assertEqual(SUPPORTED_ARTIFACT_SCHEMAS[(1, 12)]["support"], "native")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
