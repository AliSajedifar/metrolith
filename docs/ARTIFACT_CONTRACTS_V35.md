# ArchLens 3.5.1 — Artifact Contracts and Reader Reference

**Artifact Schema:** 1.11.0 · **Inventory Schema:** 1.7.0 ·
**Metric Contract:** 3.0.0 · **Complexity Contract:** 2.0.0 ·
**Exclusion Policy:** 1.5.0 ·
**Qualification Artifact / Registry Schema:** 1.0.0

These version labels record the original 3.5.1 reference context. In Metrolith
4.0.0, the current Artifact Schema is **1.12.0**; Inventory 1.7.0, Metric 3.0.0,
Complexity 2.0.0 and Exclusion Policy 1.5.0 retain their definitions. The
measurement document `analysis.json` retains its own 1.10 schema. Resolve each
artifact using its declared version and the [schema store](../validation/artifact_io/schema_store.py),
or `metrolith schema list`; the historical heading is not a schema selector.
See the [current component reference](USAGE.md#command-and-component-reference).

---

## 1. Artifact authority

Not every file in a run directory carries the same weight. Reading a projection
as if it were authoritative is how a reporting bug becomes a measurement claim.

| Artifact | Authority |
|---|---|
| `run_status.json` | **Authoritative** for run integrity |
| `run_manifest.json` | **Authoritative** for run identity, configuration, provenance, versions, counts, qualification mode, and benchmark-of-record readiness |
| `analysis.json` | **Authoritative** for finalized repository results |
| `environment.json` | **Authoritative** for recorded execution environment |
| `file_inventory/*.json` | **Authoritative** for file identity, inclusion, language, read/parse status |
| `normalized_input.csv` | **Authoritative** for the complete accepted input population |
| `contributions.csv` | **Authoritative** for recorded per-file contribution evidence |
| `benchmark_qualification.json` | **Authoritative** for repository representativeness, benchmark admission, qualification reason codes, and adjudication provenance. **Conditional**: present if and only if `qualification_mode=benchmark_qualified` |
| `logs/run.jsonl` | Authoritative for **event ordering only**; never for metric values |
| `catalog.csv`, `sheet_metrics.csv`, `language_metrics.csv`, `errors.csv`, `recoveries.csv` | Mandatory **checked projections** of authoritative results |
| `repository_level_metrics.csv` | **Checked projection**; conditionally mandatory in benchmark-qualified mode |
| `repositories/*.json`, `fact_sheets/*.md` | **Optional** projections |
| `summary.md` | Mandatory but **non-authoritative** |
| `report.html` | Optional, **non-authoritative** |
| `latest_run.json` | Convenience pointer in the output root; **no** measurement authority |

`benchmark_qualification.json` never modifies `analysis.json`, inventories,
contributions, callables, metric populations, metric values, or any
`*_status` field. It is authoritative for admission, not for measurement.

`latest_run.json` lives outside the run directory. No reader may traverse upward
to find it, write it, or treat it as run evidence.

### Derived, non-authoritative formats

`explain`, `compare --explain`, `report`, `reproduce`, and `perf` outputs carry
independent format versions and **must never be read back as measurement
evidence**. They are presentations of the authoritative artifacts, not
substitutes for them.

---

## 1b. Four independent dimensions

Artifact Schema 1.11 makes explicit a separation that was always intended and
was never persisted. Four questions have four answers, four authorities, and no
implication between them.

| # | Dimension | Question | Authority |
|---|---|---|---|
| A | **Measurement completeness** | Was the selected supported scope measured, and how completely? | `analysis.json`, `file_inventory/`, contribution and callable ledgers, `*_status` fields |
| B | **Repository representativeness** | Does that selected scope adequately represent first-party application code for the declared profile? | `benchmark_qualification.json` |
| C | **Benchmark usability / admission** | How may this measured record be used in this benchmark? | Derived in `benchmark_qualification.json`, constrained by A and by any manual restriction |
| D | **Benchmark-of-record readiness** | May this immutable run bundle serve as a benchmark of record? | `benchmark_of_record_readiness` in `run_manifest.json` |

**`metric_status` is not redefined.** It means exactly what it meant at 1.10:
measurement completeness for the selected supported scope. It has never
implied `ADEQUATE`, and it has never implied repository-level usability.

The four dimensions genuinely come apart, and the accepted 96-row corpus
contains an example of every interesting combination:

- `edwinvw/pitstop` is `metric_status=complete` with 3 files / 36 LOC / 0
  classes / 0 callables, and those numbers are **correct** for three incidental
  JavaScript files. The application is a C#/.NET multi-service system, so the
  record is `NON_REPRESENTATIVE` and `unsupported_scope`. Nothing about the
  measurement is repaired to reach that verdict — repairing it would be
  falsifying a correct measurement to fit an admission decision.
- `fledge-iot/fledge` and `NHadi/Pos` are also `NON_REPRESENTATIVE`, yet remain
  `analyzed_scope_only` rather than `unsupported_scope`. `unsupported_scope` is
  an explicit editorial decision about the benchmark subject, never an
  automatic alias for a representativeness verdict.
- Four subjects are `metric_status=partial` and `partial_but_usable`; three of
  those are simultaneously `ADEQUATE`. Completeness and representativeness are
  separate gates, so an `ADEQUATE` partial record is still excluded from
  unrestricted comparison.
- A run can hold 96 valid measurements and 82 correctly eligible records and
  still be `NOT_READY`, because dimension D inspects provenance rather than
  measurement.

### Derived, never supplied

`usable_metric_families`, `benchmark_usability` and
`repository_level_comparison_eligible` are **deterministic derivations** from
the measurement statuses plus the accepted representativeness verdict. The
registry schema refuses to carry them, so a hand-edited registry cannot supply
a second opinion about facts `analysis.json` already owns. A persisted value
that disagrees with its derivation is a semantic validation failure, never an
override.

Manual input supplies exactly two things: the representativeness verdict with
its evidence, and an optional `manual_admission_restriction` that can only make
admission **more** restrictive. There is no path by which registry input raises
admission above what measurement state and representativeness already allow.

### Semantic keys and non-semantic text

Enum values and stable reason codes are **semantic**: gates, joins, counts and
comparisons key on them, and adding or changing a code is a contract change.
`representativeness_basis` and `usability_basis` are **non-semantic** evidence
prose. They may clarify a decision and can never change one; no gate, join,
count or comparison may read them.

### Primary identity

A qualification decision binds to exactly:

```text
(qualification_profile, subject_key, analyzed_commit_sha,
 analysis_scope_hash, analysis_scope_hash_version)
```

Lookup is exact. A near miss — right subject, wrong revision or wrong scope —
is a **miss**, and produces an explicit `UNRESOLVED` record rather than a
silently reused stale decision. Multiple exact matches are a fatal registry
error: choosing between two accepted decisions about identical bytes would mean
publishing an adjudication nobody made.

`filesystem_manifest_hash` is **not** part of the binding. The contract
describes it as broader filesystem evidence rather than comparability identity,
so no match, decision or gate may depend on it. It may be copied as optional
verification evidence.

### Artifact 1.10 and earlier

A pre-1.11 run bundle carries no qualification artifact. That absence means
*not benchmark-qualified* — it is never adapted into `ADEQUATE`, into any
usability value, into evidence, or into an adjudicator. Its measurements remain
fully readable and remain valid for qualified-scope research; its
repository-level comparison eligibility is false.

---

## 2. Repository documents are one lifecycle-variant path

`repositories/<slug>.json` is a single path with two variants, not two paths.

**Checkpoint variant** — exists while the run is running or interrupted, and
carries `checkpoint_updated_at`.

**Final-result variant** — exists after finalization and must reconcile exactly
with the matching `analysis.json` element.

Discrimination is by run lifecycle first, then by the presence of
`checkpoint_updated_at`. **`checkpoint_status` is not a discriminator**: every
finalized ArchLens 3.4.0 document retains it, so using it would misclassify
every historical run.

---

## 3. Historical compatibility matrix

This table records the original reader baseline and its reviewed adapters.
It does not enumerate all current 4.0.0 readers; current native Artifact 1.12 /
Inventory 1.7 support is selected by the packaged version-aware readers.
The rejection states below retain their original meanings.

| Declared version | State | Behaviour |
|---|---|---|
| Artifact 1.5 / Inventory 1.6 | `supported` | Original native target (historical) |
| Artifact 1.4 / Inventory 1.6 | `supported` | Reviewed adapter |
| Artifact 1.3 / Inventory 1.5 | `supported` | Reviewed adapter plus the legacy cell decoder |
| Artifact 1.2 / 1.1 / 1.0 | `unsupported` | Predates the reviewed fixtures |
| absent | `undeclared` | Explicit error; never a silent downgrade |
| unparseable | `corrupt` | Explicit error |
| newer than native | `future_unsupported` | Explicit error |

A missing version never weakens a check. Absence of a version is not evidence
that an artifact is old enough to need leniency.

### Column sets differ across versions

`errors.csv` carries 43 columns at Artifact 1.0 and 79 at 1.4. Each schema
property records `x-archlens-since-artifact-schema`, derived from every
preserved run, and a column introduced after the artifact under inspection is
not required of it. Omitting the declared version keeps every column required,
which is the strict reading appropriate to the artifact's declared target.

---

## 4. Finding F-1: the legacy list-cell decoder

Artifact ≤ 1.3 serialized list-valued CSV cells with Python `repr`:

```text
artifact <= 1.3.0    ['classes_structs', 'methods_functions']    not JSON
artifact >= 1.4.0    ["classes_structs", "methods_functions"]    JSON
```

This is a **translation at a version boundary, not a relaxation**. For Artifact
≥ 1.4 a list cell *is* JSON and a non-JSON cell is genuinely malformed; Artifact
≤ 1.3 never claimed to emit JSON there.

Three gates must hold simultaneously:

1. the contract opted the column in via `legacy_python_repr_until`;
2. `(artifact, column)` is in the closed four-entry allowlist;
3. the declared version parses and is below 1.4.0.

Canonical `json.loads` is always attempted first. Recovery uses
`ast.literal_eval` — never `eval` — bounds the cell at 8192 characters before
parsing, requires a flat list of plain strings, preserves the raw value, and
always emits a diagnostic. Failure to recover is a hard error.

Allowlist: `errors.csv:affected_metrics`, and `recoveries.csv:` `affected_metrics`,
`fallback_strategies`, `selected_fallback_strategies`.

---

## 5. Normalized input ledger

`normalized_input.csv` retains **every accepted source row**, including rows the
execution set discards:

- disabled rows;
- identical duplicates, each pointing at its representative;
- repositories whose acquisition failed, **with their requested SHA intact**.

Conflicting duplicates are fatal input validation. They never start a run, so no
ledger exists for them; the error names every conflicting source row.

`repositories_frozen.csv` and `retry_failed_or_partial.csv` derive from this
ledger. Frozen rows require a **verified analyzed SHA** — a frozen input asserts
byte reproducibility, which an unacquired repository cannot support. Retry rows
fall back to the **requested** SHA, because a failed acquisition is exactly what
a retry file exists to serve.

Source input identity is stored as a **file name plus SHA-256**, never an
absolute path: a run directory is meant to be movable between machines.

---

## 6. Contribution ledger

One row per inventory record with `included_in_metrics == true`. A file whose
detected language is outside the supported set stays present with
`contribution_state = no_contribution_unsupported_language`.

Every raw `LOC_KEYS` and `ENTITY_KEYS` component is persisted, plus the derived
Lines of Code, Classes / Structs, Methods / Functions, and Source Files
contribution.

**There is no second metric calculation.** `derive_lines_of_code`,
`derive_classes_structs`, and `derive_methods_functions` are defined once in
`modules/core_metrics.py` and used by both aggregation and the ledger, so the
two cannot drift.

### Reconciliation

| Situation | Outcome |
|---|---|
| Components sum exactly | `exact` |
| Components do not sum | `residual` with the exact difference |
| Aggregate status Failed **and** value null | **`not_evaluable`** |
| Any component null | `not_evaluable` |

The null-on-failed rule matters most. An unavailable measurement read as zero
would manufacture agreement that does not exist.

---

## 7. Security model

Every run directory is untrusted input.

- Fixed artifact allowlist; strict path containment; no upward traversal.
- Symlinked mandatory artifacts refused in strict mode. This concerns **run
  artifacts**, never Git symlinks inside analyzed repositories.
- Size and row bounds on every artifact.
- Duplicate JSON keys, NaN/Infinity, duplicate CSV headers, and malformed
  JSON cells all rejected.
- Context-specific escaping: Markdown for `summary.md`, HTML for `report.html`,
  terminal for `explain`. Control and bidirectional characters rendered visibly
  in all three.
- No absolute host paths in any rendered output.
- Atomic writes; overwrite refused without `--overwrite`.
- No command replay; reproduction arguments are reconstructed from validated
  fields.
- HTML safety is enforced by construction, not by a CSP meta tag: there is no
  code path that can emit a script, an external resource, or an
  external-scheme `href`.

---

## 8. Validator independence

`validation/scripts/validate_outputs.py` recomputes the scientific invariants on
its own and **must not** import the reader, the diagnostic projection, explain,
report, or comparison modules — nor the shared `derive_*` functions.

Sharing `derive_*` between production aggregation and ledger persistence is
correct: both are production, and sharing is what stops them drifting. The
validator is different. If it reused them it would be comparing a value to
itself and could never disagree, which would make every agreement meaningless.

A mechanical import-edge test enforces this by importing each side in a
subprocess and inspecting `sys.modules`, which catches transitive edges that a
source-text scan would miss.

---

## 9. Timing contract

| Clock | Meaning |
|---|---|
| `run_started_at` | After input validation, immediately before repository processing |
| `measurement_finished_at` | After all repository results complete, before finalization |
| `finalization_finished_at` | **Normative end time** |

Per-repository durations may overlap under concurrency and are **never** summed
into a run wall time. Log-derived duration is diagnostic, not the normative
clock.

**Timing fields and performance profiles are excluded from the semantic payload
and from Frozen/Offline measurement equality.** Performance thresholds are
report-only and block nothing.

---

## 10. CLI reference

```text
archlens validate <run-dir> [--schema-only]
archlens schema list [--format json]
archlens schema export --out <directory>
archlens explain <run-dir> [--repo URL] [--only GROUP] [--format json]
archlens compare <baseline> <candidate> [...]          # unchanged default
archlens compare <run-a> <run-b> --explain [--format json] [--repo URL]
archlens report <run-dir> [--output PATH] [--overwrite]
archlens reproduce <run-dir> [--format json]           # read-only preflight
archlens reproduce <run-dir> --execute --mode offline|frozen \
    --output-root PATH [--scope full|resolved-subset|repository] \
    [--repo URL] [--allow-network] [--confirm-large-run] [--workers N]
archlens perf baseline <run-dir> --out <file>
archlens perf check <run-dir> --baseline <file>
python -m validation.conformance run [--case ID] [--format json]
```

### Exit codes

`compare` default: `0` equal · `1` any difference or artifact error · `2` usage.

`compare --explain`: `0` equal · `1` comparable and different · `2` usage ·
`3` invalid or missing artifacts · `4` incomparable.

---

## 11. Reproduction assurance states

| State | Meaning |
|---|---|
| `exact_environment_ready` | Unreachable without a real profiler commit SHA |
| `compatible_reexecution_ready` | Versions agree and the full population is available |
| `resolved_subset_only` | No ledger; only resolved repositories can re-execute |
| `not_reproducible` | A recorded blocker prevents re-execution |
| `invalid_artifacts` | The run cannot be read |

Preflight is read-only: no writes, no network, no worktree, no parser.

Execution requires explicit `--execute`, an explicit `--mode`, an isolated
`--output-root`, and — for Frozen — explicit `--allow-network`. **There is no
Offline-to-Frozen fallback**: an Offline reproduction that reached the network
would be a different experiment wearing the same name.
