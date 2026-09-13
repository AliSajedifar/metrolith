# Manual PyPI preparation and publication

`publish-pypi.yml` is manual only, restricted to `AliSajedifar/metrolith` on
`main`. Its Boolean `publish` input defaults to **false**. Preparation builds
Metrolith 4.0.0 from the immutable dispatch SHA, using CPython 3.13.9 and the
existing hash-locked build, validation and runtime provisioning. The reviewed
`ReleaseVerifier.build_distributions` method exports Git source, builds and
normalizes the sdist, then builds the wheel from that standalone sdist outside
the checkout. The full verifier and full engine suite are not run.

## Prepare only

In Actions, choose **Prepare or publish Metrolith to PyPI**, select `main`,
leave `publish` unchecked and run. Equivalent CLI:

```console
gh workflow run publish-pypi.yml --repo AliSajedifar/metrolith --ref main -f publish=false
```

The `prepare` job checks safe archive members, exact source/resource inclusion,
metadata, README, license and wheel RECORD; runs strict Twine, the documentation
and publication-boundary regression modules; then installs the actual wheel
into an isolated environment and runs pip check, version, doctor, the packaged
network-free example, report, explain and validate. Runtime payload and measured
ProducerIdentity must equal the reviewed baseline. This is bounded evidence,
not a new full qualification. Markdown uses the existing validation lock's
CommonMark renderer plus readme-renderer's HTML sanitizer; it is a
PyPI-compatible approximation, not a live PyPI page.

Review this run's `candidate-manifest.json`, `SHA256SUMS`, check results and the
two distributions. Artifacts expire after 14 days; retain private review copies.
The entire `publish` job is skipped for false/omitted input, so preparation
requires neither PyPI configuration nor OIDC publishing privileges.

## Owner setup

On PyPI, the owner completes email verification and 2FA in their own browser,
then creates a pending GitHub publisher with these exact fields:

| Field | Value |
|---|---|
| Project name | `metrolith` |
| Owner | `AliSajedifar` |
| Repository | `metrolith` |
| Workflow filename | `publish-pypi.yml` |
| Environment | `pypi` |

Use the filename alone. Account username: `AliSajedifar`. A pending publisher
does not reserve the project name. A public 404 does not prove ownership.
Never place passwords, API tokens, TOTP seeds or recovery codes in the workflow.

GitHub Settings -> Environments -> `pypi` must require `AliSajedifar` review,
allow self-review for the sole maintainer and permit only branch `main`.
An environment name in YAML alone does not establish these controls. Do not
bypass required review. Do not weaken other repository protections.

## Future publication procedure — NOT EXECUTED

1. Confirm owner PyPI setup and the GitHub environment controls. Review current
   name/version state and candidate release copy. The preparation README retains
   its truthful unpublished notice; before a publishing run, commit the reviewed
   release wording so that notice is absent from the candidate's own description.
   The upload job fails closed if it still contains that notice.
2. After explicit owner authorization, dispatch this workflow on `main` with
   `publish=true`. This new run builds its own pair and hashes. Earlier preparation
   hashes do not identify a new build, even for identical source.
3. Before approving `pypi`, inspect that run's exact SHA, checks, distributions,
   description and hashes. The owner approves only that concrete candidate pair.
4. After approval, the publish job downloads the same immutable artifact by ID,
   checks its manifest against the prepare job's digest and verifies every file's
   size/hash. It checks first-release name/version absence immediately before
   upload, failing on existing projects/versions or unexpected responses. This
   deliberately first-release workflow needs a reviewed update for later releases.
5. Only the isolated publish job has `id-token: write`; it runs no project checkout,
   build or installation. The official pinned PyPA Action uploads those exact bytes
   via Trusted Publishing, with default attestations. There is no `skip-existing`.
   A failure requires diagnosis; never silently rebuild or change reviewed bytes.

Publication is serialized with cancellation disabled and finite job timeouts.
Normal documentation/preparation commits may use `[skip ci]` to avoid the separate
long push CI; manually dispatch this workflow afterward. Do not claim the original
CI ran when it was skipped. No tags or GitHub Releases are needed by this workflow.

Primary references: [pending publishers](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/),
[publishing](https://docs.pypi.org/trusted-publishers/using-a-publisher/),
[security model](https://docs.pypi.org/trusted-publishers/security-model/),
[GitHub environments](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/manage-environments),
[manual workflows](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow),
[PyPI README guidance](https://packaging.python.org/en/latest/guides/making-a-pypi-friendly-readme/).
