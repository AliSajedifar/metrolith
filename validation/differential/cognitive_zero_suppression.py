"""Zero-suppression semantics for Cognitive Complexity differential validation.

Cyclomatic complexity starts at 1, so a callable is always reported and silence
from a reference always means something went wrong. **Cognitive complexity
starts at 0, and most real callables score exactly 0** -- so silence is
ambiguous in a way the Complexity Contract 1.0.0 study never had to handle:

    a matched callable absent from a reference's output is EITHER a genuine
    zero the tool refused to print, OR a callable the tool never saw.

Reading the first as the second loses most of the corpus. Reading the second as
the first **manufactures agreement out of nothing** -- and it does so silently,
on exactly the callables where ArchLens is most likely to be right for the wrong
reason. That is the failure this module exists to make impossible.

The protocol, in the order it must happen
=========================================

**1. Establish identity independently of the metric.** A suppressed-zero
callable produces NO metric row, so the metric output cannot be the thing that
tells us the callable exists. Each suppressing reference is therefore paired
with an *enumerator*: a second invocation of the same tool, through the same
parser, using a rule whose value is >= 1 by construction and which therefore
reports every callable.

    Java   PMD `CyclomaticComplexity` at methodReportLevel=1 -- cyclomatic
           complexity is >= 1 for every method, so every method is reported.
    JS/TS  ESLint core `complexity` at threshold 0 -- same argument.

The enumerator is not a second opinion about anything. It answers exactly one
question: *did this tool's parser see a callable here?*

**2. Prove suppression by experiment, on this run, not by memory.** A recorded
verdict is a claim about a tool version; it is re-proved by
:func:`prove_suppression` against a live probe before any absence is
interpreted. The probe holds a genuine zero AND a non-zero control in ONE file,
so a tool that reports the control and not the zero has demonstrably read the
file: the omission cannot be a parse failure or a scope miss.

**3. Only then interpret absence.** :func:`interpret_absence` refuses to return
a zero unless BOTH preconditions hold, and it is the only place in the harness
permitted to turn silence into a number.

    identity established + suppression proven  -> inferred suppressed zero
    identity NOT established                   -> reference-missing, never zero
    suppression NOT proven                     -> not evaluable, never zero

What G2-A measured, and what it corrects
========================================

The measurements below were taken on 2026-08-11 against the probes in
`validation/complexity_g2a_20260811/probes/`. They **correct the record twice**:

* **G0** recorded gocognit as listing every function regardless of value. That
  was inferred from a probe in which nothing scored 0, and it is wrong for the
  default invocation: `-over N` means *strictly greater than N*, so the default
  `-over 0` hides every zero.
* **G1-A finding N3** corrected G0 to "four of five references cannot report a
  zero". **That is too strong.** `gocognit -over -1` reports `Complexity: 0`
  directly, and `-avg` independently confirms the zero callable was measured all
  along (0.5 over two functions, from output listing only the one scoring 1).
  G1-A had not tried a negative threshold.

**The measured count is three: Java, JavaScript and TypeScript.** Go and Python
observe a genuine zero directly, which makes them the two languages where a
zero-agreement claim rests on a reported number rather than on an inference.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# -- observability verdicts -------------------------------------------------

#: The reference prints a genuine 0 as a number. Nothing has to be inferred.
ZERO_OBSERVABLE_DIRECTLY = "zero_observable_directly"

#: The reference cannot print a 0 at any legal setting, AND that was proven by
#: experiment on a probe carrying a non-zero control in the same file.
ZERO_SUPPRESSED_PROVEN = "zero_suppressed_proven"

#: Suppression is suspected but not proven on this run. Absence stays
#: uninterpreted. This is a terminal state, not a stepping stone.
ZERO_SUPPRESSION_UNPROVEN = "zero_suppression_unproven"

VERDICTS = (
    ZERO_OBSERVABLE_DIRECTLY,
    ZERO_SUPPRESSED_PROVEN,
    ZERO_SUPPRESSION_UNPROVEN,
)

# -- how absence was interpreted -------------------------------------------
#
# Deliberately a different vocabulary from the record outcomes in
# `cognitive_layer2`: this module decides the INTERPRETATION, the record module
# decides the LABEL, and keeping them separate means neither can drift into
# implying the other.

#: Absence may be read as a reference value of 0.
ABSENCE_INFERRED_ZERO = "inferred_suppressed_zero"

#: The reference's own parser never reported this callable at all. Absence says
#: nothing about a value.
ABSENCE_REFERENCE_MISSING = "reference_did_not_enumerate_the_callable"

#: Absence cannot be interpreted on this run.
ABSENCE_NOT_EVALUABLE = "absence_not_interpretable"

ABSENCE_VERDICTS = (
    ABSENCE_INFERRED_ZERO,
    ABSENCE_REFERENCE_MISSING,
    ABSENCE_NOT_EVALUABLE,
)


class SuppressionNotProven(RuntimeError):
    """A caller tried to interpret absence without a live suppression proof.

    Typed, and raised rather than defaulted, because every safe-looking default
    here is wrong: returning 0 invents agreement and returning None silently
    drops the most common callable in the corpus.
    """


@dataclass(frozen=True)
class SuppressionProbe:
    """One measured answer to "can this reference print a genuine zero?".

    ``control_reported`` is what makes the probe evidence rather than an
    absence: the zero and the control live in the SAME file, so a reported
    control proves the file was parsed and the missing zero is the tool's
    choice.
    """

    language: str
    reference: str
    reference_version: str
    #: The zero callable's value as the reference reported it, or None if the
    #: reference did not report the callable at all.
    zero_callable_value: int | None
    #: The control callable's value. None means the probe itself is broken.
    control_callable_value: int | None
    #: How the tool suppresses, or how it was made to stop suppressing.
    mechanism: str
    #: The exact invocation, so the probe is reproducible from the record.
    invocation: str

    @property
    def control_reported(self) -> bool:
        return self.control_callable_value is not None

    @property
    def verdict(self) -> str:
        if not self.control_reported:
            # The control is the probe's own validity check. Without it, an
            # absent zero proves nothing at all -- the tool may simply not have
            # run.
            return ZERO_SUPPRESSION_UNPROVEN
        if self.zero_callable_value is not None:
            return ZERO_OBSERVABLE_DIRECTLY
        return ZERO_SUPPRESSED_PROVEN

    def as_dict(self) -> dict[str, Any]:
        return {
            "language": self.language,
            "reference": self.reference,
            "reference_version": self.reference_version,
            "invocation": self.invocation,
            "mechanism": self.mechanism,
            "zero_callable_value": self.zero_callable_value,
            "control_callable_value": self.control_callable_value,
            "control_reported": self.control_reported,
            "verdict": self.verdict,
        }


# ---------------------------------------------------------------------------
# The recorded baseline, MEASURED 2026-08-11.
# ---------------------------------------------------------------------------
#
# This is not a substitute for running the probe. It is the value a live probe
# must reproduce; `prove_suppression` refuses when they disagree, because a
# reference whose zero behaviour changed under the same version string is a
# study-invalidating event rather than a detail to absorb.

RECORDED_PROBES: dict[str, SuppressionProbe] = {
    "Go": SuppressionProbe(
        language="Go",
        reference="gocognit",
        reference_version="1.2.1",
        zero_callable_value=0,
        control_callable_value=1,
        mechanism=(
            "`-over N` means STRICTLY GREATER THAN N, so the default `-over 0` "
            "hides every zero and `-over -1` reports it. There is no dedicated "
            "include-zeros flag; the negative threshold is the mechanism. "
            "`-avg` reported 0.5 over two functions while listing only the one "
            "scoring 1, which independently proves the zero was measured and "
            "merely unprinted."
        ),
        invocation="gocognit -over -1 -json <file>",
    ),
    "Java": SuppressionProbe(
        language="Java",
        reference="PMD CognitiveComplexity",
        reference_version="7.7.0",
        zero_callable_value=None,
        control_callable_value=1,
        mechanism=(
            "`reportLevel` is a MINIMUM and PMD refuses any non-positive value: "
            "a ruleset with reportLevel 0 or -1 fails XML validation with "
            "'Value should be positive' and the ruleset does not load at all. "
            "At the minimum legal reportLevel=1 the zero callable is absent "
            "while the control in the same file is reported. Suppression is "
            "therefore structural, not a configuration choice."
        ),
        invocation="pmd check -R <ruleset reportLevel=1> -f text -d <file>",
    ),
    "JavaScript": SuppressionProbe(
        language="JavaScript",
        reference="eslint-plugin-sonarjs S3776",
        reference_version="4.2.0",
        zero_callable_value=None,
        control_callable_value=1,
        mechanism=(
            "The rule reports only when value > threshold, and its own JSON "
            "schema rejects a negative threshold ('Value -1 should be >= 0'). "
            "At threshold 0 the control (value 1) is reported and the zero is "
            "absent. No legal setting makes a zero observable."
        ),
        invocation="eslint with sonarjs/cognitive-complexity: ['warn', 0]",
    ),
    "TypeScript": SuppressionProbe(
        language="TypeScript",
        reference="eslint-plugin-sonarjs S3776",
        reference_version="4.2.0",
        zero_callable_value=None,
        control_callable_value=1,
        mechanism=(
            "Same rule and same schema constraint as JavaScript, measured "
            "separately on a `.ts` probe rather than inferred from the "
            "JavaScript result, because SonarJS runs one rule over two parses."
        ),
        invocation="eslint with sonarjs/cognitive-complexity: ['warn', 0]",
    ),
    "Python": SuppressionProbe(
        language="Python",
        reference="cognitive_complexity",
        reference_version="1.3.0",
        zero_callable_value=0,
        control_callable_value=1,
        mechanism=(
            "An API returning an `int`, not a reporting tool with a threshold. "
            "`get_cognitive_complexity` returned 0 for the zero callable. "
            "Nothing is suppressed and nothing has to be inferred."
        ),
        invocation="cognitive_complexity.api.get_cognitive_complexity(<FunctionDef>)",
    ),
}

#: Which reference needs an independent enumerator, and what that enumerator is.
#: A language whose reference observes zero directly needs none, and says so
#: rather than carrying an unused entry.
ENUMERATORS: dict[str, str] = {
    "Java": (
        "PMD `CyclomaticComplexity` with methodReportLevel=1. Cyclomatic "
        "complexity is >= 1 for every method by construction, so every method "
        "is reported. Measured on the probe: both `zeroCallable` and "
        "`nonZeroCallable` enumerated, where the cognitive rule reported only "
        "the second."
    ),
    "JavaScript": (
        "ESLint core `complexity` at threshold 0, same parser as SonarJS. "
        "Cyclomatic complexity is >= 1 by construction, so every function is "
        "reported. Measured on the probe: both callables enumerated."
    ),
    "TypeScript": (
        "ESLint core `complexity` at threshold 0. Measured on the `.ts` probe: "
        "both callables enumerated."
    ),
}

NO_ENUMERATOR_NEEDED: dict[str, str] = {
    "Go": "gocognit -over -1 reports the zero itself; identity comes with the value.",
    "Python": "the API returns an int for every callable; identity comes with the value.",
}


def recorded_probe(language: str) -> SuppressionProbe:
    return RECORDED_PROBES[language]


def zero_observability_claim(language: str) -> str:
    """The mapping-facing sentence, built from the measurement rather than typed.

    Starts with the verdict constant so a caller can classify on the prefix
    without re-deriving it, and so a changed measurement changes the mapping
    text automatically instead of leaving a stale claim behind.
    """
    probe = RECORDED_PROBES[language]
    verdict = probe.verdict
    if verdict == ZERO_OBSERVABLE_DIRECTLY:
        return (
            f"{verdict}: {probe.mechanism} Absence of a callable from this "
            f"reference's output is therefore NEVER read as a zero -- it is an "
            f"unenumerated callable."
        )
    enumerator = ENUMERATORS[language]
    return (
        f"{verdict}: {probe.mechanism} Absence may be read as a reference zero "
        f"ONLY after identity is established independently by {enumerator}"
    )


def prove_suppression(live: SuppressionProbe) -> SuppressionProbe:
    """Re-prove a reference's zero behaviour on this run. Refuse on drift.

    Returns the live probe when it reproduces the recorded verdict. Raises when
    it does not, because a reference that changed its zero behaviour under an
    unchanged version string invalidates every absence this study has ever
    interpreted -- that is a stop-and-look event, not a value to carry forward.
    """
    recorded = RECORDED_PROBES.get(live.language)
    if recorded is None:
        raise SuppressionNotProven(
            f"{live.language}: no recorded zero-suppression baseline exists, so "
            f"a live probe has nothing to be checked against"
        )
    if not live.control_reported:
        raise SuppressionNotProven(
            f"{live.language}: the probe's NON-ZERO control was not reported by "
            f"{live.reference}. The probe is invalid: an absent zero proves "
            f"nothing when the tool cannot be shown to have read the file"
        )
    if live.verdict != recorded.verdict:
        raise SuppressionNotProven(
            f"{live.language}: {live.reference} {live.reference_version} now "
            f"behaves as {live.verdict!r} but the study recorded "
            f"{recorded.verdict!r}. Every absence interpreted under the "
            f"recorded verdict is now in question; this is refused rather than "
            f"absorbed"
        )
    return live


@dataclass(frozen=True)
class AbsenceVerdict:
    """How one absent reference row was interpreted, and on what grounds."""

    verdict: str
    reference_value: int | None
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "absence_verdict": self.verdict,
            "reference_value": self.reference_value,
            "reason": self.reason,
        }


def interpret_absence(
    *,
    language: str,
    identity_established: bool,
    suppression: SuppressionProbe | None,
) -> AbsenceVerdict:
    """Turn one absent reference row into a verdict. The ONLY place that may.

    ``identity_established`` must come from the independent enumerator, never
    from the metric output -- the metric output is precisely what is missing.

    ``suppression`` must be a probe that has passed :func:`prove_suppression` on
    this run. ``None`` means no proof was offered, and no proof means no zero.
    """
    if suppression is None:
        return AbsenceVerdict(
            ABSENCE_NOT_EVALUABLE,
            None,
            f"{language}: no live zero-suppression proof was offered for this "
            f"run, so absence cannot be interpreted. It is NOT read as zero.",
        )

    if suppression.language != language:
        raise SuppressionNotProven(
            f"a {suppression.language} suppression proof cannot license an "
            f"absence in {language}"
        )

    verdict = suppression.verdict

    if verdict == ZERO_OBSERVABLE_DIRECTLY:
        # The tool prints zeros. An absent row is a callable it never
        # enumerated, and reading it as zero would invent a measurement the
        # tool would have printed if it had made one.
        return AbsenceVerdict(
            ABSENCE_REFERENCE_MISSING,
            None,
            f"{language}: {suppression.reference} reports a genuine zero as a "
            f"number, so an absent row is an unenumerated callable and never a "
            f"zero.",
        )

    if verdict != ZERO_SUPPRESSED_PROVEN:
        return AbsenceVerdict(
            ABSENCE_NOT_EVALUABLE,
            None,
            f"{language}: zero suppression is {verdict!r} on this run, so "
            f"absence cannot be interpreted. It is NOT read as zero.",
        )

    if not identity_established:
        # Proven suppression is not enough on its own. Without an independent
        # enumeration this callable may simply be outside the reference's
        # population -- which is D2, a population difference, not a zero.
        return AbsenceVerdict(
            ABSENCE_REFERENCE_MISSING,
            None,
            f"{language}: {suppression.reference} suppresses zero, but this "
            f"callable was NOT enumerated independently, so its absence is a "
            f"population difference rather than a suppressed measurement. "
            f"Never read as zero.",
        )

    return AbsenceVerdict(
        ABSENCE_INFERRED_ZERO,
        0,
        f"{language}: the callable was enumerated independently by the "
        f"reference's own parser and is absent from a metric output that is "
        f"PROVEN on this run to suppress a genuine zero "
        f"({suppression.mechanism.split('.')[0]}). Absence is therefore a "
        f"reference value of 0, recorded as INFERRED rather than reported.",
    )


def capability_report(live: dict[str, SuppressionProbe] | None = None) -> dict[str, Any]:
    """The zero-observability table, for the study record."""
    probes = live or RECORDED_PROBES
    return {
        "measured_on": "2026-08-11",
        "protocol": (
            "identity first, from an independent enumerator; then a live "
            "suppression proof carrying a non-zero control in the same file; "
            "only then may absence be read as zero"
        ),
        "corrects": [
            "G0: gocognit does NOT list every function regardless of value -- "
            "`-over N` is strictly-greater-than, so the default hides zeros.",
            "G1-A N3: 'four of five references cannot report a zero' is too "
            "strong. Measured: THREE (Java, JavaScript, TypeScript). gocognit "
            "reports a zero under `-over -1`, which N3 had not tried.",
        ],
        "by_language": {
            language: probe.as_dict() for language, probe in sorted(probes.items())
        },
        "enumerators": dict(sorted(ENUMERATORS.items())),
        "no_enumerator_needed": dict(sorted(NO_ENUMERATOR_NEEDED.items())),
        "zero_observable_directly": sorted(
            language
            for language, probe in probes.items()
            if probe.verdict == ZERO_OBSERVABLE_DIRECTLY
        ),
        "zero_suppressed_proven": sorted(
            language
            for language, probe in probes.items()
            if probe.verdict == ZERO_SUPPRESSED_PROVEN
        ),
    }
