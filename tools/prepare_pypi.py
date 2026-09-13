"""Bounded, upload-free candidate preparation. Never invokes the full verifier."""

from __future__ import annotations

import argparse
import base64
import csv
import email
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import subprocess
import sys
import tarfile
import zipfile

from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from release_verify import ReleaseVerifier

VERSION = "4.0.0"
FILENAMES = (f"metrolith-{VERSION}-py3-none-any.whl", f"metrolith-{VERSION}.tar.gz")
BASELINE_RUNTIME = "9447207fbe25027a5878fd652ef56fe387372a5be690fa11e790521eea3f352a"
BASELINE_PRODUCER = "sha256:3815aa21c2d091cd208e2db7038bae50acb0bed80c34b59d36a3775ea19a0020"
LOGO_URL = "https://raw.githubusercontent.com/AliSajedifar/metrolith/af49510a0d44dd14cb18e11ec86aaee0a3bfa57c/docs/assets/Logo.png"


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def interpreter_path(path):
    # Resolving a POSIX virtualenv symlink selects the base interpreter and
    # silently discards its installed validation tools. Preserve the venv path.
    return path.absolute()


def save(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def run(args, cwd, logs, label):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1",
               PIP_DISABLE_PIP_VERSION_CHECK="1", PIP_NO_INPUT="1")
    env.pop("PYTHONPATH", None)
    result = subprocess.run([str(a) for a in args], cwd=cwd, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            timeout=600)
    (logs / f"{label}.log").write_bytes(result.stdout)
    require(result.returncode == 0, f"{label} failed: see {logs / (label + '.log')}")
    print(f"PASS {label}", flush=True)
    return result.stdout.decode("utf-8")


def safe_name(name):
    p = PurePosixPath(name)
    require(bool(name) and not p.is_absolute() and ".." not in p.parts
            and ".git" not in p.parts and "\\" not in name
            and not re.match(r"^[A-Za-z]:", name), f"unsafe member: {name}")


def archive_checks(source, dist):
    import tomllib
    project = tomllib.loads((source / "pyproject.toml").read_text("utf-8"))["project"]
    readme = (source / "README.md").read_text("utf-8").strip()
    require(LOGO_URL in readme, "README must reference the committed public logo")
    require(sorted(p.name for p in dist.iterdir()) == sorted(FILENAMES), "unexpected candidate files")

    def metadata(data):
        msg = email.message_from_bytes(data)
        require(msg["Name"] == "metrolith" and msg["Version"] == VERSION, "metadata name/version")
        require(SpecifierSet(msg["Requires-Python"]) == SpecifierSet(project["requires-python"]), "Python requirement")
        require({Requirement(x) for x in msg.get_all("Requires-Dist", [])}
                == {Requirement(x) for x in project["dependencies"]}, "runtime dependencies changed")
        require(msg["License-Expression"] == "Apache-2.0", "license metadata")
        require(msg["Description-Content-Type"] == "text/markdown", "README content type")
        require(data.decode("utf-8").replace("\r\n", "\n").partition("\n\n")[2].strip()
                == readme, "README description differs")

    with zipfile.ZipFile(dist / FILENAMES[0]) as z:
        names = z.namelist()
        require(len(names) == len(set(names)), "duplicate wheel members")
        for info in z.infolist():
            safe_name(info.filename)
            require((info.external_attr >> 16) & 0o170000 != 0o120000, "wheel symlink")
        prefix = f"metrolith-{VERSION}.dist-info/"
        metadata(z.read(prefix + "METADATA"))
        require(z.read(prefix + "licenses/LICENSE") == (source / "LICENSE").read_bytes(), "wheel license")
        records = list(csv.reader(io.StringIO(z.read(prefix + "RECORD").decode())))
        require(len(records) == len(names) and {r[0] for r in records} == set(names), "RECORD members")
        for name, digest, size in records:
            if name == prefix + "RECORD":
                require(digest == size == "", "RECORD self entry")
                continue
            data = z.read(name)
            expected = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip("=")
            require(digest == "sha256=" + expected and int(size) == len(data), f"RECORD: {name}")
        runtime = sorted((n, sha(z.read(n))) for n in names if ".dist-info/" not in n)
        runtime_hash = sha(json.dumps(runtime, separators=(",", ":"), ensure_ascii=True).encode())
        require(len(runtime) == 362 and runtime_hash == BASELINE_RUNTIME, "runtime differs from reviewed baseline")

    with tarfile.open(dist / FILENAMES[1]) as t, tarfile.open(source.parent / "source.tar") as exported:
        members = t.getmembers()
        require(len(members) == len({m.name for m in members}), "duplicate sdist members")
        for m in members:
            safe_name(m.name)
            require(m.isfile() or m.isdir(), "sdist special member")
            require(PurePosixPath(m.name).parts[0] == f"metrolith-{VERSION}", "sdist root")
        files = {m.name.partition("/")[2]: t.extractfile(m).read() for m in members if m.isfile()}
        original = {m.name: exported.extractfile(m).read() for m in exported.getmembers() if m.isfile()}
        for name, data in original.items():
            require(files.get(name) == data, f"sdist source mismatch: {name}")
        extras = set(files) - set(original)
        require(all(n.startswith("metrolith.egg-info/") or n in ("PKG-INFO", "setup.cfg") for n in extras), "sdist extras")
        metadata(files["PKG-INFO"])
        for name in ("Logo.png", "Logo_Typography.png", "metrolith-wordmark-dark.png"):
            require(files[f"docs/assets/{name}"] == (source / "docs/assets" / name).read_bytes(), "sdist logo")
    return {"safe_members_metadata_record_license_readme_assets": "passed",
            "source_files": len(original), "sdist_files": len(files), "wheel_files": len(names),
            "runtime_members": len(runtime), "runtime_sha256": runtime_hash,
            "runtime_equals_reviewed_baseline": True}


def prepare(repository, output, validation_python, source_sha):
    require(platform.python_implementation() == "CPython" and platform.python_version() == "3.13.9", "CPython 3.13.9 required")
    require(re.fullmatch(r"[0-9a-f]{40}", source_sha), "full source SHA required")
    require(not output.is_relative_to(repository), "output must be outside checkout")
    require(not output.exists(), "output must be a fresh directory")
    output.mkdir(parents=True)
    logs = output / "checks"
    logs.mkdir()
    head = run(["git", "rev-parse", "HEAD"], repository, logs, "source-sha").strip()
    require(head == source_sha, "checkout differs from dispatch SHA")
    require(not run(["git", "status", "--porcelain"], repository, logs, "source-clean").strip(), "dirty source")
    stamp = int(run(["git", "show", "-s", "--format=%ct", head], repository, logs, "source-timestamp"))
    run(["git", "--version"], output, logs, "git-version")
    run([sys.executable, "-m", "pip", "check"], output, logs, "build-pip-check")
    run([validation_python, "-m", "pip", "check"], output, logs, "validation-pip-check")

    # Invoke only the previously reviewed export -> sdist -> standalone wheel path.
    verifier = ReleaseVerifier(repository)
    verifier.build_distributions(output, commit_timestamp=stamp, version=VERSION)
    source, dist = output / "source", output / "dist"
    checks = archive_checks(source, dist)
    run([validation_python, "-m", "twine", "check", "--strict", *(dist / n for n in FILENAMES)], output, logs, "strict-twine")
    # Render CommonMark with tables, then apply the same HTML sanitizer used by
    # readme-renderer. The frozen validation lock has no optional cmarkgfm extra.
    render = "from pathlib import Path; from markdown_it import MarkdownIt; from readme_renderer.clean import clean; import sys; html=clean(MarkdownIt('commonmark', {'html':True}).enable('table').render(Path(sys.argv[1]).read_text(encoding='utf-8'))); assert html and 'Logo.png' in html; Path(sys.argv[2]).write_text(html, encoding='utf-8')"
    run([validation_python, "-c", render, source / "README.md", logs / "readme-pypi-compatible.html"], output, logs, "readme-render")
    run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         "tests/test_release_documentation.py", "tests/test_pypi_preparation.py"], source, logs, "focused-regressions")

    runtime = output / "installed-wheel"
    run([sys.executable, "-m", "venv", runtime], output, logs, "create-runtime")
    python = runtime / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    run([python, "-m", "pip", "install", "--require-hashes", "-r", source / "requirements/action-runtime.lock"], output, logs, "install-locked-runtime")
    run([python, "-m", "pip", "install", "--no-deps", dist / FILENAMES[0]], output, logs, "install-candidate-wheel")
    verify_install = "import importlib.metadata as m, pathlib, sys, zipfile, pipeline; assert pathlib.Path(pipeline.__file__).is_relative_to(pathlib.Path(sys.prefix)); d=m.distribution('metrolith'); z=zipfile.ZipFile(sys.argv[1]); assert all(pathlib.Path(d.locate_file(n)).read_bytes()==z.read(n) for n in z.namelist() if not n.endswith('/RECORD'))"
    run([python, "-I", "-c", verify_install, dist / FILENAMES[0]], output, logs, "installed-member-bytes")
    run([python, "-m", "pip", "check"], output, logs, "runtime-pip-check")
    cli = [python, "-I", "-m", "pipeline"]
    require(run([*cli, "--version"], output, logs, "installed-version").strip() == "Metrolith 4.0.0", "installed version")
    run([*cli, "doctor", "--format", "text"], output, logs, "installed-doctor")
    smoke = output / "smoke"
    smoke.mkdir()
    run([*cli, "example", "run", "--local"], smoke, logs, "packaged-local-example")
    runs = list((smoke / "metrolith-output/runs").iterdir())
    require(len(runs) == 1, "expected one example Run")
    for command in ("report", "explain", "validate"):
        run([*cli, command, runs[0]], smoke, logs, "installed-" + command)
    producer_code = "import json,sys; from pathlib import Path; from modules.ratchet.semantics import producer_from_manifest; print(json.dumps(producer_from_manifest(json.loads(Path(sys.argv[1]).read_bytes())).to_dict()))"
    producer = json.loads(run([python, "-I", "-c", producer_code, runs[0] / "run_manifest.json"], output, logs, "producer-identity"))
    require(producer == {"program_name": "Metrolith", "program_version": VERSION,
                         "evaluator_source_identity": BASELINE_PRODUCER,
                         "metric_contract_version": "3.0.0", "complexity_contract_version": "2.0.0"}, "ProducerIdentity changed")
    checks.update({"strict_twine": "passed", "focused_regressions": "passed",
                   "installed_wheel_smoke": "passed", "producer_identity": producer,
                   "full_engine_suite": "NOT RUN", "upload": "NOT PERFORMED"})
    save(logs / "checks.json", checks)
    artifacts = [{"filename": n, "size_bytes": (dist / n).stat().st_size,
                  "sha256": sha((dist / n).read_bytes())} for n in FILENAMES]
    manifest = {"project": "metrolith", "version": VERSION, "source_sha": head,
                "repository": "AliSajedifar/metrolith", "python": platform.python_version(),
                "run_id": os.environ.get("GITHUB_RUN_ID"), "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
                "artifacts": artifacts, "checks": checks,
                "lock_sha256": {p.name: sha(p.read_bytes()) for p in (source / "requirements").glob("*.lock")}}
    save(output / "candidate-manifest.json", manifest)
    (output / "SHA256SUMS").write_text("".join(f"{a['sha256']}  dist/{a['filename']}\n" for a in artifacts), encoding="ascii")
    require(not run(["git", "status", "--porcelain"], repository, logs, "source-clean-after").strip(), "source changed")
    print(json.dumps(manifest, indent=2), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--validation-python", required=True, type=Path)
    parser.add_argument("--source-sha", required=True)
    args = parser.parse_args()
    prepare(Path(__file__).resolve().parent.parent, args.output.resolve(),
            interpreter_path(args.validation_python), args.source_sha)
