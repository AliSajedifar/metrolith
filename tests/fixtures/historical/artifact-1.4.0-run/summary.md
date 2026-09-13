# ArchLens run summary

- Run ID: 2075b780073f
- ArchLens version: 3.4.0
- ArchLens Git commit/tag: N/A
- ArchLens Git dirty: True
- Metric Contract: 3.0.0
- Exclusion Policy: 1.5.0
- Inventory Schema: 1.6.0
- Artifact Schema: 1.4.0
- Acquisition mode: offline
- Workspace: D:\Synthetic\Example User\metrolith
- Cache: D:\Synthetic\Example User\metrolith\.archlens\cache\git
- Worktrees: D:\Synthetic\Example User\metrolith\.archlens\worktrees
- Output root: D:\Synthetic\Example User\metrolith\validation\post_cohort_stabilization_20260805\targeted-offline-acceptance-output
- Started: 2026-08-05T12:22:15.932854Z
- Completed: 2026-08-05T12:34:38.178193Z
- Duration seconds: 742.245

## Counts

- Input rows: 13
- Enabled/planned: 13
- Processed: 13
- Complete: 5
- Partial: 7
- Failed: 1
- Skipped/disabled: 0
- Duplicate rows dropped: 0

## Git mode-map acquisition

- https://github.com/7ep/demo: None (None entries, N/A s)
- https://github.com/ambient-code/platform: available (2537 entries, 0.041606 s)
- https://github.com/apache/roller: available (1254 entries, 0.05872 s)
- https://github.com/cgrates/cgrates: available (2680 entries, 0.046177 s)
- https://github.com/Donkie/Spoolman: available (497 entries, 0.023706 s)
- https://github.com/dotCMS/core: available (23984 entries, 0.085563 s)
- https://github.com/magda-io/magda: available (2602 entries, 0.042287 s)
- https://github.com/nuglifeleoji/Options-Analytics-Agent: available (93 entries, 0.02999 s)
- https://github.com/rodrigorodrigues/microservices-design-patterns: available (1006 entries, 0.03377 s)
- https://github.com/ucfopen/Obojobo: available (2508 entries, 0.040962 s)
- https://github.com/umermansoor/microservices: available (17 entries, 0.044585 s)
- https://github.com/uselotus/lotus: available (772 entries, 0.031295 s)
- https://github.com/zlt2000/microservices-platform: available (863 entries, 0.040946 s)

## Repository results

| Repository | Status | Expected language family status | Partial origin | LOC | Source Files | Classes / Structs | Methods / Functions |
|---|---|---|---|---:|---:|---:|---:|
| https://github.com/7ep/demo | failed | failed | acquisition_or_inventory | N/A | N/A | N/A | N/A |
| https://github.com/ambient-code/platform | complete | complete | none | 144668 | 1114 | 521 | 4306 |
| https://github.com/apache/roller | partial | complete | secondary_supported_language_only | 50158 | 558 | 466 | 4191 |
| https://github.com/cgrates/cgrates | partial | complete | secondary_supported_language_only | 137548 | 589 | 1124 | 7092 |
| https://github.com/Donkie/Spoolman | complete | complete | none | 22847 | 180 | 98 | 772 |
| https://github.com/dotCMS/core | partial | complete | secondary_supported_language_only | 1572439 | 12223 | 7501 | 50801 |
| https://github.com/magda-io/magda | complete | complete | none | 199823 | 819 | 197 | 2202 |
| https://github.com/nuglifeleoji/Options-Analytics-Agent | partial | partial | expected_language_family | 7716 | 48 | 35 | 151 |
| https://github.com/rodrigorodrigues/microservices-design-patterns | partial | complete | secondary_supported_language_only | 22868 | 294 | 252 | 1135 |
| https://github.com/ucfopen/Obojobo | partial | partial | expected_language_family | 57805 | 945 | 172 | 2678 |
| https://github.com/umermansoor/microservices | partial | partial | expected_language_family | 171 | 6 | 0 | 13 |
| https://github.com/uselotus/lotus | complete | complete | none | 54147 | 263 | 712 | 1125 |
| https://github.com/zlt2000/microservices-platform | complete | complete | none | 21362 | 417 | 289 | 884 |

## Partial and failed repositories

- https://github.com/7ep/demo: failed
- https://github.com/apache/roller: partial
- https://github.com/cgrates/cgrates: partial
- https://github.com/dotCMS/core: partial
- https://github.com/nuglifeleoji/Options-Analytics-Agent: partial
- https://github.com/rodrigorodrigues/microservices-design-patterns: partial
- https://github.com/ucfopen/Obojobo: partial
- https://github.com/umermansoor/microservices: partial

## Key artifacts

- Sheet metrics: D:\Synthetic\Example User\metrolith\validation\post_cohort_stabilization_20260805\targeted-offline-acceptance-output\runs\20260805T122215Z_n013_2075b780073f\sheet_metrics.csv
- Errors: D:\Synthetic\Example User\metrolith\validation\post_cohort_stabilization_20260805\targeted-offline-acceptance-output\runs\20260805T122215Z_n013_2075b780073f\errors.csv
- Frozen input: D:\Synthetic\Example User\metrolith\validation\post_cohort_stabilization_20260805\targeted-offline-acceptance-output\runs\20260805T122215Z_n013_2075b780073f\repositories_frozen.csv
- Retry input: D:\Synthetic\Example User\metrolith\validation\post_cohort_stabilization_20260805\targeted-offline-acceptance-output\runs\20260805T122215Z_n013_2075b780073f\retry_failed_or_partial.csv
