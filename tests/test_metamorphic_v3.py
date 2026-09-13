import json
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from modules.acquisition import AcquiredRepository, AcquisitionRecord
from modules.benchmark_runner import run_benchmark
from modules.config import AnalysisConfig
from modules.core_metrics import compute_repository_metrics
from modules.inventory import RepositoryInventory


CORE_FIELDS = (
    "lines_of_code",
    "source_files",
    "classes_structs",
    "methods_functions",
)


class SourceMetamorphicTests(unittest.TestCase):
    def _aggregate(self, files):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative, content in files.items():
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8", newline="")
            inventory = RepositoryInventory(root, full_inventory=False)
            aggregate = compute_repository_metrics(inventory)["aggregate"]
            return {field: aggregate[field] for field in CORE_FIELDS}

    def test_adding_blank_lines_does_not_change_loc(self):
        base = "class A {\n  void run() {}\n}\n"
        changed = "\nclass A {\n\n  void run() {}\n}\n\n"
        self.assertEqual(self._aggregate({"A.java": base}), self._aggregate({"A.java": changed}))

    def test_adding_comment_only_lines_does_not_change_loc(self):
        base = "class A {\n  run() {}\n}\n"
        changed = "// header\nclass A {\n  /* docs */\n  run() {}\n}\n"
        self.assertEqual(self._aggregate({"a.js": base}), self._aggregate({"a.js": changed}))

    def test_adding_inline_comments_does_not_increase_loc(self):
        base = "package p\ntype A struct{}\nfunc Run() {}\n"
        changed = "package p // package\ntype A struct{} // type\nfunc Run() {} // function\n"
        self.assertEqual(self._aggregate({"a.go": base}), self._aggregate({"a.go": changed}))

    def test_lf_and_crlf_produce_identical_metrics(self):
        source = "class A {\n  void run() {}\n}\n"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "lf").mkdir()
            (root / "crlf").mkdir()
            (root / "lf" / "A.java").write_bytes(source.encode("utf-8"))
            (root / "crlf" / "A.java").write_bytes(
                source.replace("\n", "\r\n").encode("utf-8")
            )
            results = []
            for child in (root / "lf", root / "crlf"):
                aggregate = compute_repository_metrics(
                    RepositoryInventory(child, full_inventory=False)
                )["aggregate"]
                results.append({field: aggregate[field] for field in CORE_FIELDS})
            self.assertEqual(results[0], results[1])

    def test_renaming_method_does_not_change_method_count(self):
        first = "class A { void alpha() {} }\n"
        second = "class A { void AName() {} }\n"
        self.assertEqual(self._aggregate({"A.java": first}), self._aggregate({"A.java": second}))

    def test_renaming_class_does_not_change_class_count(self):
        first = "class Alpha { void run() {} }\n"
        second = "class Beta { void run() {} }\n"
        self.assertEqual(self._aggregate({"A.java": first}), self._aggregate({"A.java": second}))

    def test_adding_constructor_does_not_increase_methods_functions(self):
        cases = (
            ("A.java", "class A { void run() {} }\n", "class A { A() {} void run() {} }\n"),
            ("a.js", "class A { run() {} }\n", "class A { constructor() {} run() {} }\n"),
            ("a.py", "class A:\n    def run(self): return 1\n", "class A:\n    def __init__(self): pass\n    def run(self): return 1\n"),
        )
        for filename, base, changed in cases:
            with self.subTest(filename=filename):
                before = self._aggregate({filename: base})
                after = self._aggregate({filename: changed})
                self.assertEqual(before["methods_functions"], after["methods_functions"])

    def test_adding_lambda_or_callback_does_not_increase_methods_functions(self):
        cases = (
            ("a.js", "function run() {}\n", "function run() {}\nitems.map(x => x);\n"),
            ("a.py", "def run(): return 1\n", "def run(): return 1\ncallback = lambda x: x\n"),
        )
        for filename, base, changed in cases:
            with self.subTest(filename=filename):
                before = self._aggregate({filename: base})
                after = self._aggregate({filename: changed})
                self.assertEqual(before["methods_functions"], after["methods_functions"])

    def test_adding_interface_does_not_increase_classes_structs(self):
        cases = (
            ("A.java", "class A {}\n", "class A {}\ninterface Port {}\n"),
            ("a.ts", "class A {}\n", "class A {}\ninterface Port {}\n"),
            ("a.go", "package p\ntype A struct{}\n", "package p\ntype A struct{}\ntype Port interface{}\n"),
        )
        for filename, base, changed in cases:
            with self.subTest(filename=filename):
                before = self._aggregate({filename: base})
                after = self._aggregate({filename: changed})
                self.assertEqual(before["classes_structs"], after["classes_structs"])

    def test_adding_anonymous_class_does_not_increase_main_classes(self):
        cases = (
            ("A.java", "class A {}\n", "class A { Object x = new Object() {}; }\n"),
            ("a.js", "class A {}\n", "class A {}\nconsume(class { ignored() {} });\n"),
        )
        for filename, base, changed in cases:
            with self.subTest(filename=filename):
                before = self._aggregate({filename: base})
                after = self._aggregate({filename: changed})
                self.assertEqual(before["classes_structs"], after["classes_structs"])

    def test_adding_test_file_does_not_change_production_metrics(self):
        base = {"src/app.py": "def run(): return 1\n"}
        changed = {**base, "tests/test_app.py": "class TestOnly:\n    def test_run(self): pass\n"}
        self.assertEqual(self._aggregate(base), self._aggregate(changed))

    def test_adding_dependency_or_build_sources_does_not_change_metrics(self):
        base = {"src/app.py": "def run(): return 1\n"}
        changed = {
            **base,
            "node_modules/pkg/a.js": "class Dependency {}\n",
            "build/A.java": "class Built {}\n",
            "target/a.go": "package p\ntype Built struct{}\n",
        }
        self.assertEqual(self._aggregate(base), self._aggregate(changed))

    def test_moving_source_between_production_directories_preserves_metrics(self):
        content = "class App:\n    def run(self): return 1\n"
        self.assertEqual(
            self._aggregate({"src/app.py": content}),
            self._aggregate({"application/core/app.py": content}),
        )


class RunMetamorphicTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repo = self.root / "repository"
        self.repo.mkdir()
        (self.repo / "app.py").write_text(
            "class App:\n    def run(self): return 1\n", encoding="utf-8"
        )

    @contextmanager
    def _acquire(self, spec, config, mode="latest", progress=None):
        del config
        if progress:
            progress("creating detached worktree")
        sha = "a" * 40 if spec.repository_name == "alpha" else "b" * 40
        yield AcquiredRepository(
            self.repo,
            AcquisitionRecord(
                repository_url=spec.url,
                repository_owner=spec.owner,
                repository_name=spec.repository_name,
                requested_commit_sha=spec.commit_sha,
                analyzed_commit_sha=sha,
                resolved_ref=spec.commit_sha or "refs/heads/main",
                default_branch="main",
                acquisition_mode=mode,
                cache_status="reused",
                remote_checked=mode == "latest",
                fetch_timestamp=None,
                checkout_timestamp="2026-08-03T00:00:00Z",
                commit_verification_status="verified",
                fetch_method="offline_cache" if mode == "offline" else "git_cache",
                cache_hit=True,
                cached_commit_available=True,
            ),
        )

    def _write_input(self, name, rows):
        path = self.root / name
        path.write_text(
            "url,architecture_type,expected_language,commit_sha,enabled,notes\n"
            + "".join(rows),
            encoding="utf-8",
        )
        return path

    def _run(self, name, input_path, mode):
        config = AnalysisConfig.from_env(
            output_root=self.root / name,
            cache_root=self.root / "cache",
            temporary_directory=self.root / "temp",
            workers=1,
            log_level="WARNING",
        )
        with patch("modules.benchmark_runner.acquire_repository", self._acquire):
            summary = run_benchmark(
                [input_path], config, mode, execution_mode="metrics"
            )
        analysis = json.loads(
            (Path(summary["run_directory"]) / "analysis.json").read_text(encoding="utf-8")
        )
        return {
            result["repository_url"]: {
                "sha": result["acquisition"]["analyzed_commit_sha"],
                "metrics": result["metrics"],
                "included_source_manifest": result["included_source_manifest"],
                "core_metric_status": result["core_metric_status"],
            }
            for result in analysis
        }

    def test_latest_frozen_and_offline_at_same_sha_preserve_semantics(self):
        row = (
            "https://github.com/acme/alpha,monolith,Python,"
            + "a" * 40
            + ",true,fixture\n"
        )
        input_path = self._write_input("same-sha.csv", [row])
        latest = self._run("latest", input_path, "latest")
        frozen = self._run("frozen", input_path, "frozen")
        offline = self._run("offline", input_path, "offline")
        self.assertEqual(latest, frozen)
        self.assertEqual(frozen, offline)

    def test_reordering_input_rows_does_not_change_repository_semantics(self):
        alpha = "https://github.com/acme/alpha,monolith,Python,,true,alpha\n"
        beta = "https://github.com/acme/beta,microservices,Python,,true,beta\n"
        first = self._write_input("first.csv", [alpha, beta])
        second = self._write_input("second.csv", [beta, alpha])
        self.assertEqual(
            self._run("ordered", first, "latest"),
            self._run("reordered", second, "latest"),
        )

    def test_repeated_run_preserves_values_statuses_paths_and_hashes(self):
        row = "https://github.com/acme/alpha,monolith,Python,,true,fixture\n"
        input_path = self._write_input("repeat.csv", [row])
        self.assertEqual(
            self._run("repeat-one", input_path, "latest"),
            self._run("repeat-two", input_path, "latest"),
        )


if __name__ == "__main__":
    unittest.main()
