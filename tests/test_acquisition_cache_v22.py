import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from modules.acquisition import (
    AcquisitionError,
    _ACQUISITION_DEADLINE,
    _resolve_commit,
    _run_git,
    acquire_repository,
    cache_path_for_url,
    cleanup_stale_cache_staging,
    validate_cache_deep,
)
from modules.config import AnalysisConfig
from modules.repository_input import RepositorySpec


class PersistentCacheV22Tests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self._git("init", "-b", "main", cwd=self.source)
        self._git("config", "user.email", "tests@example.com", cwd=self.source)
        self._git("config", "user.name", "Tests", cwd=self.source)
        (self.source / "app.py").write_text("value = 1\n", encoding="utf-8")
        self._git("add", "app.py", cwd=self.source)
        self._git("commit", "-m", "first", cwd=self.source)
        self.first = self._git("rev-parse", "HEAD", cwd=self.source).stdout.strip()
        self.url = "https://github.com/acme/cache-test"
        self.spec = RepositorySpec(self.url, "monolith", "Python", None, True)
        self.config = AnalysisConfig.from_env(
            cache_root=self.root / "cache",
            output_root=self.root / "output",
            temporary_directory=self.root / "temp",
            git_timeout_seconds=30,
            git_retries=1,
            acquisition_deadline_seconds=30,
        )

    def _git(self, *args, cwd=None):
        return subprocess.run(
            ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
        )

    def _mirror(self):
        cache = cache_path_for_url(self.config.cache_root, self.url)
        cache.parent.mkdir(parents=True, exist_ok=True)
        self._git("clone", "--mirror", str(self.source), str(cache))
        self._git("--git-dir", str(cache), "remote", "set-url", "origin", self.url)
        return cache

    def test_stale_staging_is_quarantined_only_once(self):
        root = Path(self.config.cache_root)
        root.mkdir(parents=True, exist_ok=True)
        staging = root / ".r_deadbeef.git.fetching_old"
        staging.mkdir()
        already_quarantined = root / ".r_old.git.fetching_old.quarantined_12345678"
        already_quarantined.mkdir()

        count, warnings = cleanup_stale_cache_staging(root, older_than_seconds=0)
        self.assertEqual((count, warnings), (1, []))
        self.assertFalse(staging.exists())
        self.assertTrue(already_quarantined.exists())
        self.assertEqual(len(list(root.glob(f"{staging.name}.quarantined_*"))), 1)

        count, warnings = cleanup_stale_cache_staging(root, older_than_seconds=0)
        self.assertEqual((count, warnings), (0, []))

    def test_first_latest_run_promotes_populated_staging_cache_without_mirror_clone(self):
        def fetch_local(cache, branch_ref, config, **kwargs):
            del kwargs
            _run_git(
                [
                    "--git-dir", str(cache), "fetch", "--depth", "1", "--no-tags",
                    str(self.source), f"+{branch_ref}:{branch_ref}",
                ],
                config,
            )

        with (
            patch("modules.acquisition._remote_head", return_value=("refs/heads/main", self.first)),
            patch("modules.acquisition._fetch_branch", side_effect=fetch_local),
        ):
            with acquire_repository(self.spec, self.config, "latest") as acquired:
                record = acquired.record
                self.assertEqual((acquired.path / "app.py").read_text(), "value = 1\n")

        cache = cache_path_for_url(self.config.cache_root, self.url)
        self.assertTrue((cache / ".metrolith-cache-complete.json").is_file())
        self.assertEqual(_resolve_commit(cache, self.first, self.config), self.first)
        self.assertTrue(record.cache_created)
        self.assertTrue(record.fetch_performed)
        self.assertFalse(any("clone" in command and "--mirror" in command for command in record.git_commands))
        self.assertFalse(any(path.name.startswith(f".{cache.stem}.fetching_") for path in cache.parent.iterdir()))

    def test_unchanged_latest_sha_executes_no_fetch(self):
        self._mirror()
        with patch(
            "modules.acquisition._remote_head",
            return_value=("refs/heads/main", self.first),
        ):
            with acquire_repository(self.spec, self.config, "latest") as acquired:
                record = acquired.record
        self.assertTrue(record.cache_hit)
        self.assertTrue(record.cached_commit_available)
        self.assertFalse(record.fetch_performed)
        self.assertFalse(any("fetch" in command for command in record.git_commands))

    def test_changed_latest_sha_executes_one_targeted_fetch_without_reclone(self):
        self._mirror()
        (self.source / "app.py").write_text("value = 2\n", encoding="utf-8")
        self._git("add", "app.py", cwd=self.source)
        self._git("commit", "-m", "second", cwd=self.source)
        second = self._git("rev-parse", "HEAD", cwd=self.source).stdout.strip()

        def fetch_local(cache, branch_ref, config, **kwargs):
            del kwargs
            _run_git(
                [
                    "--git-dir", str(cache), "fetch", "--depth", "1", "--no-tags",
                    str(self.source), f"+{branch_ref}:{branch_ref}",
                ],
                config,
            )

        with (
            patch("modules.acquisition._remote_head", return_value=("refs/heads/main", second)),
            patch("modules.acquisition._fetch_branch", side_effect=fetch_local),
        ):
            with acquire_repository(self.spec, self.config, "latest") as acquired:
                record = acquired.record
                self.assertEqual((acquired.path / "app.py").read_text(), "value = 2\n")

        fetches = [command for command in record.git_commands if "fetch" in command]
        self.assertEqual(len(fetches), 1)
        self.assertIn("--depth", fetches[0])
        self.assertIn("--no-tags", fetches[0])
        self.assertFalse(record.cache_hit)
        self.assertTrue(record.cache_updated)
        self.assertTrue(record.fetch_performed)
        self.assertFalse(any("clone" in command for command in record.git_commands))

    def test_frozen_and_offline_cached_runs_execute_no_network_command(self):
        self._mirror()
        frozen = RepositorySpec(self.url, "monolith", "Python", self.first, True)
        records = []
        for mode in ("frozen", "offline"):
            with acquire_repository(frozen, self.config, mode) as acquired:
                records.append(acquired.record)
        for record in records:
            self.assertFalse(record.network_contacted)
            self.assertFalse(record.fetch_performed)
            self.assertFalse(
                any(
                    "fetch" in command or "ls-remote" in command
                    for command in record.git_commands
                )
            )

    def test_failed_refresh_preserves_existing_valid_cache(self):
        cache = self._mirror()
        unavailable = "d" * 40
        with (
            patch(
                "modules.acquisition._remote_head",
                return_value=("refs/heads/main", unavailable),
            ),
            patch(
                "modules.acquisition._fetch_branch",
                side_effect=AcquisitionError("network_reset", "simulated refresh failure"),
            ),
        ):
            with self.assertRaises(AcquisitionError):
                with acquire_repository(self.spec, self.config, "latest"):
                    pass
        self.assertTrue(cache.is_dir())
        self.assertEqual(_resolve_commit(cache, self.first, self.config), self.first)

    def test_failed_staging_cache_is_never_promoted(self):
        cache = cache_path_for_url(self.config.cache_root, self.url)
        with patch(
            "modules.acquisition._prepare_revision",
            side_effect=AcquisitionError("network_reset", "simulated initialization failure"),
        ):
            with self.assertRaises(AcquisitionError):
                with acquire_repository(self.spec, self.config, "latest"):
                    pass
        self.assertFalse(cache.exists())
        if cache.parent.exists():
            self.assertFalse(any(".fetching_" in path.name for path in cache.parent.iterdir()))

    def test_invalid_final_cache_is_quarantined_not_overwritten(self):
        cache = cache_path_for_url(self.config.cache_root, self.url)
        cache.mkdir(parents=True)
        (cache / "partial.txt").write_text("incomplete", encoding="utf-8")
        with patch(
            "modules.acquisition._initialize_cache_with_revision",
            side_effect=AcquisitionError("network_reset", "stop after quarantine"),
        ):
            with self.assertRaises(AcquisitionError):
                with acquire_repository(self.spec, self.config, "latest"):
                    pass
        self.assertFalse(cache.exists())
        quarantined = list(cache.parent.glob(f"{cache.name}.invalid_*"))
        self.assertEqual(len(quarantined), 1)
        self.assertEqual(
            (quarantined[0] / "partial.txt").read_text(encoding="utf-8"),
            "incomplete",
        )

    def test_expired_total_deadline_starts_no_git_process(self):
        token = _ACQUISITION_DEADLINE.set(time.monotonic() - 1)
        try:
            with patch("modules.acquisition.subprocess.run") as run:
                with self.assertRaises(AcquisitionError) as caught:
                    _run_git(["fetch", "origin"], self.config, retry=True)
            self.assertEqual(caught.exception.error_type, "acquisition_deadline_exceeded")
            run.assert_not_called()
        finally:
            _ACQUISITION_DEADLINE.reset(token)

    def test_long_git_process_emits_heartbeat_progress(self):
        self.config.heartbeat_interval_seconds = 0.01
        messages = []

        def slow_run(command, **kwargs):
            del kwargs
            time.sleep(0.04)
            return subprocess.CompletedProcess(command, 0, "", "")

        with patch("modules.acquisition.subprocess.run", side_effect=slow_run):
            _run_git(["fetch", "origin"], self.config, progress=messages.append)

        self.assertTrue(
            any("still running" in message for message in messages),
            messages,
        )

    def test_explicit_deep_cache_validation_is_available(self):
        cache = self._mirror()
        validate_cache_deep(cache, self.url, self.config)


if __name__ == "__main__":
    unittest.main()
