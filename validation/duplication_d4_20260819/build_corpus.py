"""Materialize the D4 validation-only corpus deterministically.

The corpus extends the frozen D3-A relation corpus and the D3-B nesting
fixtures.  It is evidence only: no runtime product path imports this module.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
CORPUS = HERE / "corpus"
D3A_CORPUS = ROOT / "validation" / "duplication_d3a_20260818" / "corpus"
D3B_CORPUS = ROOT / "validation" / "duplication_d3b_20260819" / "corpus"


SOURCES = {
    "structural/role_value.py": '''def role_value(source):
    target = source
    first = target + 10
    second = first + 20
    third = second + 30
    fourth = third + 40
    fifth = fourth + 50
    sixth = fifth + 60
    seventh = sixth + 70
    return seventh
''',
    "structural/role_member.py": '''def role_member(source):
    holder.target = source
    first = holder.target + 10
    second = first + 20
    third = second + 30
    fourth = third + 40
    fifth = fourth + 50
    sixth = fifth + 60
    seventh = sixth + 70
    return seventh
''',
    "structural/fstring_a.py": '''async def fstring_alpha(source):
    first = await source.read(10)
    second = await source.read(20)
    third = first + second
    fourth = third * 30
    message = f"alpha:{first!r}:{second:>10}:{fourth}"
    await source.write(message)
    yield message
    return
''',
    "structural/fstring_b.py": '''async def fstring_beta(stream):
    one = await stream.read(101)
    two = await stream.read(202)
    three = one + two
    four = three * 303
    rendered = f"beta:{one!r}:{two:>999}:{four}"
    await stream.write(rendered)
    yield rendered
    return
''',
    "structural/fstring_shape_changed.py": '''async def fstring_changed(stream):
    one = await stream.read(101)
    two = await stream.read(202)
    three = one + two
    four = three * 303
    rendered = f"beta:{two!r}:{one:>999}:{four}"
    await stream.write(rendered)
    yield rendered
    return
''',
    "structural/template_a.js": '''async function* templateAlpha(source) {
  const first = await source.read(10);
  const second = await source.read(20);
  const third = first + second;
  const fourth = third * 30;
  const message = `alpha:${first}:${second}:${fourth}`;
  await source.write(message);
  yield message;
  return message;
}
''',
    "structural/template_b.js": '''async function* templateBeta(stream) {
  const one = await stream.read(101);
  const two = await stream.read(202);
  const three = one + two;
  const four = three * 303;
  const rendered = `beta:${one}:${two}:${four}`;
  await stream.write(rendered);
  yield rendered;
  return rendered;
}
''',
    "structural/template_shape_changed.js": '''async function* templateChanged(stream) {
  const one = await stream.read(101);
  const two = await stream.read(202);
  const three = one + two;
  const four = three * 303;
  const rendered = tag`beta:${one}:${two}:${four}`;
  await stream.write(rendered);
  yield rendered;
  return rendered;
}
''',
    "risks/external_member_a.py": '''def external_member_alpha(client, payload):
    first = client.fetch(payload, 10)
    second = client.authorize(first, 20)
    third = client.persist(second, 30)
    fourth = client.publish(third, 40)
    fifth = client.confirm(fourth, 50)
    sixth = client.audit(fifth, 60)
    seventh = client.finish(sixth, 70)
    return seventh
''',
    "risks/external_member_b.py": '''def external_member_beta(service, value):
    one = service.delete(value, 101)
    two = service.reject(one, 202)
    three = service.discard(two, 303)
    four = service.revoke(three, 404)
    five = service.cancel(four, 505)
    six = service.erase(five, 606)
    seven = service.abort(six, 707)
    return seven
''',
    "risks/syntax_augmented.py": '''def syntax_augmented(source):
    total = source
    total += transform(source, 10, 20, 30)
    total += transform(source, 40, 50, 60)
    total += transform(source, 70, 80, 90)
    total += transform(source, 100, 110, 120)
    total += transform(source, 130, 140, 150)
    total += transform(source, 160, 170, 180)
    total += transform(source, 190, 200, 210)
    return total
''',
    "risks/syntax_expanded.py": '''def syntax_expanded(value):
    amount = value
    amount = amount + convert(value, 101, 202, 303)
    amount = amount + convert(value, 404, 505, 606)
    amount = amount + convert(value, 707, 808, 909)
    amount = amount + convert(value, 1001, 1101, 1201)
    amount = amount + convert(value, 1301, 1401, 1501)
    amount = amount + convert(value, 1601, 1701, 1801)
    amount = amount + convert(value, 1901, 2001, 2101)
    return amount
''',
    "thresholds/below_floor_a.py": '''def below_floor_alpha(source):
    first = source + 10
    second = first + 20
    return second
''',
    "thresholds/below_floor_b.py": '''def below_floor_beta(value):
    one = value + 101
    two = one + 202
    return two
''',
}


EXPECTATIONS = {
    "format": "archlens-duplication-d4-expectations",
    "format_version": "1.0.0",
    "authored_before_d4_execution": True,
    "languages": ["Go", "Java", "JavaScript", "Python", "TypeScript"],
    "lexical": {
        "match_against_base": ["exact", "whitespace", "comment"],
        "non_match_against_base": [
            "identifier", "literal", "operator", "reordered", "added", "removed"
        ],
        "portability_matches": [
            ["java/base.java", "java/crlf.java"],
            ["go/base.go", "go/lone_cr.go"],
            ["typescript/base.ts", "typescript/bom.ts"],
            ["python/café/日本語_unicode_a.py", "python/café/日本語_unicode_b.py"],
        ],
        "portability_non_matches": [["python/unicode_nfc.py", "python/unicode_nfd.py"]],
    },
    "structural": {
        "match_against_base": ["exact", "whitespace", "comment", "identifier", "literal"],
        "non_match_against_base": ["operator", "added", "removed"],
        "non_relations": [
            ["relations/equality_distinct.py", "relations/equality_repeated.py", "identifier equality pattern"],
            ["relations/literal_integer.py", "relations/literal_string.py", "literal class"],
            ["relations/child_left_right.py", "relations/child_right_left.py", "child order"],
            ["structural/role_value.py", "structural/role_member.py", "identifier role and AST shape"],
            ["structural/fstring_a.py", "structural/fstring_shape_changed.py", "f-string child order"],
            ["structural/template_a.js", "structural/template_shape_changed.js", "tagged template shape"],
            ["risks/syntax_augmented.py", "risks/syntax_expanded.py", "equivalent-looking syntax is retained"],
        ],
        "match_relations": [
            ["structural/fstring_a.py", "structural/fstring_b.py", "f-string raw chunks and literal values abstracted"],
            ["structural/template_a.js", "structural/template_b.js", "template raw chunks and literal values abstracted"],
            ["risks/external_member_a.py", "risks/external_member_b.py", "documented external-member false-positive risk"],
        ],
    },
    "grouping": {
        "same_file": ["python/same_file.py"],
        "cross_file": ["python/base.py", "python/exact.py"],
        "mixed": ["python/base.py", "python/same_file.py"],
        "multiple_occurrences": ["python/base.py", "python/exact.py", "python/same_file.py"],
        "nested_suppression": ["nested/outer_if_a.py", "nested/outer_if_b.py"],
        "nested_third_retained": [
            "nested/outer_if_a.py", "nested/outer_if_b.py", "nested/inner_third.py"
        ],
        "inner_only": ["nested/inner_only_a.py", "nested/inner_only_b.py"],
        "siblings": ["nested/siblings_a.py", "nested/siblings_b.py"],
        "overlap_fixture": "synthetic_overlap.json",
    },
    "below_floor": ["thresholds/below_floor_a.py", "thresholds/below_floor_b.py"],
    "malformed_unavailable": [
        "go/malformed.go", "java/malformed.java", "javascript/malformed.js",
        "python/malformed.py", "typescript/malformed.ts"
    ],
}


OVERLAP = {
    "format": "archlens-duplication-d4-overlap",
    "format_version": "1.0.0",
    "partial": [
        {"path": "synthetic/overlap.py", "start_byte": 10, "end_byte": 90},
        {"path": "synthetic/overlap.py", "start_byte": 50, "end_byte": 130},
    ],
    "identical": [
        {"path": "synthetic/identical.py", "start_byte": 10, "end_byte": 90, "unit_kind": "callable_body"},
        {"path": "synthetic/identical.py", "start_byte": 10, "end_byte": 90, "unit_kind": "branch_body"},
    ],
}


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def main() -> None:
    if CORPUS.exists():
        if CORPUS.parent != HERE or CORPUS.name != "corpus":
            raise RuntimeError(f"refusing to replace unexpected corpus path: {CORPUS}")
        shutil.rmtree(CORPUS)
    shutil.copytree(D3A_CORPUS, CORPUS)
    shutil.copytree(D3B_CORPUS / "nested", CORPUS / "nested")
    for relative, source in SOURCES.items():
        _write_text(CORPUS / relative, source)

    _write_text(HERE / "expectations.json", json.dumps(EXPECTATIONS, ensure_ascii=False, indent=2) + "\n")
    _write_text(HERE / "synthetic_overlap.json", json.dumps(OVERLAP, indent=2) + "\n")
    hashes = {
        path.relative_to(CORPUS).as_posix(): {
            "bytes": len(path.read_bytes()),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        for path in sorted(CORPUS.rglob("*"))
        if path.is_file()
    }
    _write_text(
        HERE / "corpus_hashes.json",
        json.dumps(hashes, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )


if __name__ == "__main__":
    main()
