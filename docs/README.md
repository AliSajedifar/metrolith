# Documentation paths

## Choose a path

- **Beginner:** [PyPI quickstart](../README.md#quickstart-from-pypi) →
  [Usage](USAGE.md#first-successful-analysis) → [packaged examples](../examples/README.md).
- **Investigating results:** [recorded walkthrough](RECORDED_WALKTHROUGH.md) →
  [reproducibility](REPRODUCIBILITY.md) → [full command reference](USAGE.md#commands).
- **Contributor:** [Development](DEVELOPMENT.md) → [Architecture](ARCHITECTURE.md)
  → the relevant contract and focused tests.
- **Maintainer:** [publication record](PYPI_PUBLISHING.md) →
  [release checklist](PUBLIC_RELEASE_CHECKLIST.md). Full release qualification is deliberate.

## Terms used throughout the guides

| Term | Meaning |
|---|---|
| Run | One analysis's directory of recorded evidence and derived views. |
| Policy | Rules and thresholds selected by the user, evaluated against admitted evidence. |
| Ratchet | Comparison with a retained baseline using selected rules and tolerances. |
| Source identity | The recorded directory snapshot, working state or exact Git revision measured. |
| Scope | Included/excluded populations and the configuration defining them. |
| Measurement status | Whether a value is complete, partial, unavailable, not applicable or not requested; zero is a value. |
| Provenance | Recorded producer, source and configuration identity; protected trust needs external authentication. |

## Version map

The [component table](USAGE.md#command-and-component-reference) lists product,
metric, complexity, exclusion, inventory, artifact and Check versions.
They are independently versioned: installing product 4.0.0 does not make every
format version 4.0.0. [Installed schemas](USAGE.md#commands) are discoverable with
`metrolith schema list`.

Frozen `archlens-*` format names, schema identifiers and historical fixtures are
compatibility contracts. They are not stale branding to replace. The recorded
demo includes older producer evidence; its [provenance](RECORDED_WALKTHROUGH.md)
differs from the production PyPI package.

Current GitHub documentation includes corrections after 4.0.0 publication.
The README, citation and metadata embedded in those published files remain
historical bytes; see the [publication record](PYPI_PUBLISHING.md).
