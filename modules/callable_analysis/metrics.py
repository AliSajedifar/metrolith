"""Structural metric computation for Complexity Contract 1.0.0.

Implements the bounded descent of contract section 3: attribution starts at one
callable's body and **stops at every nested callable boundary**, so an excluded
nested callable contributes exactly zero. Discovery is a separate, global walk
and lives in the language modules.

One engine drives Java, JavaScript/TypeScript and Go; Python has its own
`ast`-based measurement in `python.py` because its tree is a different shape.
Both produce the same :class:`StructuralMetrics`.

The engine never re-reads or re-parses source. It is handed the tree
`core_metrics` already selected.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable

from modules.syntax_predicates import _node_text


@dataclass(frozen=True)
class StructuralMetrics:
    """The six tree-derived values. ``nloc`` is measured separately (section 8)."""

    decision_point_count: int = 0
    boolean_operator_count: int = 0
    max_condition_operator_count: int = 0
    max_nesting_depth: int = 0

    @property
    def cyclomatic_complexity(self) -> int:
        """Contract section 7.2, enforced by construction rather than asserted."""
        return 1 + self.decision_point_count + self.boolean_operator_count


@dataclass(frozen=True)
class LineIndex:
    """Physical lines and their comment-masked counterparts.

    Built from the masking `core_metrics` already performed for repository LOC,
    so function NLOC cannot drift from `lines_of_code`: there is exactly one
    comment-masking implementation and one line predicate.
    """

    raw: tuple[str, ...]
    masked: tuple[str, ...]

    @classmethod
    def build(cls, raw_text: str, masked_text: str) -> "LineIndex":
        raw = raw_text.split("\n")
        masked = masked_text.split("\n")
        if len(masked) < len(raw):
            masked = masked + [""] * (len(raw) - len(masked))
        return cls(tuple(raw), tuple(masked))

    def code_lines(self, start_line: int, end_line: int) -> int:
        """Count code lines in the inclusive 1-based span ``[start, end]``."""
        from modules.syntax_predicates import line_is_code

        first = max(start_line, 1) - 1
        last = min(end_line, len(self.raw))
        return sum(
            1
            for index in range(first, last)
            if line_is_code(self.raw[index], self.masked[index])
        )


@dataclass(frozen=True)
class StructuralRules:
    """One language's syntax-to-metric mapping.

    Every field is data, not behaviour, except where a decision genuinely needs
    context (a `block` opens nesting only under a control construct; an
    `object_creation_expression` is a boundary only when it carries a class
    body). Those are predicates so the rule stays exact rather than approximate.
    """

    #: Node types whose subtree belongs to another record, or to none.
    is_boundary: Callable[[Any], bool]
    #: Additional decision points contributed by this node.
    decision_increment: Callable[[Any, bytes], int]
    #: Short-circuit operator occurrences contributed by this node.
    boolean_increment: Callable[[Any, bytes], int]
    #: True when this node's children sit one nesting level deeper.
    opens_nesting: Callable[[Any], bool]
    #: The decision expressions this node introduces, if any.
    condition_nodes: Callable[[Any], Iterable[Any]]
    #: True when this node IS the branch body rather than a container of it --
    #: an unbraced single-statement body, `if (a) break;`. Such a node sits one
    #: level deeper *itself*, so it registers the depth even with no named
    #: children of its own. A block is the opposite case: an EMPTY one holds no
    #: statement and must register nothing, which is why the two are separate.
    unbraced_body: Callable[[Any], bool] = lambda node: False


def _binary_operator_counter(operators: frozenset[str]):
    """Count a `binary_expression`/augmented-assignment whose operator matches.

    The operator is read from the node's ``operator`` FIELD rather than by
    scanning text: an operator token inside a nested string or a different
    expression would otherwise be counted, and field access is exact.
    """

    def count(node: Any, source: bytes) -> int:
        operator = node.child_by_field_name("operator")
        if operator is None:
            return 0
        return 1 if _node_text(operator, source) in operators else 0

    return count


def count_boolean_operators(node: Any, source: bytes, rules: StructuralRules) -> int:
    """Short-circuit operators inside one expression, boundaries excluded.

    Used for `max_condition_operator_count`, which inspects a single decision
    expression. A lambda written inside a condition is still a boundary, so its
    operators belong to no record.
    """
    if node is None:
        return 0
    total = rules.boolean_increment(node, source)
    for child in node.named_children:
        if rules.is_boundary(child):
            continue
        total += count_boolean_operators(child, source, rules)
    return total


def measure(body: Any, source: bytes, rules: StructuralRules) -> StructuralMetrics:
    """Bounded descent from one callable's body.

    ``body`` itself is never counted and never opens a nesting level: it is the
    callable's own block, and its statements are depth 0.
    """
    decisions = 0
    booleans = 0
    max_condition = 0
    max_depth = 0

    def walk(node: Any, depth: int) -> None:
        nonlocal decisions, booleans, max_condition, max_depth
        # A node's children sit one level deeper when the node opens nesting.
        # `body` never does: its parent is the callable declaration, not a
        # control construct, so an empty block also registers no depth.
        inner = depth + (1 if rules.opens_nesting(node) else 0)
        if inner > max_depth and rules.unbraced_body(node):
            max_depth = inner
        for child in node.named_children:
            if rules.is_boundary(child):
                continue
            if inner > max_depth:
                max_depth = inner
            decisions += rules.decision_increment(child, source)
            booleans += rules.boolean_increment(child, source)
            for condition in rules.condition_nodes(child):
                found = count_boolean_operators(condition, source, rules)
                if found > max_condition:
                    max_condition = found
            walk(child, inner)

    # The body may BE an expression rather than a block -- an arrow with a
    # concise body, `x => a && b`. The root then carries the operator itself, and
    # walking only its children would miss it. For a block body every one of
    # these is zero, so the call is safe in all four grammars.
    decisions += rules.decision_increment(body, source)
    booleans += rules.boolean_increment(body, source)
    for condition in rules.condition_nodes(body):
        found = count_boolean_operators(condition, source, rules)
        if found > max_condition:
            max_condition = found

    walk(body, 0)
    return StructuralMetrics(decisions, booleans, max_condition, max_depth)


def apply_metrics(
    record: Any,
    metrics: StructuralMetrics,
    formal_parameter_count: int | None,
    lines: LineIndex | None,
) -> None:
    """Write measured values onto a record, honouring the two statuses.

    ``nloc`` is left null when no line index is available. That is not zero: the
    masking machinery failed for this file, which is a different fact from a
    callable with no code lines, and `nloc_status` carries the difference.
    """
    record.decision_point_count = metrics.decision_point_count
    record.boolean_operator_count = metrics.boolean_operator_count
    record.max_condition_operator_count = metrics.max_condition_operator_count
    record.max_nesting_depth = metrics.max_nesting_depth
    record.cyclomatic_complexity = metrics.cyclomatic_complexity
    record.formal_parameter_count = formal_parameter_count
    record.nloc = (
        lines.code_lines(record.start_line, record.end_line)
        if lines is not None
        else None
    )
