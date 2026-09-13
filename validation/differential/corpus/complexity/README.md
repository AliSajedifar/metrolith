# Complexity corpus

This synthetic corpus records per-construct expectations for the Complexity
Contract. Expectations were derived independently from the normative rules;
preserve their original bytes and provenance. Per-construct contributions,
including zero increments, distinguish rule errors that could cancel in totals.

Each language directory supplies source inputs and `expectations.json`.
The maintained complexity/callable tests read them directly. From the project
root, the independent Go arithmetic/location checker runs as:

```console
python validation/differential/corpus/complexity/go/check_expectations.py
```

It checks declared spans, code-line counts, decision/boolean contributions and
their totals without importing the production engine. A mismatch requires
investigation against the contract, not regeneration from engine output.
