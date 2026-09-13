# Changelog

## 4.0.0 - unreleased

- Make `metrolith` the canonical console command and distribution. Retain
  `archlens`/`arch-bench` aliases, environment fallbacks and frozen format IDs.
- Provide local directory, working-tree, tracked-only and exact-revision analysis,
  packaged offline examples, Run explanations and derived dossiers.
- Preserve installed evaluator provenance and explicit partial/unavailable states.
- Validate native snapshot Check output and protect exporter inputs, including
  handwritten files with generic Markdown/HTML prefixes.

### Hotspot and Duplication Policy integration

Policy admits matching optional `hotspots` and Duplication evidence through `--hotspots` and `--duplication`. Policy 2.1 and Policy 2.2 retain their respective
versioned evidence contracts; missing evidence cannot silently pass a rule.

## 3.8.0 - unreleased

The earlier command family supplied `archlens diff`, `archlens duplication` and
`archlens changed` under their versioned contracts. Existing machine identifiers
and supported artifact readers remain compatible; this note does not assert a
historical public package release.
