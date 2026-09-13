"""Validated D1/D2/D3 duplication engine primitives.

The standalone D5 product contract lives in :mod:`modules.duplication.output`
and is deliberately not re-exported here, keeping primitive consumers free of
snapshot and presentation dependencies.
"""

from modules.duplication.candidates import extract_candidates, extract_file_candidates
from modules.duplication.model import (
    Candidate,
    CandidateAdmissionStatus,
    CandidateExtractionResult,
    CandidateExtractionStatus,
    CandidateInvariantError,
    CloneDistribution,
    LexicalCloneGroup,
    LexicalOccurrence,
    SourceLineInterval,
    SourceSpan,
    StructuralCloneGroup,
    StructuralDominance,
    StructuralGroupingResult,
    StructuralOccurrence,
    UnitKind,
    validate_candidate_invariants,
)
from modules.duplication.grouping import GroupingInvariantError, group_lexical_clones
from modules.duplication.lexical import (
    LEXICAL_CANONICAL_MAGIC,
    LEXICAL_FINGERPRINT_VERSION,
    LexicalCanonicalizationError,
    canonical_lexical_bytes,
    lexical_fingerprint,
    make_lexical_occurrence,
)
from modules.duplication.structural import (
    STRUCTURAL_CANONICAL_MAGIC,
    STRUCTURAL_FINGERPRINT_VERSION,
    StructuralCanonicalizationResult,
    StructuralCanonicalizationStatus,
    StructuralCanonicalizationUnavailable,
    StructuralUnavailableReason,
    canonical_structural_bytes,
    canonicalize_structural,
    structural_fingerprint,
)
from modules.duplication.structural_grouping import (
    StructuralGroupingInvariantError,
    group_structural_clones,
    make_structural_occurrence,
)

__all__ = [
    "Candidate",
    "CandidateAdmissionStatus",
    "CandidateExtractionResult",
    "CandidateExtractionStatus",
    "CandidateInvariantError",
    "CloneDistribution",
    "GroupingInvariantError",
    "LEXICAL_CANONICAL_MAGIC",
    "LEXICAL_FINGERPRINT_VERSION",
    "LexicalCanonicalizationError",
    "LexicalCloneGroup",
    "LexicalOccurrence",
    "SourceSpan",
    "SourceLineInterval",
    "STRUCTURAL_CANONICAL_MAGIC",
    "STRUCTURAL_FINGERPRINT_VERSION",
    "StructuralCanonicalizationResult",
    "StructuralCanonicalizationStatus",
    "StructuralCanonicalizationUnavailable",
    "StructuralCloneGroup",
    "StructuralDominance",
    "StructuralGroupingInvariantError",
    "StructuralGroupingResult",
    "StructuralOccurrence",
    "StructuralUnavailableReason",
    "UnitKind",
    "extract_candidates",
    "extract_file_candidates",
    "canonical_lexical_bytes",
    "canonical_structural_bytes",
    "canonicalize_structural",
    "group_lexical_clones",
    "group_structural_clones",
    "lexical_fingerprint",
    "make_lexical_occurrence",
    "make_structural_occurrence",
    "structural_fingerprint",
    "validate_candidate_invariants",
]
