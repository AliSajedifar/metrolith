import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from modules.acquisition import (
    AcquisitionError,
    _checkout_status,
    _create_cache,
    _run_git,
    _verify_cache_identity,
    acquire_repository,
    cache_path_for_url,
)
from modules.config import AnalysisConfig
from modules.repository_input import RepositorySpec


class RepositoryAcquisitionV2Tests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self._git("init", "-b", "main", cwd=self.source)
        self._git("config", "user.email", "tests@example.com", cwd=self.source)
        self._git("config", "user.name", "Tests", cwd=self.source)
        (self.source / "app.py").write_text("value = 1\n", encoding="utf-8")
        self._git("add", "app.py", cwd=self.source)
        self._git("commit", "-m", "first", cwd=self.source)
        self.first = self._git("rev-parse", "HEAD", cwd=self.source).stdout.strip()
        self.config = AnalysisConfig.from_env(
            cache_root=self.root / "cache",
            output_root=self.root / "output",
            temporary_directory=self.root / "temporary",
            git_timeout_seconds=30,
            git_retries=3,
        )
        self.url = "https://github.com/acme/project"

    def _git(self, *args, cwd=None):
        return subprocess.run(
            ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
        )

    def _mirror(self, origin_url=None):
        cache = cache_path_for_url(self.config.cache_root, self.url)
        cache.parent.mkdir(parents=True, exist_ok=True)
        self._git("clone", "--mirror", str(self.source), str(cache))
        if origin_url:
            self._git("--git-dir", str(cache), "remote", "set-url", "origin", origin_url)
        return cache

    def _spec(self, commit=None):
        return RepositorySpec(self.url, "monolith", "Python", commit, True)

    def _second_commit(self):
        (self.source / "app.py").write_text("value = 2\n", encoding="utf-8")
        self._git("add", "app.py", cwd=self.source)
        self._git("commit", "-m", "second", cwd=self.source)
        return self._git("rev-parse", "HEAD", cwd=self.source).stdout.strip()

    def test_latest_records_exact_remote_default_branch_sha(self):
        self._mirror()
        second = self._second_commit()
        with (
            patch("modules.acquisition._verify_cache_identity"),
            patch("modules.acquisition._remote_head", return_value=("refs/heads/main", second)),
        ):
            with acquire_repository(self._spec(), self.config, "latest") as acquired:
                self.assertEqual(acquired.record.analyzed_commit_sha, second)
                self.assertEqual(acquired.record.default_branch, "main")
                self.assertTrue(acquired.record.remote_checked)
                self.assertEqual((acquired.path / "app.py").read_text(), "value = 2\n")

    def test_frozen_checks_out_exact_requested_commit(self):
        self._mirror()
        second = self._second_commit()
        with (
            patch("modules.acquisition._verify_cache_identity"),
            patch("modules.acquisition._remote_head", return_value=("refs/heads/main", second)),
        ):
            with acquire_repository(self._spec(self.first), self.config, "frozen") as acquired:
                self.assertEqual(acquired.record.analyzed_commit_sha, self.first)
                self.assertEqual((acquired.path / "app.py").read_text(), "value = 1\n")

    def test_frozen_unavailable_commit_fails_clearly(self):
        self._mirror()
        unavailable = "d" * 40
        with (
            patch("modules.acquisition._verify_cache_identity"),
            patch("modules.acquisition._remote_head", return_value=("refs/heads/main", self.first)),
        ):
            with self.assertRaises(AcquisitionError) as caught:
                with acquire_repository(self._spec(unavailable), self.config, "frozen"):
                    pass
        self.assertIn(caught.exception.error_type, {"git_error", "commit_unavailable"})

    def test_offline_records_remote_not_checked(self):
        self._mirror(origin_url=self.url)
        with acquire_repository(self._spec(), self.config, "offline") as acquired:
            self.assertFalse(acquired.record.remote_checked)
            self.assertEqual(acquired.record.fetch_method, "offline_cache")
            self.assertEqual(acquired.record.analyzed_commit_sha, self.first)

    def test_isolated_checkout_excludes_source_modifications_and_untracked_files(self):
        self._mirror()
        (self.source / "app.py").write_text("locally modified\n", encoding="utf-8")
        (self.source / "untracked.py").write_text("untracked\n", encoding="utf-8")
        checkout_path = None
        with (
            patch("modules.acquisition._verify_cache_identity"),
            patch("modules.acquisition._remote_head", return_value=("refs/heads/main", self.first)),
        ):
            with acquire_repository(self._spec(), self.config, "latest") as acquired:
                checkout_path = acquired.path
                acquisition_record = acquired.record
                self.assertEqual((acquired.path / "app.py").read_text(), "value = 1\n")
                self.assertFalse((acquired.path / "untracked.py").exists())
        self.assertFalse(checkout_path.exists())
        self.assertEqual(acquisition_record.cleanup_status, "complete")
        self.assertEqual(acquisition_record.cleanup_errors, [])
        self.assertEqual(acquisition_record.post_analysis_checkout_status, "clean")

    def test_checkout_modified_during_analysis_fails_the_post_analysis_clean_check(self):
        self._mirror(origin_url=self.url)
        with self.assertRaises(AcquisitionError) as caught:
            with acquire_repository(self._spec(self.first), self.config, "offline") as acquired:
                (acquired.path / "app.py").write_text("changed during analysis\n", encoding="utf-8")
        self.assertEqual(caught.exception.error_type, "checkout_not_clean")
        self.assertIn("after analysis", str(caught.exception))
        self.assertEqual(caught.exception.diagnostics["dirty_files"][0]["path"], "app.py")

    def test_checkout_status_content_verifies_a_clean_porcelain_result(self):
        refresh_found_change = AcquisitionError(
            "git_error", "file needs update", returncode=1
        )
        clean_porcelain = subprocess.CompletedProcess(
            ["git", "status"], 0, stdout="", stderr=""
        )
        read_tree = subprocess.CompletedProcess(
            ["git", "read-tree"], 0, stdout="", stderr=""
        )
        content_difference = subprocess.CompletedProcess(
            ["git", "diff-files"], 0, stdout="media.bin\0", stderr=""
        )
        with patch(
            "modules.acquisition._run_git",
            side_effect=[
                clean_porcelain,
                read_tree,
                refresh_found_change,
                content_difference,
            ],
        ) as run:
            status = _checkout_status(self.source, self.config)
        self.assertEqual(status, " M media.bin")
        self.assertIn("--porcelain=v1", run.call_args_list[0].args[0])
        self.assertIn("read-tree", run.call_args_list[1].args[0])
        self.assertIn("--refresh", run.call_args_list[2].args[0])
        self.assertIn("diff-files", run.call_args_list[3].args[0])

    def test_noncanonical_attribute_control_is_deterministically_dirty(self):
        media = self.source / "docs" / "BDD_video.mp4"
        media.parent.mkdir()
        media.write_bytes(b"binary\x00payload\r\nwith\r\nlines")
        self._git("add", "docs/BDD_video.mp4", cwd=self.source)
        self._git("commit", "-m", "add binary before attributes", cwd=self.source)
        (self.source / ".gitattributes").write_text("* text eol=lf\n", encoding="utf-8")
        self._git("add", ".gitattributes", cwd=self.source)
        self._git("commit", "-m", "mark existing binary as text", cwd=self.source)
        head = self._git("rev-parse", "HEAD", cwd=self.source).stdout.strip()
        self._mirror(origin_url=self.url)

        with self.assertRaises(AcquisitionError) as caught:
            with acquire_repository(self._spec(head), self.config, "offline"):
                pass
        self.assertEqual(caught.exception.error_type, "checkout_not_clean")
        dirty = caught.exception.diagnostics["dirty_files"][0]
        self.assertEqual(dirty["path"], "docs/BDD_video.mp4")
        self.assertTrue(dirty["line_ending_normalization_suspected"])

    def test_worktree_cleanup_failure_is_preserved_in_acquisition_record(self):
        self._mirror()
        original_run_git = _run_git

        def fail_worktree_remove(args, config, cwd=None, timeout=None, **kwargs):
            if "worktree" in args and "remove" in args:
                raise AcquisitionError("git_error", "simulated locked worktree")
            return original_run_git(args, config, cwd=cwd, timeout=timeout, **kwargs)

        with (
            patch("modules.acquisition._verify_cache_identity"),
            patch("modules.acquisition._remote_head", return_value=("refs/heads/main", self.first)),
            patch("modules.acquisition._run_git", side_effect=fail_worktree_remove),
        ):
            with acquire_repository(self._spec(), self.config, "latest") as acquired:
                acquisition_record = acquired.record

        self.assertEqual(acquisition_record.cleanup_status, "failed")
        self.assertTrue(any("simulated locked worktree" in item for item in acquisition_record.cleanup_errors))
        self.assertFalse(any(self.config.temporary_directory.iterdir()))

    def test_cache_identity_is_tied_to_canonical_url(self):
        other = "https://github.com/acme/other"
        self.assertNotEqual(
            cache_path_for_url(self.config.cache_root, self.url),
            cache_path_for_url(self.config.cache_root, other),
        )
        cache = self._mirror(origin_url=self.url)
        with self.assertRaises(AcquisitionError) as caught:
            _verify_cache_identity(cache, other, self.config)
        self.assertEqual(caught.exception.error_type, "cache_identity_mismatch")

    def test_offline_without_cache_fails_without_remote_contact(self):
        with patch("modules.acquisition._remote_head") as remote:
            with self.assertRaises(AcquisitionError) as caught:
                with acquire_repository(self._spec(), self.config, "offline"):
                    pass
        remote.assert_not_called()
        self.assertEqual(caught.exception.error_type, "cache_unavailable")

    def test_git_timeout_is_classified_and_keeps_diagnostics(self):
        with patch(
            "modules.acquisition.subprocess.run",
            side_effect=subprocess.TimeoutExpired(["git", "fetch"], 1, output="partial", stderr="slow"),
        ):
            with self.assertRaises(AcquisitionError) as caught:
                _run_git(["fetch"], self.config, timeout=1)
        self.assertEqual(caught.exception.error_type, "git_timeout")
        self.assertIn("timed out", str(caught.exception))

    def test_new_cache_is_bare_init_not_mirror_clone(self):
        cache = cache_path_for_url(self.config.cache_root, self.url)
        with patch("modules.acquisition._run_git", wraps=_run_git) as run_git:
            status = _create_cache(cache, self.url, self.config)
        commands = [call.args[0] for call in run_git.call_args_list]
        self.assertEqual(status, "created")
        self.assertTrue(cache.is_dir())
        self.assertTrue(any(command[:2] == ["init", "--bare"] for command in commands))
        self.assertFalse(any(command[:2] == ["clone", "--mirror"] for command in commands))
        self.assertEqual(
            self._git("--git-dir", str(cache), "remote", "get-url", "origin").stdout.strip(),
            self.url,
        )

    def test_latest_fetch_is_shallow_targeted_and_omits_tags(self):
        self._mirror()
        second = self._second_commit()
        commands = []
        original = _run_git

        def capture(args, config, cwd=None, timeout=None, **kwargs):
            commands.append(args)
            return original(args, config, cwd=cwd, timeout=timeout, **kwargs)

        with (
            patch("modules.acquisition._verify_cache_identity"),
            patch("modules.acquisition._remote_head", return_value=("refs/heads/main", second)),
            patch("modules.acquisition._run_git", side_effect=capture),
        ):
            with acquire_repository(self._spec(), self.config, "latest"):
                pass
        fetch = next(command for command in commands if "fetch" in command)
        self.assertIn("--depth", fetch)
        self.assertIn("1", fetch)
        self.assertIn("--no-tags", fetch)
        self.assertIn("+refs/heads/main:refs/heads/main", fetch)
        self.assertFalse(any("refs/heads/*" in part or "refs/tags/*" in part for part in fetch))

    def test_frozen_cached_commit_does_not_contact_remote(self):
        self._mirror(origin_url=self.url)
        with patch("modules.acquisition._remote_head") as remote:
            with acquire_repository(self._spec(self.first), self.config, "frozen") as acquired:
                self.assertEqual(acquired.record.analyzed_commit_sha, self.first)
                self.assertFalse(acquired.record.remote_checked)
        remote.assert_not_called()

    def test_transient_network_failure_retries_with_bounded_backoff(self):
        failure = subprocess.CalledProcessError(
            128, ["git", "fetch"], stderr="fatal: early EOF\nremote end hung up unexpectedly"
        )
        success = subprocess.CompletedProcess(["git", "fetch"], 0, "ok", "")
        with (
            patch("modules.acquisition.subprocess.run", side_effect=[failure, success]) as run,
            patch("modules.acquisition.time.sleep") as sleep,
        ):
            result = _run_git(["fetch", "origin"], self.config, retry=True)
        self.assertEqual(result.stdout, "ok")
        self.assertEqual(run.call_count, 2)
        sleep.assert_called_once_with(5)

    def test_repository_not_found_is_not_retried(self):
        failure = subprocess.CalledProcessError(
            128, ["git", "fetch"], stderr="remote: Repository not found.\nfatal: repository not found"
        )
        with patch("modules.acquisition.subprocess.run", side_effect=failure) as run:
            with self.assertRaises(AcquisitionError) as caught:
                _run_git(["fetch", "origin"], self.config, retry=True)
        self.assertEqual(caught.exception.error_type, "repository_not_found")
        self.assertEqual(run.call_count, 1)

    def test_dirty_checkout_error_has_structured_path_and_attribute_diagnostics(self):
        media = self.source / "docs" / "BDD_video.mp4"
        media.parent.mkdir()
        media.write_bytes(b"original")
        (self.source / ".gitattributes").write_text("*.mp4 -text\n", encoding="utf-8")
        self._git("add", ".gitattributes", "docs/BDD_video.mp4", cwd=self.source)
        self._git("commit", "-m", "media", cwd=self.source)
        head = self._git("rev-parse", "HEAD", cwd=self.source).stdout.strip()
        self._mirror()
        original_run_git = _run_git

        def make_checkout_dirty(args, config, cwd=None, timeout=None, **kwargs):
            if "status" in args and "--porcelain=v1" in args:
                checkout = Path(args[args.index("-C") + 1])
                (checkout / "docs" / "BDD_video.mp4").write_bytes(b"changed")
            return original_run_git(args, config, cwd=cwd, timeout=timeout, **kwargs)

        with (
            patch("modules.acquisition._verify_cache_identity"),
            patch("modules.acquisition._remote_head", return_value=("refs/heads/main", head)),
            patch("modules.acquisition._run_git", side_effect=make_checkout_dirty),
        ):
            with self.assertRaises(AcquisitionError) as caught:
                with acquire_repository(self._spec(), self.config, "latest"):
                    pass
        self.assertEqual(caught.exception.error_type, "checkout_not_clean")
        dirty = caught.exception.diagnostics["dirty_files"][0]
        self.assertEqual(dirty["path"], "docs/BDD_video.mp4")
        self.assertEqual(dirty["git_status_code"], " M")
        self.assertEqual(dirty["extension"], ".mp4")
        self.assertFalse(dirty["supported_source_file"])
        self.assertEqual(dirty["gitattributes"]["text"], "unset")
        self.assertFalse(dirty["line_ending_normalization_suspected"])
