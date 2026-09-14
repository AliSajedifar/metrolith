# Development

## First contribution

Install from PyPI if you only want to use the tool. For a contribution, clone the
public repository, create a topic branch and use the locked setup below.
Start with a small documentation example or a focused bug reproduction. Find
the owning module in [Architecture](ARCHITECTURE.md), read its contract, make the
smallest change and run the specific affected test module. Describe observed
before/after behavior and exact checks in your pull request. Preserve fixtures
and independently versioned contracts; do not regenerate expectations just to pass.

Module map: `pipeline.py` / `modules/cli` dispatch; `acquisition.py` / `local_source.py`
select source; `inventory.py` accounts for scope; `source_frontend.py` and
`core_metrics.py` parse/measure; `callable_analysis` handles callable metrics;
`validation/artifact_io` reads evidence; `report_view.py`, `policy` and `ratchet`
consume it. See [Architecture](ARCHITECTURE.md) for implementation links.

## Build & smoke scope

[Build & smoke](../.github/workflows/package-smoke.yml) is a secretless Ubuntu
24.04 / CPython 3.13.9 job: standalone sdist and wheel build, strict Twine,
`tests/test_release_documentation.py`, `tests/test_pypi_preparation.py`, and the two
documentation/navigation checks in `FinalCliSurfaceTests`, then an
isolated wheel installation, pip check, version/help/doctor and one packaged
local example with report/explain/validate. It has a 20-minute timeout and read-only
repository permissions. It uploads nothing and has no publishing privileges.
Normal branch pushes and pull requests run it; manual dispatch supports a bounded
check after a `[skip ci]` documentation commit. Its main-branch badge follows
current workflow state, not a permanently pinned successful commit.

This job does not run the full engine suite, Windows qualification, performance
benchmarks or protected policy admission. The existing `ci.yml` remains the full
Windows/Ubuntu release verifier. Use full CI deliberately at a release boundary;
a skipped full CI is **NOT RUN**, not passed. Do not dispatch the publisher for
documentation checks: its frozen first-release identities no longer describe
changed packaged documentation. See [publishing](PYPI_PUBLISHING.md).

## Reproducible source setup

Use CPython 3.13.9 and Git on PATH on x86-64 Windows or Ubuntu. Start each block
in the root of a **disposable extracted source copy** containing `pyproject.toml`.
Build/editable installation creates `build/` and egg-info there. Use empty sibling
venvs with no system-site-packages. The [beginner setup](../README.md) installs the
runtime; this guide separately provisions build/test and artifact-validation tools.
No shell activation, global tool installation or PowerShell policy change is needed.

## Maintainer build/test environment

PowerShell (`python` must be your CPython 3.13.9 executable; a quoted absolute
interpreter path with `&` also works):

```powershell
python --version
git --version
python -m venv ../metrolith-dev
& ../metrolith-dev/Scripts/python.exe -m pip install --require-hashes -r requirements/release-verification.lock
& ../metrolith-dev/Scripts/python.exe -m pip install --no-build-isolation --no-deps -e .
& ../metrolith-dev/Scripts/python.exe -m pip check
& ../metrolith-dev/Scripts/python.exe -m build --no-isolation --outdir ../metrolith-dist
```

Ubuntu:

```bash
python3.13 --version
git --version
python3.13 -m venv ../metrolith-dev
../metrolith-dev/bin/python -m pip install --require-hashes -r requirements/release-verification.lock
../metrolith-dev/bin/python -m pip install --no-build-isolation --no-deps -e .
../metrolith-dev/bin/python -m pip check
../metrolith-dev/bin/python -m build --no-isolation --outdir ../metrolith-dist
```

This intentionally uses the complete hash-verified build environment with
`--no-isolation`. It does not rely on `PIP_CONSTRAINT` leaking into an isolated
backend. Ordinary source installation still uses the exact backend pin in
`pyproject.toml`. Do not pass the full runtime/test lock as build constraints.

Run only tests affected by your change. For the focused documentation checks,
from the same source root:

```powershell
& ../metrolith-dev/Scripts/python.exe -m pytest -p no:cacheprovider tests/test_release_documentation.py
```

```bash
../metrolith-dev/bin/python -m pytest -p no:cacheprovider tests/test_release_documentation.py
```

Fixtures may deliberately contain invalid syntax, excluded directory names or
legacy formats. Do not compile or rename them indiscriminately. Historical
reader fixtures and conformance expectations retain their original bytes and
identity manifests. Tests use independent oracles where the metric contract
requires them; current producer output is not an oracle.

`pipeline.py` supplies the CLI; `modules/` contains the engine. `config/` and
`examples/` supply installed data. `validation/artifact_io`, `resources`,
`scripts` and `conformance` provide runtime readers, schemas, validators and
packaged conformance resources. Other retained validation directories are
maintainer test corpora/reference implementations, not runtime dependencies.
Optional external reference adapters require their separately installed tools;
the regular engine does not acquire them.

## Release engineering: separate artifact-validation environment

Stay in the same extracted-source root. The complete pinned Twine closure in
[artifact-validation.lock](../requirements/artifact-validation.lock) includes
the platform-specific Windows/Linux dependencies. It is not a runtime extra.

PowerShell:

```powershell
python -m venv ../metrolith-validate
& ../metrolith-validate/Scripts/python.exe -m pip install --require-hashes -r requirements/artifact-validation.lock
& ../metrolith-validate/Scripts/python.exe -m pip check
& ../metrolith-validate/Scripts/python.exe -m twine check --strict ../metrolith-dist/metrolith-4.0.0-py3-none-any.whl ../metrolith-dist/metrolith-4.0.0.tar.gz
```

Ubuntu:

```bash
python3.13 -m venv ../metrolith-validate
../metrolith-validate/bin/python -m pip install --require-hashes -r requirements/artifact-validation.lock
../metrolith-validate/bin/python -m pip check
../metrolith-validate/bin/python -m twine check --strict ../metrolith-dist/metrolith-4.0.0-py3-none-any.whl ../metrolith-dist/metrolith-4.0.0.tar.gz
```

Strict Twine checks metadata and README rendering; it does not scan for
vulnerabilities. Advisory scanning is a separate maintainer operation requiring
its own explicitly provisioned scanner environment and dated database evidence.
Neither scanning nor Twine uploads the package in this procedure.

An extracted sdist is independently buildable without a Git repository or
private sibling inputs, using the same locked build steps. Install the exact
resulting wheel in a fresh user environment and run `pip check`, version/help/
doctor, the packaged tutorial, schemas and affected installed tests outside the
source copy. Analysis/tutorial execution still requires Git on PATH.

For a committed release, `tools/release_verify.py --output EXTERNAL_PATH` is
the complete verification entry point. It requires a clean committed tree,
the reviewed lock and supported interpreter; it includes the full engine test
suite, packaging and installed conformance checks. Invoke it deliberately at
the release boundary. It is not the focused edit/test command. Applicable
Windows/Ubuntu CI runs this verifier; local tests do not claim hosted CI success.

Normative references: [METRIC_CONTRACT_V3](METRIC_CONTRACT_V3.md), [COMPLEXITY_CONTRACT_V1](COMPLEXITY_CONTRACT_V1.md),
[COMPLEXITY_CONTRACT_V2](COMPLEXITY_CONTRACT_V2.md), [ARTIFACT_CONTRACTS_V35](ARTIFACT_CONTRACTS_V35.md),
[ANALYSIS_SCOPE_HASH_V2](ANALYSIS_SCOPE_HASH_V2.md), [REPORT_VIEW_CONTRACT_V1](REPORT_VIEW_CONTRACT_V1.md), and the three
`DUPLICATION_*_CONTRACT_V1.md` files in this directory. The earlier metric
contract remains a compatibility reference.

Optional differential reference environments are selected with
`ARCHLENS_DIFFVAL_REFS` (default `.metrolith-reference`); the Windows reference
JDK location can be set with `METROLITH_REFERENCE_JDK`. Existing external-tool
version checks still apply. Missing optional tools/evidence are explicit skips,
not successful comparisons. No external research acquisitions are shipped.
