import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from modules import clone_repo


class FetchReliabilityTests(unittest.TestCase):
    def test_archive_manifest_detects_same_size_content_change(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            source = repo / "app.py"
            source.write_text("aaa", encoding="utf-8")
            files = clone_repo._project_files(repo)
            marker = {
                "file_count": len(files),
                "manifest_sha256": clone_repo._file_manifest(repo, files),
            }
            (repo / clone_repo.ARCHIVE_MARKER).write_text(
                json.dumps(marker), encoding="utf-8"
            )
            source.write_text("bbb", encoding="utf-8")
            validation = clone_repo.validate_repository(repo)
            self.assertFalse(validation["valid"])
            self.assertIn("manifest", validation["message"].lower())

    def test_partial_clone_is_removed_before_archive_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "r_deadbeef0000"
            clone_staging = None

            def failed_clone(url, path, git):
                nonlocal clone_staging
                clone_staging = path
                path.mkdir()
                (path / "partial.txt").write_text("partial", encoding="utf-8")
                raise clone_repo.FetchFailure("clone_error", "checkout failed")

            def failed_archive(url, path, default_branch=None):
                self.assertEqual(path, clone_staging)
                self.assertFalse(path.exists())
                raise clone_repo.FetchFailure("archive_error", "download failed")

            with (
                patch.object(clone_repo.shutil, "which", return_value="git"),
                patch.object(clone_repo, "_clone_with_git", side_effect=failed_clone),
                patch.object(
                    clone_repo, "_download_archive", side_effect=failed_archive
                ),
            ):
                result = clone_repo.clone_repository(
                    "https://github.com/example/project", destination
                )

            self.assertEqual(result["fetch_status"], "failed")
            self.assertFalse(destination.exists())
            self.assertIsNotNone(clone_staging)
            self.assertFalse(clone_staging.parent.exists())

    def test_existing_invalid_cache_is_deleted_and_refetched(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "r_deadbeef0000"
            destination.mkdir()
            (destination / "partial.txt").write_text("partial", encoding="utf-8")

            def successful_clone(url, staging, git):
                self.assertFalse(destination.exists())
                staging.mkdir()
                (staging / "complete.txt").write_text("complete", encoding="utf-8")

            def validate(path, git=None):
                valid = (Path(path) / "complete.txt").is_file()
                return {
                    "valid": valid,
                    "repository_kind": "git" if valid else "unknown",
                    "error_type": "none" if valid else "incomplete_repository",
                    "message": "" if valid else "completion file is missing",
                }

            with (
                patch.object(clone_repo.shutil, "which", return_value="git"),
                patch.object(
                    clone_repo, "_clone_with_git", side_effect=successful_clone
                ),
                patch.object(
                    clone_repo,
                    "validate_repository",
                    side_effect=validate,
                ),
            ):
                result = clone_repo.clone_repository(
                    "https://github.com/example/project", destination
                )

            self.assertEqual(result["fetch_status"], "success")
            self.assertEqual(result["fetch_method"], "git_clone")
            self.assertFalse((destination / "partial.txt").exists())
            self.assertTrue((destination / "complete.txt").is_file())

    def test_invalid_non_zip_archive_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "repository.zip"
            extraction = Path(directory) / "extracted"
            archive.write_text("<html>rate limited</html>", encoding="utf-8")
            extraction.mkdir()

            with self.assertRaises(clone_repo.FetchFailure) as context:
                clone_repo._extract_validated_archive(archive, extraction)

            self.assertEqual(context.exception.error_type, "archive_error")
            self.assertIn("not a valid ZIP", context.exception.message)

    def test_archive_fallback_rejects_downloaded_html_and_cleans_up(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "repo"

            def failed_clone(url, path, git):
                path.mkdir()
                raise clone_repo.FetchFailure("clone_error", "checkout failed")

            def html_download(urls, archive_path, attempts=3):
                archive_path.write_text("<html>rate limited</html>", encoding="utf-8")
                return urls[0]

            with (
                patch.object(clone_repo.shutil, "which", return_value="git"),
                patch.object(clone_repo, "_clone_with_git", side_effect=failed_clone),
                patch.object(
                    clone_repo,
                    "_download_with_retries",
                    side_effect=html_download,
                ),
            ):
                result = clone_repo.clone_repository(
                    "https://github.com/example/project",
                    destination,
                    default_branch="main",
                )

            self.assertEqual(result["fetch_status"], "failed")
            self.assertEqual(result["fetch_error_type"], "archive_error")
            self.assertIn("not a valid ZIP", result["fetch_error_message"])
            self.assertFalse(destination.exists())
            self.assertFalse(list(destination.parent.glob("*.fetching")))
            self.assertFalse(list(destination.parent.glob("*.fetching.archive")))

    def test_successful_clone_with_failed_checkout_validation_is_not_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            staging = Path(directory) / "repo.fetching"
            with (
                patch.object(clone_repo.subprocess, "run") as run,
                patch.object(
                    clone_repo,
                    "validate_repository",
                    return_value={
                        "valid": False,
                        "repository_kind": "git",
                        "error_type": "incomplete_repository",
                        "message": "tracked working-tree files are missing",
                    },
                ),
            ):
                run.return_value = subprocess.CompletedProcess([], 0, "", "")
                with self.assertRaises(clone_repo.FetchFailure) as context:
                    clone_repo._clone_with_git(
                        "https://github.com/example/project", staging, "git"
                    )
            self.assertEqual(
                context.exception.error_type, "incomplete_repository"
            )

    def test_windows_long_path_error_is_classified(self):
        message = "error: unable to create file src/deep/file: Filename too long"
        self.assertEqual(
            clone_repo.detect_fetch_error_type(message, "clone_error"),
            "long_path_error",
        )

        error = OSError("path failure")
        error.winerror = 206
        self.assertEqual(
            clone_repo.detect_fetch_error_type(error, "archive_error"),
            "long_path_error",
        )

    def test_archive_cache_requires_exact_completion_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / "README.md").write_text("project", encoding="utf-8")
            (repo / clone_repo.ARCHIVE_MARKER).write_text(
                json.dumps({"file_count": 2}), encoding="utf-8"
            )
            validation = clone_repo.validate_repository(repo)
            self.assertFalse(validation["valid"])
            self.assertEqual(
                validation["error_type"], "incomplete_repository"
            )

    def test_valid_archive_cache_is_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory) / "repo"
            repo.mkdir()
            (repo / "app.py").write_text("print('ok')\n", encoding="utf-8")
            files = clone_repo._project_files(repo)
            marker = {
                "file_count": len(files),
                "manifest_sha256": clone_repo._file_manifest(repo, files),
            }
            (repo / clone_repo.ARCHIVE_MARKER).write_text(
                json.dumps(marker), encoding="utf-8"
            )
            result = clone_repo.clone_repository(
                "https://github.com/example/project", repo
            )
            self.assertEqual(result["fetch_status"], "success")
            self.assertEqual(result["fetch_method"], "existing_valid_cache")

    def test_partial_git_marker_is_rejected_without_running_git(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / ".git").mkdir()
            (repo / ".git" / "index.lock").write_text("", encoding="utf-8")
            validation = clone_repo.validate_repository(
                repo, git_executable="git"
            )
            self.assertFalse(validation["valid"])
            self.assertIn("index.lock", validation["message"])

    def test_cleanup_failure_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "repo"
            destination.mkdir()
            original_remove = clone_repo._remove_path

            def fail_destination_cleanup(path):
                if Path(path) == destination:
                    raise clone_repo.FetchFailure(
                        "cleanup_error", "directory is locked"
                    )
                return original_remove(path)

            with patch.object(
                clone_repo, "_remove_path", side_effect=fail_destination_cleanup
            ):
                result = clone_repo.clone_repository(
                    "https://github.com/example/project", destination
                )
            self.assertEqual(result["fetch_status"], "failed")
            self.assertEqual(result["fetch_error_type"], "cleanup_error")

    def test_git_clone_command_enables_long_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            staging = Path(directory) / "repo.fetching"
            with (
                patch.object(clone_repo.subprocess, "run") as run,
                patch.object(
                    clone_repo,
                    "validate_repository",
                    return_value={
                        "valid": True,
                        "repository_kind": "git",
                        "error_type": "none",
                        "message": "",
                    },
                ),
            ):
                run.return_value = subprocess.CompletedProcess([], 0, "", "")
                clone_repo._clone_with_git(
                    "https://github.com/example/project", staging, "git"
                )
            command = run.call_args.args[0]
            self.assertEqual(command[:4], ["git", "-c", "core.longpaths=true", "clone"])

    def test_stale_fetching_directories_are_cleaned_at_startup(self):
        with tempfile.TemporaryDirectory() as directory:
            raw_dir = Path(directory)
            stale = raw_dir / "r_deadbeef.fetching"
            stale.mkdir()
            (stale / ".git").mkdir()

            warnings = clone_repo.cleanup_stale_staging_directories(raw_dir)

            self.assertEqual(warnings, [])
            self.assertFalse(stale.exists())

    def test_stale_staging_cleanup_failure_is_a_warning(self):
        with tempfile.TemporaryDirectory() as directory:
            raw_dir = Path(directory)
            stale = raw_dir / "r_deadbeef.fetching"
            stale.mkdir()

            with patch.object(
                clone_repo,
                "_remove_path",
                side_effect=clone_repo.FetchFailure(
                    "staging_cleanup_error", "directory is locked"
                ),
            ):
                warnings = clone_repo.cleanup_stale_staging_directories(raw_dir)

            self.assertEqual(warnings, ["directory is locked"])
            self.assertTrue(stale.exists())

    def test_stale_cleanup_does_not_delete_regular_tmp_files(self):
        with tempfile.TemporaryDirectory() as directory:
            raw_dir = Path(directory)
            regular_file = raw_dir / "notes.tmp"
            regular_file.write_text("keep", encoding="utf-8")

            warnings = clone_repo.cleanup_stale_staging_directories(raw_dir)

            self.assertEqual(warnings, [])
            self.assertTrue(regular_file.is_file())

    def test_configured_temp_directory_is_created_automatically(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            destination = root / "raw" / "r_deadbeef"
            temp_parent = root / "short" / "temp"
            seen_staging = []

            def successful_archive(url, staging, default_branch=None):
                seen_staging.append(Path(staging))
                staging.mkdir()
                (staging / "app.go").write_text(
                    "package main\nfunc main() {}\n", encoding="utf-8"
                )

            valid = {
                "valid": True,
                "repository_kind": "archive",
                "error_type": "none",
                "message": "",
            }
            with (
                patch.dict(
                    os.environ,
                    {"ARCHLENS_TEMP_DIR": str(temp_parent)},
                    clear=False,
                ),
                patch.object(clone_repo.shutil, "which", return_value=None),
                patch.object(
                    clone_repo, "_download_archive", side_effect=successful_archive
                ),
                patch.object(clone_repo, "validate_repository", return_value=valid),
            ):
                result = clone_repo.clone_repository(
                    "https://github.com/example/project", destination
                )

            self.assertEqual(result["fetch_status"], "success")
            self.assertTrue(temp_parent.is_dir())
            self.assertEqual(len(seen_staging), 1)
            self.assertIn(temp_parent, seen_staging[0].parents)
            self.assertFalse(list(temp_parent.glob("archlens_*")))

    def test_permission_error_move_is_retried(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            staging = root / "staging"
            destination = root / "final"
            staging.mkdir()
            (staging / "app.py").write_text("print('ok')\n", encoding="utf-8")
            original_replace = Path.replace
            attempts = 0

            def locked_once(path, target):
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    error = PermissionError("locked")
                    error.winerror = 5
                    raise error
                return original_replace(path, target)

            with (
                patch.object(Path, "replace", side_effect=locked_once, autospec=True),
                patch.object(clone_repo.time, "sleep") as sleep,
                patch.object(
                    clone_repo,
                    "validate_repository",
                    return_value={
                        "valid": True,
                        "repository_kind": "git",
                        "error_type": "none",
                        "message": "",
                    },
                ),
            ):
                clone_repo._install_staging_repository(staging, destination)

            self.assertEqual(attempts, 2)
            sleep.assert_called_once()
            self.assertTrue((destination / "app.py").is_file())

    def test_locked_move_falls_back_to_copy_then_validate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            staging = root / "staging"
            destination = root / "final"
            staging.mkdir()
            (staging / "app.py").write_text("print('ok')\n", encoding="utf-8")
            validated_paths = []

            def validate(path, git=None):
                validated_paths.append(Path(path))
                return {
                    "valid": (Path(path) / "app.py").is_file(),
                    "repository_kind": "archive",
                    "error_type": "none",
                    "message": "",
                }

            locked = PermissionError("locked")
            locked.winerror = 5
            with (
                patch.object(Path, "replace", side_effect=locked),
                patch.object(clone_repo.time, "sleep"),
                patch.object(clone_repo, "validate_repository", side_effect=validate),
            ):
                clone_repo._install_staging_repository(staging, destination)

            self.assertEqual(validated_paths, [destination])
            self.assertTrue((destination / "app.py").is_file())
            self.assertFalse(staging.exists())

    def test_final_cache_is_validated_after_move(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            staging = root / "staging"
            destination = root / "final"
            staging.mkdir()
            (staging / "app.py").write_text("print('ok')\n", encoding="utf-8")
            validated_paths = []

            def validate(path, git=None):
                validated_paths.append(Path(path))
                return {
                    "valid": True,
                    "repository_kind": "archive",
                    "error_type": "none",
                    "message": "",
                }

            with patch.object(
                clone_repo, "validate_repository", side_effect=validate
            ):
                clone_repo._install_staging_repository(staging, destination)

            self.assertEqual(validated_paths, [destination])
            self.assertFalse(staging.exists())
            self.assertTrue(destination.exists())


if __name__ == "__main__":
    unittest.main()
