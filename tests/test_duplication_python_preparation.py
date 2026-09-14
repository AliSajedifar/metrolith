"""Token coordinates are local to one extraction, independent of body count."""
import io
import tokenize

import pytest

from modules.duplication import candidates as implementation
from modules.duplication.model import CandidateExtractionStatus
from modules.inventory import RepositoryInventory
from modules.source_frontend import ParserRegistry, select_syntax


def selected(tmp_path, text):
    (tmp_path / 'subject.py').write_bytes(text.encode('utf-8'))
    inventory = RepositoryInventory(tmp_path)
    record = next(item for item in inventory if item.relative_path == 'subject.py')
    return select_syntax(record, inventory.read_bytes(record), ParserRegistry(),
                         python_text=inventory.read_text(record))


@pytest.mark.parametrize('newline', ['\n', '\r\n'])
@pytest.mark.parametrize('final_newline', [False, True])
def test_unicode_coordinates_and_body_local_admission(tmp_path, newline, final_newline):
    lines = ['def calculate():', '    café = 1 + 2 + 3 + 4 + 5', '    résultat = café + 2 + 3 + 4 + 5',
             '    x = résultat * 3 + 4 + 5 + 6', '    y = x + 4 + 5 + 6 + 7',
             '    café += 1', '    résultat += 2', '    x += 3', '    return café + résultat + x + y']
    text = newline.join(lines) + (newline if final_newline else '')
    syntax = selected(tmp_path, text)
    result = implementation.extract_candidates(syntax)
    assert result.status is CandidateExtractionStatus.COMPLETE
    candidate, = result.candidates
    assert candidate.span.start_byte == syntax.selected_source.index('café'.encode())
    assert candidate.span.end_byte == syntax.selected_source.index(b' + y') + 4
    assert (candidate.span.start_line, candidate.span.end_line) == (2, 9)
    assert candidate.immediate_statement_count == 8
    assert candidate.duplicated_nloc == 8


def test_each_significant_token_is_positioned_once_per_extraction(tmp_path, monkeypatch):
    text = ''.join(f'def f{i}():\n    x = {i} + 1 + 2 + 3 + 4\n    y = x + 1 + 2 + 3 + 4\n    z = y + 2 + 3 + 4 + 5\n    q = z + 3 + 4 + 5 + 6\n    x += 1\n    y += 2\n    z += 3\n    return x + y + z + q\n' for i in range(12))
    syntax = selected(tmp_path, text)
    calls = []
    original = implementation._python_position_to_byte
    def observed(*args):
        calls.append(args[-2:])
        return original(*args)
    monkeypatch.setattr(implementation, '_python_position_to_byte', observed)
    result = implementation.extract_candidates(syntax)
    significant = sum(token.type not in implementation.PYTHON_TRIVIA
                      for token in tokenize.generate_tokens(io.StringIO(syntax.evidence_text).readline))
    assert len(result.candidates) == 12
    assert len(calls) == significant * 2
    assert implementation.extract_candidates(syntax) == result
    assert len(calls) == significant * 4  # A later call prepares its own coordinates.


@pytest.mark.parametrize('text', ['', '# comment only\n', 'x = 1\n', 'def broken(:\n'])
def test_empty_small_and_partial_sources(tmp_path, text):
    result = implementation.extract_candidates(selected(tmp_path, text))
    assert not result.candidates
    if text.startswith('def broken'):
        assert result.status is CandidateExtractionStatus.UNAVAILABLE


def test_same_path_with_new_text_gets_new_coordinates(tmp_path):
    body = 'def f():\n    a = 1 + 2 + 3 + 4 + 5\n    b = a + 2 + 3 + 4 + 5\n    c = b + 3 + 4 + 5 + 6\n    d = c + 4 + 5 + 6 + 7\n    a += 1\n    b += 2\n    c += 3\n    return a + b + c + d\n'
    first = implementation.extract_candidates(selected(tmp_path, body))
    prefix = '# café\n'
    second = implementation.extract_candidates(selected(tmp_path, prefix + body))
    assert second.candidates[0].span.start_byte == first.candidates[0].span.start_byte + len(prefix.encode())
    assert second.candidates[0].span.start_line == first.candidates[0].span.start_line + 1
