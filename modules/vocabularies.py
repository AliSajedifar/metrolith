"""The single typed source of truth for Metrolith status vocabularies.

Every vocabulary here used to be written out at least three times — as a literal
in the producer, as a ``frozenset`` in the diagnostic consumer, and again in
tests — with nothing forcing the copies to agree. They drifted, and the drift
was not theoretical: ``modules/inventory.py`` emitted
``git_mode_map_status = "unavailable_not_git"`` while the consumer's set of
recognized values did not contain it, so a **completely healthy** analysis of a
directory that simply is not a Git checkout reported

    unknown_categories = ("git_mode_map_status='unavailable_not_git'",)

That is a false alarm about a fully explained outcome. It is latent today,
because a cloned repository is always a Git repository — but it becomes
systematic the moment local directory analysis ships, since every such run takes
that code path.

**Status and cause are different things.** ``unavailable_not_git`` is a compound
value: it encodes the state (the Git mode map is unavailable) *and* the reason
(the directory is not a Git repository) in one string. The canonical model
separates them::

    git_mode_map_status = "unavailable"
    git_mode_map_reason = "not_git_repository"

That separation is deliberately **not** applied to the wire format yet. Artifact
Schema 1.5.0 is frozen (decision D-6): its schemas already ship inside a built
wheel and real 1.5.0 artifacts exist, so the emitted value stays
``unavailable_not_git`` and the normalized model is reached through
:func:`normalize_git_mode_map_state`. The wire change belongs to Artifact Schema
1.6.0, alongside the rest of the B1 source-model work.

So this module holds two related things, and the distinction matters:

* the **normalized model** — what Metrolith means (1.6.0 target);
* the **1.5.0 wire vocabulary** — what Metrolith currently writes and must keep
  reading, including the compound legacy value.

A reader that only accepts the normalized model would reject every artifact
produced to date. A consumer that only knows the wire vocabulary cannot tell
state from cause. Both are needed, and they are named so that it is obvious
which one is in play.

Reasons are added **only when a producing condition exists**. An aspirational
reason vocabulary would be a list of causes Metrolith cannot actually distinguish,
which is worse than no vocabulary at all.
"""

from __future__ import annotations

from enum import Enum

# ---------------------------------------------------------------- statuses ---
#
# `(str, Enum)` rather than `StrEnum` to match `EvidenceScope` and `MetricEffect`
# in `modules/diagnostics.py`. Every call site reads `.value` explicitly.


class AnalysisStatus(str, Enum):
    """Terminal state of one repository's analysis."""

    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    SKIPPED = "skipped"


class MetricStatus(str, Enum):
    """Availability of one metric or metric family.

    ``NOT_APPLICABLE`` is not a failure: it records that the metric has no
    meaning for the subject, which is different from having failed to measure it.
    """

    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"
    NOT_APPLICABLE = "not_applicable"


class PartialOrigin(str, Enum):
    """Why a repository result is ``partial``, within a closed taxonomy.

    ``MULTIPLE`` means more than one origin contributed, not "unknown".
    """

    NONE = "none"
    ACQUISITION_OR_INVENTORY = "acquisition_or_inventory"
    EXPECTED_LANGUAGE_FAMILY = "expected_language_family"
    SECONDARY_SUPPORTED_LANGUAGE_ONLY = "secondary_supported_language_only"
    MULTIPLE = "multiple"


class GitModeMapStatus(str, Enum):
    """Normalized state of the Git mode map (Artifact Schema 1.6.0 target).

    State only. The cause of ``UNAVAILABLE`` is carried by
    :class:`GitModeMapReason`.
    """

    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


class GitModeMapReason(str, Enum):
    """Why Git-derived evidence was unavailable.

    Exactly one member, because exactly one producing condition exists today:
    ``modules/inventory.py`` distinguishes "not a git repository" from every
    other failure and records the rest as ``failed``. Add a member here only
    when a code path actually produces it.
    """

    NOT_GIT_REPOSITORY = "not_git_repository"


# ------------------------------------------------------ subject and source ---


class SourceMode(str, Enum):
    """How the analyzed bytes were obtained.

    Four concepts are kept apart deliberately, because collapsing them is how
    ``repository_url`` became a hidden primary key:

    * **logical subject identity** — :class:`SubjectKeyBasis` / ``subject_key``;
    * **source locator** — where the bytes came from (a URL, a local path);
    * **analyzed revision/snapshot identity** — a commit SHA, or
      ``analysis_scope_hash`` when there is no commit;
    * **acquisition/source mode** — this enum.

    Source mode alone never decides comparability. Two analyses of the same
    revision, one fetched remotely and one read from a local clone, are the same
    measurement of the same bytes; refusing to compare them because the labels
    differ would be an artefact of bookkeeping, not a fact about the software.
    """

    #: A revision fetched from a remote Git origin.
    REMOTE_GIT_REVISION = "remote_git_revision"
    #: An exact committed revision materialized from a local Git repository.
    LOCAL_GIT_REVISION = "local_git_revision"
    #: The current contents of a local Git working tree, dirt included.
    LOCAL_WORKTREE_SNAPSHOT = "local_worktree_snapshot"
    #: A local directory that is not a Git repository at all.
    LOCAL_DIRECTORY_SNAPSHOT = "local_directory_snapshot"

    @property
    def is_local(self) -> bool:
        return self is not SourceMode.REMOTE_GIT_REVISION

    @property
    def is_exact_revision(self) -> bool:
        """Whether the analyzed bytes are an immutable committed revision."""
        return self in (
            SourceMode.REMOTE_GIT_REVISION,
            SourceMode.LOCAL_GIT_REVISION,
        )


class SubjectKeyBasis(str, Enum):
    """How ``subject_key`` was obtained.

    Recorded because the four bases carry genuinely different portability. A
    key derived from a remote locator means the same thing on any machine; a
    local fallback does not, and saying so is the difference between provenance
    and a guess.
    """

    #: Supplied by the user, which is how two source modes are declared to be
    #: the same logical subject.
    EXPLICIT = "explicit"
    #: Canonical remote repository identity.
    REMOTE_LOCATOR = "remote_locator"
    #: Canonical identity of a local Git repository's origin.
    GIT_ORIGIN = "git_origin"
    #: Derived locally when no portable identity exists. **Not portable.**
    LOCAL_FALLBACK = "local_fallback"

    @property
    def is_portable(self) -> bool:
        """Whether this key means the same thing on another machine."""
        return self is not SubjectKeyBasis.LOCAL_FALLBACK


class WorkingTreeState(str, Enum):
    """Whether the analyzed snapshot was an exact commit or a live worktree."""

    #: Bytes are exactly those of a committed revision.
    COMMITTED_REVISION = "committed_revision"
    #: Worktree contents matching HEAD, with no modifications.
    CLEAN_WORKTREE = "clean_worktree"
    #: Worktree with modified tracked files and/or included untracked files.
    DIRTY_WORKTREE = "dirty_worktree"
    #: No Git working tree exists.
    NOT_APPLICABLE = "not_applicable"


# ------------------------------------------------- environment capabilities ---


class CapabilityState(str, Enum):
    """Outcome of one environment/capability check.

    ``NOT_REQUIRED`` and ``NOT_EVALUATED`` are deliberately distinct from
    ``AVAILABLE``. "This run does not need the Go grammar" and "the Go grammar
    is present" are different facts, and collapsing them would make a report
    claiming an absent capability was fine indistinguishable from one that
    actually checked.
    """

    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    WARNING = "warning"
    NOT_REQUIRED = "not_required"
    NOT_EVALUATED = "not_evaluated"


class CapabilityReason(str, Enum):
    """Why a capability is not available. Kept out of the state string.

    A cause encoded inside a status — ``unavailable_grammar_missing`` — is the
    same mistake as ``git_mode_map_status = "unavailable_not_git"``: it makes
    the vocabulary unbounded and forces every consumer to parse it.
    """

    GRAMMAR_UNAVAILABLE = "grammar_unavailable"
    PARSER_INITIALIZATION_FAILED = "parser_initialization_failed"
    TREE_SITTER_CORE_UNAVAILABLE = "tree_sitter_core_unavailable"
    DEPENDENCY_MISSING = "dependency_missing"
    DEPENDENCY_VERSION_MISMATCH = "dependency_version_mismatch"
    UNSUPPORTED_RUNTIME = "unsupported_runtime"
    GIT_UNAVAILABLE = "git_unavailable"
    PATH_NOT_WRITABLE = "path_not_writable"
    LOW_FREE_SPACE = "low_free_space"
    FREE_SPACE_UNKNOWN = "free_space_unknown"
    SYMLINK_CREATION_UNAVAILABLE = "symlink_creation_unavailable"
    POLICY_FILE_MISSING = "policy_file_missing"
    NO_INDEPENDENT_INSTALLATION = "no_independent_installation"
    VERSION_DISAGREEMENT = "version_disagreement"


class InstallationKind(str, Enum):
    """How this Metrolith is present, as far as it can honestly be determined.

    ``SOURCE_TREE_ONLY`` exists because stray generated distribution metadata in
    the working tree can satisfy ``importlib.metadata.version("metrolith")``. A check
    that only compared versions therefore reported "version agreement" while
    proving nothing about any installation — it was comparing the source tree
    to itself.
    """

    SOURCE_TREE_ONLY = "source_tree_only"
    EDITABLE_INSTALL = "editable_install"
    INSTALLED_DISTRIBUTION = "installed_distribution"
    INDETERMINATE = "indeterminate"


class PreflightStage(str, Enum):
    """Which barrier produced a result.

    The two stages carry genuinely different guarantees, and conflating them
    would let a Stage B refusal be reported as "nothing was acquired".
    """

    #: Before acquisition. A refusal here means no acquisition began.
    GLOBAL = "stage_a_global"
    #: After acquisition and parser-free discovery, before any metric parsing.
    #: A refusal here means acquisition and cache activity may have occurred,
    #: but no source file was parsed and no run was published.
    CAPABILITY_BARRIER = "stage_b_capability_barrier"


#: Capability identifier for the parser of one language.
def parser_capability(language: str) -> str:
    return f"{language.lower()}_parser"


# --------------------------------------------------- Artifact Schema 1.5.0 ---

#: The compound value Artifact Schema 1.5.0 writes for "not a Git repository".
#: Frozen: it appears in shipped schemas, in existing run artifacts, and in
#: ``validation/conformance/data/expected/``.
GIT_MODE_MAP_NOT_GIT_WIRE_VALUE = "unavailable_not_git"

#: Every value a 1.5.0 producer can emit for ``git_mode_map_status``. This is
#: the vocabulary a *consumer* must recognize, and it is a superset of
#: :class:`GitModeMapStatus` because of the compound legacy value above.
KNOWN_GIT_MODE_STATES = frozenset(
    {member.value for member in GitModeMapStatus} | {GIT_MODE_MAP_NOT_GIT_WIRE_VALUE}
)

KNOWN_ANALYSIS_STATUSES = frozenset(member.value for member in AnalysisStatus)
KNOWN_METRIC_STATUSES = frozenset(member.value for member in MetricStatus)
KNOWN_PARTIAL_ORIGINS = frozenset(member.value for member in PartialOrigin)


def normalize_git_mode_map_state(
    value: str | None,
) -> tuple[GitModeMapStatus | None, GitModeMapReason | None]:
    """Map an emitted ``git_mode_map_status`` onto the normalized model.

    This is the 1.5.0 → 1.6.0 compatibility seam, and the only place the
    compound value is decomposed::

        "unavailable_not_git" -> (UNAVAILABLE, NOT_GIT_REPOSITORY)
        "available"           -> (AVAILABLE,   None)
        None                  -> (None,        None)

    An unrecognized value returns ``(None, None)`` rather than guessing. The
    caller still has the raw string and should surface it as unknown — silently
    normalizing an unrecognized status into a known one would hide exactly the
    drift this module exists to prevent.
    """
    if value is None:
        return (None, None)
    if value == GIT_MODE_MAP_NOT_GIT_WIRE_VALUE:
        return (GitModeMapStatus.UNAVAILABLE, GitModeMapReason.NOT_GIT_REPOSITORY)
    try:
        return (GitModeMapStatus(value), None)
    except ValueError:
        return (None, None)
