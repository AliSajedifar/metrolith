"""Check output safety and truthful, serialization-neutral presentation."""
import io
import json
import os
from argparse import Namespace
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from unittest.mock import patch

from modules.cli import check_command
from modules.policy.check import CheckFailed, FAILURE_EVALUATION_ERROR
from tests.test_policy_v2_check import RunFixture, _policy, _rule, _validate_check_result


class CheckOutputTests(RunFixture):
    def args(self, root, *, run=None, **overrides):
        policy=root/'policy.json'
        if not policy.exists():
            policy.write_text(json.dumps(_policy(_rule('r','repository.source_files','gt',100)).as_dict()),encoding='utf-8')
        values=dict(run_directory=run or self.shared_run,policy=policy,format='json',output=None,overwrite=False)
        values.update(overrides)
        return Namespace(**values)

    def call(self,args):
        out,err=io.StringIO(),io.StringIO()
        with redirect_stdout(out),redirect_stderr(err):
            code=check_command.handle(args)
        return code,out.getvalue(),err.getvalue()

    def test_run_inputs_and_new_nested_destination_are_protected_before_evaluation(self):
        run=self.copy_run()
        root=self.scratch()
        for name in ('analysis.json','run_status.json','new/nested/result.json'):
            for overwrite in (False,True):
                destination=run/name
                before=destination.read_bytes() if destination.exists() else None
                args=self.args(root,run=run,output=destination,overwrite=overwrite)
                with patch.object(check_command,'evaluate_check',side_effect=AssertionError('evaluated before protection')):
                    code,out,err=self.call(args)
                self.assertEqual(code,2,(out,err))
                self.assertEqual(destination.read_bytes() if destination.exists() else None,before)

    def test_policy_and_supplied_evidence_and_sidecar_protected_on_error_paths(self):
        root=self.scratch()
        args=self.args(root)
        for key in ('policy','hotspots','duplication','evidence_receipt','baseline'):
            target=root/(key+'.json')
            target.write_text('{',encoding='utf-8')
            opts={key:target,'output':target,'overwrite':True}
            code,_,_=self.call(self.args(root,**opts))
            self.assertEqual(code,2)
            self.assertEqual(target.read_bytes(),b'{')
        sidecar=root/'baseline.json.source-run'
        target=sidecar/'deep/new.json'
        code,_,_=self.call(self.args(root,baseline=root/'baseline.json',output=target,overwrite=True))
        self.assertEqual(code,2)
        self.assertFalse(target.exists())

    def test_ordinary_existing_output_requires_explicit_overwrite(self):
        root=self.scratch()
        target=root/'check.json'
        target.write_bytes(b'owner data')
        self.assertEqual(self.call(self.args(root,output=target))[0],2)
        self.assertEqual(target.read_bytes(),b'owner data')
        self.assertEqual(self.call(self.args(root,output=target,overwrite=True))[0],0)
        self.assertEqual(json.loads(target.read_text())['verdict'],'pass')

    def test_passing_violating_and_evaluation_error_new_outputs_keep_format(self):
        root=self.scratch()
        for format in ('text','json','sarif'):
            for threshold,exit_code in ((100,0),(0,1)):
                policy=root/'policy.json'
                policy.write_text(json.dumps(_policy(_rule('r','repository.source_files','gt',threshold)).as_dict()))
                target=root/f'{format}-{threshold}.json'
                code,out,_=self.call(self.args(root,format=format,output=target))
                self.assertEqual(code,exit_code)
                payload=json.loads(target.read_text(encoding='utf-8'))
                if format=='sarif':
                    self.assertEqual(payload['version'],'2.1.0')
                else:
                    self.assertEqual(payload['exit_code'],exit_code)
                if format!='text':
                    self.assertEqual(json.loads(out),payload)
            target=root/f'{format}-error.json'
            with patch.object(check_command,'evaluate_check',side_effect=CheckFailed(FAILURE_EVALUATION_ERROR,'synthetic evaluation failure')):
                code,_,_=self.call(self.args(root,format=format,output=target))
            self.assertEqual(code,2)
            self.assertTrue(target.exists())

    def test_hardlink_alias_and_relative_dot_segments_are_protected(self):
        root=self.scratch()
        args=self.args(root)
        alias=root/'alias.json'
        os.link(args.policy,alias)
        before=args.policy.read_bytes()
        self.assertEqual(self.call(self.args(root,output=alias,overwrite=True))[0],2)
        self.assertEqual(args.policy.read_bytes(),before)
        self.assertEqual(alias.read_bytes(),before)
        self.assertEqual(self.call(self.args(root,output=root/'sub'/ '..'/'policy.json',overwrite=True))[0],2)

    def test_native_symlink_and_dangling_output_and_parent_alias(self):
        root=self.scratch()
        args=self.args(root)
        alias=root/'alias.json'
        try:
            os.symlink(args.policy,alias)
        except OSError as exc:
            self.skipTest(f'Native symlink capability unavailable: {exc}')
        self.assertEqual(self.call(self.args(root,output=alias,overwrite=True))[0],2)
        alias.unlink()
        os.symlink(root/'missing',alias)
        self.assertEqual(self.call(self.args(root,output=alias))[0],2)
        parent=root/'run-link'
        os.symlink(self.shared_run,parent,target_is_directory=True)
        self.assertEqual(self.call(self.args(root,output=parent/'new.json'))[0],2)

    def test_concurrent_creation_at_publication_survives(self):
        root=self.scratch()
        target=root/'race.json'
        real_link=os.link
        def race(source,destination,*args,**kwargs):
            if Path(destination)==target:
                target.write_bytes(b'concurrent owner')
            return real_link(source,destination,*args,**kwargs)
        with patch('os.link',side_effect=race):
            code,_,_=self.call(self.args(root,output=target))
        self.assertEqual(code,2)
        self.assertEqual(target.read_bytes(),b'concurrent owner')
        self.assertEqual(list(root.glob('.race.json.*')),[])

    def test_output_io_failure_is_operational_and_does_not_retry_emission(self):
        root=self.scratch()
        target=root/'result.json'
        with patch('os.fsync',side_effect=OSError('disk failure')):
            self.assertEqual(self.call(self.args(root,output=target))[0],2)
        self.assertFalse(target.exists())

    def test_temporary_cleanup_failure_is_operational(self):
        root=self.scratch()
        original=Path.unlink
        def deny(path,*args,**kwargs):
            if path.name.startswith('.result.json.'):
                raise PermissionError('cleanup denied')
            return original(path,*args,**kwargs)
        with patch.object(Path,'unlink',deny):
            self.assertEqual(self.call(self.args(root,output=root/'result.json'))[0],2)
        self.assertEqual(json.loads((root/'result.json').read_text())['exit_code'],0)

    def test_windows_case_and_junction_aliases(self):
        if os.name!='nt':
            self.skipTest('Windows path-equivalence boundary')
        import subprocess
        root=self.scratch()
        args=self.args(root)
        before=args.policy.read_bytes()
        self.assertEqual(self.call(self.args(root,output=root/'POLICY.JSON',overwrite=True))[0],2)
        junction=root/'run-junction'
        command="New-Item -ItemType Junction -Path '"+str(junction).replace("'","''")+"' -Target '"+str(self.shared_run).replace("'","''")+"' | Out-Null"
        result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',command],capture_output=True)
        if result.returncode:
            self.skipTest(f'Junction capability unavailable: {result.stderr!r}')
        self.assertEqual(self.call(self.args(root,output=junction/'new.json',overwrite=True))[0],2)
        self.assertEqual(args.policy.read_bytes(),before)

    def test_bundle_hardlink_and_protected_inputs_for_each_policy_outcome(self):
        root=self.scratch()
        run=self.copy_run()
        original=(run/'analysis.json').read_bytes()
        alias=root/'analysis-alias.json'
        os.link(run/'analysis.json',alias)
        for threshold in (100,0,None):
            args=self.args(root,run=run)
            args.policy.write_text('{' if threshold is None else json.dumps(_policy(_rule('r','repository.source_files','gt',threshold)).as_dict()))
            for destination in (run/'analysis.json',run/'run_status.json',alias,args.policy):
                before=destination.read_bytes()
                self.assertEqual(self.call(self.args(root,run=run,output=destination,overwrite=True))[0],2)
                self.assertEqual(destination.read_bytes(),before)
        self.assertEqual((run/'analysis.json').read_bytes(),original)


class PartialPresentationTests(RunFixture):
    def test_partial_pass_is_disclosed_without_changing_json(self):
        run=self.copy_run()
        rows=self.analysis(run)
        rows[0]['metrics']['aggregate']['methods_functions_status']='partial'
        rows[0]['core_metric_status']='partial'
        self.write_analysis(run,rows)
        policy=_policy(_rule('r','repository.methods_functions','gt',100))
        result=self.evaluate(policy,run)
        self.assertEqual(result['exit_code'],0)
        self.assertEqual(_validate_check_result(result),[])
        raw=json.dumps(result,sort_keys=True)
        rendered=check_command.render_text(result)
        self.assertIn('partial observation',rendered.lower())
        self.assertIn('evaluated',rendered.lower())
        self.assertEqual(json.dumps(result,sort_keys=True),raw)
        historical=json.loads(raw)
        self.assertIn('unavailable',check_command.render_text(historical).lower())

    def test_partial_run_with_complete_gated_family_does_not_claim_partial_evaluation(self):
        run=self.copy_run()
        rows=self.analysis(run)
        rows[0]['metrics']['aggregate']['methods_functions_status']='partial'
        rows[0]['core_metric_status']='partial'
        self.write_analysis(run,rows)
        result=self.evaluate(_policy(_rule('r','repository.source_files','gt',100)),run)
        rendered=check_command.render_text(result).lower()
        self.assertIn('aggregate incompleteness',rendered)
        self.assertIn('no gated partial observation was evaluated',rendered)
        self.assertEqual(result['exit_code'],0)

    def test_warning_is_explicitly_nonblocking(self):
        result=self.evaluate(_policy(_rule('r','repository.source_files','gt',0,severity='warning')))
        self.assertEqual(result['exit_code'],0)
        self.assertIn('1 nonblocking warning',check_command.render_text(result).lower())
