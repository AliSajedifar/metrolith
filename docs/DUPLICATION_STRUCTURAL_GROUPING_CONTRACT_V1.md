# Duplication Structural Grouping Contract v1

Status: frozen for D3-B. This contract covers internal structural clone
grouping and maximality suppression only. It adds no command, output format,
artifact schema, persistence, Policy behavior, SARIF, GitHub Action, hotspot
behavior, percentage, density, or score.

## Inputs and equality

The input is a finite collection of admitted D1 candidates for which D3-A
produced `structural-v1` canonical bytes and fingerprints. The five independent
match domains are Go, Java, JavaScript, Python, and TypeScript.

A structural group equality class has one language, one fingerprint version,
and byte-for-byte equal canonical streams. Implementations first bucket by
`(language, fingerprint_version, fingerprint)` and then split every bucket by
exact `canonical_bytes` equality. A SHA-256 fingerprint is only an index. Equal
fingerprints never establish equality by themselves.

An equality class becomes an initial duplicate group only when it contains at
least two distinct occurrences.

## Occurrences

The authoritative occurrence coordinate is:

```text
(relative_posix_path, start_line, end_line, unit_kind)
```

Paths are normalized repository-relative POSIX text. Absolute paths, drive
paths, backslashes, empty/dot/parent segments, and NUL are rejected. Lines are
one-based inclusive source coordinates. The unit kind is the D1 body kind.

The deterministic occurrence ID is:

```text
"do1:" + hex(SHA-256(
    frame("archlens-duplication-occurrence")
  + frame(relative_posix_path)
  + frame(uint64(start_line))
  + frame(uint64(end_line))
  + frame(unit_kind)
))
```

An identical coordinate with identical candidate and structural content is
deduplicated. Conflicting content at one coordinate fails grouping. When an
adapter supplies the same exact byte span under multiple unit labels, equal
content is deterministically deduplicated using the frozen D1 unit-kind
priority; conflicting content fails grouping.

One path cannot contain multiple detected languages. Equal canonical bytes in
one language/version domain cannot carry inconsistent fingerprints.

## Group identity and order

Occurrences are sorted by Unicode code-point path order, start line, end line,
and unit kind. The deterministic group ID is:

```text
"dg1:" + hex(SHA-256(
    frame("archlens-duplication-group")
  + frame(fingerprint_version)
  + frame(fingerprint)
  + frame(each sorted occurrence coordinate field)
))
```

There are no UUIDs, timestamps, random values, filesystem enumeration ordinals,
or display ordinals in machine identity.

Retained groups use this total order: language, fingerprint, sorted occurrence
coordinate tuple, then group ID. Suppression evidence uses child group order,
parent group order, then occurrence-pair order. Repeated runs and input
permutations therefore produce equal values.

## Distribution and safe span metadata

- `same_file`: every occurrence is in one file.
- `cross_file`: occurrences span files and each file contributes exactly one.
- `mixed`: occurrences span files and some file contributes more than one.

Every group exposes occurrence count and file count. Its source-span union is a
sorted tuple of inclusive path-local line intervals. Overlapping or adjacent
intervals in one path merge; intervals from different paths never merge. The
derived line count sums those disjoint path-local components. It is not a
duplication percentage, avoidable-line estimate, density, score, or Policy
metric.

## Laminar overlap invariant

Within one file, accepted structural occurrence byte spans must be laminar:
they are disjoint, identical, or one strictly contains the other. Identical
spans are handled by the duplicate-occurrence rule above. Any partial
intersection fails grouping explicitly. No winner is guessed.

## Maximal structural groups

For two initial groups `G` (inner) and `H` (outer), `H` dominates `G` exactly
when all of these hold:

1. both have the same language and structural fingerprint version;
2. both have the same occurrence count;
3. a bijection maps every occurrence of `G` to one occurrence of `H`;
4. every mapped pair is in the same file and the `H` byte span strictly
   contains the `G` byte span; and
5. every occurrence of `H` participates once.

Every dominated inner group is suppressed, leaving maximal retained groups.
All valid direct or transitive initial-group dominance relationships remain as
explicit evidence, even when a parent is itself dominated. The evidence stores
child and parent group IDs plus a deterministic occurrence-ID mapping.

If more than one complete bijection exists, the lexicographically first
mapping is stored and `mapping_ambiguous` is true. Suppression is still safe
because every mapping relates the same complete child and parent populations;
the ambiguity is exposed rather than silently discarded. A missing complete
bijection means no suppression.

Consequences:

- an inner group with a third independent occurrence remains when two outer
  occurrences clone;
- nested groups with different occurrence populations remain;
- an inner-only clone remains;
- disjoint sibling groups remain independently;
- identical-span duplicates collapse before grouping; and
- partial overlaps fail closed.

This selection is maximal only within admitted whole-body D1 candidates. It
does not claim maximal token-substring clone discovery.

## Failure and phase boundary

Malformed or unavailable syntax produces no structural occurrence and cannot
be grouped. Invalid admitted occurrences, noncanonical paths/IDs/versions,
conflicting duplicates, inconsistent equal-byte fingerprints, and partial
overlaps fail with a typed grouping invariant error.

D3-B returns typed in-memory groups and suppression evidence. D4 integration,
CLI/reporting, schemas, serialization, persistence, Policy, SARIF, GitHub
Action, and hotspot changes are outside this contract.

Navigation: [usage](USAGE.md) · [project README](../README.md).
