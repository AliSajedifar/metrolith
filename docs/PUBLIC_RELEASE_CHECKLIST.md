# Releasing Metrolith

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
