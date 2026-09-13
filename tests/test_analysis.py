import subprocess
import tempfile
import unittest
from pathlib import Path
from urllib.error import URLError
from unittest.mock import patch

from modules.coverage import compute_coverage
from modules.db_analysis import detect_db_schema
from modules.deployability import assess_deployability
from modules.endpoints import extract_endpoints
from modules.metadata import fetch_metadata, parse_github_repo
from modules.static_analysis import perform_static_analysis


class RepositoryFixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.repo = Path(self.temporary.name)
        (self.repo / "src").mkdir()
        (self.repo / "tests").mkdir()
        (self.repo / "node_modules").mkdir()

        (self.repo / "src" / "app.py").write_text(
            "from flask import Flask\n"
            "app = Flask(__name__)\n"
            "class User:\n"
            "    pass\n"
            '@app.route("/users", methods=["GET", "POST"])\n'
            "def users():\n"
            "    return []\n",
            encoding="utf-8",
        )
        (self.repo / "src" / "main.go").write_text(
            "package main\n"
            "type Thing struct {}\n"
            "func Run() {}\n",
            encoding="utf-8",
        )
        (self.repo / "tests" / "test_app.py").write_text(
            "def test_one():\n    pass\n",
            encoding="utf-8",
        )
        (self.repo / "node_modules" / "ignored.js").write_text(
            "class Ignored {}\nfunction ignored() {}\n",
            encoding="utf-8",
        )
        (self.repo / "compose.yml").write_text(
            "services:\n  db:\n    image: postgres:16\n",
            encoding="utf-8",
        )
        (self.repo / "Dockerfile").write_text("FROM python:3.12\n", encoding="utf-8")

    def tearDown(self):
        self.temporary.cleanup()

    def test_static_analysis_excludes_tests_and_dependencies(self):
        result = perform_static_analysis(self.repo)
        self.assertEqual(result["source_files"], 2)
        self.assertEqual(result["classes"], 2)
        self.assertEqual(result["methods"], 2)
        self.assertEqual(result["primary_language"], "Python")
        self.assertEqual(set(result["language_breakdown"]), {"Go", "Python"})

    def test_endpoints_expand_flask_methods_and_include_provenance(self):
        endpoints = extract_endpoints(self.repo)
        self.assertEqual(
            endpoints,
            [
                {"method": "GET", "route": "/users", "file": "src/app.py"},
                {"method": "POST", "route": "/users", "file": "src/app.py"},
            ],
        )

    def test_spring_class_prefix_is_combined(self):
        java = self.repo / "src" / "UsersController.java"
        java.write_text(
            '@RequestMapping("/api")\n'
            "public class UsersController {\n"
            '  @GetMapping("/users")\n'
            "  public Object users() { return null; }\n"
            "}\n",
            encoding="utf-8",
        )
        endpoints = extract_endpoints(self.repo)
        self.assertIn(
            {
                "method": "GET",
                "route": "/api/users",
                "file": "src/UsersController.java",
            },
            endpoints,
        )
        self.assertNotIn(
            {
                "method": "ANY",
                "route": "/api",
                "file": "src/UsersController.java",
            },
            endpoints,
        )

    def test_structural_coverage_is_bounded(self):
        result = compute_coverage(self.repo)
        self.assertEqual(result["test_file_count"], 1)
        self.assertEqual(result["test_cases"], 1)
        self.assertEqual(result["source_files"], 2)
        self.assertEqual(result["estimated_coverage_ratio"], 0.5)
        self.assertLessEqual(result["estimated_coverage_ratio"], 1.0)

    def test_deployment_and_database_detection(self):
        self.assertTrue(assess_deployability(self.repo)["dockerfile"])
        db = detect_db_schema(self.repo)
        self.assertEqual(db["db_type"], "postgres")
        self.assertEqual(db["docker_compose_detected_dbs"], ["postgres"])


class MetadataTests(unittest.TestCase):
    def test_parse_common_github_urls(self):
        self.assertEqual(
            parse_github_repo("https://github.com/example/project.git"),
            ("example", "project"),
        )
        self.assertEqual(
            parse_github_repo("git@github.com:example/project.git"),
            ("example", "project"),
        )

    def test_reject_non_github_and_nested_urls(self):
        with self.assertRaises(ValueError):
            parse_github_repo("https://gitlab.com/example/project")
        with self.assertRaises(ValueError):
            parse_github_repo("https://github.com/example/project/issues")

    def test_curl_fallback_has_a_timeout_and_reports_expiry(self):
        with (
            patch("modules.metadata.urlopen", side_effect=URLError("offline")),
            patch("modules.metadata.shutil.which", return_value="curl"),
            patch(
                "modules.metadata.subprocess.run",
                side_effect=subprocess.TimeoutExpired(["curl"], 10),
            ) as run,
        ):
            result = fetch_metadata("https://github.com/example/project")
        self.assertIn("timed out after 10s", result["error"])
        self.assertEqual(run.call_args.kwargs["timeout"], 10)


if __name__ == "__main__":
    unittest.main()
