"""Check's input-preserving, atomic destination contract (not a shared default)."""
from __future__ import annotations

import os
from pathlib import Path
import stat
import tempfile


class CheckOutputError(OSError):
    """The requested destination could not be safely published."""


def _inside(path: Path, root: Path) -> bool:
    return path == root or path.is_relative_to(root)


def validate_destination(args) -> Path | None:
    raw = getattr(args, 'output', None)
    if raw is None:
        if getattr(args, 'overwrite', False):
            raise CheckOutputError('--overwrite requires --output')
        return None
    try:
        path = Path(raw).expanduser().absolute()
        if path.is_symlink() or path.is_junction():
            raise CheckOutputError('output destination is a symlink or junction; choose an ordinary file')
        destination = path.resolve(strict=False)
        roots = []
        if getattr(args, 'run_directory', None) is not None:
            roots.append(Path(args.run_directory).expanduser().resolve(strict=False))
        baseline = getattr(args, 'baseline', None)
        if baseline is not None:
            from modules.ratchet.cli_request import source_run_directory
            roots.append(source_run_directory(baseline))
        files = [Path(value).expanduser().resolve(strict=False) for key in (
            'policy', 'baseline', 'hotspots', 'duplication', 'evidence_receipt'
        ) if (value := getattr(args, key, None)) is not None]
        if any(_inside(destination, root) for root in roots) or destination in files:
            raise CheckOutputError('output is a protected input or lies inside an input run bundle; choose an external output')
        try:
            info = destination.stat()
        except FileNotFoundError:
            info = None
        if info is not None:
            if not stat.S_ISREG(info.st_mode):
                raise CheckOutputError('output destination is not an ordinary file')
            # samefile catches hardlinks; resolve above catches path/case/parent
            # aliases. Inspect only input bundles, never arbitrary host trees.
            for root in roots:
                if root.is_dir():
                    def onerror(exc):
                        raise exc
                    for directory, _, names in os.walk(root, followlinks=False, onerror=onerror):
                        files.extend(Path(directory) / name for name in names)
            for source in files:
                try:
                    same = destination.samefile(source)
                except FileNotFoundError:
                    continue
                if same:
                    raise CheckOutputError('output aliases protected input evidence; choose a different output')
            if not getattr(args, 'overwrite', False):
                raise CheckOutputError('output already exists; use --overwrite to replace an ordinary output file')
        return destination
    except (OSError, ValueError, RuntimeError) as exc:
        if isinstance(exc, CheckOutputError):
            raise
        raise CheckOutputError(f'cannot establish a safe Check output destination: {exc}') from exc


def write_result(args, payload: str) -> None:
    destination = validate_destination(args)
    if destination is None:
        return
    temporary = None
    write_failure = None
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=f'.{destination.name}.', suffix='.tmp', dir=destination.parent)
        temporary = Path(name)
        with os.fdopen(fd, 'w', encoding='utf-8', errors='strict', newline='') as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        # Recheck the caller's original path, including symlinked parents, at
        # publication. No-overwrite uses an atomic create, never check/replace.
        if validate_destination(args) != destination:
            raise CheckOutputError('output destination changed during evaluation')
        if getattr(args, 'overwrite', False):
            from modules.run_artifacts import _replace_with_retry
            _replace_with_retry(temporary, destination)
        else:
            os.link(temporary, destination)
    except (OSError, ValueError) as exc:
        write_failure = CheckOutputError(f'Check output was not written: {exc}')
        raise write_failure from exc
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError as exc:
                detail = f'{write_failure}; ' if write_failure is not None else ''
                raise CheckOutputError(f'{detail}Check output temporary-file cleanup failed: {exc}; any published result retains its evaluation verdict') from exc
