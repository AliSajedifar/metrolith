# ArchLens Conformance Corpus — Review Guide

**Corpus version:** 1.0.0
**Metric Contract:** 3.0.0 · **Exclusion Policy:** 1.5.0 · **Inventory Schema:** 1.6.0

## The oracle rule

**Current ArchLens output is not the oracle.** A case whose expected values were
copied from a program run would pass forever and prove nothing, including after
the behaviour it claims to pin silently regressed.

Every value in `expected/<case-id>.json` is derived by a human reviewer from the
Metric Contract clause quoted in that case's `metric_contract_clause` field, and
is written down *before* the case is run. When a hand-derived expectation and the
program disagree, that disagreement is the finding — it is investigated, not
edited away.

## What each case records

| Field | Meaning |
|---|---|
| `id` | Stable case identifier; also the directory name under `cases/` |
| `purpose` | What contract behaviour the case pins |
| `metric_contract_clause` | The exact normative clause the expectation comes from |
| `input_path` | Case input, relative to `cases/` |
| `input_sha256` | SHA-256 of the input bytes; detects silent fixture drift |
| `observed_metric_paths` | The result paths the case asserts on, and only those |
| `notes` | Line-by-line derivation where the count is not self-evident |
| `reviewer`, `review_date`, `review_status` | Provenance of the human review |

`review_status` must be `reviewed` or `reviewed_with_notes`. Any other value
makes the runner refuse the case rather than run it: an unreviewed oracle is not
evidence.

## Adding a case

1. Write the input file under `cases/<case-id>/`.
2. Find the normative clause in `docs/METRIC_CONTRACT_V3.md`. If no clause
   covers the behaviour, stop — the contract is what needs changing first.
3. Derive every expected value by hand from that clause. Record the derivation
   in `notes` whenever a reader could not reproduce the count at a glance.
4. Write `expected/<case-id>.json`.
5. Add the manifest entry with reviewer, date, and status.
6. Run `python -m validation.conformance run --case <case-id>`.
7. If it fails, determine which side is wrong before touching either.

## Deliberately excluded entity kinds

Several cases exist specifically to pin what does **not** count, because a
silent inclusion is much harder to notice than a silent omission:

- `entity-java-interface-is-secondary` — interfaces are secondary, and a
  signature-only method contributes nothing.
- `entity-java-constructor-is-secondary` — ordinary Java constructors.
- `entity-python-init-is-secondary` — Python `__init__`.
- `scope-verified-zero-entities` — a **verified complete** zero, which the
  contract distinguishes from a partial zero.

## Running

```text
python -m validation.conformance run
python -m validation.conformance run --case <id>
python -m validation.conformance run --format json
python -m validation.conformance list
```

No network access and no repository acquisition occur. The corpus loads through
`importlib.resources`, so it runs identically from an installed wheel.

## Platform behaviour

A case may declare a `platforms` list. Cases without one run everywhere. Cases
whose expected behaviour genuinely differs between Windows and Ubuntu must
declare the platform explicitly rather than encoding one platform's result as
universal.
