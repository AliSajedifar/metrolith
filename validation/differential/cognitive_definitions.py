"""Definition mappings for ArchLens Cognitive Complexity differential validation.

Written BEFORE any real-subject run, for the same reason the Complexity Contract
1.0.0 mappings were: a numeric disagreement means nothing until both sides'
definitions are written down, and a divergence enumerated in advance is
classified rather than explained after the fact.

**One tier only, and that is the whole shape of this study.** Complexity Contract
1.0.0 validation had two tiers -- primary independent adapters implementing
*ArchLens's* definition with a different parser, and external tools implementing
their own. **Cognitive Complexity has no primary independent adapter.** Every
reference here implements its *own* definition, so:

* a difference is a property of two definitions, never on its own evidence of an
  ArchLens defect;
* **no defect claim may rest on a reference alone.** The frozen rule table and
  the G1-A corpus are the specification; a reference is a second opinion about
  the definition, not an oracle over the implementation.

The G0 finding that governs the naming is load-bearing here too: the four
references **disagree with each other**, and two disagree with the published
model. There is no single "Sonar behaviour" to be compatible with, so nothing in
this module is permitted to describe a comparison as a compatibility check.

`AGREEMENT` in this study therefore means "these two definitions, mapped as
recorded below, produced the same number on the same callable" -- and nothing
more.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from validation.differential.cognitive_zero_suppression import (
    ZERO_OBSERVABLE_DIRECTLY,
    ZERO_SUPPRESSED_PROVEN,
    zero_observability_claim,
)

# -- comparability classifications ------------------------------------------
#
# Reused verbatim from the Complexity Contract 1.0.0 study so one reader
# vocabulary covers both metrics.

EXACT = "exact_comparable"
DOCUMENTED = "definitionally_comparable_with_documented_differences"
NOT_COMPARABLE = "not_comparable_definition"
NOT_EVALUABLE = "not_evaluable"

CLASSIFICATIONS = (EXACT, DOCUMENTED, NOT_COMPARABLE, NOT_EVALUABLE)

LANGUAGES = ("Go", "Java", "JavaScript", "TypeScript", "Python")

#: The one metric this study compares. Named rather than assumed, so a reader
#: never mistakes it for the seven Complexity Contract 1.0.0 metrics.
METRIC = "cognitive_complexity"

# ---------------------------------------------------------------------------
# Pinned reference versions. Task 1 of G2-A.
# ---------------------------------------------------------------------------
#
# Every entry is EXACT and was read from the provisioned artifact on
# 2026-08-11, not from a project page and not from a prior record. The
# `nodepkgs-g0/package.json` that provisioned the JS/TS references carries
# CARET RANGES (`^4.2.0`), which is not a pin: the pins below are the versions
# the lockfile actually resolved, and `verify_pins` re-reads them at run time so
# a reinstall that drifts is refused rather than absorbed.

COGNITIVE_REFERENCE_VERSIONS: dict[str, str] = {
    # Java
    "pmd": "7.7.0",
    "jdk": "17.0.17.10 (Eclipse Adoptium)",
    # Go -- see GO_TOOLCHAIN_PINS for the two-toolchain decision
    "gocognit": "1.2.1",
    "go_measurement_toolchain": "1.23.12",
    "go_gocognit_build_toolchain": "1.25.12",
    "golang.org/x/tools": "0.42.0",
    # JavaScript / TypeScript
    "eslint-plugin-sonarjs": "4.2.0",
    "eslint": "9.15.0",
    "@typescript-eslint/parser": "8.15.0",
    "typescript": "5.6.3",
    "node": "22.20.0",
    # Python
    "cognitive_complexity": "1.3.0",
    "python_reference_interpreter": "3.13.9",
}

#: Which reference covers each language, and the exact rule or entry point.
COGNITIVE_REFERENCE: dict[str, str] = {
    "Go": "gocognit 1.2.1",
    "Java": "PMD 7.7.0 CognitiveComplexity",
    "JavaScript": "eslint-plugin-sonarjs 4.2.0 S3776",
    "TypeScript": "eslint-plugin-sonarjs 4.2.0 S3776",
    "Python": "cognitive_complexity 1.3.0",
}

# ---------------------------------------------------------------------------
# The divergence register, D1 - D33. Task 5 of G2-A; D28-D33 added by G2-B.
# ---------------------------------------------------------------------------
#
# **The register is NOT contiguous, and that is recorded rather than hidden.**
# D20 and D21 were never issued: G0 defined D1-D17, the G1-A corpus added D18
# and D19, and Amendment 002 resumed numbering at D22. Enumerating the register
# as "D1-D33" without saying so would assert 33 divergences where 31 exist, and
# `NEVER_ISSUED` exists so a test can pin that rather than a reader discovering
# two silent holes.
#
# D28-D33 were issued by the G2-B real-subject campaign and its closure analysis. The register grows when a
# real subject exhibits a definitional difference the synthetic corpus did not
# reach -- which is the reason Layer 2 exists.

NEVER_ISSUED = ("D20", "D21")

#: Every issued divergence, with the source that issued it and its current
#: state. `status` is the honest one: a divergence RESOLVED by measurement is
#: not deleted, because the evidence that produced it is why the decision was
#: made.
DIVERGENCES: dict[str, dict[str, str]] = {
    "D1": {
        "scope": "all languages",
        "summary": (
            "nested callables are EXCLUDED from the enclosing callable; PMD, "
            "gocognit and cognitive_complexity fold them in"
        ),
        "direction": "ArchLens lower wherever a lambda, callback, closure or nested function appears",
        "issued_by": "G0 4.1",
        "status": (
            "confirmed. G1-A resolved the anonymous-class sub-case: PMD BOTH "
            "folds an anonymous class's method into the enclosing method AND "
            "reports it as its own method, so the same code is counted twice "
            "across two records -- which a comparison must never read as "
            "agreement"
        ),
    },
    "D2": {
        "scope": "all languages",
        "summary": (
            "population: CG measures the canonical `methods_functions` "
            "population; constructors, anonymous-class methods and "
            "declaration-only signatures are outside it"
        ),
        "direction": "rows exist on one side only; compared on the matched intersection",
        "issued_by": "G0 4.1",
        "status": "structural; handled by population accounting, never by a metric row",
    },
    "D3": {
        "scope": "Java, JavaScript, TypeScript",
        "summary": "a genuine zero is unobservable in the reference",
        "direction": "absence must be interpreted, never read as agreement",
        "issued_by": "G0 4.1",
        "status": (
            "CORRECTED TWICE. G0 said three references; G1-A N3 said four; G2-A "
            "measured THREE -- gocognit reports a zero under `-over -1` and the "
            "Python API returns one. See `cognitive_zero_suppression`"
        ),
    },
    "D4": {
        "scope": "Java",
        "summary": "`synchronized`: both ArchLens and PMD give +0 and no nesting",
        "direction": "no divergence",
        "issued_by": "G0 4.2",
        "status": "recorded because Complexity Contract 1.0.0 treats it differently and a reader will expect one",
    },
    "D5": {
        "scope": "Java",
        "summary": "JDK 21 `case X when g` guard: ArchLens +1 (`F-GUARD`)",
        "direction": "unknown against PMD",
        "issued_by": "G0 4.2",
        "status": (
            "PARTIALLY RESOLVED by G1-A N5: PMD parses JDK 21 syntax under "
            "`--use-version java-21` on a JDK 17 runtime, so the toolchain is "
            "not the blocker. N2 is -- a guard can only appear in a switch, and "
            "PMD scores a switch EXPRESSION at 0"
        ),
    },
    "D6": {
        "scope": "JavaScript, TypeScript",
        "summary": "`||` sequences: ArchLens +1, SonarJS +0",
        "direction": "ArchLens higher, on very many callables",
        "issued_by": "G0 4.3",
        "status": (
            "confirmed at source, not inferred: `cjs/S3776/rule.js` excludes the "
            "operator by name. The single largest expected divergence"
        ),
    },
    "D7": {
        "scope": "JavaScript, TypeScript",
        "summary": "`??` sequences: ArchLens +1, SonarJS +0",
        "direction": "ArchLens higher",
        "issued_by": "G0 4.3",
        "status": "confirmed at source, same exclusion as D6",
    },
    "D8": {
        "scope": "JavaScript, TypeScript",
        "summary": "recursion: ArchLens +1, SonarJS +0",
        "direction": "ArchLens higher",
        "issued_by": "G0 4.3",
        "status": "confirmed; SonarJS does not count recursion at all",
    },
    "D9": {
        "scope": "JavaScript, TypeScript",
        "summary": "JSX short-circuits: ArchLens counts them, SonarJS excludes them entirely",
        "direction": "ArchLens higher across React code",
        "issued_by": "G0 4.3",
        "status": "confirmed on the `.tsx` corpus case, with a non-JSX control",
    },
    "D10": {
        "scope": "JavaScript, TypeScript",
        "summary": "nested functions: SonarJS emits them as separate entries and does NOT fold them",
        "direction": "no divergence -- it AGREES with the D1 exclusion",
        "issued_by": "G0 4.3",
        "status": "confirmed. JS/TS is the one family where the boundary rule aligns",
    },
    "D11": {
        "scope": "Python",
        "summary": "comprehensions: ArchLens counts them, the reference scores 0",
        "direction": "ArchLens higher",
        "issued_by": "G0 4.4",
        "status": "confirmed; ArchLens diverges knowingly -- a comprehension is a loop and a filter",
    },
    "D12": {
        "scope": "Python",
        "summary": "`match`: the reference cannot see it and returns 0",
        "direction": "NOT EVALUABLE, never a number",
        "issued_by": "G0 4.4",
        "status": (
            "confirmed. The reference returns a clean 0 rather than refusing, "
            "which is the dangerous shape: it looks like a measurement"
        ),
    },
    "D13": {
        "scope": "Python",
        "summary": (
            "nested `def`: the reference FOLDS the body into the enclosing "
            "callable; ArchLens excludes it (`B-NESTED`)"
        ),
        "direction": "ArchLens lower",
        "issued_by": "G0 4.4",
        "status": (
            "confirmed. CORRECTED at the G2-B closure from source: G0 recorded "
            "the fold as carrying NO nesting increment, but "
            "`process_node_itself` puts `FunctionDef`/`AsyncFunctionDef`/"
            "`Lambda` in `incrementers_nodes` and does `increment_by += 1`. The "
            "body is folded in AND one level deeper"
        ),
    },
    "D14": {
        "scope": "Python",
        "summary": "`with`: both +0 -- no divergence against the reference",
        "direction": "no divergence",
        "issued_by": "G0 4.4",
        "status": (
            "recorded because it DOES diverge from Complexity Contract 1.0.0 "
            "10, where `with` nests. The two contracts must not be assumed to agree"
        ),
    },
    "D15": {
        "scope": "Python",
        "summary": (
            "boolean sequences: CG counts one per maximal same-operator run; "
            "Complexity Contract 1.0.0 counts operator TOKENS (n-1)"
        ),
        "direction": "CG and CX legitimately differ on the same source",
        "issued_by": "G0 4.4",
        "status": "a CX/CG divergence, not a reference divergence; recorded so the two are never summed",
    },
    "D16": {
        "scope": "Go",
        "summary": "nested `func_literal`: gocognit folds it in, ArchLens excludes it",
        "direction": "ArchLens lower; the most common Go divergence given `defer func(){}()`",
        "issued_by": "G0 4.5",
        "status": "confirmed",
    },
    "D17": {
        "scope": "Go",
        "summary": "`goto`: ArchLens +1 (`F-LABELJUMP`)",
        "direction": "was unknown",
        "issued_by": "G0 4.5",
        "status": "RESOLVED by G1-A N4: gocognit scores `goto` at +1. No divergence",
    },
    "D18": {
        "scope": "Python",
        "summary": "`try...else`: ArchLens +1 (`F-TRYELSE`), the reference does not count it",
        "direction": "ArchLens higher by 1",
        "issued_by": "G1-A corpus authoring",
        "status": (
            "confirmed, and PREDICTED at a value of 3 before the tool was run. "
            "Deliberately kept, on the same reasoning as `F-LOOPELSE`: it is a "
            "branch. Amendment 001 isolated it by removing the compounded "
            "`finally` term"
        ),
    },
    "D19": {
        "scope": "Java",
        "summary": "multi-catch `catch (A | B e)`: one catch, and the `|` is not a boolean operator",
        "direction": "no divergence",
        "issued_by": "G1-A corpus authoring",
        "status": "RESOLVED by G1-A N6: PMD agrees",
    },
    "D22": {
        "scope": "Java, Go",
        "summary": "recursion counted PER CALL SITE by PMD and gocognit; ArchLens caps at +1 per callable",
        "direction": "reference higher on any callable with two or more self-calls",
        "issued_by": "Amendment 002",
        "status": "measured: 1/2/3 calls scored 1/2/3 by both tools",
    },
    "D23": {
        "scope": "Go",
        "summary": "receiver-qualified self-recursion is not detected by gocognit",
        "direction": "ArchLens higher",
        "issued_by": "Amendment 002",
        "status": "measured: gocognit scored a receiver self-call at 0",
    },
    "D24": {
        "scope": "Python",
        "summary": "`self.`-qualified self-recursion is not detected by cognitive_complexity",
        "direction": "ArchLens higher",
        "issued_by": "Amendment 002",
        "status": "measured: the reference scored `self.m()` at 0",
    },
    "D25": {
        "scope": "Go, Python",
        "summary": "parentheses SPLIT a same-operator run in gocognit and cognitive_complexity",
        "direction": "reference higher on `a && (b && c)`",
        "issued_by": "Amendment 002",
        "status": "measured; ArchLens treats parentheses as transparent (rule table 3.2)",
    },
    "D26": {
        "scope": "Java, Python",
        "summary": "two same-operator runs separated by another operator are MERGED by PMD and cognitive_complexity",
        "direction": "reference lower on `a || b && c || d`",
        "issued_by": "Amendment 002",
        "status": "measured; ArchLens scores 3 by owner pin",
    },
    "D27": {
        "scope": "Java",
        "summary": "a named local class is folded into the enclosing method AND reported separately by PMD",
        "direction": "double counting across two records",
        "issued_by": "Amendment 002",
        "status": "measured; the same shape as D1's anonymous-class finding",
    },
    "D28": {
        "scope": "Python",
        "summary": (
            "branch-body nesting in an `if`/`elif`/`else` chain. "
            "`process_control_flow_breaker` gives an `If` whose `orelse` is "
            "exactly one `If` -- i.e. one followed by an `elif` -- "
            "`increment = 0` and NO `increment_by` raise, and "
            "`process_child_nodes` then hands that same `increment_by` to the "
            "body and to the `orelse` alike. So a chain's branch bodies are "
            "measured at the chain's own level. Rule table 2.2 instead keeps "
            "the chain flat (`F-ELSEIF`/`F-ELSE` are +1 each) and deepens each "
            "branch BODY by one"
        ),
        "direction": "reference lower on any construct inside a chain branch",
        "issued_by": "G2-B real-subject campaign, 2026-08-12",
        "status": (
            "CHARACTERIZED FROM SOURCE at the G2-B closure, not inferred from "
            "output: `cognitive_complexity/utils/ast.py`, "
            "`process_control_flow_breaker` and `process_child_nodes`. "
            "Reproduced on a 132-probe matrix varying chain length, branch "
            "position, trailing `else`, construct kind, nesting depth and "
            "construct count (`reductions/d28_matrix.py`): the reference falls "
            "short of the frozen table by 0 on 66 probes, 1 on 24 and 2 on 42, "
            "and a transcription of the source repaired ONLY for this mechanism "
            "reproduces the frozen table on 132/132. **An earlier '-1 per "
            "structural construct in a non-final branch' rate model was "
            "falsified and withdrawn** -- the effect is path-dependent, not a "
            "rate. Header position is NOT part of this divergence; see D33"
        ),
    },
    "D29": {
        "scope": "Java",
        "summary": (
            "PMD nests a construct in the `else` body of an if/else-if chain "
            "one level deeper than the frozen table. Rule table 2.2 puts an "
            "`else` body at nesting 1, so a ternary there scores 2"
        ),
        "direction": "reference higher",
        "issued_by": "G2-B real-subject campaign, 2026-08-12",
        "status": (
            "NEW. Derived on `LoginServlet.doPost`: rule table 1 + 1 + 1 + 2 = "
            "5, ArchLens 5, PMD 6. `reductions/ElseNesting.java` reproduces the "
            "shape and ArchLens measures the frozen value"
        ),
    },
    "D30": {
        "scope": "Java",
        "summary": (
            "a call to an OVERLOAD of the current method is scored as "
            "`F-RECURSION` by ArchLens and is not recursion at all; PMD "
            "resolves types and does not count it"
        ),
        "direction": "ArchLens higher",
        "issued_by": "G2-B real-subject campaign, 2026-08-12",
        "status": (
            "NEW as an observation, but NOT a defect: rule table 4.2 A fires on "
            "a bare callee identifier equal to the callable's name, and 4.4 "
            "records the residual false positive as the stated cost of having "
            "no symbol table. G0 3.1 said the same -- 'overloads are "
            "indistinguishable'. Observed on `LibraryUtils.lendBook`, which "
            "calls `lendBook(Book, Borrower, Date)` from "
            "`lendBook(String, String, Date)`: ArchLens 1, PMD 0"
        ),
    },
    "D31": {
        "scope": "Python",
        "summary": (
            "cognitive_complexity 1.3.0 does not traverse INTO a boolean "
            "operand, so a ternary written inside one is uncounted. Rule table "
            "6.2 keeps operands traversable and 6.1 suppresses only the "
            "overlapping rule, never the operands"
        ),
        "direction": "reference lower",
        "issued_by": "G2-B real-subject campaign, 2026-08-12",
        "status": (
            "NEW. `reductions/py_boolop_operand.py`: `a or (f(user) if user "
            "else None)` is one `or` run plus one `S-TERNARY` at nesting 0 -- "
            "rule table 2, ArchLens 2, reference 1. Observed on "
            "`Job.prepare_params`"
        ),
    },
    "D33": {
        "scope": "Python",
        "summary": (
            "a construct in a HEADER -- an `if`/`while` test or a `for` "
            "iterable -- is scored one level DEEPER by the reference. "
            "`process_child_nodes` hands the raised `increment_by` to every "
            "child of the node, and the test/iterable are children. Rule table "
            "2.1 raises nesting for the BODY only, so a header construct sits "
            "at the enclosing construct's own level"
        ),
        "direction": "reference higher",
        "issued_by": "G2-B closure analysis, 2026-08-12",
        "status": (
            "NEW, and deliberately NOT folded into D28: it is a different "
            "site with its own evidence. `reductions/py_header_position.py`: a "
            "ternary in a loop BODY agrees at 3, while the same ternary in a "
            "`for` iterable, an `if` test and a `while` test is ArchLens 2 "
            "against reference 3 in each. It accounts for the whole -5 residue "
            "on `DNSaaSPublisherMixin.get_auto_txt_data`, whose five ternaries "
            "all sit in a `for` iterable"
        ),
    },
    "D32": {
        "scope": "Python",
        "summary": (
            "cognitive_complexity 1.3.0 does not recognize `async for` as a "
            "loop: it contributes 0 AND raises no nesting, so every construct "
            "inside an async loop is scored one level shallower. Rule table "
            "2.1 `S-LOOP` covers `for` in all its forms"
        ),
        "direction": "reference lower, by 1 per construct inside the loop",
        "issued_by": "G2-B closure analysis, 2026-08-12",
        "status": (
            "NEW. `reductions/py_async.py`: `for` agrees at 3 while the "
            "identical `async for` is ArchLens 3 against reference 1 -- the "
            "loop itself and the nesting of its body are both lost. `with` and "
            "`async with` agree at 1, so this is specific to the async LOOP "
            "form and not to async syntax generally. It accounts for the whole "
            "-6 on `OllamaProvider.chat_stream`, whose six constructs all sit "
            "inside an `async for`"
        ),
    },
}

#: D25 and D26 together are the pair that makes the naming discipline
#: unavoidable, and the mappings below cite it rather than restating it.
BOOLEAN_SEQUENCE_NO_CONSENSUS = (
    "NO TWO REFERENCES AGREE ON BOTH boolean-sequence cases: gocognit and "
    "cognitive_complexity split a same-operator run at parentheses (D25) where "
    "PMD and SonarJS do not, and PMD and cognitive_complexity merge two "
    "same-operator runs separated by a different operator (D26) where gocognit "
    "does not. The definition could not have been inferred from any of them, "
    "which is why the owner pinned the seven expressions in rule table 3.3."
)

# -- the two standing reference limitations, N2 and N3 ----------------------

N2_LIMITATION = (
    "N2: PMD 7.7.0 scores a Java switch EXPRESSION at 0 while scoring the "
    "statement form at 1 -- isolated on a two-method probe. Because a `case ... "
    "when` guard can only appear inside a switch, PMD can validate NEITHER "
    "`S-SWITCH`'s expression form NOR `F-GUARD`. Those two cells are "
    "rule-validated only, against the G1-A corpus, and are reported "
    "not_comparable rather than compared."
)

N3_LIMITATION = (
    "N3: zero observability. G0 recorded three references unable to report a "
    "genuine zero; G1-A corrected that to four; G2-A MEASURED three -- gocognit "
    "reports a zero under `-over -1`, which G1-A had not tried, and the Python "
    "API returns one. Java, JavaScript and TypeScript remain unable to report a "
    "zero at any legal threshold, and since most real callables score zero this "
    "governs what a campaign in those three languages can claim."
)


@dataclass(frozen=True)
class CognitiveMapping:
    """One reviewed (language, reference) comparison for Cognitive Complexity.

    Every field is mandatory and none may be blank. A mapping with an empty
    semantics field would be a claim that a comparison was reviewed when it was
    not, which is the exact failure the C4-B coverage audit was built to fail.
    """

    language: str
    reference: str
    reference_version: str
    unit_compared: str
    callable_population: str
    zero_suppression_behaviour: str
    nesting_semantics: str
    boolean_sequence_semantics: str
    recursion_semantics: str
    nested_callable_treatment: str
    classification: str
    known_divergences: tuple[str, ...]
    limitations: tuple[str, ...]
    metric: str = METRIC

    def __post_init__(self) -> None:
        if self.classification not in CLASSIFICATIONS:
            raise ValueError(f"unknown classification {self.classification!r}")
        if self.language not in LANGUAGES:
            raise ValueError(f"unknown language {self.language!r}")
        unknown = [item for item in self.known_divergences if item not in DIVERGENCES]
        if unknown:
            raise ValueError(
                f"{self.language}: cites divergences that were never issued: "
                f"{unknown}. D20 and D21 do not exist -- see NEVER_ISSUED."
            )

    def as_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "language": self.language,
            "reference": self.reference,
            "reference_version": self.reference_version,
            "unit_compared": self.unit_compared,
            "callable_population": self.callable_population,
            "zero_suppression_behaviour": self.zero_suppression_behaviour,
            "nesting_semantics": self.nesting_semantics,
            "boolean_sequence_semantics": self.boolean_sequence_semantics,
            "recursion_semantics": self.recursion_semantics,
            "nested_callable_treatment": self.nested_callable_treatment,
            "comparability": self.classification,
            "known_divergences": [
                {"id": item, **DIVERGENCES[item]} for item in self.known_divergences
            ],
            "limitations": list(self.limitations),
        }


_POPULATION = (
    "ArchLens measures the canonical `methods_functions` population "
    "(Complexity Contract 1.0.0 5.1, reused verbatim by rule table 7.3). The "
    "reference measures its own, which is BROADER in four of five languages. "
    "Comparison is on the matched intersection only; population counts are "
    "reported separately and are never folded into a metric row (D2)."
)

_NESTING = (
    "ArchLens: a structural rule contributes `1 + nesting_level`, where "
    "nesting_level counts enclosing nesting-raising constructs INSIDE the "
    "measured callable and the callable's own body is level 0. `finally`, "
    "Python `with` and Java `synchronized` raise NO nesting (`Z-FINALLY`, "
    "`Z-WITH`, `Z-SYNCHRONIZED`), and an `else if` chain stays flat."
)

_RECURSION = (
    "ArchLens: `F-RECURSION` is +1 per callable AT MOST ONCE, recognized only "
    "for a bare self-name call or a member call on the language's explicit "
    "current receiver or enclosing type name (rule table 4)."
)


def _mapping(
    language: str,
    *,
    zero: str,
    boolean: str,
    recursion: str,
    nested: str,
    classification: str,
    divergences: tuple[str, ...],
    limitations: tuple[str, ...],
    nesting: str = _NESTING,
) -> CognitiveMapping:
    return CognitiveMapping(
        language=language,
        reference=COGNITIVE_REFERENCE[language],
        reference_version=COGNITIVE_REFERENCE_VERSIONS[
            {
                "Go": "gocognit",
                "Java": "pmd",
                "JavaScript": "eslint-plugin-sonarjs",
                "TypeScript": "eslint-plugin-sonarjs",
                "Python": "cognitive_complexity",
            }[language]
        ],
        unit_compared="one canonical callable, paired on identity alone",
        callable_population=_POPULATION,
        zero_suppression_behaviour=zero,
        nesting_semantics=nesting,
        boolean_sequence_semantics=boolean,
        recursion_semantics=recursion,
        nested_callable_treatment=nested,
        classification=classification,
        known_divergences=divergences,
        limitations=limitations,
    )


_MAPPINGS: dict[str, CognitiveMapping] = {
    "Go": _mapping(
        "Go",
        zero=zero_observability_claim("Go"),
        boolean=(
            "ArchLens: +1 per maximal same-operator run in FLATTENED SOURCE "
            "ORDER, parentheses transparent. gocognit SPLITS a run at "
            "parentheses (D25) but does not merge runs separated by another "
            "operator. " + BOOLEAN_SEQUENCE_NO_CONSENSUS
        ),
        recursion=_RECURSION + " gocognit counts PER CALL SITE (D22) and does "
        "not detect a receiver-qualified self-call at all (D23).",
        nested=(
            "gocognit FOLDS a `func_literal` into the enclosing function and "
            "raises nesting for it; ArchLens excludes it entirely (D16). Given "
            "`defer func(){}()` this is the most common Go divergence."
        ),
        classification=DOCUMENTED,
        divergences=("D1", "D2", "D16", "D17", "D22", "D23", "D25"),
        limitations=(
            "gocognit is built by a DIFFERENT Go toolchain than the one that "
            "runs the Complexity Contract 1.0.0 Go adapter -- see "
            "GO_TOOLCHAIN_PINS. Both are pinned explicitly and the harness "
            "refuses a silent escalation.",
        ),
    ),
    "Java": _mapping(
        "Java",
        zero=zero_observability_claim("Java"),
        boolean=(
            "ArchLens: +1 per maximal same-operator run in flattened source "
            "order. PMD MERGES two same-operator runs separated by a different "
            "operator (D26), scoring `a || b && c || d` at 2 where ArchLens "
            "scores 3. PMD does not split at parentheses. "
            + BOOLEAN_SEQUENCE_NO_CONSENSUS
        ),
        recursion=_RECURSION + " PMD counts PER CALL SITE (D22) and does detect "
        "`this.`-qualified self-calls.",
        nested=(
            "PMD folds a lambda, anonymous class and named local class into the "
            "enclosing method AND separately reports the local/anonymous "
            "method as its own record (D1, D27). The same source is counted "
            "twice across two records, which a comparison must never read as "
            "agreement. ArchLens excludes all of them."
        ),
        classification=DOCUMENTED,
        divergences=(
            "D1", "D2", "D3", "D4", "D5", "D19", "D22", "D26", "D27",
            "D29", "D30",
        ),
        limitations=(N2_LIMITATION, N3_LIMITATION),
    ),
    "JavaScript": _mapping(
        "JavaScript",
        zero=zero_observability_claim("JavaScript"),
        boolean=(
            "ArchLens: +1 per maximal same-operator run over `&&`, `||` and "
            "`??`. **SonarJS 4.2.0 scores `||` and `??` at ZERO** -- verified in "
            "its shipped `cjs/S3776/rule.js`, which excludes both operators by "
            "name (D6, D7). This is the single largest expected divergence and "
            "will appear on a large fraction of real callables."
        ),
        recursion=_RECURSION + " SonarJS does NOT count recursion at all (D8).",
        nested=(
            "SonarJS emits a nested function as its OWN entry and does not fold "
            "it into the enclosing function -- which AGREES with the ArchLens "
            "exclusion (D10). JS/TS is the one family where the boundary rule "
            "aligns."
        ),
        classification=DOCUMENTED,
        divergences=("D1", "D2", "D3", "D6", "D7", "D8", "D9", "D10"),
        limitations=(N3_LIMITATION,),
    ),
    "TypeScript": _mapping(
        "TypeScript",
        zero=zero_observability_claim("TypeScript"),
        boolean=(
            "As JavaScript (D6, D7). `??` is pervasive in real TypeScript, so "
            "the divergence density is expected to be higher here than in JS."
        ),
        recursion=_RECURSION + " SonarJS does NOT count recursion at all (D8).",
        nested=(
            "As JavaScript: SonarJS reports a nested function separately and "
            "agrees with the ArchLens exclusion (D10)."
        ),
        classification=DOCUMENTED,
        divergences=("D1", "D2", "D3", "D6", "D7", "D8", "D9", "D10"),
        limitations=(
            N3_LIMITATION,
            "JSX short-circuits are excluded entirely by SonarJS's "
            "`getJsxShortCircuitNodes` (D9), so a React subject diverges on "
            "nearly every rendering method.",
        ),
    ),
    "Python": _mapping(
        "Python",
        zero=zero_observability_claim("Python"),
        boolean=(
            "ArchLens: +1 per maximal same-operator run over `and`/`or`. "
            "cognitive_complexity SPLITS at parentheses (D25) AND merges runs "
            "separated by another operator (D26) -- it is the only reference "
            "exhibiting both. " + BOOLEAN_SEQUENCE_NO_CONSENSUS
        ),
        recursion=_RECURSION + " cognitive_complexity caps per callable, which "
        "AGREES with ArchLens, but does not detect `self.`-qualified self-calls (D24).",
        nested=(
            "cognitive_complexity folds a nested `def` into the enclosing "
            "function WITHOUT a nesting increment; ArchLens excludes it (D13)."
        ),
        classification=DOCUMENTED,
        divergences=(
            "D1", "D2", "D11", "D12", "D13", "D14", "D15", "D18", "D24",
            "D25", "D26", "D28", "D31", "D32", "D33",
        ),
        limitations=(
            "**Not parser-independent.** cognitive_complexity imports stdlib "
            "`ast` -- the SAME parser ArchLens uses for Python. It is an "
            "independent DEFINITION, not an independent PARSE, so a Python "
            "parse defect is invisible to it. Any five-language claim must say "
            "'parser-level independence in four'.",
            "`match` is unparsed and scored 0 rather than refused (D12), so a "
            "callable containing one is reported not_comparable and never "
            "compared. The clean 0 is the dangerous shape: it looks measured.",
        ),
    ),
}

#: Cells that exist as a comparison but cannot be compared, stated explicitly
#: rather than left as an absent row.
NOT_COMPARABLE_CELLS: tuple[dict[str, str], ...] = (
    {
        "language": "Java",
        "reference": COGNITIVE_REFERENCE["Java"],
        "construct": "switch EXPRESSION form (`S-SWITCH`)",
        "reason": N2_LIMITATION,
    },
    {
        "language": "Java",
        "reference": COGNITIVE_REFERENCE["Java"],
        "construct": "`case X when g` pattern guard (`F-GUARD`)",
        "reason": N2_LIMITATION,
    },
    {
        "language": "Python",
        "reference": COGNITIVE_REFERENCE["Python"],
        "construct": "`match` statement (`S-SWITCH`, `F-GUARD`)",
        "reason": (
            "D12: cognitive_complexity 1.3.0 cannot see `match` and returns 0 "
            "rather than refusing. A callable containing one is reported "
            "not_comparable; the 0 is never compared."
        ),
    },
)


def all_mappings() -> list[CognitiveMapping]:
    return [_MAPPINGS[language] for language in LANGUAGES]


def mapping_for(language: str) -> CognitiveMapping:
    return _MAPPINGS[language]


def coverage_report() -> dict[str, Any]:
    """What this study can and cannot compare, stated before it runs."""
    mappings = all_mappings()
    cited: set[str] = set()
    for mapping in mappings:
        cited.update(mapping.known_divergences)
    return {
        "metric": METRIC,
        "reference_versions": dict(COGNITIVE_REFERENCE_VERSIONS),
        "languages": list(LANGUAGES),
        "cells": len(mappings),
        "every_language_mapped": len(mappings) == len(LANGUAGES),
        "by_classification": {
            name: sum(1 for item in mappings if item.classification == name)
            for name in CLASSIFICATIONS
        },
        "divergences_issued": len(DIVERGENCES),
        "divergence_ids_never_issued": list(NEVER_ISSUED),
        "divergences_cited_by_a_mapping": sorted(cited, key=lambda item: int(item[1:])),
        "divergences_not_cited_by_any_mapping": sorted(
            set(DIVERGENCES) - cited, key=lambda item: int(item[1:])
        ),
        "zero_observable_languages": [
            language
            for language in LANGUAGES
            if zero_observability_claim(language).startswith(ZERO_OBSERVABLE_DIRECTLY)
        ],
        "zero_suppressed_languages": [
            language
            for language in LANGUAGES
            if zero_observability_claim(language).startswith(ZERO_SUPPRESSED_PROVEN)
        ],
        "not_comparable_cells": [dict(item) for item in NOT_COMPARABLE_CELLS],
        "single_tier_note": (
            "There is NO primary independent adapter for Cognitive Complexity. "
            "Every reference implements its own definition, so no defect claim "
            "may rest on one. The frozen rule table and the G1-A corpus are the "
            "specification."
        ),
    }
