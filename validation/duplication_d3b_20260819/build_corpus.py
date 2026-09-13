"""Materialize the frozen D3-B grouping corpus deterministically."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
CORPUS = HERE / "corpus"
D3A = ROOT / "validation" / "duplication_d3a_20260818" / "corpus"

LANGUAGES = {
    "go": "go",
    "java": "java",
    "javascript": "js",
    "python": "py",
    "typescript": "ts",
}
VARIANTS = ("base", "exact", "identifier", "literal", "operator", "malformed")

NESTED_SOURCES = {
    "nested/outer_if_a.py": '''def outer_alpha(source, enabled):
    seed = source + 1
    first = seed * 2
    if enabled:
        inner_a = source + 10
        inner_b = source + 20
        inner_c = source + 30
        inner_d = source + 40
        inner_e = source + 50
        inner_f = source + 60
        inner_g = source + 70
        inner_h = source + 80
        inner_i = source + 90
    tail_a = first + 3
    tail_b = tail_a * 4
    tail_c = tail_b - 5
    return tail_c
''',
    "nested/outer_if_b.py": '''def outer_beta(value, active):
    origin = value + 101
    primary = origin * 202
    if active:
        nested_a = value + 110
        nested_b = value + 120
        nested_c = value + 130
        nested_d = value + 140
        nested_e = value + 150
        nested_f = value + 160
        nested_g = value + 170
        nested_h = value + 180
        nested_i = value + 190
    ending_a = primary + 303
    ending_b = ending_a * 404
    ending_c = ending_b - 505
    return ending_c
''',
    "nested/inner_third.py": '''def independent_inner(value, active):
    preface = value / 7
    if active:
        nested_a = value + 210
        nested_b = value + 220
        nested_c = value + 230
        nested_d = value + 240
        nested_e = value + 250
        nested_f = value + 260
        nested_g = value + 270
        nested_h = value + 280
        nested_i = value + 290
    ending_a = preface ** 2
    ending_b = ending_a // 3
    return ending_b
''',
    "nested/inner_only_a.py": '''def inner_only_alpha(source, enabled):
    before = source + 1
    if enabled:
        inner_a = source + 10
        inner_b = source + 20
        inner_c = source + 30
        inner_d = source + 40
        inner_e = source + 50
        inner_f = source + 60
        inner_g = source + 70
        inner_h = source + 80
        inner_i = source + 90
    after_a = before + 2
    after_b = after_a * 3
    return after_b
''',
    "nested/inner_only_b.py": '''def inner_only_beta(value, active):
    before = value - 101
    if active:
        nested_a = value + 110
        nested_b = value + 120
        nested_c = value + 130
        nested_d = value + 140
        nested_e = value + 150
        nested_f = value + 160
        nested_g = value + 170
        nested_h = value + 180
        nested_i = value + 190
    after_a = before + 202
    after_b = after_a * 303
    return after_b
''',
    "nested/outer_loop_a.py": '''def loop_outer_alpha(source, items):
    seed = source + 1
    first = seed * 2
    for item in items:
        loop_a = item + 10
        loop_b = item + 20
        loop_c = item + 30
        loop_d = item + 40
        loop_e = item + 50
        loop_f = item + 60
        loop_g = item + 70
        loop_h = item + 80
        loop_i = item + 90
    tail_a = first + 3
    tail_b = tail_a * 4
    return tail_b
''',
    "nested/outer_loop_b.py": '''def loop_outer_beta(value, values):
    origin = value + 101
    primary = origin * 202
    for element in values:
        nested_a = element + 110
        nested_b = element + 120
        nested_c = element + 130
        nested_d = element + 140
        nested_e = element + 150
        nested_f = element + 160
        nested_g = element + 170
        nested_h = element + 180
        nested_i = element + 190
    ending_a = primary + 303
    ending_b = ending_a * 404
    return ending_b
''',
    "nested/siblings_a.py": '''def sibling_alpha(source, first_flag, second_flag):
    seed = source + 1
    if first_flag:
        one_a = source + 10
        one_b = source + 20
        one_c = source + 30
        one_d = source + 40
        one_e = source + 50
        one_f = source + 60
        one_g = source + 70
        one_h = source + 80
        one_i = source + 90
    if second_flag:
        two_a = transform(source, 10)
        two_b = transform(source, 20)
        two_c = transform(source, 30)
        two_d = transform(source, 40)
        two_e = transform(source, 50)
        two_f = transform(source, 60)
        two_g = transform(source, 70)
        two_h = transform(source, 80)
        two_i = transform(source, 90)
    tail = seed + 2
    return tail
''',
    "nested/siblings_b.py": '''def sibling_beta(value, left_flag, right_flag):
    origin = value - 101
    if left_flag:
        left_a = value + 110
        left_b = value + 120
        left_c = value + 130
        left_d = value + 140
        left_e = value + 150
        left_f = value + 160
        left_g = value + 170
        left_h = value + 180
        left_i = value + 190
    if right_flag:
        right_a = convert(value, 110)
        right_b = convert(value, 120)
        right_c = convert(value, 130)
        right_d = convert(value, 140)
        right_e = convert(value, 150)
        right_f = convert(value, 160)
        right_g = convert(value, 170)
        right_h = convert(value, 180)
        right_i = convert(value, 190)
    ending = origin + 202
    return ending
''',
}

EXPECTATIONS = {
    "format": "archlens-duplication-d3b-expectations",
    "format_version": "1.0.0",
    "languages": ["Go", "Java", "JavaScript", "Python", "TypeScript"],
    "core_variants": ["base", "exact", "identifier", "literal"],
    "operator_non_clone": "operator",
    "distributions": {
        "same_file": ["python/same_file.py"],
        "cross_file": ["python/base.py", "python/exact.py"],
        "mixed": ["python/base.py", "python/same_file.py"],
    },
    "nested": {
        "outer_if": ["nested/outer_if_a.py", "nested/outer_if_b.py"],
        "outer_if_with_third_inner": [
            "nested/outer_if_a.py",
            "nested/outer_if_b.py",
            "nested/inner_third.py",
        ],
        "inner_only": ["nested/inner_only_a.py", "nested/inner_only_b.py"],
        "outer_loop": ["nested/outer_loop_a.py", "nested/outer_loop_b.py"],
        "siblings": ["nested/siblings_a.py", "nested/siblings_b.py"],
    },
    "malformed_unavailable": [
        "go/malformed.go",
        "java/malformed.java",
        "javascript/malformed.js",
        "python/malformed.py",
        "typescript/malformed.ts",
    ],
    "synthetic_overlap_fixture": "synthetic_overlap.json",
}

SYNTHETIC_OVERLAP = {
    "format": "archlens-duplication-d3b-synthetic-overlap",
    "format_version": "1.0.0",
    "partial_overlap": [
        {"path": "synthetic/overlap.py", "start_byte": 10, "end_byte": 90},
        {"path": "synthetic/overlap.py", "start_byte": 50, "end_byte": 130},
    ],
    "identical_span": [
        {"path": "synthetic/identical.py", "start_byte": 10, "end_byte": 90, "unit_kind": "callable_body"},
        {"path": "synthetic/identical.py", "start_byte": 10, "end_byte": 90, "unit_kind": "branch_body"},
    ],
}


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def main() -> None:
    if CORPUS.exists():
        shutil.rmtree(CORPUS)
    for folder, extension in LANGUAGES.items():
        for variant in VARIANTS:
            source = D3A / folder / f"{variant}.{extension}"
            destination = CORPUS / folder / source.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
    for extra in (
        "python/same_file.py",
        "python/café/日本語_unicode_a.py",
        "python/café/日本語_unicode_b.py",
    ):
        destination = CORPUS / extra
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(D3A / extra, destination)
    for relative, source in NESTED_SOURCES.items():
        _write(CORPUS / relative, source)

    _write(HERE / "expectations.json", json.dumps(EXPECTATIONS, ensure_ascii=False, indent=2) + "\n")
    _write(
        HERE / "synthetic_overlap.json",
        json.dumps(SYNTHETIC_OVERLAP, ensure_ascii=False, indent=2) + "\n",
    )
    hashes = {
        path.relative_to(HERE).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(CORPUS.rglob("*"))
        if path.is_file()
    }
    hashes["synthetic_overlap.json"] = hashlib.sha256(
        (HERE / "synthetic_overlap.json").read_bytes()
    ).hexdigest()
    _write(HERE / "corpus_hashes.json", json.dumps(hashes, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
