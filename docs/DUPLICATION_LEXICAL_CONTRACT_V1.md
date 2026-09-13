# Duplication lexical contract v1

Status: **frozen**

Clone kind: `lexical_exact`

Canonical/fingerprint version: `lexical-exact-v1`

Supported same-language domains: Go, Java, JavaScript, Python, TypeScript

## 1. Scope

This contract defines only exact lexical equality for admitted D1 executable
body candidates. A clone relation exists exactly when two candidates in the
same detected-language domain have byte-identical canonical representations.
There is no similarity threshold or score.

This contract does not define structural clones, AST abstraction, identifier
normalization, literal abstraction, semantic equivalence, approximate matching,
cross-language matching, reporting, persistence, CLI behavior, Policy, SARIF,
or an Artifact schema.

## 2. Source and candidate preconditions

The input is one admitted D1 candidate and its `SelectedSyntax`:

- parsing must be `complete` or an aligned `recovered` selection;
- the candidate must meet all frozen D1 size floors;
- candidate language and selected-syntax language must agree;
- the half-open candidate byte span must be inside the selected source;
- lexical spelling is read from the evidence-aligned parser source, never from
  a compatibility rewrite and never from a filesystem path.

An unavailable/partial/malformed parse emits no canonical candidate. A body
below a D1 floor is not canonicalized. These states are not empty bodies and do
not participate in grouping.

## 3. Normalization

The following are ignored:

- comments identified by CPython `tokenize` or by the selected tree-sitter
  grammar;
- raw whitespace between tokens;
- formatting-only spacing and non-significant line-break changes;
- CRLF versus LF and accepted lone CR versus LF differences;
- a decoded BOM used only as an encoding marker;
- original indentation width when Python block structure is unchanged.

The following remain exact and ordered:

- token kinds and token count;
- identifiers, including spelling, case, and Unicode scalar sequence;
- literal spelling, delimiters, prefixes, suffixes, escapes, and values;
- keywords and modifiers;
- operators;
- punctuation, explicit separators, optional semicolons, and delimiters;
- token order;
- grammar-significant statement and block boundaries.

There is no Unicode NFC/NFD/NFKC normalization and no case folding. Therefore
identifier renames and literal changes do not match. Statement addition,
removal, or reordering does not match. Operator changes do not match.

Line endings are normalized to LF inside a retained multi-line token as part of
the accepted analysis view. Other whitespace inside a token (for example a
string or template literal) remains part of its exact spelling.

## 4. Lexical item stream

Every candidate becomes one ordered synthetic `statement_sequence`.

For Python:

- retained lexical items use CPython token kind names and their exact UTF-8
  spelling;
- `COMMENT`, non-significant `NL`, `ENCODING`, and `ENDMARKER` are discarded;
- logical `NEWLINE`, relative `INDENT`, and relative `DEDENT` are normalized
  zero-spelling markers;
- indentation entering or leaving the surrounding candidate body is excluded;
  indentation opened by a compound statement inside the candidate is retained.

For Go, Java, JavaScript, and TypeScript:

- every non-comment tree-sitter leaf wholly inside the candidate span is a
  token whose kind is the grammar leaf type and whose spelling comes from the
  aligned pre-recovery parser source;
- generic zero-spelling start/end markers surround contained statement,
  declaration, clause, case, and block/statement-container nodes;
- the markers contain no AST node type or abstract semantic value. Their sole
  purpose is to retain grammar-significant boundaries, including JavaScript
  and Go line-terminator-sensitive parses, while ignoring line movement that
  does not change the selected parse.

Token text alone is not an equality oracle: kind and spelling are both retained.
Python docstrings and JavaScript string-expression statements are literals, not
comments.

## 5. Canonical bytes

Canonical bytes begin with the ASCII magic including its final NUL:

```text
ARCHLENS-DUPLICATION-LEXICAL\0
```

Every following field uses this implementation-independent frame:

```text
frame(value) = uint64_big_endian(byte_length(value)) || value
```

Strings are encoded as strict UTF-8. The stream is:

```text
magic
frame("lexical-exact-v1")
frame(detected_language)
frame("sequence:start")
for each lexical item:
    frame("token" | "marker")
    frame(item_kind)
    frame(exact_lexeme_or_empty_marker_spelling)
frame("sequence:end")
```

For example, `frame("x")` is hexadecimal
`000000000000000178`. No `repr`, native `hash()`, locale, timestamp, random
identity, path, source coordinate, or machine-specific value enters canonical
bytes. Repeated canonicalization of the same selected input must produce
identical bytes.

## 6. Fingerprint

The fingerprint is an index, not the equality oracle. Its payload is:

```text
frame("archlens-duplication-fingerprint")
|| frame("lexical-exact-v1")
|| frame(detected_language)
|| frame(canonical_bytes)
```

The runtime fingerprint is `sha256:` followed by lowercase SHA-256 hexadecimal.
Canonical bytes already bind their language and version; consequently identical
canonical bytes deterministically produce the same fingerprint. Paths, lines,
unit kinds, repository identity, timestamps, UUIDs, and random values do not
enter it.

After a fingerprint bucket match, grouping must compare canonical bytes. An
injected or real digest collision must never merge unequal canonical streams.

## 7. Exact grouping and identity

Grouping is independent for each
`(detected_language, fingerprint_version, fingerprint)` bucket:

1. validate portable relative POSIX paths and occurrence identities;
2. deduplicate identical occurrence coordinates;
3. split the digest bucket by exact canonical-byte equality;
4. retain equality classes with at least two distinct coordinates.

The duplicate-occurrence coordinate is:

```text
(relative_posix_path, start_line, end_line, unit_kind)
```

It is authoritative. Its deterministic convenience ID is `do1:` plus SHA-256
of the framed namespace `archlens-duplication-occurrence` and the framed
coordinate. A repeated coordinate with different language or canonical content
is an invariant failure, never two occurrences.

Occurrence order is Unicode code-point path order, then start line, end line,
and unit kind. A retained group receives:

```text
group_id = "dg1:" + lowercase_hex(SHA-256(
    frame("archlens-duplication-group")
  || frame("lexical-exact-v1")
  || frame(fingerprint)
  || frame(each sorted occurrence coordinate field)
))
```

Groups are ordered by language, fingerprint, sorted occurrence-coordinate
tuple, then group ID. Enumeration and hash-map order are unobservable.

Distribution is `same_file` when all occurrences share one path,
`cross_file` when every occurrence is in a different path, and `mixed` when
multiple paths participate and at least one contributes multiple occurrences.

D2 performs no structural or nested-dominance suppression. Whole admitted body
candidates are the only population.

## 8. Validation and classification

The frozen corpus and reviewed relation oracle are under
`validation/duplication_d2_20260818/`. The clean-room oracle does not import the
production canonicalizer or grouper. Validation covers canonical-byte equality,
exact and non-exact relations, five languages, injected collisions,
deterministic ordering, same-/cross-/mixed groups, Unicode and portable paths,
BOM/CRLF/lone-CR, and malformed/unavailable syntax.

A disagreement is classified before correction as one of:

- extraction;
- lexical contract;
- canonical bytes;
- grouping;
- parser limitation.

It is not automatically classified as a production implementation defect.

## 9. Remaining boundary

Structural canonicalization, structural grouping/suppression, CLI and reports,
Artifact formats, persistence, Policy metrics, SARIF findings, similarity
percentages, and semantic claims remain outside D2.

Navigation: [usage](USAGE.md) · [project README](../README.md).
