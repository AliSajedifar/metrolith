# Examples

The beginner example is `local_project/app.py` and `local_project/web.js`.
Use CPython 3.13 (`>=3.13,<3.14`; tested 3.13.9) and Git on PATH.
Git is required even though these files are not a Git repository.
Run it without network access after installation:

```console
metrolith example run --local
```

The command copies the packaged sources into controlled temporary storage and
runs the normal analysis pipeline. Read the printed Run path and `summary.md`;
`metrolith report RUN` creates an offline HTML report.

From the source project you can use the same files directly:

```console
metrolith analyze examples/local_project --workspace ../metrolith-demo
```

`quickstart_repositories.csv` supports the existing optional `example run`
language selections. It contains exact frozen commits; network access or an
already populated cache is required. `input_template.csv` documents the CSV
columns for explicit repository-list workflows and is not an offline demo.

`ratchet-rules.json` is an illustrative rule array for `baseline capture`.
Choose your own tolerance. Capture requires a qualifying exact-revision Run;
the baseline's `.source-run/` sidecar and the current CLI ancestry limitation
are explained in [USAGE](../docs/USAGE.md) in the source distribution.

Start with the [source setup](../README.md); see [USAGE](../docs/USAGE.md)
for quoted Run paths and the example wrapper's exit 1 on preflight refusal.
