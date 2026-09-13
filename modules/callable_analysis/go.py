"""Go callable enumeration.

The population predicate is **the same code** `_go_entities` uses, imported from
the shared `modules.syntax_predicates` seam rather than restated: the same
`_iter_nodes` walk, the same `_at_module_scope`, `_reliable_declaration` and
`_contains_malformed` guards, in the same order. That is what makes

    count(callable records) == methods_functions

true by construction rather than by coincidence. A second, parallel definition of
"function" is exactly the drift this module exists to avoid.

**Never import `modules.core_metrics` from this package.** `core_metrics` calls
`analyze_callables`, so an import back would cycle. `tests/test_callable_analysis.py`
enforces this with an AST scan rather than trusting convention, because a lazy
import inside a function would hide the cycle from the interpreter while leaving
it in the dependency graph.
"""

from __future__ import annotations

from typing import Any

from modules.syntax_predicates import (
    _at_module_scope,
    _contains_malformed,
    _iter_nodes,
    _node_text,
    _reliable_declaration,
)

from .cognitive import SelfCallContext, measure_cognitive
from .cognitive_rules import GO_RULES
from .metrics import (
    LineIndex,
    StructuralRules,
    _binary_operator_counter,
    apply_metrics,
    measure,
)
from .model import (
    KIND_MODULE_FUNCTION,
    KIND_RECEIVER_METHOD,
    OWNER_MODULE,
    OWNER_RECEIVER_TYPE,
    CallableRecord,
    qualify,
)

#: Traversal boundaries. A Go function literal ends the enclosing callable's
#: traversal; its control flow belongs to no record under Complexity Contract
#: 1.0.0 section 3.
BOUNDARY_NODES = frozenset(
    {"function_declaration", "method_declaration", "func_literal"}
)

#: Contract section 7.1. Every case arm counts; `default_case` does not.
_CASE_NODES = frozenset({"expression_case", "type_case", "communication_case"})
_DECISION_NODES = frozenset({"if_statement", "for_statement"}) | _CASE_NODES

#: Go has no ternary and no try/catch, so the decision set is small.
#: `default_case` is listed to state the zero explicitly rather than by omission.
_ZERO_DECISION_NODES = frozenset({"default_case"})

_BOOLEAN_OPERATORS = frozenset({"&&", "||"})

#: A switch or select opens one level, and each of its arms opens another, so a
#: statement inside a case sits at depth 2.
_SWITCH_NODES = frozenset(
    {"expression_switch_statement", "type_switch_statement", "select_statement"}
)
#: A `block` opens nesting only under a control construct. A bare block -- whose
#: parent is another block -- does not, per contract section 10.
_BLOCK_CONTROL_PARENTS = frozenset({"if_statement", "for_statement"})


def _is_boundary(node: Any) -> bool:
    return node.type in BOUNDARY_NODES


def _opens_nesting(node: Any) -> bool:
    node_type = node.type
    if node_type in _SWITCH_NODES or node_type in _CASE_NODES:
        return True
    if node_type == "default_case":
        return True
    if node_type == "block":
        parent = node.parent
        return parent is not None and parent.type in _BLOCK_CONTROL_PARENTS
    return False


def _decision_increment(node: Any, source: bytes) -> int:
    return 1 if node.type in _DECISION_NODES else 0


_boolean_increment = _binary_operator_counter(_BOOLEAN_OPERATORS)


def _condition_nodes(node: Any):
    """Decision expressions: the boolean-valued expressions that drive a branch."""
    node_type = node.type
    if node_type == "if_statement":
        condition = node.child_by_field_name("condition")
        if condition is not None:
            yield condition
    elif node_type == "for_statement":
        # `for a; cond; b {}` keeps the condition inside a `for_clause`;
        # `for cond {}` puts a bare expression here; `for range xs {}` has none.
        for child in node.named_children:
            if child.type == "for_clause":
                condition = child.child_by_field_name("condition")
                if condition is not None:
                    yield condition
            elif child.type not in {"block", "range_clause"}:
                yield child
    elif node_type == "expression_case":
        # An expressionless `switch { case a && b: }` makes the case value a
        # genuine boolean condition.
        value = node.child_by_field_name("value")
        if value is not None:
            yield value


RULES = StructuralRules(
    is_boundary=_is_boundary,
    decision_increment=_decision_increment,
    boolean_increment=_boolean_increment,
    opens_nesting=_opens_nesting,
    condition_nodes=_condition_nodes,
)


def formal_parameter_count(node: Any) -> int:
    """Contract section 9 for Go: count DECLARED NAMES, receiver excluded.

    `func f(a, b int)` is two declarations and three names; `func f(int, string)`
    is two declarations and none, and a declaration with no identifier counts as
    one. The receiver is a separate field and never enters the count.
    """
    parameters = node.child_by_field_name("parameters")
    if parameters is None:
        return 0
    total = 0
    for child in parameters.named_children:
        if child.type == "variadic_parameter_declaration":
            total += 1
        elif child.type == "parameter_declaration":
            names = [item for item in child.named_children if item.type == "identifier"]
            total += len(names) if names else 1
    return total


def _line(point) -> int:
    """Tree-sitter rows are 0-based; every persisted line number is 1-based."""
    return point[0] + 1


def _receiver_evidence(node: Any, source: bytes) -> tuple[str | None, bool | None]:
    """Return ``(receiver_type_name, receiver_is_pointer)`` for a method.

    The type name excludes the pointer marker deliberately. Go forbids declaring
    both a value- and a pointer-receiver method of the same name on one type, so
    pointer-ness is not needed for uniqueness, and excluding it keeps the
    qualified name stable when a maintainer changes the receiver form -- which is
    a change a diff should *show*, not report as a delete plus an add.
    """
    receiver = node.child_by_field_name("receiver")
    if receiver is None:
        return None, None
    is_pointer = any(
        child.type == "pointer_type" for child in _iter_nodes(receiver)
    )
    for child in _iter_nodes(receiver):
        if child.type == "type_identifier":
            return _node_text(child, source), is_pointer
    return None, is_pointer


def discover_callables(
    root: Any, source: bytes, lines: LineIndex | None = None
) -> list[CallableRecord]:
    """Every Go callable in the canonical population, in document order."""
    records: list[CallableRecord] = []
    order = 0

    for node in _iter_nodes(root):
        node_type = node.type
        if node_type not in {"function_declaration", "method_declaration"}:
            continue
        # Identical guard chain to `_go_entities`. Kept literally parallel, in
        # the same order, so a reader can diff the two by eye.
        if not _at_module_scope(node):
            continue
        if not _reliable_declaration(node, "name", "parameters", "body"):
            continue
        if _contains_malformed(node.child_by_field_name("parameters")):
            continue

        name_node = node.child_by_field_name("name")
        body = node.child_by_field_name("body")
        name = _node_text(name_node, source)

        if node_type == "function_declaration":
            kind = KIND_MODULE_FUNCTION
            owner_kind: str | None = OWNER_MODULE
            owner_name: str | None = None
            receiver_type_name: str | None = None
            receiver_is_pointer: bool | None = None
            qualified_name = name
        else:
            kind = KIND_RECEIVER_METHOD
            receiver_type_name, receiver_is_pointer = _receiver_evidence(node, source)
            owner_kind = OWNER_RECEIVER_TYPE
            owner_name = receiver_type_name
            qualified_name = qualify(receiver_type_name, name)

        record = CallableRecord(
            name=name,
            qualified_name=qualified_name,
            callable_kind=kind,
            owner_kind=owner_kind,
            owner_name=owner_name,
            receiver_type_name=receiver_type_name,
            receiver_is_pointer=receiver_is_pointer,
            start_line=_line(node.start_point),
            end_line=_line(node.end_point),
            body_start_line=_line(body.start_point) if body is not None else None,
            body_end_line=_line(body.end_point) if body is not None else None,
            _order=order,
        )
        apply_metrics(
            record, measure(body, source, RULES), formal_parameter_count(node), lines
        )
        # Go's explicit current receiver is the DECLARED receiver identifier, so
        # `r.M()` inside `func (r T) M()` is recursion and `o.M()` is not.
        receivers = {receiver_type_name} if receiver_type_name else set()
        receiver_field = node.child_by_field_name("receiver")
        if receiver_field is not None:
            for parameter in receiver_field.named_children:
                identifier = parameter.child_by_field_name("name")
                if identifier is not None:
                    receivers.add(_node_text(identifier, source))
        record.cognitive_complexity = (
            measure_cognitive(
                body, source, GO_RULES,
                SelfCallContext(name, frozenset(receivers)),
            )
            if body is not None else 0
        )
        records.append(record)
        order += 1

    return records
