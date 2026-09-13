"""G2-B adjudication — classifying Cognitive Complexity disagreements.

**A reference disagreement is not an ArchLens defect.** Every reference in this
study implements its own definition, and there is no primary independent adapter
for Cognitive Complexity at all. So this module's job is to attribute each
numerical disagreement to a *cause*, and the only cause it is structurally
unable to assign is :data:`FAMILY_ARCHLENS_DEFECT`.

That refusal is deliberate and load-bearing. An ArchLens defect may be recorded
only when the **frozen rule table independently proves ArchLens wrong** on a
localized construct -- a derivation, done by hand, against
`FROZEN_RULE_TABLE.md` revision 3, not an inference from a tool that disagrees.
:func:`classify` therefore returns :data:`FAMILY_UNRESOLVED` for anything it
cannot attribute to a documented divergence, and the unresolved set is worked by
hand afterwards. A classifier that could conclude "ArchLens defect" from a
reference disagreement would invert the whole discipline.

How attribution works
=====================

For each disagreement the callable's own source is located and scanned for the
constructs the divergence register (D1-D27) names. A candidate divergence is
accepted only when **its recorded direction matches the observed sign**: D11
says the Python reference scores comprehensions 0, so it can explain the
reference being LOWER and never the reference being higher. A marker pointing
the wrong way is evidence against that explanation, not for it.

Several divergences legitimately co-occur in one real callable and can offset.
Where more than one candidate matches the sign, all are cited: claiming a single
cause we cannot separate would be a guess wearing a citation.

Evidence, not proof
===================

Detection is syntactic. Python uses the real `ast`, locating the callable by
line and inspecting its own subtree; the other four languages scan the
callable's source span for the operators and constructs concerned. That is
enough to attribute a disagreement to a divergence family, and it is **not**
enough to conclude anything about a value -- which is why the residue goes to
manual derivation rather than to a default.
"""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from validation.differential.cognitive_definitions import DIVERGENCES

# -- root-cause families ----------------------------------------------------

FAMILY_ARCHLENS_DEFECT = "archlens_defect"
FAMILY_REFERENCE_TOOL_LIMITATION = "reference_tool_limitation"
FAMILY_DOCUMENTED_DIVERGENCE = "documented_definition_divergence"
FAMILY_PARSER_LIMITATION = "parser_representation_limitation"
FAMILY_ZERO_SUPPRESSION = "zero_suppression_limitation"
FAMILY_POPULATION_MATCHING = "population_matching_difference"
FAMILY_UNSUPPORTED = "unsupported_not_evaluable"
FAMILY_UNRESOLVED = "unresolved"

FAMILIES = (
    FAMILY_ARCHLENS_DEFECT,
    FAMILY_REFERENCE_TOOL_LIMITATION,
    FAMILY_DOCUMENTED_DIVERGENCE,
    FAMILY_PARSER_LIMITATION,
    FAMILY_ZERO_SUPPRESSION,
    FAMILY_POPULATION_MATCHING,
    FAMILY_UNSUPPORTED,
    FAMILY_UNRESOLVED,
)

#: The families :func:`classify` may assign. `archlens_defect` is absent on
#: purpose -- see the module docstring.
AUTOMATABLE_FAMILIES = tuple(
    name for name in FAMILIES if name != FAMILY_ARCHLENS_DEFECT
)

REFERENCE_HIGHER = "reference_higher"
REFERENCE_LOWER = "reference_lower"


@dataclass(frozen=True)
class Candidate:
    """One divergence that could explain a disagreement, and in which direction."""

    divergence: str
    direction: str
    marker: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "divergence": self.divergence,
            "direction": self.direction,
            "marker": self.marker,
            "summary": DIVERGENCES[self.divergence]["summary"],
        }


@dataclass
class Adjudication:
    """One disagreement, classified. The raw observation is carried unchanged."""

    observation: dict[str, Any]
    family: str
    candidates: list[Candidate] = field(default_factory=list)
    note: str = ""
    expected_from_rule_table: int | None = None
    minimal_reproduction: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            # The raw record, byte-for-byte as the baseline holds it. Carried
            # rather than referenced so an adjudication document can be read on
            # its own without silently drifting from the baseline.
            "raw_observation": dict(self.observation),
            "family": self.family,
            "cited_divergences": [item.divergence for item in self.candidates],
            "candidates": [item.as_dict() for item in self.candidates],
            "note": self.note,
            "expected_from_rule_table": self.expected_from_rule_table,
            "minimal_reproduction": self.minimal_reproduction,
        }


# ---------------------------------------------------------------------------
# Locating the construct
# ---------------------------------------------------------------------------


def source_slice(
    subject_root: Path, relative_path: str, start_line: int | None, end_line: int | None
) -> str:
    """The callable's own source text, or '' when it cannot be read."""
    path = Path(subject_root) / relative_path
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    if start_line is None:
        return ""
    first = max(0, int(start_line) - 1)
    last = int(end_line) if end_line else first + 1
    return "\n".join(lines[first:last])


def _strip_strings_and_comments(text: str) -> str:
    """Crude but one-directional: remove text that must not fire a marker.

    Over-removal loses a marker and produces `unresolved`, which is worked by
    hand. Under-removal invents a marker inside a string literal and produces a
    confident wrong attribution. So the bias is deliberately toward removing.
    """
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.DOTALL)
    text = re.sub(r"//[^\n]*", " ", text)
    text = re.sub(r"#[^\n]*", " ", text)
    text = re.sub(r'"""(?:.|\n)*?"""', ' " " ', text)
    text = re.sub(r"'''(?:.|\n)*?'''", " ' ' ", text)
    text = re.sub(r'"(?:\\.|[^"\\\n])*"', ' " " ', text)
    text = re.sub(r"'(?:\\.|[^'\\\n])*'", " ' ' ", text)
    text = re.sub(r"`(?:\\.|[^`\\])*`", " ` ` ", text, flags=re.DOTALL)
    return text


def _has_mixed_boolean_run(text: str) -> bool:
    """Both `&&`/`and` and `||`/`or` present, which is what D26 needs."""
    return bool(re.search(r"&&|\band\b", text)) and bool(
        re.search(r"\|\||\bor\b", text)
    )


def _parenthesized_same_operator(text: str) -> bool:
    """`a && (b && c)` shape: an operator, then a parenthesis holding the same."""
    for operator in (r"&&", r"\|\|", r"\band\b", r"\bor\b"):
        if re.search(rf"{operator}\s*\([^()]*{operator}", text):
            return True
    return False


def _self_call_count(text: str, name: str | None) -> int:
    if not name:
        return 0
    bare = name.split(".")[-1]
    if not bare or not bare.isidentifier():
        return 0
    return len(re.findall(rf"(?<![\w.]){re.escape(bare)}\s*\(", text))


def _receiver_self_call(text: str, name: str | None) -> bool:
    if not name:
        return False
    bare = name.split(".")[-1]
    if not bare or not bare.isidentifier():
        return False
    return bool(re.search(rf"\w+\.{re.escape(bare)}\s*\(", text))


# ---------------------------------------------------------------------------
# Python: the real AST, not a scan
# ---------------------------------------------------------------------------


def _python_node(
    path: Path, start_line: int | None, end_line: int | None = None
) -> ast.AST | None:
    """The `FunctionDef` an ArchLens span refers to.

    **Decorators are why this is not a line-equality test.** ArchLens's span
    starts at the first decorator line, while Python's `ast` reports `lineno`
    as the `def` itself -- so a decorated callable's two line numbers differ by
    however many lines of decorator sit between them, which in real code is
    routinely dozens. Matching on equality alone silently failed to localize
    every decorated function, and an unlocalized callable becomes `unresolved`
    for a harness reason wearing the appearance of a finding.
    """
    if start_line is None:
        return None
    start = int(start_line)
    end = int(end_line) if end_line else start
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, SyntaxError, ValueError):
        return None

    contained: list[ast.AST] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.lineno == start:
            return node
        decorators = getattr(node, "decorator_list", ())
        if decorators and min(item.lineno for item in decorators) - 1 <= start <= node.lineno:
            return node
        if start <= node.lineno <= end:
            contained.append(node)
    # The outermost callable in the span: a nested `def` also sits inside it,
    # and attributing the enclosing callable's difference to the nested one
    # would cite the wrong construct.
    return min(contained, key=lambda item: item.lineno) if contained else None


def _python_boolean_shapes(node: ast.AST) -> tuple[bool, bool]:
    """``(parenthesized_same_operator, mixed_operator_runs)`` for D25 and D26.

    Read off the AST rather than the text, because Python's `BoolOp` nests
    exactly where the source parenthesizes: `a or not (b or c)` is an `Or`
    holding an `Or`, which is the shape D25 says the reference splits and the
    frozen table 3.2 says is ONE run.
    """
    parenthesized = False
    operators: set[type] = set()
    for child in ast.walk(node):
        if not isinstance(child, ast.BoolOp):
            continue
        operators.add(type(child.op))
        for value in ast.walk(child):
            if (
                isinstance(value, ast.BoolOp)
                and value is not child
                and isinstance(value.op, type(child.op))
            ):
                parenthesized = True
    return parenthesized, len(operators) > 1


def _python_nested_if_in_non_final_branch(node: ast.AST) -> bool:
    """The D28 chain shape, isolated to its exact boundary.

    An `if`/`elif` chain's branches are its own body, each `elif` body, and a
    trailing bare `else`. When a nested `if` sits in a branch that is NOT the
    last one, cognitive_complexity 1.3.0 scores one lower than the frozen
    table; in the last branch it agrees at every chain length. That boundary is
    measured in `reductions/py_chain.py`, which is why this checks position
    rather than merely "the chain contains a nested if".
    """
    for chain_head in ast.walk(node):
        if not isinstance(chain_head, ast.If):
            continue
        # Skip an `elif` reached as someone else's orelse: the chain is walked
        # from its head so branch positions are counted once.
        branches: list[list[ast.stmt]] = []
        current: ast.If | None = chain_head
        while current is not None:
            branches.append(current.body)
            following = current.orelse
            if len(following) == 1 and isinstance(following[0], ast.If):
                current = following[0]
            else:
                if following:
                    branches.append(following)
                current = None
        if len(branches) < 2:
            continue
        for branch in branches[:-1]:
            for statement in branch:
                for inner in ast.walk(statement):
                    if isinstance(inner, ast.If):
                        return True
    return False


def _python_else_holding_if(node: ast.AST) -> bool:
    """A bare `else:` whose body is a single `if` -- the D28 shape.

    ArchLens reads it as `F-ELSE` (+1 flat) raising nesting for its own body,
    per frozen table 2.2, so the inner `if` scores `1 + 1`. The reference reads
    the same source differently. `elif` is excluded here by column, exactly as
    the frozen semantics require: in Python's AST the two are otherwise
    indistinguishable.
    """
    for child in ast.walk(node):
        if not isinstance(child, ast.If) or not child.orelse:
            continue
        if len(child.orelse) != 1 or not isinstance(child.orelse[0], ast.If):
            continue
        inner = child.orelse[0]
        if inner.col_offset > child.col_offset:
            return True
    return False


def _python_candidates(node: ast.AST, name: str | None) -> list[Candidate]:
    found: list[Candidate] = []
    comprehensions = 0
    nested = 0
    try_else = 0
    has_match = False
    self_recursion = False

    for child in ast.walk(node):
        if isinstance(
            child, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
        ):
            comprehensions += 1
        elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child is not node:
            nested += 1
        elif isinstance(child, ast.Lambda):
            nested += 1
        elif isinstance(child, ast.Try) and child.orelse:
            try_else += 1
        elif isinstance(child, ast.Match):
            has_match = True
        elif isinstance(child, ast.Call):
            callee = child.func
            bare = (name or "").split(".")[-1]
            if (
                isinstance(callee, ast.Attribute)
                and callee.attr == bare
                and isinstance(callee.value, ast.Name)
                and callee.value.id in ("self", "cls")
            ):
                self_recursion = True

    if comprehensions:
        found.append(
            Candidate("D11", REFERENCE_LOWER, f"{comprehensions} comprehension(s)")
        )
    if nested:
        found.append(
            Candidate("D13", REFERENCE_HIGHER, f"{nested} nested callable(s)")
        )
    if try_else:
        found.append(Candidate("D18", REFERENCE_LOWER, f"{try_else} try...else"))
    if self_recursion:
        found.append(Candidate("D24", REFERENCE_LOWER, "self-qualified self-call"))
    if has_match:
        found.append(Candidate("D12", REFERENCE_LOWER, "match statement"))

    parenthesized, mixed = _python_boolean_shapes(node)
    if parenthesized:
        found.append(
            Candidate("D25", REFERENCE_HIGHER, "parenthesized same-operator run")
        )
    if mixed:
        found.append(
            Candidate("D26", REFERENCE_LOWER, "mixed-operator boolean runs")
        )
    if _python_else_holding_if(node):
        found.append(
            Candidate("D28", REFERENCE_LOWER, "bare `else` whose body is an `if`")
        )
    elif _python_nested_if_in_non_final_branch(node):
        found.append(
            Candidate(
                "D28", REFERENCE_LOWER,
                "nested `if` in a non-final branch of an if/elif chain",
            )
        )
    return found


# ---------------------------------------------------------------------------
# Per-language candidate detection
# ---------------------------------------------------------------------------


def candidates_for(
    *, language: str, subject_root: Path, observation: Mapping[str, Any]
) -> list[Candidate]:
    """Every divergence whose construct appears in this callable's source."""
    relative = observation.get("relative_path") or ""
    start = observation.get("start_line")
    end = observation.get("end_line")
    name = observation.get("qualified_name")

    if language == "Python":
        node = _python_node(Path(subject_root) / relative, start, end)
        if node is not None:
            return _python_candidates(node, name)

    raw = source_slice(Path(subject_root), relative, start, end)
    if not raw:
        return []
    text = _strip_strings_and_comments(raw)
    found: list[Candidate] = []

    if language in ("JavaScript", "TypeScript"):
        if "||" in text:
            found.append(Candidate("D6", REFERENCE_LOWER, "`||` sequence"))
        if "??" in text:
            found.append(Candidate("D7", REFERENCE_LOWER, "`??` sequence"))
        if _self_call_count(text, name) >= 1:
            found.append(Candidate("D8", REFERENCE_LOWER, "direct self-call"))
        if re.search(r"<[A-Za-z][\w.]*[^>]*>", text) and "&&" in text:
            found.append(Candidate("D9", REFERENCE_LOWER, "JSX short-circuit"))
    elif language == "Go":
        if re.search(r"func\s*\(", text[text.find("{") :] if "{" in text else text):
            found.append(Candidate("D16", REFERENCE_HIGHER, "nested func literal"))
        if _self_call_count(text, name) >= 2:
            found.append(Candidate("D22", REFERENCE_HIGHER, "multiple self-call sites"))
        if _receiver_self_call(text, name):
            found.append(
                Candidate("D23", REFERENCE_LOWER, "receiver-qualified self-call")
            )
        if _parenthesized_same_operator(text):
            found.append(
                Candidate("D25", REFERENCE_HIGHER, "parenthesized same-operator run")
            )
    elif language == "Java":
        if "->" in text or re.search(r"new\s+\w[\w.<>]*\s*\([^)]*\)\s*\{", text):
            found.append(
                Candidate("D1", REFERENCE_HIGHER, "lambda or anonymous class")
            )
        if re.search(r"\bclass\s+\w+", text):
            found.append(Candidate("D27", REFERENCE_HIGHER, "named local class"))
        if _self_call_count(text, name) >= 2:
            found.append(Candidate("D22", REFERENCE_HIGHER, "multiple self-call sites"))
        if _has_mixed_boolean_run(text):
            found.append(
                Candidate("D26", REFERENCE_LOWER, "mixed-operator boolean runs")
            )
        if _parenthesized_same_operator(text):
            found.append(
                Candidate("D25", REFERENCE_HIGHER, "parenthesized same-operator run")
            )
        if re.search(r"switch\s*\([^)]*\)\s*\{[^}]*->", text) or "case " in text and " when " in text:
            found.append(
                Candidate("D5", REFERENCE_LOWER, "switch expression or guarded case (N2)")
            )
    return found


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def classify(
    observation: Mapping[str, Any], subject_root: Path
) -> Adjudication:
    """Attribute one numerical disagreement, or leave it unresolved.

    Never returns :data:`FAMILY_ARCHLENS_DEFECT`. That verdict requires a hand
    derivation from the frozen rule table and is applied afterwards.
    """
    observation = dict(observation)
    difference = observation.get("difference")
    language = observation.get("language") or ""

    if difference is None:
        return Adjudication(
            observation, FAMILY_UNSUPPORTED,
            note="no numeric difference to attribute",
        )

    direction = REFERENCE_HIGHER if difference > 0 else REFERENCE_LOWER
    found = candidates_for(
        language=language, subject_root=subject_root, observation=observation
    )
    matching = [item for item in found if item.direction == direction]

    if matching:
        note = (
            f"reference {'higher' if difference > 0 else 'lower'} by "
            f"{abs(difference)}; explained by "
            + ", ".join(sorted({item.divergence for item in matching}))
            + ". Direction matches each cited divergence's recorded direction."
        )
        if observation.get("reference_value_source") == "inferred_suppressed_zero":
            note += (
                " The reference value is an INFERRED zero, so this row also "
                "carries the N3 zero-suppression limitation."
            )
        return Adjudication(
            observation, FAMILY_DOCUMENTED_DIVERGENCE, matching, note
        )

    wrong_way = sorted({item.divergence for item in found})
    return Adjudication(
        observation,
        FAMILY_UNRESOLVED,
        [],
        note=(
            f"no documented divergence explains a reference {'higher' if difference > 0 else 'lower'} "
            f"by {abs(difference)} here"
            + (
                f"; constructs present ({', '.join(wrong_way)}) point the OTHER way"
                if wrong_way
                else "; no divergence construct detected in the callable"
            )
            + ". Requires hand derivation from the frozen rule table."
        ),
    )


# ---------------------------------------------------------------------------
# Hand adjudication. Recorded separately from the automated attribution.
# ---------------------------------------------------------------------------
#
# One reviewer, derivations done against `FROZEN_RULE_TABLE.md` revision 3, each
# with a minimal reproduction under `reductions/`. These are the only verdicts
# permitted to name a family the classifier cannot reach -- and in this campaign
# every one of them concluded the reference diverges, not that ArchLens is
# wrong. The two ArchLens defects G2-B did find were fixed before this rerun, so
# they are absent here by construction rather than by omission.

MANUAL_VERDICTS: dict[tuple[str, int], dict[str, Any]] = {
    ("src/main/java/com/coveros/training/authentication/LoginServlet.java", 26): {
        "family": FAMILY_DOCUMENTED_DIVERGENCE,
        "divergences": ("D29",),
        "expected_from_rule_table": 5,
        "minimal_reproduction": "reductions/ElseNesting.java",
        "note": (
            "Hand derivation: `S-IF@0` 1 + `F-ELSEIF` 1 + `F-ELSE` 1 + "
            "`S-TERNARY@1` 2 = 5. ArchLens 5, PMD 6 -- PMD nests the `else` "
            "body one level deeper than rule table 2.2 allows."
        ),
    },
    ("src/main/java/com/coveros/training/library/LibraryUtils.java", 39): {
        "family": FAMILY_DOCUMENTED_DIVERGENCE,
        "divergences": ("D30",),
        "expected_from_rule_table": 1,
        "minimal_reproduction": "reductions/OverloadRecursion.java",
        "note": (
            "`lendBook(String, String, Date)` calls the OVERLOAD "
            "`lendBook(Book, Borrower, Date)`. Rule table 4.2 A fires on the "
            "bare name and 4.4 records the overload false positive as the "
            "stated cost of having no symbol table, so ArchLens 1 is correct "
            "PER THE FROZEN SEMANTICS. PMD resolves types and scores 0. A "
            "divergence, and a candidate for a future owner amendment -- not a "
            "defect against the table as frozen."
        ),
    },
    ("src/ralph/lib/external_services/models.py", 207): {
        "family": FAMILY_DOCUMENTED_DIVERGENCE,
        "divergences": ("D31",),
        "expected_from_rule_table": 2,
        "minimal_reproduction": "reductions/py_boolop_operand.py",
        "note": (
            "`result.get(...) or (dump(user) if user else None)`: one `or` run "
            "(rule table 3) plus `S-TERNARY@0` (rule table 6.2 keeps operands "
            "traversable) = 2. ArchLens 2, reference 1 -- the reference does "
            "not traverse into a boolean operand."
        ),
    },
}


def apply_manual_verdicts(adjudications: Sequence[Adjudication]) -> list[Adjudication]:
    """Overlay the hand-derived verdicts onto the automated attribution."""
    updated: list[Adjudication] = []
    for item in adjudications:
        key = (
            str(item.observation.get("relative_path") or ""),
            int(item.observation.get("start_line") or -1),
        )
        verdict = MANUAL_VERDICTS.get(key)
        if verdict is None or item.family != FAMILY_UNRESOLVED:
            updated.append(item)
            continue
        updated.append(
            Adjudication(
                observation=item.observation,
                family=verdict["family"],
                candidates=[
                    Candidate(
                        name,
                        REFERENCE_HIGHER
                        if (item.observation.get("difference") or 0) > 0
                        else REFERENCE_LOWER,
                        "hand-derived from the frozen rule table",
                    )
                    for name in verdict["divergences"]
                ],
                note="MANUALLY ADJUDICATED, single reviewer. " + verdict["note"],
                expected_from_rule_table=verdict["expected_from_rule_table"],
                minimal_reproduction=verdict["minimal_reproduction"],
            )
        )
    return updated


def adjudicate(
    raw_document: Mapping[str, Any],
    subject_roots: Mapping[str, Path],
    *,
    manual: bool = True,
) -> list[Adjudication]:
    """Classify every numerical disagreement in a raw baseline document."""
    found: list[Adjudication] = []
    for comparison in raw_document["comparisons"]:
        root = subject_roots[comparison["subject_key"]]
        for observation in comparison["observations"]:
            if observation.get("state") != "numerical_disagreement":
                continue
            found.append(classify(observation, root))
    return apply_manual_verdicts(found) if manual else found


def summarize(adjudications: Sequence[Adjudication]) -> dict[str, Any]:
    """Counts by family and by divergence. No percentage."""
    by_family: dict[str, int] = {name: 0 for name in FAMILIES}
    by_divergence: dict[str, int] = {}
    by_language_family: dict[str, dict[str, int]] = {}
    for item in adjudications:
        by_family[item.family] = by_family.get(item.family, 0) + 1
        language = item.observation.get("language") or "?"
        by_language_family.setdefault(language, {})
        by_language_family[language][item.family] = (
            by_language_family[language].get(item.family, 0) + 1
        )
        for candidate in item.candidates:
            by_divergence[candidate.divergence] = (
                by_divergence.get(candidate.divergence, 0) + 1
            )
    return {
        "disagreements_adjudicated": len(adjudications),
        "by_family": {name: count for name, count in by_family.items() if count},
        "by_language_and_family": by_language_family,
        "citations_by_divergence": dict(
            sorted(by_divergence.items(), key=lambda pair: int(pair[0][1:]))
        ),
        "unresolved": by_family.get(FAMILY_UNRESOLVED, 0),
        "archlens_defects": by_family.get(FAMILY_ARCHLENS_DEFECT, 0),
        "reporting_rule": (
            "Raw counts only, no percentage. A classified disagreement is a "
            "successful outcome; an unclassified one is not. `archlens_defect` "
            "is never assigned automatically -- it requires a hand derivation "
            "from the frozen rule table."
        ),
    }


def persist(
    adjudications: Sequence[Adjudication],
    destination: Path,
    *,
    stage: str = "adjudicated",
    study_metadata: Mapping[str, Any] | None = None,
) -> Path:
    """Write an adjudication document. NEVER the raw baseline's path."""
    destination = Path(destination)
    if destination.name.startswith("cognitive_raw"):
        raise ValueError(
            "adjudication must never be written over the raw baseline; the "
            "unclassified record has to survive as its own artifact"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "stage": stage,
        "study_metadata": dict(study_metadata or {}),
        "families": list(FAMILIES),
        "summary": summarize(adjudications),
        "adjudications": [item.as_dict() for item in adjudications],
    }
    destination.write_text(
        json.dumps(payload, indent=2, sort_keys=False, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return destination
