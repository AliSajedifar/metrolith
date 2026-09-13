"""JavaScript / TypeScript callable discovery.

Mirrors `core_metrics._js_ts_entities` branch for branch, including its two-pass
shape: classes are resolved first, because whether a `method_definition` is a
class method or an anonymous-class method depends on whether its owning class was
counted.

The population is narrower than "every function in the file", and deliberately
so. A stable named module-scope binding counts; an anonymous callback does not.
A method of a counted class counts; a method of an uncounted class expression is
an anonymous-class method. Those distinctions are `_js_ts_entities`'s, not this
module's, which is why every predicate is imported from the shared seam.

**Discovery is global.** A class declared inside a function is still a counted
class, so its methods are still in the population. Discovery walks the whole file
to find them; `BOUNDARY_NODES` governs metric attribution only.

Never import `modules.core_metrics` here; see the package docstring.
"""

from __future__ import annotations

from typing import Any

from modules.syntax_predicates import (
    _at_module_scope,
    _commonjs_assignment,
    _field_is_reliable,
    _has_callable_ancestor,
    _is_named_module_variable,
    _iter_nodes,
    _named_object_container,
    _node_text,
    _reliable_declaration,
    _stably_assigned_class,
)

from .cognitive import SelfCallContext, measure_cognitive
from .cognitive_rules import JS_TS_RULES
from .metrics import (
    LineIndex,
    StructuralMetrics,
    StructuralRules,
    _binary_operator_counter,
    apply_metrics,
    measure,
)
from .model import (
    KIND_CLASS_METHOD,
    KIND_MODULE_FUNCTION,
    OWNER_CLASS,
    OWNER_MODULE,
    OWNER_OBJECT_LITERAL,
    CallableRecord,
)

#: Ends metric attribution for the enclosing callable (C2). Not consulted by
#: discovery. Note the ABSENCE of the bare `class` keyword token type: these are
#: matched against named nodes only, and `_iter_all_nodes` would otherwise report
#: a `class` node for every `class` keyword.
BOUNDARY_NODES = frozenset(
    {
        "function_declaration",
        "generator_function_declaration",
        "function_expression",
        "arrow_function",
        "generator_function",
        "method_definition",
        "class_declaration",
        "abstract_class_declaration",
    }
)

_FUNCTION_DECLARATIONS = {"function_declaration", "generator_function_declaration"}
_FUNCTION_EXPRESSIONS = {"function_expression", "arrow_function", "generator_function"}

_DECISION_NODES = frozenset(
    {
        "if_statement",
        "while_statement",
        "do_statement",
        "for_statement",
        "for_in_statement",  # covers both `for..in` and `for..of`
        "catch_clause",
        "ternary_expression",
        "switch_case",
    }
)

#: Stated explicitly: `switch_default`, `finally_clause` and a bare `else` add
#: nothing, and `optional_chain` (`?.`) is not a decision under contract 7.1.
_ZERO_DECISION_NODES = frozenset(
    {"switch_default", "finally_clause", "optional_chain"}
)

#: `??` and the logical assignments are genuine short-circuit alternative paths
#: and are counted; contract section 7.2 records the divergence from tools that
#: count only `&&` and `||`.
_BOOLEAN_OPERATORS = frozenset({"&&", "||", "??", "&&=", "||=", "??="})

_BLOCK_CONTROL_PARENTS = frozenset(
    {
        "if_statement",
        "while_statement",
        "do_statement",
        "for_statement",
        "for_in_statement",
        "try_statement",
        "catch_clause",
        "finally_clause",
    }
)


def _line(point) -> int:
    return point[0] + 1


def _span_start_line(node: Any) -> int:
    """Declaration start, extended back over attached decorators.

    Established by probe (validation/complexity_c0_20260810): JS/TS decorators
    are **preceding siblings**, not children, so a decorated method's own
    `start_point` is the line BELOW its decorators. Contract section 8.1 includes
    them in the span, so the start walks back over contiguous preceding sibling
    `decorator` nodes -- all of them, not just the nearest.
    """
    start = _line(node.start_point)
    parent = node.parent
    if parent is None:
        return start
    children = list(parent.children)
    try:
        index = children.index(node)
    except ValueError:  # pragma: no cover - defensive
        return start
    for previous in reversed(children[:index]):
        if previous.type != "decorator":
            break
        start = _line(previous.start_point)
    return start


def _is_boundary(node: Any) -> bool:
    return node.type in BOUNDARY_NODES


#: The field each control construct holds its branch body in. Contract section
#: 10 nests on entering the BODY, so an unbraced single statement in one of
#: these slots opens a level exactly as a `{ }` block does. `alternative` is
#: absent deliberately: in this grammar it is an `else_clause`, handled below.
_CONTROL_BODY_FIELDS = {
    "if_statement": ("consequence",),
    "while_statement": ("body",),
    "do_statement": ("body",),
    "for_statement": ("body",),
    "for_in_statement": ("body",),
}


def _same_node(left: Any, right: Any) -> bool:
    return left is not None and left.id == right.id


def _is_branch_body(node: Any) -> bool:
    """This node occupies a control construct's branch-body slot."""
    parent = node.parent
    if parent is None:
        return False
    if parent.type == "else_clause":
        # `else if` reads flat and measures flat; any other else body nests.
        return node.type != "if_statement"
    if node.type == "statement_block":
        return parent.type in _BLOCK_CONTROL_PARENTS
    fields = _CONTROL_BODY_FIELDS.get(parent.type)
    if not fields:
        return False
    # Compared by node id: `child_by_field_name` builds a fresh wrapper on every
    # call, so `is` is never true even for the same node.
    return any(_same_node(parent.child_by_field_name(field), node) for field in fields)


def _opens_nesting(node: Any) -> bool:
    if node.type in {"switch_body", "switch_case", "switch_default"}:
        return True
    return _is_branch_body(node)


def _unbraced_body(node: Any) -> bool:
    """An unbraced body IS the statement inside the branch, not a container."""
    return node.type != "statement_block" and _is_branch_body(node)


def _decision_increment(node: Any, source: bytes) -> int:
    return 1 if node.type in _DECISION_NODES else 0


_boolean_increment = _binary_operator_counter(_BOOLEAN_OPERATORS)


def _condition_nodes(node: Any):
    if node.type in {
        "if_statement", "while_statement", "do_statement", "ternary_expression",
        "for_statement",
    }:
        condition = node.child_by_field_name("condition")
        if condition is not None:
            yield condition


RULES = StructuralRules(
    is_boundary=_is_boundary,
    decision_increment=_decision_increment,
    boolean_increment=_boolean_increment,
    opens_nesting=_opens_nesting,
    condition_nodes=_condition_nodes,
    unbraced_body=_unbraced_body,
)


def _is_typescript_this_parameter(node: Any, source: bytes) -> bool:
    """TypeScript's `this` parameter is a type-checking device, not an argument."""
    pattern = node.child_by_field_name("pattern")
    target = pattern if pattern is not None else (
        node.named_children[0] if node.named_children else None
    )
    return target is not None and _node_text(target, source).strip() == "this"


def formal_parameter_count(node: Any, source: bytes) -> tuple[int, bool | None]:
    """Contract section 9 for JS/TS. Returns ``(count, declares_this)``.

    One per declared parameter: a destructuring pattern is ONE, a rest parameter
    is ONE, a default is one. The TypeScript `this` parameter is excluded, and
    the flag records that the exclusion applied so the published number is
    explainable.

    A `comment` is a NAMED child of `formal_parameters` in these grammars, so
    counting named children counts prose: a two-parameter list documented with
    two comments published 4. Found by the C4 Layer-C2 campaign.
    """
    parameters = node.child_by_field_name("parameters")
    if parameters is None:
        # An arrow with a single unparenthesized parameter: `x => x * 2`.
        single = node.child_by_field_name("parameter")
        return (1, None) if single is not None else (0, None)
    total = 0
    declares_this = False
    for child in parameters.named_children:
        if child.type == "comment":
            continue
        if _is_typescript_this_parameter(child, source):
            declares_this = True
            continue
        total += 1
    return total, (True if declares_this else None)


def _declarator_name(node: Any, source: bytes) -> str | None:
    """Name of the `variable_declarator` a value is bound to, if any."""
    parent = node.parent
    if parent is None or parent.type != "variable_declarator":
        return None
    name = parent.child_by_field_name("name")
    return _node_text(name, source) if name is not None else None


def _counted_classes(root: Any, source: bytes) -> dict[int, str]:
    """`node.id -> class name` for every class `_js_ts_entities` counts.

    Same two rules, same order: a named declaration, or a class expression held
    by a stable module-scope binding or an exact CommonJS export target.
    """
    counted: dict[int, str] = {}
    for node in _iter_nodes(root):
        if node.type in {"class_declaration", "abstract_class_declaration"}:
            if _reliable_declaration(node, "name", "body"):
                name_node = node.child_by_field_name("name")
                counted[node.id] = _node_text(name_node, source)
        elif node.type == "class":
            if _field_is_reliable(node, "body") and _stably_assigned_class(node, source):
                name_node = node.child_by_field_name("name")
                counted[node.id] = (
                    _node_text(name_node, source)
                    if name_node is not None
                    else _declarator_name(node, source)
                    or _commonjs_assignment(node, source)
                    or "<class>"
                )
    return counted


def _scope_name(node: Any, source: bytes, counted: dict[int, str]) -> str | None:
    """The owner-chain segment an ancestor contributes, or None."""
    node_type = node.type
    if node_type in {"class_declaration", "abstract_class_declaration", "class"}:
        return counted.get(node.id)
    if node_type in _FUNCTION_DECLARATIONS:
        name = node.child_by_field_name("name")
        return _node_text(name, source) if name is not None else None
    if node_type == "method_definition":
        name = node.child_by_field_name("name")
        return _node_text(name, source) if name is not None else None
    if node_type in _FUNCTION_EXPRESSIONS:
        return _declarator_name(node, source)
    if node_type == "object" and _named_object_container_parent(node):
        return _declarator_name_of_object(node, source)
    return None


def _named_object_container_parent(node: Any) -> bool:
    container = node.parent
    return bool(
        container is not None
        and container.type == "variable_declarator"
        and container.child_by_field_name("value") == node
        and _at_module_scope(container)
    )


def _declarator_name_of_object(node: Any, source: bytes) -> str | None:
    container = node.parent
    if container is None:
        return None
    name = container.child_by_field_name("name")
    return _node_text(name, source) if name is not None else None


def _owner_chain(node: Any, source: bytes, counted: dict[int, str]) -> list[str]:
    chain: list[str] = []
    current = node.parent
    while current is not None:
        segment = _scope_name(current, source, counted)
        if segment:
            chain.append(segment)
        current = current.parent
    chain.reverse()
    return chain


def discover_callables(
    root: Any, source: bytes, lines: LineIndex | None = None
) -> list[CallableRecord]:
    """Every JS/TS callable in the canonical population, in document order."""
    counted = _counted_classes(root, source)
    records: list[CallableRecord] = []
    order = 0

    def emit(
        node: Any,
        name: str,
        kind: str,
        owner_kind: str | None,
        owner_name: str | None,
    ) -> None:
        nonlocal order
        body = node.child_by_field_name("body")
        chain = _owner_chain(node, source, counted)
        parameter_count, declares_this = formal_parameter_count(node, source)
        record = CallableRecord(
                name=name,
                qualified_name=".".join((*chain, name)),
                callable_kind=kind,
                # TypeScript overload SIGNATURES are `signature_only_methods` and
                # outside the population, so the implementation is unique by name
                # within its owner and needs no discriminator. Should a genuine
                # collision ever occur, `assign_row_ids` falls back to positional.
                signature_discriminator=None,
                owner_kind=owner_kind,
                owner_name=owner_name,
                start_line=_span_start_line(node),
                end_line=_line(node.end_point),
                body_start_line=_line(body.start_point) if body is not None else None,
                body_end_line=_line(body.end_point) if body is not None else None,
                _order=order,
        )
        record.declares_typescript_this_parameter = declares_this
        # An arrow body can be an expression rather than a block; measuring it
        # directly is correct -- `x => a && b` has one boolean operator.
        apply_metrics(
            record,
            measure(body, source, RULES) if body is not None else StructuralMetrics(),
            parameter_count,
            lines,
        )
        receivers = {"this"}
        if owner_name:
            receivers.add(owner_name.split(".")[-1])
        record.cognitive_complexity = (
            measure_cognitive(
                body, source, JS_TS_RULES, SelfCallContext(name, frozenset(receivers))
            )
            if body is not None else 0
        )
        records.append(record)
        order += 1

    for node in _iter_nodes(root):
        node_type = node.type

        if node_type in _FUNCTION_DECLARATIONS:
            if not _reliable_declaration(node, "name", "parameters", "body"):
                continue
            if _has_callable_ancestor(node):
                continue                      # nested_functions
            if not _at_module_scope(node):
                continue                      # counted nowhere by _js_ts_entities
            name_node = node.child_by_field_name("name")
            emit(node, _node_text(name_node, source), KIND_MODULE_FUNCTION,
                 OWNER_MODULE, None)

        elif node_type in _FUNCTION_EXPRESSIONS:
            body = node.child_by_field_name("body")
            if body is None or not _reliable_declaration(node, "parameters", "body"):
                continue
            commonjs_name = _commonjs_assignment(node, source)
            if not (_is_named_module_variable(node) or commonjs_name is not None):
                continue                      # nested_functions / anonymous_functions
            name = _declarator_name(node, source) or commonjs_name or "<anonymous>"
            emit(node, name, KIND_MODULE_FUNCTION, OWNER_MODULE, None)

        elif node_type == "method_definition":
            name_node = node.child_by_field_name("name")
            name = _node_text(name_node, source) if name_node is not None else ""
            if name == "constructor":
                continue                      # constructors are secondary
            body = node.child_by_field_name("body")
            if body is None:
                continue                      # signature_only_methods
            parent = node.parent
            if parent is not None and parent.type == "class_body":
                owner = parent.parent
                if owner is None or owner.id not in counted:
                    continue                  # anonymous_class_methods
                if not _reliable_declaration(node, "name", "parameters", "body"):
                    continue
                emit(node, name, KIND_CLASS_METHOD, OWNER_CLASS, counted[owner.id])
            elif _named_object_container(node) and _reliable_declaration(
                node, "name", "parameters", "body"
            ):
                owner_name = (
                    _declarator_name_of_object(node.parent, source)
                    if node.parent is not None
                    else None
                )
                emit(node, name, KIND_CLASS_METHOD, OWNER_OBJECT_LITERAL, owner_name)

    return records
