"""Independently check the hand-authored Go complexity expectations.

Deliberately does NOT import ArchLens. The whole value of the expectations file
is that it was derived from the contract by hand; checking it with the
implementation it is meant to test would be circular. This script uses its own
small Go line scanner so an arithmetic slip in the expectations is caught now
rather than during C2 adjudication.

Checks:
  1. expectations.json is valid JSON;
  2. every declared start_line really begins that callable's declaration;
  3. every declared end_line is the closing brace of a top-level declaration;
  4. the declared `nloc` equals an independent non-blank / non-comment-only
     count over [start_line, end_line];
  5. `span_line_count`, where declared, equals end_line - start_line + 1;
  6. cyclomatic_complexity == 1 + decision_point_count + boolean_operator_count;
  7. the sum of the `increment` values equals decision + boolean counts;
  8. max_condition_operator_count <= boolean_operator_count.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

FAILURES: list[str] = []


def fail(message: str) -> None:
    FAILURES.append(message)
    print(f"  [FAIL] {message}")


def ok(message: str) -> None:
    print(f"  [OK  ] {message}")


def classify_lines(text: str) -> list[str]:
    """Return 'code' | 'comment' | 'blank' per physical line.

    Clean-room Go scanner with string, raw-string and comment states. A line
    holding any non-comment, non-whitespace character is code; a line whose only
    content is comment text is a comment; anything else is blank.
    """
    lines = text.split("\n")
    states: list[str] = []
    in_block_comment = False
    in_raw_string = False

    for line in lines:
        has_code = False
        has_comment = False
        index = 0
        in_string = False
        in_char = False
        length = len(line)

        while index < length:
            char = line[index]
            two = line[index : index + 2]

            if in_block_comment:
                if two == "*/":
                    in_block_comment = False
                    index += 2
                    continue
                has_comment = True
                index += 1
                continue

            if in_raw_string:
                # A raw string spans lines; its content is code, and a `//`
                # inside it is not a comment.
                has_code = True
                if char == "`":
                    in_raw_string = False
                index += 1
                continue

            if in_string:
                has_code = True
                if char == "\\":
                    index += 2
                    continue
                if char == '"':
                    in_string = False
                index += 1
                continue

            if in_char:
                has_code = True
                if char == "\\":
                    index += 2
                    continue
                if char == "'":
                    in_char = False
                index += 1
                continue

            if two == "//":
                has_comment = True
                break
            if two == "/*":
                in_block_comment = True
                has_comment = True
                index += 2
                continue
            if char == "`":
                in_raw_string = True
                has_code = True
                index += 1
                continue
            if char == '"':
                in_string = True
                has_code = True
                index += 1
                continue
            if char == "'":
                in_char = True
                has_code = True
                index += 1
                continue
            if not char.isspace():
                has_code = True
            index += 1

        if has_code:
            states.append("code")
        elif has_comment:
            states.append("comment")
        else:
            states.append("blank")

    return states


def main() -> int:
    source_path = ROOT / "constructs.go"
    expectations_path = ROOT / "expectations.json"

    text = source_path.read_text(encoding="utf-8")
    lines = text.split("\n")
    states = classify_lines(text)

    try:
        document = json.loads(expectations_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"  [FAIL] expectations.json is not valid JSON: {exc}")
        return 1
    ok("expectations.json parses")

    callables = document["callables"]
    print(f"\n  {len(callables)} callables declared; "
          f"file_level.callable_count = {document['file_level']['callable_count']}")
    if len(callables) != document["file_level"]["callable_count"]:
        fail(
            f"declared callable_count {document['file_level']['callable_count']} "
            f"!= {len(callables)} callable entries"
        )
    else:
        ok("callable entry count matches file_level.callable_count")

    print()
    for entry in callables:
        name = entry["name"]
        start = entry["start_line"]
        end = entry["end_line"]
        expected = entry["expected"]

        start_text = lines[start - 1]
        if not start_text.startswith("func ") or name not in start_text:
            fail(f"{name}: line {start} is not its declaration: {start_text!r}")

        end_text = lines[end - 1]
        if end_text.strip() != "}":
            fail(f"{name}: line {end} is not a closing brace: {end_text!r}")

        span = states[start - 1 : end]
        computed_nloc = sum(1 for state in span if state == "code")
        if computed_nloc != expected["nloc"]:
            fail(
                f"{name}: nloc expected {expected['nloc']}, "
                f"independent scan says {computed_nloc} "
                f"(span {start}-{end}, {len(span)} lines)"
            )

        declared_span = expected.get("span_line_count")
        if declared_span is not None and declared_span != end - start + 1:
            fail(
                f"{name}: span_line_count {declared_span} != {end - start + 1}"
            )

        derived = 1 + expected["decision_point_count"] + expected["boolean_operator_count"]
        if derived != expected["cyclomatic_complexity"]:
            fail(
                f"{name}: cyclomatic {expected['cyclomatic_complexity']} != "
                f"1 + {expected['decision_point_count']} + "
                f"{expected['boolean_operator_count']} = {derived}"
            )

        increments = sum(item["increment"] for item in expected["contributions"])
        counted = expected["decision_point_count"] + expected["boolean_operator_count"]
        if increments != counted:
            fail(
                f"{name}: contributions sum to {increments} but decision+boolean "
                f"is {counted}"
            )

        if expected["max_condition_operator_count"] > expected["boolean_operator_count"]:
            fail(
                f"{name}: max_condition_operator_count "
                f"{expected['max_condition_operator_count']} > "
                f"boolean_operator_count {expected['boolean_operator_count']}"
            )

    print()
    if FAILURES:
        print(f"  {len(FAILURES)} problem(s) found")
        return 1
    ok(f"all {len(callables)} callables: locations, nloc, decomposition and bounds hold")
    return 0


if __name__ == "__main__":
    sys.exit(main())
