"""Execute the actual isolated upload guard against bounded fake index states."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import textwrap
from unittest.mock import patch
import urllib.error
import zipfile

import pytest

WORKFLOW = Path(__file__).resolve().parents[1] / '.github/workflows/publish-pypi.yml'
BASELINE = {
    'info': {'name': 'metrolith', 'version': '4.0.0'},
    'urls': [
        {'filename': 'metrolith-4.0.0-py3-none-any.whl', 'digests': {'sha256': 'bddeca14d71485529f54c5639c43d0ba193694a45a011b93336751ac85c38a5a'}},
        {'filename': 'metrolith-4.0.0.tar.gz', 'digests': {'sha256': '0c93efa7485c5a2a38ae5904069e8abbd2a5fadeec1a72b781248f6df955c95f'}},
    ],
}


@pytest.mark.parametrize('fault', [None, 'existing_version', 'index_500', 'missing_baseline',
                                  'wrong_baseline', 'wrong_source', 'wrong_run', 'wrong_attempt',
                                  'wrong_version', 'wrong_manifest', 'changed_wheel', 'extra_file',
                                  'changed_checksums'])
def test_patch_publication_guard(fault):
    text = WORKFLOW.read_text(encoding='utf-8').split('\n  publish:\n', 1)[1]
    code = textwrap.dedent(text.split("python3 - <<'PY'\n", 1)[1].split('\n          PY', 1)[0])
    with tempfile.TemporaryDirectory() as temporary, contextlib.chdir(temporary):
        root = Path('.')
        dist = root / 'dist'
        dist.mkdir()
        wheel = dist / 'metrolith-4.0.1-py3-none-any.whl'
        with zipfile.ZipFile(wheel, 'w') as archive:
            archive.writestr('metrolith-4.0.1.dist-info/METADATA', 'Name: metrolith\nVersion: 4.0.1\n')
        (dist / 'metrolith-4.0.1.tar.gz').write_bytes(b'bounded guard fixture')
        artifacts = [{'filename': p.name, 'size_bytes': p.stat().st_size,
                      'sha256': hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted(dist.iterdir())]
        manifest = {'source_sha': 'a' * 40, 'run_id': '123', 'run_attempt': '1',
                    'project': 'metrolith', 'version': '4.0.1', 'artifacts': artifacts}
        for name, value in [('source', 'source_sha'), ('run', 'run_id'), ('attempt', 'run_attempt'), ('version', 'version')]:
            if fault == 'wrong_' + name:
                manifest[value] = 'wrong'
        data = json.dumps(manifest).encode()
        (root / 'candidate-manifest.json').write_bytes(data)
        sums = ''.join(f"{a['sha256']}  dist/{a['filename']}\n" for a in artifacts)
        (root / 'SHA256SUMS').write_text(sums, encoding='ascii')
        env = {'EXPECTED_MANIFEST_SHA256': hashlib.sha256(data).hexdigest(),
               'SOURCE_SHA': 'a' * 40, 'GITHUB_RUN_ID': '123', 'GITHUB_RUN_ATTEMPT': '1'}
        if fault == 'wrong_manifest': env['EXPECTED_MANIFEST_SHA256'] = '0' * 64
        if fault == 'changed_wheel': wheel.write_bytes(b'changed')
        if fault == 'extra_file': (dist / 'extra.whl').write_bytes(b'extra')
        if fault == 'changed_checksums': (root / 'SHA256SUMS').write_text('wrong', encoding='ascii')

        def urlopen(url, timeout):
            assert timeout == 30
            if url.endswith('/4.0.0/json'):
                if fault == 'missing_baseline': raise urllib.error.HTTPError(url, 404, 'missing', {}, None)
                baseline = json.loads(json.dumps(BASELINE))
                if fault == 'wrong_baseline': baseline['urls'][0]['digests']['sha256'] = '0' * 64
                return io.BytesIO(json.dumps(baseline).encode())
            assert url == 'https://pypi.org/pypi/metrolith/4.0.1/json'
            if fault == 'existing_version': return io.BytesIO(b'{}')
            raise urllib.error.HTTPError(url, 500 if fault == 'index_500' else 404, 'fixture', {}, None)

        with patch.dict(os.environ, env), patch('urllib.request.urlopen', side_effect=urlopen):
            if fault is None:
                exec(compile(code, str(WORKFLOW), 'exec'), {})
            else:
                with pytest.raises((AssertionError, RuntimeError, urllib.error.HTTPError)):
                    exec(compile(code, str(WORKFLOW), 'exec'), {})
