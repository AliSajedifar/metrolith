"""Java callable discovery.

Mirrors the `method_declaration` branch of `core_metrics._java_entities`: the
same `_field_is_reliable(node, "name")` pre-check, the same `_java_method_owner`
classification, the same `_reliable_declaration(node, "name", "parameters",
"body")` guard. A method counts when it has a body and a **named** owner, which
is what makes interface `default`/`static` methods, enum-body methods and the
methods of named local classes part of the population while anonymous-class
methods are not.

**Discovery is global.** `_iter_nodes` walks the whole file, so a method of a
named class declared inside another method is found. The class and method
boundaries in Complexity Contract 1.0.0 section 3 stop *metric attribution* only.

Never import `modules.core_metrics` here; see the package docstring.
"""

from __future__ import annotations

from typing import Any

from modules.syntax_predicates import (
    _field_is_reliable,
    _iter_nodes,
    _java_method_owner,
    _node_text,
    _reliable_declaration,
)

from .cognitive import SelfCallContext, measure_cognitive
from .cognitive_rules import JAVA_RULES
from .metrics import (
    LineIndex,
    StructuralRules,
    _binary_operator_counter,
    apply_metrics,
    measure,
)
from .model import (
    KIND_CLASS_METHOD,
    OWNER_CLASS,
    OWNER_ENUM,
    OWNER_INTERFACE,
    OWNER_RECORD,
    CallableRecord,
)

#: Ends metric attribution for the enclosing callable (C2). Not consulted by
#: discovery.
BOUNDARY_NODES = frozenset(
    {
        "method_declaration",
        "constructor_declaration",
        "compact_constructor_declaration",
        "lambda_expression",
        "class_declaration",
        "record_declaration",
        "enum_declaration",
        "interface_declaration",
        "object_creation_expression",
        "static_initializer",
    }
)

_DECISION_NODES = frozenset(
    {
        "if_statement",
        "while_statement",
        "do_statement",
        "for_statement",
        "enhanced_for_statement",
        "catch_clause",
        "ternary_expression",
        "guard",  # Java 21 `case P when g` -- the guard is its own node
    }
)

#: Stated explicitly rather than by omission: `finally` and a bare `else` add no
#: decision, and a `default` label adds none either (handled in the increment).
_ZERO_DECISION_NODES = frozenset({"finally_clause"})

_BOOLEAN_OPERATORS = frozenset({"&&", "||"})

#: A `block` opens nesting only under a control construct. A bare block -- whose
#: parent is another block -- does not.
#:
#: `synchronized_statement` IS here: max nesting is structural nesting, and the
#: contract already treats semantic blocks that contribute no decision (Python
#: `with`, `try`/`finally`, case bodies) as nesting-increasing. `synchronized`
#: contributes 0 to cyclomatic complexity for the same reason those do, and
#: nests for the same reason too.
_BLOCK_CONTROL_PARENTS = frozenset(
    {
        "if_statement",
        "while_statement",
        "do_statement",
        "for_statement",
        "enhanced_for_statement",
        "try_statement",
        "try_with_resources_statement",
        "catch_clause",
        "finally_clause",
        "synchronized_statement",
    }
)

#: The switch body opens one level and each arm opens another, so a statement
#: inside a case sits at depth 2. Both the colon form
#: (`switch_block_statement_group`) and the arrow form (`switch_rule`) are arms.
_SWITCH_ARM_NODES = frozenset({"switch_block_statement_group", "switch_rule"})

_TYPE_DECLARATIONS = {
    "class_declaration": OWNER_CLASS,
    "record_declaration": OWNER_RECORD,
    "interface_declaration": OWNER_INTERFACE,
    "enum_declaration": OWNER_ENUM,
    "annotation_type_declaration": OWNER_CLASS,
}

#: Contribute a segment to the lexical owner chain. A method does, because a
#: local class declared inside one is genuinely owned by it; a plain block does
#: not.
_SCOPE_NODES = frozenset(set(_TYPE_DECLARATIONS) | {"method_declaration"})


def _line(point) -> int:
    return point[0] + 1


def _is_boundary(node: Any) -> bool:
    node_type = node.type
    if node_type == "object_creation_expression":
        # A boundary only when it carries an anonymous class body. A plain
        # `new Foo(a && b)` is an ordinary expression of the enclosing callable
        # and its operators must still count.
        return any(child.type == "class_body" for child in node.named_children)
    if node_type == "block":
        # An instance initializer block: a class member, not a statement of the
        # enclosing callable.
        parent = node.parent
        return parent is not None and parent.type == "class_body"
    return node_type in BOUNDARY_NODES


#: The field each control construct holds its branch body in. Contract section
#: 10 nests on entering the BODY, so an unbraced single statement in one of
#: these slots opens a level exactly as a `{ }` block does -- found by the C4
#: Layer-C2 campaign on `if (hex.length() == 1) hexString.append('0');`.
_CONTROL_BODY_FIELDS = {
    "if_statement": ("consequence", "alternative"),
    "while_statement": ("body",),
    "do_statement": ("body",),
    "for_statement": ("body",),
    "enhanced_for_statement": ("body",),
}


def _same_node(left: Any, right: Any) -> bool:
    return left is not None and left.id == right.id


def _is_branch_body(node: Any) -> bool:
    """This node occupies a control construct's branch-body slot."""
    parent = node.parent
    if parent is None:
        return False
    if node.type == "block":
        return parent.type in _BLOCK_CONTROL_PARENTS
    fields = _CONTROL_BODY_FIELDS.get(parent.type)
    if not fields:
        return False
    if (
        parent.type == "if_statement"
        and node.type == "if_statement"
        and _same_node(parent.child_by_field_name("alternative"), node)
    ):
        # `else if` reads flat and measures flat.
        return False
    # Compared by node id: `child_by_field_name` builds a fresh wrapper on every
    # call, so `is` is never true even for the same node.
    return any(_same_node(parent.child_by_field_name(field), node) for field in fields)


def _opens_nesting(node: Any) -> bool:
    node_type = node.type
    if node_type == "switch_block" or node_type in _SWITCH_ARM_NODES:
        return True
    return _is_branch_body(node)


def _unbraced_body(node: Any) -> bool:
    """An unbraced body IS the statement inside the branch, not a container."""
    return node.type != "block" and _is_branch_body(node)


def _decision_increment(node: Any, source: bytes) -> int:
    node_type = node.type
    if node_type in _DECISION_NODES:
        return 1
    if node_type == "switch_label":
        # One arm each; `default` contributes nothing. Counting labels rather
        # than groups keeps colon-form fallthrough (`case 1: case 2:`) at two.
        return 1 if _node_text(node, source).strip().startswith("case") else 0
    return 0


_boolean_increment = _binary_operator_counter(_BOOLEAN_OPERATORS)


def _condition_nodes(node: Any):
    node_type = node.type
    if node_type in {"if_statement", "while_statement", "do_statement", "ternary_expression"}:
        condition = node.child_by_field_name("condition")
        if condition is not None:
            yield condition
    elif node_type == "for_statement":
        condition = node.child_by_field_name("condition")
        if condition is not None:
            yield condition
    elif node_type == "guard":
        yield node


RULES = StructuralRules(
    is_boundary=_is_boundary,
    decision_increment=_decision_increment,
    boolean_increment=_boolean_increment,
    opens_nesting=_opens_nesting,
    condition_nodes=_condition_nodes,
    unbraced_body=_unbraced_body,
)


def formal_parameter_count(node: Any) -> int:
    """Contract section 9 for Java: `formal_parameter` and `spread_parameter` only.

    `receiver_parameter` is a distinct node type and is excluded. Counting
    `named_children` would include it and report `void m(Foo Foo.this, int a)`
    as two parameters instead of one. Varargs `T... x` is a single
    `spread_parameter` and counts once.
    """
    parameters = node.child_by_field_name("parameters")
    if parameters is None:
        return 0
    return sum(
        1
        for child in parameters.named_children
        if child.type in {"formal_parameter", "spread_parameter"}
    )


def _owner_chain(node: Any, source: bytes) -> tuple[list[str], str | None, str | None]:
    """Return ``(chain, owner_kind, owner_name)`` for a method declaration."""
    chain: list[str] = []
    owner_kind: str | None = None
    owner_name: str | None = None
    current = node.parent
    while current is not None:
        if current.type in _SCOPE_NODES:
            name_node = current.child_by_field_name("name")
            if name_node is not None:
                name = _node_text(name_node, source)
                chain.append(name)
                if owner_kind is None:
                    owner_kind = _TYPE_DECLARATIONS.get(current.type, OWNER_CLASS)
                    owner_name = name
        current = current.parent
    chain.reverse()
    return chain, owner_kind, owner_name


def _signature_discriminator(node: Any, source: bytes) -> str | None:
    """The parameter type list as written in source, whitespace-collapsed.

    Java overloads differ only by parameter types, so the qualified name alone
    cannot separate them. This is deliberately **syntactic**: Metrolith has no
    symbol table, so a type alias and its fully qualified form produce different
    discriminators. Recorded as a contract limitation rather than approximated.
    """
    parameters = node.child_by_field_name("parameters")
    if parameters is None:
        return None
    types: list[str] = []
    for child in parameters.named_children:
        if child.type not in {"formal_parameter", "spread_parameter"}:
            continue  # `receiver_parameter` is not a formal parameter
        type_node = child.child_by_field_name("type")
        if type_node is None and child.type == "spread_parameter":
            # tree-sitter-java gives `spread_parameter` NO named fields: its
            # children are the type, the literal `...`, then a
            # `variable_declarator`. Reading a `type` field yields None, which
            # would render every varargs parameter identically and collide
            # `f(long...)` with `f(int...)`.
            type_node = next(
                (
                    item
                    for item in child.children
                    if item.type not in {"...", "variable_declarator", "modifiers"}
                ),
                None,
            )
        text = _node_text(type_node, source) if type_node is not None else "?"
        if child.type == "spread_parameter":
            text = f"{text}..."
        types.append(" ".join(text.split()))
    return f"({','.join(types)})"


def discover_callables(
    root: Any, source: bytes, lines: LineIndex | None = None
) -> list[CallableRecord]:
    """Every Java callable in the canonical population, in document order."""
    records: list[CallableRecord] = []
    order = 0

    for node in _iter_nodes(root):
        if node.type != "method_declaration":
            continue
        # Identical guard chain to `_java_entities`, in the same order.
        if not _field_is_reliable(node, "name"):
            continue
        body = node.child_by_field_name("body")
        if body is None:
            continue  # signature_only_methods
        if _java_method_owner(node) != "named":
            continue  # anonymous_class_methods, or no owner at all
        if not _reliable_declaration(node, "name", "parameters", "body"):
            continue

        name = _node_text(node.child_by_field_name("name"), source)
        chain, owner_kind, owner_name = _owner_chain(node, source)
        record = CallableRecord(
            name=name,
            qualified_name=".".join((*chain, name)),
            callable_kind=KIND_CLASS_METHOD,
            signature_discriminator=_signature_discriminator(node, source),
            owner_kind=owner_kind,
            owner_name=owner_name,
            start_line=_line(node.start_point),
            end_line=_line(node.end_point),
            body_start_line=_line(body.start_point),
            body_end_line=_line(body.end_point),
            _order=order,
        )
        apply_metrics(
            record, measure(body, source, RULES), formal_parameter_count(node), lines
        )
        # `this.m()` inside `m` is recursion; `Type.m()` is too when the type is
        # the enclosing one. `other.m()` is not.
        receivers = {"this"}
        if owner_name:
            receivers.add(owner_name.split(".")[-1])
        record.cognitive_complexity = measure_cognitive(
            body, source, JAVA_RULES, SelfCallContext(name, frozenset(receivers))
        )
        records.append(record)
        order += 1

    return records
