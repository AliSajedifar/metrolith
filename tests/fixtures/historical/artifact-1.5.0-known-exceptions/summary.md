# ArchLens run summary

This file is a human-readable projection and is **not authoritative**. Final run integrity is recorded in `run_status.json`; authoritative repository results are in `analysis.json`. This summary reports the finalized measurement facts available at render time.

## 1. Run integrity and measurement outcome

- Run ID: 738a9f827f8e
- Run integrity status: completed
- Measurement outcome: complete
- Mandatory output failures: 0
- Optional output failures: 0
- Note: measurement outcome is derived from repository results and is not changed by failure to write an optional projection.

## 2. Provenance warnings

- profiler Git commit SHA is null; this run has no immutable revision and is not benchmark\-of\-record ready
- profiler working tree was dirty at run time

## 3. Input population

- Accepted source rows: 1
- Enabled / planned: 1
- Processed: 1
- Skipped because disabled: 0
- Identical duplicates dropped from execution: 0
- Complete accepted population retained in `normalized_input.csv`: 1 row(s)

## 4. Per-metric completeness

Denominator: 1 repository result(s).

| Metric status field | Distribution |
|---|---|
| inventory\_status | complete=1/1 |
| source\_files\_status | complete=1/1 |
| loc\_status | complete=1/1 |
| classes\_structs\_status | complete=1/1 |
| methods\_functions\_status | complete=1/1 |

## 5. Expected language-family status

- not\_applicable: 1/1

## 6. Partial-origin distribution

- none: 1/1

## 7. Error and recovery evidence

- Recorded errors: 0
- Recorded parser recoveries: 0
- Raw error previews are deliberately not reproduced here; see `errors.csv` and `recoveries.csv`.

## 8. Git mode-map summary

- available: 1/1

## 9. Runtime observations

- Run started: 2026\-08\-06T13:37:30\.019672Z
- Measurement finished: 2026\-08\-06T13:37:35\.018751Z
- Finalization finished: not yet recorded at summary render time; see `run_status.json`
- Workers: 1
- Runtime observations are excluded from semantic run equality and from the semantic hash.

## 10. Cross-language comparability limitations

The four metrics share names and aggregation rules, but their raw entity counts are not perfectly measurement-equivalent across languages. Interfaces are excluded from Classes / Structs in every language; Go counts named module-scope structs; Python counts nested and function-local classes. Raw counts alone must not be interpreted as cross-language architecture-quality measures.

## 11. Supplied architecture-label disclaimer

`architecture_type` is **supplied input metadata**, not an inferred or verified property. ArchLens does not classify architecture and does not treat any reference architecture as absolute ground truth.

## 12. Version and contract boundary

- ArchLens program: 3\.5\.0
- Metric Contract: 3\.0\.0
- Exclusion Policy: 1\.5\.0
- Inventory Schema: 1\.6\.0
- Artifact Schema: 1\.5\.0

## 13. Artifacts

Paths are relative to this run directory.

- `run_manifest.json`
- `run_status.json`
- `analysis.json`
- `environment.json`
- `sheet_metrics.csv`
- `language_metrics.csv`
- `catalog.csv`
- `errors.csv`
- `recoveries.csv`
- `normalized_input.csv`
- `repositories_frozen.csv`
- `retry_failed_or_partial.csv`
- `file_inventory/`
- `logs/run.jsonl`

## 14. Limitations

- This summary is non-authoritative and must not be parsed for measurement.
- Metric values are static observations; no dynamic behaviour is measured.
- A partial numeric value is an observation, not a verified complete value.
- A null value means unavailable, and is never equivalent to zero.

## 15. Appendix: repository results

| Repository | Recorded status | Expected-family status | Partial origin | LOC | Source Files | Classes / Structs | Methods / Functions |
|---|---|---|---|---:|---:|---:|---:|
| https://github\.com/codergogoi/Grocery\_Online\_Shopping\_App | complete | not\_applicable | none | 1045 | 24 | 11 | 49 |
