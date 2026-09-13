# -*- coding: utf-8 -*-
"""Conservative repository fetching and completeness validation."""

import errno
import hashlib
import json
import os
import shutil
import stat
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path, PurePosixPath
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from modules.config import resolve_environment_value
from modules.metadata import fetch_metadata, parse_github_repo


ARCHIVE_MARKER = ".metrolith_archive.json"
ARCHLENS_ARCHIVE_MARKER = ".archlens_archive.json"
LEGACY_ARCHIVE_MARKER = ".arch_bench_archive.json"
READABLE_ARCHIVE_MARKERS = (
    ARCHIVE_MARKER,
    ARCHLENS_ARCHIVE_MARKER,
    LEGACY_ARCHIVE_MARKER,
)
PARTIAL_GIT_MARKERS = (
    "index.lock",
    "shallow.lock",
    "MERGE_HEAD",
    "CHERRY_PICK_HEAD",
    "REVERT_HEAD",
    "rebase-apply",
    "rebase-merge",
)
LONG_PATH_PATTERNS = (
    "filename too long",
    "file name too long",
    "path too long",
    "the filename or extension is too long",
)
PROJECT_FILE_NAMES = {
    "build.gradle",
    "build.gradle.kts",
    "cargo.toml",
    "dockerfile",
    "go.mod",
    "makefile",
    "package.json",
    "pom.xml",
    "pyproject.toml",
    "readme",
    "readme.md",
    "requirements.txt",
    "setup.py",
    "license",
    "license.md",
}
PROJECT_SOURCE_EXTENSIONS = {
    ".c", ".cc", ".cpp", ".cs", ".go", ".h", ".hpp", ".java", ".js",
    ".jsx", ".kt", ".kts", ".php", ".py", ".rb", ".rs", ".scala",
    ".swift", ".ts", ".tsx",
}
GIT_TIMEOUT_SECONDS = int(
    resolve_environment_value(
        "METROLITH_GIT_TIMEOUT", "ARCHLENS_GIT_TIMEOUT", "ARCH_BENCH_GIT_TIMEOUT", "300"
    )
)
DOWNLOAD_TIMEOUT_SECONDS = int(
    resolve_environment_value(
        "METROLITH_DOWNLOAD_TIMEOUT", "ARCHLENS_DOWNLOAD_TIMEOUT",
        "ARCH_BENCH_DOWNLOAD_TIMEOUT", "120",
    )
)
MAX_ARCHIVE_FILES = int(
    resolve_environment_value(
        "METROLITH_MAX_ARCHIVE_FILES", "ARCHLENS_MAX_ARCHIVE_FILES",
        "ARCH_BENCH_MAX_ARCHIVE_FILES", "200000",
    )
)
MAX_ARCHIVE_EXTRACTED_BYTES = int(
    resolve_environment_value(
        "METROLITH_MAX_ARCHIVE_BYTES", "ARCHLENS_MAX_ARCHIVE_BYTES",
        "ARCH_BENCH_MAX_ARCHIVE_BYTES", str(4 * 1024 * 1024 * 1024),
    )
)


class FetchFailure(RuntimeError):
    def __init__(self, error_type, message):
        super().__init__(message)
        self.error_type = error_type
        self.message = message


def _result(status, method="none", error_type="none", message="", path=None):
    return {
        "fetch_status": status,
        "fetch_method": method,
        "fetch_error_type": error_type,
        "fetch_error_message": message,
        "path": Path(path) if path is not None else None,
    }


def _short_message(value, limit=500):
    message = " ".join(str(value or "").split())
    return message[:limit]


def detect_fetch_error_type(message, default="unknown"):
    if getattr(message, "winerror", None) == 206:
        return "long_path_error"
    if getattr(message, "errno", None) == errno.ENAMETOOLONG:
        return "long_path_error"
    lowered = str(message or "").lower()
    if any(pattern in lowered for pattern in LONG_PATH_PATTERNS):
        return "long_path_error"
    return default


def _handle_remove_readonly(function, path, exc_info):
    try:
        os.chmod(path, stat.S_IWRITE)
        function(path)
    except OSError:
        raise exc_info[1]


def _remove_path(path, error_type="cleanup_error"):
    """Remove a fetch target completely or raise a classified cleanup error."""
    path = Path(path)
    if not os.path.lexists(path):
        return
    try:
        if path.is_symlink() or path.is_file():
            path.unlink()
        else:
            shutil.rmtree(path, onerror=_handle_remove_readonly)
    except OSError as exc:
        raise FetchFailure(
            error_type, f"Could not remove partial repository {path}: {exc}"
        ) from exc
    if os.path.lexists(path):
        raise FetchFailure(
            error_type, f"Partial repository still exists after cleanup: {path}"
        )


def cleanup_stale_staging_directories(raw_dir):
    """Remove legacy in-cache staging directories without failing the run."""
    raw_dir = Path(raw_dir)
    warnings = []
    if not raw_dir.is_dir():
        return warnings
    for pattern in ("*.fetching", "*.fetching.archive", "*.tmp"):
        for path in raw_dir.glob(pattern):
            if not path.is_dir() and not path.is_symlink():
                continue
            try:
                _remove_path(path, "staging_cleanup_error")
            except FetchFailure as exc:
                warnings.append(_short_message(exc.message))
    return warnings


def _run_git(git_executable, repo_path, arguments, check=True):
    command = [
        git_executable,
        "-c",
        "core.longpaths=true",
        "-C",
        str(repo_path),
        *arguments,
    ]
    return subprocess.run(
        command,
        check=check,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=GIT_TIMEOUT_SECONDS,
    )


def _git_validation_failure(message):
    return {
        "valid": False,
        "repository_kind": "git",
        "error_type": detect_fetch_error_type(message, "incomplete_repository"),
        "message": _short_message(message),
    }


def _validate_git_repository(repo_path, git_executable):
    git_dir = repo_path / ".git"
    if not git_dir.exists():
        return _git_validation_failure("Git metadata directory is missing")

    for marker in PARTIAL_GIT_MARKERS:
        if (git_dir / marker).exists():
            return _git_validation_failure(
                f"Partial Git operation marker exists: .git/{marker}"
            )
    if (git_dir / "info" / "sparse-checkout").exists():
        return _git_validation_failure(
            "Sparse checkout metadata is present; full working tree is required"
        )

    try:
        _run_git(
            git_executable,
            repo_path,
            ["config", "core.longpaths", "true"],
        )
        inside = _run_git(
            git_executable,
            repo_path,
            ["rev-parse", "--is-inside-work-tree"],
        ).stdout.strip()
        if inside != "true":
            return _git_validation_failure("Directory is not a Git working tree")

        _run_git(git_executable, repo_path, ["rev-parse", "--verify", "HEAD^{commit}"])
        _run_git(git_executable, repo_path, ["fsck", "--connectivity-only", "--no-dangling"])

        status = _run_git(
            git_executable,
            repo_path,
            ["status", "--porcelain=v1", "--untracked-files=no"],
        ).stdout.strip()
        if status:
            return _git_validation_failure(
                f"Tracked working tree differs from HEAD: {status[:300]}"
            )

        tracked_output = _run_git(
            git_executable,
            repo_path,
            ["ls-tree", "-r", "-z", "--name-only", "HEAD"],
        ).stdout
        tracked_paths = [item for item in tracked_output.split("\0") if item]
        if not tracked_paths:
            return _git_validation_failure("HEAD contains no tracked project files")

        missing = [
            relative
            for relative in tracked_paths
            if not os.path.lexists(repo_path / Path(relative))
        ]
        if missing:
            sample = ", ".join(missing[:5])
            return _git_validation_failure(
                f"{len(missing)} tracked working-tree files are missing: {sample}"
            )

        submodules = _run_git(
            git_executable,
            repo_path,
            ["submodule", "status", "--recursive"],
        ).stdout.splitlines()
        incomplete_submodules = [
            line for line in submodules if line.startswith(("-", "+", "U"))
        ]
        if incomplete_submodules:
            return _git_validation_failure(
                "Submodules are not fully checked out: "
                + "; ".join(incomplete_submodules[:5])
            )
    except subprocess.CalledProcessError as exc:
        details = exc.stderr or exc.stdout or str(exc)
        return _git_validation_failure(f"Git validation failed: {details}")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return _git_validation_failure(f"Git validation could not run: {exc}")

    return {
        "valid": True,
        "repository_kind": "git",
        "error_type": "none",
        "message": "",
    }


def _project_files(repo_path):
    files = []
    for root, dirs, names in os.walk(repo_path, topdown=True):
        dirs[:] = sorted(d for d in dirs if d != ".git")
        for name in sorted(names):
            if name not in set(READABLE_ARCHIVE_MARKERS):
                files.append(Path(root) / name)
    return files


def _has_real_project_files(files):
    for path in files:
        lower_name = path.name.lower()
        if (
            lower_name in PROJECT_FILE_NAMES
            or lower_name.startswith(("readme.", "license."))
            or path.suffix.lower() in PROJECT_SOURCE_EXTENSIONS
        ):
            return True
    return False


def _file_manifest(repo_path, files):
    digest = hashlib.sha256()
    for path in sorted(files, key=lambda item: item.relative_to(repo_path).as_posix()):
        relative = path.relative_to(repo_path).as_posix()
        try:
            with path.open("rb") as handle:
                content_hash = hashlib.sha256()
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    content_hash.update(chunk)
        except OSError as exc:
            raise FetchFailure(
                "incomplete_repository",
                f"Could not inspect extracted project file {relative}: {exc}",
            ) from exc
        digest.update(relative.encode("utf-8", errors="surrogateescape"))
        digest.update(b"\0")
        digest.update(content_hash.hexdigest().encode("ascii"))
        digest.update(b"\0")
    return digest.hexdigest()


def _validate_archive_repository(repo_path):
    marker_path = next(
        (
            repo_path / marker
            for marker in READABLE_ARCHIVE_MARKERS
            if (repo_path / marker).is_file()
        ),
        repo_path / ARCHIVE_MARKER,
    )
    if not marker_path.is_file():
        return {
            "valid": False,
            "repository_kind": "archive",
            "error_type": "incomplete_repository",
            "message": "Archive completion marker is missing",
        }
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "valid": False,
            "repository_kind": "archive",
            "error_type": "incomplete_repository",
            "message": f"Archive completion marker is invalid: {exc}",
        }

    files = _project_files(repo_path)
    if not files or not _has_real_project_files(files):
        return {
            "valid": False,
            "repository_kind": "archive",
            "error_type": "incomplete_repository",
            "message": "Archive repository contains no recognizable project files",
        }
    expected_count = marker.get("file_count")
    if not isinstance(expected_count, int) or expected_count != len(files):
        return {
            "valid": False,
            "repository_kind": "archive",
            "error_type": "incomplete_repository",
            "message": (
                "Archive file count does not match its completion marker "
                f"({len(files)} present, {expected_count} expected)"
            ),
        }
    try:
        actual_manifest = _file_manifest(repo_path, files)
    except FetchFailure as exc:
        return {
            "valid": False,
            "repository_kind": "archive",
            "error_type": exc.error_type,
            "message": exc.message,
        }
    if marker.get("manifest_sha256") != actual_manifest:
        return {
            "valid": False,
            "repository_kind": "archive",
            "error_type": "incomplete_repository",
            "message": "Archive file manifest does not match its completion marker",
        }
    if (repo_path / ".gitmodules").exists():
        return {
            "valid": False,
            "repository_kind": "archive",
            "error_type": "incomplete_repository",
            "message": "GitHub archives do not contain required submodule working trees",
        }
    return {
        "valid": True,
        "repository_kind": "archive",
        "error_type": "none",
        "message": "",
    }


def validate_repository(repo_path, git_executable=None):
    """Strictly validate a Git clone or a completed archive cache."""
    repo_path = Path(repo_path)
    if not repo_path.is_dir():
        return {
            "valid": False,
            "repository_kind": "unknown",
            "error_type": "incomplete_repository",
            "message": "Repository directory does not exist",
        }
    if not any(repo_path.iterdir()):
        return {
            "valid": False,
            "repository_kind": "unknown",
            "error_type": "incomplete_repository",
            "message": "Repository directory is empty",
        }

    if (repo_path / ".git").exists():
        git_executable = git_executable or shutil.which("git")
        if not git_executable:
            return {
                "valid": False,
                "repository_kind": "git",
                "error_type": "incomplete_repository",
                "message": "Git is required to validate the existing clone",
            }
        return _validate_git_repository(repo_path, git_executable)
    return _validate_archive_repository(repo_path)


def _download_file(url, destination):
    request = Request(url, headers={"User-Agent": "Metrolith/3.0.0"})
    with urlopen(request, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:
        with open(destination, "wb") as file_handle:
            shutil.copyfileobj(response, file_handle)


def _download_file_with_powershell(url, destination):
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        raise RuntimeError("PowerShell is not available for download fallback")
    command = (
        "[Net.ServicePointManager]::SecurityProtocol = "
        "[Net.SecurityProtocolType]::Tls12; "
        "Invoke-WebRequest "
        f"-Uri '{url}' -OutFile '{destination}' "
        "-Headers @{ 'User-Agent' = 'Metrolith/3.0.0' }"
    )
    subprocess.run(
        [powershell, "-NoProfile", "-Command", command],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=DOWNLOAD_TIMEOUT_SECONDS,
    )


def _download_file_with_curl(url, destination):
    curl = shutil.which("curl.exe") or shutil.which("curl")
    if not curl:
        raise RuntimeError("curl is not available for download fallback")
    command = [
        curl,
        "--ssl-no-revoke",
        "-L",
        "--fail",
        "--retry",
        "3",
        "--retry-delay",
        "2",
        "-A",
        "Metrolith/3.0.0",
        "-o",
        str(destination),
        url,
    ]
    subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=DOWNLOAD_TIMEOUT_SECONDS,
    )


def _download_with_retries(urls, destination, attempts=3):
    last_error = None
    for url in urls:
        for downloader, retries in (
            (_download_file, attempts),
            (_download_file_with_powershell, 1),
            (_download_file_with_curl, 1),
        ):
            for _ in range(retries):
                try:
                    downloader(url, destination)
                    return url
                except (
                    HTTPError,
                    URLError,
                    TimeoutError,
                    OSError,
                    subprocess.CalledProcessError,
                    RuntimeError,
                ) as exc:
                    last_error = exc
                    if destination.exists():
                        destination.unlink()
    raise last_error or RuntimeError("Archive download failed")


def _validate_zip_path(member_name):
    normalized = member_name.replace("\\", "/")
    member = PurePosixPath(normalized)
    if member.is_absolute() or ".." in member.parts:
        raise FetchFailure("archive_error", f"Unsafe path in archive: {member_name}")
    if member.parts and ":" in member.parts[0]:
        raise FetchFailure("archive_error", f"Unsafe drive path in archive: {member_name}")


def _extract_validated_archive(archive_path, extraction_dir):
    if not zipfile.is_zipfile(archive_path):
        raise FetchFailure(
            "archive_error",
            "Downloaded content is not a valid ZIP archive (possibly an HTML/API error page)",
        )
    try:
        with zipfile.ZipFile(archive_path) as archive:
            bad_member = archive.testzip()
            if bad_member:
                raise FetchFailure(
                    "archive_error", f"ZIP integrity check failed for {bad_member}"
                )
            members = [member for member in archive.infolist() if member.filename]
            if not members:
                raise FetchFailure("archive_error", "ZIP archive is empty")
            if len(members) > MAX_ARCHIVE_FILES:
                raise FetchFailure(
                    "archive_limit_exceeded",
                    f"ZIP contains {len(members)} entries; limit is {MAX_ARCHIVE_FILES}",
                )
            extracted_size = sum(member.file_size for member in members)
            if extracted_size > MAX_ARCHIVE_EXTRACTED_BYTES:
                raise FetchFailure(
                    "archive_limit_exceeded",
                    f"ZIP expands to {extracted_size} bytes; limit is {MAX_ARCHIVE_EXTRACTED_BYTES}",
                )
            for member in members:
                _validate_zip_path(member.filename)
            archive.extractall(extraction_dir)
    except zipfile.BadZipFile as exc:
        raise FetchFailure("archive_error", f"Invalid ZIP archive: {exc}") from exc
    except OSError as exc:
        error_type = detect_fetch_error_type(exc, "archive_error")
        raise FetchFailure(error_type, f"Archive extraction failed: {exc}") from exc

    roots = [path for path in extraction_dir.iterdir() if path.is_dir()]
    loose_files = [path for path in extraction_dir.iterdir() if path.is_file()]
    if len(roots) != 1 or loose_files:
        raise FetchFailure(
            "archive_error", "ZIP archive does not contain one repository root"
        )
    return roots[0]


def _download_archive(repo_url, staging_path, default_branch=None):
    owner, repo = parse_github_repo(repo_url)
    branch = default_branch
    if not branch:
        metadata = fetch_metadata(repo_url)
        branch = metadata.get("default_branch") if not metadata.get("error") else None
    branches = []
    for candidate in (branch, "main", "master"):
        if candidate and candidate not in branches:
            branches.append(candidate)

    workspace = staging_path.parent / f"{staging_path.name}.archive"
    _remove_path(workspace)
    workspace.mkdir(parents=True)
    archive_path = workspace / "repository.zip"
    last_failure = None
    try:
        for branch_name in branches:
            urls = [
                f"https://codeload.github.com/{owner}/{repo}/zip/refs/heads/{branch_name}",
                f"https://github.com/{owner}/{repo}/archive/refs/heads/{branch_name}.zip",
            ]
            try:
                source_url = _download_with_retries(urls, archive_path)
                extraction_dir = workspace / "extracted"
                _remove_path(extraction_dir)
                extraction_dir.mkdir()
                root = _extract_validated_archive(archive_path, extraction_dir)
                files = _project_files(root)
                if not files or not _has_real_project_files(files):
                    raise FetchFailure(
                        "archive_error",
                        "Extracted archive contains no recognizable project files",
                    )
                if (root / ".gitmodules").exists():
                    raise FetchFailure(
                        "incomplete_repository",
                        "Archive contains submodule declarations but not submodule contents",
                    )

                marker = {
                    "owner": owner,
                    "repository": repo,
                    "branch": branch_name,
                    "source_url": source_url,
                    "file_count": len(files),
                    "manifest_sha256": _file_manifest(root, files),
                }
                (root / ARCHIVE_MARKER).write_text(
                    json.dumps(marker, sort_keys=True), encoding="utf-8"
                )
                _remove_path(staging_path)
                root.replace(staging_path)
                validation = validate_repository(staging_path)
                if not validation["valid"]:
                    raise FetchFailure(
                        validation["error_type"], validation["message"]
                    )
                return
            except FetchFailure as exc:
                last_failure = exc
            except (
                HTTPError,
                URLError,
                TimeoutError,
                OSError,
                subprocess.CalledProcessError,
                RuntimeError,
            ) as exc:
                error_type = detect_fetch_error_type(exc, "archive_error")
                last_failure = FetchFailure(
                    error_type, f"Archive fetch failed for branch {branch_name}: {exc}"
                )
            finally:
                if archive_path.exists():
                    archive_path.unlink()
        raise last_failure or FetchFailure("archive_error", "No archive branch succeeded")
    finally:
        _remove_path(workspace)


def _clone_with_git(repo_url, staging_path, git_executable):
    command = [
        git_executable,
        "-c",
        "core.longpaths=true",
        "clone",
        "--depth",
        "1",
        "--recurse-submodules",
        "--shallow-submodules",
        repo_url,
        str(staging_path),
    ]
    try:
        subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except subprocess.CalledProcessError as exc:
        details = exc.stderr or exc.stdout or str(exc)
        error_type = detect_fetch_error_type(details, "clone_error")
        raise FetchFailure(error_type, f"Git clone failed: {_short_message(details)}") from exc
    except (OSError, subprocess.TimeoutExpired) as exc:
        error_type = detect_fetch_error_type(exc, "clone_error")
        raise FetchFailure(error_type, f"Git clone could not run: {exc}") from exc

    validation = validate_repository(staging_path, git_executable)
    if not validation["valid"]:
        raise FetchFailure(validation["error_type"], validation["message"])


def _is_retryable_move_error(exc):
    return (
        isinstance(exc, PermissionError)
        or getattr(exc, "winerror", None) in (5, 32)
        or getattr(exc, "errno", None) in (errno.EACCES, errno.EPERM)
    )


def _validate_installed_repository(destination, git_executable):
    validation = validate_repository(destination, git_executable)
    if not validation["valid"]:
        try:
            _remove_path(destination, "staging_cleanup_error")
        except FetchFailure as cleanup_failure:
            raise cleanup_failure
        raise FetchFailure(
            "incomplete_repository",
            f"Installed repository failed final validation: {validation['message']}",
        )


def _install_staging_repository(
    staging_path,
    destination,
    git_executable=None,
    move_attempts=4,
    retry_delay=0.1,
):
    """Promote a complete staging tree without ever merging into the cache."""
    staging_path = Path(staging_path)
    destination = Path(destination)
    if os.path.lexists(destination):
        raise FetchFailure(
            "staging_move_error",
            f"Final cache path already exists; refusing to merge: {destination}",
        )

    move_error = None
    for attempt in range(move_attempts):
        try:
            staging_path.replace(destination)
            _validate_installed_repository(destination, git_executable)
            return
        except OSError as exc:
            move_error = exc
            if not _is_retryable_move_error(exc) or attempt + 1 >= move_attempts:
                break
            time.sleep(retry_delay * (attempt + 1))

    if os.path.lexists(destination):
        raise FetchFailure(
            "staging_move_error",
            f"Failed move left an unexpected final cache directory: {destination}",
        )

    try:
        shutil.copytree(staging_path, destination, symlinks=True)
        _validate_installed_repository(destination, git_executable)
    except FetchFailure:
        raise
    except OSError as exc:
        try:
            _remove_path(destination, "staging_cleanup_error")
        except FetchFailure as cleanup_failure:
            raise cleanup_failure from exc
        raise FetchFailure(
            "staging_move_error",
            f"Could not install repository after move failed ({move_error}): {exc}",
        ) from exc

    try:
        _remove_path(staging_path, "staging_cleanup_error")
    except FetchFailure:
        try:
            _remove_path(destination, "staging_cleanup_error")
        except FetchFailure:
            pass
        raise


def clone_repository(
    repo_url, destination, default_branch=None, legacy_paths=None
):
    """
    Validate an existing cache or fetch into a staging directory and validate it.

    The returned dictionary is the fetch contract consumed by the pipeline.
    Analysis is allowed only when ``fetch_status`` is ``success``.
    """
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    git_executable = shutil.which("git")
    staging_root = None
    staging_path = None

    def failed_result(error):
        if staging_root is not None:
            try:
                _remove_path(staging_root, "staging_cleanup_error")
            except FetchFailure as cleanup_failure:
                error = cleanup_failure
        return _result(
            "failed",
            "none",
            error.error_type,
            _short_message(error.message),
        )

    try:
        if not os.path.lexists(destination):
            for legacy_path in legacy_paths or []:
                legacy_path = Path(legacy_path)
                if legacy_path == destination or not os.path.lexists(legacy_path):
                    continue
                validation = validate_repository(legacy_path, git_executable)
                if validation["valid"]:
                    _install_staging_repository(
                        legacy_path, destination, git_executable
                    )
                    return _result(
                        "success",
                        "existing_valid_cache",
                        path=destination,
                    )
                _remove_path(legacy_path)

        if os.path.lexists(destination):
            validation = validate_repository(destination, git_executable)
            if validation["valid"]:
                return _result(
                    "success",
                    "existing_valid_cache",
                    path=destination,
                )
            _remove_path(destination)

        temp_parent = resolve_environment_value(
            "METROLITH_TEMP_DIR", "ARCHLENS_TEMP_DIR", "ARCH_BENCH_TEMP_DIR"
        )
        if temp_parent:
            try:
                temp_parent = str(Path(temp_parent).expanduser().resolve())
                Path(temp_parent).mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise FetchFailure(
                    "staging_cleanup_error",
                    f"Could not prepare METROLITH_TEMP_DIR {temp_parent}: {exc}",
                ) from exc
        staging_root = Path(
            tempfile.mkdtemp(
                prefix=f"metrolith_{destination.name}_",
                dir=temp_parent or None,
            )
        )
        staging_path = staging_root / "repository"
        git_failure = None
        if git_executable:
            try:
                _clone_with_git(repo_url, staging_path, git_executable)
                _install_staging_repository(
                    staging_path, destination, git_executable
                )
                _remove_path(staging_root, "staging_cleanup_error")
                return _result("success", "git_clone", path=destination)
            except FetchFailure as exc:
                if exc.error_type in (
                    "staging_move_error",
                    "staging_cleanup_error",
                ):
                    raise
                git_failure = exc
                _remove_path(staging_path, "staging_cleanup_error")

        try:
            _download_archive(repo_url, staging_path, default_branch=default_branch)
            _install_staging_repository(staging_path, destination, git_executable)
            _remove_path(staging_root, "staging_cleanup_error")
            return _result("success", "archive", path=destination)
        except FetchFailure as archive_failure:
            _remove_path(staging_path, "staging_cleanup_error")
            if (
                git_failure
                and git_failure.error_type == "long_path_error"
                and archive_failure.error_type != "long_path_error"
            ):
                final_failure = FetchFailure(
                    "long_path_error",
                    f"{git_failure.message}; archive fallback also failed: "
                    f"{archive_failure.message}",
                )
            elif git_failure:
                final_failure = FetchFailure(
                    archive_failure.error_type,
                    f"Git attempt failed: {git_failure.message}; "
                    f"archive fallback failed: {archive_failure.message}",
                )
            else:
                final_failure = archive_failure
            return failed_result(final_failure)
    except FetchFailure as exc:
        return failed_result(exc)
    except Exception as exc:
        error_type = detect_fetch_error_type(exc, "unknown")
        return failed_result(FetchFailure(error_type, _short_message(exc)))
