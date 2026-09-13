# ArchLens Report View Contract 1.0

## Purpose

`archlens-report-view` is a stable, UI-neutral projection of a finalized ArchLens run and any optional documents that pass the existing contract and provenance admission boundary. It gives a later renderer one strict document whose values, states, and source locations are already resolved in Python. It does not define or implement a user interface.

## Authority boundary

Report View is derived, non-authoritative presentation data. It is not a run artifact, measurement artifact, Policy or Ratchet input, trusted evidence, or a Duplication, Hotspots, or Changed-Code input. It is never admissible evidence for another document. Its source references identify authorities; they do not make Report View an authority or attest to its own contents.

## Identity

- Format: `archlens-report-view`
- Format version: `1.0.0`
- Builder contract: `archlens-report-view-builder` `1.0.0`
- Original Program context: ArchLens `3.8.0`; retained in Metrolith `4.0.0`
- Schema: `validation/resources/schemas/report_view-1.0.schema.json`

The document has no self-digest, generation clock, random identifier, host identity, locale, or machine-local path. A caller may hash its canonical bytes externally.

## Authoritative inputs

The required input is one finalized run opened through the canonical artifact reader. Run manifest, status, environment, analysis, sheet/language/callable ledgers, diagnostic ledgers, catalog, and per-subject inventories are cited by exact-byte SHA-256 when present. Optional inputs are existing Duplication, Hotspots, Changed-Code, and Check Result documents.

The builder selects persisted values. It does not re-analyze source, recompute measurements or aggregates, calculate percentiles, evaluate Policy, compare Ratchet baselines, or infer trust. Report View 1.0 contains no exact-frequency complexity distribution because the current run contract does not persist one; this is a deliberate contract omission, not an accidentally missing builder field. A future browser renderer must not derive a distribution from callable rows. Adding distributions requires a later versioned authority decision—either a persisted source contract or an explicitly contracted derived projection. UI ideation must design against only the data actually present in Report View 1.0.

## Composition and admission reuse

Report View and Dossier use `modules.report_composition` for standalone validation, Check Result validation, contract compatibility, subject/provenance binding, and the existing closed admission meanings. Admission is fail-closed and refuses ambiguous multi-subject matches; it never uses filename trust or first-match selection. Every optional kind emits a record for `admitted`, `not_supplied`, `incompatible`, `provenance_mismatch`, `unreadable`, `ambiguous`, or `refused`. Non-admitted documents contribute no rows or findings.

## Semantic domains

The closed top-level model contains document identity, authority, source documents, presentation vocabulary, run state, subjects, measurements, findings, Duplication, Hotspots, Changed-Code, diagnostics, trust and reproducibility, supplement admission, privacy inventory, and limits.

Measurements retain core and language values/statuses, complete callable rows, persisted structural/cognitive aggregates, and source composition. Check Result findings and Ratchet summary, admitted Duplication groups/occurrences, Hotspot file evidence, Changed-Code files/hunks, and persisted diagnostics/exclusions retain their existing identities and meanings. No score, default threshold, cross-subject ranking, cross-revision callable inference, source body, or diff body is added.

## Status and trust representation

Each status record preserves the source vocabulary and raw code beside a presentation axis/code, bounded label and meaning, safe reason, measured/evaluable flags, and nullable pass state. Run lifecycle, measurement missingness, Policy/Ratchet evaluation, supplement admission, and trust are separate axes. In particular, zero is not absence, refusal is not absence, no Policy is not a pass, Ratchet-not-configured is not unchanged, and a passing Check Result does not imply protected trust.

Trust fields are projected only from existing run and Check Result provenance: gate mode, evaluator and Policy identity/trust, source/evidence digests, receipt and baseline evidence, source-run binding, measurement semantics, contract versions, and analyzed revisions. Generating Report View never upgrades trust.

## Traceability

A source reference names the source role, format/version, SHA-256, portable run-relative path, optional subject key, stable row/finding/group identity, logical pointer, and ledger row identity. Core/language metrics, callables, findings, Ratchet, Duplication groups and occurrences, Hotspot rows, Changed-Code files and hunks, diagnostics, and supplement admissions all carry source references at their relevant granularity.

## Privacy

The machine-readable privacy inventory reports whether portable repository locators, revisions, subject keys, relative paths, callable names, Policy text, evaluator identity, digests, and bounded error text are present. Validation forbids source/diff bodies, absolute Windows or Unix paths, usernames, hostnames, environment dumps, secret-like material, tokens, and credentials. Unsafe diagnostic text is omitted; authoritative local artifacts remain the diagnostic authority.

## Determinism

Serialization uses the shared strict JSON boundary: UTF-8, recursive JSON types, duplicate-key and non-finite-number refusal, compact sorted keys, deterministic collection ordering, and exactly one final LF. No filesystem location participates in identity. Identical admitted source bytes therefore produce identical Report View bytes after moves or repeated generation.

## Scale observations

The historical sample package (not shipped in this source/sdist) recorded warmed Windows/Python 3.13 observations, not product or UI budgets. The small fixture is about 46 KB with 12 callables; the medium fixture about 337 KB with 240 callables; and the complete large fixture about 3.94 MB with 2,500 callables, 120 Duplication groups/360 occurrences, 500 Hotspot rows, and 250 Changed-Code files/hunks. Nothing is silently truncated. The archived size, canonicalization, validation and memory records were `validation/report_view_v1_samples/SAMPLES.json` and `SCALE_OBSERVATIONS.json`.

That separate historical sample package preserves its original six payloads as exact historical design evidence. `missing-refused-unavailable.json` has a known contradiction: its Duplication admission records a supplied/refused document while its Duplication section availability records `not_supplied`. The retained bytes must not be silently reconciled. The additive `supplied-refused-unavailable-corrected.json` fixture follows the builder mapping by recording section availability as `refused`; it was designated as a synthetic design/testing input for that separate Explorer work. Neither fixture is experimental or real-repository evidence.

## Compatibility

Report View adds no public command or default output. Existing Summary, HTML, Fact Sheet, Dossier 1.0, Changed Markdown, Check JSON/text/SARIF, Hotspots, and Duplication products remain on their current paths and contracts. Report View is registered only in the schema store, not the standalone admissible-contract registry. These format/builder contracts remain 1.0.0 in Metrolith 4.0.0; the original 3.8.0 program context is historical. See [current components](USAGE.md#command-and-component-reference).

## Explicitly deferred UI decisions

UI pages or views, navigation, themes/colors, charts, component design, interaction model, frontend framework, single-file versus served implementation, which public command owns UI generation, and hosted-viewer design are intentionally deferred until independent design review of the contract samples and scale evidence. No Offline Explorer is claimed here.
