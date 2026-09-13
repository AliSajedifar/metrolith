#!/usr/bin/env python3
"""Compare normalized scientific output across ArchLens run directories."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

try:
    from .validate_outputs import semantic_payload
except ImportError:  # Direct script execution.
    from validate_outputs import semantic_payload


def differences(left, right, path="$"):
    found = []
    if type(left) is not type(right):
        return [{"path": path, "left": left, "right": right}]
    if isinstance(left, dict):
        for key in sorted(set(left) | set(right)):
            if key not in left or key not in right:
                found.append({"path": f"{path}.{key}", "left": left.get(key), "right": right.get(key)})
            else:
                found.extend(differences(left[key], right[key], f"{path}.{key}"))
    elif isinstance(left, list):
        if len(left) != len(right):
            found.append({"path": f"{path}.length", "left": len(left), "right": len(right)})
        for index, (lvalue, rvalue) in enumerate(zip(left, right)):
            found.extend(differences(lvalue, rvalue, f"{path}[{index}]"))
    elif left != right:
        found.append({"path": path, "left": left, "right": right})
    return found


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("baseline", type=Path)
    parser.add_argument("candidates", nargs="+", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    baseline = semantic_payload(args.baseline.resolve())
    comparisons = []
    all_equal = True
    for candidate in args.candidates:
        mismatch = differences(baseline, semantic_payload(candidate.resolve()))
        comparisons.append({
            "baseline": str(args.baseline.resolve()),
            "candidate": str(candidate.resolve()),
            "semantically_equal": not mismatch,
            "difference_count": len(mismatch),
            "differences": mismatch,
        })
        all_equal = all_equal and not mismatch
    report = {"all_semantically_equal": all_equal, "comparisons": comparisons}
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    raise SystemExit(0 if all_equal else 1)


if __name__ == "__main__":
    main()
