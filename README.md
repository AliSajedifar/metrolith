# Metrolith

**Evidence, not scores.** Metrolith helps repository maintainers and researchers
inspect exact source states and evaluate user-authored Policy. It analyzes Java,
JavaScript/TypeScript, Python and Go, recording inspectable, versioned evidence
for code lines, source files, classes/structs, methods/functions, complexity and
optional maintenance analyses.

## Prerequisites

Use CPython 3.13 (`>=3.13,<3.14`); the tested patch is **3.13.9**, on x86-64
Windows and Ubuntu. **Git must be on PATH for all current analysis acquisition
modes**, including non-Git directory snapshots and the packaged local tutorial.
The analyzed directory does **not** need to be a Git repository. Native
tree-sitter dependencies are installed for Java, JavaScript/TypeScript and Go.

## Install and try the packaged example

The current route is this extracted source tree or an identified local wheel;
public `pip install metrolith` availability is not assumed. Installation downloads
dependencies. The installed local tutorial can then run without network access,
with the Git executable still available.

Start in the **root of the extracted source tree** (the directory containing
`pyproject.toml`). Use an empty sibling environment. On Windows, `python` below
must resolve to your CPython 3.13.9 interpreter; substitute its quoted absolute
path with PowerShell's `&` operator if needed.

PowerShell:

```powershell
python --version
git --version
python -m venv ../metrolith-user
& ../metrolith-user/Scripts/python.exe -m pip install --require-hashes -r requirements/install-tooling.lock
& ../metrolith-user/Scripts/python.exe -m pip install .
$env:PATH = "$((Resolve-Path '../metrolith-user/Scripts').Path);$env:PATH"
New-Item -ItemType Directory -Force ../metrolith-try | Out-Null
Set-Location ../metrolith-try
metrolith --version
metrolith doctor --format text
metrolith example run --local
```

Ubuntu shell, from that same extracted-source root:

```bash
python3.13 --version
git --version
python3.13 -m venv ../metrolith-user
../metrolith-user/bin/python -m pip install --require-hashes -r requirements/install-tooling.lock
../metrolith-user/bin/python -m pip install .
source ../metrolith-user/bin/activate
mkdir -p ../metrolith-try
cd ../metrolith-try
metrolith --version
metrolith doctor --format text
metrolith example run --local
```

PowerShell's PATH change applies only to this session and requires no execution
policy change. Stop if either prerequisite check fails. See [USAGE](docs/USAGE.md)
for the local-wheel route and command-specific refusal codes: missing Git exits
4 from `analyze`/`run`, and 1 from `example run`.

This contiguous excerpt is from the **packaged synthetic example**:

```text
Subject key      example:network-free
Source           directory snapshot (non-Git)
Overall status   complete
Lines of Code       22 (complete)
Source Files        2 (complete)
Classes/Structs     0 (complete)
Methods/Functions   4 (complete)
```

Zero classes is a measured zero. This is a tutorial, not a real-repository
benchmark, architectural ground truth or performance result. Read the printed
Run directory and its `summary.md`; they show the result and measurement states.
Default output is `metrolith-output/runs/` beneath the invocation workspace.

## Analyze your project

In the same configured shell, change to your own source directory and run:

```console
metrolith analyze .
```

Use the newly printed Run path for the next commands:

```console
metrolith report "RUN"
metrolith explain "RUN"
metrolith validate "RUN"
```

`RUN` is a placeholder: replace it with the actual directory printed by analyze,
keeping the quotes around paths with spaces or Unicode. Report creates static
offline HTML at `RUN/report.html`. Per-language details are in
`language_metrics.csv`; JSON/CSV evidence and per-subject fact sheets remain in
the Run. Analysis does not modify the source. The separately implemented hosted
Story/Explorer website is not installed by pip.

## Evidence and limits

Directory, working-tree, tracked-only and exact-revision inputs have different
source identities. Complete, partial, unavailable, not requested and verified-zero
measurements are distinct. An analysis exit of 0 can include explicitly partial
measurements; inspect the Run and metric statuses. Metrolith does not infer
architecture labels or supply universal quality thresholds.

Use `metrolith check` to evaluate your Policy and `metrolith dossier` to compose
admitted evidence. Local checks are labeled **LOCAL — UNPROTECTED**. Protected
admission requires externally authenticated evaluator, Policy and supplied-evidence
provenance. A successful local check does not establish organization trust.

Byte identity, measurement-semantic equality and environment equivalence are
separate claims. Runs retain timestamps, UUIDs and host provenance. Read
[REPRODUCIBILITY](docs/REPRODUCIBILITY.md) before comparing outputs.

## Documentation and development

- [USAGE](docs/USAGE.md): source modes, scope, Policy, Ratchet, exports and the full command/version reference.
- [Examples](examples/README.md): the packaged local sample and optional frozen-repository variants.
- [DEVELOPMENT](docs/DEVELOPMENT.md): complete pinned build and separate Twine validation environments.
- [Release checklist](docs/PUBLIC_RELEASE_CHECKLIST.md): release checks and owner decisions.
- [Metric](docs/METRIC_CONTRACT_V3.md), [Complexity](docs/COMPLEXITY_CONTRACT_V2.md) and [Artifact contracts](docs/ARTIFACT_CONTRACTS_V35.md): normative definitions and historical compatibility.

The pinned developer environment is [requirements/release-verification.lock](requirements/release-verification.lock).
[tools/release_verify.py](tools/release_verify.py) is the complete committed-release
verification entry point; its full suite is a separate, deliberate operation.

The distribution is `metrolith`, version **4.0.0, unreleased**. The deprecated
`archlens` and `arch-bench` commands, `archlens_json` import, legacy environment
keys and versioned format identifiers remain for compatibility.

## License, citation and contact

Licensed under [Apache-2.0](LICENSE). Maintainer: Ali Sajedifar
(`its.alisajedifar@gmail.com`).
For software citation, use [CITATION.cff](CITATION.cff).

The [source repository](https://github.com/AliSajedifar/metrolith) is private;
repository access is required for its [documentation](https://github.com/AliSajedifar/metrolith/blob/main/docs/USAGE.md)
and [issues](https://github.com/AliSajedifar/metrolith/issues).
Version 4.0.0 remains unreleased and has not been uploaded to PyPI.
