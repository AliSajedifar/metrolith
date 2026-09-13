"""Public conformance corpus for ArchLens (plan section 9).

Each case is a hand-reviewed oracle: expected inclusion, raw metric components,
derived metrics, and statuses are derived from the Metric Contract by a human
reviewer and recorded alongside the clause they come from. **Current ArchLens
output is not the oracle** (plan section 9.3) — a case that merely records what
the program happens to emit today would pass forever and prove nothing.

Cases run offline with no network and no repository acquisition, and the corpus
loads through :mod:`importlib.resources` so ``python -m validation.conformance
run`` works identically from an installed wheel.
"""

from __future__ import annotations

from .runner import CaseOutcome, CaseResult, load_manifest, run_cases

__all__ = ["CaseOutcome", "CaseResult", "load_manifest", "run_cases"]
