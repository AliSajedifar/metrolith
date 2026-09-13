"""Focused LEP producer, destination and guidance regressions."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from modules.cli.export_output import admit, ExportOutputError
from modules.ratchet.check_service import _optional_current_commit
from modules.policy.check import CheckFailed


class OptionalCommitTests(unittest.TestCase):
    def snapshot(self, commit=""):
        return {"source_mode":"local_directory_snapshot", "working_tree_state":"not_applicable", "acquisition":{"acquisition_mode":"local_directory_snapshot", "analyzed_commit_sha":commit, "requested_commit_sha":None}}

    def test_proven_absence_and_bad_identity_are_distinct(self):
        for value in (None, ""):
            self.assertIsNone(_optional_current_commit(self.snapshot(value)))
        for value in (0, False, "bad", "0", " "*40, "A"*40, "a"*40):
            with self.subTest(value=value), self.assertRaises(CheckFailed):
                _optional_current_commit(self.snapshot(value))

    def test_real_commit_keeps_exact_identity_and_snapshot_context(self):
        for mode in ("local_git_revision", "remote_git_revision", "local_worktree_snapshot"):
            for value in ("a"*40, "b"*64):
                source={"source_mode":mode,"working_tree_state":"dirty_worktree" if mode.endswith("snapshot") else "committed_revision","acquisition":{"analyzed_commit_sha":value,"acquisition_mode":mode}}
                before=json.dumps(source)
                self.assertEqual(_optional_current_commit(source),value)
                self.assertEqual(json.dumps(source),before)

    def test_inconsistent_mode_and_missing_revision_refuse(self):
        source=self.snapshot();source['acquisition']['acquisition_mode']='local_git_revision'
        with self.assertRaises(CheckFailed): _optional_current_commit(source)
        for mode in ('local_git_revision','remote_git_revision'):
            for value in (None,'',0,'bad'):
                with self.subTest(mode=mode,value=value), self.assertRaises(CheckFailed):
                    _optional_current_commit({'source_mode':mode,'acquisition':{'analyzed_commit_sha':value}})

    def test_invalid_document_never_reaches_stdout_file_or_sarif(self):
        from modules.cli.check_command import _emit
        from modules.cli.check_output import CheckOutputError
        from contextlib import redirect_stdout
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'result.json';path.write_text('prior')
            for format in ('json','text','sarif'):
                stream=io.StringIO()
                with redirect_stdout(stream), self.assertRaises(CheckOutputError):
                    _emit(SimpleNamespace(output=path,overwrite=True),{'verdict':'pass','exit_code':0},format)
                self.assertEqual(stream.getvalue(),'')
                self.assertEqual(path.read_text(),'prior')


class DestinationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import pipeline
        temporary=tempfile.TemporaryDirectory();cls.addClassCleanup(temporary.cleanup)
        root=Path(temporary.name);source=root/'source';source.mkdir()
        (source/'app.py').write_text('def f(x):\n    return x + 1\n',encoding='utf-8')
        result=subprocess.run(
            [sys.executable,'-B',pipeline.__file__,'analyze',str(source),'--workspace',str(root/'workspace')],
            cwd=root,capture_output=True,timeout=120,
            env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',PYTHONUTF8='1'),
        )
        if result.returncode:
            raise AssertionError((result.stdout,result.stderr))
        cls.native_run=next((root/'workspace/metrolith-output/runs').iterdir())

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.source=self.root/'source';self.source.mkdir()
        self.input=self.source/'app.py';self.input.write_text('x=1\n')
        self.run=self.root/'run';self.run.mkdir();(self.run/'analysis.json').write_text('[]')

    def guard(self,path,**kwargs):
        return admit(path,kind='report',sources=[self.source],runs=[self.run],**kwargs)

    def report(self):
        from modules.cli.report_command import render_report
        from validation.artifact_io.reader import open_run
        return render_report(open_run(self.native_run))

    def test_all_authoritative_files_and_source_preserved(self):
        for path in (self.input,self.run/'analysis.json',self.run/'run_manifest.json',self.run/'callables/container.json'):
            for overwrite in (False,True):
                with self.subTest(path=path,overwrite=overwrite),self.assertRaises(ExportOutputError):
                    self.guard(path,overwrite=overwrite)
        self.assertEqual(self.input.read_text(),'x=1\n')

    def test_new_unicode_space_dot_and_default_report_replacement(self):
        path=self.run/'report.html';self.guard(path).write(self.report())
        with self.assertRaises(ExportOutputError):self.guard(path)
        self.guard(path,overwrite=True).write('<!doctype html>\nchanged')
        self.assertIn('changed',path.read_text())
        p=self.source/'new dir'/'..'/'caf\u00e9 space.html'
        self.guard(p).write('<!doctype html>\nnew')

    def test_external_existing_output_and_hardlink(self):
        dest=self.root/'result';dest.write_text('old')
        with self.assertRaises(ExportOutputError):self.guard(dest)
        self.guard(dest,overwrite=True).write('new');self.assertEqual(dest.read_text(),'new')
        link=self.root/'hardlink';os.link(self.input,link)
        with self.assertRaises(ExportOutputError):self.guard(link,overwrite=True)
        self.assertEqual(self.input.read_text(),'x=1\n')

    def test_dot_components_in_source_keep_eligible_derived_replacement(self):
        output=self.source/'result.html';output.write_text(self.report(),encoding='utf-8',newline='')
        admit(output,kind='report',sources=[self.source/'..'/'source'],overwrite=True).write('<!doctype html>\nupdated')
        self.assertIn('updated',output.read_text())

    def test_custom_derived_extension_and_source_extension_protection(self):
        for root in (self.source,self.run):
            output=root/'result.out';output.write_text(self.report(),encoding='utf-8',newline='')
            self.guard(output,overwrite=True).write('<!doctype html>\nupdated')
            self.assertIn('updated',output.read_text())
        self.input.write_text('# Metrolith analysis dossier\n')
        with self.assertRaises(ExportOutputError):
            admit(self.input,kind='dossier',sources=[self.source],overwrite=True)

    def test_handwritten_marker_collisions_are_not_derived_outputs(self):
        for kind,name,content in (
            ('dossier','README.md','# Metrolith\nHandwritten documentation.\n'),
            ('report','index.html','<!doctype html>\n<html>Handwritten HTML</html>\n'),
            ('report','page.out','<!DOCTYPE html>\n<html>Handwritten HTML</html>\n'),
            ('dossier','notes.out','# Metrolith analysis dossier\nHandwritten notes.\n'),
            ('changed','brief.out','# Metrolith\nHandwritten notes.\n'),
        ):
            with self.subTest(kind=kind,name=name):
                output=self.source/name;output.write_text(content,encoding='utf-8')
                before=output.read_bytes()
                with self.assertRaises(ExportOutputError):
                    admit(output,kind=kind,sources=[self.source],overwrite=True)
                self.assertEqual(before,output.read_bytes())

    def test_native_markdown_formats_replace_only_their_own_kind(self):
        from modules.dossier import build_dossier, render_markdown
        from modules.changed_code import render_markdown as changed_markdown
        from validation.artifact_io.reader import open_run
        dossier=render_markdown(build_dossier(open_run(self.native_run)))
        from modules.changed_code import diagnostic_document
        changed=changed_markdown(diagnostic_document(status='unavailable',reason='revision_unavailable',base_requested='missing',head_requested='HEAD'))
        for kind,payload in (('dossier',dossier),('changed',changed)):
            output=self.source/(kind+'.out');output.write_text(payload,encoding='utf-8',newline='')
            admit(output,kind=kind,sources=[self.source],overwrite=True).write(payload)
            other='changed' if kind=='dossier' else 'dossier'
            with self.assertRaises(ExportOutputError):
                admit(output,kind=other,sources=[self.source],overwrite=True)

    def test_native_links_dangling_and_linked_parent(self):
        try:
            link=self.root/'link';link.symlink_to(self.input)
        except OSError as exc:
            self.skipTest('native symlink unavailable: '+str(exc))
        dangling=self.root/'dangling';dangling.symlink_to(self.root/'absent')
        parent=self.root/'linked-parent';parent.symlink_to(self.source,target_is_directory=True)
        for path in (link,dangling,parent/'new.json'):
            with self.subTest(path=path), self.assertRaises(ExportOutputError):self.guard(path,overwrite=True)

    def test_unsafe_types_and_case_distinct_paths(self):
        with self.assertRaises(ExportOutputError):self.guard(self.root,overwrite=True)
        lower=self.root/'case';lower.write_text('a');upper=self.root/'CASE'
        if upper.exists():self.skipTest('destination filesystem is case-insensitive')
        admit(upper,kind='diff',files=[lower]).write('b')
        self.assertEqual(lower.read_text(),'a')
        if hasattr(os,'mkfifo'):
            fifo=self.root/'fifo';os.mkfifo(fifo)
            with self.assertRaises(ExportOutputError):self.guard(fifo,overwrite=True)

    def test_destination_changes_after_admission_and_during_publication(self):
        for phase in ('evaluation','publication'):
            dest=self.root/phase;dest.write_text('old');guard=self.guard(dest,overwrite=True)
            def swap(*args):
                dest.unlink();os.link(self.input,dest)
            if phase=='evaluation':
                swap()
                with self.assertRaises(ExportOutputError):guard.write('bad')
            else:
                with patch('modules.cli.export_output.os.fsync',side_effect=swap), self.assertRaises(ExportOutputError):guard.write('bad')
            self.assertEqual(self.input.read_text(),'x=1\n')

    def test_parent_identity_change_and_git_worktree_administration(self):
        parent=self.root/'parent';parent.mkdir();guard=self.guard(parent/'out')
        parent.rename(self.root/'prior-parent');parent.mkdir()
        with self.assertRaises(ExportOutputError):guard.write('bad')
        admin=self.root/'repo.git';work=admin/'worktrees'/'w';work.mkdir(parents=True)
        (work/'commondir').write_text('../..');(self.source/'.git').write_text('gitdir: '+str(work))
        index=admin/'index';index.write_text('index')
        with self.assertRaises(ExportOutputError):self.guard(index,overwrite=True)
        alias=self.root/'index-link';os.link(index,alias)
        with self.assertRaises(ExportOutputError):self.guard(alias,overwrite=True)

    def test_explicit_supplement_even_when_derived_is_protected(self):
        p=self.root/'supplement.json';p.write_text('{"format":"archlens-dossier"}')
        with self.assertRaises(ExportOutputError):admit(p,overwrite=True,files=[p],kind='dossier')


class GuidanceTests(unittest.TestCase):
    def test_resolved_source_provenance_preserves_options(self):
        import argparse
        from pipeline import _record_analyze_source, build_cli
        parser=argparse.ArgumentParser();parser.add_argument('--workspace');parser.add_argument('--tracked-only',action='store_true');parser.add_argument('path')
        source=Path.cwd()/'source'
        for argv in (['analyze','.','--workspace','.'], ['analyze','--workspace','.','--tracked-only','.'], ['analyze','--workspace=.','--','.']):
            actual=_record_analyze_source(argv,parser,source)
            self.assertEqual(actual[1],str(source))
            self.assertEqual(parser.parse_args(actual[1:]).workspace,'.')
            self.assertEqual(_record_analyze_source(argv,build_cli(),source),actual)

    def test_guidance_has_prerequisite_and_honest_refusal(self):
        from modules.ratchet.cli_request import REVISION_SOURCE_GUIDANCE
        for value in ('URL-backed','runner cache','local analyze --revision','no repository-override','Capture success','no ancestry comparison ran'):
            self.assertIn(value,REVISION_SOURCE_GUIDANCE)
