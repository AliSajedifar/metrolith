"""Fail-closed guards for the optional publishing boundary; no upload tests."""

import re
import os
import subprocess
import sys
import tempfile
import unittest
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / '.github/workflows/publish-pypi.yml'


def evaluate_guard(expression, publish=None, repository='AliSajedifar/metrolith',
                   ref='refs/heads/main', event='workflow_dispatch'):
    # Evaluate the actual workflow's deliberately small conjunction. All inputs
    # here are GitHub's typed Boolean or missing/null, never form strings.
    context = {'inputs.publish': publish, 'github.repository': repository,
               'github.ref': ref, 'github.event_name': event}
    results = []
    for clause in expression.split(' && '):
        left, right = clause.split(' == ')
        value = context[left]
        results.append(value is True if right == 'true' else value == right.strip("'"))
    return all(results)


class PyPIPreparationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding='utf-8')
        cls.prepare, cls.publish = cls.text.split('\n  publish:\n', 1)
        cls.guard = re.search(r'if: \$\{\{ (.*?) \}\}', cls.publish).group(1)

    def test_default_and_only_manual_trigger(self):
        trigger = self.text.split('\non:\n')[1].split('\npermissions:\n')[0]
        self.assertEqual(re.findall(r'^  (\w+):', trigger, re.M), ['workflow_dispatch'])
        self.assertIn('type: boolean\n        default: false', trigger)
        self.assertNotIn('github.event.inputs', self.text)

    def test_false_and_omitted_cannot_publish(self):
        self.assertFalse(evaluate_guard(self.guard, publish=False))
        self.assertFalse(evaluate_guard(self.guard))
        self.assertTrue(evaluate_guard(self.guard, publish=True))
        for kwargs in ({'repository': 'other/metrolith'}, {'ref': 'refs/heads/topic'},
                       {'ref': 'refs/tags/main'}, {'event': 'push'}):
            self.assertFalse(evaluate_guard(self.guard, publish=True, **kwargs))

    def test_privilege_and_upload_isolation(self):
        self.assertNotIn('id-token:', self.prepare)
        self.assertNotIn('gh-action-pypi-publish', self.prepare)
        self.assertIn('needs: prepare', self.publish)
        self.assertIn('environment:\n      name: pypi', self.publish)
        self.assertIn('permissions:\n      id-token: write', self.publish)
        for forbidden in ('actions/checkout@', 'pip install', 'tools/prepare_pypi.py',
                          'password:', 'user:', 'skip-existing:', 'repository-url:'):
            self.assertNotIn(forbidden, self.publish)

    def test_source_and_same_run_bytes_are_bound(self):
        self.assertIn('ref: ${{ github.sha }}', self.prepare)
        self.assertIn('persist-credentials: false', self.prepare)
        self.assertIn('artifact-ids: ${{ needs.prepare.outputs.artifact-id }}', self.publish)
        self.assertIn('EXPECTED_MANIFEST_SHA256', self.publish)
        self.assertIn('sha256sum --check --strict SHA256SUMS', self.publish)
        self.assertIn('cancel-in-progress: false', self.text)
        for action in re.findall(r'uses: (.+)', self.text):
            self.assertRegex(action, r'^[\w/-]+@[0-9a-f]{40}(?: # .*)?$')

    @unittest.skipIf(os.name == 'nt', 'POSIX virtualenv interpreter symlink regression')
    def test_validation_interpreter_keeps_its_virtualenv(self):
        sys.path.insert(0, str(ROOT / 'tools'))
        try:
            from prepare_pypi import interpreter_path
        finally:
            sys.path.pop(0)
        with tempfile.TemporaryDirectory() as temporary:
            environment = Path(temporary) / 'validation'
            venv.EnvBuilder(with_pip=False, symlinks=True).create(environment)
            python = interpreter_path(environment / 'bin/python')
            result = subprocess.check_output([str(python), '-c', 'import sys; print(sys.prefix)'], text=True)
            self.assertEqual(Path(result.strip()), environment)


if __name__ == '__main__':
    unittest.main()
