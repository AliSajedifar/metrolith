# Reproducibility and output contracts

Byte identity means equal serialized bytes and SHA-256. Measurement-semantic
equality compares the versioned normalized measurement projection. Environment
equivalence compares the interpreter, dependencies, Git, platform, configuration
and acquisition inputs.

Semantic equivalence does not imply byte identity.
Environment reproducibility does not imply measurement equivalence.

## A tiny observed comparison

Two network-free bundled examples were run in separate empty workspaces using
production-PyPI Metrolith 4.0.0 on Windows, CPython 3.13.9, with the same installed
dependencies and default configuration. Both recorded 22 code lines, 2 files,
0 classes/structs and 4 methods/functions with complete core metrics.

| Evidence | First Run | Second Run |
|---|---|---|
| Run ID | `15f6a63ce259` | `43942a0fe0e8` |
| Manifest SHA-256 | `c76242a3bcb3cca1dd75692c2b54a8e1e84507ec1c022b729614800544414a1b` | `8714900ede7aa7247bf2913688150af56565c768935273baa0b15f6c29ffe9c3` |

Their IDs, timestamps and manifest bytes differ. Their versioned measurement
projection hashes are equal:
`09f6b6a5d44ba76e5f14b02f5e5021089f881d66326f662d36a639da4bdef16b`.
This was computed with
`validation.scripts.semantic_projection.measurement_semantic_hash`, which first
admits the Run through the strict reader. It hashes the projection version and
selected semantic fields: subject identity, source/measurement context, contracts,
normalized metrics and inventory. Its separate environment section retains
producer/interpreter/parser/acquisition context but is not part of that hash.

To compare your two Runs without another analysis, replace the quoted paths:

```console
python -c "from pathlib import Path; from validation.scripts.semantic_projection import measurement_semantic_hash as h; print(h(Path('FIRST_RUN'))); print(h(Path('SECOND_RUN')))"
```

Use the installed virtual environment's Python. Equal hashes support this
specific measurement projection, not equality of every artifact, environment,
optional supplement or byte. No arbitrary timestamp/path stripping is used.
The manifest's validation `semantic_sha256` is a different digest boundary;
do not interchange it with this measurement projection hash.

## Standalone output contracts

| Surface | Equality boundary |
|---|---|
| Canonical run bundle | Includes UUID, timestamps, logs, durations, platform evidence and absolute workspace/cache/input/output paths. Whole-directory byte identity is not promised. |
| Measurement-semantic projection | Its versioned semantic section/hash excludes run IDs, clocks and environment evidence. Equal hashes do not prove equal environments or evaluator builds. |
| Policy/check JSON | Stable canonical bytes for the same admitted Run, Policy, Program version and result; invalid-input details may reflect the supplied paths. |
| SARIF projection | Canonical bytes for the same Check result and Program version. Hosted ingestion/annotations are separate service behavior. |
| Direct Revision Diff JSON | Same resolved source states, configuration, contracts and Program version; source/Git failures may change refusal diagnostics. |
| Duplication JSON | Byte-identical only for the same snapshot, requested kinds, configuration, contracts, Program revision and parser/runtime environment. |
| Changed-Code JSON | Byte-identical only for the same two committed Git objects/history, configuration, contracts, Program revision, Git and parser environment. |
| Hotspots JSON | Byte-identical only for the same admitted ledgers, mapped repositories, reachable history, Program revision, Git and runtime environment. Missing history is explicitly unavailable. |
| Wheel/sdist | A recorded filename, size and SHA-256 identifies the verified artifact. Cross-platform repeat-build byte equality requires observation; archive metadata can differ. |

Use `metrolith schema list` and `metrolith schema export --help` for installed
contract versions. `validation.artifact_io.schema_store` validates native and
supported historical documents. `validation.scripts.semantic_projection`
provides the measurement projection. Dossier output remains derived and cannot
replace its authoritative Run or evidence inputs.

CLI machine output is UTF-8; Check JSON/SARIF stdout contains the requested
document. Analyze progress is human output. Check text/JSON with `--output`
writes Check JSON; `--format sarif` writes SARIF. Check output failures exit 2.
Source acquisition and partial-data treatment are described in [USAGE](USAGE.md).

The developer lock is `requirements/release-verification.lock` and the complete
committed-release verifier is `tools/release_verify.py`. Its manifest records
the exact environment, source, artifacts, checks and platform limitations;
its success applies only to those bytes and checks. See [DEVELOPMENT](DEVELOPMENT.md) for
focused tests and builds without private Git history.
