"""Differential-validation study: adapter correctness and independence.

The study's own credibility rests on two things these tests protect:

* the reference implementations really are independent of ArchLens, and
* the study never reports agreement it did not establish — an unavailable
  reference produces a recorded `not_evaluable`, never a silent absence.

"No unexplained adapter behaviour" is a stated success criterion, so the
adapters are tested against hand-derived expectations before any result they
produce is trusted.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from validation.differential import definitions, records, study, track_b
from validation.differential.reference import (
    entity_drivers,
    java_entities,
    line_classifier,
    selection,
)

REPOSITORY = Path(__file__).resolve().parent.parent
DIFFERENTIAL = REPOSITORY / "validation" / "differential"
LAYER1 = DIFFERENTIAL / "corpus" / "layer1"


class IndependenceTests(unittest.TestCase):
    """A reference that reuses ArchLens cannot disagree with it."""

    REFERENCE_MODULES = (
        "validation/differential/reference/line_classifier.py",
        "validation/differential/reference/selection.py",
        "validation/differential/reference/java_entities.py",
    )

    @staticmethod
    def _code_symbols(relative: str) -> tuple[set[str], set[str]]:
        """Imported modules and referenced identifiers, ignoring prose.

        Scanned through the AST rather than by text match: these modules
        *document* what they deliberately avoid, and a raw substring search
        would fire on the documentation that states the guarantee.
        """
        import ast

        tree = ast.parse((REPOSITORY / relative).read_text(encoding="utf-8"))
        imported: set[str] = set()
        referenced: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
            elif isinstance(node, ast.Name):
                referenced.add(node.id)
            elif isinstance(node, ast.Attribute):
                referenced.add(node.attr)
        return imported, referenced

    def test_no_reference_implementation_imports_archlens(self):
        for relative in self.REFERENCE_MODULES:
            with self.subTest(module=relative):
                imported, referenced = self._code_symbols(relative)
                leaked = sorted(
                    name for name in imported
                    if name == "modules" or name.startswith("modules.")
                )
                self.assertEqual(
                    leaked, [],
                    f"{relative} imports ArchLens and is therefore not an "
                    f"independent reference",
                )
                for forbidden in (
                    "RepositoryInventory", "core_metrics",
                    "derive_lines_of_code", "derive_classes_structs",
                    "compute_repository_metrics",
                ):
                    self.assertNotIn(forbidden, referenced, relative)

    def test_the_line_reference_uses_no_tokenizer_or_parser(self):
        """ArchLens uses stdlib `tokenize` for Python; the reference must not."""
        imported, referenced = self._code_symbols(
            "validation/differential/reference/line_classifier.py"
        )
        for forbidden in ("tokenize", "ast", "tree_sitter", "re"):
            self.assertNotIn(
                forbidden, imported,
                f"the LOC reference imports {forbidden}; it must remain a "
                f"character scanner independent of any tokenizer or parser",
            )
        self.assertNotIn("parse", referenced)

    def test_track_b_never_receives_the_archlens_file_list(self):
        """Track B's whole purpose is independent derivation of the scope."""
        source = (DIFFERENTIAL / "track_b.py").read_text(encoding="utf-8")
        self.assertIn("selection.select(", source)
        # The reference is called with a filesystem root, never with a list of
        # paths that ArchLens chose.
        self.assertNotIn("selection.select(archlens", source)


class LineClassifierTests(unittest.TestCase):
    """Hand-derived expectations for the independent LOC reference."""

    def test_python_docstrings_are_code_and_comments_are_not(self):
        text = (
            '"""Docstring."""\n'      # code
            "# comment\n"              # comment
            "\n"                       # blank
            "import os\n"              # code
            'marker = "# not a comment"  # real comment\n'  # code
        )
        counts = line_classifier.classify(text, "Python")
        self.assertEqual(counts.total_physical_lines, 5)
        self.assertEqual(counts.blank_lines, 1)
        self.assertEqual(counts.comment_lines, 1)
        self.assertEqual(counts.code_lines, 3)

    def test_a_python_triple_quoted_block_is_code_throughout(self):
        text = 'x = """\n# still a string\n"""\n'
        counts = line_classifier.classify(text, "Python")
        self.assertEqual(counts.code_lines, 3)
        self.assertEqual(counts.comment_lines, 0)

    def test_a_java_block_comment_spans_its_lines(self):
        text = "/* one\n   two */\nclass A { }\n"
        counts = line_classifier.classify(text, "Java")
        self.assertEqual(counts.comment_lines, 2)
        self.assertEqual(counts.code_lines, 1)

    def test_a_comment_marker_inside_a_java_string_is_not_a_comment(self):
        counts = line_classifier.classify('String s = "// nope";\n', "Java")
        self.assertEqual(counts.code_lines, 1)
        self.assertEqual(counts.comment_lines, 0)

    def test_a_go_raw_string_swallows_comment_markers(self):
        text = "var s = `// nope\nstill raw`\nfunc f() {}\n"
        counts = line_classifier.classify(text, "Go")
        self.assertEqual(counts.comment_lines, 0)
        self.assertEqual(counts.code_lines, 3)

    def test_a_javascript_template_literal_is_code(self):
        text = "const s = `line // nope\nmore`;\n"
        counts = line_classifier.classify(text, "JavaScript")
        self.assertEqual(counts.comment_lines, 0)
        self.assertEqual(counts.code_lines, 2)

    def test_a_mixed_code_and_comment_line_counts_as_code(self):
        counts = line_classifier.classify("int x = 1; // trailing\n", "Java")
        self.assertEqual(counts.code_lines, 1)
        self.assertEqual(counts.comment_lines, 0)

    def test_an_unknown_language_is_refused_rather_than_guessed(self):
        with self.assertRaises(line_classifier.UnsupportedLanguage):
            line_classifier.classify("x", "Ruby")


class JavaScriptReferenceAdapterRegressionTests(unittest.TestCase):
    """The TypeScript-API adapter must implement the reviewed owner rules.

    These cases are deliberately expressed in the reference parser's own
    vocabulary and run in its isolated Node/TypeScript environment.  They do
    not call ArchLens or derive expectations from ArchLens output.
    """

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="archlens_jsref_")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def _analyze(self, source: str, name: str = "case.js") -> dict:
        path = self.root / name
        path.write_text(source, encoding="utf-8", newline="\n")
        payload = entity_drivers.javascript_entities([path])
        self.assertEqual(payload["totals"]["files_failed"], 0)
        self.assertEqual(payload["adapter_version"], "1.1.0")
        self.assertIn("adapter 1.1.0", payload["reference_version"])
        return payload["files"][0]

    def test_only_direct_members_of_a_module_bound_object_count(self):
        entry = self._analyze(
            "const Named = {\n"
            "  direct() {},\n"
            "  nested: { notDirect() {} }\n"
            "};\n"
            "Thing.config = { propertyAssigned() {} };\n"
            "use({ inline() {}, nested: { alsoNotDirect() {} } });\n"
        )
        self.assertEqual(entry["methods"], 1)
        self.assertEqual(entry["anonymous_container_members"], 4)

    def test_members_of_a_local_named_class_are_not_nested_functions(self):
        entry = self._analyze(
            "function outer() {\n"
            "  class Local { method() {} }\n"
            "  return Local;\n"
            "}\n"
        )
        self.assertEqual(entry["methods"], 2)
        self.assertEqual(entry["nested_functions"], 0)

    def test_anonymous_default_declaration_is_not_a_named_function(self):
        entry = self._analyze(
            "export default function () {}\n"
            "export function named() {}\n",
            "case.mjs",
        )
        self.assertEqual(entry["methods"], 1)
        self.assertEqual(entry["anonymous_functions"], 1)

    def test_stably_assigned_class_expression_has_a_counted_owner(self):
        entry = self._analyze("const Service = class { run() {} };\n")
        self.assertEqual(entry["types"], 1)
        self.assertEqual(entry["methods"], 1)

    def test_accessors_obey_the_same_direct_owner_boundary(self):
        entry = self._analyze(
            "const Named = { get direct() { return 1; } };\n"
            "Object.defineProperties(Target.prototype, {\n"
            "  value: { get() { return 2; } }\n"
            "});\n"
        )
        self.assertEqual(entry["methods"], 1)
        # `get direct()` is a GetAccessor. The descriptor's `get() {}` is a
        # method whose name is literally "get", not accessor syntax.
        self.assertEqual(entry["accessors"], 1)
        self.assertEqual(entry["anonymous_container_members"], 1)


class Layer2AdjudicationTests(unittest.TestCase):
    """Construct evidence, not filenames, assigns adapter finding families."""

    @staticmethod
    def _record(archlens: int, reference: int, *, status: str, note: str = ""):
        return {
            "subject_key": "s",
            "metric": "methods_functions",
            "evidence_paths": ["case.js"],
            "archlens_result": archlens,
            "reference_result": reference,
            "agreement_status": status,
            "adjudication_note": note,
        }

    def test_adapter_families_are_inferred_from_construct_evidence(self):
        from validation.differential.adjudication_20260810 import adjudicate

        exact = records.AGREEMENT_EXACT
        owner = adjudicate.adapter_finding(
            self._record(2, 4, status=records.AGREEMENT_DISAGREEMENT),
            self._record(
                2, 2, status=exact,
                note="excluded: anonymous_container_members=2, anonymous_functions=0",
            ),
        )
        anonymous = adjudicate.adapter_finding(
            self._record(0, 1, status=records.AGREEMENT_DISAGREEMENT),
            self._record(
                0, 0, status=exact,
                note="excluded: anonymous_container_members=0, anonymous_functions=1",
            ),
        )
        local_class = adjudicate.adapter_finding(
            self._record(3, 2, status=records.AGREEMENT_DISAGREEMENT),
            self._record(3, 3, status=exact, note="nested_functions=1"),
        )
        self.assertEqual(owner, "DVL2-REF-OWNER-001")
        self.assertEqual(anonymous, "DVL2-REF-ANON-DEFAULT-003")
        self.assertEqual(local_class, "DVL2-REF-LOCAL-CLASS-002")

    def test_every_retained_limitation_path_has_a_typed_finding(self):
        from validation.differential.adjudication_20260810 import adjudicate

        self.assertEqual(len(adjudicate.LIMITATION_FINDINGS), 6)
        for finding_id in adjudicate.LIMITATION_FINDINGS.values():
            classification = adjudicate.FINDINGS[finding_id]["classification"]
            self.assertIn(
                classification,
                {"reference_tool_limitation", "parser_limitation"},
            )


class SelectionReferenceTests(unittest.TestCase):
    """The Track B reference derives inclusion from its own rules."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="archlens_sel_")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def _write(self, relative: str, body: str = "x = 1\n") -> None:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8", newline="\n")

    def test_conventional_exclusions_are_derived_independently(self):
        self._write("src/main.py")
        self._write("tests/test_main.py")
        self._write("vendor/lib.py")
        self._write("node_modules/pkg/index.js")
        self._write("build/out.py")
        self._write("README.md", "text\n")

        included = selection.included_paths(selection.select(self.root))
        self.assertEqual(included, {"src/main.py"})

    def test_generated_code_is_detected_from_its_header(self):
        self._write(
            "src/pb2.py",
            "# Code generated by the protocol buffer compiler. DO NOT EDIT!\nx = 1\n",
        )
        found = {item.relative_path: item for item in selection.select(self.root)}
        self.assertFalse(found["src/pb2.py"].included)
        self.assertEqual(found["src/pb2.py"].exclusion_reason, "generated")

    def test_a_generated_marker_in_ordinary_source_is_not_a_banner(self):
        """Regression: the adapter defect Track B found on real code.

        `modules/inventory.py` defines a regex containing "do not edit". The
        first version of this reference matched the raw head text, classified
        ArchLens's own detector as generated, and produced a Track B
        disagreement in which ArchLens was correct. A banner must be a comment.
        """
        self._write(
            "src/detector.py",
            'import re\n'
            'MARKER = re.compile(r"code generated .* do not edit")\n'
            'x = 1\n',
        )
        found = {item.relative_path: item for item in selection.select(self.root)}
        self.assertTrue(
            found["src/detector.py"].included,
            "a file that merely mentions a generated-code phrase in source is "
            "not generated",
        )

    def test_a_real_generated_banner_is_still_detected(self):
        self._write(
            "src/pb.py",
            "# Code generated by protoc. DO NOT EDIT.\nx = 1\n",
        )
        found = {item.relative_path: item for item in selection.select(self.root)}
        self.assertEqual(found["src/pb.py"].exclusion_reason, "generated")

    def test_a_typescript_declaration_file_is_excluded(self):
        self._write("src/types.d.ts", "export declare const x: number;\n")
        found = {item.relative_path: item for item in selection.select(self.root)}
        self.assertEqual(found["src/types.d.ts"].exclusion_reason, "declaration_only")

    def test_language_is_derived_from_the_extension(self):
        self._write("src/a.go", "package main\n")
        found = {item.relative_path: item for item in selection.select(self.root)}
        self.assertEqual(found["src/a.go"].language, "Go")


class DefinitionMappingTests(unittest.TestCase):
    def test_every_mapping_states_both_mechanisms(self):
        for identifier, mapping in definitions.REGISTRY.items():
            with self.subTest(mapping=identifier):
                self.assertTrue(mapping.archlens_mechanism)
                self.assertTrue(mapping.reference_mechanism)
                self.assertNotEqual(
                    mapping.archlens_mechanism, mapping.reference_mechanism,
                    "a reference sharing ArchLens's mechanism is not independent",
                )
                self.assertTrue(mapping.independent)

    def test_the_java_mapping_is_still_the_one_that_was_reviewed(self):
        """**Drift detector.** Not a synchronizer.

        The intended relationship is three separate things:

            reviewed metric-definition mapping   (definitions.py, by hand)
          + independent reference implementation (javac, by hand)
          + this detector, which fails if ArchLens's semantics move

        It deliberately does NOT derive the mapping or the reference from
        ArchLens. If this fails, the correct response is to re-review the
        mapping and decide what the reference should do — never to regenerate
        the reference from the production implementation, which would make the
        two agree by construction and destroy the study's independence.

        The first draft of the mapping asserted that interfaces, enums and
        constructors were counted. They are not, and that wrong mapping
        manufactured a disagreement. This detector exists so that mistake is
        caught by review rather than by a false finding.
        """
        import inspect

        from modules import core_metrics

        classes = inspect.getsource(core_metrics.derive_classes_structs)
        methods = inspect.getsource(core_metrics.derive_methods_functions)
        message = (
            "ArchLens's entity definition changed. RE-REVIEW "
            "validation/differential/definitions.py and decide deliberately "
            "what the independent reference should count. Do not regenerate "
            "the reference from this source."
        )
        for component in ("classes", "records", "structs"):
            self.assertIn(component, classes, message)
        for excluded in ("interfaces", "enums", "annotation_types"):
            self.assertNotIn(excluded, classes, message)
        for component in ("module_functions", "class_methods", "receiver_methods"):
            self.assertIn(component, methods, message)
        self.assertNotIn("constructors", methods, message)
        self.assertNotIn("signature_only_methods", methods, message)

        self.assertIn("EXCLUDED", definitions.JAVA_TYPES.construct_treatment)
        self.assertIn("EXCLUDED", definitions.JAVA_METHODS.construct_treatment)

    def test_the_reference_semantics_are_written_down_not_derived(self):
        """The reference must carry its own definition, independently.

        A reference that read ArchLens's component names, imported its
        `derive_*` functions, or was generated from its source could not
        disagree with it. Each reference states its counting rule in its own
        source, in its own technology.
        """
        source = (
            DIFFERENTIAL / "reference" / "java" / "ReferenceEntityCount.java"
        ).read_text(encoding="utf-8")
        # The reference *documents* the ArchLens decomposition it implements,
        # which is exactly what a reviewed mapping should do. Only executable
        # code is checked, so that documentation is not mistaken for coupling.
        code = line_classifier.mask_comments(source, "Java")

        # The rule is expressed in javac's own vocabulary.
        self.assertIn("case CLASS, RECORD", code)
        self.assertIn("<init>", code)
        self.assertIn("getBody() == null", code)
        # And the executable path never reaches back into ArchLens. Matched on
        # word boundaries: the reference's own JSON key
        # `anonymous_class_methods` legitimately contains `class_methods`.
        import re

        for forbidden in (
            "derive_classes_structs", "core_metrics", "class_methods",
            "module_functions", "signature_only_methods", "receiver_methods",
        ):
            self.assertIsNone(
                re.search(rf"\b{forbidden}\b", code),
                f"the reference's executable code names ArchLens's "
                f"{forbidden!r} component",
            )

    def test_no_reference_output_is_read_back_from_archlens_entities(self):
        """Track A may take ArchLens's *file list*; never its entity results."""
        import ast

        source = (DIFFERENTIAL / "track_a.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        called = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        for forbidden in (
            "compute_repository_metrics", "derive_classes_structs",
            "derive_methods_functions", "derive_lines_of_code",
        ):
            self.assertNotIn(forbidden, called)

    def test_every_supported_entity_pair_now_has_a_reviewed_mapping(self):
        """Provisioning closed the gaps this test used to assert existed.

        Before the reference environment was provisioned, entity metrics for
        Python, JavaScript, TypeScript and Go had no independent reference. All
        four now do, so the assertion is inverted: full coverage is the state,
        and a regression to a gap must be deliberate.
        """
        for language in ("Python", "Java", "JavaScript", "TypeScript", "Go"):
            for metric in ("classes_structs", "methods_functions"):
                with self.subTest(metric=metric, language=language):
                    self.assertIn(
                        (metric, language), definitions.ENTITY_MAPPINGS,
                        "every supported entity metric needs a reviewed "
                        "definition mapping",
                    )

    def test_js_owner_exclusions_are_retained_as_record_evidence(self):
        """A corrected over-count must remain visible after equality returns."""
        from validation.differential import track_a

        for language in ("JavaScript", "TypeScript"):
            with self.subTest(language=language):
                method_evidence = track_a._ENTITY_REFERENCES[language][3]
                self.assertIn("anonymous_container_members", method_evidence)

    def test_a_coverage_gap_would_still_be_reported_with_its_reason(self):
        """The mechanism, tested independently of whether a gap exists today.

        Both gap kinds are kept distinct: a missing toolchain is a capability
        gap that provisioning can close, and a definition gap is not.
        """
        from unittest.mock import patch

        with (
            patch.dict(
                definitions.UNAVAILABLE_REFERENCES,
                {("classes_structs", "Fictional"): "no reference toolchain exists"},
                clear=False,
            ),
            patch.dict(
                definitions.NOT_COMPARABLE_DEFINITIONS,
                {("methods_functions", "Fictional"): "definitions cannot be mapped"},
                clear=False,
            ),
        ):
            coverage = definitions.coverage_report()
            uncovered = {
                (item["metric"], item["language"]): item["reason"]
                for item in coverage["not_covered"]
            }
            self.assertIn(("classes_structs", "Fictional"), uncovered)
            self.assertIn(("methods_functions", "Fictional"), uncovered)
            for reason in uncovered.values():
                self.assertTrue(reason, "an uncovered metric must say why")

    def test_the_two_gap_kinds_are_not_conflated(self):
        """`not_evaluable` is capability; `not_comparable_definition` is meaning."""
        self.assertNotEqual(
            records.AGREEMENT_NOT_EVALUABLE, records.AGREEMENT_NOT_COMPARABLE
        )
        self.assertIn(records.AGREEMENT_NOT_COMPARABLE, records.AGREEMENT_STATUSES)


class RecordInvariantTests(unittest.TestCase):
    BASE = dict(
        subject_key="s", analyzed_revision=None, analysis_scope_hash=None,
        language="Python", track=records.TRACK_A, corpus_layer="layer1_synthetic",
        metric="lines_of_code", definition_mapping_id="x",
        archlens_version="3.5.1", metric_contract_version="3.0.0",
        exclusion_policy_version="1.5.0", reference_name="r",
        reference_version="1", archlens_result=1, reference_result=1,
        difference=0,
    )

    def test_a_disagreement_must_carry_a_cause(self):
        with self.assertRaises(ValueError):
            records.DifferentialRecord(
                **{**self.BASE, "agreement_status": records.AGREEMENT_DISAGREEMENT,
                   "disagreement_cause": records.CAUSE_NONE}
            )

    def test_unresolved_is_an_accepted_terminal_cause(self):
        record = records.DifferentialRecord(
            **{**self.BASE, "agreement_status": records.AGREEMENT_DISAGREEMENT,
               "disagreement_cause": records.CAUSE_UNRESOLVED}
        )
        self.assertEqual(record.disagreement_cause, "unresolved")

    def test_a_not_evaluable_record_must_state_why(self):
        with self.assertRaises(ValueError):
            records.DifferentialRecord(
                **{**self.BASE, "agreement_status": records.AGREEMENT_NOT_EVALUABLE,
                   "disagreement_cause": records.CAUSE_NONE}
            )

    def test_an_unknown_cause_is_refused(self):
        with self.assertRaises(ValueError):
            records.DifferentialRecord(
                **{**self.BASE, "agreement_status": records.AGREEMENT_DISAGREEMENT,
                   "disagreement_cause": "probably_fine"}
            )

    def test_the_summary_does_not_report_an_agreement_rate_verdict(self):
        summary = records.summarize([
            records.DifferentialRecord(
                **{**self.BASE, "agreement_status": records.AGREEMENT_DISAGREEMENT,
                   "disagreement_cause": records.CAUSE_UNRESOLVED}
            )
        ])
        self.assertFalse(summary["all_disagreements_classified"])
        self.assertEqual(summary["unresolved_disagreements"], 1)
        self.assertNotIn("passed", summary)


class StudyOutputContractTests(unittest.TestCase):
    """Producer-to-schema contract, written with the schema."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="archlens_dvout_")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def _document(self):
        record = records.DifferentialRecord(
            subject_key="s", analyzed_revision=None, analysis_scope_hash=None,
            language="Java", track=records.TRACK_A,
            corpus_layer="layer1_synthetic", metric="classes_structs",
            definition_mapping_id=definitions.JAVA_TYPES.identifier,
            archlens_version="3.5.1", metric_contract_version="3.0.0",
            exclusion_policy_version="1.5.0", reference_name="jdk",
            reference_version="javac 17", archlens_result=2, reference_result=2,
            difference=0, agreement_status=records.AGREEMENT_EXACT,
            disagreement_cause=records.CAUSE_NONE, evidence_paths=["A.java"],
        )
        destination = records.write_records(
            [record], self.root / "out.json", study_metadata=study.study_metadata()
        )
        return json.loads(destination.read_text(encoding="utf-8"))

    def test_the_emitted_document_validates_against_the_study_schema(self):
        self.assertEqual(records.validate_document(self._document()), [])

    def test_an_undeclared_property_is_rejected(self):
        document = self._document()
        document["invented"] = True
        self.assertTrue(records.validate_document(document))

    def test_the_schema_lives_outside_the_production_registry(self):
        """Study output must not be advertised as a production contract."""
        from validation.artifact_io.schema_store import SCHEMA_REGISTRY

        self.assertNotIn("differential_record", SCHEMA_REGISTRY)
        for filename, _version in SCHEMA_REGISTRY.values():
            self.assertNotIn("differential", filename)
        self.assertTrue(records.SCHEMA_PATH.is_file())
        self.assertNotIn("resources", records.SCHEMA_PATH.parts)

    def test_study_metadata_states_that_no_reference_is_ground_truth(self):
        metadata = self._document()["study_metadata"]
        self.assertIn("ground truth", metadata["ground_truth_disclaimer"])
        self.assertIn("not evidence of real-world validity",
                      metadata["layer1_disclaimer"])
        self.assertIn("coverage", metadata)


class JavaReferenceTests(unittest.TestCase):
    def test_availability_is_reported_rather_than_assumed(self):
        state = java_entities.availability()
        self.assertIsInstance(state.available, bool)
        if not state.available:
            self.assertTrue(state.reason)
        else:
            self.assertTrue(state.version)

    def test_the_reference_counts_the_mapped_definition(self):
        state = java_entities.availability()
        if not state.available:
            self.skipTest(state.reason or "no JDK available")
        payload = java_entities.count_entities(
            [LAYER1 / "java" / "Service.java"]
        )
        entry = payload["files"][0]
        # class Service + record Pair. Interface and enum are excluded by the
        # mapped definition, and reported separately as evidence.
        self.assertEqual(entry["types"], 2)
        self.assertEqual(entry["all_types"], 4)
        self.assertEqual(entry["interfaces"], 1)
        self.assertEqual(entry["enums"], 1)
        # handle() only: the constructor and the interface method are excluded.
        self.assertEqual(entry["methods"], 1)
        self.assertEqual(entry["constructors"], 1)
        self.assertEqual(entry["abstract_or_interface_methods"], 1)


class Layer1StudyTests(unittest.TestCase):
    """End to end over the synthetic layer."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="archlens_layer1_")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def _run(self, case: str, language: str):
        from unittest.mock import patch

        root = LAYER1 / case
        with patch("modules.benchmark_runner._distribution_version", return_value="4.0.1"):
            run_directory, summary = study.analyze_subject(
                root, self.root / case, subject_key=f"layer1-{case}",
                expected_language=language,
            )
        self.assertEqual(summary["status"], "completed")
        context = study.context_for(
            run_directory, f"layer1-{case}", "layer1_synthetic"
        )
        return root, run_directory, context

    def test_loc_agrees_independently_for_every_supported_language(self):
        for case, language in (
            ("python", "Python"), ("java", "Java"),
            ("javascript", "JavaScript"), ("go", "Go"),
        ):
            with self.subTest(language=language):
                root, run_directory, context = self._run(case, language)
                from validation.differential import track_a

                found = track_a.run_loc(root, run_directory, context)
                self.assertTrue(found, "no LOC comparison was produced")
                for record in found:
                    self.assertEqual(
                        record.agreement_status, records.AGREEMENT_EXACT,
                        f"{record.evidence_paths}: ArchLens "
                        f"{record.archlens_result} vs reference "
                        f"{record.reference_result}",
                    )

    def test_java_entities_agree_with_javac_under_the_mapped_definition(self):
        if not java_entities.availability().available:
            self.skipTest("no JDK available")
        from validation.differential import track_a

        root, run_directory, context = self._run("java", "Java")
        found = track_a.run_java_entities(root, run_directory, context)
        self.assertEqual(len(found), 2)
        for record in found:
            self.assertEqual(
                record.agreement_status, records.AGREEMENT_EXACT,
                f"{record.metric}: {record.archlens_result} vs "
                f"{record.reference_result}",
            )
            self.assertIn("excluded by the mapped definition",
                          record.adjudication_note)

    def test_track_b_independently_reproduces_the_selection(self):
        root, run_directory, context = self._run("selection", "Python")
        comparison = track_b.compare_selection(
            root, run_directory, "layer1-selection"
        )
        self.assertEqual(comparison["archlens_only_inclusions"], [])
        self.assertEqual(comparison["reference_only_inclusions"], [])
        self.assertTrue(comparison["exact_agreement"])
        self.assertEqual(comparison["archlens_included_count"], 1)

    def test_entity_metrics_are_now_evaluated_for_every_language(self):
        """The provisioned references cover all five languages."""
        from validation.differential import track_a

        for case, language in (
            ("python", "Python"), ("java", "Java"),
            ("javascript", "JavaScript"), ("typescript", "TypeScript"),
            ("go", "Go"),
        ):
            with self.subTest(language=language):
                root, run_directory, context = self._run(case, language)
                found = track_a.run_entities(root, run_directory, context)
                metrics = {record.metric for record in found}
                self.assertEqual(
                    metrics, {"classes_structs", "methods_functions"},
                    f"{language} produced no entity comparison",
                )
                for record in found:
                    self.assertEqual(
                        record.agreement_status, records.AGREEMENT_EXACT,
                        f"{language} {record.metric}: ArchLens "
                        f"{record.archlens_result} vs reference "
                        f"{record.reference_result} ({record.evidence_paths})",
                    )

    def test_a_reference_that_cannot_run_is_recorded_not_skipped(self):
        """A broken toolchain must still produce an explicit record."""
        from unittest.mock import patch

        from validation.differential import track_a

        root, run_directory, context = self._run("python", "Python")
        with patch.object(
            track_a.entity_drivers, "python_entities",
            side_effect=RuntimeError("simulated: reference interpreter missing"),
        ):
            found = track_a.run_entities(root, run_directory, context)

        self.assertEqual(len(found), 2)
        for record in found:
            self.assertEqual(record.agreement_status, records.AGREEMENT_NOT_EVALUABLE)
            self.assertIn("simulated", record.not_evaluable_reason)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
