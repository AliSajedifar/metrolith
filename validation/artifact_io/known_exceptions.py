"""Known defects in the frozen Artifact Schema 1.5.0, in exactly one place.

Artifact Schema 1.5.0 is frozen, and it contains schemas that **contradict
their own producers and, in two cases, their own sibling schemas**. Each was
invisible until something started validating the affected document: a
successful run does not exercise any of them.

A waived document is **not schema-valid**. It is *accepted under a known 1.5
compatibility exception*, and every caller must report it that way. Calling it
valid would launder a defect into a guarantee.

Why waive at all, rather than enforce and let the artifacts fail? Because every
one of these fires only on a *failed* or non-CSV run. Enforcing them verbatim
would make whole classes of run unpublishable or unvalidatable — hiding
failures completely, which is strictly worse than the defect being hidden.

Why one module? Because finalization, ``archlens validate``, and the strict
reader must never disagree about the same artifact. Before this existed,
finalization waived F-P3-4 and published a run as ``completed_with_errors``
while ``archlens validate --schema-only`` reported that same run as failing on
exactly the violation finalization had waived. Two commands, one artifact,
opposite verdicts.

**These are not a permanent solution.** They are a bridge until the schemas are
corrected. A corrective *patch* version (1.5.1) was evaluated and is not
structurally supported: ``classify_artifact_schema`` keys on ``(major, minor)``
and discards the patch component, and ``load_schema`` / ``validate_document``
select a schema by *name* with no version parameter at all, so no reader could
distinguish a 1.5.1 artifact or validate against a specific version. The
corrections therefore belong in Artifact Schema 1.6.0. Once artifacts declare a
corrected schema, :func:`applies_to` stops matching and every exception here
becomes dead — which is the intended end state.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from .compatibility import parse_version
from .errors import StructuralError


@dataclass(frozen=True)
class AcceptedException:
    """One violation accepted under a documented exception."""

    issue_id: str
    schema: str
    artifact: str
    location: str
    message: str
    summary: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "issue_id": self.issue_id,
            "schema": self.schema,
            "artifact": self.artifact,
            "location": self.location,
            "message": self.message,
            "summary": self.summary,
            # Stated on every record so no consumer can read this as validity.
            "status": "accepted_under_known_1_5_compatibility_exception",
        }


@dataclass(frozen=True)
class KnownException:
    """A single, exactly-scoped defect in a frozen 1.5.0 schema."""

    issue_id: str
    schema: str
    location: str
    keyword: str
    summary: str
    resolution: str
    #: Optional extra predicate on the document, for value-specific exceptions.
    value_predicate: Any = None
    #: True when `location` is a prefix rather than an exact pointer.
    location_is_prefix: bool = False
    #: Set when the pointer carries a container index, e.g. `/3/input_line`.
    location_suffix: str | None = None

    def matches(self, schema_name: str, error: Any, document: Any) -> bool:
        if schema_name != self.schema:
            return False
        location = str(getattr(error, "location", "") or "")
        keyword = str((getattr(error, "detail", None) or {}).get("keyword", ""))
        if keyword != self.keyword:
            return False
        if self.location_is_prefix:
            if not location.startswith(self.location):
                return False
        elif self.location_suffix is not None:
            if not location.endswith(self.location_suffix):
                return False
        elif location != self.location:
            return False
        if self.value_predicate is not None:
            return bool(self.value_predicate(document, location))
        return True


def _resolve_pointer(document: Any, location: str) -> Any:
    """Resolve a JSON pointer against a document. ``_MISSING`` when absent."""
    current = document
    for raw in location.split("/"):
        if raw == "":
            continue
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(current, Mapping):
            if token not in current:
                return _MISSING
            current = current[token]
        elif isinstance(current, Sequence) and not isinstance(current, (str, bytes)):
            try:
                current = current[int(token)]
            except (ValueError, IndexError):
                return _MISSING
        else:
            return _MISSING
    return current


_MISSING = object()


def _measurement_outcome_is_failed(document: Any, location: str) -> bool:
    """Only the one value the sibling `run_manifest` schema explicitly allows."""
    del location
    return isinstance(document, Mapping) and document.get("measurement_outcome") == "failed"


def _input_line_is_the_absent_sentinel(document: Any, location: str) -> bool:
    """Only exactly ``0``, the sentinel for "not from an input-file line".

    A negative or otherwise nonsensical line number is a real violation and is
    left to fail. The value is resolved through the error's own JSON pointer, so
    this works for both the ``repository_document`` form (``/input_line``) and
    the ``analysis`` element form (``/3/input_line``).
    """
    value = _resolve_pointer(document, location)
    return value is not _MISSING and value == 0 and not isinstance(value, bool)


KNOWN_1_5_EXCEPTIONS: tuple[KnownException, ...] = (
    KnownException(
        issue_id="F-P3-3",
        schema="run_manifest",
        location="/output_failures/",
        location_is_prefix=True,
        keyword="type",
        summary=(
            "run_manifest declares output_failures entries as objects; the "
            "producer, every consumer, and the sibling run_status schema all "
            "use strings"
        ),
        resolution="Artifact Schema 1.6.0: item type becomes string",
    ),
    KnownException(
        issue_id="F-P3-4",
        schema="run_status",
        location="/measurement_outcome",
        keyword="enum",
        value_predicate=_measurement_outcome_is_failed,
        summary=(
            "measurement_outcome() returns 'failed', which run_manifest permits "
            "and run_status does not; run_status lists 'unavailable' instead"
        ),
        resolution=(
            "Artifact Schema 1.6.0: permit 'failed' instead of 'unavailable', "
            "matching the producer and the sibling schema"
        ),
    ),
    KnownException(
        issue_id="F-P3-5",
        schema="repository_document",
        location="/input_line",
        location_suffix="/input_line",
        keyword="minimum",
        value_predicate=_input_line_is_the_absent_sentinel,
        summary=(
            "RepositorySpec.input_line defaults to 0, the sentinel for a "
            "repository that did not come from an input-file line; the schema "
            "requires minimum 1 and cannot express the sentinel"
        ),
        resolution=(
            "Artifact Schema 1.6.0: allow 0, or make the field nullable and "
            "emit null, matching normalized_input's existing `or None` handling"
        ),
    ),
    KnownException(
        issue_id="F-P3-10",
        schema="repository_document",
        location="/expected_language",
        location_suffix="/expected_language",
        keyword="type",
        value_predicate=lambda document, location: (
            _resolve_pointer(document, location) is None
        ),
        summary=(
            "repository_document declares expected_language as a non-nullable "
            "string, but RepositorySpec.expected_language is optional, so any "
            "input row or --repo invocation without one emits null. The "
            "catalog_row and sheet_metrics_row schemas already declared "
            "[\"string\", \"null\"] for the same field"
        ),
        resolution=(
            "Artifact Schema 1.6.0: expected_language becomes "
            "[\"string\", \"null\"], matching the row schemas"
        ),
    ),
    KnownException(
        issue_id="F-P3-10",
        schema="analysis",
        location="/expected_language",
        location_suffix="/expected_language",
        keyword="type",
        value_predicate=lambda document, location: (
            _resolve_pointer(document, location) is None
        ),
        summary="the analysis.json element form of F-P3-10; same cause",
        resolution="Artifact Schema 1.6.0: see the repository_document entry",
    ),
    KnownException(
        issue_id="F-P3-6",
        schema="file_inventory",
        location="/git_mode",
        location_suffix="/git_mode",
        keyword="type",
        value_predicate=lambda document, location: (
            _resolve_pointer(document, location) is None
        ),
        summary=(
            "file_inventory declares git_mode as a non-nullable string, but the "
            "producer emits null for any file with no Git mode — every file in a "
            "non-Git directory, and every untracked file in a Git one. Its "
            "immediate neighbour git_symlink_target is declared "
            "[\"string\", \"null\"], so the omission is an oversight rather than "
            "a constraint"
        ),
        resolution=(
            "Artifact Schema 1.6.0: git_mode becomes [\"string\", \"null\"], "
            "matching git_symlink_target"
        ),
    ),
    KnownException(
        issue_id="F-P3-5",
        schema="analysis",
        # analysis.json is an array, so the pointer carries an element index:
        # `/0/input_line`, `/7/input_line`, and so on.
        location="/input_line",
        location_suffix="/input_line",
        keyword="minimum",
        value_predicate=_input_line_is_the_absent_sentinel,
        summary=(
            "the analysis.json element form of F-P3-5; same sentinel, same cause"
        ),
        resolution="Artifact Schema 1.6.0: see the repository_document entry",
    ),
)


class CompatibilityExceptionMisuse(RuntimeError):
    """A corrected artifact version tried to claim a 1.5 compatibility waiver."""


def refuse_if_corrected(declared_artifact_schema: Any, accepted: Sequence[Any]) -> None:
    """Hard rule: a 1.6+ artifact may never rely on a 1.5 exception.

    The waivers exist because Artifact Schema 1.5.0 is frozen and defective.
    1.6.0 corrects exactly those defects, so a 1.6 artifact needing one of them
    means either the correction did not land or a new producer defect appeared —
    both of which must fail loudly rather than be absorbed.
    """
    if not accepted:
        return
    parsed = parse_version(declared_artifact_schema)
    if parsed is not None and parsed >= (1, 6, 0):
        raise CompatibilityExceptionMisuse(
            f"artifact declares schema {declared_artifact_schema} but required "
            f"{len(accepted)} Artifact Schema 1.5 compatibility exception(s): "
            f"{sorted({getattr(item, 'issue_id', '?') for item in accepted})}. "
            f"1.6.0 corrects those defects, so this indicates either an "
            f"incomplete correction or a new producer defect."
        )


def applies_to(declared_artifact_schema: Any) -> bool:
    """Whether the 1.5 exceptions apply to an artifact declaring this version.

    Scoped to 1.5.x deliberately. A 1.6.0 artifact must be held to the corrected
    schemas with no waivers, so these die automatically rather than needing to
    be remembered and removed.
    """
    parsed = parse_version(declared_artifact_schema)
    if parsed is None:
        # Undeclared or unparseable: the compatibility layer handles that
        # separately and far more strictly. No waiver applies.
        return False
    # Exactly 1.5.0. Artifact Schema versions carry a zero patch component by
    # contract, so `1.5.1` is not a version whose defects were ever reviewed and
    # must not inherit 1.5.0's waivers.
    return parsed == (1, 5, 0)


def partition(
    schema_name: str,
    errors: Iterable[StructuralError],
    document: Any,
    *,
    declared_artifact_schema: Any = None,
    enabled: bool = True,
) -> tuple[list[StructuralError], list[AcceptedException]]:
    """Split violations into real ones and ones accepted under an exception.

    ``enabled=False`` (or an artifact outside 1.5.x) waives nothing, so callers
    that want the unfiltered truth can have it.
    """
    # A waiver requires positive evidence that the artifact declares 1.5.x.
    # An absent, empty or unparseable version earns no exception: that artifact
    # is in worse shape than one with a known schema defect, and the strict
    # compatibility layer already treats it far more harshly.
    if not enabled or not applies_to(declared_artifact_schema):
        return list(errors), []

    real: list[StructuralError] = []
    accepted: list[AcceptedException] = []
    for error in errors:
        exception = next(
            (
                item for item in KNOWN_1_5_EXCEPTIONS
                if item.matches(schema_name, error, document)
            ),
            None,
        )
        if exception is None:
            real.append(error)
            continue
        accepted.append(AcceptedException(
            issue_id=exception.issue_id,
            schema=schema_name,
            artifact=str(getattr(error, "artifact", "") or ""),
            location=str(getattr(error, "location", "") or ""),
            message=str(getattr(error, "message", "") or ""),
            summary=exception.summary,
        ))
    return real, accepted


def issue_ids() -> tuple[str, ...]:
    """Every distinct issue id, for reporting and for tests."""
    return tuple(sorted({item.issue_id for item in KNOWN_1_5_EXCEPTIONS}))


def describe() -> list[dict[str, str]]:
    """Human- and machine-readable description of every exception."""
    return [
        {
            "issue_id": item.issue_id,
            "schema": item.schema,
            "location": item.location + ("*" if item.location_is_prefix else ""),
            "keyword": item.keyword,
            "summary": item.summary,
            "resolution": item.resolution,
        }
        for item in KNOWN_1_5_EXCEPTIONS
    ]
