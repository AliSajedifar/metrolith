# Analysis Scope Hash — construction contract 2.0.0

**Status:** normative for `ANALYSIS_SCOPE_HASH_VERSION = "2.0.0"`.
**Applies to:** the value recorded as `analysis_scope_hash` in Artifact Schema
1.7 repository documents whose `analysis_scope_hash_version` is `2.0.0`.
**Supersedes:** the `analysis_scope_hash` *description* embedded in
`repository_document-1.7.schema.json`, for hash construction 2.0 only.
**Ratified:** 2026-08-09.

---

## 1. Why this document exists separately

The Artifact 1.7 schema bytes are shipped and real 1.7 artifacts exist on disk.
Editing a published schema file to correct prose would put two different byte
sets into the world both claiming to be Artifact Schema 1.7.0 — the exact hazard
decision D-6 exists to prevent — so the schema is deliberately left untouched.

A new Artifact Schema version was likewise **not** created, because the
persisted field, its type and its purpose are unchanged. What changed is the
*construction* of the digest, and `analysis_scope_hash_version` is the field
that exists to carry precisely that.

So the correction lives here, versioned to the hash construction rather than to
the artifact contract.

**The schema description is stale in one specific clause.** It says the digest
covers "Git mode/symlink evidence". For construction 2.0 that is superseded by
§3 and §4 below. Correct the schema text at the next *real* Artifact Schema
bump, never as a documentation-only version.

---

## 2. What the digest is for

`analysis_scope_hash` is the **exact identity of the analyzed scope**: the
materialization ArchLens actually measured.

* Equality proves the analyzed source scope was identical.
* **Inequality alone does not make two runs incomparable.** Comparability is
  multidimensional — `subject_key`, `analyzed_commit_sha`, this digest, and the
  contract versions are separate dimensions and must never be collapsed into
  one.

It is **not** a filesystem manifest. Files ArchLens excludes cannot affect any
metric, so they cannot affect this digest; `filesystem_manifest_hash` is the
separate, deliberately broader value and is explicitly *not* the identity.

---

## 3. What enters the digest

For **every file with `included_in_metrics == true`**, and nothing else, in
this field order, joined by `\x1f`, one record per line, lines sorted, digested
with SHA-256 and rendered as `sha256:<hex>`:

| # | Field | Definition |
|---|---|---|
| 1 | **normalized analyzed relative path** | The repository-relative path of the analyzed file, with `\` normalized to `/`. Absolute host paths, temporary worktree paths and cache locations never appear. |
| 2 | **exact analyzed content identity** | `content_hash`: the digest of **the bytes that were parsed**. No normalization is applied here and none may be introduced. |
| 3 | **derived `scope_file_kind`** | One of `regular`, `symlink`, `submodule`. Derived from the record's own evidence — see §4. |
| 4 | **symlink target evidence** | `git_symlink_target`, or the empty string when there is none. What a symlink points at determines what would be analyzed. |

Absent values are rendered as the empty string, so "absent" and "present but
empty" are the same byte sequence by design; no field here distinguishes them
meaningfully.

### 3.1 The bytes hashed are the bytes parsed

This is load-bearing and is **not** weakened by construction 2.0. On Windows a
clean worktree at commit *X* holds CRLF bytes while an exact revision
materialization of commit *X* holds canonical LF bytes. Those are genuinely
different analyzed bytes, and they **must** produce different digests while
remaining the same subject at the same revision. Recording that is evidence;
engineering it away would manufacture an equality that does not exist.

A representation-normalized identity would need defensible answers for
`.gitattributes`, `working-tree-encoding`, clean/smudge filters, binary files,
symlinks and exec bits. That is a separate concept for a later phase, not a
partial reimplementation of Git normalization bolted on to force agreement.

---

## 4. `scope_file_kind` — metric-relevant file kind

```
submodule   the record is a Git submodule
symlink     the record is a symlink (Git-reported or native filesystem)
regular     everything else
```

Derived from `exclusion_reason` (`git_submodule` / `git_symlink` / `symlink`),
falling back to `is_git_submodule` and `is_git_symlink`. Deriving it rather than
reading a raw mode is what lets the kind be established from **native**
filesystem evidence when Git evidence is unavailable.

A symlink and the file it points to are not the same source, so file kind is
genuinely metric-relevant and stays in the digest.

> Today this is belt and braces: symlinks and submodules are the first three
> exclusion reasons applied, so they are never `included_in_metrics` and never
> reach the digest. Keeping the concept means the guarantee stays correct if
> that exclusion policy is ever revisited.

---

## 5. What is excluded, and why — regular-file Git mode

**A regular file's Git mode (`100644`, `100755`) is provenance evidence and does
not enter scope identity.**

The test applied is whether the value can affect **source selection, parsing, or
metric computation**. For a regular file's mode the answer is no, established by
inspection of the whole production tree:

* `git_mode` is compared against exactly two values anywhere in ArchLens:
  `120000` (symlink) and `160000` (submodule);
* the literals `100644` and `100755` appear in **no** comparison in any
  production module — no inclusion rule, no exclusion rule, no parser
  dispatch, no metric derivation reads them.

Under construction **1.0.0** the raw mode string *was* hashed, and the
consequence was a false inequality:

| Acquisition path | `git_mode` observed | Analyzed bytes |
|---|---|---|
| checkout (remote clone) | `100644` | identical |
| `git archive` materialization | *(none — not a Git repository)* | identical |

Two source modes at the **same commit**, analyzing **byte-identical** regular
files, produced two different digests — because one materialization can observe
Git metadata about itself and the other cannot. That made the digest record
*observability of provenance*, not analyzed scope, in the one value whose stated
purpose is exact analyzed-scope identity.

Empty evidence is not the same fact as mode `100644`, which is why the fix is to
remove the field from the digest rather than to coerce absent modes to a
default.

**The exec bit.** `100755` is likewise excluded. If ArchLens ever begins to make
a selection, parsing or metric decision from the exec bit, this exclusion must be
re-made deliberately — it is not a permanent judgement about executability, it is
a judgement about metric relevance today. `tests/test_local_analysis.py`
mechanically fails if any production path starts reading either literal, so the
decision cannot lapse silently.

---

## 6. Versioning rules applied here

| Change | Version moved | Rationale |
|---|---|---|
| Digest construction | `ANALYSIS_SCOPE_HASH_VERSION` 1.0.0 → **2.0.0** | The constant exists to record a change in what the hash covers. |
| Artifact Schema | **unchanged at 1.7.0** | The persisted field, its type and its meaning are unchanged. A construction correction is not a contract change, and a documentation fix is never grounds for a version bump. |
| Semantic Projection | 2.0.0 → **2.1.0** | Separate change: the projection now carries `analysis_scope_hash_version` alongside the digest, so a construction difference is visible rather than silent. See §7. |

**Cross-version comparison.** Two artifacts recording different
`analysis_scope_hash_version` values were produced by different constructions.
Their digests are **not comparable as equality evidence**, even if the strings
happen to match. Consumers must compare the version first; the semantic
projection does this from 2.1.0 onward.

---

## 7. Consumer obligations

1. Read `analysis_scope_hash_version` before comparing two `analysis_scope_hash`
   values. Equal digests under different constructions are not evidence of an
   equal scope.
2. Treat an **absent** `analysis_scope_hash_version` as its own state. It is not
   `1.0.0` and it is not `2.0.0`; it means the producer recorded no construction
   identity, and no equality claim may be derived from the digest.
3. Never treat digest inequality as a comparability verdict (§2).

---

## 8. History

| Version | Change |
|---|---|
| 1.0.0 | Initial construction: path, content hash, **raw `git_mode`**, `is_git_symlink` flag, `git_symlink_target`. |
| 2.0.0 | Raw `git_mode` replaced by derived `scope_file_kind`; the separate `is_git_symlink` flag folded into that kind. Content, path and symlink-target semantics unchanged. Motivated by the checkout-vs-archive false inequality in §5, surfaced by Direct Revision Diff. |

Navigation: [usage](USAGE.md) · [project README](../README.md).
