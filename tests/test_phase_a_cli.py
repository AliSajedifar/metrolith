"""Owned CLI transport and actionable terminal presentation boundaries."""
import io
import json
import sys
import subprocess
import unittest
from argparse import Namespace
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from unittest.mock import patch

import pipeline
from modules.cli import policy_command, report_command, explain_command
from tests.test_policy_v2_check import RunFixture, _policy, _rule


class UnicodeTransportTests(RunFixture):
    def test_html_report_distinguishes_working_reference_and_unavailable_source(self):
        from validation.artifact_io.reader import open_run
        run=self.copy_run()
        rows=self.analysis(run)
        rows[0]['source_mode']='local_worktree_snapshot'
        self.write_analysis(run,rows)
        html=report_command.render_report(open_run(run))
        self.assertIn('HEAD reference only:',html)
        self.assertNotIn('>Analyzed commit<',html)
        rows[0]['metrics']['aggregate']['source_files_status']='failed'
        rows[0]['metrics']['aggregate']['source_files']=None
        self.write_analysis(run,rows)
        html=report_command.render_report(open_run(run))
        self.assertNotIn('No recognized source files were found',html)
        self.assertIn('Source measurement is unavailable',html)
        self.assertNotIn(str(run),html)

    def invoke(self,stream,*args):
        stderr=io.StringIO()
        with patch.object(sys,'argv',['metrolith',*map(str,args)]),redirect_stdout(stream),redirect_stderr(stderr):
            try:
                code=pipeline.main() or 0
            except SystemExit as exc:
                code=exc.code
        return code,stderr.getvalue()

    def test_cp1252_initial_stream_preserves_machine_unicode_and_exit(self):
        root=self.scratch()
        policy=root/'policy.json'
        policy.write_text(json.dumps(_policy(_rule('r','repository.source_files','gt',100),name='کیفیت café').as_dict()),encoding='utf-8')
        for format in ('json','sarif','text'):
            raw=io.BytesIO()
            stream=io.TextIOWrapper(raw,encoding='cp1252',errors='strict')
            code,err=self.invoke(stream,'check',self.shared_run,'--policy',policy,'--format',format)
            stream.flush()
            text=raw.getvalue().decode('utf-8')
            self.assertEqual(code,0,err)
            self.assertIn('کیفیت café',text)
            if format!='text':
                json.loads(text)

    def test_non_reconfigurable_unicode_stream_and_policy_validation(self):
        root=self.scratch()
        policy=root/'policy.json'
        policy.write_text(json.dumps(_policy(_rule('r','repository.source_files','gt',100),name='کیفیت café').as_dict()),encoding='utf-8')
        stream=io.StringIO()
        self.assertEqual(self.invoke(stream,'policy','validate',policy)[0],0)
        self.assertIn('کیفیت café',stream.getvalue())

    def test_closed_stdout_is_operational_and_preserves_written_verdict(self):
        root=self.scratch()
        policy=root/'policy.json'
        policy.write_text(json.dumps(_policy(_rule('r','repository.source_files','gt',100)).as_dict()))
        output=root/'result.json'
        stream=io.StringIO(); stream.close()
        code,err=self.invoke(stream,'check',self.shared_run,'--policy',policy,'--output',output,'--format','json')
        self.assertEqual(code,2)
        self.assertEqual(json.loads(output.read_text())['exit_code'],0)
        self.assertNotIn('Traceback',err)

    def test_real_broken_pipe_has_no_shutdown_traceback(self):
        import os
        env=os.environ.copy()
        env.pop('PYTHONIOENCODING',None)
        env.pop('PYTHONUTF8',None)
        process=subprocess.Popen([sys.executable,'-B','-c','import pipeline; pipeline.main()','policy','metrics'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=env)
        process.stdout.close()
        error=process.stderr.read()
        self.assertEqual(process.wait(timeout=20),2,error)
        self.assertNotIn(b'Traceback',error)
        self.assertNotIn(b'Exception ignored',error)

    def test_text_only_encoding_fallback_is_explicit_and_reversible(self):
        from modules.cli.transport import _Stream, DeliveryError
        class AsciiStream(io.StringIO):
            def write(self,text):
                text.encode('ascii')
                return super().write(text)
        target=AsciiStream()
        value='café فارسی \\u1234\n'
        _Stream(target).write(value)
        self.assertEqual(json.loads(target.getvalue().split('] ',1)[1]),value)
        with self.assertRaises(DeliveryError):
            _Stream(AsciiStream(),machine=True).write(value)


class SummaryTests(unittest.TestCase):
    def summary(self,mode='local_worktree_snapshot',failed=False):
        aggregate={key:None if failed else 0 for key in ('lines_of_code','source_files','classes_structs','methods_functions')}
        aggregate.update({key:'failed' if failed else 'complete' for key in ('loc_status','source_files_status','classes_structs_status','methods_functions_status')})
        return dict(run_directory=str(Path.cwd().parent/'external workspace'/'run'),summary_markdown=str(Path.cwd().parent/'external workspace'/'run'/'summary.md'),status='failed' if failed else 'completed',planned_repository_count=1,repository_count=1,success_count=0 if failed else 1,partial_count=0,failure_count=1 if failed else 0,results=[dict(subject_key='local:fixture',repository_name='fixture',source_mode=mode,analysis_status='failed' if failed else 'complete',acquisition={'analyzed_commit_sha':'a'*40},local_source_evidence={'modified_tracked_count':1,'untracked_included_count':2},metrics={'aggregate':aggregate},errors=[{'message':'revision missing-ref could not be resolved'}] if failed else [])])

    def render(self,**kwargs):
        out=io.StringIO()
        with redirect_stdout(out):
            pipeline._print_completion_summary(self.summary(**kwargs),single=True)
        return out.getvalue()

    def test_working_source_is_not_labelled_exact_commit(self):
        text=self.render()
        self.assertIn('HEAD reference',text)
        self.assertIn('working files',text)
        self.assertNotIn('Analyzed SHA',text)

    def test_unavailable_source_is_not_called_empty(self):
        text=self.render(failed=True)
        self.assertNotIn('No recognized source files',text)
        self.assertIn('missing-ref',text)
        self.assertIn('unavailable',text)

    def test_successfully_examined_empty_source_keeps_empty_message(self):
        self.assertIn('No recognized source files',self.render(mode='local_directory_snapshot'))

    def test_producer_not_applicable_empty_state_is_examined(self):
        summary=self.summary(mode='local_directory_snapshot')
        aggregate=summary['results'][0]['metrics']['aggregate']
        aggregate.update(source_files_status='not_applicable',inventory_status='complete')
        out=io.StringIO()
        with redirect_stdout(out):
            pipeline._print_completion_summary(summary,single=True)
        self.assertIn('No recognized source files were found',out.getvalue())
        self.assertIn('Source recognized no',out.getvalue())

    def test_readable_summary_preserves_unavailable_and_reference_identity(self):
        from modules.summary import render_summary
        failed=self.summary(failed=True)
        text=render_summary({},failed['results'],outcome='failed',integrity_status='failed')
        self.assertNotIn('No recognized source files',text)
        self.assertIn('unavailable',text)
        self.assertIn('HEAD reference',text)

    def test_printed_output_location_is_resolvable(self):
        text=self.render()
        path=text.split('Run directory    ',1)[1].splitlines()[0]
        self.assertEqual(Path(path).resolve(),Path(self.summary()['run_directory']))
