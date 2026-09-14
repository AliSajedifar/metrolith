# PyPI publication record and future releases

## Completed 4.0.0 publication

[Metrolith 4.0.0](https://pypi.org/project/metrolith/4.0.0/) was published to
production PyPI on **2026-09-13**, using the first production upload date in UTC.
The wheel arrived at `2026-09-13T23:09:04.37946Z`; the sdist followed at
`2026-09-13T23:09:06.49975Z`.
[PyPI JSON](https://pypi.org/pypi/metrolith/4.0.0/json) records the file identities.
[Publishing run 34788631105](https://github.com/AliSajedifar/metrolith/actions/runs/34788631105)
completed successfully from commit
[`0fba1c9d0b57fa16524ca8bb9ac4315430442f95`](https://github.com/AliSajedifar/metrolith/commit/0fba1c9d0b57fa16524ca8bb9ac4315430442f95).

| Published file | SHA-256 |
|---|---|
| `metrolith-4.0.0-py3-none-any.whl` | `bddeca14d71485529f54c5639c43d0ba193694a45a011b93336751ac85c38a5a` |
| `metrolith-4.0.0.tar.gz` | `0c93efa7485c5a2a38ae5904069e8abbd2a5fadeec1a72b781248f6df955c95f` |

PyPI also exposes [wheel provenance](https://pypi.org/integrity/metrolith/4.0.0/metrolith-4.0.0-py3-none-any.whl/provenance)
and [sdist provenance](https://pypi.org/integrity/metrolith/4.0.0/metrolith-4.0.0.tar.gz/provenance).
The retained statements identify those hashes and the GitHub publishing workflow.
Downloading and hashing files checks file identity; it does not independently
perform every cryptographic attestation verification step.

The original packaged README was source-first, and its changelog/citation still
called 4.0.0 unreleased. Current GitHub documentation corrects these statements.
It does **not** modify the long description, citation or other bytes embedded in
published 4.0.0. Source metadata now links the website; published metadata remains
as uploaded. Carry revised documentation into a separately approved new version.
Do not re-upload, delete, yank or cosmetically relabel the existing files.

## Existing workflow: first release only

[`publish-pypi.yml`](../.github/workflows/publish-pypi.yml) is manual, restricted
to this repository's `main`, with `publish` defaulting to false. Its protected
`pypi` environment separates preparation from the OIDC upload job. Only upload
has `id-token: write`; that job downloads its own run's exact artifact by ID and
verifies manifest/file hashes before the pinned PyPA publishing action.

**It is not ready for a second release.** The upload guard refuses an existing
project or version, and filenames/version expectations are fixed to 4.0.0.
Preparation also requires the frozen first-release 362-member payload and
ProducerIdentity. Editing installed `examples/README.md` changes those identities,
even though executable code is unchanged. Therefore current documentation builds
must use [Build & smoke](../.github/workflows/package-smoke.yml), not rerun the
publisher or relax its baseline checks.

The original preparation used CPython 3.13.9, hash-locked tooling, standalone
sdist/wheel builds, strict Twine, documentation/publication-boundary tests and a
tiny installed example. This bounded scope is not full engine qualification.
The separate `ci.yml` invokes `tools/release_verify.py` on Windows and Ubuntu.

## Future release procedure

1. Obtain approval for the new version and exact intended source. Follow the
   [release checklist](PUBLIC_RELEASE_CHECKLIST.md), including deliberate full
   qualification and review of new documentation and installed data identities.
2. Review and update the first-release workflow for repeat publication before
   using it: version/file expectations, reviewed baseline identities, and safe
   existing-project/new-version checks all need explicit treatment. Preserve
   fail-closed behavior, immutable artifact binding and approval separation.
3. Confirm the owner's PyPI Trusted Publisher and GitHub `pypi` environment
   controls: repository `AliSajedifar/metrolith`, workflow `publish-pypi.yml`,
   environment `pypi`, allowed branch `main`, and required owner review. A YAML
   environment name alone does not prove that these controls are configured.
4. After that workflow update is separately reviewed, prepare a candidate with
   upload disabled. Review its exact source SHA, metadata, README rendering,
   standalone archives, hashes, installed resources and check evidence. Retain
   copies before temporary Actions artifacts expire.
5. Only with explicit publication authorization, request publication and have
   the owner approve that run's exact pair. Do not substitute an earlier build's
   hashes. Keep uploading isolated from checkout, builds and installation; never
   add `skip-existing` to disguise a conflict.
6. Read production PyPI file metadata and provenance back, authenticate downloads,
   and test fresh index installations outside the checkout. Record historical
   upload time separately from any later GitHub Release creation time.

A GitHub Release is independent of PyPI success. A retrospective `v4.0.0` must
target the publishing commit above, never a later docs or performance commit.
The current unrestricted `ci.yml` push trigger also reacts to tags; creating a
tag can launch full qualification. Review event effects before any tag/release
write. No tag is needed for installation from PyPI.

References: [Trusted Publishing](https://docs.pypi.org/trusted-publishers/using-a-publisher/),
[GitHub environments](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/manage-environments),
[PyPI README guidance](https://packaging.python.org/en/latest/guides/making-a-pypi-friendly-readme/).
