import csv
import hashlib
import json
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from modules import benchmark_runner
from modules.acquisition import AcquiredRepository, AcquisitionRecord
from modules.benchmark_runner import run_benchmark
from modules.config import AnalysisConfig
from modules.core_metrics import compute_repository_metrics
from modules.inventory import RepositoryInventory
from validation.scripts.validate_outputs import validate_run


class ParserCompatibilityV34Tests(unittest.TestCase):
    def _repo(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        return Path(temporary.name)

    def _metrics(self, filename, content, *, extra_files=None, expected_language=None):
        repo = self._repo()
        path = repo / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
        for extra_name, extra_content in (extra_files or {}).items():
            extra = repo / extra_name
            extra.parent.mkdir(parents=True, exist_ok=True)
            extra.write_bytes(
                extra_content
                if isinstance(extra_content, bytes)
                else extra_content.encode("utf-8")
            )
        inventory = RepositoryInventory(repo)
        metrics = compute_repository_metrics(
            inventory, expected_language=expected_language
        )
        return metrics, inventory, inventory.get(filename)

    def _recovery(self, metrics, filename):
        return next(
            item
            for item in metrics["recovered_parser_diagnostics"]
            if item["file_path"] == filename
        )

    def test_minimal_spoolman_nul_pattern_recovers_without_changing_bytes(self):
        source = b"const OPEN = '\x00IPL\x00';\nconst CLOSE = '\x00IPR\x00';\n"
        metrics, inventory, record = self._metrics(
            "client_v2/scripts/convert-locales.mjs", source
        )
        self.assertEqual(metrics["aggregate"]["metric_status"], "complete")
        self.assertEqual(record.content_hash, hashlib.sha256(source).hexdigest())
        self.assertEqual(inventory.read_bytes(record), source)
        expected_positions = [index for index, byte in enumerate(source) if byte == 0]
        self.assertEqual(record.nul_count, 4)
        self.assertEqual(record.nul_positions, expected_positions)
        self.assertAlmostEqual(record.nul_density, 4 / len(source))
        self.assertEqual(record.nul_classification, "low_density_intentional_textual_nul")
        self.assertFalse(record.suspected_bomless_utf16)
        self.assertIn("low_density_textual_nul_compat", record.parser_compatibility_strategy)
        self.assertEqual(record.original_byte_length, record.parser_byte_length)
        self.assertTrue(record.parser_offsets_map_directly_to_original_bytes)

    def test_minimal_dotcms_nul_pattern_recovers(self):
        source = b'function key(sessionId, url) { return `${sessionId}\x00${url}`; }\n'
        metrics, _, record = self._metrics(
            "core-web/libs/sdk/ai/src/adapter/context-cache.ts", source
        )
        self.assertEqual(metrics["aggregate"]["metric_status"], "complete")
        self.assertEqual(record.nul_classification, "low_density_intentional_textual_nul")
        self.assertIn("low_density_textual_nul_compat", record.parser_compatibility_strategy)

    def test_low_density_nul_is_allowed_only_in_recognized_textual_contexts(self):
        accepted = {
            "single.js": b"const value = '\x00';\n",
            "double.js": b'const value = "\x00";\n',
            "template.js": b"const value = `\x00`;\n",
            "line-comment.js": b"const value = 1; // \x00 marker\n",
            "block-comment.js": b"const value = 1; /* \x00 marker */\n",
        }
        for filename, source in accepted.items():
            with self.subTest(filename=filename):
                metrics, _, record = self._metrics(filename, source)
                self.assertEqual(metrics["aggregate"]["metric_status"], "complete")
                self.assertEqual(
                    record.nul_classification,
                    "low_density_intentional_textual_nul",
                )

        metrics, _, record = self._metrics(
            "between.js", b"const\x00 value = 1;\n"
        )
        diagnostic = metrics["parser_diagnostics"][0]
        self.assertEqual(metrics["aggregate"]["loc_status"], "failed")
        self.assertEqual(diagnostic["error_category"], "source_encoding_failure")
        self.assertEqual(record.nul_classification, "untrusted_textual_nul")

    def test_nul_binary_and_encoding_classes_remain_conservative(self):
        cases = {
            "alternating.js": (
                b"A\x00\x00B\x00\x00C\x00\x00D\x00\x00",
                "high_density_alternating_nul",
            ),
            "bomless.js": (
                "const value = 1;\n".encode("utf-16-le"),
                "bomless_utf16_like",
            ),
            "binary.js": (
                b"\x01\x00\x02\x00\x03\x00\x04\x00",
                "arbitrary_binary",
            ),
            "invalid.js": (b"const value = '\xff';\n", "invalid_utf8"),
        }
        for filename, (source, classification) in cases.items():
            with self.subTest(filename=filename):
                metrics, _, record = self._metrics(filename, source)
                self.assertEqual(metrics["aggregate"]["loc_status"], "failed")
                self.assertEqual(
                    metrics["parser_diagnostics"][0]["error_category"],
                    "source_encoding_failure",
                )
                self.assertEqual(record.nul_classification, classification)

        text = "const value = 'ok';\n"
        for filename, source, encoding in (
            ("little.js", b"\xff\xfe" + text.encode("utf-16-le"), "utf-16-le"),
            ("big.js", b"\xfe\xff" + text.encode("utf-16-be"), "utf-16-be"),
        ):
            with self.subTest(filename=filename):
                metrics, _, record = self._metrics(filename, source)
                self.assertEqual(metrics["aggregate"]["metric_status"], "complete")
                self.assertEqual(record.original_encoding, encoding)
                self.assertEqual(record.encoding_transformation_applied, "utf16_to_utf8")

    def test_nul_preview_and_offsets_are_deterministic_and_truthful(self):
        source = b'const marker = "\x00";\nfunction broken( {\n'
        outcomes = []
        for _ in range(2):
            metrics, _, record = self._metrics("deterministic.js", source)
            diagnostic = metrics["parser_diagnostics"][0]
            outcomes.append(
                (
                    diagnostic["preview"],
                    diagnostic["first_error_start_byte"],
                    diagnostic["first_error_parser_start_byte"],
                    record.nul_positions,
                )
            )
            self.assertNotIn("\x00", diagnostic["preview"])
            self.assertIn("\\x00", diagnostic["preview"])
            self.assertEqual(
                diagnostic["first_error_start_byte"],
                diagnostic["first_error_parser_start_byte"],
            )
            self.assertTrue(diagnostic["parser_offsets_map_directly_to_original_bytes"])
        self.assertEqual(outcomes[0], outcomes[1])

    def test_raw_ampersands_recover_only_in_jsx_text_and_quoted_attributes(self):
        positives = {
            "view.jsx": "const V = () => <div>A & B</div>;\n",
            "view.tsx": "const V = () => <div>A & B</div>;\n",
            "query.jsx": 'const V = () => <a href="...?a=1&b=2">go</a>;\n',
            "multiple.tsx": (
                'const V = () => <a title="A & B" href="?a=1&b=2">A & B</a>;\n'
            ),
        }
        for filename, source in positives.items():
            with self.subTest(filename=filename):
                metrics, _, record = self._metrics(filename, source)
                self.assertEqual(metrics["aggregate"]["metric_status"], "complete")
                recovery = self._recovery(metrics, filename)
                self.assertIn(
                    "raw_jsx_ampersand_compat",
                    recovery["selected_fallback_strategies"],
                )
                self.assertTrue(recovery["fallback_offsets_match_original"])
                self.assertEqual(record.original_byte_length, recovery["parser_byte_length"])

    def test_obojobo_coarse_error_does_not_trigger_an_unsafe_ampersand_fallback(self):
        source = (
            'const V = () => <link href="//fonts.googleapis.com/css?'
            'family=Noto+Serif:400,400i&display=swap" />;\n'
            "const config = { first: value, mode: defaultMode, next: value };\n"
        )
        metrics, _, _ = self._metrics("obojobo.jsx", source)
        self.assertEqual(metrics["aggregate"]["metric_status"], "partial")
        diagnostic = metrics["parser_diagnostics"][0]
        self.assertEqual(diagnostic["error_category"], "syntax_partial")
        self.assertNotEqual(
            source.encode("utf-8")[diagnostic["first_error_start_byte"] :][0:1],
            b"&",
        )
        self.assertNotIn(
            "raw_jsx_ampersand_compat",
            diagnostic["selected_fallback_strategies"],
        )

    def test_ampersand_compatibility_does_not_touch_other_constructs(self):
        negatives = {
            "entity.jsx": "const V = () => <div>A &amp; B</div>;\n",
            "bitwise.jsx": "const value = left & right;\n",
            "logical.jsx": "const value = left && right;\n",
            "assign.jsx": "left &= right;\n",
            "string.jsx": 'const value = "A & B";\n',
            "comment.jsx": "const V = () => <div>{/* A & B */}</div>;\n",
        }
        for filename, source in negatives.items():
            with self.subTest(filename=filename):
                metrics, _, _ = self._metrics(filename, source)
                self.assertEqual(metrics["aggregate"]["metric_status"], "complete")
                selected = [
                    strategy
                    for item in metrics["recovered_parser_diagnostics"]
                    for strategy in item["selected_fallback_strategies"]
                ]
                self.assertNotIn("raw_jsx_ampersand_compat", selected)

    def test_typescript_keyword_parameter_names_recover_in_function_types(self):
        cases = {
            "keywords.ts": (
                "type A = (any) => void;\n"
                "type B = (boolean) => void;\n"
                "type C = (string) => void;\n"
            ),
            "keywords.tsx": (
                "interface Props { onAny: (any) => void; onFlag: (boolean) => void }\n"
                "const V = (_props: Props) => <div />;\n"
            ),
        }
        for filename, source in cases.items():
            with self.subTest(filename=filename):
                metrics, _, record = self._metrics(filename, source)
                self.assertEqual(metrics["aggregate"]["metric_status"], "complete")
                recovery = self._recovery(metrics, filename)
                self.assertIn(
                    "typescript_keyword_parameter_compat",
                    recovery["selected_fallback_strategies"],
                )
                self.assertTrue(recovery["fallback_offsets_match_original"])
                self.assertEqual(record.original_byte_length, recovery["parser_byte_length"])

    def test_typescript_keyword_parameter_compatibility_has_negative_controls(self):
        source = (
            "type Typed = (x: boolean) => void;\n"
            "type Union = boolean | string;\n"
            "const runtime = (boolean) => boolean;\n"
            "function identity<T>(value: T): T { return value; }\n"
            "const string = 1;\n"
        )
        metrics, _, _ = self._metrics("controls.ts", source)
        self.assertEqual(metrics["aggregate"]["metric_status"], "complete")
        selected = [
            strategy
            for item in metrics["recovered_parser_diagnostics"]
            for strategy in item["selected_fallback_strategies"]
        ]
        self.assertNotIn("typescript_keyword_parameter_compat", selected)

    def test_typed_javascript_requires_positive_repository_dialect_evidence(self):
        source = (
            "class Connector {\n"
            "  updateState(update: any) { return update; }\n"
            "}\n"
            "export function makeAsync(importComponent: Function) { return importComponent; }\n"
        )
        evidence = {
            ".flowconfig": "[ignore]\n",
            "package.json": json.dumps(
                {"devDependencies": {"flow-bin": "0.91.0", "@babel/preset-flow": "7.0.0"}}
            ),
        }
        metrics, _, record = self._metrics(
            "src/typed.js", source, extra_files=evidence
        )
        self.assertEqual(metrics["by_language"]["javascript"]["metric_status"], "complete")
        self.assertEqual(record.detected_language, "JavaScript")
        recovery = self._recovery(metrics, "src/typed.js")
        self.assertEqual(recovery["fallback_grammar"], "tsx")
        self.assertIn(
            "typed_javascript_tsx_compat",
            recovery["selected_fallback_strategies"],
        )
        self.assertTrue(recovery["typed_javascript_dialect_evidence"])

        unsupported, _, _ = self._metrics("typed.js", source)
        diagnostic = unsupported["parser_diagnostics"][0]
        self.assertEqual(diagnostic["error_category"], "unsupported_javascript_dialect")
        self.assertEqual(unsupported["aggregate"]["metric_status"], "partial")


class RepositoryDiagnosticV34Tests(unittest.TestCase):
    @staticmethod
    def _language(status):
        return {
            "source_files": 0 if status == "not_applicable" else 1,
            "metric_status": status,
            "inventory_status": status,
            "source_files_status": status,
            "loc_status": status,
            "classes_structs_status": status,
            "methods_functions_status": status,
        }

    def _result(self, expected, statuses, *, analysis_status="partial", errors=None):
        by_language = {
            language: self._language(statuses.get(language, "not_applicable"))
            for language in ("java", "javascript", "typescript", "python", "go")
        }
        aggregate_status = (
            "complete" if analysis_status == "complete" else "failed"
            if analysis_status == "failed"
            else "partial"
        )
        return {
            "expected_language": expected,
            "analysis_status": analysis_status,
            "metrics": {
                "by_language": by_language,
                "aggregate": {
                    "metric_status": aggregate_status,
                    "inventory_status": "failed" if analysis_status == "failed" else "complete",
                    "source_files_status": "failed" if analysis_status == "failed" else "complete",
                },
            },
            "errors": errors or [],
        }

    def _derive(self, result):
        derive = getattr(benchmark_runner, "derive_repository_diagnostics")
        return derive(result)

    def test_expected_family_and_partial_origin_cases(self):
        cases = [
            (
                self._result("Java", {"java": "complete", "javascript": "partial"}),
                ("complete", "secondary_supported_language_only"),
            ),
            (
                self._result("JavaScript", {"javascript": "complete", "typescript": "partial"}),
                ("partial", "expected_language_family"),
            ),
            (
                self._result("Python", {"python": "partial"}),
                ("partial", "expected_language_family"),
            ),
            (
                self._result(
                    "Java",
                    {},
                    analysis_status="failed",
                    errors=[{"module": "acquisition", "error_category": "acquisition_failure"}],
                ),
                ("failed", "acquisition_or_inventory"),
            ),
            (
                self._result(
                    "Python",
                    {"python": "partial"},
                    errors=[{"module": "inventory", "error_category": "git_mode_map_failed"}],
                ),
                ("partial", "multiple"),
            ),
            (
                self._result(None, {"go": "complete"}, analysis_status="complete"),
                ("not_applicable", "none"),
            ),
        ]
        for result, expected in cases:
            with self.subTest(expected=expected):
                derived = self._derive(result)
                self.assertEqual(
                    (
                        derived["expected_language_family_status"],
                        derived["partial_origin"],
                    ),
                    expected,
                )


class ArtifactAndVersionV34Tests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repo = self.root / "fixture"
        self.repo.mkdir()
        (self.repo / "app.py").write_text(
            "class App:\n    def run(self):\n        return 1\n", encoding="utf-8"
        )
        self.input = self.root / "repositories.csv"
        self.input.write_text(
            "url,architecture_type,expected_language,commit_sha,enabled,notes\n"
            f"https://github.com/acme/fixture,monolith,Python,{'a' * 40},true,diagnostic\n",
            encoding="utf-8",
        )

    @contextmanager
    def _acquire(self, spec, config, mode="offline", progress=None):
        del config, mode
        if progress:
            progress("creating detached worktree")
        yield AcquiredRepository(
            self.repo,
            AcquisitionRecord(
                repository_url=spec.url,
                repository_owner=spec.owner,
                repository_name=spec.repository_name,
                requested_commit_sha=spec.commit_sha,
                analyzed_commit_sha="a" * 40,
                resolved_ref="refs/heads/main",
                default_branch="main",
                acquisition_mode="offline",
                cache_status="reused",
                remote_checked=False,
                fetch_timestamp=None,
                checkout_timestamp="2026-08-05T00:00:00Z",
                commit_verification_status="verified",
                fetch_method="offline_cache",
            ),
        )

    def _run(self):
        config = AnalysisConfig.from_env(
            workspace=self.root,
            output_root=self.root / "output",
            cache_root=self.root / "cache",
            temporary_directory=self.root / "temp",
            workers=1,
        )
        with (
            patch("modules.benchmark_runner.acquire_repository", self._acquire),
            patch("modules.benchmark_runner._distribution_version", return_value="4.0.0"),
        ):
            return run_benchmark(
                [self.input], config, "offline", command_line_arguments=["test"]
            )

    def test_diagnostic_fields_are_emitted_and_independently_validated(self):
        summary = self._run()
        run = Path(summary["run_directory"])
        analysis = json.loads((run / "analysis.json").read_text(encoding="utf-8"))
        result = analysis[0]
        self.assertEqual(result["expected_language_family_status"], "complete")
        self.assertEqual(result["partial_origin"], "none")

        for name in ("sheet_metrics.csv", "catalog.csv"):
            with (run / name).open(encoding="utf-8", newline="") as handle:
                row = next(csv.DictReader(handle))
            self.assertEqual(row["expected_language_family_status"], "complete")
            self.assertEqual(row["partial_origin"], "none")

        summary_text = (run / "summary.md").read_text(encoding="utf-8")
        # Phase 7 renamed this section; the fact it asserts is unchanged.
        self.assertIn("Expected language-family status", summary_text)
        self.assertIn("Partial-origin distribution", summary_text)
        validation = validate_run(run)
        self.assertTrue(validation["passed"], validation["failures"])

        analysis[0]["expected_language_family_status"] = "failed"
        (run / "analysis.json").write_text(
            json.dumps(analysis, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        validation = validate_run(run)
        self.assertTrue(
            any(
                "expected_language_family_status" in failure
                and "recomputed" in failure
                for failure in validation["failures"]
            ),
            validation["failures"],
        )

    def test_release_versions_are_coherent(self):
        from modules.config import (
            ARTIFACT_SCHEMA_VERSION,
            INVENTORY_SCHEMA_VERSION,
            METRIC_CONTRACT_VERSION,
            PROGRAM_VERSION,
        )

        self.assertEqual(PROGRAM_VERSION, "4.0.0")
        self.assertEqual(METRIC_CONTRACT_VERSION, "3.0.0")
        self.assertEqual(INVENTORY_SCHEMA_VERSION, "1.7.0")
        self.assertEqual(ARTIFACT_SCHEMA_VERSION, "1.12.0")
        self.assertEqual(
            AnalysisConfig.from_env().exclusion_policy_version,
            "1.5.0",
        )


if __name__ == "__main__":
    unittest.main()
