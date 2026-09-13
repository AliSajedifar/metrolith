# Reproducibility and output contracts

Byte identity means equal serialized bytes and SHA-256. Measurement-semantic
equality compares the versioned normalized measurement projection. Environment
equivalence compares the interpreter, dependencies, Git, platform, configuration
and acquisition inputs.

Semantic equivalence does not imply byte identity.
Environment reproducibility does not imply measurement equivalence.

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
