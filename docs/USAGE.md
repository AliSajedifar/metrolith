# Using Metrolith locally

## Installation and prerequisites

Follow the [README setup](../README.md) from an extracted source root. CPython
`>=3.13,<3.14` is required; tested hosts use 3.13.9 on x86-64 Windows and Ubuntu.
Git must be on PATH for all current acquisition modes, including non-Git
directory snapshots and `metrolith example run --local`. The analyzed directory
need not be a Git repository. The installed local tutorial needs no network
after installation; Git availability is a separate prerequisite.

For an identified local wheel, use the same fresh venv and the README's hashed
install-tooling setup, replacing its `pip install .` step with the appropriate
command below. Replace `WHEEL` with the exact owner's wheel file path and retain
quotes. These commands start at the extracted-source root; no global Twine or
developer tools are required.

```powershell
& ../metrolith-user/Scripts/python.exe -m pip install "WHEEL"
```

```bash
../metrolith-user/bin/python -m pip install "WHEEL"
```

Public package-index availability is not assumed. Installation can download
dependencies; only subsequent local tutorial execution is network-free. Source
and sdist include this guide; the wheel does not install the full docs tree.

## Command-specific refusal

`analyze` and `run`: exit 0 means completed (possibly partial), 1 analysis failure,
2 invalid source/usage, and **4 environment preflight refusal**, including missing
Git. `example run` retains its wrapper contract: 0 completed (possibly partial),
**1 failure including preflight refusal**, and 2 invalid command-line usage.
Preflight refusal prints the reason without a traceback or a successful Run
summary. No acquisition begins on a global preflight refusal. Policy `check`
PASS/FAIL codes are separate and unchanged; consult each command's `--help`.

Throughout this guide `RUN` means the actual printed Run directory, not the
literal word RUN. Quote it (for example `metrolith explain "RUN"`) when it contains
spaces or Unicode. Read `summary.md`, then use report/explain/validate.

## Select the source state

```console
metrolith analyze PATH
metrolith analyze PATH --tracked-only
metrolith analyze PATH --revision FULL_COMMIT_SHA
```

`PATH` can be a directory or local Git repository. A non-Git directory is a
directory snapshot. A Git working snapshot includes tracked and non-ignored
untracked files; `--tracked-only` restricts that snapshot to tracked paths.
These modes capture current working bytes. Their recorded HEAD is context,
not proof that dirty files equal committed bytes. `--revision` resolves and
materializes an exact committed revision in controlled storage. Git sources
are not checked out or reset in place.

`--subject-key` declares a shared logical identity when a local clone and a
remote source represent the same subject. `--expected-language` records an
optional language expectation. `--architecture-type` is supplied metadata,
not an inferred classification.

`--workspace` chooses the invocation workspace. Its default is `METROLITH_HOME`
or the current directory. Outputs go under `metrolith-output/runs/`; caches
and temporary worktrees go under `.metrolith/`. `--output-root`, `--cache-root`
and `--temp-dir` override those locations. Keep generated outputs separate
from source and retained authoritative inputs.

## Scope and measurement states

The versioned exclusion policy in `config/exclusions.v1.json` defines source
selection. Inventories preserve inclusion/exclusion reasons and parser/dialect
diagnostics. Git symlinks are excluded; missing parser support or failed source
preparation is explicit. Use `metrolith explain RUN` for the selected scope.

A complete file inventory does not guarantee complete measurements. Syntax
errors may yield partial results and `completed_with_errors`. A verified zero
means the metric was measured completely and counted zero. Unavailable or
not-applicable values must not be read as zero. Check `run_status.json`, per-
metric statuses, and the report before using numbers. Policy failure is
separate from Run integrity: a valid completed Run can fail your threshold.

## Author and evaluate Policy

This example fails when source-file count is greater than 10. The threshold
is illustrative; choose it yourself.

```console
metrolith policy metrics
metrolith policy init --output policy.json --metric repository.source_files --operator gt --threshold 10 --severity violation
metrolith policy validate policy.json
metrolith check RUN --policy policy.json --format json --output check.json
metrolith dossier RUN --policy-result check.json --format markdown --output dossier.md
```

Check exits 0 for PASS, 1 for a Policy violation and 2 for configuration,
evaluation or output failure. Policy initialization uses `partial_data=evaluate`;
edit `options.partial_data` to `not_evaluable` to refuse partial observations.
`on_not_evaluable=fail` governs observations that cannot be evaluated.

Optional evidence is supplied through `--hotspots` and `--duplication`:

```console
metrolith check RUN --policy policy.json --hotspots hotspots.json --duplication duplication.json --format json --output check.json --overwrite
```

Evidence must pass its schema and match the Run's subject, revision/snapshot,
scope and producer contracts. Missing or mismatched evidence is not a pass.
Local checks are labeled **LOCAL — UNPROTECTED**. Protected admission
requires externally authenticated evaluator, Policy and supplied-evidence
provenance. A successful local check does not establish organization trust.

A Dossier is derived and non-authoritative; only admitted supplements
contribute. The repository's `.github/actions/metrolith-check` includes the
protected-mode integration. Example workflows are in `docs/examples/`.

## Baseline and Ratchet

```console
metrolith baseline capture RUN --rules examples/ratchet-rules.json --output baseline.json
```

Capture requires an exact eligible revision and reproducible evaluator
provenance, and creates `baseline.json.source-run/`. Keep that immutable sidecar
with the baseline. Check requires an externally supplied SHA-256 with
`--baseline-sha256`; local capture does not establish protected trust.

The current CLI Ratchet ancestry lookup requires URL-backed subjects and their
existing Git sources in the current Run's runner cache. Local
`analyze --revision` alone has no repository mapping on `check`: it refuses
with `revision_source_missing`/exit 2 rather than fabricating a comparison.
The installed rules example is available via `importlib.resources.files('examples')`.

## History and exports

Hotspots uses available reachable Git history and explicit source mapping.
Changed Code accepts explicit `--base` and `--head` revisions. Use the actual
subcommand help for mapping and source arguments; no recent-N History mode is
provided. Offline execution requires the selected Git objects already present.

The engine produces JSON/CSV Run evidence, Markdown summaries/fact sheets,
static HTML reports, explain text/JSON, standalone Duplication/Hotspot/
Changed-Code outputs, Dossier Markdown/JSON and Check JSON/SARIF. It supplies
no hosted service or runtime interactive Explorer.

Use fresh export paths. Where offered, `--overwrite` replaces an eligible
derived output or ordinary external file, never an explicitly consumed input,
an authoritative Run file, source-language input, Git administration or an
alias of a protected file. Check disallows every destination inside its input
Run. Report's normal `RUN/report.html` is an eligible derivative.

For discoverable source/Run trees, other exporters admit existing derivatives
using bounded exporter-specific signatures. A generic `# Metrolith` heading or
HTML doctype is insufficient. Signature recognition is not cryptographic
authorship proof. Historical Runs with unavailable source locators cannot
protect an undiscoverable original source tree; keep inputs separately and
retain backups. No guarantee against arbitrary concurrent filesystem changes
is implied.

## Compatibility

Use `metrolith` for new commands. `archlens` and `arch-bench` remain warning-
emitting aliases. Canonical `METROLITH_*` environment variables accept supported
`ARCHLENS_*`/`ARCH_BENCH_*` fallbacks; conflicting values refuse. Legacy workspace
state is not automatically merged into the new `.metrolith` workspace.

JSON format discriminators, schema `$id`/`$ref` values, the `archlens_json`
module, frozen metric names and historical fixtures retain their exact
identifiers. Renaming them would require a versioned compatibility migration.

## Command and component reference

| Component | Version |
|---|---:|
| Metrolith | 4.0.0 |
| Metric Contract | 3.0.0 |
| Complexity Contract | 2.0.0 |
| Exclusion Policy | 1.5.0 |
| Inventory Schema | 1.7.0 |
| Artifact Schema | 1.12.0 |
| Check Result | 1.4.0 |

## Commands

Use `metrolith COMMAND --help` for exact arguments and exit codes.

| Command | Purpose |
|---|---|
| `metrolith analyze` | Analyze one local source state. |
| `metrolith report` | Render offline HTML from a Run. |
| `metrolith explain` | Explain scope, exclusions and availability. |
| `metrolith doctor` | Check runtime and parser support. |
| `metrolith duplication` | Measure lexical or structural duplication. |
| `metrolith hotspots` | Join Run complexity with available Git history. |
| `metrolith changed` | Review changes between explicit Git revisions. |
| `metrolith policy` | List metrics; author, validate or evaluate Policy. |
| `metrolith check` | Evaluate Policy and optional Ratchet evidence. |
| `metrolith baseline` | Capture a baseline and its source-Run sidecar. |
| `metrolith trust` | Create provenance receipts using declared authority. |
| `metrolith validate` | Validate a Run's structure or semantics. |
| `metrolith compare` | Compare existing Run evidence. |
| `metrolith diff` | Compare source revisions or existing Runs. |
| `metrolith reproduce` | Inspect or execute a declared reproduction plan. |
| `metrolith dossier` | Compose a derived Markdown/JSON review document. |
| `metrolith schema` | List or export installed schemas. |
| `metrolith run` | Analyze explicit CSV repository inputs. |
| `metrolith audit` | Run repository acquisition and inventory audits. |
| `metrolith migrate-input` | Convert filename-labeled legacy TXT inputs. |
| `metrolith perf` | Record and compare performance evidence. |
| `metrolith example` | Run packaged local or frozen-revision examples. |
| `metrolith menu` | Open the interactive command menu. |


See [examples](../examples/README.md), [development](DEVELOPMENT.md),
[reproduction](REPRODUCIBILITY.md), [artifact contracts](ARTIFACT_CONTRACTS_V35.md)
and [JavaScript compatibility](JAVASCRIPT_COMPATIBILITY_FALLBACK.md).
