# Releasing Metrolith

## Published baseline

4.0.0 was published on **2026-09-13 UTC**, from
`0fba1c9d0b57fa16524ca8bb9ac4315430442f95` in
[run 34788631105](https://github.com/AliSajedifar/metrolith/actions/runs/34788631105).
See the [publication record](PYPI_PUBLISHING.md) for exact PyPI file hashes.
Current documentation corrections do not replace published bytes.

## Checklist for a future approved release

The current publisher is first-release-specific and refuses an existing project
or version. Review a repeat-release workflow update before using it for another
version; its frozen payload/ProducerIdentity checks also require deliberate review.
Build & smoke is a bounded packaging check, not full release qualification.


1. Confirm version, license, maintainer metadata and controlled repository URLs.
   Do not infer identity, citation status or publication rights.
2. Review exactly the source being released, including corrected working files.
   For an authorized committed release, install the reviewed lock and run
   `tools/release_verify.py` with its evidence output outside the repository.
3. Build a new sdist and wheel, verify standalone sdist builds, installed runtime
   resources, applicable tests and strict Twine checks. Record filenames,
   sizes and SHA-256 identities for those exact artifacts.
4. Review the GitHub tree, sdist and wheel separately: maintainable tests and
   fixtures belong in source; runtime/resources/licenses belong in the wheel.
5. Confirm protected CI provenance and baseline source configuration before
   enabling a required gate. Local checks do not establish protected authority.
6. Publish only the approved version, source and exact distribution artifacts
   to the approved destinations. A changed artifact needs a new identity.

Build instructions are in [DEVELOPMENT](DEVELOPMENT.md); equivalence limits are in
[REPRODUCIBILITY](REPRODUCIBILITY.md). This checklist performs no upload or Git operation.

Navigation: [usage](USAGE.md) · [project README](../README.md).
