# Using Metrolith locally

## First successful analysis

Follow the [PyPI quickstart](../README.md#quickstart-from-pypi): no source checkout,
lockfile or build tools are needed. Use a fresh virtual environment and keep it
outside the source you analyze. CPython `>=3.13,<3.14` is required; 3.13.9 is
verified on Windows and non-root Ubuntu. Git must be on PATH even for non-Git
directory snapshots. Installation downloads dependencies; the installed local
tutorial needs no network afterward.

1. Run `metrolith doctor --format text`, then `metrolith example run --local`.
2. Open `summary.md` in the printed Run directory. The tiny Python/JavaScript
   sample records 22 code lines, 2 source files, 0 classes/structs and 4
   methods/functions. Inspect each metric's status as well as its value.
3. Replace `RUN` with that directory: `metrolith report "RUN"` creates
   `RUN/report.html`; open it in your browser. `metrolith explain "RUN"` describes
   scope and availability. `metrolith validate "RUN"` checks the recorded bundle,
   not a new analysis or proof of authenticity.
4. Change to your project's directory and run
   `metrolith analyze . --workspace "../metrolith-results"`, selecting a workspace
   outside the source. Read the new printed Run path; do not reuse the example's.

A **Run** is one analysis's directory of evidence. Its HTML report is derived.
The separately hosted [Story/Explorer](https://metrolith.dev/demo) is not installed
by the package; see the [recorded walkthrough](RECORDED_WALKTHROUGH.md).

## Advanced installation

For editable source work or fully locked environments, use [Development](DEVELOPMENT.md).
Do not point an end-user index install at a source-root lockfile. For an identified
local wheel, create the same fresh venv as the README, then replace the index
installation command with `python -m pip install "WHEEL"` using that venv's
Python and the exact file path. Dependencies may still require network access.
The wheel installs the beginner example; the full docs tree is in source/sdist
and on GitHub. No global Twine or global PowerShell policy change is required.

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

## Optional investigations

These three recipes were tested with production-PyPI **4.0.0** on Windows
PowerShell and non-root Ubuntu, both with CPython 3.13.9. Use the configured
environment from the quickstart (`python` and `metrolith` on PATH), Git, and a
fresh empty working directory outside your own repository. Stop on failed setup
commands. All paths below are literal except the explicitly marked Run placeholders.

### Shared input: a disposable two-revision repository

Run this block in either shell. It creates only `recipe-source`; the temporary
commit identity applies to these two example commits, not your Git configuration.

```console
python -c "from pathlib import Path; p=Path('recipe-source'); p.mkdir(); s='def total(values):\n    result = 0\n    for value in values:\n        if value > 0:\n            result += value * 2\n        else:\n            result -= value\n    return result\n'; (p/'first.py').write_text(s, encoding='utf-8'); (p/'second.py').write_text(s, encoding='utf-8')"
git init "recipe-source"
git -C "recipe-source" add first.py second.py
git -C "recipe-source" -c user.name="Recipe Example" -c user.email="recipe@example.invalid" commit -m "Add two small functions"
python -c "from pathlib import Path; p=Path('recipe-source/first.py'); p.write_text(p.read_text(encoding='utf-8')+'\ndef twice(value):\n    return value * 2\n', encoding='utf-8')"
git -C "recipe-source" add first.py
git -C "recipe-source" -c user.name="Recipe Example" -c user.email="recipe@example.invalid" commit -m "Add a helper"
```

Resolve the references to immutable commit IDs now; later commands use these
IDs even if a branch moves. Your IDs will differ because commit metadata differs.

PowerShell:

```powershell
$base = git -C "recipe-source" rev-parse "HEAD~1"
$head = git -C "recipe-source" rev-parse HEAD
Write-Output "base=$base head=$head"
```

Ubuntu / POSIX:

```bash
base=$(git -C "recipe-source" rev-parse "HEAD~1")
head=$(git -C "recipe-source" rev-parse HEAD)
echo "base=$base head=$head"
```

### A. Investigate duplication

**Question:** Does this revision contain admitted lexical or structural repetition?
Use the local repository and resolved `$head` above. Both kinds can be requested
in one invocation; `--kind lexical` or `--kind structural` selects one population.

```console
metrolith duplication "recipe-source" --revision "$head" --kind all --format json --output "duplication.json"
python -m json.tool "duplication.json"
```

The output is `duplication.json` in your working directory. This tiny example
records **0 lexical groups and 0 retained structural groups**, both `complete`,
with 2 candidate-complete files and 0 candidate-unavailable files. The bodies
fall below candidate size floors: identical-looking source need not form an
admitted group. Zero groups or a partial result is not automatically failure.

For a nonempty result, read each `lexical_groups` or `structural_groups` entry's
`occurrences`: `path`, `start_line` and `end_line` locate the evidence. Inspect
those lines at the recorded revision. Read `counts.lexical.status`,
`counts.structural.status`, candidate coverage counts and per-file reasons beside
the groups. Lexical and structural populations overlap; **do not add their group
counts as one defect total**. The [lexical](DUPLICATION_LEXICAL_CONTRACT_V1.md),
[structural](DUPLICATION_STRUCTURAL_CONTRACT_V1.md) and
[grouping](DUPLICATION_STRUCTURAL_GROUPING_CONTRACT_V1.md) contracts define eligibility.
Standalone output is not automatically admitted to a native Dossier: required
commit provenance must be present and match the Run. A snapshot without that
provenance cannot supply an admitted duplication summary.

### B. Compare two revisions

**Question:** What measurements and changed source ranges differ between base and head?
Use the two resolved commits above. First measure each exact revision into a
distinct workspace outside `recipe-source`, then compare those retained Runs.
This preserves evidence for the history recipe and leaves your original project
untouched. Existing-Run comparison reads retained artifacts; direct revision
Diff (`metrolith diff --help`) materializes and measures its source sides.

```console
metrolith analyze "recipe-source" --revision "$base" --workspace "base-results"
metrolith analyze "recipe-source" --revision "$head" --workspace "head-results"
```

The two printed Run directories are under `base-results/metrolith-output/runs/`
and `head-results/metrolith-output/runs/`. Replace `BASE_RUN` and `HEAD_RUN` below
with those full printed paths (keep the `run:` prefix). Reuse this same `HEAD_RUN`
in recipe C; neither placeholder means the packaged tutorial's Run.

```console
metrolith diff --from "run:BASE_RUN" --to "run:HEAD_RUN" --format json --output "revision-diff.json" --workspace "diff-results"
```

`revision-diff.json` records source identities, comparability and measurement
deltas. Here `aggregate_metric_deltas` reports code lines **16 → 18 (+2)** and
methods/functions **2 → 3 (+1)**. Diff exits **1 for differences**, 0 for none;
2–4 indicate usage, artifact or comparison failures. Inspect the exit immediately
(`$LASTEXITCODE` in PowerShell, `$?` in POSIX); do not suppress failures.

For Git file/range evidence over the same commits, request Changed Code explicitly:

```console
metrolith changed "recipe-source" --base "$base" --head "$head" --format json --output "changed.json"
python -m json.tool "changed.json"
```

`changed.json` records one modified code file, `first.py`, with 3 added diff lines
(including a blank line), 0 deleted lines, and side-local callable overlap evidence.
It exits 0 when completed, even with changes. It does not match callables across
revisions or judge a change as improvement, risk or architecture quality. See
[source identity](#select-the-source-state) and the
[standalone output contracts](REPRODUCIBILITY.md#standalone-output-contracts).

### C. Add history context

**Question:** Which measured files also have repeated committed changes?
Reuse the exact head Run from B and the full local Git history. This one-repository
Run accepts a bare repository path; multi-repository Runs require repeated
`--repository "SUBJECT_KEY=PATH"` mappings using the Run's recorded subject keys.

```console
metrolith hotspots "HEAD_RUN" --repository "recipe-source" --format json --output "hotspots.json"
python -m json.tool "hotspots.json"
metrolith dossier "HEAD_RUN" --hotspots "hotspots.json" --format markdown --output "maintenance.md"
```

Read `hotspots.json` and the derived `maintenance.md` in the working directory.
Here `first.py` has `churn.status` of `measured`, `churn.commits` of **2** and
`churn.touched_lines` of **11** (creation counts). Its `moderate_attention`
classification combines relative complexity and churn signals; it is not a defect
prediction or universal threshold. The Dossier lists this Hotspots supplement as
`admitted`; inspect that admission before using its figures.

History means **all commits reachable from the analyzed revision**, not latest-N.
Required objects must be locally available for offline use; shallow history is
`unavailable`, never complete or zero churn. Missing source mapping can produce
`local_repository_override_required` and unclassified files. Check repository
history status, per-file reasons and metric availability. Acquiring Git history
does not request every consumer: Hotspots is invoked separately, and Changed Code
still needs its own `--base`/`--head`. Hosted admission caps do not define local CLI
history limits. See the [history semantics](../modules/hotspots.py),
[output contracts](REPRODUCIBILITY.md#standalone-output-contracts) and
[Dossier admission](#author-and-evaluate-policy).


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

The [history recipe](#c-add-history-context) supplies the source mapping for
Hotspots; [revision comparison](#b-compare-two-revisions) supplies Changed Code's
explicit base/head inputs. Offline execution requires the selected Git objects
already present; no recent-N History mode is provided.

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
