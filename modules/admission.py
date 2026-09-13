"""The shared admission vocabulary for standalone evidence documents.

Two very different consumers ask the same question about the same documents:
:mod:`modules.dossier` asks it to decide whether a figure may be *shown*, and
:mod:`modules.policy.evidence` asks it to decide whether a figure may be
*gated on*. The question is identical -- may this document speak for this run?
-- so the vocabulary that answers it must have exactly one definition.

It previously had one definition in `dossier` and none anywhere else. Adding a
second copy for the policy gate is how two consumers start disagreeing about
what `provenance_mismatch` means, which is precisely the drift this module
exists to prevent. Nothing here decides anything: it declares the closed set of
outcomes, their published meanings, and the per-format provenance rule. The
deciding lives in each consumer.

**The set is closed.** A consumer may not invent a seventh outcome, and
:func:`admission_meaning` raises rather than inventing a meaning for one, so an
unrecognized outcome cannot reach a reader as an empty string.

**Consequence wording belongs to the consumer, not to this module.** The
meanings below state what happened and what the consumer's convention is; they
are the exact strings the released dossier document format embeds, and they are
kept byte-for-byte so moving them here changes no published document. A gate
states its own consequence through its own typed reason and through the
structural guarantee that a non-admitted record carries no document at all --
never by rewording a shared string.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Mapping

from modules.standalone_contracts import (
    CHANGED_CODE_FORMAT,
    DUPLICATION_FORMAT,
    HOTSPOT_FORMAT,
)

#: The document passed its own validator and describes the same analyzed thing
#: this run describes.
ADMITTED = "admitted"
#: No document of this kind was supplied. An absence of INPUT, and never a
#: measured absence of findings.
NOT_SUPPLIED = "not_supplied"
#: The supplied file could not be read or parsed.
UNREADABLE = "unreadable"
#: The document's format identity is not an active exact-version standalone
#: contract, or its analysis contract version is not the one this build reads.
CONTRACT_INCOMPATIBLE = "contract_incompatible"
#: The identity is active but the document's own validator rejected it.
VALIDATOR_REJECTED = "validator_rejected"
#: The document is valid and describes something else.
PROVENANCE_MISMATCH = "provenance_mismatch"

#: Closed, ordered set. Ordering is the natural reading order of the gates:
#: supplied at all, readable, identity, contents, provenance.
ADMISSION_STATES: tuple[str, ...] = (
    ADMITTED,
    NOT_SUPPLIED,
    UNREADABLE,
    CONTRACT_INCOMPATIBLE,
    VALIDATOR_REJECTED,
    PROVENANCE_MISMATCH,
)

#: Published meaning of each outcome. These strings are embedded verbatim in the
#: released dossier document format, so they are stable text and not a place to
#: improve wording casually.
ADMISSION_MEANINGS: Mapping[str, str] = MappingProxyType({
    ADMITTED: (
        "the document passed its own validator and describes the same analyzed "
        "scope as this run; its summary is shown"
    ),
    NOT_SUPPLIED: (
        "no document of this kind was supplied; this is an absence of input, "
        "not a measured absence of findings"
    ),
    UNREADABLE: "the supplied file could not be read or parsed as JSON",
    CONTRACT_INCOMPATIBLE: (
        "the document's format identity is not an active exact-version "
        "standalone contract"
    ),
    VALIDATOR_REJECTED: (
        "the document carries an active identity but its own validator "
        "rejected its contents"
    ),
    PROVENANCE_MISMATCH: (
        "the document is valid but describes a different run, revision or "
        "analysis scope; its numbers are NOT shown, because a figure measured "
        "elsewhere would be read as a figure measured here"
    ),
})

#: What each supplement kind must agree with, and the fields compared. Stated as
#: data so the rule is inspectable and identical in every consumer.
PROVENANCE_RULES: Mapping[str, str] = MappingProxyType({
    DUPLICATION_FORMAT: (
        "the analyzed commit SHA and analysis scope hash must match a "
        "repository measured in this run"
    ),
    HOTSPOT_FORMAT: "the source run id must be this run's id",
    CHANGED_CODE_FORMAT: (
        "the HEAD side's analyzed commit SHA must match a repository measured "
        "in this run; the base side is a different revision by construction"
    ),
    "archlens-check-result": "the evaluated run id must be this run's id",
})


class UnknownAdmissionState(ValueError):
    """A consumer produced an outcome outside the closed set."""


def admission_meaning(state: str) -> str:
    """The published meaning of one outcome.

    Raises rather than returning a default. A missing meaning must surface as a
    defect, because the alternative is a reader being handed an outcome with no
    explanation of what it cost them.
    """
    try:
        return ADMISSION_MEANINGS[state]
    except KeyError:
        raise UnknownAdmissionState(
            f"{state!r} is not one of {', '.join(ADMISSION_STATES)}"
        ) from None


def is_admitted(state: str) -> bool:
    """Whether this outcome permits the document's figures to be used at all.

    An unrecognized state raises rather than answering ``False``. "I do not know
    what this outcome is" and "this document may not be used" are different
    facts, and a consumer that conflated them would carry on past a defect.
    """
    admission_meaning(state)
    return state == ADMITTED


def provenance_rule(format_name: str | None) -> str | None:
    """The provenance sentence for one format, or ``None`` when none applies."""
    if format_name is None:
        return None
    return PROVENANCE_RULES.get(format_name)


__all__ = [
    "ADMISSION_MEANINGS",
    "ADMISSION_STATES",
    "ADMITTED",
    "CONTRACT_INCOMPATIBLE",
    "NOT_SUPPLIED",
    "PROVENANCE_MISMATCH",
    "PROVENANCE_RULES",
    "UNREADABLE",
    "UnknownAdmissionState",
    "VALIDATOR_REJECTED",
    "admission_meaning",
    "is_admitted",
    "provenance_rule",
]
