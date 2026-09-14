# Changelog

## Unreleased

- Improve current documentation, PyPI onboarding, recorded examples and package smoke checks.
  These changes are not included in the already published 4.0.0 distributions.

## 4.0.0 - 2026-09-13

Released on [production PyPI](https://pypi.org/project/metrolith/4.0.0/).
Date convention: first production upload in UTC, as recorded by
[PyPI file metadata](https://pypi.org/pypi/metrolith/4.0.0/json).
[Publishing run](https://github.com/AliSajedifar/metrolith/actions/runs/34788631105)
used source `0fba1c9d0b57fa16524ca8bb9ac4315430442f95`.


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
