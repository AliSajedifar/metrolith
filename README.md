<a name="metrolith"></a>
<h1>
  <img src="https://raw.githubusercontent.com/AliSajedifar/metrolith/af49510a0d44dd14cb18e11ec86aaee0a3bfa57c/docs/assets/Logo.png" width="64" height="64" align="absmiddle" alt="">
  Metrolith
</h1>

[![PyPI](https://img.shields.io/pypi/v/metrolith)](https://pypi.org/project/metrolith/)
[![Build & smoke](https://github.com/AliSajedifar/metrolith/actions/workflows/package-smoke.yml/badge.svg?branch=main)](https://github.com/AliSajedifar/metrolith/actions/workflows/package-smoke.yml?query=branch%3Amain)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache--2.0-blue.svg)](https://github.com/AliSajedifar/metrolith/blob/main/LICENSE)

**Evidence, not scores.**

Metrolith is a local-first, multi-language static-analysis CLI. It produces versioned code metrics and offline reports so you can investigate a source tree, inspect complex functions, and compare changes between revisions. Optional duplication and history analyses provide additional evidence; you choose which analyses and rules to run.

Inspect what was measured, what was excluded, and where results are partial or unavailable before deciding what to change.

**Languages:** Python, Java, JavaScript/TypeScript and Go. Parsing and measurement coverage are language-specific.

[Try online](https://metrolith.dev/#try) · [Explore a recorded demo](https://metrolith.dev/demo) · [Install from PyPI](https://pypi.org/project/metrolith/) · [Read the guide](https://github.com/AliSajedifar/metrolith/blob/main/docs/USAGE.md)

[![Recorded hosted Story for ambient-code/platform, showing source metrics, language composition, and partial duplication coverage](https://raw.githubusercontent.com/AliSajedifar/metrolith/main/docs/assets/recorded-story.png)](https://metrolith.dev/demo)

*The separately hosted companion's recorded Story/Explorer, using historical evidence for `ambient-code/platform`. This is not the offline HTML report produced by `metrolith report`, or a fresh PyPI execution. The [walkthrough](https://github.com/AliSajedifar/metrolith/blob/main/docs/RECORDED_WALKTHROUGH.md) explains the original 3.8.0 producer and later evaluator provenance despite the viewer's 4.0.0 heading.*

## Three questions to start with

- **What is in this source tree?** Inspect languages, files, code size, classes/structs and methods/functions alongside inclusion reasons and explicit measurement coverage.
- **Where should I look more closely?** Read callable complexity, then request lexical or structural duplication evidence separately. Their populations can overlap; their group counts are not a combined defect total.
- **What changed between revisions?** Use revision Diff or existing-Run comparisons, and Changed Code for explicit base/head revisions. Evaluate a Policy of rules you select; measurements do not infer architecture quality.

## Quickstart from PyPI

Install in a fresh virtual environment, check the environment, then run a tiny bundled example. **CPython 3.13 is required** (`>=3.13,<3.14`); **3.13.9** is the verified patch on Windows and Ubuntu. **Git must be on PATH**, including for local directory snapshots. Your analyzed directory need not be a Git repository.

Start in an empty working directory outside your project's source. Use an unused `metrolith-user` environment directory. Installation can download dependencies; the bundled local example is network-free afterward.

**PowerShell** — `python` must resolve to CPython 3.13:

```powershell
python --version
git --version
python -m venv "metrolith-user"
& "./metrolith-user/Scripts/python.exe" -m pip install "metrolith==4.0.0"
$env:PATH = "$((Resolve-Path './metrolith-user/Scripts').Path);$env:PATH"
metrolith doctor --format text
metrolith example run --local
```

**Ubuntu / POSIX shell:**

```bash
python3.13 --version
git --version
python3.13 -m venv "metrolith-user"
"./metrolith-user/bin/python" -m pip install "metrolith==4.0.0"
. "./metrolith-user/bin/activate"
metrolith doctor --format text
metrolith example run --local
```

Stop if a prerequisite check or installation fails. PowerShell changes PATH only for this session; no execution-policy change is needed.

Next, in the same configured shell, change to your project's source directory and run:

```console
metrolith analyze . --workspace "../metrolith-results"
```

Choose an output workspace outside the analyzed source. The command prints a **Run directory** under `WORKSPACE/metrolith-output/runs/`. `metrolith analyze .` also works; its default workspace is `METROLITH_HOME` or the current directory. Analysis reads source and writes outputs/cache state in the workspace. See [source modes and locations](https://github.com/AliSajedifar/metrolith/blob/main/docs/USAGE.md#select-the-source-state).

## Read your first Run

Open the printed Run directory's `summary.md`. The bundled example measures **22 code lines, 2 source files, 0 classes/structs and 4 methods/functions**, with complete core metrics. Zero classes is a measured result.

Replace `RUN` below with the printed directory, keeping quotes:

```console
metrolith report "RUN"
metrolith explain "RUN"
metrolith validate "RUN"
```

`report` writes the derived offline view at `RUN/report.html`; open it in a browser. `explain` describes recorded scope and availability. `validate` checks required artifacts and cross-file consistency; it does not rerun analysis or prove source authenticity. Keep the Run's JSON/CSV evidence and fact sheets with the report.

Completion does not mean every measurement is complete. Read **partial**, **unavailable**, **not requested**, and **not applicable** separately from measured zero. Optional analyses are not all executed by `analyze`.

## Evidence and boundaries

Directory snapshots, working snapshots and exact Git revisions record different source identities. A working snapshot is not proof of committed bytes. A **Policy** contains your rules; a **Ratchet** compares selected measurements with a retained baseline and chosen tolerances. Neither supplies universal quality thresholds. Local PASS does not establish protected organizational approval.

`pip install metrolith` installs the CLI and offline reporting, not the hosted web application. Local source processing and the website's server-side repository processing have different privacy and feasibility boundaries; see the [website guide](https://metrolith.dev/guide).

## Choose your next path

- **Using Metrolith:** [Documentation map](https://github.com/AliSajedifar/metrolith/blob/main/docs/README.md), [Usage and full command reference](https://github.com/AliSajedifar/metrolith/blob/main/docs/USAGE.md), [Examples](https://github.com/AliSajedifar/metrolith/blob/main/examples/README.md).
- **Understanding evidence:** [Architecture](https://github.com/AliSajedifar/metrolith/blob/main/docs/ARCHITECTURE.md), [recorded walkthrough](https://github.com/AliSajedifar/metrolith/blob/main/docs/RECORDED_WALKTHROUGH.md), [reproducibility](https://github.com/AliSajedifar/metrolith/blob/main/docs/REPRODUCIBILITY.md).
- **Contributing:** [Development](https://github.com/AliSajedifar/metrolith/blob/main/docs/DEVELOPMENT.md) preserves the hash-locked source setup. Build & smoke checks Ubuntu packaging and a tiny installed example; it is not full engine qualification.
- **Maintaining releases:** [Publication record and procedure](https://github.com/AliSajedifar/metrolith/blob/main/docs/PYPI_PUBLISHING.md). Deliberate full qualification uses `tools/release_verify.py` and `requirements/release-verification.lock`.

The deprecated `archlens`/`arch-bench` commands and frozen ArchLens format identifiers remain compatibility contracts.

## License, citation and contact

[Apache-2.0](https://github.com/AliSajedifar/metrolith/blob/main/LICENSE) · [Software citation](https://github.com/AliSajedifar/metrolith/blob/main/CITATION.cff) · [Issues](https://github.com/AliSajedifar/metrolith/issues)

Maintainer: **Ali Sajedifar** — `its.alisajedifar@gmail.com`.
