"""Versioned, reproducible Metrolith configuration."""

from __future__ import annotations

import json
import hashlib
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping


PROJECT_ROOT = Path(__file__).resolve().parent.parent
POLICY_PATH = PROJECT_ROOT / "config" / "exclusions.v1.json"

METRIC_CONTRACT_VERSION = "3.0.0"
# Artifact Schema 1.7.0 introduces the subject identity and source model:
# `subject_key` is the logical join key, `repository_url` becomes a nullable
# locator, and `analysis_scope_hash` identifies the measured source scope.
# Inventory Schema 1.7.0 is unchanged from the 1.6 pairing.
INVENTORY_SCHEMA_VERSION = "1.7.0"
# Artifact Schema 1.9.0 adds per-callable complexity: a callables artifact
# family, per-file structural/nloc status and callable_count on the contribution
# ledger, and a repository complexity summary.
# Artifact Schema 1.11.0 adds benchmark qualification as a separate authority
# in the same immutable run bundle: a conditionally mandatory
# `benchmark_qualification.json`, its manifest hash binding, qualification
# columns on the three checked row contracts, and a run-level
# benchmark-of-record readiness verdict.
# Artifact Schema 1.12.0 adds honest evaluator provenance for installed wheels.
#
# This is a RUN-BUNDLE FORMAT boundary, not a metric-definition change. Nothing
# below moves: Metric Contract stays 3.0.0, Complexity Contract stays 2.0.0,
# Inventory Schema stays 1.7.0, Exclusion Policy stays 1.5.0, and `analysis.json`
# keeps its unchanged 1.10 measurement schema. Qualification reads measurement
# statuses and identities; it writes nothing back.
# Installed wheels do
# not live in a Git worktree, so Git dirty state is nullable and accompanied by
# an explicit provenance kind/state plus a digest of installed evaluator bytes.
# No metric, inventory, complexity, exclusion, or qualification semantics move.
ARTIFACT_SCHEMA_VERSION = "1.12.0"
# Complexity Contract is versioned separately from Metric Contract, which stays
# 3.0.0. `metric_contract_version` is a BLOCKING comparability dimension in
# `metrolith diff`; folding complexity into it would refuse every comparison
# against every run ever produced, for a metric family the four benchmark
# metrics do not depend on.
COMPLEXITY_CONTRACT_VERSION = "2.0.0"
PROGRAM_VERSION = "4.0.0"

# The fixed profile every R0 qualification decision is made under. It asks
# whether the selected supported scope adequately represents first-party
# application code across the repository's relevant services/modules at the
# exact analyzed revision. It does NOT claim automatic architecture
# understanding: counts, bytes and structure are evidence, and a non-UNRESOLVED
# verdict always carries accepted human or forensic-audit provenance.
QUALIFICATION_PROFILE = "repository_application_code_v1"
SUPPORTED_PYTHON_MINOR = (3, 13)

# Passed on every Git invocation that can observe or materialize repository
# bytes. Repository .gitattributes still takes precedence over these defaults.
GIT_CHECKOUT_CONFIGURATION = {
    "core.longpaths": "true",
    "core.autocrlf": "false",
    "core.eol": "lf",
    "core.safecrlf": "false",
}


def git_command_prefix() -> list[str]:
    command = ["git"]
    for key, value in GIT_CHECKOUT_CONFIGURATION.items():
        command.extend(("-c", f"{key}={value}"))
    return command


def supported_python_version(version_info: object | None = None) -> bool:
    value = version_info if version_info is not None else __import__("sys").version_info
    return tuple(value[:2]) == SUPPORTED_PYTHON_MINOR

JAVASCRIPT_FAMILY_EXTENSIONS = (
    ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts"
)

SUPPORTED_EXTENSIONS = {
    ".java": "Java",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".mjs": "JavaScript",
    ".cjs": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".mts": "TypeScript",
    ".cts": "TypeScript",
    ".py": "Python",
    ".go": "Go",
}
SUPPORTED_LANGUAGES = ("Java", "JavaScript", "TypeScript", "Python", "Go")


_ENVIRONMENT_WARNINGS_EMITTED: set[tuple[str, ...]] = set()


def resolve_environment_value(
    canonical_name: str,
    deprecated_name: str | None = None,
    older_name: str | None = None,
    default: str | Path | None = None,
    *,
    environment: Mapping[str, str] | None = None,
) -> str | Path | None:
    """Resolve one Metrolith setting across its bounded compatibility names.

    Empty values are absent. Conflicting non-empty values fail closed. A
    deprecated name is reported at most once per exact name set, and values are
    never included in the warning.
    """

    env = os.environ if environment is None else environment
    names = tuple(
        name for name in (canonical_name, deprecated_name, older_name) if name
    )
    supplied = {
        name: value
        for name in names
        if (value := env.get(name)) is not None and value != ""
    }
    distinct = set(supplied.values())
    if len(distinct) > 1:
        raise ValueError(
            "conflicting environment variables: " + ", ".join(supplied)
        )
    deprecated_supplied = tuple(
        name for name in (deprecated_name, older_name) if name in supplied
    )
    if deprecated_supplied and names not in _ENVIRONMENT_WARNINGS_EMITTED:
        print(
            "WARNING: deprecated environment variable(s) "
            + ", ".join(deprecated_supplied)
            + f" supplied; use {canonical_name}.",
            file=sys.stderr,
            flush=True,
        )
        _ENVIRONMENT_WARNINGS_EMITTED.add(names)
    if canonical_name in supplied:
        return supplied[canonical_name]
    if deprecated_name in supplied:
        return supplied[deprecated_name]
    if older_name in supplied:
        return supplied[older_name]
    return default


def _env_int(
    canonical_name: str,
    deprecated_name: str,
    older_name: str,
    default: int,
    *,
    environment: Mapping[str, str] | None = None,
) -> int:
    value = resolve_environment_value(
        canonical_name,
        deprecated_name,
        older_name,
        environment=environment,
        default=default,
    )
    if value is None:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{canonical_name} must be an integer") from exc


def resolve_legacy_workspace_state(
    canonical: str | Path,
    legacy: str | Path,
    *,
    environment: Mapping[str, str] | None = None,
) -> Path | None:
    """Select existing state for a read-only compatibility operation.

    New writes never call this boundary. An explicit caller may read the
    canonical state, or the legacy state only when canonical state is absent.
    Existing state in both locations is ambiguous and therefore refused.
    """

    canonical_path = Path(canonical).expanduser().resolve()
    legacy_path = Path(legacy).expanduser().resolve()
    canonical_exists = canonical_path.exists()
    legacy_exists = legacy_path.exists()
    if canonical_exists and legacy_exists:
        raise ValueError(
            "both canonical .metrolith and legacy .archlens state exist; "
            "select a path explicitly"
        )
    if canonical_exists:
        return canonical_path
    if legacy_exists:
        resolve_environment_value(
            "METROLITH_WORKSPACE_STATE",
            "ARCHLENS_WORKSPACE_STATE",
            default=str(legacy_path),
            environment={"ARCHLENS_WORKSPACE_STATE": str(legacy_path)},
        )
        return legacy_path
    return None


def resolve_workspace_paths(
    *,
    workspace: str | Path | None = None,
    output_root: str | Path | None = None,
    cache_root: str | Path | None = None,
    temporary_directory: str | Path | None = None,
    cwd: str | Path | None = None,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Path]:
    """Resolve professional workspace paths with one explicit precedence rule."""
    env = os.environ if environment is None else environment
    invocation_cwd = Path(cwd or Path.cwd()).expanduser().resolve()
    base_value = workspace or resolve_environment_value(
        "METROLITH_HOME",
        "ARCHLENS_HOME",
        default=invocation_cwd,
        environment=env,
    )
    workspace_root = Path(base_value).expanduser().resolve()
    return {
        "workspace_root": workspace_root,
        "cache_root": Path(
            cache_root if cache_root is not None else workspace_root / ".metrolith/cache/git"
        ).expanduser().resolve(),
        "temporary_directory": Path(
            temporary_directory
            if temporary_directory is not None
            else workspace_root / ".metrolith/worktrees"
        ).expanduser().resolve(),
        "output_root": Path(
            output_root if output_root is not None else workspace_root / "metrolith-output"
        ).expanduser().resolve(),
    }


def load_exclusion_policy(path: Path = POLICY_PATH) -> dict[str, Any]:
    """Load and minimally validate the checked-in exclusion policy."""
    try:
        policy = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Cannot load exclusion policy {path}: {exc}") from exc
    required = {
        "version",
        "excluded_directories",
        "dependency_directories",
        "build_output_directories",
        "cache_directories",
        "test_directories",
        "generated_suffixes",
        "minified_or_bundle_suffixes",
    }
    missing = sorted(required - policy.keys())
    if missing:
        raise RuntimeError(f"Exclusion policy is missing keys: {', '.join(missing)}")
    return policy


@dataclass(slots=True)
class AnalysisConfig:
    """Effective run configuration; every field is written to the manifest."""

    workspace_root: Path = field(default_factory=lambda: Path.cwd().resolve())
    output_root: Path = field(default_factory=lambda: (Path.cwd() / "metrolith-output").resolve())
    cache_root: Path = field(default_factory=lambda: (Path.cwd() / ".metrolith/cache/git").resolve())
    temporary_directory: Path | None = field(
        default_factory=lambda: (Path.cwd() / ".metrolith/worktrees").resolve()
    )
    workers: int = 1
    log_level: str = "INFO"
    git_timeout_seconds: int = 900
    git_retries: int = 3
    acquisition_deadline_seconds: int = 1800
    download_timeout_seconds: int = 120
    repository_warning_threshold_seconds: int = 1800
    auxiliary_warning_seconds: int = 120
    heartbeat_interval_seconds: int = 60
    full_inventory: bool = False
    require_clean_profiler: bool = False
    max_source_file_size_bytes: int = 5 * 1024 * 1024
    max_archive_files: int = 200_000
    max_archive_extracted_bytes: int = 4 * 1024 * 1024 * 1024
    metric_contract_version: str = METRIC_CONTRACT_VERSION
    inventory_schema_version: str = INVENTORY_SCHEMA_VERSION
    artifact_schema_version: str = ARTIFACT_SCHEMA_VERSION
    program_version: str = PROGRAM_VERSION
    policy_path: Path = POLICY_PATH
    exclusion_policy: dict[str, Any] = field(default_factory=load_exclusion_policy)

    @classmethod
    def from_env(cls, **overrides: Any) -> "AnalysisConfig":
        environment = overrides.pop("environment", None)
        env = os.environ if environment is None else environment
        workspace = overrides.pop("workspace", None)
        cwd = overrides.pop("cwd", None)
        explicit_output = overrides.pop("output_root", None)
        explicit_cache = overrides.pop("cache_root", None)
        explicit_temp = overrides.pop("temporary_directory", None)
        paths = resolve_workspace_paths(
            workspace=workspace,
            output_root=(
                explicit_output
                if explicit_output is not None
                else resolve_environment_value(
                    "METROLITH_OUTPUT_ROOT", "ARCHLENS_OUTPUT_ROOT",
                    "ARCH_BENCH_OUTPUT_ROOT", environment=env,
                )
            ),
            cache_root=(
                explicit_cache
                if explicit_cache is not None
                else resolve_environment_value(
                    "METROLITH_CACHE_ROOT", "ARCHLENS_CACHE_ROOT",
                    "ARCH_BENCH_CACHE_ROOT", environment=env,
                )
            ),
            temporary_directory=(
                explicit_temp
                if explicit_temp is not None
                else resolve_environment_value(
                    "METROLITH_TEMP_DIR", "ARCHLENS_TEMP_DIR",
                    "ARCH_BENCH_TEMP_DIR", environment=env,
                )
            ),
            cwd=cwd,
            environment=env,
        )
        values: dict[str, Any] = {
            **paths,
            "workers": _env_int(
                "METROLITH_WORKERS", "ARCHLENS_WORKERS", "ARCH_BENCH_WORKERS", 1,
                environment=env,
            ),
            "log_level": str(
                resolve_environment_value(
                    "METROLITH_LOG_LEVEL", "ARCHLENS_LOG_LEVEL", "ARCH_BENCH_LOG_LEVEL",
                    "INFO", environment=env,
                )
            ),
            "git_timeout_seconds": _env_int(
                "METROLITH_GIT_TIMEOUT", "ARCHLENS_GIT_TIMEOUT", "ARCH_BENCH_GIT_TIMEOUT", 900,
                environment=env,
            ),
            "git_retries": _env_int(
                "METROLITH_GIT_RETRIES", "ARCHLENS_GIT_RETRIES", "ARCH_BENCH_GIT_RETRIES", 3,
                environment=env,
            ),
            "acquisition_deadline_seconds": _env_int(
                "METROLITH_ACQUISITION_DEADLINE", "ARCHLENS_ACQUISITION_DEADLINE",
                "ARCH_BENCH_ACQUISITION_DEADLINE", 1800, environment=env,
            ),
            "download_timeout_seconds": _env_int(
                "METROLITH_DOWNLOAD_TIMEOUT", "ARCHLENS_DOWNLOAD_TIMEOUT",
                "ARCH_BENCH_DOWNLOAD_TIMEOUT", 120, environment=env,
            ),
            "repository_warning_threshold_seconds": _env_int(
                "METROLITH_REPOSITORY_WARNING_SECONDS",
                "ARCHLENS_REPOSITORY_WARNING_SECONDS",
                "ARCH_BENCH_REPOSITORY_WARNING_SECONDS",
                _env_int(
                    "METROLITH_REPOSITORY_TIMEOUT", "ARCHLENS_REPOSITORY_TIMEOUT",
                    "ARCH_BENCH_REPOSITORY_TIMEOUT", 1800, environment=env,
                ),
                environment=env,
            ),
            "auxiliary_warning_seconds": _env_int(
                "METROLITH_AUXILIARY_WARNING_SECONDS", "ARCHLENS_AUXILIARY_WARNING_SECONDS",
                "ARCH_BENCH_AUXILIARY_WARNING_SECONDS", 120, environment=env,
            ),
            "heartbeat_interval_seconds": _env_int(
                "METROLITH_HEARTBEAT_SECONDS", "ARCHLENS_HEARTBEAT_SECONDS",
                "ARCH_BENCH_HEARTBEAT_SECONDS", 60, environment=env,
            ),
            "full_inventory": str(
                resolve_environment_value(
                    "METROLITH_FULL_INVENTORY", "ARCHLENS_FULL_INVENTORY",
                    "ARCH_BENCH_FULL_INVENTORY", "false", environment=env,
                )
            ).strip().casefold()
            in {"1", "true", "yes", "on"},
            "require_clean_profiler": str(
                resolve_environment_value(
                    "METROLITH_REQUIRE_CLEAN_PROFILER",
                    "ARCHLENS_REQUIRE_CLEAN_PROFILER",
                    "ARCH_BENCH_REQUIRE_CLEAN_PROFILER",
                    "false",
                    environment=env,
                )
            ).strip().casefold()
            in {"1", "true", "yes", "on"},
            "max_source_file_size_bytes": _env_int(
                "METROLITH_MAX_SOURCE_FILE_SIZE",
                "ARCHLENS_MAX_SOURCE_FILE_SIZE",
                "ARCH_BENCH_MAX_SOURCE_FILE_SIZE",
                5 * 1024 * 1024,
                environment=env,
            ),
            "max_archive_files": _env_int(
                "METROLITH_MAX_ARCHIVE_FILES", "ARCHLENS_MAX_ARCHIVE_FILES",
                "ARCH_BENCH_MAX_ARCHIVE_FILES", 200_000, environment=env,
            ),
            "max_archive_extracted_bytes": _env_int(
                "METROLITH_MAX_ARCHIVE_BYTES",
                "ARCHLENS_MAX_ARCHIVE_BYTES",
                "ARCH_BENCH_MAX_ARCHIVE_BYTES",
                4 * 1024 * 1024 * 1024,
                environment=env,
            ),
        }
        values.update({key: value for key, value in overrides.items() if value is not None})
        config = cls(**values)
        config.validate()
        return config

    @property
    def exclusion_policy_version(self) -> str:
        return str(self.exclusion_policy["version"])

    @property
    def exclusion_policy_sha256(self) -> str:
        try:
            return hashlib.sha256(self.policy_path.read_bytes()).hexdigest()
        except OSError as exc:
            raise RuntimeError(
                f"Cannot hash exclusion policy {self.policy_path}: {exc}"
            ) from exc

    def validate(self) -> None:
        if self.workers < 1:
            raise ValueError("worker count must be at least 1")
        for name in (
            "git_timeout_seconds",
            "git_retries",
            "acquisition_deadline_seconds",
            "download_timeout_seconds",
            "repository_warning_threshold_seconds",
            "auxiliary_warning_seconds",
            "heartbeat_interval_seconds",
            "max_source_file_size_bytes",
            "max_archive_files",
            "max_archive_extracted_bytes",
        ):
            minimum = 1
            if getattr(self, name) < minimum:
                raise ValueError(f"{name} must be greater than zero")
        self.log_level = self.log_level.upper()
        if self.log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError(f"unsupported log level: {self.log_level}")

    def prepare_paths(self) -> None:
        """Create every program-owned directory or fail without falling back."""
        for label, path in (
            ("workspace", self.workspace_root),
            ("cache", self.cache_root),
            ("temporary worktree", self.temporary_directory),
            ("output", self.output_root),
        ):
            if path is None:
                continue
            try:
                path.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise PermissionError(
                    f"Cannot create or write the resolved {label} directory {path}: {exc}"
                ) from exc

    def snapshot(self) -> dict[str, Any]:
        values = asdict(self)
        for key in (
            "workspace_root",
            "output_root",
            "cache_root",
            "temporary_directory",
            "policy_path",
        ):
            if values[key] is not None:
                values[key] = str(values[key])
        values["supported_extensions"] = dict(sorted(SUPPORTED_EXTENSIONS.items()))
        values["exclusion_policy_version"] = self.exclusion_policy_version
        values["exclusion_policy_sha256"] = self.exclusion_policy_sha256
        values["effective_git_checkout_configuration"] = dict(
            GIT_CHECKOUT_CONFIGURATION
        )
        return values
