"""Metrolith Cognitive Complexity — production computation.

Implements revision 3 of `validation/complexity_g1a_20260811/FROZEN_RULE_TABLE.md`
and nothing else. The rules are frozen; this module is the reading of them, not a
place to reinterpret them.

**This is not Complexity Contract 2.0.0.** No contract document is created, no
artifact column exists, and nothing here is persisted. The value lives on the
in-memory record only, exactly as the seven Complexity Contract 1.0.0 metrics did
at the C2 checkpoint before C3 gave them an artifact.

Why a separate engine rather than another `StructuralRules` field: cognitive
complexity is not a decision count. Three of its mechanics have no counterpart
in the cyclomatic engine —

* a **structural** increment scales with nesting (`1 + depth`) while a **flat**
  one never does, so depth must be threaded per construct rather than tracked as
  a maximum;
* **boolean sequences** are counted as maximal runs in *flattened source order*,
  not per operator token;
* **recursion** is capped at one per callable, so it is a set membership question
  rather than a sum.

Sharing the cyclomatic engine would have meant bending all three. The boundary
rule is the one thing both share, and it is imported rather than restated.

Discovery is NOT this module's concern. The language modules walk the whole file
to find every canonical callable; this module measures one of them and stops at
every nested callable boundary (frozen table section 7).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Sequence

from modules.syntax_predicates import _node_text

#: Short-circuit operators per language, frozen table section 3.1. Nothing else
#: is a boolean-sequence operator: bitwise `&`/`|`, Java multi-catch, TypeScript
#: union types and the logical ASSIGNMENTS are all outside the set.
BOOLEAN_OPERATORS: dict[str, frozenset[str]] = {
    "Java": frozenset({"&&", "||"}),
    "Go": frozenset({"&&", "||"}),
    "JavaScript": frozenset({"&&", "||", "??"}),
    "TypeScript": frozenset({"&&", "||", "??"}),
    "Python": frozenset({"and", "or"}),
}


@dataclass(frozen=True)
class CognitiveRules:
    """One language's mapping from syntax to the frozen rule set."""

    #: Ends attribution: a nested callable's subtree belongs to another record.
    is_boundary: Callable[[Any], bool]
    #: `1 + depth` when this node is a structural construct (`S-`).
    is_structural: Callable[[Any], bool]
    #: The children of a structural or flat construct that sit one level deeper.
    body_children: Callable[[Any], Sequence[Any]]
    #: `+1` flat, once per node (`F-ELSEIF`, `F-ELSE`, `F-LABELJUMP`, `F-GUARD`).
    flat_increment: Callable[[Any, bytes], int]
    #: True for the OUTERMOST node of a short-circuit expression, so the run
    #: flattening happens once per expression rather than once per operator.
    is_boolean_root: Callable[[Any, bytes], bool]
    #: The operator text of a short-circuit node, or None.
    boolean_operator: Callable[[Any, bytes], str | None]
    #: True when this node is a self-call under the frozen heuristic.
    is_self_call: Callable[[Any, bytes, "SelfCallContext"], bool]
    #: Nodes whose subtree is excluded from attribution but which are not
    #: callables -- TypeScript type positions (`Z-TYPEONLY`).
    is_excluded_subtree: Callable[[Any], bool] = lambda node: False


@dataclass(frozen=True)
class SelfCallContext:
    """What "calling myself" means for the callable being measured.

    Frozen table section 4.2: a bare self-name call, or a member call whose
    receiver is the language's explicit current receiver or the enclosing type
    name. Anything else -- `arbitraryObject.sameName()` -- is not recursion.
    """

    name: str
    #: `this` / `self` / `cls` / the declared Go receiver identifier, plus the
    #: enclosing type name where a qualified self-call is syntactically explicit.
    receivers: frozenset[str] = field(default_factory=frozenset)


def flatten_boolean_runs(
    node: Any, source: bytes, rules: CognitiveRules
) -> int:
    """Maximal same-operator runs in flattened SOURCE order (section 3.2).

    Parentheses are transparent -- they never split a run. A different operator
    between two runs of the same operator DOES split them, so
    `a || b && c || d` is three runs and not two. Tree adjacency is explicitly
    the wrong reading: the two `||` nodes there are parent and child.

    Flattening in source order is what makes the difference, so the operators
    are collected by position rather than by tree shape.
    """
    operators: list[tuple[int, str]] = []

    def collect(current: Any) -> None:
        if current is None or rules.is_boundary(current) or rules.is_excluded_subtree(current):
            return
        symbol = rules.boolean_operator(current, source)
        if symbol is not None:
            operator = current.child_by_field_name("operator")
            position = operator.start_byte if operator is not None else current.start_byte
            operators.append((position, symbol))
        for child in current.named_children:
            collect(child)

    collect(node)
    operators.sort()

    runs = 0
    previous: str | None = None
    for _position, symbol in operators:
        if symbol != previous:
            runs += 1
        previous = symbol
    return runs


def measure_cognitive(
    body: Any,
    source: bytes,
    rules: CognitiveRules,
    context: SelfCallContext,
) -> int:
    """Cognitive complexity of one callable. Starts at 0."""
    total = 0
    recursion_found = False

    def visit(node: Any, depth: int) -> None:
        nonlocal total, recursion_found

        if rules.is_boundary(node) or rules.is_excluded_subtree(node):
            return

        if rules.is_self_call(node, source, context):
            recursion_found = True

        # A boolean sequence is counted once per outermost short-circuit
        # expression; descending into it again would count the same run twice.
        if rules.is_boolean_root(node, source):
            total += flatten_boolean_runs(node, source, rules)
            for child in node.named_children:
                if rules.is_boundary(child) or rules.is_excluded_subtree(child):
                    continue
                _visit_inside_boolean(child, depth)
            return

        structural = rules.is_structural(node)
        flat = rules.flat_increment(node, source)
        if structural:
            total += 1 + depth
        total += flat

        bodies = list(rules.body_children(node)) if (structural or flat) else []
        # Compared by tree-sitter node id: `child_by_field_name` builds a fresh
        # wrapper on every call, so Python object identity is never true even
        # for the same node. The cyclomatic engine learned this the same way.
        deeper = {item.id for item in bodies if item is not None}
        for child in node.named_children:
            visit(child, depth + 1 if child.id in deeper else depth)

    def _visit_inside_boolean(node: Any, depth: int) -> None:
        """Operands of a boolean expression still carry their own constructs.

        A ternary or a self-call written inside a condition is not swallowed by
        the sequence that surrounds it; only the operators are already counted.
        """
        if rules.is_boundary(node) or rules.is_excluded_subtree(node):
            return
        if rules.boolean_operator(node, source) is not None:
            for child in node.named_children:
                _visit_inside_boolean(child, depth)
            return
        visit(node, depth)

    visit(body, 0)
    if recursion_found:
        total += 1
    return total


# ---------------------------------------------------------------------------
# shared helpers for the tree-sitter languages
# ---------------------------------------------------------------------------


def binary_operator_reader(operators: frozenset[str]):
    """Read a short-circuit operator from the node's ``operator`` FIELD.

    Field access rather than text scanning, for the same reason the cyclomatic
    engine does it: an operator inside a nested string is not an operator.
    """

    def read(node: Any, source: bytes) -> str | None:
        if node.type not in {"binary_expression", "logical_expression"}:
            return None
        operator = node.child_by_field_name("operator")
        if operator is None:
            return None
        symbol = _node_text(operator, source)
        return symbol if symbol in operators else None

    return read


def boolean_root_predicate(read_operator: Callable[[Any, bytes], str | None]):
    """True for the outermost node of a short-circuit expression.

    "Outermost" ignores parentheses, so `(a && b) || c` has one root and not
    two: the parenthesized wrapper is transparent both to the run flattening and
    to this test.
    """

    def is_root(node: Any, source: bytes) -> bool:
        if read_operator(node, source) is None:
            return False
        parent = node.parent
        while parent is not None and parent.type in {
            "parenthesized_expression", "parenthesized_type",
        }:
            parent = parent.parent
        return parent is None or read_operator(parent, source) is None

    return is_root


def call_name_parts(node: Any, source: bytes, call_types: frozenset[str],
                    function_field: str = "function") -> tuple[str | None, str | None]:
    """``(receiver, callee)`` for a call node, or ``(None, None)``.

    Purely syntactic: no symbol table is consulted, which is the whole reason
    the frozen heuristic is written the way it is.
    """
    if node.type not in call_types:
        return (None, None)
    target = node.child_by_field_name(function_field)
    if target is None:
        return (None, None)
    if target.type in {"identifier", "field_identifier", "property_identifier"}:
        # A plain identifier callee does NOT imply an unqualified call. Java's
        # `method_invocation` keeps its receiver in a SIBLING `object` field
        # rather than wrapping the callee in a selector node, so reading the
        # `name` field alone made `other.sameName()` look exactly like a bare
        # `sameName()` -- and every same-named call scored F-RECURSION, which
        # frozen table section 4.3 explicitly excludes. Go and JS/TS
        # `call_expression` nodes carry no `object` field, so they are
        # unaffected.
        receiver = node.child_by_field_name("object")
        return (
            _node_text(receiver, source) if receiver is not None else None,
            _node_text(target, source),
        )
    if target.type in {
        "selector_expression", "member_expression", "field_access",
        "attribute", "navigation_expression",
    }:
        receiver = (
            target.child_by_field_name("object")
            or target.child_by_field_name("operand")
            or target.child_by_field_name("value")
        )
        member = (
            target.child_by_field_name("field")
            or target.child_by_field_name("property")
            or target.child_by_field_name("name")
            or target.child_by_field_name("attribute")
        )
        return (
            _node_text(receiver, source) if receiver is not None else None,
            _node_text(member, source) if member is not None else None,
        )
    return (None, None)


def self_call_predicate(call_types: frozenset[str], function_field: str = "function"):
    """The frozen high-precision heuristic, section 4.2.

    Rule A: a bare callee identifier equal to the callable's own name.
    Rule B: a member call whose name matches AND whose receiver is the explicit
    current receiver or the enclosing type name.

    `arbitraryObject.sameName()` is deliberately NOT recursion. Narrowing to
    this costs the alias false negative and removes the far larger
    same-name-different-receiver false-positive class.
    """

    def is_self_call(node: Any, source: bytes, context: SelfCallContext) -> bool:
        receiver, callee = call_name_parts(node, source, call_types, function_field)
        if callee is None or callee != context.name:
            return False
        if receiver is None:
            return True
        return receiver in context.receivers

    return is_self_call
