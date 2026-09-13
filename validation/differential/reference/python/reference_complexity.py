"""Independent Python complexity reference, using parso.

Runs under the *reference* interpreter. ArchLens parses Python with the CPython
stdlib (`ast` + `tokenize`); this uses parso's own grammar and concrete syntax
tree. The two share no parser, no traversal and no rule table, which is the only
reason a disagreement here would mean anything.

**Implemented from `docs/COMPLEXITY_CONTRACT_V1.md` and the hand-authored
synthetic expectations. No ArchLens metric output was consulted.** Nothing from
`modules.` is imported, here or transitively.

Definitions implemented -- ArchLens's, independently:

``population``
    module-level functions plus direct class-body methods. Excluded:
    ``__init__``/``__new__``, ``@overload`` declarations, functions nested in
    another function, and functions in a class body that are not direct members.
    This is the canonical `methods_functions` population and is deliberately NOT
    broadened to every callable the way a general-purpose counter would.

``decision_point_count``
    §7.1: each ``if``/``elif``; each ``for``/``while``; each ``except``; each
    conditional expression; per comprehension, one per ``for`` clause and one
    per ``if`` clause; each non-wildcard ``match`` arm and each guard.
    ``else``, ``finally`` and a bare ``case _`` contribute nothing.

``boolean_operator_count``
    §7.2: ``and``/``or`` operator occurrences. An ``n``-operand chain carries
    ``n-1`` operators.

``max_condition_operator_count``
    §11: the largest operator count inside a SINGLE decision expression.

``max_nesting_depth``
    §10: bodies of if/else, loops, ``with``, ``try``/``except``/``finally``, and
    a ``match`` plus each arm. An ``elif`` does not nest. A nested callable is a
    boundary and its interior is not traversed.

``nloc``
    §8: physical lines in the declaration span, minus blank and comment-only
    lines. A docstring is code. The span includes decorator lines.

Not ground truth. parso is a reference front end whose disagreements are
findings about both sides until adjudicated.
"""

from __future__ import annotations

import json
import sys

import parso

#: Versioned independently of parso: the adapter's RULES can change while the
#: parser stays fixed, and a reader must be able to tell which moved.
ADAPTER_VERSION = "2.2.0"

_CONSTRUCTORS = {"__init__", "__new__"}
#: parso node types that end attribution for the enclosing callable (§3.1).
_BOUNDARY = {"funcdef", "classdef", "lambdef"}


# --------------------------------------------------------------------------
# tree helpers -- deliberately small, so the traversal stays readable
# --------------------------------------------------------------------------

def _children(node):
    return getattr(node, "children", []) or []


def _type(node):
    return getattr(node, "type", None)


def _walk(node, *, skip_boundaries: bool):
    """Yield descendants. Stops at a nested callable when asked to."""
    for child in _children(node):
        if skip_boundaries and _type(child) in _BOUNDARY:
            continue
        yield child
        yield from _walk(child, skip_boundaries=skip_boundaries)


def _keyword_count(node, keyword: str) -> int:
    return sum(
        1
        for child in _children(node)
        if _type(child) == "keyword" and child.value == keyword
    )


def _is_wildcard_pattern(node) -> bool:
    """`case _:` -- a bare wildcard adds no decision."""
    text = node.get_code().strip()
    return text.startswith("case _") and ":" in text and "if" not in text.split(":")[0]


# --------------------------------------------------------------------------
# metric computation
# --------------------------------------------------------------------------

def _boolean_operators(node) -> int:
    """`and`/`or` occurrences, boundaries excluded. `a and b and c` is 2."""
    total = 0
    stack = [node]
    while stack:
        current = stack.pop()
        node_type = _type(current)
        if node_type in {"and_test", "or_test"}:
            keyword = "and" if node_type == "and_test" else "or"
            total += _keyword_count(current, keyword)
        for child in _children(current):
            if _type(child) in _BOUNDARY:
                continue
            stack.append(child)
    return total


def _condition_expressions(node):
    """The decision expressions a construct introduces (§11)."""
    node_type = _type(node)
    children = _children(node)
    if node_type in {"if_stmt", "while_stmt"}:
        # children alternate: keyword, test, ':', suite, ...
        for index, child in enumerate(children):
            if _type(child) == "keyword" and child.value in {"if", "elif", "while"}:
                if index + 1 < len(children):
                    yield children[index + 1]
    elif node_type == "comp_if":
        for child in children:
            if _type(child) != "keyword":
                yield child
    elif node_type == "test":
        # `a if cond else b` -- the CONDITION is the operand after `if`.
        for index, child in enumerate(children):
            if _type(child) == "keyword" and child.value == "if":
                if index + 1 < len(children):
                    yield children[index + 1]


def _measure(body, source_lines):
    """Bounded descent over one callable's own body."""
    decisions = 0
    booleans = 0
    max_condition = 0
    max_depth = 0

    def opens_nesting(node) -> bool:
        """A `suite` under a control construct opens one level."""
        if _type(node) != "suite":
            return False
        parent = node.parent
        return _type(parent) in {
            "if_stmt", "while_stmt", "for_stmt", "try_stmt", "with_stmt",
            "except_clause", "case_block", "match_stmt",
        }

    def walk(node, depth):
        nonlocal decisions, booleans, max_condition, max_depth
        node_type = _type(node)

        # `elif` does not nest: parso keeps it inside the SAME if_stmt, so an
        # elif chain is naturally flat here and no special case is needed.
        inner = depth + 1 if opens_nesting(node) else depth
        if _type(node) == "match_stmt":
            inner = depth + 1

        for child in _children(node):
            child_type = _type(child)
            if child_type in _BOUNDARY:
                continue

            if inner > max_depth and child_type not in {"keyword", "operator"}:
                max_depth = inner

            if child_type == "if_stmt":
                decisions += _keyword_count(child, "if") + _keyword_count(child, "elif")
            elif child_type in {"for_stmt", "while_stmt"}:
                decisions += 1
            elif child_type == "except_clause":
                decisions += 1
            elif child_type == "try_stmt":
                # A BARE `except:` has no `except_clause` node -- parso leaves
                # the keyword as a direct child of `try_stmt`, so a rule table
                # keyed on `except_clause` misses it entirely. Contract section
                # 7.1 counts each `except` clause, bare or not. Only the bare
                # ones are reachable here: a named clause carries its own
                # `except` keyword inside the `except_clause`.
                decisions += _keyword_count(child, "except")
            elif child_type in {"comp_for", "sync_comp_for"}:
                decisions += 1
            elif child_type == "comp_if":
                decisions += 1
            elif child_type == "case_block":
                if not _is_wildcard_pattern(child):
                    decisions += 1
                if " if " in child.get_code().split(":")[0]:
                    decisions += 1
            elif child_type == "test" and any(
                _type(item) == "keyword" and item.value == "if"
                for item in _children(child)
            ):
                decisions += 1
            elif child_type in {"and_test", "or_test"}:
                keyword = "and" if child_type == "and_test" else "or"
                booleans += _keyword_count(child, keyword)

            for condition in _condition_expressions(child):
                found = _boolean_operators(condition)
                if found > max_condition:
                    max_condition = found

            walk(child, inner)

    walk(body, 0)
    return decisions, booleans, max_condition, max_depth


def _mask_comments(text: str) -> list[str]:
    """Blank comment bytes, preserving line structure.

    A clean-room scanner with string states, so a `#` inside a string is not a
    comment. parso exposes comments only as prefix trivia, which is awkward to
    map back to columns, and scanning is the more direct independent route.
    """
    masked: list[str] = []
    in_single = in_double = False
    in_triple: str | None = None
    for line in text.split("\n"):
        out = []
        index = 0
        while index < len(line):
            char = line[index]
            three = line[index : index + 3]
            if in_triple:
                out.append(char)
                if three == in_triple:
                    out.append(line[index + 1 : index + 3])
                    index += 3
                    in_triple = None
                    continue
                index += 1
                continue
            if not in_single and not in_double and three in ('"""', "'''"):
                in_triple = three
                out.append(three)
                index += 3
                continue
            if in_single:
                out.append(char)
                if char == "\\":
                    if index + 1 < len(line):
                        out.append(line[index + 1])
                    index += 2
                    continue
                if char == "'":
                    in_single = False
                index += 1
                continue
            if in_double:
                out.append(char)
                if char == "\\":
                    if index + 1 < len(line):
                        out.append(line[index + 1])
                    index += 2
                    continue
                if char == '"':
                    in_double = False
                index += 1
                continue
            if char == "#":
                out.append(" " * (len(line) - index))
                break
            if char == "'":
                in_single = True
            elif char == '"':
                in_double = True
            out.append(char)
            index += 1
        masked.append("".join(out))
    return masked


def _nloc(raw_lines, masked_lines, start_line: int, end_line: int) -> int:
    total = 0
    for index in range(start_line - 1, min(end_line, len(raw_lines))):
        if raw_lines[index].strip() and masked_lines[index].strip():
            total += 1
    return total


def _parameter_count(funcdef) -> int:
    """§9 Python: every declared formal parameter, `self` included."""
    parameters = None
    for child in _children(funcdef):
        if _type(child) == "parameters":
            parameters = child
            break
    if parameters is None:
        return 0
    total = 0
    for child in _children(parameters):
        if _type(child) == "param":
            code = child.get_code().strip().lstrip("*").lstrip()
            if code.startswith("/") or code.startswith(","):
                continue
            total += 1
        elif _type(child) == "operator" and child.value == "/":
            continue
    return total


def _is_overload(funcdef) -> bool:
    parent = funcdef.parent
    if _type(parent) != "decorated":
        return False
    return "overload" in parent.children[0].get_code()


def _span(funcdef, raw_lines) -> tuple[int, int]:
    """Declaration span, extended over decorators (§8.1).

    parso's `end_pos` points at the position AFTER the node, so a suite that
    ends with a newline reports the following line at column 0. ArchLens's
    convention is the last line carrying content. Neither is wrong; they are
    different conventions for the same span, so the end is normalized here by
    walking back over trailing blank lines IN THE SOURCE -- derived from the
    file, not from ArchLens.
    """
    # parso wraps `async def` in `async_funcdef`/`async_stmt`, so a decorated
    # async function's `decorated` node is the GRANDparent. Checking only the
    # immediate parent dropped every decorator line from the span -- 264 nloc
    # disagreements on one Layer-C2 subject, up to 80 lines on a single
    # `@tool(...)`. The walk steps through the wrappers, exactly as the scope
    # walk already does.
    node = funcdef
    while _type(node.parent) in {"async_stmt", "async_funcdef"}:
        node = node.parent
    if _type(node.parent) == "decorated":
        node = node.parent
    start = node.start_pos[0]
    end = funcdef.end_pos[0]
    if funcdef.end_pos[1] == 0:
        end -= 1
    while end > start and end <= len(raw_lines) and not raw_lines[end - 1].strip():
        end -= 1
    return start, end


#: Wrappers parso inserts that carry no scope of their own. `async_stmt` is the
#: important one: parso wraps `async def` in an `async_stmt`, so a scope walk
#: that does not step through it classifies every async function as excluded.
_TRANSPARENT = {"suite", "simple_stmt", "decorated", "async_stmt", "async_funcdef"}

#: Block statements are not scopes. ArchLens's population rule is *no callable
#: ancestor*, so a `def` guarded by a module-level `if FLAG:` -- the Django and
#: optional-dependency idiom -- is a module function. Stopping the scope walk at
#: `if_stmt` dropped two callables on `layer2-python-ralph`. A class body is
#: deliberately NOT here: ArchLens counts only DIRECT class-body members, so a
#: `def` inside an `if` inside a class body stays excluded on both sides.
_NON_SCOPE_BLOCKS = {"if_stmt", "try_stmt", "with_stmt", "for_stmt", "while_stmt"}


def _enclosing_kind(funcdef) -> str | None:
    """Classify against the canonical population, or None to exclude."""
    parent = funcdef.parent
    while parent is not None and _type(parent) in _TRANSPARENT:
        parent = parent.parent
    if _type(parent) == "classdef":
        return "class_method"
    # Walk out of any enclosing block statements. Reaching `file_input` means no
    # callable and no class body ever intervened.
    while parent is not None and _type(parent) in _NON_SCOPE_BLOCKS:
        parent = parent.parent
        while parent is not None and _type(parent) in _TRANSPARENT:
            parent = parent.parent
        if _type(parent) == "classdef":
            # Inside a class body but not a direct member: nested, not a method.
            return None
    if _type(parent) == "file_input":
        return "module_function"
    return None


def _contains_parse_error(node) -> bool:
    """True when parso could not parse part of this subtree.

    parso 0.8.7 has no `match` grammar and emits `error_node`/`error_leaf` for
    it. Reporting 0 decisions for an unparsed construct would manufacture
    agreement, so such a callable is reported NOT EVALUABLE instead.
    """
    if _type(node) in {"error_node", "error_leaf"}:
        return True
    return any(_contains_parse_error(child) for child in _children(node))


def analyze(path: str) -> dict:
    text = open(path, encoding="utf-8").read()
    tree = parso.parse(text)
    raw_lines = text.split("\n")
    masked_lines = _mask_comments(text)

    records = []
    excluded = {"nested_functions": 0, "constructors": 0, "overloads": 0, "lambdas": 0}

    for node in _walk(tree, skip_boundaries=False):
        if _type(node) == "lambdef":
            excluded["lambdas"] += 1
            continue
        if _type(node) != "funcdef":
            continue

        name = node.name.value
        # A funcdef whose nearest scope is another funcdef is nested.
        ancestor = node.parent
        nested = False
        while ancestor is not None and _type(ancestor) != "file_input":
            if _type(ancestor) == "funcdef":
                nested = True
                break
            if _type(ancestor) == "classdef":
                break
            ancestor = ancestor.parent
        # `async_stmt` is a wrapper, not a scope; see _TRANSPARENT.
        if nested:
            excluded["nested_functions"] += 1
            continue

        kind = _enclosing_kind(node)
        if kind is None:
            excluded["nested_functions"] += 1
            continue
        if kind == "class_method" and name in _CONSTRUCTORS:
            excluded["constructors"] += 1
            continue
        if _is_overload(node):
            excluded["overloads"] += 1
            continue

        body = None
        for child in _children(node):
            if _type(child) == "suite":
                body = child
                break
        if body is None:
            continue

        start_line, end_line = _span(node, raw_lines)
        record = {
            "name": name,
            "qualified_name": _qualified(node, name),
            "callable_kind": kind,
            "start_line": start_line,
            "end_line": end_line,
            "nloc": _nloc(raw_lines, masked_lines, start_line, end_line),
            "formal_parameter_count": _parameter_count(node),
            "not_evaluable_reason": None,
        }
        if _contains_parse_error(body):
            # The structural metrics are unavailable, NOT zero. NLOC and the
            # parameter count survive: both are read from the source and the
            # signature, neither of which failed to parse.
            record.update(
                {
                    "decision_point_count": None,
                    "boolean_operator_count": None,
                    "max_condition_operator_count": None,
                    "max_nesting_depth": None,
                    "cyclomatic_complexity": None,
                    "not_evaluable_reason": "parso_grammar_cannot_parse_construct",
                }
            )
        else:
            decisions, booleans, max_condition, max_depth = _measure(body, raw_lines)
            record.update(
                {
                    "decision_point_count": decisions,
                    "boolean_operator_count": booleans,
                    "max_condition_operator_count": max_condition,
                    "max_nesting_depth": max_depth,
                    "cyclomatic_complexity": 1 + decisions + booleans,
                }
            )
        records.append(record)

    return {
        "path": path,
        "adapter_version": ADAPTER_VERSION,
        "parso_version": parso.__version__,
        "callables": records,
        "excluded_populations": excluded,
    }


def _qualified(funcdef, name: str) -> str:
    parts = [name]
    ancestor = funcdef.parent
    while ancestor is not None and _type(ancestor) != "file_input":
        if _type(ancestor) in {"classdef", "funcdef"}:
            parts.append(ancestor.name.value)
        ancestor = ancestor.parent
    return ".".join(reversed(parts))


def _input_paths(argv: list[str]) -> list[str]:
    """Resolve inputs. `--list FILE` reads one UTF-8 path per line.

    A listing file removes any dependence on the command-line length limit,
    which a real subject with a thousand source files exceeds on Windows.
    """
    if len(argv) == 3 and argv[1] == "--list":
        with open(argv[2], encoding="utf-8") as handle:
            return [line.strip() for line in handle if line.strip()]
    return argv[1:]


def main(argv: list[str]) -> int:
    results = [analyze(path) for path in _input_paths(argv)]
    json.dump(
        {"adapter_version": ADAPTER_VERSION, "files": results},
        sys.stdout,
        indent=2,
        sort_keys=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
