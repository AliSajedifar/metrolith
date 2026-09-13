"""Input-preserving admission and publication for derived CLI exports.

The caller declares consumed Runs, source trees and individual inputs. Existing
input objects are protected by path AND filesystem identity. Derived documents
may live beside evidence, but authoritative Run names and Git administration
are never output destinations. No arbitrary concurrent-filesystem-race claim:
we detect static aliases and recheck controlled changes before publication.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import stat
import tempfile


class ExportOutputError(OSError):
    """A derived destination could not be admitted or safely published."""


def _stamp(path: Path):
    try:
        s = path.lstat()
    except FileNotFoundError:
        return None
    return s.st_dev, s.st_ino, stat.S_IFMT(s.st_mode)


def _linked(path: Path) -> bool:
    return path.is_symlink() or path.is_junction()


def _same(left: Path, right: Path) -> bool:
    if str(left) == str(right):
        return True
    try:
        return left.samefile(right)
    except FileNotFoundError:
        return False


def _inside(path: Path, root: Path) -> bool:
    # Existing object identity uses the destination filesystem's semantics,
    # including case-sensitive Windows directories; never global casefold.
    return any(_same(parent, root) for parent in (path, *path.parents))


def _json(path: Path):
    from archlens_json import ARTIFACT_JSON_LIMITS, load_file
    try:
        return load_file(path, source="export input", limits=ARTIFACT_JSON_LIMITS)
    except (OSError, ValueError):
        return None


# Persisted reader contracts, including the partition families and ledgers.
_RUN_FILES = {
    "analysis.json", "run_manifest.json", "run_status.json", "environment.json",
    "catalog.csv", "sheet_metrics.csv", "language_metrics.csv", "errors.csv",
    "recoveries.csv", "normalized_input.csv", "contributions.csv", "callables.csv",
    "repositories_frozen.csv", "retry_failed_or_partial.csv", "benchmark_qualification.json",
    "repository_level_metrics.csv", "summary.md",
}
_RUN_FAMILIES = {"repositories", "file_inventory", "contributions", "callables", "git_mode_maps", "logs", "fact_sheets"}


def _derived(path: Path, kind: str) -> bool:
    """Recognize only this export's existing derived document in input trees."""
    from modules.config import SUPPORTED_EXTENSIONS

    # --output accepts arbitrary filenames. Keep analyzed source extensions
    # protected, but do not reject a native derived result named result.out.
    if path.suffix.lower() in SUPPORTED_EXTENSIONS or not path.is_file():
        return False
    with path.open("rb") as handle:
        prefix = handle.read(4096)
    # These are bounded format signatures, not proof of authorship. A generic
    # doctype or project heading is ordinary repository content. Require the
    # existing exporter's own identity and surrounding document structure.
    if kind == "report":
        return bool(re.match(
            rb'<!DOCTYPE html>\n<html lang="en">\n<head>\n'
            rb'<meta charset="utf-8">\n'
            rb'<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            rb'<title>Metrolith run report [^\n<>]+</title>\n<style>', prefix
        )) and all(marker in prefix for marker in (
            b'</style>\n</head>\n<body>\n',
            b'<h1>Metrolith run report</h1>\n',
            b'<nav aria-label="Report sections">',
            b'<main id="main-content">\n',
        ))
    if kind == "dossier" and prefix.startswith(b"#"):
        return bool(re.match(
            rb'# Metrolith analysis dossier\n\nFormat `archlens-dossier` '
            rb'[^\n]+, composed by Metrolith [^\n]+\.\n\n## Authority\n\n', prefix
        )) and b'\n## Run\n\n| Field | Value |\n|---|---|\n' in prefix
    if kind == "changed" and prefix.startswith(b"#"):
        return bool(re.match(
            rb'# Metrolith Changed-Code review brief\n\nFormat `archlens-changed-code` '
            rb'[^\n]+, produced by Metrolith [^\n]+\.\n\n- Status: \*\*[^\n]+\*\*\n', prefix
        ))
    markers = {
        "hotspots": (b"Metrolith hotspots ",),
        "duplication": (b"Metrolith duplication ",),
        "changed": (b"Metrolith Changed-Code Analysis ",),
    }
    if any(prefix.startswith(marker) for marker in markers.get(kind, ())):
        return True
    keys = {"policy": "policy_result_format_version", "perf": "performance_format_version",
            "diff": "revision_diff_format_version"}
    value = _json(path)
    if not isinstance(value, dict):
        return False
    if kind in keys:
        return keys[kind] in value
    return value.get("format") == {"hotspots": "archlens-hotspots", "duplication": "archlens-duplication", "changed": "archlens-changed-code", "dossier": "archlens-dossier"}.get(kind, "<none>")


class ExportDestination:
    def __init__(self, output, *, overwrite=False, runs=(), sources=(), files=(), kind):
        self.path = Path(os.path.abspath(Path(output).expanduser())) if output is not None else None
        self.overwrite = overwrite
        self.kind = kind
        self.files = [Path(os.path.abspath(Path(p).expanduser())) for p in files if p is not None]
        self.runs = [Path(os.path.abspath(Path(p).expanduser())) for p in runs if p is not None]
        self.sources = [Path(os.path.abspath(Path(p).expanduser())) for p in sources if p is not None]
        self.admin = []
        if self.path is None:
            return
        self._discover_runs()
        self._discover_admin()
        self._check()
        self.original = _stamp(self.path)
        self.parents = {str(p): _stamp(p) for p in self.path.parents if p.exists()}

    def _discover_runs(self):
        for root in self.runs:
            manifest = _json(root / "run_manifest.json")
            if not isinstance(manifest, dict):
                continue
            for key in ("input_file_path", "benchmark_qualification_artifact"):
                raw = manifest.get(key)
                if isinstance(raw, str) and Path(raw).is_absolute():
                    self.files.append(Path(raw))
            argv = manifest.get("command_line_arguments") or []
            command = argv.index("analyze") if "analyze" in argv else -1
            if command >= 0 and len(argv) > command + 1 and isinstance(argv[command + 1], str):
                source = Path(argv[command + 1]).expanduser()
                if source.is_absolute():
                    self.sources.append(Path(os.path.abspath(source)))
                else:
                    # Retained relative invocations have no recorded cwd. Only
                    # available candidate locations can be protected; no source
                    # identity/ancestry is inferred from them.
                    self.sources.append(Path(os.path.abspath(Path.cwd() / source)))
                    workspace = (manifest.get("resolved_paths") or {}).get("workspace_root")
                    if workspace:
                        self.sources.append(Path(os.path.abspath(Path(workspace) / source)))
            cache = (manifest.get("resolved_paths") or {}).get("cache_root")
            if cache:
                self.admin.append(Path(cache))

    def protect_runs(self, runs):
        """Add freshly resolved comparison Runs before publishing a diff."""
        self.runs.extend(Path(os.path.abspath(p)) for p in runs)
        if self.path is not None:
            self._discover_runs()
            self._discover_admin()
            self._check()

    def _discover_admin(self):
        for source in self.sources:
            for parent in (source, *source.parents):
                git = parent / ".git"
                if git.exists() or git.is_symlink():
                    self.files.append(git)
                    if git.is_dir():
                        self.admin.append(git)
                    elif git.is_file():
                        text = git.read_text(encoding="utf-8")[:4096].strip()
                        if text.startswith("gitdir: "):
                            directory = (git.parent / text[8:]).resolve()
                            self.admin.append(directory)
                            common = directory / "commondir"
                            if common.is_file():
                                self.admin.append((directory / common.read_text(encoding="utf-8").strip()).resolve())
                    break
            # A bare repository is entirely Git administration.
            if (source / "HEAD").is_file() and (source / "objects").is_dir():
                self.admin.append(source)

    def _members(self, root):
        if not root.is_dir():
            return
        def error(exc):
            raise exc
        for directory, dirs, names in os.walk(root, followlinks=False, onerror=error):
            for name in list(dirs):
                p = Path(directory) / name
                if _linked(p):
                    dirs.remove(name)
                    yield p
            for name in names:
                yield Path(directory) / name

    def _check(self):
        p = self.path
        for component in (p, *p.parents):
            if _linked(component):
                raise ExportOutputError("output destination or parent is a symlink or junction")
        info = _stamp(p)
        if info is not None and info[2] != stat.S_IFREG:
            raise ExportOutputError("output destination is not an ordinary file")
        if any(_inside(p, root) for root in self.admin):
            raise ExportOutputError("output is protected Git administration")
        for root in self.runs:
            if any(_same(p, root / name) for name in _RUN_FILES) or any(_inside(p, root / name) for name in _RUN_FAMILIES):
                raise ExportOutputError("output is protected Run evidence")
        protected = list(self.files)
        # An absent ordinary destination cannot be a hardlink to an input.
        # Recheck at publication; only existing destinations need enumeration.
        for root in ((*self.runs, *self.sources, *self.admin) if info is not None else ()):
            for member in self._members(root):
                # Only the exact eligible derived path may be replaced, never
                # a hardlink/other alias of a different protected object.
                if str(member) == str(p) and not any(_inside(member, a) for a in self.admin) and _derived(member, self.kind):
                    continue
                protected.append(member)
        for member in protected:
            if _same(p, member):
                raise ExportOutputError("output aliases protected input evidence or source")
        if info is not None and not self.overwrite:
            raise ExportOutputError("output already exists; use --overwrite where supported or choose a new output")

    def write(self, payload: str):
        if self.path is None:
            return
        temporary = None
        try:
            self._check()
            self._unchanged()
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._check()
            parents = {str(p): _stamp(p) for p in self.path.parents}
            fd, name = tempfile.mkstemp(prefix=".metrolith-export-", suffix=".tmp", dir=self.path.parent)
            temporary = Path(name)
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            self._check()
            self._unchanged()
            if any(_stamp(Path(p)) != identity for p, identity in parents.items()):
                raise ExportOutputError("output parent changed before publication")
            if self.overwrite:
                os.replace(temporary, self.path)
            else:
                os.link(temporary, self.path)
        except (OSError, ValueError, RuntimeError) as exc:
            raise ExportOutputError(f"derived output was not written: {exc}") from exc
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def _unchanged(self):
        if _stamp(self.path) != self.original or any(_stamp(Path(p)) != identity for p, identity in self.parents.items()):
            raise ExportOutputError("output destination or parent changed during evaluation")


def admit(output, **kwargs):
    try:
        return ExportDestination(output, **kwargs)
    except (OSError, ValueError, RuntimeError) as exc:
        raise ExportOutputError(f"cannot establish safe export destination: {exc}") from exc
