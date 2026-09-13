"""G2-B closure analysis — attributing a compound disagreement by counterfactual.

Nine Python observations left G2-B `unresolved`. Each sits in a large callable
where several documented divergences co-occur and offset, so no single-cause
classifier can separate them: the SIGN of the residue is the sum of several
contributions pulling in both directions.

This module answers a narrower and decidable question. For one callable:

    if every construct a documented divergence names is neutralized, one class
    at a time, does the ArchLens-versus-reference delta go to ZERO?

If it does, the whole numerical residue is accounted for by documented
divergences acting together, and the case closes as
:data:`FAMILY_COMPOUND_DOCUMENTED`. If any residue survives with no construct
left to explain it, the case stays `unresolved` -- honestly, and without forcing
a single root cause onto interacting ones.

The rules of the procedure
==========================

**Nothing is tuned and no frozen semantic moves.** The counterfactuals edit a
COPY of the subject's syntax tree. ArchLens is measured through its production
path and the reference through its own interpreter, exactly as the campaign ran
them.

**Each neutralizer removes a construct class, not a number.** It never subtracts
an expected contribution -- it deletes the syntax and re-measures both sides, so
the movement is observed rather than modelled. A modelled contribution would be
the same guess the classifier already refused to make.

**Reordering, not deleting, for a positional divergence.** D28 is about a nested
`if` in a NON-FINAL branch of an `if`/`elif` chain. Deleting the branch would
change both sides; moving it to the final position changes only the reference,
because `F-ELSEIF` and its nesting are position-independent under rule table
2.2. :class:`_ReorderChains` therefore rotates the chain and asserts ArchLens's
value is unchanged -- if it moves, the counterfactual is void and the case is
reported as such rather than believed.

**The baseline is the unparsed extraction, not the original file.** Round-tripping
through `ast.unparse` normalizes formatting, so the extraction is re-measured
first and must reproduce the campaign delta before any neutralizer runs. All
nine did.
"""

from __future__ import annotations

import os

import ast
import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

FAMILY_COMPOUND_DOCUMENTED = "compound_documented_definition_divergence"

PYREF = Path(os.environ.get("ARCHLENS_DIFFVAL_REFS", ".metrolith-reference")) / 'pyref/Scripts/python.exe'

#: One neutralizer per documented Python divergence, with the ID it evidences.
#: `D25`/`D26` share a neutralizer because both are properties of the same
#: boolean-run counting and cannot be separated by deleting syntax -- removing
#: the operators removes both, so both are cited together when it moves.
NEUTRALIZER_DIVERGENCES: dict[str, tuple[str, ...]] = {
    "comprehensions": ("D11",),
    "nested_callables": ("D13",),
    "try_else": ("D18",),
    "self_recursion": ("D24",),
    "ternary_in_boolean_operand": ("D31",),
    "boolean_runs": ("D25", "D26"),
    "chain_branch_position": ("D28",),
    "async_for": ("D32",),
}


# ---------------------------------------------------------------------------
# Measurement
# ---------------------------------------------------------------------------

_REFERENCE_SCRIPT = """
import ast, json, sys
from cognitive_complexity.api import get_cognitive_complexity
tree = ast.parse(open(sys.argv[1], encoding="utf-8").read())
found = {}
def walk(node, prefix):
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            found[prefix + child.name] = get_cognitive_complexity(child)
            walk(child, prefix + child.name + ".")
        elif isinstance(child, ast.ClassDef):
            walk(child, prefix + child.name + ".")
        else:
            walk(child, prefix)
walk(tree, "")
print(json.dumps(found))
"""


def measure_archlens(source: str) -> dict[str, int | None]:
    from modules.callable_analysis import analyze_callables

    raw = source.encode("utf-8")
    result = analyze_callables(
        "Python", ast.parse(source), raw, "closure_probe.py",
        raw_text=source, masked_text=source,
    )
    return {record.qualified_name: record.cognitive_complexity for record in result.records}


def measure_reference(source: str, scratch: Path) -> dict[str, int]:
    scratch.mkdir(parents=True, exist_ok=True)
    probe = scratch / "closure_probe.py"
    probe.write_text(source, encoding="utf-8", newline="\n")
    completed = subprocess.run(
        [str(PYREF), "-c", _REFERENCE_SCRIPT, str(probe)],
        capture_output=True, text=True, timeout=600,
    )
    if completed.returncode != 0 or not completed.stdout.strip():
        raise RuntimeError(
            f"the Python reference failed on the probe: "
            f"{completed.stderr.strip()[:400]}"
        )
    return json.loads(completed.stdout)


def delta_for(source: str, name: str, scratch: Path) -> tuple[int | None, int | None, int | None]:
    """``(archlens, reference, reference - archlens)`` for one callable."""
    left = measure_archlens(source).get(name)
    right = measure_reference(source, scratch).get(name)
    if left is None or right is None:
        return (left, right, None)
    return (left, right, right - left)


# ---------------------------------------------------------------------------
# Neutralizers. Each deletes or moves SYNTAX; none subtracts a number.
# ---------------------------------------------------------------------------


class _Neutralizer(ast.NodeTransformer):
    """Base: counts how many sites it touched, so a no-op is visible."""

    def __init__(self) -> None:
        self.hits = 0


class _Comprehensions(_Neutralizer):
    """D11 -- the reference scores a comprehension 0; ArchLens counts it."""

    def _replace(self, node: ast.AST) -> ast.AST:
        self.hits += 1
        return ast.copy_location(ast.Name(id="_comprehension", ctx=ast.Load()), node)

    visit_ListComp = visit_SetComp = visit_DictComp = visit_GeneratorExp = _replace


class _NestedCallables(_Neutralizer):
    """D13 -- the reference FOLDS a nested callable in; ArchLens excludes it.

    The `def` is kept so neither side's population changes; only its body goes.

    **The outer callable is identified by NAME AND DEPTH, never by object
    identity.** Every stage re-parses the source, so an identity comparison
    against a node from an earlier tree matches nothing -- and the outer
    function would then have its own body replaced, zeroing both sides and
    reporting a clean `residue = 0` that means the callable was deleted. That
    is the same node-identity trap the cognitive and cyclomatic engines both
    hit; it is worth naming a third time.
    """

    def __init__(self, name: str) -> None:
        super().__init__()
        self.name = name
        self._depth = 0

    def _function(self, node: ast.AST) -> ast.AST:
        outermost = self._depth == 0 and getattr(node, "name", None) == self.name
        self._depth += 1
        self.generic_visit(node)
        self._depth -= 1
        if not outermost:
            self.hits += 1
            node.body = [ast.copy_location(ast.Pass(), node)]
        return node

    visit_FunctionDef = _function
    visit_AsyncFunctionDef = _function

    def visit_Lambda(self, node: ast.Lambda) -> ast.AST:
        self.hits += 1
        return ast.copy_location(ast.Name(id="_lambda", ctx=ast.Load()), node)


class _TryElse(_Neutralizer):
    """D18 -- ArchLens counts `try...else` as a branch; the reference does not."""

    def visit_Try(self, node: ast.Try) -> ast.AST:
        self.generic_visit(node)
        if node.orelse:
            self.hits += 1
            node.body = list(node.body) + list(node.orelse)
            node.orelse = []
        return node


class _SelfRecursion(_Neutralizer):
    """D24 -- the reference does not detect a `self.`-qualified self-call."""

    def __init__(self, name: str) -> None:
        super().__init__()
        self.name = name

    def visit_Call(self, node: ast.Call) -> ast.AST:
        self.generic_visit(node)
        callee = node.func
        if (
            isinstance(callee, ast.Attribute)
            and callee.attr == self.name
            and isinstance(callee.value, ast.Name)
            and callee.value.id in ("self", "cls")
        ):
            self.hits += 1
            callee.attr = f"_not_{self.name}"
        elif isinstance(callee, ast.Name) and callee.id == self.name:
            self.hits += 1
            callee.id = f"_not_{self.name}"
        return node


class _TernaryInBooleanOperand(_Neutralizer):
    """D31 -- the reference does not traverse into a boolean operand."""

    def visit_BoolOp(self, node: ast.BoolOp) -> ast.AST:
        replaced: list[ast.expr] = []
        for value in node.values:
            stripper = _StripTernary()
            replaced.append(stripper.visit(value))
            self.hits += stripper.hits
        node.values = replaced
        self.generic_visit(node)
        return node


class _StripTernary(_Neutralizer):
    def visit_IfExp(self, node: ast.IfExp) -> ast.AST:
        self.hits += 1
        return ast.copy_location(ast.Name(id="_ternary", ctx=ast.Load()), node)


class _BooleanRuns(_Neutralizer):
    """D25 and D26 -- both are properties of the same run counting.

    Deleting the operators removes both contributions at once, so the two IDs
    are cited together rather than separated by a distinction the syntax cannot
    make.
    """

    def visit_BoolOp(self, node: ast.BoolOp) -> ast.AST:
        self.generic_visit(node)
        self.hits += 1
        return ast.copy_location(ast.Name(id="_boolean", ctx=ast.Load()), node)


def _chain_of(node: ast.If) -> tuple[list[ast.If], list[ast.stmt]]:
    """The `if`/`elif` links of one chain, plus its trailing bare `else` body."""
    links: list[ast.If] = []
    current: ast.If | None = node
    trailing: list[ast.stmt] = []
    while current is not None:
        links.append(current)
        following = current.orelse
        if len(following) == 1 and isinstance(following[0], ast.If):
            current = following[0]
        else:
            trailing = list(following)
            current = None
    return links, trailing


#: Everything rule table 2.1 makes STRUCTURAL -- each scores `1 + nesting`, so
#: each is sensitive to the branch it sits in.
#:
#: D28 was first characterized on a nested `if`, which is simply the shape the
#: real subjects surfaced first. The divergence is not about `if`: it is about
#: branch-body NESTING, so any structural construct in a non-final branch is
#: affected. `_summarize_result` is the case that showed it -- its non-final
#: branch holds a ternary, and looking only for `if` missed it entirely.
_STRUCTURAL_NODES = (
    ast.If, ast.For, ast.AsyncFor, ast.While, ast.Match, ast.Try,
    ast.IfExp, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp,
)


def _holds_structural(body: Sequence[ast.stmt]) -> bool:
    return any(
        isinstance(inner, _STRUCTURAL_NODES)
        for statement in body
        for inner in ast.walk(statement)
    )


class _AsyncFor(_Neutralizer):
    """D32 -- the reference does not recognize `async for` as a loop.

    Rewritten to a synchronous `for`, which both sides agree on. Nothing is
    deleted and ArchLens is unaffected: rule table 2.1 `S-LOOP` covers `for` in
    all its forms, so the two spellings are the same construct to ArchLens and
    only the reference distinguishes them.
    """

    def visit_AsyncFor(self, node: ast.AsyncFor) -> ast.AST:
        self.generic_visit(node)
        self.hits += 1
        return ast.copy_location(
            ast.For(target=node.target, iter=node.iter, body=node.body,
                    orelse=node.orelse, type_comment=None),
            node,
        )


def _count_chain_sites(tree: ast.AST, callable_name: str) -> int:
    """Structural constructs sitting in a NON-FINAL branch of an if/elif chain.

    The unit D28 is measured in. Counted over the named callable only, and never
    through a nested callable boundary -- rule table 7.2 stops attribution
    there, so a construct inside a nested `def` belongs to that callable's own
    row rather than to this one.
    """
    outer = next(
        (node for node in ast.walk(tree)
         if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
         and node.name == callable_name),
        None,
    )
    if outer is None:
        return 0

    seen: set[int] = set()
    total = 0
    for node in ast.walk(outer):
        if not isinstance(node, ast.If) or id(node) in seen:
            continue
        links, trailing = _chain_of(node)
        for link in links:
            seen.add(id(link))
        if len(links) < 2 or trailing:
            continue
        for body in [link.body for link in links][:-1]:
            for statement in body:
                for inner in ast.walk(statement):
                    if isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                        break
                    if isinstance(inner, _STRUCTURAL_NODES):
                        total += 1
    return total


class _ChainBranchPosition(_Neutralizer):
    """D28 -- move a nested `if` into the chain's FINAL branch.

    Nothing is deleted. Rule table 2.2 makes `F-ELSEIF` and its nesting
    position-independent, so ArchLens must be unchanged; the reference agrees in
    the final position and diverges elsewhere, which is the boundary
    `reductions/py_chain.py` measured.
    """

    def visit_If(self, node: ast.If) -> ast.AST:
        self.generic_visit(node)
        links, trailing = _chain_of(node)
        if len(links) < 2 or trailing:
            return node
        bodies = [link.body for link in links]
        offending = [
            index for index, body in enumerate(bodies[:-1])
            if _holds_structural(body)
        ]
        if not offending:
            return node
        self.hits += len(offending)
        # Rotate the first offending branch to the end of the chain. Tests move
        # with their bodies so the chain stays well formed.
        index = offending[0]
        order = [item for position, item in enumerate(links) if position != index]
        order.append(links[index])
        tests = [link.test for link in order]
        blocks = [link.body for link in order]
        rebuilt: ast.If | None = None
        for test, block in zip(reversed(tests), reversed(blocks)):
            rebuilt = ast.copy_location(
                ast.If(test=test, body=block, orelse=[rebuilt] if rebuilt else []),
                node,
            )
        return rebuilt or node


# ---------------------------------------------------------------------------
# The procedure
# ---------------------------------------------------------------------------


@dataclass
class Stage:
    """One neutralizer's effect on the delta."""

    neutralizer: str
    divergences: tuple[str, ...]
    sites: int
    archlens: int | None
    reference: int | None
    delta: int | None
    moved: int | None

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass
class ClosureResult:
    relative_path: str
    start_line: int
    qualified_name: str
    campaign_archlens: int | None
    campaign_reference: int | None
    campaign_delta: int | None
    baseline_delta: int | None
    reproduced: bool
    stages: list[Stage] = field(default_factory=list)
    residue: int | None = None
    family: str = ""
    cited: tuple[str, ...] = ()
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "relative_path": self.relative_path,
            "start_line": self.start_line,
            "qualified_name": self.qualified_name,
            "campaign_archlens": self.campaign_archlens,
            "campaign_reference": self.campaign_reference,
            "campaign_delta": self.campaign_delta,
            "baseline_delta": self.baseline_delta,
            "reproduced_standalone": self.reproduced,
            "stages": [stage.as_dict() for stage in self.stages],
            "unexplained_residue": self.residue,
            "family": self.family,
            "cited_divergences": list(self.cited),
            "note": self.note,
        }


def _build(name: str, callable_name: str) -> _Neutralizer:
    return {
        "comprehensions": lambda: _Comprehensions(),
        "nested_callables": lambda: _NestedCallables(callable_name),
        "try_else": lambda: _TryElse(),
        "self_recursion": lambda: _SelfRecursion(callable_name),
        "ternary_in_boolean_operand": lambda: _TernaryInBooleanOperand(),
        "boolean_runs": lambda: _BooleanRuns(),
        "chain_branch_position": lambda: _ChainBranchPosition(),
        "async_for": lambda: _AsyncFor(),
    }[name]()


#: Neutralizers that must leave the ARCHLENS value untouched, and why.
#:
#: `nested_callables` removes bodies ArchLens never attributed in the first
#: place (`B-NESTED`, rule table 7.2), and `chain_branch_position` only moves a
#: branch, which rule table 2.2 makes position-independent. If either moves
#: ArchLens it has changed something else as well, and its evidence is void --
#: an unchecked one silently deleted the outer callable and reported a clean
#: zero residue, which is exactly what this guard exists to catch.
ARCHLENS_INVARIANT = frozenset({
    "nested_callables", "chain_branch_position", "async_for",
})


#: Applied in this order. Positional and operand-level neutralizers run BEFORE
#: the boolean one, which would otherwise delete the operands they inspect.
ORDER = (
    "async_for",
    "chain_branch_position",
    "ternary_in_boolean_operand",
    "comprehensions",
    "nested_callables",
    "try_else",
    "self_recursion",
    "boolean_runs",
)


def close_one(
    *,
    source: str,
    qualified_name: str,
    relative_path: str,
    start_line: int,
    campaign_archlens: int | None,
    campaign_reference: int | None,
    campaign_delta: int | None,
    scratch: Path,
) -> ClosureResult:
    """Neutralize one class at a time and report where the delta ends up."""
    name = qualified_name.split(".")[-1]
    left, right, baseline = delta_for(source, name, scratch)
    result = ClosureResult(
        relative_path=relative_path,
        start_line=start_line,
        qualified_name=qualified_name,
        campaign_archlens=campaign_archlens,
        campaign_reference=campaign_reference,
        campaign_delta=campaign_delta,
        baseline_delta=baseline,
        reproduced=(baseline == campaign_delta),
    )
    if not result.reproduced:
        result.family = "unresolved"
        result.note = (
            f"the standalone extraction measured a delta of {baseline}, not the "
            f"campaign's {campaign_delta}. The reproduction is void, so nothing "
            f"is concluded from it."
        )
        return result

    tree = ast.parse(source)
    previous = baseline
    for step in ORDER:
        transformer = _build(step, name)
        candidate = transformer.visit(ast.parse(ast.unparse(tree)))
        ast.fix_missing_locations(candidate)
        if transformer.hits == 0:
            continue
        text = ast.unparse(candidate)
        try:
            new_left, new_right, new_delta = delta_for(text, name, scratch)
        except (SyntaxError, RuntimeError, StopIteration):
            continue
        if new_delta is None:
            continue
        if step in ARCHLENS_INVARIANT and new_left != left:
            # Void, not merely uninteresting: this neutralizer changed
            # something it was not supposed to touch, so its movement cannot be
            # attributed to the divergence it stands for.
            result.stages.append(
                Stage(step, NEUTRALIZER_DIVERGENCES[step], transformer.hits,
                      new_left, new_right, new_delta, None)
            )
            continue
        result.stages.append(
            Stage(step, NEUTRALIZER_DIVERGENCES[step], transformer.hits,
                  new_left, new_right, new_delta, new_delta - previous)
        )
        previous = new_delta
        tree = candidate
        left = new_left

    # A rate-based attribution for D28 was BUILT AND THEN WITHDRAWN, and the
    # withdrawal is recorded here rather than quietly dropped.
    #
    # `reductions/py_d28_rate.py` measured -1 per structural construct in a
    # non-final branch on three chain shapes (1 construct -1; 2 in one branch
    # -2; 2 across two branches -2), which would have closed the multi-offender
    # cases the rotation counterfactual cannot reach. A fourth shape falsified
    # it: with a trailing bare `else`, two sites produce -1, not -2. So the rate
    # is not a general rule, the agreements it did produce cannot be
    # distinguished from coincidence, and no case may close on it.
    #
    # D28's mechanism is therefore ISOLATED BUT NOT MODELLED: the boundary
    # between agreement and disagreement is measured, the magnitude is not.

    result.residue = previous
    if previous == 0:
        cited: list[str] = []
        for stage in result.stages:
            if stage.moved:
                cited.extend(stage.divergences)
        result.family = FAMILY_COMPOUND_DOCUMENTED
        result.cited = tuple(sorted(set(cited), key=lambda item: int(item[1:])))
        result.note = (
            "every documented-divergence construct was neutralized one class at "
            "a time and the delta reached zero, so the whole numerical residue "
            "is accounted for by documented divergences acting together."
        )
    else:
        result.family = "unresolved"
        result.cited = tuple(
            sorted(
                {item for stage in result.stages if stage.moved
                 for item in stage.divergences},
                key=lambda item: int(item[1:]),
            )
        )
        result.note = (
            f"a residue of {previous} survives with no documented-divergence "
            f"construct left to explain it. Kept unresolved rather than forced "
            f"onto one of the causes that did move."
        )
    return result


def summarize(results: Sequence[ClosureResult]) -> dict[str, Any]:
    by_family: dict[str, int] = {}
    cited: dict[str, int] = {}
    for item in results:
        by_family[item.family] = by_family.get(item.family, 0) + 1
        for name in item.cited:
            cited[name] = cited.get(name, 0) + 1
    return {
        "cases": len(results),
        "reproduced_standalone": sum(1 for item in results if item.reproduced),
        "by_family": by_family,
        "citations_by_divergence": dict(
            sorted(cited.items(), key=lambda pair: int(pair[0][1:]))
        ),
        "unresolved": by_family.get("unresolved", 0),
        "reporting_rule": (
            "Raw counts only. A compound classification requires the delta to "
            "reach EXACTLY zero after neutralization; any surviving residue "
            "keeps the case unresolved."
        ),
    }


def persist(
    results: Sequence[ClosureResult], destination: Path,
    *, study_metadata: Mapping[str, Any] | None = None,
) -> Path:
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(
            {
                "stage": "g2b_closure_analysis",
                "study_metadata": dict(study_metadata or {}),
                "neutralizer_order": list(ORDER),
                "neutralizer_divergences": {
                    name: list(ids) for name, ids in NEUTRALIZER_DIVERGENCES.items()
                },
                "summary": summarize(results),
                "cases": [item.as_dict() for item in results],
            },
            indent=2, ensure_ascii=False,
        ) + "\n",
        encoding="utf-8", newline="\n",
    )
    return destination
