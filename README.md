<a name="metrolith"></a>
<h1>
  <img src="https://raw.githubusercontent.com/AliSajedifar/metrolith/af49510a0d44dd14cb18e11ec86aaee0a3bfa57c/docs/assets/Logo.png" width="64" height="64" align="absmiddle" alt="">
  Metrolith
</h1>

**Evidence, not scores.**

Metrolith helps repository maintainers and researchers understand their code and evaluate rules they choose, with measurements they can inspect.

**Languages:** Java, JavaScript/TypeScript, Python and Go.

It records versioned evidence for code lines, source files, classes/structs, methods/functions and complexity. Maintenance analyses are optional.

## Prerequisites

**Requires CPython 3.13.** Release verification is currently performed with **3.13.9 on x86-64 Windows and Ubuntu**.

**Git must be on PATH.** The current source-acquisition workflow requires it even for directory snapshots. Your source directory does **not** need to be a Git repository.

## Install

> Install from a source checkout or a local wheel using the instructions below.

Start in the downloaded or cloned **source root** (containing `pyproject.toml`). Use CPython 3.13 and an **unused sibling environment directory**; stop if either prerequisite check fails. The commands below install Metrolith and its dependencies, including native tree-sitter parsers, and make `metrolith` available in the current shell. Installation can download dependencies.

**PowerShell** — `python` must resolve to CPython 3.13:

```powershell
python --version
git --version
python -m venv ../metrolith-user
& ../metrolith-user/Scripts/python.exe -m pip install --require-hashes -r requirements/install-tooling.lock
& ../metrolith-user/Scripts/python.exe -m pip install .
$env:PATH = "$((Resolve-Path '../metrolith-user/Scripts').Path);$env:PATH"
```

**Ubuntu shell:**

```bash
python3.13 --version
git --version
python3.13 -m venv ../metrolith-user
../metrolith-user/bin/python -m pip install --require-hashes -r requirements/install-tooling.lock
../metrolith-user/bin/python -m pip install .
source ../metrolith-user/bin/activate
```

PowerShell's PATH change is session-only; no execution-policy change is needed. See [Usage](docs/USAGE.md) for local-wheel installation and troubleshooting.

Check the installed environment:

```console
metrolith doctor --format text
```

## Analyze your project

In the same configured shell, change to your project's source directory and run:

```console
metrolith analyze .
```

A **Run directory** holds the results and evidence for an analysis; the command prints its path. By default, Runs are written under `metrolith-output/runs/` in the invocation workspace (`METROLITH_HOME` or the current directory). Analysis leaves analyzed source files unchanged; it writes generated outputs and cache/workspace state. See [source and output locations](docs/USAGE.md#select-the-source-state) for overrides.

## Inspect the results

Open the printed Run directory's `summary.md` for the summary and measurement states.

Replace `RUN` below with that printed directory. Keep the quotes around the path.

| Command | Purpose |
|---|---|
| `metrolith report "RUN"` | Build a static offline HTML view at `RUN/report.html`. |
| `metrolith explain "RUN"` | Explain recorded diagnostics and measurement availability. |
| `metrolith validate "RUN"` | Check required artifacts and cross-file measurement consistency. |

Validation checks recorded evidence; it does not rerun analysis or establish repository authenticity. The HTML report is a derived view. The Run also contains JSON/CSV evidence, per-subject fact sheets and per-language details in `language_metrics.csv`.

## Optional: verify your installation

From an empty working directory, this command runs the packaged example without network access. Git must remain available.

```console
metrolith example run --local
```

Recorded output from the bundled synthetic example:

```text
Subject key      example:network-free
Source           directory snapshot (non-Git)
Overall status   complete
Lines of Code       22 (complete)
Source Files        2 (complete)
Classes/Structs     0 (complete)
Methods/Functions   4 (complete)
```

Zero classes is a measured result, not missing data. This example illustrates the output; it is not a benchmark. Use its newly printed Run path with the inspection commands above.

## Evidence and limits

**Source state.** Directory snapshots, Git working snapshots, tracked-only snapshots and exact Git revisions have different source identities. Each Run records its source identity; a working snapshot is not proof of committed bytes.

**Measurement status.** Complete, partial, unavailable, not requested and measured zero are different outcomes. A successful analysis can contain partial measurements; inspect the recorded statuses rather than relying on the exit code alone.

**Your rules.** A *policy* is a set of measurement rules you provide. `metrolith check` evaluates it; Metrolith does not supply universal quality thresholds or infer architecture labels. `metrolith dossier` combines accepted evidence into a report. A local PASS does not establish organizational approval. See [Policy and trust requirements](docs/USAGE.md#author-and-evaluate-policy) for these workflows.

See [Reproducibility](docs/REPRODUCIBILITY.md) for source identity, output comparisons and environment limitations. The separately implemented hosted website is not installed by this package.

## Documentation and development

- [Usage](docs/USAGE.md): source modes, policies, Ratchet, safe exports and the full command/version reference.
- [Examples](examples/README.md): local samples and optional repository examples.
- [Development](docs/DEVELOPMENT.md): pinned build, test and artifact-validation environments, using [requirements/release-verification.lock](requirements/release-verification.lock) and [tools/release_verify.py](tools/release_verify.py) for deliberate full release verification.
- [Release checklist](docs/PUBLIC_RELEASE_CHECKLIST.md): maintainer publication checks.

Technical references: [metrics](docs/METRIC_CONTRACT_V3.md), [complexity](docs/COMPLEXITY_CONTRACT_V2.md) and [artifact contracts](docs/ARTIFACT_CONTRACTS_V35.md).

The package is `metrolith`. The deprecated `archlens` and `arch-bench` commands, `archlens_json` import, legacy configuration keys and format identifiers remain for compatibility.

## License, citation and contact

[Apache-2.0](LICENSE) · [Software citation](CITATION.cff)

Maintainer: **Ali Sajedifar** — `its.alisajedifar@gmail.com`.

[Source](https://github.com/AliSajedifar/metrolith) · [Issues](https://github.com/AliSajedifar/metrolith/issues)
