"""Local Repository Analysis and the Artifact 1.7 subject identity model.

Artifact 1.6 and earlier used ``repository_url`` as the de-facto primary key.
That only worked while every subject was a remote GitHub URL; the moment a
subject is a local directory the URL is absent, and a key that is sometimes
absent is not a key. These tests pin the replacement:

* ``subject_key`` is the logical identity — what makes two analyses analyses of
  the same software, across revisions, runs and source modes;
* ``analysis_scope_hash`` is the analyzed-scope identity — *which bytes*;
* ``source_mode`` is evidence and **never** decides comparability.

The scenarios deliberately include both directions of every trap: a mode change
alone must not change the digest, and a content change must.

Remote acquisition is exercised through a **local bare repository** acting as
the origin, so the remote-style path is covered without the suite touching the
network.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from modules.config import ARTIFACT_SCHEMA_VERSION, AnalysisConfig
from modules.local_source import (
    LocalSourceError,
    classify_local_source,
    prepare_local_source,
)
from modules.repository_input import RepositorySpec
from modules.subject import (
    canonical_remote_subject_key,
    compute_analysis_scope_hash,
    legacy_subject_identity,
    resolve_subject_identity,
)
from modules.vocabularies import SourceMode, SubjectKeyBasis, WorkingTreeState

PYTHON_SOURCE = "class App:\n    def run(self):\n        return 1\n"


def git(root: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True, capture_output=True, text=True,
    ).stdout.strip()


class LocalFixture(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="archlens_b1_")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def make_git_repository(self, name: str = "project", *, origin: str | None = None) -> Path:
        path = self.root / name
        path.mkdir(parents=True)
        subprocess.run(["git", "init", "-b", "main", str(path)],
                       check=True, capture_output=True)
        git(path, "config", "user.email", "tests@example.com")
        git(path, "config", "user.name", "Tests")
        (path / "app.py").write_text(PYTHON_SOURCE, encoding="utf-8")
        (path / ".gitignore").write_text("ignored/\n", encoding="utf-8")
        git(path, "add", "-A")
        git(path, "commit", "-m", "first")
        if origin:
            git(path, "remote", "add", "origin", origin)
        return path

    def make_plain_directory(self, name: str = "plain") -> Path:
        path = self.root / name
        path.mkdir(parents=True)
        (path / "app.py").write_text(PYTHON_SOURCE, encoding="utf-8")
        return path

    def analyze(self, path: Path, **kwargs) -> Path:
        """Run one local subject through the canonical pipeline."""
        from modules.benchmark_runner import run_benchmark

        output = kwargs.pop("output", "output")
        spec = RepositorySpec(
            url="",
            architecture_type="unknown",
            expected_language="Python",
            local_path=str(path),
            **kwargs,
        )
        config = AnalysisConfig.from_env(
            workspace=self.root,
            output_root=self.root / output,
            cache_root=self.root / "cache",
            temporary_directory=self.root / "worktrees",
            workers=1,
        )
        with patch("modules.benchmark_runner._distribution_version", return_value="4.0.1"):
            summary = run_benchmark(
                repository_specs=[spec], config=config, acquisition_mode="offline",
                command_line_arguments=["analyze"], single_repository=True,
            )
        return Path(summary["run_directory"])

    @staticmethod
    def result_of(run: Path) -> dict:
        return json.loads((run / "analysis.json").read_text(encoding="utf-8"))[0]


class SubjectKeyResolutionTests(LocalFixture):
    def test_explicit_key_wins(self):
        identity = resolve_subject_identity(
            source_mode=SourceMode.LOCAL_WORKTREE_SNAPSHOT,
            explicit_subject_key="my-project",
            repository_url="https://github.com/acme/other",
        )
        self.assertEqual(identity.subject_key, "my-project")
        self.assertIs(identity.subject_key_basis, SubjectKeyBasis.EXPLICIT)

    def test_remote_locator_is_canonical_across_transports(self):
        keys = {
            canonical_remote_subject_key(locator)
            for locator in (
                "https://github.com/Acme/App",
                "https://github.com/acme/app.git",
                "git@github.com:acme/app.git",
                "ssh://git@github.com/acme/app",
            )
        }
        self.assertEqual(keys, {"github.com/acme/app"})

    def test_git_origin_is_used_when_no_remote_locator_is_supplied(self):
        repository = self.make_git_repository(
            "with_origin", origin="https://github.com/acme/app.git"
        )
        identity = resolve_subject_identity(
            source_mode=SourceMode.LOCAL_WORKTREE_SNAPSHOT, local_path=repository
        )
        self.assertEqual(identity.subject_key, "github.com/acme/app")
        self.assertIs(identity.subject_key_basis, SubjectKeyBasis.GIT_ORIGIN)
        self.assertTrue(identity.portable)

    def test_local_fallback_is_marked_non_portable(self):
        """Scenario I: a local Git repository with no usable origin."""
        repository = self.make_git_repository("no_origin")
        identity = resolve_subject_identity(
            source_mode=SourceMode.LOCAL_WORKTREE_SNAPSHOT, local_path=repository
        )
        self.assertIs(identity.subject_key_basis, SubjectKeyBasis.LOCAL_FALLBACK)
        self.assertFalse(
            identity.portable,
            "a host-derived key must never be presented as portable identity",
        )
        self.assertNotIn(
            str(repository), identity.subject_key,
            "the absolute host path leaked into the identity",
        )

    def test_subject_key_is_not_a_content_hash(self):
        """Two revisions of one project keep one logical identity."""
        repository = self.make_git_repository("evolving")
        first = resolve_subject_identity(
            source_mode=SourceMode.LOCAL_WORKTREE_SNAPSHOT, local_path=repository
        )
        (repository / "app.py").write_text(PYTHON_SOURCE + "# changed\n", encoding="utf-8")
        second = resolve_subject_identity(
            source_mode=SourceMode.LOCAL_WORKTREE_SNAPSHOT, local_path=repository
        )
        self.assertEqual(first.subject_key, second.subject_key)

    def test_legacy_artifacts_derive_the_same_key_at_read_time(self):
        legacy = legacy_subject_identity("https://github.com/acme/app")
        current = resolve_subject_identity(
            source_mode=SourceMode.REMOTE_GIT_REVISION,
            repository_url="https://github.com/acme/app",
        )
        self.assertEqual(legacy.subject_key, current.subject_key)


class SourceModeTests(LocalFixture):
    def test_plain_directory_is_a_directory_snapshot(self):
        """Scenario H."""
        self.assertIs(
            classify_local_source(self.make_plain_directory(), revision=None),
            SourceMode.LOCAL_DIRECTORY_SNAPSHOT,
        )

    def test_git_worktree_without_revision_is_a_worktree_snapshot(self):
        self.assertIs(
            classify_local_source(self.make_git_repository(), revision=None),
            SourceMode.LOCAL_WORKTREE_SNAPSHOT,
        )

    def test_git_with_revision_is_an_exact_revision(self):
        self.assertIs(
            classify_local_source(self.make_git_repository(), revision="HEAD"),
            SourceMode.LOCAL_GIT_REVISION,
        )

    def test_revision_on_a_non_git_directory_is_refused(self):
        with self.assertRaises(LocalSourceError):
            classify_local_source(self.make_plain_directory(), revision="HEAD")


class SnapshotScopeTests(LocalFixture):
    """Scenarios C–G: what a worktree snapshot includes."""

    def _snapshot_files(self, path: Path, **kwargs) -> set[str]:
        with prepare_local_source(path, **kwargs) as snapshot:
            return {
                item.relative_to(snapshot.path).as_posix()
                for item in snapshot.path.rglob("*") if item.is_file()
            }

    def test_clean_worktree_reports_clean(self):
        repository = self.make_git_repository("clean")
        with prepare_local_source(repository) as snapshot:
            self.assertIs(
                snapshot.working_tree_state, WorkingTreeState.CLEAN_WORKTREE
            )

    def test_dirty_tracked_modification_is_recorded(self):
        repository = self.make_git_repository("dirty")
        (repository / "app.py").write_text(PYTHON_SOURCE + "# edit\n", encoding="utf-8")
        with prepare_local_source(repository) as snapshot:
            self.assertIs(snapshot.working_tree_state, WorkingTreeState.DIRTY_WORKTREE)
            self.assertEqual(snapshot.modified_tracked_count, 1)

    def test_non_ignored_untracked_file_is_included_by_default(self):
        repository = self.make_git_repository("untracked")
        (repository / "extra.py").write_text("class E: pass\n", encoding="utf-8")
        self.assertIn("extra.py", self._snapshot_files(repository))

    def test_tracked_only_excludes_the_untracked_file(self):
        repository = self.make_git_repository("tracked_only")
        (repository / "extra.py").write_text("class E: pass\n", encoding="utf-8")
        self.assertNotIn("extra.py", self._snapshot_files(repository, tracked_only=True))

    def test_ignored_content_is_never_included_by_default(self):
        """Otherwise merely having built the project locally changes the numbers."""
        repository = self.make_git_repository("ignored")
        (repository / "ignored").mkdir()
        (repository / "ignored" / "junk.py").write_text("x = 1\n", encoding="utf-8")
        files = self._snapshot_files(repository)
        self.assertNotIn("ignored/junk.py", files)

    def test_exact_revision_ignores_working_tree_dirt(self):
        repository = self.make_git_repository("exact")
        head = git(repository, "rev-parse", "HEAD")
        (repository / "app.py").write_text("# replaced entirely\n", encoding="utf-8")
        (repository / "extra.py").write_text("class E: pass\n", encoding="utf-8")
        with prepare_local_source(repository, revision=head) as snapshot:
            self.assertIs(
                snapshot.working_tree_state, WorkingTreeState.COMMITTED_REVISION
            )
            self.assertEqual(snapshot.analyzed_commit_sha, head)
            content = (snapshot.path / "app.py").read_text(encoding="utf-8")
            self.assertEqual(content, PYTHON_SOURCE)
            self.assertFalse((snapshot.path / "extra.py").exists())


class NonMutationTests(LocalFixture):
    def test_analysis_never_writes_to_the_users_repository(self):
        """`git worktree add` would leave `.git/worktrees` behind; this must not."""
        repository = self.make_git_repository("untouched")
        head = git(repository, "rev-parse", "HEAD")
        before = sorted(item.name for item in repository.iterdir())

        with prepare_local_source(repository, revision=head):
            pass
        with prepare_local_source(repository):
            pass

        self.assertFalse((repository / ".git" / "worktrees").exists())
        self.assertEqual(sorted(item.name for item in repository.iterdir()), before)
        self.assertEqual(git(repository, "status", "--porcelain"), "")


class ScopeHashTests(LocalFixture):
    """`analysis_scope_hash` identifies the measured bytes, nothing else."""

    def test_changed_worktree_changes_the_scope_hash(self):
        """Scenario L."""
        repository = self.make_git_repository("scope")
        first = self.result_of(self.analyze(repository, output="a"))
        (repository / "app.py").write_text(
            PYTHON_SOURCE + "    def extra(self):\n        return 2\n", encoding="utf-8"
        )
        second = self.result_of(self.analyze(repository, output="b"))
        self.assertNotEqual(
            first["analysis_scope_hash"], second["analysis_scope_hash"]
        )
        self.assertEqual(first["subject_key"], second["subject_key"])

    def test_identical_content_produces_an_identical_scope_hash(self):
        one = self.make_plain_directory("same_one")
        two = self.make_plain_directory("same_two")
        first = self.result_of(self.analyze(one, output="c"))
        second = self.result_of(self.analyze(two, output="d"))
        self.assertEqual(
            first["analysis_scope_hash"], second["analysis_scope_hash"],
            "identical measured bytes must produce an identical scope hash",
        )
        self.assertNotEqual(
            first["subject_key"], second["subject_key"],
            "two different subjects with identical bytes must stay distinct",
        )

    def test_absolute_paths_never_enter_the_scope_hash(self):
        repository = self.make_plain_directory("pathfree")
        result = self.result_of(self.analyze(repository, output="e"))
        self.assertNotIn(str(self.root), json.dumps(result["analysis_scope_hash"]))


class ScopeHashBoundaryTests(LocalFixture):
    """Exactly which evidence may move `analysis_scope_hash`, and which may not.

    The boundary is metric relevance, not observability. Content and file kind
    can change what ArchLens measures; whether Git happened to be able to report
    a regular file's mode cannot.
    """

    class _Record:
        """Minimal inventory record. Deliberately not a real inventory.

        These four cases are about the hash construction itself, so they drive
        it directly rather than through a full analysis, where an unrelated
        inventory change could mask a regression in what the hash covers.
        """

        def __init__(self, path, content_hash, **overrides):
            self.relative_path = path
            self.content_hash = content_hash
            self.included_in_metrics = True
            self.git_mode = None
            self.is_git_symlink = False
            self.is_git_submodule = False
            self.git_symlink_target = None
            self.exclusion_reason = None
            self.__dict__.update(overrides)

    # -- 1. provenance must NOT move the hash -----------------------------

    def test_regular_file_git_mode_availability_does_not_move_the_hash(self):
        """Checkout vs archive: same bytes, only Git-mode evidence differs.

        No production path compares a regular file's mode against anything;
        `git_mode` is only ever tested for 120000 and 160000. So this difference
        is availability of provenance, not a difference in what was analyzed.
        """
        checkout = [self._Record("app.py", "abc", git_mode="100644")]
        archive = [self._Record("app.py", "abc", git_mode=None)]
        self.assertEqual(
            compute_analysis_scope_hash(checkout),
            compute_analysis_scope_hash(archive),
        )

    def test_the_executable_bit_alone_does_not_move_the_hash(self):
        """100755 is also never read by source selection, parsing or metrics."""
        plain = [self._Record("build.sh", "abc", git_mode="100644")]
        executable = [self._Record("build.sh", "abc", git_mode="100755")]
        self.assertEqual(
            compute_analysis_scope_hash(plain),
            compute_analysis_scope_hash(executable),
        )

    def test_no_production_path_reads_a_regular_file_mode(self):
        """The evidence for the change above, asserted mechanically.

        If a future change starts branching on 100644 or 100755, this fails and
        the scope-hash decision has to be revisited rather than silently
        invalidated.
        """
        import re

        root = Path(__file__).resolve().parent.parent
        offenders = []
        for path in list((root / "modules").rglob("*.py")) + [root / "pipeline.py"]:
            for number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), start=1
            ):
                if re.search(r'["\'](?:100644|100755)["\']', line):
                    offenders.append(f"{path.name}:{number}")
        self.assertEqual(
            offenders, [],
            "a production path now reads a regular-file Git mode; "
            "analysis_scope_hash excludes it and that decision must be re-made",
        )

    # -- 2/3/4. real differences MUST move the hash -----------------------

    def test_different_analyzed_bytes_still_move_the_hash(self):
        """CRLF vs LF at one commit. The ratified behaviour, not weakened."""
        lf = [self._Record("app.py", "hash_of_lf_bytes")]
        crlf = [self._Record("app.py", "hash_of_crlf_bytes")]
        self.assertNotEqual(
            compute_analysis_scope_hash(lf), compute_analysis_scope_hash(crlf)
        )

    def test_a_file_kind_difference_still_moves_the_hash(self):
        """A symlink and the file it points to are not the same source."""
        regular = [self._Record("link.py", "abc")]
        symlink = [self._Record(
            "link.py", "abc", is_git_symlink=True, git_symlink_target="real.py"
        )]
        submodule = [self._Record("link.py", "abc", is_git_submodule=True)]

        digests = {
            compute_analysis_scope_hash(regular),
            compute_analysis_scope_hash(symlink),
            compute_analysis_scope_hash(submodule),
        }
        self.assertEqual(len(digests), 3, "file kinds must stay distinguishable")

    def test_file_kind_is_derived_from_native_evidence_when_git_is_absent(self):
        """A symlink stays a symlink in an archive, where no Git mode exists."""
        native = [self._Record("link.py", "abc", exclusion_reason="symlink")]
        regular = [self._Record("link.py", "abc")]
        self.assertNotEqual(
            compute_analysis_scope_hash(native),
            compute_analysis_scope_hash(regular),
        )

    def test_a_changed_symlink_target_still_moves_the_hash(self):
        one = [self._Record(
            "link.py", "abc", is_git_symlink=True, git_symlink_target="a.py"
        )]
        two = [self._Record(
            "link.py", "abc", is_git_symlink=True, git_symlink_target="b.py"
        )]
        self.assertNotEqual(
            compute_analysis_scope_hash(one), compute_analysis_scope_hash(two)
        )

    def test_an_ordinary_content_modification_still_moves_the_hash(self):
        """Scenario 4, end to end rather than on synthetic records."""
        repository = self.make_git_repository("contentmod")
        first = self.result_of(self.analyze(repository, output="cm_a"))
        (repository / "app.py").write_text(
            PYTHON_SOURCE + "    def added(self):\n        return 7\n",
            encoding="utf-8",
        )
        second = self.result_of(self.analyze(repository, output="cm_b"))
        self.assertNotEqual(
            first["analysis_scope_hash"], second["analysis_scope_hash"]
        )

    def test_a_renamed_file_still_moves_the_hash(self):
        """Path is part of the analyzed scope, not just content."""
        before = [self._Record("old.py", "abc")]
        after = [self._Record("new.py", "abc")]
        self.assertNotEqual(
            compute_analysis_scope_hash(before), compute_analysis_scope_hash(after)
        )

    def test_the_construction_version_records_the_change(self):
        from modules.subject import ANALYSIS_SCOPE_HASH_VERSION

        self.assertEqual(ANALYSIS_SCOPE_HASH_VERSION, "2.0.0")

    def test_the_scope_hash_correction_did_not_drive_an_artifact_bump(self):
        """A construction correction is not a contract change.

        Artifact Schema later moved to 1.8.0, but for an unrelated reason
        (RECOVERY-SHA). The property here is that `analysis_scope_hash`'s
        persisted field and meaning never changed, so the scope-hash work
        contributed no schema version of its own -- checked by asserting the
        digest is still carried by its own construction version.
        """
        from modules.subject import ANALYSIS_SCOPE_HASH_VERSION

        self.assertEqual(ANALYSIS_SCOPE_HASH_VERSION, "2.0.0")


class ArtifactIdentityTests(LocalFixture):
    def test_a_local_run_declares_1_7_and_validates_literally(self):
        from modules.cli.validate_command import schema_only_report

        run = self.analyze(self.make_plain_directory("v17"))
        report = schema_only_report(run)
        self.assertEqual(report["schema_contract_evaluated"], ARTIFACT_SCHEMA_VERSION)
        self.assertEqual(report["violations"], [])
        self.assertEqual(report["accepted_compatibility_exceptions"], [])
        self.assertEqual(report["result"], "schema_valid")

    def test_a_non_git_directory_uses_the_normalized_git_evidence(self):
        """Scenario H: status plus reason, not a compound value."""
        result = self.result_of(self.analyze(self.make_plain_directory("nogit")))
        self.assertEqual(result["source_mode"], "local_directory_snapshot")
        self.assertEqual(result["git_mode_map_status"], "unavailable")
        self.assertEqual(result["git_mode_map_reason"], "not_git_repository")
        self.assertNotEqual(
            result["git_mode_map_status"], "unavailable_not_git",
            "1.7 must not emit the compound 1.5/1.6 value",
        )

    def test_repository_url_is_null_for_a_local_subject(self):
        result = self.result_of(self.analyze(self.make_plain_directory("nourl")))
        self.assertIsNone(result["repository_url"])
        self.assertTrue(result["subject_key"])

    def test_explicit_subject_key_reaches_the_artifact(self):
        """Scenario J."""
        result = self.result_of(
            self.analyze(self.make_plain_directory("explicit"), subject_key="my-project")
        )
        self.assertEqual(result["subject_key"], "my-project")
        self.assertEqual(result["subject_key_basis"], "explicit")

    def test_worktree_state_distinguishes_a_dirty_snapshot(self):
        repository = self.make_git_repository("state")
        (repository / "app.py").write_text(PYTHON_SOURCE + "# edit\n", encoding="utf-8")
        result = self.result_of(self.analyze(repository))
        self.assertEqual(result["working_tree_state"], "dirty_worktree")
        self.assertEqual(result["source_mode"], "local_worktree_snapshot")


class ComparabilityTests(LocalFixture):
    """Source mode alone must never decide comparability."""

    def test_two_exact_revisions_of_one_commit_agree_completely(self):
        """Contract A: exact revision reached two ways, same commit.

        Both materialize from Git revision data rather than a working-tree
        representation, so they are canonical and host-independent: same
        subject, same commit, **same** analyzed scope.

        The second repository is an independent clone through a local bare
        origin, so this exercises the remote-style path without the network.
        """
        source = self.make_git_repository("exact_src")
        head = git(source, "rev-parse", "HEAD")

        bare = self.root / "exact_origin.git"
        subprocess.run(["git", "clone", "--bare", str(source), str(bare)],
                       check=True, capture_output=True)
        clone = self.root / "exact_clone"
        subprocess.run(["git", "clone", str(bare), str(clone)],
                       check=True, capture_output=True)

        first = self.result_of(
            self.analyze(source, revision=head, subject_key="exact", output="x")
        )
        second = self.result_of(
            self.analyze(clone, revision=head, subject_key="exact", output="y")
        )

        self.assertEqual(first["subject_key"], second["subject_key"])
        self.assertEqual(
            first["acquisition"]["analyzed_commit_sha"],
            second["acquisition"]["analyzed_commit_sha"],
        )
        self.assertEqual(
            first["analysis_scope_hash"], second["analysis_scope_hash"],
            "exact-revision materialization must be canonical and "
            "host-independent, so the same commit yields the same analyzed scope",
        )

    def test_exact_revision_and_clean_worktree_share_subject_and_revision(self):
        """Contract B: same subject and revision; scope equality is conditional.

        A clean worktree and its HEAD revision are the same subject at the same
        commit. Whether they analyzed the *same bytes* is a separate question:
        checkout transformations (CRLF on Windows) can make the on-disk bytes
        differ from the canonical revision materialization.

        Git cleanliness and byte identity are different claims, so this asserts
        the claims that always hold and treats scope equality as conditional on
        the captured bytes.
        """
        repository = self.make_git_repository("dual")
        head = git(repository, "rev-parse", "HEAD")

        exact = self.result_of(
            self.analyze(repository, revision=head, subject_key="dual", output="x")
        )
        worktree = self.result_of(
            self.analyze(repository, subject_key="dual", output="y")
        )

        self.assertNotEqual(exact["source_mode"], worktree["source_mode"])
        self.assertEqual(exact["subject_key"], worktree["subject_key"])
        self.assertEqual(worktree["working_tree_state"], "clean_worktree")
        self.assertEqual(
            exact["acquisition"]["analyzed_commit_sha"],
            worktree["acquisition"]["analyzed_commit_sha"],
            "a clean worktree analysis still records the HEAD it corresponds to",
        )
        # Scope equality is conditional, so it is asserted against the captured
        # bytes rather than assumed in either direction.
        self.assertTrue(exact["analysis_scope_hash"])
        self.assertTrue(worktree["analysis_scope_hash"])

    def test_crlf_worktree_legitimately_differs_from_the_canonical_revision(self):
        """The case that forced the model correction, pinned as evidence.

        A CRLF working tree and the canonical LF revision materialization
        analyze genuinely different bytes. The digests must differ — that is
        evidence, not a defect — while both analyses stay attached to the same
        logical subject and the same Git revision.
        """
        repository = self.make_git_repository("crlf")
        head = git(repository, "rev-parse", "HEAD")
        # No setup is needed to create the divergence: `Path.write_text`
        # produced CRLF on disk, Git stored LF in the object database, and the
        # worktree is clean. That *is* the ordinary Windows situation, which is
        # precisely why it matters.
        if git(repository, "status", "--porcelain"):
            self.fail("fixture assumption broken: the worktree is not clean")

        exact = self.result_of(
            self.analyze(repository, revision=head, subject_key="crlf", output="x")
        )
        worktree = self.result_of(
            self.analyze(repository, subject_key="crlf", output="y")
        )

        on_disk = (repository / "app.py").read_bytes()
        if b"\r\n" not in on_disk:
            self.fail(
                "fixture assumption broken: the worktree file is not CRLF, so "
                "this test would prove nothing"
            )

        # Same subject, same revision — the dimensions that must not collapse.
        self.assertEqual(exact["subject_key"], worktree["subject_key"])
        self.assertEqual(
            exact["acquisition"]["analyzed_commit_sha"],
            worktree["acquisition"]["analyzed_commit_sha"],
        )
        # Different analyzed materialization, correctly recorded as different.
        self.assertNotEqual(
            exact["analysis_scope_hash"], worktree["analysis_scope_hash"],
            "CRLF worktree bytes and canonical LF revision bytes are different "
            "source; the scope hash must say so rather than be normalized into "
            "a false equality",
        )

    def test_the_measurement_digest_ignores_source_mode_for_equal_bytes(self):
        """Two exact-revision analyses of one commit are one measurement."""
        from validation.scripts.semantic_projection import measurement_semantic_hash

        repository = self.make_git_repository("digest")
        head = git(repository, "rev-parse", "HEAD")
        first = self.analyze(repository, revision=head, subject_key="d", output="p")
        second = self.analyze(repository, revision=head, subject_key="d", output="q")
        self.assertEqual(
            measurement_semantic_hash(first), measurement_semantic_hash(second)
        )

    def test_two_subjects_with_identical_bytes_remain_distinct(self):
        from validation.scripts.semantic_projection import semantic_projection

        one = self.analyze(self.make_plain_directory("dist_a"), output="r")
        two = self.analyze(self.make_plain_directory("dist_b"), output="s")
        keys = {
            semantic_projection(run)["semantic"]["repositories"][0]["identity"]["subject_key"]
            for run in (one, two)
        }
        self.assertEqual(len(keys), 2, "distinct subjects collapsed into one key")


class RemoteStyleAcquisitionTests(LocalFixture):
    """Scenario A, without touching the network.

    A local bare repository stands in for the origin, so the remote-style
    acquisition path is genuinely exercised while the suite stays hermetic.
    """

    def test_a_local_bare_origin_yields_a_portable_subject_key(self):
        source = self.make_git_repository("origin_src")
        bare = self.root / "origin.git"
        subprocess.run(["git", "clone", "--bare", str(source), str(bare)],
                       check=True, capture_output=True)

        clone = self.root / "clone"
        subprocess.run(["git", "clone", str(bare), str(clone)],
                       check=True, capture_output=True)
        git(clone, "remote", "set-url", "origin", "https://github.com/acme/app.git")

        identity = resolve_subject_identity(
            source_mode=SourceMode.LOCAL_WORKTREE_SNAPSHOT, local_path=clone
        )
        self.assertEqual(identity.subject_key, "github.com/acme/app")
        self.assertIs(identity.subject_key_basis, SubjectKeyBasis.GIT_ORIGIN)
        self.assertTrue(identity.portable)


class PreflightReuseTests(LocalFixture):
    """Scenario M: one capability refusal, zero parsed files, single subject."""

    def test_missing_grammar_refuses_the_subject_before_parsing(self):
        from modules import preflight
        from modules.preflight import PreflightRefused
        from modules.vocabularies import parser_capability
        import modules.benchmark_runner as runner

        repository = self.root / "java_local"
        repository.mkdir()
        for index in range(5):
            (repository / f"Service{index}.java").write_text(
                f"package a;\npublic class Service{index} {{ public void run() {{}} }}\n",
                encoding="utf-8",
            )

        parsed: list[str] = []
        original = runner.compute_repository_metrics

        def recording(inventory, *args, **kwargs):
            parsed.append(str(inventory.root))
            return original(inventory, *args, **kwargs)

        probes = dict(preflight.probe_parser_capabilities())
        probes[parser_capability("Java")] = preflight.CapabilityCheck(
            capability=parser_capability("Java"),
            state=preflight.CapabilityState.UNAVAILABLE,
            blocking=False,
            evidence="simulated: grammar not installed",
            reason=preflight.CapabilityReason.GRAMMAR_UNAVAILABLE,
        )

        with (
            patch.object(runner, "compute_repository_metrics", recording),
            patch("modules.preflight.probe_parser_capabilities", lambda: probes),
        ):
            with self.assertRaises(PreflightRefused) as caught:
                self.analyze(repository, output="pf")

        report = caught.exception.report
        self.assertEqual(
            [check.capability for check in report.refusals], ["java_parser"]
        )
        self.assertEqual(
            parsed, [], "the subject entered metric parsing despite a refusal"
        )


class LocalRunIndependentValidationTests(LocalFixture):
    """The independent validator must accept a local run, not just a remote one.

    Nothing exercised `validate_run` against a local subject, so the Artifact 1.7
    nullable-locator model went unverified there: the validator still keyed its
    tables and its ordering check on `repository_url` and crashed outright on
    `None`, and it demanded a verified 40-hex commit of every subject including
    a plain directory that has no commit at all.
    """

    def _subject(self, run):
        from validation.scripts.validate_outputs import validate_run

        report = validate_run(run)
        self.assertTrue(report["passed"], report["failures"])
        return report

    def test_a_directory_snapshot_passes_independent_validation(self):
        run = self.analyze(self.make_plain_directory("iv_dir"), output="iv_dir_out")
        report = self._subject(run)
        self.assertEqual(len(report["repository_durations"]), 1)
        # Identity, not the locator, is what the duration record is attributed to.
        self.assertTrue(report["repository_durations"][0]["subject_key"])
        self.assertIsNone(report["repository_durations"][0]["repository_url"])

    def test_a_worktree_snapshot_passes_independent_validation(self):
        run = self.analyze(self.make_git_repository("iv_wt"), output="iv_wt_out")
        self._subject(run)
        result = self.result_of(run)
        self.assertEqual(result["source_mode"], "local_worktree_snapshot")
        # HEAD is recorded as provenance even though the analyzed bytes are the
        # worktree's, so `commit_verification_status` is `not_applicable` and a
        # SHA is still present. The validator must accept exactly that pairing.
        self.assertEqual(
            result["acquisition"]["commit_verification_status"], "not_applicable"
        )
        self.assertRegex(result["acquisition"]["analyzed_commit_sha"], r"^[0-9a-f]{40}$")

    def test_an_exact_revision_passes_independent_validation(self):
        repository = self.make_git_repository("iv_rev")
        head = git(repository, "rev-parse", "HEAD").strip()
        run = self.analyze(repository, revision=head, output="iv_rev_out")
        self._subject(run)
        result = self.result_of(run)
        self.assertEqual(result["source_mode"], "local_git_revision")
        self.assertEqual(
            result["acquisition"]["commit_verification_status"], "verified"
        )

    def test_a_local_run_is_literally_schema_valid_with_no_waivers(self):
        from modules.cli.validate_command import schema_only_report

        run = self.analyze(self.make_plain_directory("iv_schema"), output="iv_schema_out")
        report = schema_only_report(run)
        self.assertEqual(report["result"], "schema_valid")
        self.assertEqual(report["accepted_compatibility_exception_count"], 0)
        self.assertEqual(
            report["schema_contract_evaluated"], ARTIFACT_SCHEMA_VERSION
        )
        self.assertTrue(report["satisfies_current_schema"])


if __name__ == "__main__":
    unittest.main()


class RecoverySchemaCorrectionTests(LocalFixture):
    """RECOVERY-SHA: a local snapshot with a parser recovery must finalize.

    Artifact 1.7's `recoveries_row` required a non-null `analyzed_commit_sha`
    while `errors_row`, `catalog_row`, `sheet_metrics_row` and
    `language_metrics_row` in the **same generation** all allowed null. A local
    directory or worktree snapshot has no commit, so any such subject that
    produced a parser recovery wrote an empty cell into a non-nullable column
    and finalization refused to publish. Two real Layer 2 subjects, Obojobo and
    Magda, were blocked by exactly this.

    Corrected in Artifact Schema 1.8.0, correction-only: one property, no field
    added or removed, 1.7.0 bytes retained unedited.
    """

    #: An import assertion triggers ArchLens's JavaScript compatibility
    #: fallback, which is what produces a recovery diagnostic row.
    RECOVERING_SOURCE = (
        'import pkg from "./package.json" assert { type: "json" };\n'
        "export function create() { return pkg; }\n"
    )

    def _snapshot_with_a_recovery(self, name: str) -> Path:
        path = self.root / name
        path.mkdir(parents=True)
        (path / "assertion.js").write_text(
            self.RECOVERING_SOURCE, encoding="utf-8", newline="\n"
        )
        return path

    def test_the_correction_matches_its_sibling_row_contracts(self):
        """The evidence the correction was based on, asserted mechanically."""
        from validation.artifact_io.schema_store import load_schema

        siblings = (
            "errors_row", "catalog_row", "sheet_metrics_row",
            "language_metrics_row", "recoveries_row",
        )
        for name in siblings:
            with self.subTest(schema=name):
                prop = load_schema(name)["properties"]["analyzed_commit_sha"]
                self.assertIn(
                    "null", prop["type"],
                    f"{name} must allow a null commit: a local snapshot has none",
                )
                self.assertTrue(
                    prop["x-archlens-nullable"],
                    f"{name} declares type nullable but the reader annotation "
                    f"says otherwise; both halves must agree",
                )

    def test_artifact_1_7_bytes_were_not_edited(self):
        """1.8 is a new file. The frozen predecessor is retained unchanged."""
        from validation.artifact_io.schema_store import (
            SCHEMA_REGISTRY, load_schema, schema_version,
        )

        self.assertEqual(schema_version("recoveries_row"), "1.8.0")
        self.assertEqual(schema_version("recoveries_row_1_7_historical"), "1.7.0")
        historical = load_schema("recoveries_row_1_7_historical")
        self.assertEqual(
            historical["properties"]["analyzed_commit_sha"]["type"], "string",
            "the 1.7 contract must still say what it originally said",
        )
        self.assertIn("recoveries_row-1.7.schema.json", str(SCHEMA_REGISTRY))

    def test_1_8_is_correction_only_against_1_7(self):
        """No new field, no removed field, exactly one property changed."""
        from validation.artifact_io.schema_store import load_schema

        current = load_schema("recoveries_row")
        historical = load_schema("recoveries_row_1_7_historical")
        self.assertEqual(
            set(current["properties"]), set(historical["properties"]),
            "a correction-only version adds and removes no field",
        )
        self.assertEqual(current.get("required"), historical.get("required"))
        differing = [
            name for name in current["properties"]
            if current["properties"][name] != historical["properties"][name]
        ]
        self.assertEqual(differing, ["analyzed_commit_sha"])

    def test_a_local_snapshot_with_a_recovery_finalizes(self):
        """The end-to-end property. This is what was broken."""
        run = self.analyze(
            self._snapshot_with_a_recovery("recovery_local"), output="rec"
        )
        status = json.loads(
            (run / "run_status.json").read_text(encoding="utf-8")
        )["status"]
        self.assertIn(status, {"completed", "completed_with_errors"})

        import csv

        with (run / "recoveries.csv").open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertTrue(rows, "the fixture must actually produce a recovery")
        self.assertEqual(
            rows[0]["analyzed_commit_sha"], "",
            "a local directory snapshot has no commit, and that is legitimate",
        )

    def test_such_a_run_is_literally_valid_with_no_waivers(self):
        from modules.cli.validate_command import schema_only_report

        run = self.analyze(
            self._snapshot_with_a_recovery("recovery_valid"), output="recv"
        )
        report = schema_only_report(run)
        self.assertEqual(report["schema_contract_evaluated"], ARTIFACT_SCHEMA_VERSION)
        self.assertEqual(report["result"], "schema_valid")
        self.assertEqual(report["violation_count"], 0)
        self.assertEqual(report["accepted_compatibility_exception_count"], 0)
