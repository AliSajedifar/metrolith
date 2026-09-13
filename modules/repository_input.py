"""Canonical repository input parsing and legacy TXT migration."""

from __future__ import annotations

import csv
import hashlib
import os
import re
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

from modules.config import SUPPORTED_LANGUAGES
from modules.metadata import parse_github_repo


CSV_COLUMNS = (
    "url",
    "architecture_type",
    "expected_language",
    "commit_sha",
    "enabled",
    "notes",
)
REQUIRED_COLUMNS = ("url",)
ARCHITECTURE_TYPES = {"monolith", "microservices", "unknown"}
SHA_PATTERN = re.compile(r"^[0-9a-fA-F]{7,40}$")
TRUE_VALUES = {"1", "true", "yes", "y", "on"}
FALSE_VALUES = {"0", "false", "no", "n", "off"}


class InputValidationError(ValueError):
    """Raised after canonical input has been validated as a whole."""

    def __init__(self, errors: Iterable[str]):
        self.errors = list(errors)
        super().__init__("Repository input validation failed:\n- " + "\n- ".join(self.errors))


@dataclass(frozen=True, slots=True)
class RepositorySpec:
    url: str
    architecture_type: str
    expected_language: str | None = None
    commit_sha: str | None = None
    enabled: bool = True
    notes: str = ""
    input_file: str = ""
    input_line: int = 0

    # -- local source model (Artifact 1.7) --------------------------------
    # A local subject has no remote URL to parse an owner and name out of, so
    # these carry what a GitHub URL used to imply. They are optional so every
    # existing cohort row keeps working untouched.
    local_path: str | None = None
    revision: str | None = None
    tracked_only: bool = False
    #: User-supplied logical identity. The mechanism by which a user declares
    #: that a remote revision and a local clone are the same subject.
    subject_key: str | None = None
    #: Display name for a local subject; never an identity.
    local_name: str | None = None

    @property
    def is_local(self) -> bool:
        return self.local_path is not None

    @property
    def owner(self) -> str:
        if self.is_local:
            return "local"
        return parse_github_repo(self.url)[0]

    @property
    def repository_name(self) -> str:
        if self.is_local:
            from pathlib import Path

            return self.local_name or Path(self.local_path).resolve().name
        return parse_github_repo(self.url)[1]

    def scientific_fields(self) -> tuple[object, ...]:
        return (
            self.url.lower(),
            self.architecture_type,
            self.expected_language,
            self.commit_sha,
            self.enabled,
            self.notes,
        )

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def canonicalize_github_url(value: str) -> str:
    owner, name = parse_github_repo(value.strip())
    return f"https://github.com/{owner}/{name}"


def _parse_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in TRUE_VALUES:
        return True
    if normalized in FALSE_VALUES:
        return False
    raise ValueError("enabled must be a boolean (true/false, yes/no, or 1/0)")


def _normalize_language(value: str) -> str | None:
    if not value.strip():
        return None
    by_lower = {language.lower(): language for language in SUPPORTED_LANGUAGES}
    normalized = by_lower.get(value.strip().lower())
    if normalized is None:
        raise ValueError(
            "expected_language must be blank or one of " + ", ".join(SUPPORTED_LANGUAGES)
        )
    return normalized


def _parse_row(row: dict[str, str | None], path: Path, line: int) -> RepositorySpec:
    missing_values = [name for name in REQUIRED_COLUMNS if not (row.get(name) or "").strip()]
    if missing_values:
        raise ValueError("missing required value(s): " + ", ".join(missing_values))
    url = canonicalize_github_url(row["url"] or "")
    architecture = (row.get("architecture_type") or "unknown").strip().lower() or "unknown"
    if architecture not in ARCHITECTURE_TYPES:
        raise ValueError(
            "architecture_type must be monolith, microservices, or unknown"
        )
    language = _normalize_language(row.get("expected_language") or "")
    commit = (row.get("commit_sha") or "").strip().lower() or None
    if commit and not SHA_PATTERN.fullmatch(commit):
        raise ValueError("commit_sha must contain 7 to 40 hexadecimal characters")
    enabled_text = row.get("enabled") or ""
    enabled = True if not enabled_text.strip() else _parse_bool(enabled_text)
    return RepositorySpec(
        url=url,
        architecture_type=architecture,
        expected_language=language,
        commit_sha=commit,
        enabled=enabled,
        notes=(row.get("notes") or "").strip(),
        input_file=str(path),
        input_line=line,
    )


def parse_repository_rows(path: str | Path) -> list[RepositorySpec]:
    """Parse and validate every row, **without** deduplicating or filtering.

    This is the complete accepted population in source order. It exists because
    ``load_repositories_csv`` collapses identical duplicates into a count and
    drops disabled rows, which is right for driving a run but destroys the
    evidence ``normalized_input.csv`` has to record (plan section 11.1).

    Row-level validation still applies, so a malformed row fails here exactly as
    it would through the execution path. Conflicting duplicates are *not*
    detected here; that check belongs to :func:`load_repositories_csv`, which is
    what actually starts a run.
    """
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"Repository input does not exist: {path}")
    errors: list[str] = []
    parsed: list[RepositorySpec] = []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            _validate_headers(tuple(reader.fieldnames or ()))
            for line, row in enumerate(reader, start=2):
                try:
                    parsed.append(_parse_row(row, path, line))
                except ValueError as exc:
                    errors.append(f"{path}:{line}: {exc}")
    except csv.Error as exc:
        errors.append(f"{path}: malformed CSV: {exc}")
    if errors:
        raise InputValidationError(errors)
    return parsed


def _validate_headers(headers: tuple[str, ...]) -> None:
    unknown_columns = sorted(str(name) for name in headers if name not in CSV_COLUMNS)
    duplicate_columns = sorted({name for name in headers if headers.count(name) > 1})
    header_errors = []
    if unknown_columns:
        header_errors.append(
            "unknown column(s): "
            + ", ".join(unknown_columns)
            + "; expected headers: "
            + ", ".join(CSV_COLUMNS)
        )
    if duplicate_columns:
        header_errors.append("duplicate column(s): " + ", ".join(duplicate_columns))
    missing_columns = [name for name in REQUIRED_COLUMNS if name not in headers]
    if missing_columns:
        header_errors.append("missing required column(s): " + ", ".join(missing_columns))
    if header_errors:
        raise InputValidationError(header_errors)


def load_repositories_csv(
    path: str | Path,
    include_disabled: bool = False,
    *,
    diagnostics: dict[str, int] | None = None,
) -> list[RepositorySpec]:
    """Validate, normalize, deduplicate, and return canonical input rows."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"Repository input does not exist: {path}")
    errors: list[str] = []
    parsed: list[RepositorySpec] = []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            headers = tuple(reader.fieldnames or ())
            unknown_columns = sorted(
                str(name) for name in headers if name not in CSV_COLUMNS
            )
            duplicate_columns = sorted(
                {name for name in headers if headers.count(name) > 1}
            )
            header_errors = []
            if unknown_columns:
                header_errors.append(
                    "unknown column(s): "
                    + ", ".join(unknown_columns)
                    + "; expected headers: "
                    + ", ".join(CSV_COLUMNS)
                )
            if duplicate_columns:
                header_errors.append(
                    "duplicate column(s): " + ", ".join(duplicate_columns)
                )
            missing_columns = [name for name in REQUIRED_COLUMNS if name not in headers]
            if missing_columns:
                header_errors.append(
                    "missing required column(s): " + ", ".join(missing_columns)
                )
            if header_errors:
                raise InputValidationError(header_errors)
            for line, row in enumerate(reader, start=2):
                try:
                    parsed.append(_parse_row(row, path, line))
                except ValueError as exc:
                    errors.append(f"{path}:{line}: {exc}")
    except csv.Error as exc:
        errors.append(f"{path}: malformed CSV: {exc}")
    if errors:
        raise InputValidationError(errors)

    unique: list[RepositorySpec] = []
    by_url: dict[str, RepositorySpec] = {}
    conflicts: list[str] = []
    duplicate_rows_dropped = 0
    for spec in parsed:
        key = spec.url.lower()
        previous = by_url.get(key)
        if previous is None:
            by_url[key] = spec
            unique.append(spec)
        elif previous.scientific_fields() != spec.scientific_fields():
            conflicts.append(
                f"conflicting duplicate {spec.url} at {previous.input_file}:{previous.input_line} "
                f"and {spec.input_file}:{spec.input_line}"
            )
        else:
            duplicate_rows_dropped += 1
    if conflicts:
        raise InputValidationError(conflicts)
    if diagnostics is not None:
        diagnostics["duplicate_rows_dropped"] = duplicate_rows_dropped
    return [spec for spec in unique if include_disabled or spec.enabled]


def _infer_legacy_labels(path: Path) -> tuple[str, str | None]:
    lowered = path.name.lower()
    if "monolith" in lowered:
        architecture = "monolith"
    elif "microservice" in lowered:
        architecture = "microservices"
    else:
        raise InputValidationError([f"cannot infer architecture label from legacy file {path}"])
    language = next(
        (candidate for candidate in SUPPORTED_LANGUAGES if candidate.lower() in lowered),
        None,
    )
    if language is None and re.search(r"(?:^|[^a-z])go(?:[^a-z]|$)|golang", lowered):
        language = "Go"
    return architecture, language


def load_legacy_txt(
    paths: Iterable[str | Path],
    *,
    diagnostics: dict[str, int] | None = None,
) -> list[RepositorySpec]:
    """Load deprecated filename-labeled TXT lists with conflict detection."""
    specs: list[RepositorySpec] = []
    errors: list[str] = []
    for raw_path in paths:
        path = Path(raw_path)
        if not path.is_file():
            errors.append(f"missing legacy repository list: {path}")
            continue
        try:
            architecture, language = _infer_legacy_labels(path)
        except InputValidationError as exc:
            errors.extend(exc.errors)
            continue
        for line, raw in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
            value = raw.strip()
            if not value or value.startswith("#"):
                continue
            try:
                url = canonicalize_github_url(value)
            except ValueError as exc:
                errors.append(f"{path}:{line}: {exc}: {value}")
                continue
            specs.append(
                RepositorySpec(
                    url=url,
                    architecture_type=architecture,
                    expected_language=language,
                    enabled=True,
                    input_file=str(path),
                    input_line=line,
                )
            )
    if errors:
        raise InputValidationError(errors)
    by_url: dict[str, RepositorySpec] = {}
    unique: list[RepositorySpec] = []
    conflicts: list[str] = []
    duplicate_rows_dropped = 0
    for spec in specs:
        previous = by_url.get(spec.url.lower())
        if previous is None:
            by_url[spec.url.lower()] = spec
            unique.append(spec)
        elif previous.scientific_fields() != spec.scientific_fields():
            conflicts.append(
                f"conflicting legacy duplicate {spec.url}: "
                f"{previous.architecture_type} vs {spec.architecture_type}"
            )
        else:
            duplicate_rows_dropped += 1
    if conflicts:
        raise InputValidationError(conflicts)
    if diagnostics is not None:
        diagnostics["duplicate_rows_dropped"] = duplicate_rows_dropped
    return unique


def write_repositories_csv(specs: Iterable[RepositorySpec], output_path: str | Path) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{output_path.name}.", suffix=".tmp", dir=output_path.parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS, lineterminator="\n")
            writer.writeheader()
            for spec in specs:
                writer.writerow(
                    {
                        "url": spec.url,
                        "architecture_type": spec.architecture_type,
                        "expected_language": spec.expected_language or "",
                        "commit_sha": spec.commit_sha or "",
                        "enabled": "true" if spec.enabled else "false",
                        "notes": spec.notes,
                    }
                )
        os.replace(temporary_name, output_path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)
    return output_path


def migrate_legacy_inputs(paths: Iterable[str | Path], output_path: str | Path) -> Path:
    return write_repositories_csv(load_legacy_txt(paths), output_path)


def input_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
