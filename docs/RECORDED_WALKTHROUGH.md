# A recorded walk through ambient-code/platform

Open the stable [recorded Story](https://metrolith.dev/demo), then follow its
links into Explorer. This is a reading exercise over retained public evidence,
not a new repository analysis, a benchmark or a defect assessment. The README
preview is an unaltered browser capture of this Story; the local CLI's
`metrolith report` creates a different, offline HTML report.

## Know which evidence you are reading

| Field | Recorded value |
|---|---|
| Repository | [ambient-code/platform](https://github.com/ambient-code/platform) |
| Analyzed revision | [`cc8f5ad5fb209bd31a7790187a36055c94db90ee`](https://github.com/ambient-code/platform/tree/cc8f5ad5fb209bd31a7790187a36055c94db90ee) |
| Comparison base | `f4e07dfefc5053de8681b129a5467bd3ec43100d` |
| Run ID | `7b8d7b513467` |
| Original producer | Program **3.8.0**, recorded in `binding.programVersion` |
| Later evaluator | Program **4.0.0**, local checkout `9827fd16b21f266ed8eae2ef03a1c7349a5073d4`; locally trusted only |
| Contracts | Metric 3.0.0; Complexity 2.0.0; Exclusion 1.5.0; Inventory 1.7.0; Artifact 1.12.0; Report View 1.0.0 |
| Recorded runtime | CPython 3.13.9; Tree-sitter 0.25.2; Go grammar 0.25.0, Java 0.23.5, JavaScript 0.25.0, TypeScript 0.23.2 |
| Run state | `completed`; core measurement outcome `complete`; reader `finalized_valid` |
| Evaluation boundary | `local_unprotected`; no protected organizational approval |

The viewer's 4.0.0 heading is not the original producer identity. Neither this
recorded run nor its evaluator is being identified as the published PyPI 4.0.0
artifact. The model's architecture label comes from supplied input metadata,
not inferred architecture ground truth.

The accessible [derived model](https://metrolith.dev/recorded/data/metrolith-model.json)
is the evidence carrier inspected for this walkthrough. Its captured SHA-256 is
`89762e6e76e5f0354a4b9b6c42a88f0a300291e7a582b239c44ce26a480a7b44`. It records values and source pointers; it is not an
authoritative Run, Policy input or Ratchet input. Its `sourceDocuments` describes
retention of original documents, but the underlying Report View and original
supplement URLs were not retrievable from the public service during this check.
We therefore verify the downloaded model's bytes, not missing original bytes or
the origin digests it cites. Explorer's Trace displays those recorded identities;
it does not cryptographically verify them.

Configuration is identified by `trust.measurementSemantics`: fingerprint
`0c94ee26f8329eca28a359a44ad8828557f3d6fbe84f8d5bd78c657168d7ddaa`,
effective exclusions `097423cf231f1b7d43dc3b9c7546fc0584a31fc1f081a0b56028fbefb9a1cda7`,
and metric options `433639edf3acbd3822d4f309258615dc6db505542183e60e4d9d341acc2e371c`.
These identify recorded configuration; they do not recover absent original settings
or justify synthesizing a reproduction command.

## 1. Count the population before interpreting its size

The [Measured chapter](https://metrolith.dev/recorded/index.html#measured) shows
144,668 code lines, 1,114 source files, 521 classes/structs and 4,306
methods/functions. Go contributes 65,978 LOC, TypeScript 58,645, Python 18,598
and JavaScript 1,447. The [Scope chapter](https://metrolith.dev/recorded/index.html#scope)
accounts for 1,629 recognized files: 1,114 measured plus 515 excluded
(368 test and 147 generated). One recovered parse remains in the measured population.

Open **Inspect all exclusions** and follow a path's provenance. Excluded tests
are outside this measurement scope, not missing project files. Java has zero
source files here; detailed Java metrics are not applicable. Do not turn those
states into unavailable data or a language-quality comparison.

## 2. Investigate a callable without inventing a score

The [Complexity chapter](https://metrolith.dev/recorded/index.html#complexity)
records 4,306 callables, cyclomatic total 19,551, maximum 161, arithmetic mean
4.5404 and lower median 2. Use **Sort the ledger by cyclomatic complexity** to
inspect individual declarations and their recorded source pointers. The largest
value is a place to read, not a proven defect or a universal threshold.

This Report View has no frequency distribution and no repository cognitive
aggregates. Do not infer a histogram or population-wide cognitive score from
those omissions. Per-callable cognitive values are a separate available surface.

## 3. Keep repetition and its coverage together

The [Repetition chapter](https://metrolith.dev/recorded/index.html#repetition)
shows **partial** lexical coverage: 19 groups and 48 occurrences. Structural
evidence is also **partial**: 95 initial groups, 6 suppressed, 89 retained and
258 occurrences. Its 1,114 eligible files include 1,090 candidate-complete and
24 candidate-unavailable files. Those unavailable candidates explain why the
group count is not a complete repository duplication claim.

Open the persisted Go structural group with nine occurrences across nine files,
then use Trace to inspect paths and line ranges. Lexical and structural definitions
overlap, so do not add their totals. The public view contains locations rather
than copied source or diff bodies. Reading the exact repository revision is the
next human step; repetition alone does not establish a maintenance defect.

The subsequent Policy/Ratchet chapters illustrate user-selected rules and a
local gate. Their failures are not universal judgments. The final synthetic
refusal lane is explicitly separate from this repository and contributes none
of the observations above.
