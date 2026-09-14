"""Upload-free package checks; one tiny example, never full qualification."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tomllib

from prepare_pypi import interpreter_path, require, run, save
from release_verify import ReleaseVerifier


def check(output: Path, validation_python: Path, *, skip_example: bool = False) -> None:
    repository = Path(__file__).resolve().parents[1]
    output = output.resolve()
    require(not output.is_relative_to(repository), "output must be outside checkout")
    require(not output.exists(), "use a fresh output directory")
    output.mkdir(parents=True)
    logs = output / "checks"
    logs.mkdir()
    require(not run(["git", "status", "--porcelain"], repository, logs, "clean-before").strip(), "commit changes first")
    head = run(["git", "rev-parse", "HEAD"], repository, logs, "source").strip()
    stamp = int(run(["git", "show", "-s", "--format=%ct", head], repository, logs, "timestamp"))
    version = tomllib.loads((repository / "pyproject.toml").read_text("utf-8"))["project"]["version"]
    # Reuse the established Git export -> normalized sdist -> standalone wheel path.
    ReleaseVerifier(repository).build_distributions(output, commit_timestamp=stamp, version=version)
    dist = output / "dist"
    wheel, = dist.glob("*.whl")
    sdist, = dist.glob("*.tar.gz")
    run([validation_python, "-m", "twine", "check", "--strict", wheel, sdist], output, logs, "twine")
    tests = ["tests/test_release_documentation.py", "tests/test_pypi_preparation.py",
             "tests/test_final_release_hardening.py::FinalCliSurfaceTests::test_readme_links_to_reference_covering_the_audited_commands",
             "tests/test_final_release_hardening.py::FinalCliSurfaceTests::test_public_reproducibility_claims_keep_the_three_boundaries"]
    run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *tests], output / "source", logs, "focused-tests")
    env = output / "installed-wheel"
    run([sys.executable, "-m", "venv", env], output, logs, "venv")
    python = env / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    run([python, "-m", "pip", "install", "--require-hashes", "-r", repository / "requirements/install-tooling.lock"], output, logs, "install-tooling")
    run([python, "-m", "pip", "install", "--require-hashes", "-r", repository / "requirements/action-runtime.lock"], output, logs, "runtime")
    run([python, "-m", "pip", "install", "--no-deps", wheel], output, logs, "wheel")
    run([python, "-m", "pip", "check"], output, logs, "pip-check")
    cli = [python, "-I", "-m", "pipeline"]
    for name, args in (("version", ["--version"]), ("help", ["--help"]), ("doctor", ["doctor", "--format", "text"])):
        run([*cli, *args], output, logs, name)
    identity = run([python, "-I", "-c", "from modules.benchmark_runner import _installed_source_sha256; print(_installed_source_sha256())"], output, logs, "installed-source-identity").strip()
    if not skip_example:
        work = output / "example"
        work.mkdir()
        run([*cli, "example", "run", "--local"], work, logs, "example")
        example, = (work / "metrolith-output/runs").iterdir()
        for command in ("report", "explain", "validate"):
            run([*cli, command, example], work, logs, command)
        code = "import json,sys; from pathlib import Path; from modules.ratchet.semantics import producer_from_manifest; print(json.dumps(producer_from_manifest(json.loads(Path(sys.argv[1]).read_bytes())).to_dict()))"
        producer = json.loads(run([python, "-I", "-c", code, example / "run_manifest.json"], output, logs, "producer"))
    else:
        producer = None
    require(not run(["git", "status", "--porcelain"], repository, logs, "clean-after").strip(), "source changed")
    save(logs / "summary.json", {"source_sha": head, "version": version,
         "installed_source_sha256": identity, "producer": producer,
         "example": "NOT RUN (explicit local option)" if skip_example else "passed",
         "full_engine_suite": "NOT RUN", "upload": "NONE"})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--validation-python", type=Path, required=True)
    parser.add_argument("--skip-example", action="store_true", help="Local packaging-only check; CI always runs the example")
    args = parser.parse_args()
    check(args.output, interpreter_path(args.validation_python), skip_example=args.skip_example)
