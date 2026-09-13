"""Layer 2 — selected real repositories, one materialization per subject.

Subjects come from the existing benchmark cache rather than from toy
repositories invented for the study, because the point of Layer 2 is exactly
what Layer 1 cannot show: real directory layouts, real vendored and generated
code, real test conventions, real multi-language trees.

Selection is **intentional**, not "whatever was handy". For each language family
the set spans repository size and source-selection conditions — a small
single-purpose project and a larger one with more structure — so an adapter that
only works on tidy trees is exposed.

Materialization uses ``git archive`` from the bare cache into a temporary
directory: offline, no network, and the user's cache is never modified.
"""

from __future__ import annotations

import io
import os
import subprocess
import tarfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterator

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CACHE_ROOT = REPOSITORY_ROOT / ".archlens" / "cache" / "git"


@lru_cache(maxsize=1)
def _cache_index() -> dict[str, Path]:
    """Map every cached clone's origin URL to its directory."""
    index: dict[str, Path] = {}
    if not CACHE_ROOT.is_dir():
        return index
    for candidate in sorted(CACHE_ROOT.iterdir()):
        if not candidate.is_dir():
            continue
        completed = subprocess.run(
            ["git", "-C", str(candidate), "config", "--get", "remote.origin.url"],
            capture_output=True, text=True, timeout=120,
        )
        url = completed.stdout.strip()
        if completed.returncode == 0 and url:
            index[url.rstrip("/").casefold()] = candidate
    return index


@dataclass(frozen=True)
class Layer2Subject:
    """One selected real repository, with the reason it was selected."""

    subject_key: str
    url: str
    language: str
    rationale: str

    @property
    def cache_directory(self) -> Path | None:
        """The bare cache clone holding this URL.

        Resolved by reading each cache entry's own `remote.origin.url` rather
        than by recomputing ArchLens's cache-key hash. Reproducing that hash
        here would couple the study to a private implementation detail and
        would silently miss entries whenever the derivation changed — which is
        exactly what happened on the first attempt, for the mixed-case URLs.
        """
        return _cache_index().get(self.url.rstrip("/").casefold())


#: The selected corpus. Two subjects per family wherever the cache allows, with
#: a deliberate size and structure contrast inside each family.
SUBJECTS: tuple[Layer2Subject, ...] = (
    Layer2Subject(
        "layer2-python-ralph", "https://github.com/allegro/ralph", "Python",
        "large Django application: deep package tree, migrations, static assets, "
        "many test modules",
    ),
    Layer2Subject(
        "layer2-python-spoolman", "https://github.com/Donkie/Spoolman", "Python",
        "small mixed-language service: Python backend beside a JS/TS client, so "
        "language detection has to discriminate",
    ),
    Layer2Subject(
        "layer2-java-roller", "https://github.com/apache/roller", "Java",
        "large classic Maven Java project: nested modules, generated sources, "
        "webapp resources",
    ),
    Layer2Subject(
        "layer2-java-demo", "https://github.com/7ep/demo", "Java",
        "small Java project with an unusual layout, contrasting with Roller",
    ),
    Layer2Subject(
        "layer2-javascript-monolith",
        "https://github.com/googlecodelabs/monolith-to-microservices", "JavaScript",
        "small multi-service JavaScript repository with per-service directories "
        "and vendored client bundles",
    ),
    Layer2Subject(
        "layer2-javascript-obojobo", "https://github.com/ucfopen/Obojobo",
        "JavaScript",
        "larger JavaScript monorepo: many packages, heavy test conventions",
    ),
    Layer2Subject(
        "layer2-typescript-magda", "https://github.com/magda-io/magda", "TypeScript",
        "large TypeScript monorepo: many packages, generated API clients, "
        "declaration files",
    ),
    Layer2Subject(
        "layer2-typescript-securo", "https://github.com/securo-finance/securo",
        "TypeScript",
        "smaller TypeScript project, contrasting with the Magda monorepo",
    ),
    Layer2Subject(
        "layer2-go-cgrates", "https://github.com/cgrates/cgrates", "Go",
        "large Go codebase with extensive `_test.go` files and generated code",
    ),
    Layer2Subject(
        "layer2-go-shop",
        "https://github.com/ThreeDotsLabs/monolith-microservice-shop", "Go",
        "small idiomatic Go service, contrasting with cgrates",
    ),
)


class SubjectUnavailable(RuntimeError):
    """The cached repository is missing. Reported, never silently skipped."""


@lru_cache(maxsize=1)
def _pinned_revisions() -> dict[str, tuple[str, str]]:
    """URL -> (commit sha, where it was recorded).

    The benchmark's own frozen inputs are the source. The cached clones hold
    objects but **no refs** — ArchLens fetches specific commits into them — so
    `HEAD` is not resolvable there, and inventing a revision would make the
    study measure something the benchmark never pinned.
    """
    import csv
    import glob

    found: dict[str, tuple[str, str]] = {}
    sources = sorted(glob.glob(str(REPOSITORY_ROOT / "input" / "*.csv"))) + sorted(
        glob.glob(
            str(REPOSITORY_ROOT / "validation" / "**" / "repositories_frozen.csv"),
            recursive=True,
        )
    )
    for path in sources:
        try:
            with open(path, encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
        except (OSError, csv.Error, UnicodeDecodeError):
            continue
        for row in rows:
            url = (row.get("url") or row.get("repository_url") or "").strip()
            sha = (
                row.get("commit_sha") or row.get("analyzed_commit_sha") or ""
            ).strip()
            if not url or len(sha) != 40:
                continue
            found.setdefault(url.rstrip("/").casefold(), (sha, path))
    return found


#: Clones the study acquired itself, when the benchmark cache lacked the pinned
#: revision. Kept separate from ArchLens's cache, which is never written to.
ACQUIRED_ROOT = Path(
    os.environ.get("ARCHLENS_DIFFVAL_REFS", ".metrolith-reference")
) / "acquired"

#: Full acquisition provenance per subject, emitted into the study document.
_ACQUISITION_PROVENANCE: dict[str, dict[str, Any]] = {}


def acquisition_provenance() -> dict[str, dict[str, Any]]:
    return dict(_ACQUISITION_PROVENANCE)


def _has_revision(cache: Path, revision: str) -> bool:
    completed = subprocess.run(
        ["git", "-C", str(cache), "cat-file", "-e", f"{revision}^{{commit}}"],
        capture_output=True, text=True, timeout=300,
    )
    return completed.returncode == 0


def _acquired_clone(subject: Layer2Subject) -> Path:
    slug = subject.url.rstrip("/").rsplit("/", 1)[-1].replace(".git", "")
    return ACQUIRED_ROOT / f"{slug}.git"


def resolve_revision(subject: Layer2Subject, *, allow_acquisition: bool = True) -> str:
    """The exact pinned revision this subject is measured at.

    Never substitutes. HEAD, the default branch, the latest release and "some
    nearby commit" are all wrong answers: the benchmark pinned a specific
    revision, and a validation record against a different one would not be
    evidence about the pinned subject.

    Resolution order, with the outcome recorded either way:

    1. the benchmark cache, if it already holds the pinned commit;
    2. a clone the study acquired itself, from the canonical remote, for that
       exact commit. ArchLens's own cache is never written to.

    If neither yields the exact commit, :class:`SubjectUnavailable` is raised
    and the caller records the subject as
    ``not_executed_missing_pinned_revision``.
    """
    entry = _pinned_revisions().get(subject.url.rstrip("/").casefold())
    if entry is None:
        raise SubjectUnavailable(
            f"{subject.subject_key}: no pinned revision recorded for "
            f"{subject.url} in any benchmark input"
        )
    revision, recorded_in = entry

    provenance: dict[str, Any] = {
        "requested_revision": revision,
        "obtained_revision": None,
        "acquisition_source": None,
        "network_required": False,
        "cache_identity": None,
        "revision_recorded_in": recorded_in,
    }

    cache = subject.cache_directory
    if cache is not None and _has_revision(cache, revision):
        provenance.update({
            "obtained_revision": revision,
            "acquisition_source": "existing_benchmark_cache",
            "network_required": False,
            "cache_identity": str(cache),
        })
        _ACQUISITION_PROVENANCE[subject.subject_key] = provenance
        return revision

    acquired = _acquired_clone(subject)
    if acquired.is_dir() and _has_revision(acquired, revision):
        provenance.update({
            "obtained_revision": revision,
            "acquisition_source": "study_acquired_clone",
            "network_required": True,
            "cache_identity": str(acquired),
        })
        _ACQUISITION_PROVENANCE[subject.subject_key] = provenance
        return revision

    if not allow_acquisition:
        _ACQUISITION_PROVENANCE[subject.subject_key] = provenance
        raise SubjectUnavailable(
            f"{subject.subject_key}: pinned revision {revision[:12]} is absent "
            f"and acquisition is disabled"
        )

    # Fetch exactly the pinned commit from the canonical remote. Depth 1: the
    # study needs that tree, not the project's history.
    acquired.parent.mkdir(parents=True, exist_ok=True)
    if not acquired.is_dir():
        subprocess.run(
            ["git", "init", "--bare", "-q", str(acquired)],
            capture_output=True, text=True, timeout=300,
        )
        subprocess.run(
            ["git", "-C", str(acquired), "remote", "add", "origin", subject.url],
            capture_output=True, text=True, timeout=300,
        )
    fetch = subprocess.run(
        ["git", "-C", str(acquired), "fetch", "--depth", "1", "origin", revision],
        capture_output=True, text=True, timeout=1800,
    )
    provenance["network_required"] = True
    if fetch.returncode != 0 or not _has_revision(acquired, revision):
        _ACQUISITION_PROVENANCE[subject.subject_key] = provenance
        raise SubjectUnavailable(
            f"{subject.subject_key}: pinned revision {revision[:12]} could not "
            f"be obtained from {subject.url}: "
            f"{fetch.stderr.strip()[:200] or 'commit not present after fetch'}"
        )

    provenance.update({
        "obtained_revision": revision,
        "acquisition_source": "canonical_remote_fetch",
        "cache_identity": str(acquired),
    })
    _ACQUISITION_PROVENANCE[subject.subject_key] = provenance
    return revision


def revision_cache(subject: Layer2Subject, revision: str) -> Path:
    """Whichever clone actually holds the pinned commit."""
    cache = subject.cache_directory
    if cache is not None and _has_revision(cache, revision):
        return cache
    acquired = _acquired_clone(subject)
    if acquired.is_dir() and _has_revision(acquired, revision):
        return acquired
    raise SubjectUnavailable(
        f"{subject.subject_key}: no clone holds pinned revision {revision[:12]}"
    )


def revision_provenance(subject: Layer2Subject) -> str | None:
    entry = _pinned_revisions().get(subject.url.rstrip("/").casefold())
    return entry[1] if entry else None


def materialize(subject: Layer2Subject, destination: Path, revision: str) -> Path:
    """Extract one revision from the bare cache. Offline; cache is read-only."""
    cache = revision_cache(subject, revision)
    destination.mkdir(parents=True, exist_ok=True)
    archive = subprocess.run(
        ["git", "-C", str(cache), "archive", "--format=tar", revision],
        capture_output=True, timeout=1800,
    )
    if archive.returncode != 0:
        raise SubjectUnavailable(
            f"{subject.subject_key}: git archive failed: "
            f"{archive.stderr.decode('utf-8', 'ignore')[:200]}"
        )

    # Extracted with Python's tarfile rather than by shelling to `tar`. The
    # external tool refuses some perfectly ordinary paths on Windows once the
    # temporary root pushes them past the legacy length limit, and it aborts the
    # whole archive when it does. Extracting here also lets link entries be
    # skipped deliberately: ArchLens excludes symlinks from measurement anyway,
    # and the count of what was skipped is returned rather than hidden.
    skipped: list[str] = []
    with tarfile.open(fileobj=io.BytesIO(archive.stdout), mode="r|") as bundle:
        for member in bundle:
            if member.issym() or member.islnk():
                skipped.append(member.name)
                continue
            if not member.isfile() and not member.isdir():
                skipped.append(member.name)
                continue
            target = (destination / member.name).resolve()
            if not str(target).startswith(str(destination.resolve())):
                # A path escaping the destination is never extracted.
                skipped.append(member.name)
                continue
            try:
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                extracted = bundle.extractfile(member)
                if extracted is None:
                    skipped.append(member.name)
                    continue
                with open(target, "wb") as handle:
                    handle.write(extracted.read())
            except OSError as exc:
                skipped.append(f"{member.name} ({type(exc).__name__})")

    _MATERIALIZATION_NOTES[subject.subject_key] = {
        "revision": revision,
        "entries_skipped": len(skipped),
        "skipped_examples": sorted(skipped)[:10],
    }
    return destination


#: Recorded per subject so a materialization that dropped entries is visible.
_MATERIALIZATION_NOTES: dict[str, dict[str, Any]] = {}


def materialization_notes() -> dict[str, dict[str, Any]]:
    return dict(_MATERIALIZATION_NOTES)


def available_subjects() -> Iterator[Layer2Subject]:
    """Every subject with a pinned revision, cached or acquirable.

    Availability of a *clone* is no longer the filter: a subject whose
    pinned commit the study can fetch is still in scope, and one that
    cannot be obtained is recorded by the caller rather than skipped here.
    """
    for subject in SUBJECTS:
        if subject.url.rstrip('/').casefold() in _pinned_revisions():
            yield subject


def selection_manifest() -> dict[str, Any]:
    """What was selected, why, and what was not available."""
    selected = []
    missing = []
    for subject in SUBJECTS:
        entry = {
            "subject_key": subject.subject_key,
            "url": subject.url,
            "language": subject.language,
            "selection_rationale": subject.rationale,
        }
        cache = subject.cache_directory
        if cache is not None and cache.is_dir():
            entry["cache_directory"] = str(cache)
            pinned = _pinned_revisions().get(subject.url.rstrip("/").casefold())
            entry["pinned_revision"] = pinned[0] if pinned else None
            entry["revision_recorded_in"] = pinned[1] if pinned else None
            selected.append(entry)
        else:
            missing.append({**entry, "reason": "no cached clone in this tree"})
    return {
        "selection_policy": (
            "Intentional selection from the existing benchmark cache, spanning "
            "repository size and source-selection conditions within each "
            "language family. Not toy repositories, and not an arbitrary sample."
        ),
        "selected": selected,
        "unavailable": missing,
    }
