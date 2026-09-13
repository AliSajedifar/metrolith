"""Python callable discovery.

Mirrors `core_metrics._PythonEntityVisitor` statement for statement: the same
`callable_depth` / `class_stack` state, the same order of tests, the same
`python_is_overload` guard from the shared seam. Only the output differs -- a
record instead of a counter increment.

**Discovery is global.** The `ClassDef` boundary in Complexity Contract 1.0.0
section 3 stops *metric attribution*, never discovery: a method of a class
declared inside a function is in the canonical population and must receive its
own row. Descending through the class is exactly how it is found.

Never import `modules.core_metrics` here; see the package docstring.
"""

from __future__ import annotations

import ast

from modules.syntax_predicates import python_is_overload

from .metrics import LineIndex, StructuralMetrics, apply_metrics
from .model import (
    KIND_CLASS_METHOD,
    KIND_MODULE_FUNCTION,
    OWNER_CLASS,
    OWNER_MODULE,
    CallableRecord,
)

#: Nodes that end metric attribution for the enclosing callable (C2). Listed
#: here so the boundary set lives beside the language it describes, and so a
#: reader can see that it is NOT consulted by discovery below.
BOUNDARY_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)

#: Excluded from `methods_functions` as constructors, so outside the population.
_CONSTRUCTOR_NAMES = {"__init__", "__new__"}

#: Statement fields whose bodies open a nesting level (contract section 10).
_NESTING_BODY_FIELDS = {
    ast.If: ("body", "orelse"),
    ast.For: ("body", "orelse"),
    ast.AsyncFor: ("body", "orelse"),
    ast.While: ("body", "orelse"),
    ast.With: ("body",),
    ast.AsyncWith: ("body",),
    ast.Try: ("body", "orelse", "finalbody"),
    ast.ExceptHandler: ("body",),
}
if hasattr(ast, "TryStar"):  # pragma: no branch - version dependent
    _NESTING_BODY_FIELDS[ast.TryStar] = ("body", "orelse", "finalbody")


def _is_elif(node: ast.If) -> bool:
    """`elif`, as opposed to an `else:` whose body happens to be an `if`.

    Both render as ``orelse == [If]`` -- stdlib `ast` keeps no `elif` token --
    so shape alone cannot tell them apart, and flattening on shape flattened a
    real `else` branch too. Contract section 10 makes an `else` branch
    nesting-increasing, so the two must be distinguished. Column does it: an
    `elif` starts at the outer `if`'s column, an indented `if` starts further
    right. Found by the C4 Layer-C2 campaign.
    """
    if len(node.orelse) != 1 or not isinstance(node.orelse[0], ast.If):
        return False
    return node.orelse[0].col_offset == node.col_offset


def _is_wildcard_pattern(pattern: ast.AST) -> bool:
    """`case _:` -- a bare wildcard adds no decision, like `default`."""
    return (
        isinstance(pattern, ast.MatchAs)
        and pattern.pattern is None
        and pattern.name is None
    )


def _boolean_operators_excluding_boundaries(node: ast.AST | None) -> int:
    """Short-circuit operators in one expression, boundaries excluded.

    An `n`-value `BoolOp` carries `n - 1` operators: `a and b and c` is one node
    with three values and two operators.

    `ast.walk` would descend into a lambda, so boundaries are pruned explicitly
    and the node itself is tested -- a condition that IS a `BoolOp` must count.
    """
    if node is None:
        return 0
    total = 0
    stack: list[ast.AST] = [node]
    while stack:
        current = stack.pop()
        if isinstance(current, BOUNDARY_NODES):
            continue
        if isinstance(current, ast.BoolOp):
            total += len(current.values) - 1
        stack.extend(ast.iter_child_nodes(current))
    return total


class _MetricVisitor:
    """Bounded descent over one Python callable's own body.

    Written as an explicit walker rather than `ast.NodeVisitor` because nesting
    depth is a property of which *field* a statement sits in -- an `elif` lives
    in `orelse` and must not nest, while an `else` block must.
    """

    def __init__(self) -> None:
        self.decisions = 0
        self.booleans = 0
        self.max_condition = 0
        self.max_depth = 0

    def _condition(self, node: ast.AST | None) -> None:
        """Contract section 11: operators within ONE decision expression."""
        found = _boolean_operators_excluding_boundaries(node)
        if found > self.max_condition:
            self.max_condition = found

    def _expression(self, node: ast.AST | None, depth: int) -> None:
        """Count decisions and operators inside an expression.

        The node itself is tested, not only its children: an assignment's value
        may BE the comprehension or the `BoolOp`, and testing only children
        silently dropped every comprehension.
        """
        if node is None:
            return
        stack: list[ast.AST] = [node]
        while stack:
            current = stack.pop()
            if isinstance(current, BOUNDARY_NODES):
                continue
            if isinstance(current, ast.BoolOp):
                self.booleans += len(current.values) - 1
            elif isinstance(current, ast.IfExp):
                self.decisions += 1
                self._condition(current.test)
            elif isinstance(
                current, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
            ):
                for generator in current.generators:
                    # A comprehension is a loop: one per `for` clause and one per
                    # `if` clause (contract section 7.1).
                    self.decisions += 1
                    self.decisions += len(generator.ifs)
                    for condition in generator.ifs:
                        self._condition(condition)
            stack.extend(ast.iter_child_nodes(current))

    def walk_body(self, body: list[ast.stmt], depth: int) -> None:
        if depth > self.max_depth and body:
            self.max_depth = depth
        for statement in body:
            self.walk_statement(statement, depth)

    def walk_statement(self, node: ast.stmt, depth: int) -> None:
        if isinstance(node, BOUNDARY_NODES):
            return

        if isinstance(node, ast.If):
            self.decisions += 1
            self._condition(node.test)
            self._expression(node.test, depth)
            self.walk_body(node.body, depth + 1)
            # `elif` is an `If` alone in `orelse`. It reads flat and must measure
            # flat, so it stays at this depth; a real `else` block nests.
            if _is_elif(node):
                self.walk_statement(node.orelse[0], depth)
            else:
                self.walk_body(node.orelse, depth + 1)
            return

        if isinstance(node, (ast.For, ast.AsyncFor, ast.While)):
            self.decisions += 1
            if isinstance(node, ast.While):
                self._condition(node.test)
                self._expression(node.test, depth)
            else:
                self._expression(node.iter, depth)
            self.walk_body(node.body, depth + 1)
            self.walk_body(node.orelse, depth + 1)
            return

        if isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                self._expression(item.context_expr, depth)
            self.walk_body(node.body, depth + 1)
            return

        if isinstance(node, ast.Try) or (
            hasattr(ast, "TryStar") and isinstance(node, ast.TryStar)
        ):
            self.walk_body(node.body, depth + 1)
            for handler in node.handlers:
                # `except` is a decision; `try`, `else` and `finally` are not.
                self.decisions += 1
                self.walk_body(handler.body, depth + 1)
            self.walk_body(node.orelse, depth + 1)
            self.walk_body(node.finalbody, depth + 1)
            return

        if isinstance(node, ast.Match):
            self._expression(node.subject, depth)
            for case in node.cases:
                if not _is_wildcard_pattern(case.pattern):
                    self.decisions += 1
                if case.guard is not None:
                    self.decisions += 1
                    self._condition(case.guard)
                    self._expression(case.guard, depth + 1)
                # The match opens one level and each case another, matching the
                # switch treatment in the other four languages.
                self.walk_body(case.body, depth + 2)
            return

        # An ordinary statement: only its expressions contribute.
        for child in ast.iter_child_nodes(node):
            if isinstance(child, BOUNDARY_NODES):
                continue
            if isinstance(child, ast.stmt):
                self.walk_statement(child, depth)
            else:
                self._expression(child, depth)



# ---------------------------------------------------------------------------
# Metrolith Cognitive Complexity (G1-B) -- frozen rule table revision 3.
#
# Python gets its own reading for the same reason the cyclomatic engine does:
# the `ast` tree is a different shape from a tree-sitter one. The RULES are the
# frozen ones; only the traversal differs. In-memory only -- no artifact column,
# no Complexity Contract 2.0.0.
# ---------------------------------------------------------------------------


def _cognitive_boolean_runs(node: ast.AST) -> int:
    """Maximal same-operator runs in flattened SOURCE order (section 3.2).

    `ast` records no operator position, so each operator is located by the
    operand that follows it: in `a or b and c or d` the two `or`s and the one
    `and` interleave, giving three runs rather than the two a tree-shaped
    reading would produce.
    """
    operators: list[tuple[tuple[int, int], str]] = []

    def collect(current: ast.AST) -> None:
        if isinstance(current, BOUNDARY_NODES):
            return
        if isinstance(current, ast.BoolOp):
            symbol = "and" if isinstance(current.op, ast.And) else "or"
            for following in current.values[1:]:
                operators.append(
                    ((following.lineno, following.col_offset), symbol)
                )
        for child in ast.iter_child_nodes(current):
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


def _cognitive_is_self_call(node: ast.AST, name: str, receivers: frozenset[str]) -> bool:
    """Frozen section 4.2: bare self-name, or explicit-receiver member call."""
    if not isinstance(node, ast.Call):
        return False
    target = node.func
    if isinstance(target, ast.Name):
        return target.id == name
    if isinstance(target, ast.Attribute) and target.attr == name:
        return isinstance(target.value, ast.Name) and target.value.id in receivers
    return False


_COMPREHENSIONS = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)


def measure_cognitive_python(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    receivers: frozenset[str] = frozenset(),
) -> int:
    """Cognitive complexity of one Python callable. Starts at 0."""
    total = 0
    recursion = False
    name = node.name

    def expression(item: ast.AST, depth: int) -> None:
        """Expressions carry ternaries, comprehensions and boolean runs."""
        nonlocal total, recursion
        if isinstance(item, BOUNDARY_NODES):
            return
        if _cognitive_is_self_call(item, name, receivers):
            recursion = True
        if isinstance(item, ast.BoolOp):
            total += _cognitive_boolean_runs(item)
            for child in ast.walk(item):
                if child is item or isinstance(child, (ast.BoolOp,)):
                    continue
                if isinstance(child, BOUNDARY_NODES):
                    continue
                if isinstance(child, (ast.IfExp,) + _COMPREHENSIONS):
                    expression(child, depth)
                elif _cognitive_is_self_call(child, name, receivers):
                    recursion = True
            return
        if isinstance(item, ast.IfExp):
            total += 1 + depth                                   # S-TERNARY
            expression(item.test, depth)
            expression(item.body, depth + 1)
            expression(item.orelse, depth + 1)
            return
        if isinstance(item, _COMPREHENSIONS):
            total += 1 + depth                          # S-COMPREHENSION, once
            for generator in item.generators:
                expression(generator.iter, depth)
                for condition in generator.ifs:
                    total += 1                                   # F-COMPIF
                    expression(condition, depth + 1)
            for child in ast.iter_child_nodes(item):
                if isinstance(child, ast.comprehension):
                    continue
                expression(child, depth + 1)
            return
        for child in ast.iter_child_nodes(item):
            expression(child, depth)

    def body(statements: list[ast.stmt], depth: int) -> None:
        for statement in statements:
            walk(statement, depth)

    def walk(statement: ast.AST, depth: int) -> None:
        nonlocal total, recursion
        if isinstance(statement, BOUNDARY_NODES):
            return                                               # B-NESTED

        if isinstance(statement, ast.If):
            total += 1 + depth                                   # S-IF
            expression(statement.test, depth)
            body(statement.body, depth + 1)
            if statement.orelse:
                if _is_elif(statement):
                    # F-ELSEIF: flat, and the chain does not deepen.
                    total += 1
                    inner = statement.orelse[0]
                    expression(inner.test, depth)
                    body(inner.body, depth + 1)
                    if inner.orelse:
                        walk_else(inner, depth)
                else:
                    total += 1                                   # F-ELSE
                    body(statement.orelse, depth + 1)
            return

        if isinstance(statement, (ast.For, ast.AsyncFor, ast.While)):
            total += 1 + depth                                   # S-LOOP
            if isinstance(statement, ast.While):
                expression(statement.test, depth)
            else:
                expression(statement.iter, depth)
                expression(statement.target, depth)
            body(statement.body, depth + 1)
            if statement.orelse:
                total += 1                                       # F-LOOPELSE
                body(statement.orelse, depth + 1)
            return

        if isinstance(statement, ast.Try) or (
            hasattr(ast, "TryStar") and isinstance(statement, ast.TryStar)
        ):
            body(statement.body, depth)                          # Z-TRY
            for handler in statement.handlers:
                total += 1 + depth                               # S-CATCH
                body(handler.body, depth + 1)
            if statement.orelse:
                total += 1                                       # F-TRYELSE
                body(statement.orelse, depth + 1)
            # Z-FINALLY (Amendment 001): ignored entirely -- no increment and
            # no nesting, so its body stays at this depth.
            body(statement.finalbody, depth)
            return

        if isinstance(statement, (ast.With, ast.AsyncWith)):
            for item in statement.items:                         # Z-WITH
                expression(item.context_expr, depth)
            body(statement.body, depth)
            return

        if isinstance(statement, getattr(ast, "Match", ())):
            total += 1 + depth                                   # S-SWITCH
            expression(statement.subject, depth)
            for case in statement.cases:
                if case.guard is not None:
                    total += 1                                   # F-GUARD
                    expression(case.guard, depth + 1)
                body(case.body, depth + 1)
            return

        for child in ast.iter_child_nodes(statement):
            if isinstance(child, ast.stmt):
                walk(child, depth)
            else:
                expression(child, depth)

    def walk_else(inner: ast.If, depth: int) -> None:
        """Continue an `elif` chain without deepening it."""
        nonlocal total
        if _is_elif(inner):
            total += 1
            nested = inner.orelse[0]
            expression(nested.test, depth)
            body(nested.body, depth + 1)
            if nested.orelse:
                walk_else(nested, depth)
        else:
            total += 1
            body(inner.orelse, depth + 1)

    body(node.body, 0)
    if recursion:
        total += 1                                               # F-RECURSION
    return total

def measure_python(node: ast.FunctionDef | ast.AsyncFunctionDef) -> StructuralMetrics:
    visitor = _MetricVisitor()
    visitor.walk_body(node.body, 0)
    return StructuralMetrics(
        visitor.decisions, visitor.booleans, visitor.max_condition, visitor.max_depth
    )


def formal_parameter_count(node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    """Contract section 9 for Python: every declared formal parameter.

    `self` and `cls` ARE counted -- they are declared parameters and the rule is
    purely syntactic. `*args` and `**kwargs` count one each. Defaults change
    nothing.
    """
    arguments = node.args
    return (
        len(arguments.posonlyargs)
        + len(arguments.args)
        + len(arguments.kwonlyargs)
        + (1 if arguments.vararg is not None else 0)
        + (1 if arguments.kwarg is not None else 0)
    )


def _span(node: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[int, int]:
    """Declaration span, extended over decorators.

    `ast` reports `lineno` at the `def` keyword and keeps decorators in a
    separate list, so a decorated function's span would otherwise begin below its
    own decorators. Complexity Contract 1.0.0 section 8.1 includes them.
    """
    start = node.lineno
    if node.decorator_list:
        start = min(start, min(item.lineno for item in node.decorator_list))
    return start, int(node.end_lineno or node.lineno)


class _DiscoveryVisitor(ast.NodeVisitor):
    """Emit one record per canonical callable, in document order."""

    def __init__(self, lines: LineIndex | None = None) -> None:
        self.records: list[CallableRecord] = []
        self.callable_depth = 0
        self.class_stack: list[ast.ClassDef] = []
        #: Lexical owner chain: callables and classes both contribute a segment.
        self.scope_names: list[str] = []
        self._order = 0
        self.lines = lines

    def visit_ClassDef(self, node: ast.ClassDef):
        self.class_stack.append(node)
        self.scope_names.append(node.name)
        for child in node.body:
            self.visit(child)
        self.scope_names.pop()
        self.class_stack.pop()

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        is_direct_class_method = bool(
            self.class_stack and node in self.class_stack[-1].body
        )
        kind: str | None = None
        owner_kind: str | None = None
        owner_name: str | None = None

        if python_is_overload(node):
            kind = None                       # signature_only_methods
        elif is_direct_class_method:
            if node.name in _CONSTRUCTOR_NAMES:
                kind = None                   # constructors
            else:
                kind = KIND_CLASS_METHOD
                owner_kind = OWNER_CLASS
                owner_name = self.class_stack[-1].name
        elif self.callable_depth or self.class_stack:
            kind = None                       # nested_functions
        else:
            kind = KIND_MODULE_FUNCTION
            owner_kind = OWNER_MODULE

        if kind is not None:
            start, end = _span(node)
            body_start = node.body[0].lineno if node.body else None
            body_end = int(node.body[-1].end_lineno) if node.body else None
            record = CallableRecord(
                name=node.name,
                qualified_name=".".join((*self.scope_names, node.name)),
                callable_kind=kind,
                owner_kind=owner_kind,
                owner_name=owner_name,
                start_line=start,
                end_line=end,
                body_start_line=body_start,
                body_end_line=body_end,
                _order=self._order,
            )
            apply_metrics(
                record,
                measure_python(node),
                formal_parameter_count(node),
                self.lines,
            )
            # `self.m()` / `cls.m()` inside `m` is recursion; `other.m()` is not.
            receivers = {"self", "cls"}
            if owner_name:
                receivers.add(owner_name)
            record.cognitive_complexity = measure_cognitive_python(
                node, frozenset(receivers)
            )
            self.records.append(record)
            self._order += 1

        # Descend regardless of whether this callable was itself counted: a
        # class declared inside an uncounted nested function still yields
        # counted methods, and discovery must find them.
        self.callable_depth += 1
        self.scope_names.append(node.name)
        for child in node.body:
            self.visit(child)
        self.scope_names.pop()
        self.callable_depth -= 1

    def visit_FunctionDef(self, node: ast.FunctionDef):
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
        self._visit_function(node)


def discover_callables(
    tree: ast.AST, lines: LineIndex | None = None
) -> list[CallableRecord]:
    """Every Python callable in the canonical population, in document order."""
    visitor = _DiscoveryVisitor(lines)
    visitor.visit(tree)
    return visitor.records
