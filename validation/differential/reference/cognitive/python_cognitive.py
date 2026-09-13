"""cognitive_complexity 1.3.0 driver, run in the reference interpreter.

Runs OUTSIDE the ArchLens acceptance environment, in `pyref`, so the reference
package is never installed beside the code under study.

**This reference is not parser-independent.** It imports stdlib ``ast`` -- the
same parser ArchLens uses for Python. It is an independent *definition* and not
an independent *parse*, and the definition mapping says so. Nothing here can
detect a Python parse defect.

Two behaviours are reported rather than smoothed over:

* a genuine 0 is returned as 0, so Python needs no zero-suppression inference;
* a module containing ``match`` still parses under ``ast``, but the reference
  scores the statement at 0 rather than refusing (D12). The driver therefore
  FLAGS every callable containing a ``match`` so the comparison can report it
  not_comparable. A clean 0 that means "I could not see this construct" is the
  most dangerous shape a reference can produce, and it is caught here rather
  than at adjudication.
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path


def _qualified_names(tree: ast.Module) -> list[tuple[str, ast.AST]]:
    """Every callable, with the dotted path ArchLens would give it.

    Discovery is global -- it walks through classes and functions -- because the
    frozen rule table separates discovery from attribution (7.1) and a method on
    a class declared inside a function is in the population.
    """
    found: list[tuple[str, ast.AST]] = []

    def walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = f"{prefix}{child.name}"
                found.append((name, child))
                walk(child, f"{name}.")
            elif isinstance(child, ast.ClassDef):
                walk(child, f"{prefix}{child.name}.")
            else:
                walk(child, prefix)

    walk(tree, "")
    return found


def _contains_match(node: ast.AST) -> bool:
    return any(isinstance(item, ast.Match) for item in ast.walk(node))


def main(listing: str, root: str) -> int:
    from cognitive_complexity.api import get_cognitive_complexity

    base = Path(root)
    paths = [
        line.strip()
        for line in Path(listing).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not paths:
        print("empty file listing: refused rather than reported as zero rows",
              file=sys.stderr)
        return 2

    rows: list[dict[str, object]] = []
    unreadable: list[dict[str, str]] = []

    for relative in paths:
        absolute = base / relative
        try:
            source = absolute.read_text(encoding="utf-8")
            tree = ast.parse(source)
        except (OSError, SyntaxError, ValueError) as error:
            unreadable.append(
                {
                    "relative_path": relative.replace("\\", "/"),
                    "reason": f"{type(error).__name__}: {error}",
                }
            )
            continue

        for qualified_name, node in _qualified_names(tree):
            rows.append(
                {
                    "relative_path": relative.replace("\\", "/"),
                    "qualified_name": qualified_name,
                    "start_line": node.lineno,
                    "end_line": getattr(node, "end_lineno", node.lineno),
                    "value": get_cognitive_complexity(node),
                    # D12. Recorded per row so the comparison can refuse the
                    # number instead of trusting a 0 the tool could not have
                    # measured.
                    "contains_unsupported_match": _contains_match(node),
                }
            )

    json.dump({"rows": rows, "unreadable_files": unreadable}, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1], sys.argv[2]))
