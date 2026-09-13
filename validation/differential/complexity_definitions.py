"""Definition mappings for Complexity Contract 1.0.0 differential validation.

A numeric disagreement means nothing until both sides' definitions are written
down. These mappings are the record of what each comparison actually compares,
and they are written BEFORE any real-subject run so a disagreement can be
classified rather than explained after the fact.

Two reference tiers, and the distinction is load-bearing:

**Primary independent adapters** implement *ArchLens's* definition with a
different parser. Holding the definition fixed and varying the implementation is
what turns a disagreement into evidence about an implementation. These are the
adapters a defect claim may rest on.

**External CC tools** implement *their own* definition. They are a second
opinion on cyclomatic complexity only, and every divergence below is a property
of the definitions rather than a defect on either side. **An external tool
difference is never grounds for changing ArchLens.**

No reference here is ground truth. A disagreement is a finding about both sides
until adjudicated.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# -- comparability classifications ------------------------------------------
EXACT = "exact_comparable"
DOCUMENTED = "definitionally_comparable_with_documented_differences"
NOT_COMPARABLE = "not_comparable_definition"
NOT_EVALUABLE = "not_evaluable"

CLASSIFICATIONS = (EXACT, DOCUMENTED, NOT_COMPARABLE, NOT_EVALUABLE)

LANGUAGES = ("Go", "Java", "JavaScript", "TypeScript", "Python")

METRICS = (
    "nloc",
    "formal_parameter_count",
    "cyclomatic_complexity",
    "decision_point_count",
    "boolean_operator_count",
    "max_condition_operator_count",
    "max_nesting_depth",
)

#: Pinned reference environment, measured 2026-08-10. None is an ArchLens
#: runtime dependency.
REFERENCE_VERSIONS: dict[str, str] = {
    "parso": "0.8.7",
    "javac": "JDK 17.0.17.10 (Eclipse Adoptium)",
    "typescript": "5.6.3",
    "node": "22.20.0",
    "go": "go/parser, pinned toolchain",
    "lizard": "1.17.31",
    "eslint": "9.15.0",
    "@typescript-eslint/parser": "8.15.0",
    # The adapters version INDEPENDENTLY of one another and of their tools, so a
    # reader can tell which moved. Divergence is deliberate and is the point:
    #
    #   2.0.0  the reviewed baseline; Java is still there, unchanged.
    #   2.1.0  Go only -- the SERIALIZED contract changed (an empty result emits
    #          `[]` rather than `null`); no metric rule moved.
    #   2.2.0  Python and Node -- metric RULES corrected by the C4 Layer-C2
    #          campaign: a decorated `async def` keeps its decorator lines in
    #          the span, a bare `except:` is a decision, a module-level `def`
    #          inside a block statement is in the population, and expressions
    #          inside nested statements are counted.
    "reference_complexity_adapter": "2.2.0",
    "reference_complexity_adapter_go": "2.1.0",
    "reference_complexity_adapter_java": "2.0.0",
    "reference_complexity_adapter_python": "2.2.0",
    "reference_complexity_adapter_node": "2.2.0",
}

#: Which primary adapter carries each language.
PRIMARY_ADAPTER: dict[str, str] = {
    "Go": "go/parser + go/ast",
    "Java": "javac Compiler Tree API (com.sun.source)",
    "JavaScript": "TypeScript Compiler API",
    "TypeScript": "TypeScript Compiler API",
    "Python": "parso concrete syntax tree",
}

#: Which external cyclomatic tool covers each language, and why.
EXTERNAL_CC: dict[str, str] = {
    "Go": "Lizard 1.17.31",
    "Java": "Lizard 1.17.31",
    "JavaScript": "Lizard 1.17.31",
    "Python": "Lizard 1.17.31",
    "TypeScript": "ESLint 9.15.0 core `complexity`",
}


@dataclass(frozen=True)
class ComplexityMapping:
    """One reviewed (metric, language, reference) comparison."""

    metric: str
    language: str
    reference: str
    unit_compared: str
    callable_population: str
    construct_semantics: str
    nested_callable_treatment: str
    classification: str
    known_differences: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "language": self.language,
            "reference": self.reference,
            "unit_compared": self.unit_compared,
            "callable_population": self.callable_population,
            "construct_semantics": self.construct_semantics,
            "nested_callable_treatment": self.nested_callable_treatment,
            "comparability": self.classification,
            "known_systematic_differences": list(self.known_differences),
        }


# ---------------------------------------------------------------------------
# Primary independent adapters: ArchLens's definition, a different parser.
# ---------------------------------------------------------------------------

_CANONICAL_POPULATION = (
    "the canonical `methods_functions` population, implemented independently: "
    "constructors, lambdas, nested and anonymous callables, class-property "
    "arrows, declaration-only signatures and initializer blocks are excluded on "
    "BOTH sides. Deliberately not broadened to every callable."
)

_BOUNDARY_TREATMENT = (
    "attribution stops at every nested callable or class boundary on both "
    "sides, so an excluded nested callable contributes zero (contract section 3)"
)

_NLOC_SEMANTICS = (
    "physical lines in the declaration span that are neither blank nor "
    "comment-only, after comment masking. The span includes attached decorators "
    "or annotations. Both sides implement the SAME line predicate; the reference "
    "uses its own clean-room comment scanner."
)

_NLOC_NESTED = (
    "NLOC is a span measure and INCLUDES lexically nested callables on both "
    "sides -- the deliberate asymmetry of contract section 8.2"
)

_PARAMETER_SEMANTICS = (
    "formal parameters declared in the callable's own parameter list. A "
    "destructuring pattern is one; a rest/varargs parameter is one; receivers "
    "and the TypeScript `this` parameter are excluded. Purely syntactic; no "
    "claim of cross-language equivalence."
)

_CC_SEMANTICS = (
    "1 + decision_point_count + boolean_operator_count, over the callable's own "
    "traversed content (contract section 7.2)"
)

_DECISION_SEMANTICS = (
    "each if/else-if, loop, non-default switch or select arm, catch/except, "
    "conditional expression, comprehension for/if clause and match guard. "
    "`else`, `default`, `finally` and a bare wildcard arm contribute zero."
)

_BOOLEAN_SEMANTICS = (
    "short-circuit operator occurrences: `&&`/`||` everywhere, plus `??` and "
    "the logical assignments in JS/TS, and `and`/`or` in Python where an "
    "n-operand chain carries n-1 operators"
)

_MAX_CONDITION_SEMANTICS = (
    "the largest short-circuit operator count within a SINGLE decision "
    "expression, which is deliberately not the callable total"
)

_NESTING_SEMANTICS = (
    "the maximum count of enclosing nesting-increasing constructs. Structural "
    "nesting: `with`, try/finally, case bodies and Java `synchronized` open a "
    "level while contributing zero decisions. An `else if` measures flat."
)

_SEMANTICS = {
    "nloc": (_NLOC_SEMANTICS, _NLOC_NESTED),
    "formal_parameter_count": (_PARAMETER_SEMANTICS, "not applicable"),
    "cyclomatic_complexity": (_CC_SEMANTICS, _BOUNDARY_TREATMENT),
    "decision_point_count": (_DECISION_SEMANTICS, _BOUNDARY_TREATMENT),
    "boolean_operator_count": (_BOOLEAN_SEMANTICS, _BOUNDARY_TREATMENT),
    "max_condition_operator_count": (_MAX_CONDITION_SEMANTICS, _BOUNDARY_TREATMENT),
    "max_nesting_depth": (_NESTING_SEMANTICS, _BOUNDARY_TREATMENT),
}

#: Per-language known differences against the PRIMARY adapter. Empty means the
#: corpus comparison was exact for every metric.
_PRIMARY_DIFFERENCES: dict[tuple[str, str], tuple[str, ...]] = {
    ("Python", "cyclomatic_complexity"): (
        "parso 0.8.7 has no `match` grammar and emits error nodes, so a callable "
        "containing a match statement is reported NOT EVALUABLE rather than 0. "
        "NLOC and the parameter count still resolve.",
    ),
    ("Python", "decision_point_count"): (
        "same parso `match` limitation as cyclomatic_complexity",
    ),
    ("Python", "max_nesting_depth"): (
        "same parso `match` limitation as cyclomatic_complexity",
    ),
    ("Java", "cyclomatic_complexity"): (
        "JDK 17 javac cannot parse a guarded case (`case P when g`), finalized "
        "only in JDK 21, so such a callable is reported NOT EVALUABLE rather "
        "than given a number.",
    ),
    ("Java", "decision_point_count"): (
        "same JDK 17 guarded-case limitation as cyclomatic_complexity",
    ),
}

#: A metric/language cell whose PRIMARY comparison is not fully exact.
_PRIMARY_CLASSIFICATION: dict[tuple[str, str], str] = {
    ("Python", "cyclomatic_complexity"): DOCUMENTED,
    ("Python", "decision_point_count"): DOCUMENTED,
    ("Python", "max_nesting_depth"): DOCUMENTED,
    ("Java", "cyclomatic_complexity"): DOCUMENTED,
    ("Java", "decision_point_count"): DOCUMENTED,
}


def _primary_mappings() -> list[ComplexityMapping]:
    found: list[ComplexityMapping] = []
    for language in LANGUAGES:
        for metric in METRICS:
            semantics, nested = _SEMANTICS[metric]
            found.append(
                ComplexityMapping(
                    metric=metric,
                    language=language,
                    reference=PRIMARY_ADAPTER[language],
                    unit_compared="one canonical callable",
                    callable_population=_CANONICAL_POPULATION,
                    construct_semantics=semantics,
                    nested_callable_treatment=nested,
                    classification=_PRIMARY_CLASSIFICATION.get(
                        (language, metric), EXACT
                    ),
                    known_differences=_PRIMARY_DIFFERENCES.get((language, metric), ()),
                )
            )
    return found


# ---------------------------------------------------------------------------
# External cyclomatic tools: their OWN definition. Second opinion only.
# ---------------------------------------------------------------------------

_EXTERNAL_POPULATION = (
    "the tool's own population, which is BROADER than ArchLens's: it measures "
    "lambdas, callbacks, constructors and accessors that the canonical "
    "population excludes. Comparison is on the matched intersection only; "
    "population counts are NOT compared."
)

_LIZARD_MAPPINGS = {
    "Go": ComplexityMapping(
        metric="cyclomatic_complexity", language="Go", reference="Lizard 1.17.31",
        unit_compared="one callable matched on path, name and overlapping span",
        callable_population=_EXTERNAL_POPULATION,
        construct_semantics="Lizard's own token-based decision counting",
        nested_callable_treatment=(
            "Lizard reports a `func_literal` as its own function; ArchLens "
            "excludes it from the population entirely"
        ),
        classification=DOCUMENTED,
        known_differences=(
            "Lizard reported 22 functions against 21 canonical callables on the "
            "synthetic corpus; the extra is the function literal.",
            "Every sampled matched value agreed exactly.",
        ),
    ),
    "Java": ComplexityMapping(
        metric="cyclomatic_complexity", language="Java", reference="Lizard 1.17.31",
        unit_compared="one callable matched on path, name and overlapping span",
        callable_population=_EXTERNAL_POPULATION,
        construct_semantics="Lizard's own token-based decision counting",
        nested_callable_treatment=(
            "Lizard FOLDS a lambda body into the enclosing method; ArchLens "
            "treats a lambda as a traversal boundary"
        ),
        classification=DOCUMENTED,
        known_differences=(
            "22 of 24 corpus values agreed exactly.",
            "`excludedLambda`: contract 1 vs Lizard 3 -- Lizard folds the "
            "lambda's `if` and `&&` into the enclosing method.",
            "`guardedSwitch`: contract 3 vs Lizard 2 -- Lizard does not count "
            "the `when` guard as a decision.",
            "26 Lizard functions against 24 canonical callables; the extras are "
            "the anonymous-class method and the constructor.",
        ),
    ),
    "JavaScript": ComplexityMapping(
        metric="cyclomatic_complexity", language="JavaScript",
        reference="Lizard 1.17.31",
        unit_compared="one callable matched on path, name and overlapping span",
        callable_population=_EXTERNAL_POPULATION,
        construct_semantics="Lizard's own token-based decision counting",
        nested_callable_treatment=(
            "Lizard reports callbacks and class-property arrows as their own "
            "functions; ArchLens excludes them from the population"
        ),
        classification=DOCUMENTED,
        known_differences=(
            "21 of 22 comparable corpus values agreed exactly.",
            "`nullishAndLogicalAssignment`: contract 5 vs Lizard 7 -- a "
            "systematic difference across `??`, `??=`, `||=` and `&&=`. The "
            "exact tokenization Lizard applies to the compound assignment forms "
            "was NOT established, so this is recorded as observed rather than "
            "explained.",
            "28 Lizard functions against 25 canonical callables.",
        ),
    ),
    "Python": ComplexityMapping(
        metric="cyclomatic_complexity", language="Python",
        reference="Lizard 1.17.31",
        unit_compared="one callable matched on path, name and overlapping span",
        callable_population=_EXTERNAL_POPULATION,
        construct_semantics="Lizard's own token-based decision counting",
        nested_callable_treatment=(
            "Lizard reports a nested `def` as its own function; ArchLens "
            "excludes it from the population"
        ),
        classification=DOCUMENTED,
        known_differences=(
            "24 Lizard functions against 22 canonical callables; the extras are "
            "the nested helper and `__init__`.",
            "Every sampled matched value agreed exactly.",
        ),
    ),
}

_ESLINT_MAPPING = ComplexityMapping(
    metric="cyclomatic_complexity", language="TypeScript",
    reference="ESLint 9.15.0 core `complexity` (@typescript-eslint/parser 8.15.0)",
    unit_compared="one callable matched on path and reported declaration line",
    callable_population=_EXTERNAL_POPULATION,
    construct_semantics=(
        "ESLint's own `complexity` rule, run with a threshold of 0 so every "
        "function reports its computed value"
    ),
    nested_callable_treatment=(
        "ESLint reports each arrow function separately; ArchLens excludes "
        "non-canonical arrows from the population"
    ),
    classification=DOCUMENTED,
    known_differences=(
        "19 ESLint findings matched 16 canonical callables, with 3 ESLint-only "
        "arrows and 0 ArchLens-only callables.",
        "14 of 16 matched values agreed exactly.",
        "ESLint counts a DEFAULT PARAMETER VALUE as a decision point; "
        "Complexity Contract 1.0.0 section 7.1 has no such rule. This accounts "
        "for both differences: `nullishCoalescing` 2 vs 3 and "
        "`optionalAndDefaultParameters` 1 vs 2.",
    ),
)

#: Lizard is explicitly NOT usable for TypeScript.
_LIZARD_TYPESCRIPT_EXCLUSION = ComplexityMapping(
    metric="cyclomatic_complexity", language="TypeScript",
    reference="Lizard 1.17.31",
    unit_compared="not applicable",
    callable_population="not applicable",
    construct_semantics="not applicable",
    nested_callable_treatment="not applicable",
    classification=NOT_EVALUABLE,
    known_differences=(
        "Lizard reports overlapping, incorrect spans for `.ts` -- `trivial` as "
        "9-13 where the declaration is 9-11 -- with NLOC inflated accordingly. "
        "Overlapping spans are something a correct reader cannot produce. "
        "Recorded as a reference-tool limitation; ESLint is the TypeScript "
        "external reference instead.",
    ),
)

#: Metrics no external tool reports, so no external mapping exists for them.
_EXTERNAL_UNCOVERED_NOTE = (
    "no external tool reports this metric, so the primary independent adapter "
    "is the only cross-check. Recorded rather than left implicit."
)


def all_mappings() -> list[ComplexityMapping]:
    found = _primary_mappings()
    found.extend(_LIZARD_MAPPINGS.values())
    found.append(_ESLINT_MAPPING)
    found.append(_LIZARD_TYPESCRIPT_EXCLUSION)
    return found


def coverage_report() -> dict[str, Any]:
    """What this study can and cannot compare, stated up front."""
    primary = {
        (mapping.language, mapping.metric): mapping.classification
        for mapping in _primary_mappings()
    }
    external = {}
    for mapping in list(_LIZARD_MAPPINGS.values()) + [_ESLINT_MAPPING]:
        external[(mapping.language, mapping.metric)] = mapping.classification

    uncovered_by_external = [
        {"language": language, "metric": metric, "reason": _EXTERNAL_UNCOVERED_NOTE}
        for language in LANGUAGES
        for metric in METRICS
        if (language, metric) not in external
    ]
    return {
        "reference_versions": dict(REFERENCE_VERSIONS),
        "primary_cells": len(primary),
        "primary_exact": sum(1 for value in primary.values() if value == EXACT),
        "primary_documented": sum(1 for value in primary.values() if value == DOCUMENTED),
        "primary_not_comparable": sum(
            1 for value in primary.values() if value == NOT_COMPARABLE
        ),
        "primary_not_evaluable": sum(
            1 for value in primary.values() if value == NOT_EVALUABLE
        ),
        "external_cells": len(external),
        "external_not_evaluable": [
            {"language": mapping.language, "reference": mapping.reference}
            for mapping in [_LIZARD_TYPESCRIPT_EXCLUSION]
        ],
        "metrics_without_an_external_reference": uncovered_by_external,
        "every_primary_cell_mapped": len(primary) == len(LANGUAGES) * len(METRICS),
    }
