"""C4 Layer-C2 adjudication: every observation gets a cause, or the pass fails.

This is a **separate pass over persisted raw observations**, never a step inside
the comparison. The raw file keeps every disagreement as ``unresolved``; this
module reads it, assigns a cause from the eight-class taxonomy, and writes a
second file. Both survive, so the unclassified baseline can always be re-read.

Two rules the shape of this module enforces:

**A family is a mechanism, not a label.** Each entry names the construct that
produces the difference and points at the evidence that the mechanism accounts
for the observed numbers -- per callable, not for a sample. A family that cannot
show its arithmetic is not a classification, it is a guess with a nicer name.

**`unresolved` is a real outcome, not a leftover.** :func:`adjudicate` returns
every observation it could not place, and the caller is expected to refuse to
close on a non-empty list. Silence is what a mismatch uses to survive.

An external tool's difference is never grounds for changing ArchLens. The
external families below are all properties of two definitions, and ArchLens
production semantics were not modified for any of them.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from validation.differential import records
from validation.differential.complexity_layer2 import (
    C4_CAUSES,
    CAUSE_ADAPTER_DEFECT,
    CAUSE_ARCHLENS_DEFECT,
    CAUSE_DEFINITION_MISMATCH,
    CAUSE_MATCHING_AMBIGUITY,
    CAUSE_PARSER_LIMITATION,
    CAUSE_REFERENCE_TOOL_LIMITATION,
    TIER_EXTERNAL,
    TIER_PRIMARY,
)


@dataclass(frozen=True)
class Family:
    """One adjudicated root cause, with the evidence that it accounts."""

    family_id: str
    cause: str
    mechanism: str
    evidence: str
    resolution: str
    matches: Callable[[Mapping[str, Any]], bool]

    def as_dict(self) -> dict[str, Any]:
        return {
            "family_id": self.family_id,
            "cause": self.cause,
            "mechanism": self.mechanism,
            "evidence": self.evidence,
            "resolution": self.resolution,
        }


def _is(observation: Mapping[str, Any], **fields: Any) -> bool:
    return all(observation.get(name) == value for name, value in fields.items())


# ---------------------------------------------------------------------------
# Families observed on the initial five subjects.
# ---------------------------------------------------------------------------

FAMILIES: tuple[Family, ...] = (
    # -- not a disagreement, but still an observation that needs a cause ----
    Family(
        "parso-cannot-parse-match",
        CAUSE_PARSER_LIMITATION,
        "parso 0.8.7 has no `match` grammar and emits error nodes for it.",
        "10 observations on `layer2-python-ralph`, two callables x five "
        "tree-derived metrics. The adapter reports NOT EVALUABLE with reason "
        "`parso_grammar_cannot_parse_construct`; reporting 0 for a construct "
        "the parser could not read would manufacture agreement. `nloc` and the "
        "parameter count survive, being read from the source and the signature.",
        "Left as not-evaluable. Upgrading parso is the only fix and is not a "
        "C4 decision.",
        lambda item: (
            item["agreement_status"] == records.AGREEMENT_NOT_EVALUABLE
            and (item.get("not_evaluable_reason") or "").startswith("parso_grammar")
        ),
    ),
    Family(
        "population-difference-creates-span-ambiguity",
        CAUSE_MATCHING_AMBIGUITY,
        "Lizard measures callables ArchLens excludes -- Go `func_literal`, "
        "Python nested functions -- and their spans sit INSIDE the canonical "
        "callable's span, so the span-only match tier finds several candidates.",
        "37 observations: 4 on `layer2-go-shop`, 32 on `layer2-python-ralph`, "
        "1 on `layer2-javascript-monolith`. Every one is refused, never "
        "guessed: a wrong pairing does not lose an observation, it invents a "
        "disagreement. Each of these callables is compared exactly against the "
        "primary independent adapter, so none is left uncompared.",
        "Recorded. Tightening the matcher would mean giving it more than "
        "identity evidence, which is exactly what it must not have.",
        lambda item: item["metric"] == "identity",
    ),
    # -- external cyclomatic references -------------------------------------
    Family(
        "eslint-counts-constructs-the-contract-does-not",
        CAUSE_DEFINITION_MISMATCH,
        "ESLint core `complexity` counts three constructs Complexity Contract "
        "1.0.0 section 7.1 does not: every optional-chaining `?.`, every "
        "defaulted parameter, and every default inside a destructuring pattern "
        "-- including one written in an ordinary declaration such as "
        "`const { theme = 'system' } = useTheme()`. The contract gives `?.` "
        "explicitly +0 and has no default-value rule at all.",
        "All **122** TypeScript disagreements, checked per callable against the "
        "identity `eslint == archlens + optional_chains + defaulted_parameters "
        "+ destructuring_defaults`: 122 of 122 hold exactly, residual 0. "
        "`adjudicated/eslint_accounting.json`.",
        "Not forced to equality. ArchLens is unchanged; the mapping records the "
        "difference.",
        lambda item: (
            item["reference_tier"] == TIER_EXTERNAL
            and item["language"] == "TypeScript"
            and item["agreement_status"] == records.AGREEMENT_DISAGREEMENT
        ),
    ),
    Family(
        "lizard-folds-java-lambda-and-anonymous-class-bodies",
        CAUSE_DEFINITION_MISMATCH,
        "Lizard folds a lambda body and an anonymous-class body into the "
        "enclosing method. Contract section 3 makes both traversal boundaries, "
        "so their control flow belongs to no ArchLens record.",
        "All **7** Java disagreements, checked per callable against "
        "`lizard == archlens + decisions and operators inside those bodies`: "
        "7 of 7 hold exactly. Spans agree on every one, so nothing here is a "
        "span artefact. `adjudicated/lizard_accounting.json`.",
        "Not forced to equality; this is the divergence the definition mapping "
        "already carries for Java.",
        lambda item: (
            item["reference_tier"] == TIER_EXTERNAL
            and item["language"] == "Java"
            and item["agreement_status"] == records.AGREEMENT_DISAGREEMENT
        ),
    ),
    Family(
        "lizard-python-definition-differences",
        CAUSE_DEFINITION_MISMATCH,
        "Four separate Lizard rules, three of them definitional: it counts a "
        "`finally` block as a decision (the contract gives it +0), it folds "
        "`lambda` bodies into the enclosing function (a boundary in the "
        "contract), and it counts no `match` arm at all (the contract counts "
        "each non-wildcard arm and each guard).",
        "Together with the f-string term below, all **26** Python "
        "disagreements are accounted for per callable by "
        "`lizard == archlens + finally + lambda_bodies - match_arms - "
        "fstring_contents`: 26 of 26 hold exactly. "
        "`adjudicated/lizard_accounting.json`.",
        "Not forced to equality. ArchLens is unchanged.",
        lambda item: (
            item["reference_tier"] == TIER_EXTERNAL
            and item["language"] == "Python"
            and item["agreement_status"] == records.AGREEMENT_DISAGREEMENT
        ),
    ),
    Family(
        "lizard-javascript-span-truncation",
        CAUSE_REFERENCE_TOOL_LIMITATION,
        "Lizard's JavaScript tokenizer ends the function early -- `deploy` is "
        "reported as lines 18-20 of a function that runs to 27, and three React "
        "components are truncated inside their JSX. Decisions after the "
        "truncation point therefore fall outside the function Lizard measured.",
        "All **4** JavaScript disagreements. No arithmetic identity is offered "
        "because there is none to offer: the two numbers describe different "
        "regions of source. The evidence is the span pair itself, recorded per "
        "callable in `adjudicated/lizard_accounting.json`. Same class as "
        "Lizard's already-recorded broken TypeScript spans.",
        "Recorded as a tool limitation. ArchLens's spans for these callables "
        "were confirmed against the source by inspection.",
        lambda item: (
            item["reference_tier"] == TIER_EXTERNAL
            and item["language"] == "JavaScript"
            and item["agreement_status"] == records.AGREEMENT_DISAGREEMENT
        ),
    ),
)


#: Defects the campaign found, fixed and re-ran. These describe the RAW,
#: pre-fix observations; after the fix the corresponding families are empty,
#: which is the point of re-running rather than re-labelling.
FIXED_DEFECTS: tuple[Family, ...] = (
    Family(
        "archlens-unbraced-body-opens-no-nesting-level",
        CAUSE_ARCHLENS_DEFECT,
        "`max_nesting_depth` opened a level on seeing a block, not on entering "
        "a branch, so `if (c) statement` without braces measured one level "
        "shallower than the same code with braces. Contract section 10 nests on "
        "the BODY of an `if`/`else if`/`else` branch or a loop.",
        "56 observations before the fix: 54 TypeScript, 1 JavaScript, 1 Java. "
        "Reproduced minimally in `tests/test_complexity_c4_layer2_findings.py`.",
        "Fixed in `modules/callable_analysis/{js_ts,java}.py` by keying nesting "
        "on the branch-body slot, plus a `unbraced_body` rule so a childless "
        "body such as `if (a) break;` registers its depth. An empty block still "
        "registers nothing, and `else if` still measures flat.",
        lambda item: False,
    ),
    Family(
        "archlens-comments-counted-as-parameters",
        CAUSE_ARCHLENS_DEFECT,
        "`comment` is a NAMED child of `formal_parameters` in tree-sitter, so "
        "counting named children counted prose. A two-parameter TypeScript "
        "function documented with two comments published 4.",
        "1 observation on `layer2-typescript-securo`. Java and Go were checked "
        "and are immune: both count from an allowlist.",
        "Fixed in `modules/callable_analysis/js_ts.py`; the TypeScript `this` "
        "exclusion is pinned unchanged beside it.",
        lambda item: False,
    ),
    Family(
        "archlens-else-holding-an-if-flattened-as-elif",
        CAUSE_ARCHLENS_DEFECT,
        "`elif` and `else:` + indented `if` are the same `ast` shape "
        "(`orelse == [If]`), so flattening on shape flattened a real `else` "
        "branch too. Contract section 10 makes an `else` branch "
        "nesting-increasing.",
        "1 observation on `layer2-typescript-securo`'s Python backend.",
        "Fixed in `modules/callable_analysis/python.py` by separating the two "
        "on `col_offset`: an `elif` sits at the outer `if`'s column.",
        lambda item: False,
    ),
    Family(
        "adapter-decorated-async-def-loses-its-decorator-lines",
        CAUSE_ADAPTER_DEFECT,
        "parso wraps `async def` in `async_funcdef`, so a decorated async "
        "function's `decorated` node is the grandparent; a parent-only check "
        "dropped every decorator line from the span.",
        "264 `nloc` observations on `layer2-typescript-securo`, up to 80 lines "
        "on a single `@tool(...)`.",
        "Fixed in the parso adapter; version 2.0.0 -> 2.2.0.",
        lambda item: False,
    ),
    Family(
        "adapter-bare-except-is-not-a-decision",
        CAUSE_ADAPTER_DEFECT,
        "parso gives `except X:` an `except_clause` node but leaves a bare "
        "`except:` as a keyword leaf of `try_stmt`, so a rule table keyed on "
        "`except_clause` missed it. Contract section 7.1 counts each `except` "
        "clause, bare or not.",
        "18 observations (9 cyclomatic, 9 decision) across "
        "`layer2-python-ralph` and `layer2-java-demo`.",
        "Fixed in the parso adapter; version 2.0.0 -> 2.2.0.",
        lambda item: False,
    ),
    Family(
        "adapter-module-level-def-in-a-block-left-out-of-the-population",
        CAUSE_ADAPTER_DEFECT,
        "A block statement is not a scope. ArchLens's population rule is *no "
        "callable ancestor*, so a `def` guarded by a module-level `if FLAG:` is "
        "a module function; the adapter's scope walk stopped at `if_stmt`.",
        "2 unmatched ArchLens rows on `layer2-python-ralph`. A `def` inside a "
        "function, and a `def` inside an `if` inside a class body, are both "
        "still excluded -- pinned as guards.",
        "Fixed in the parso adapter; version 2.0.0 -> 2.2.0.",
        lambda item: False,
    ),
    Family(
        "adapter-expressions-in-nested-statements-counted-nowhere",
        CAUSE_ADAPTER_DEFECT,
        "The TypeScript-compiler adapter counted operators only inside the "
        "conditions it visited, and ran its expression pass on top-level "
        "statements only. A `??` or a ternary in a `return` inside an `if` or a "
        "`case` therefore counted nowhere.",
        "117 observations on `layer2-typescript-securo`, worst case 22 of 24 "
        "boolean operators lost on one callable.",
        "Fixed in the Node adapter by routing every statement descent through "
        "one function that runs both passes; version 2.0.0 -> 2.2.0.",
        lambda item: False,
    ),
    Family(
        "adapter-for-of-iterable-not-scanned",
        CAUSE_ADAPTER_DEFECT,
        "The `for..of` branch walked only the body, so `for (const x of xs ?? "
        "[])` lost the operator in the iterable. The C-style `for` header's "
        "initializer and incrementor had the same gap.",
        "4 observations on `layer2-typescript-securo`, found only after the "
        "previous fix removed the noise that was hiding them.",
        "Fixed in the Node adapter; version 2.2.0.",
        lambda item: False,
    ),
)


def classify(observation: Mapping[str, Any]) -> Family | None:
    """The one family this observation belongs to, or None."""
    if observation["agreement_status"] == records.AGREEMENT_EXACT:
        return None
    for family in FAMILIES:
        if family.matches(observation):
            return family
    return None


def adjudicate(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Assign a cause to every non-exact observation in a persisted raw file."""
    adjudicated: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    counts: dict[str, int] = {}

    for comparison in raw["comparisons"]:
        for observation in comparison["observations"]:
            if observation["agreement_status"] == records.AGREEMENT_EXACT:
                continue
            family = classify(observation)
            if family is None:
                unresolved.append(dict(observation))
                continue
            counts[family.family_id] = counts.get(family.family_id, 0) + 1
            adjudicated.append(dict(
                observation,
                cause=family.cause,
                family_id=family.family_id,
                adjudication_note=family.mechanism,
                adjudication_status=records.ADJUDICATION_MANUAL_SINGLE_REVIEWER,
            ))

    return {
        "stage": "adjudicated",
        "reporting_rule": (
            "Raw counts only. No accuracy percentage: a reference is a "
            "triangulation mechanism, not ground truth, and success is every "
            "disagreement localized and classified -- not agreement."
        ),
        "cause_taxonomy": list(C4_CAUSES),
        "families": [family.as_dict() for family in FAMILIES],
        "fixed_defects": [family.as_dict() for family in FIXED_DEFECTS],
        "observations_by_family": dict(sorted(counts.items())),
        "unresolved_count": len(unresolved),
        "unresolved": unresolved,
        "observations": adjudicated,
    }


def write(raw_path: Path, destination: Path) -> dict[str, Any]:
    raw = json.loads(Path(raw_path).read_text(encoding="utf-8"))
    payload = adjudicate(raw)
    Path(destination).parent.mkdir(parents=True, exist_ok=True)
    Path(destination).write_text(
        json.dumps(payload, indent=2), encoding="utf-8", newline="\n"
    )
    return payload
