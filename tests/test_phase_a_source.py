"""Local materialization must preserve completeness and file-kind authority."""
import os
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

from modules.inventory import RepositoryInventory
from modules.local_source import LocalSourceError, prepare_local_source
from tests.test_local_analysis import LocalFixture, git


class PreparationTests(LocalFixture):
    def test_plain_directory_enumeration_failure_is_not_empty(self):
        repo=self.make_plain_directory()
        original=os.scandir
        def deny(path):
            # POSIX cleanup may scan a directory descriptor; deny only the source path.
            if not isinstance(path,int) and Path(path)==repo:
                raise PermissionError(13,'enumeration denied',str(path))
            return original(path)
        with patch('os.scandir',side_effect=deny):
            with self.assertRaisesRegex(LocalSourceError,'enumeration failed'):
                with prepare_local_source(repo):
                    self.fail('Failed walk was treated as an empty source')

    def test_failed_preparation_does_not_replace_latest_or_reuse_measurements(self):
        import json
        from modules.policy.check import CheckFailed, evaluate_check
        from tests.test_policy_v2_check import _policy, _rule
        repo=self.make_git_repository()
        earlier=self.analyze(repo)
        old_analysis=(earlier/'analysis.json').read_bytes()
        latest=self.root/'output/latest_run.json'
        pointer=latest.read_bytes()
        original=shutil.copy2
        def deny(source,target,*args,**kwargs):
            if Path(source).name=='app.py':
                raise PermissionError('denied selected app.py')
            return original(source,target,*args,**kwargs)
        with patch('modules.local_source.shutil.copy2',side_effect=deny):
            failed=self.analyze(repo)
        self.assertNotEqual(failed,earlier)
        self.assertEqual(json.loads((failed/'run_status.json').read_text())['status'],'failed')
        self.assertIsNone(self.result_of(failed)['metrics']['aggregate']['source_files'])
        self.assertEqual(latest.read_bytes(),pointer)
        self.assertEqual((earlier/'analysis.json').read_bytes(),old_analysis)
        with self.assertRaises(CheckFailed):
            evaluate_check(failed,_policy(_rule('r','repository.source_files','gt',100)))

    def test_selected_read_denial_fails_with_relative_path_and_cleans_snapshot(self):
        repo = self.make_git_repository()
        original = shutil.copy2
        targets = []
        def deny(source, target, *args, **kwargs):
            targets.append(Path(target))
            if Path(source).name == 'app.py':
                raise PermissionError('synthetic read denial')
            return original(source, target, *args, **kwargs)
        with patch('modules.local_source.shutil.copy2', side_effect=deny):
            with self.assertRaisesRegex(LocalSourceError, 'app.py.*denial'):
                with prepare_local_source(repo):
                    self.fail('Incomplete source was yielded')
        self.assertTrue(targets)
        self.assertTrue(all(not target.exists() for target in targets))
        self.assertTrue((repo/'app.py').exists())

    def test_git_enumeration_failure_is_not_empty_selection(self):
        repo = self.make_git_repository()
        original = subprocess.run
        def fail(args, **kwargs):
            if 'ls-files' in args:
                raise subprocess.CalledProcessError(128, args, stderr=b'enumeration denied')
            return original(args, **kwargs)
        with patch('modules.local_source.subprocess.run', side_effect=fail):
            with self.assertRaisesRegex(LocalSourceError, 'ls-files'):
                with prepare_local_source(repo):
                    self.fail('Failed enumeration yielded a snapshot')

    def test_selected_file_disappearing_during_copy_is_not_a_worktree_deletion(self):
        from modules import local_source
        repo = self.make_git_repository()
        original = local_source._copy_selected
        def race(*args, **kwargs):
            (repo/'app.py').unlink()
            return original(*args, **kwargs)
        with patch.object(local_source, '_copy_selected', side_effect=race):
            with self.assertRaisesRegex(LocalSourceError, 'app.py'):
                with prepare_local_source(repo):
                    self.fail('Selected-file race was silently lost')

    def test_intentional_deletion_before_selection_is_allowed(self):
        repo = self.make_git_repository()
        (repo/'app.py').unlink()
        with prepare_local_source(repo) as snapshot:
            self.assertFalse((snapshot.path/'app.py').exists())

    def test_plain_directory_copy_denial_has_a_relative_cause(self):
        repo = self.make_plain_directory()
        with patch('modules.local_source.shutil.copy2', side_effect=PermissionError('app.py denied')):
            with self.assertRaisesRegex(LocalSourceError, 'app.py.*denied'):
                with prepare_local_source(repo):
                    self.fail('Plain directory denied file was lost')

    def test_git_probe_error_does_not_fall_back_to_plain_directory(self):
        repo = self.make_git_repository()
        with patch('modules.local_source.subprocess.run', side_effect=OSError('git denied')):
            with self.assertRaisesRegex(LocalSourceError, 'git'):
                with prepare_local_source(repo):
                    self.fail('Git error changed source mode')


class KindEvidenceTests(LocalFixture):
    def test_nested_repository_keeps_its_exclusion_boundary(self):
        repo=self.make_plain_directory()
        nested=repo/'nested'
        nested.mkdir()
        (nested/'.git').mkdir()
        (nested/'hidden.py').write_text('x=1\n')
        with prepare_local_source(repo) as snapshot:
            inventory=RepositoryInventory(snapshot.path,**snapshot.inventory_options)
            self.assertIn('nested',inventory.nested_repositories)
            self.assertNotIn('nested/hidden.py',{r.relative_path for r in inventory})

    def make_kinds(self):
        repo = self.make_git_repository('café space فارسی')
        (repo/'link.py').write_text('app.py\n', encoding='utf-8')
        blob = git(repo, 'hash-object', '-w', 'link.py')
        git(repo,'update-index','--add','--cacheinfo',f'120000,{blob},link.py')
        head = git(repo,'rev-parse','HEAD')
        git(repo,'update-index','--add','--cacheinfo',f'160000,{head},submodule')
        git(repo,'update-index','--chmod=+x','app.py')
        git(repo,'commit','-m','Synthetic kinds')
        git(repo,'-c','core.symlinks=false','checkout-index','--force','--','link.py')
        (repo/'submodule').mkdir()
        (repo/'submodule'/'must_not_measure.py').write_text('x=1\n')
        (repo/'app.py').write_text('def working():\n    return 3\n',encoding='utf-8')
        return repo

    def test_runner_keeps_text_git_link_and_submodule_excluded(self):
        repo = self.make_kinds()
        run = self.analyze(repo)
        result = self.result_of(run)
        self.assertEqual(result['metrics']['aggregate']['source_files'],1)
        self.assertEqual(result['metrics']['aggregate']['lines_of_code'],2)
        import json
        document=json.loads(next((run/'file_inventory').glob('*.json')).read_text(encoding='utf-8'))
        rows={row['relative_path']:row for row in document['files']}
        self.assertEqual(rows['link.py']['exclusion_reason'],'git_symlink')
        self.assertEqual(rows['link.py']['git_mode'],'120000')
        self.assertEqual(rows['submodule']['exclusion_reason'],'git_submodule')
        self.assertEqual(rows['app.py']['git_mode'],'100755')
        self.assertNotIn('submodule/must_not_measure.py',rows)

    def test_exact_revision_with_git_link_needs_no_native_symlink_privilege(self):
        repo=self.make_kinds()
        result=self.result_of(self.analyze(repo,revision='HEAD'))
        self.assertEqual(result['analysis_status'],'complete')
        self.assertEqual(result['metrics']['aggregate']['source_files'],1)
        self.assertEqual(result['metrics']['aggregate']['lines_of_code'],3)

    def test_duplication_consumer_uses_the_same_kind_evidence(self):
        from modules.duplication.output import analyze_duplication_snapshot
        from modules.subject import compute_analysis_scope_hash
        repo=self.make_kinds()
        with prepare_local_source(repo) as snapshot:
            inventory=RepositoryInventory(snapshot.path,full_inventory=False,**snapshot.inventory_options)
            document=analyze_duplication_snapshot(snapshot).document
            self.assertEqual(document['counts']['eligible_file_count'],1)
            self.assertEqual(document['source']['analysis_scope_hash'],compute_analysis_scope_hash(inventory))

    def test_native_external_and_broken_directory_links_are_never_followed(self):
        repo=self.make_plain_directory()
        outside=self.root/'outside'
        outside.mkdir()
        (outside/'external.py').write_text('x=1\n')
        try:
            os.symlink(outside/'external.py',repo/'external.py')
            os.symlink(outside,repo/'external-dir',target_is_directory=True)
            os.symlink(outside/'missing',repo/'broken.py')
        except OSError as exc:
            self.skipTest(f'Native symlink capability unavailable: {exc}')
        result=self.result_of(self.analyze(repo))
        self.assertEqual(result['metrics']['aggregate']['source_files'],1)
        self.assertEqual(result['analysis_status'],'complete')
