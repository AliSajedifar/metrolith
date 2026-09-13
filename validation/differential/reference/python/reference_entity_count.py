"""Independent Python entity reference, using parso.

Runs under the *reference* interpreter, not ArchLens's. ArchLens parses Python
with the CPython stdlib (`ast` + `tokenize`); this uses parso's own grammar and
concrete syntax tree, so the two share no parser. That is the only reason a
disagreement here would mean anything.

**Definition implemented** — ArchLens's, independently:

``classes_structs``
    every ``class`` statement, at any nesting depth.

``methods_functions``
    module-level functions plus direct class-body methods. Excluded:
    ``__init__`` / ``__new__`` (constructors), ``@overload`` declarations,
    functions nested inside another function, and functions inside a class that
    are not direct members of the class body (for example one defined inside an
    ``if`` in the class body).

Emits one JSON object per file plus totals, with the excluded populations
reported separately so the size of each definitional exclusion stays visible.
"""

from __future__ import annotations

import json
import sys

import parso

#: Pinned so a grammar change is a deliberate, recorded act.
GRAMMAR_VERSION = "3.12"

CONSTRUCTOR_NAMES = {"__init__", "__new__"}

#: parso wraps a function in these before its real syntactic parent.
_WRAPPERS = {"async_stmt", "async_funcdef", "decorated", "simple_stmt"}


def _effective_parent(node):
    """Skip parso's wrapper nodes to reach the enclosing block."""
    parent = node.parent
    while parent is not None and parent.type in _WRAPPERS:
        parent = parent.parent
    return parent


def _is_direct_class_member(node) -> bool:
    """True when the function is declared directly in a class body."""
    parent = _effective_parent(node)
    if parent is None:
        return False
    if parent.type != "suite":
        return False
    return parent.parent is not None and parent.parent.type == "classdef"


def _enclosing_kinds(node) -> list[str]:
    """Types of every enclosing classdef/funcdef, innermost first."""
    found: list[str] = []
    current = node.parent
    while current is not None:
        if current.type in ("classdef", "funcdef"):
            found.append(current.type)
        current = current.parent
    return found


def _is_overload(node) -> bool:
    """Whether the declaration carries an `@overload` decorator."""
    parent = node.parent
    while parent is not None and parent.type in ("async_stmt", "async_funcdef"):
        parent = parent.parent
    if parent is None or parent.type != "decorated":
        return False
    decorators = parent.children[0]
    candidates = (
        decorators.children if decorators.type == "decorators" else [decorators]
    )
    for decorator in candidates:
        text = decorator.get_code()
        # `@overload`, `@typing.overload`, `@t.overload`, with or without call.
        head = text.strip().lstrip("@").split("(")[0].strip()
        if head.split(".")[-1] == "overload":
            return True
    return False


def count(text: str) -> dict[str, int]:
    grammar = parso.load_grammar(version=GRAMMAR_VERSION)
    tree = grammar.parse(text)

    result = {
        "classes": 0,
        "methods": 0,
        "constructors": 0,
        "overload_declarations": 0,
        "nested_functions": 0,
        "module_functions": 0,
        "class_methods": 0,
    }

    stack = [tree]
    while stack:
        node = stack.pop()
        for child in getattr(node, "children", []) or []:
            stack.append(child)

        if node.type == "classdef":
            result["classes"] += 1
            continue
        if node.type != "funcdef":
            continue

        if _is_overload(node):
            result["overload_declarations"] += 1
            continue

        enclosing = _enclosing_kinds(node)
        if _is_direct_class_member(node):
            if node.name.value in CONSTRUCTOR_NAMES:
                result["constructors"] += 1
            else:
                result["class_methods"] += 1
                result["methods"] += 1
            continue
        if "funcdef" in enclosing:
            result["nested_functions"] += 1
            continue
        if "classdef" in enclosing:
            # Inside a class but not a direct body member.
            result["nested_functions"] += 1
            continue
        result["module_functions"] += 1
        result["methods"] += 1
    return result


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(json.dumps({"error": "usage: reference_entity_count.py <file-list>"}))
        return 2
    with open(argv[1], encoding="utf-8") as handle:
        files = [line.strip() for line in handle if line.strip()]

    entries = []
    totals = {"types": 0, "methods": 0, "files_parsed": 0, "files_failed": 0}
    for path in files:
        try:
            with open(path, encoding="utf-8", errors="strict") as handle:
                text = handle.read()
            counts = count(text)
        except Exception as exc:  # noqa: BLE001 - reported, never swallowed
            entries.append({
                "path": path, "types": None, "methods": None,
                "error": f"{type(exc).__name__}: {exc}",
            })
            totals["files_failed"] += 1
            continue
        entries.append({
            "path": path,
            "types": counts["classes"],
            "methods": counts["methods"],
            "constructors": counts["constructors"],
            "overload_declarations": counts["overload_declarations"],
            "nested_functions": counts["nested_functions"],
            "module_functions": counts["module_functions"],
            "class_methods": counts["class_methods"],
            "error": None,
        })
        totals["types"] += counts["classes"]
        totals["methods"] += counts["methods"]
        totals["files_parsed"] += 1

    print(json.dumps({
        "files": entries,
        "totals": totals,
        "grammar_version": GRAMMAR_VERSION,
        "parso_version": parso.__version__,
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
