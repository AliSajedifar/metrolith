"""Draft 2020-12 schema loading and structural validation.

Schema semantics are provided entirely by the standard ``jsonschema`` library
(decision D-4). ArchLens reimplements no keyword behavior. This module only:

* resolves schema documents as package resources, so they load from an installed
  wheel outside any source checkout (plan section 7.3);
* offers a stable ``name -> schema`` registry for ``archlens schema list`` and
  ``archlens schema export``;
* assigns each loaded resource the canonical URI derived from its registered
  filename, without rewriting frozen schema files; and
* converts library validation errors into the ArchLens structural error
  taxonomy.

**Missing dependency is a hard failure.** If ``jsonschema`` is unavailable this
module raises a typed error. It never returns "valid" for an unchecked document,
because a silent pass would be indistinguishable from real conformance.

**Independence.** Structural schema conformance is not semantic validity.
``validation.scripts.validate_outputs`` recomputes scientific invariants on its
own and must not treat a schema pass as evidence of semantic correctness.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from functools import lru_cache
from typing import Any, Iterator

from .errors import StructuralError, StructuralErrorCode, raise_structural

SCHEMA_PACKAGE = "validation.resources"
SCHEMA_DIRECTORY = "schemas"
SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"
SCHEMA_BASE_URI = "https://archlens.dev/schemas/"

# Seven already-published files carry their predecessor's `$id`. Their bytes
# are frozen, so the loader corrects identity in memory instead of rewriting
# evidence. This exact allowlist is also an authoring guard: any new mismatch,
# or any different value in one of these files, is a schema defect.
FROZEN_SCHEMA_ID_OVERRIDES: dict[str, str] = {
    "analysis-1.10.schema.json": f"{SCHEMA_BASE_URI}analysis-1.9.schema.json",
    "callable_row-1.10.schema.json": (
        f"{SCHEMA_BASE_URI}callable_row-1.9.schema.json"
    ),
    "explain_output-1.3.schema.json": (
        f"{SCHEMA_BASE_URI}explain_output-1.2.schema.json"
    ),
    "repository_document-1.10.schema.json": (
        f"{SCHEMA_BASE_URI}repository_document-1.9.schema.json"
    ),
    "revision_diff-1.2.schema.json": (
        f"{SCHEMA_BASE_URI}revision_diff-1.1.schema.json"
    ),
    "run_manifest-1.10.schema.json": (
        f"{SCHEMA_BASE_URI}run_manifest-1.9.schema.json"
    ),
    "run_status-1.10.schema.json": (
        f"{SCHEMA_BASE_URI}run_status-1.9.schema.json"
    ),
}

# Exact required release. Must equal the `jsonschema==` pin in pyproject.toml;
# a test asserts the two agree. ArchLens refuses to validate against any other
# release, because a different validator build can change which constraints are
# enforced and would make acceptance evidence non-reproducible.
REQUIRED_JSONSCHEMA_VERSION = "4.26.0"

# Registry of every published schema: logical name -> (filename, format version).
# `archlens schema list` renders this table, so the ordering is deliberate:
# authoritative run documents first, then tabular rows, then derived formats.
SCHEMA_REGISTRY: dict[str, tuple[str, str]] = {
    # Authoritative run documents. Artifact Schema 1.6.0 is CORRECTION-ONLY:
    # four documents changed to stop contradicting their own producers
    # (F-P3-3..F-P3-6). The rest are byte-identical to 1.5.0 and are shared
    # rather than duplicated under a new name — a schema file name is not the
    # artifact version, and copying unchanged files would add drift surface for
    # no benefit.
    # Artifact Schema 1.9.0 adds per-callable complexity. Five existing documents
    # gained a described field and move to 1.9; `environment` and every row
    # contract not listed here are unchanged and are shared, not duplicated.
    # Artifact Schema 1.10.0 adds ArchLens Cognitive Complexity under Complexity
    # Contract 2.0.0. Five documents gained a described field and move to 1.10;
    # every row contract not listed here -- `contribution_row` included -- is
    # unchanged and is SHARED, not duplicated. The producer -> schema audit in
    # validation/complexity_g1c_20260811 records why the contribution ledger
    # does not move: cognitive shares the structural traversal, so a second
    # per-file status would be one fact and a drift surface.
    # Artifact Schema 1.11.0 adds the conditional benchmark qualification
    # authority. Exactly four documents move: `run_manifest` gains the mode,
    # the artifact hash binding and the readiness verdict, and the three checked
    # row contracts gain the qualification projection. `analysis` and
    # `repository_document` are UNCHANGED and are deliberately shared at 1.10
    # rather than copied -- measurement did not move, and minting an
    # `analysis-1.11` whose bytes equal 1.10 would add a drift surface while
    # implying a measurement-contract change that R0 explicitly does not make.
    # Artifact Schema 1.12.0 changes only evaluator provenance. The active
    # manifest/environment schemas allow a null Git dirty state only when the
    # explicit provenance kind/state explains it. Every 1.11 predecessor stays
    # frozen and is registered under a historical name.
    "run_manifest": ("run_manifest-1.12.schema.json", "1.12.0"),
    "run_status": ("run_status-1.10.schema.json", "1.10.0"),
    "environment": ("environment-1.12.schema.json", "1.12.0"),
    "analysis": ("analysis-1.10.schema.json", "1.10.0"),
    "repository_document": ("repository_document-1.10.schema.json", "1.10.0"),
    # The qualification authority and its input registry. Both carry their own
    # format version, independent of Artifact Schema: the registry is an input
    # contract whose shape does not move when a run bundle's does.
    "benchmark_qualification": ("benchmark_qualification-1.0.schema.json", "1.0.0"),
    "benchmark_qualification_registry": (
        "benchmark_qualification_registry-1.0.schema.json", "1.0.0",
    ),
    # Artifact Schema 1.11 predecessors of the two provenance documents 1.12
    # changed. Environment had remained byte-identical since 1.5.
    "run_manifest_1_11_historical": ("run_manifest-1.11.schema.json", "1.11.0"),
    "environment_1_5_historical": ("environment-1.5.schema.json", "1.5.0"),
    # Artifact Schema 1.10 predecessor of the one run document 1.11 changed.
    "run_manifest_1_10_historical": ("run_manifest-1.10.schema.json", "1.10.0"),
    # Artifact Schema 1.9 predecessors of the five documents 1.10 changed.
    "run_manifest_1_9_historical": ("run_manifest-1.9.schema.json", "1.9.0"),
    "run_status_1_9_historical": ("run_status-1.9.schema.json", "1.9.0"),
    "analysis_1_9_historical": ("analysis-1.9.schema.json", "1.9.0"),
    "repository_document_1_9_historical": (
        "repository_document-1.9.schema.json", "1.9.0",
    ),
    "callable_row_1_9_historical": ("callable_row-1.9.schema.json", "1.9.0"),
    # Artifact Schema 1.7 predecessors of the five documents 1.9 changed.
    # Aliases onto the EXISTING 1.7 files: a schema file name is not the
    # artifact version, and copying unchanged bytes adds drift surface.
    "run_manifest_1_7_historical": ("run_manifest-1.7.schema.json", "1.7.0"),
    "run_status_1_7_historical": ("run_status-1.7.schema.json", "1.7.0"),
    "analysis_1_7_historical": ("analysis-1.7.schema.json", "1.7.0"),
    "repository_document_1_7_historical": (
        "repository_document-1.7.schema.json", "1.7.0",
    ),
    # Artifact Schema 1.6 predecessors, retained for historical artifacts.
    "run_manifest_1_6_historical": ("run_manifest-1.6.schema.json", "1.6.0"),
    "run_status_1_6_historical": ("run_status-1.6.schema.json", "1.6.0"),
    "analysis_1_6_historical": ("analysis-1.6.schema.json", "1.6.0"),
    "repository_document_1_6_historical": ("repository_document-1.6.schema.json", "1.6.0"),
    "log_event": ("log_event-1.5.schema.json", "1.5.0"),
    # Inventory keeps its own independent schema version. 1.7.0 is the
    # correction-only pairing for Artifact Schema 1.6.0.
    "file_inventory": ("file_inventory-1.7.schema.json", "1.7.0"),
    # Frozen predecessors, retained byte-for-byte for historical artifacts.
    "run_manifest_1_5_historical": ("run_manifest-1.5.schema.json", "1.5.0"),
    "run_status_1_5_historical": ("run_status-1.5.schema.json", "1.5.0"),
    "analysis_1_5_historical": ("analysis-1.5.schema.json", "1.5.0"),
    "repository_document_1_5_historical": ("repository_document-1.5.schema.json", "1.5.0"),
    "file_inventory_1_6_historical": ("file_inventory-1.6.schema.json", "1.6.0"),
    # Tabular row contracts (Artifact Schema 1.5).
    "catalog_row": ("catalog_row-1.11.schema.json", "1.11.0"),
    "catalog_row_1_7_historical": ("catalog_row-1.7.schema.json", "1.7.0"),
    "catalog_row_1_5_historical": ("catalog_row-1.5.schema.json", "1.5.0"),
    "sheet_metrics_row": ("sheet_metrics_row-1.11.schema.json", "1.11.0"),
    "sheet_metrics_row_1_7_historical": ("sheet_metrics_row-1.7.schema.json", "1.7.0"),
    "sheet_metrics_row_1_5_historical": ("sheet_metrics_row-1.5.schema.json", "1.5.0"),
    "language_metrics_row": ("language_metrics_row-1.11.schema.json", "1.11.0"),
    "language_metrics_row_1_7_historical": (
        "language_metrics_row-1.7.schema.json", "1.7.0",
    ),
    "language_metrics_row_1_5_historical": ("language_metrics_row-1.5.schema.json", "1.5.0"),
    "errors_row": ("errors_row-1.7.schema.json", "1.7.0"),
    "errors_row_1_5_historical": ("errors_row-1.5.schema.json", "1.5.0"),
    # Artifact Schema 1.8.0 is CORRECTION-ONLY and changes exactly one
    # contract: `analyzed_commit_sha` becomes nullable here, matching every
    # sibling row schema in 1.7 and the state a local snapshot actually
    # produces. Every other 1.7 schema is byte-identical under 1.8 and is
    # shared rather than duplicated -- a schema file name is not the
    # artifact version.
    "recoveries_row": ("recoveries_row-1.8.schema.json", "1.8.0"),
    "recoveries_row_1_7_historical": ("recoveries_row-1.7.schema.json", "1.7.0"),
    "recoveries_row_1_5_historical": ("recoveries_row-1.5.schema.json", "1.5.0"),
    "frozen_input_row": ("frozen_input_row-1.5.schema.json", "1.5.0"),
    "retry_input_row": ("retry_input_row-1.5.schema.json", "1.5.0"),
    "normalized_input_row": ("normalized_input_row-1.7.schema.json", "1.7.0"),
    "normalized_input_row_1_5_historical": ("normalized_input_row-1.5.schema.json", "1.5.0"),
    # 1.9 adds three columns: per-file structural/nloc status and callable_count.
    # This row contract is `additionalProperties: false`, so unlike the run
    # documents this is a HARD requirement, not a discipline choice.
    "contribution_row": ("contribution_row-1.9.schema.json", "1.9.0"),
    "contribution_row_1_7_historical": ("contribution_row-1.7.schema.json", "1.7.0"),
    "contribution_row_1_5_historical": ("contribution_row-1.5.schema.json", "1.5.0"),
    "contribution_container": ("contribution_container-1.5.schema.json", "1.5.0"),
    # Per-callable complexity (Complexity Contract 1.0.0).
    "callable_row": ("callable_row-1.10.schema.json", "1.10.0"),
    # The callables container has the SAME shape as the contribution container,
    # and `row_contract_version` there is a generic semver pattern rather than a
    # const, so the existing document describes it exactly. Aliased rather than
    # copied.
    "callable_container": ("contribution_container-1.5.schema.json", "1.5.0"),
    # Derived, explicitly non-authoritative formats (plan section 3.3).
    # `explain_output` is 1.1 because 1.0 described five properties while the
    # command emitted twelve under `additionalProperties: false`, so every real
    # invocation violated the schema that claimed to describe it. Nothing ever
    # validated the output, which is why the drift went unnoticed (F-P3-2).
    # 1.0 is retained, byte-for-byte unchanged, as historical material only.
    # 1.2 adds the `complexity-unavailable` group and three complexity fields
    # per entry. The document is `additionalProperties: false`, so a new field
    # is a new version; 1.1 and 1.0 are retained byte-for-byte as historical
    # material, and 1.2 was GENERATED from 1.1 by script rather than copied.
    # 1.3 adds the two cognitive fields on every entry. Each superseded output
    # is retained under its own historical name, byte-for-byte.
    "explain_output": ("explain_output-1.3.schema.json", "1.3.0"),
    "explain_output_1_2_historical": ("explain_output-1.2.schema.json", "1.2.0"),
    "explain_output_1_1_historical": ("explain_output-1.1.schema.json", "1.1.0"),
    "explain_output_1_0_historical": ("explain_output-1.0.schema.json", "1.0.0"),
    "compare_output": ("compare_output-1.1.schema.json", "1.1.0"),
    "compare_output_1_0_historical": ("compare_output-1.0.schema.json", "1.0.0"),
    "reproduction_output": ("reproduction_output-1.1.schema.json", "1.1.0"),
    "reproduction_output_1_0_historical": ("reproduction_output-1.0.schema.json", "1.0.0"),
    # F-P3-7: `report_metadata` has no producer. `archlens report` writes an
    # HTML file and prints a byte count; it never emits this document. A
    # schema advertised as a current derived format while nothing can produce
    # it is a false contract, so it is demoted to historical rather than
    # justified by inventing an output nobody asked for.
    "report_metadata_historical": ("report_metadata-1.0.schema.json", "1.0.0"),
    "performance_profile": ("performance_profile-1.0.schema.json", "1.0.0"),
    # UI-neutral derived presentation data. It is intentionally absent from
    # modules.standalone_contracts because a Report View is never evidence.
    "report_view": ("report_view-1.0.schema.json", "1.0.0"),
    # Direct Revision Diff. Independent format version, not the Artifact Schema
    # version, and registered with a producer-to-schema contract test from the
    # first commit — `report_metadata` was registered with no producer at all
    # and `explain_output` drifted from its own schema unnoticed, both because
    # nothing exercised them.
    # 1.1 adds Complexity Contract 1.0.0 deltas under their own comparability
    # verdict. Generated from 1.0 by script; 1.0 retained byte-for-byte.
    # 1.2 adds the cognitive verdict and its three delta levels.
    "revision_diff_output": ("revision_diff-1.2.schema.json", "1.2.0"),
    "revision_diff_output_1_1_historical": ("revision_diff-1.1.schema.json", "1.1.0"),
    "revision_diff_output_1_0_historical": ("revision_diff-1.0.schema.json", "1.0.0"),
    # Policy-as-Code v1. The document is an INPUT contract and the result a
    # derived output; both are registered so `archlens schema export` ships
    # them, and both have producer-to-schema contract tests.
    "policy_document": ("policy_document-1.0.schema.json", "1.0.0"),
    "policy_result_output": ("policy_result-1.0.schema.json", "1.0.0"),
    # Policy v2 and `archlens check`. Two NEW files rather than edits: v1's
    # `policy_document-1.0` and `policy_result-1.0` are unchanged and remain
    # the contract for v1 documents and for `archlens policy evaluate`, which
    # keeps working exactly as before. A v2 document is a different input
    # contract and the check result is a different derived output, so each gets
    # its own version surface rather than a widened old one.
    # ACTIVATED: the producers now emit Policy 2.2.0 / Check Result 1.3.0, so the plain names take
    # the newest files and every predecessor keeps its bytes under a historical
    # name -- the same rename `explain_output` has been through three times.
    "policy_document_v2": ("policy_document-2.2.schema.json", "2.2.0"),
    "policy_document_v2_2_1_historical": (
        "policy_document-2.1.schema.json", "2.1.0",
    ),
    "policy_document_v2_2_0_historical": (
        "policy_document-2.0.schema.json", "2.0.0",
    ),
    "check_result_output": ("check_result-1.4.schema.json", "1.4.0"),
    "check_result_output_1_3_historical": (
        "check_result-1.3.schema.json", "1.3.0",
    ),
    "check_result_output_1_2_historical": (
        "check_result-1.2.schema.json", "1.2.0",
    ),
    "check_result_output_1_1_historical": (
        "check_result-1.1.schema.json", "1.1.0",
    ),
    "check_result_output_1_0_historical": (
        "check_result-1.0.schema.json", "1.0.0",
    ),
    "trusted_evidence_receipt": (
        "trusted_evidence_receipt-1.0.schema.json", "1.0.0",
    ),
    "ratchet_baseline": (
        "ratchet_baseline-1.0.schema.json", "1.0.0",
    ),
    # ArchLens 4.0 contract evolution, in two steps.
    #
    # Hotspot H1 published and activated `policy_document-2.1` (the Hotspot
    # metric vocabulary and the `hotspot_file` scope) and `check_result-1.1`
    # (the `evidence` block and the same finding scope).
    #
    # Duplication DP1 published and activated `policy_document-2.2` (the
    # Duplication metric vocabulary and the `duplication_group` scope) and
    # `check_result-1.2` (the `duplication` evidence kind, the same finding
    # scope and the `evidence_kind_not_requested` reason). Additive at every
    # step: nothing valid under a predecessor becomes invalid.
    #
    # Every predecessor file keeps its BYTES and its DIGEST; only its registry
    # name changes, because in this repository the plain name tracks WHAT THE
    # PRODUCER EMITS. DP1 publishes its two schemas in the same commit that
    # moves the producers, deliberately: H1 published `check_result-1.1` one
    # commit ahead of its producer, and that file pinned
    # `evaluated_as_format_version` to a version the eventual producer did not
    # emit -- a contract no document could satisfy, which the next commit had to
    # repair. Baseline/Ratchet BR4 now publishes `check_result-1.3`, adding only
    # the bounded ratchet summary and typed admission/evaluation failures; 1.2
    # remains frozen under its historical registry name. A schema and its
    # producer move together.
}

#: Document families whose schema is selected by the version the DOCUMENT
#: declares, rather than by any run bundle's artifact schema version.
#:
#: `HISTORICAL_SCHEMA_NAMES` cannot serve here: it is keyed on the artifact
#: schema of a run bundle, and a policy document is an INPUT that no run bundle
#: owns, while a check result is an output whose format version moves
#: independently of every measurement contract.
DOCUMENT_FORMAT_SCHEMAS: dict[str, dict[str, str]] = {
    "policy_document_v2": {
        "2.0.0": "policy_document_v2_2_0_historical",
        "2.1.0": "policy_document_v2_2_1_historical",
        "2.2.0": "policy_document_v2",
    },
    "check_result_output": {
        "1.0.0": "check_result_output_1_0_historical",
        "1.1.0": "check_result_output_1_1_historical",
        "1.2.0": "check_result_output_1_2_historical",
        "1.3.0": "check_result_output_1_3_historical",
        "1.4.0": "check_result_output",
    },
}

#: The field each family declares its own format version in.
DOCUMENT_FORMAT_VERSION_FIELD: dict[str, str] = {
    "policy_document_v2": "policy_document_format_version",
    "check_result_output": "check_result_format_version",
}


# Tabular row contracts were first published at 1.5 and are unchanged across
# every generation before 1.7, so all of them share this one set rather than
# repeating it four times.
_ROW_SCHEMAS_1_5: dict[str, str] = {
    "catalog_row": "catalog_row_1_5_historical",
    "sheet_metrics_row": "sheet_metrics_row_1_5_historical",
    "language_metrics_row": "language_metrics_row_1_5_historical",
    "errors_row": "errors_row_1_5_historical",
    "recoveries_row": "recoveries_row_1_5_historical",
    "normalized_input_row": "normalized_input_row_1_5_historical",
    "contribution_row": "contribution_row_1_5_historical",
}

# The four authoritative run documents as published at Artifact Schema 1.5.0.
# 1.3 and 1.4 reuse this set; see the note on those entries below.
_RUN_DOCUMENTS_1_5: dict[str, str] = {
    "run_manifest": "run_manifest_1_5_historical",
    "run_status": "run_status_1_5_historical",
    "analysis": "analysis_1_5_historical",
    "repository_document": "repository_document_1_5_historical",
}

#: Schema set to use for an artifact declaring an older Artifact Schema, keyed
#: on ``(major, minor)``. Deliberately a small explicit table, not a general
#: version-resolution engine: literal schema validity has to be judged against
#: the contract the artifact *declares*, or a historical artifact would be
#: called valid merely because a newer, more permissive schema happens to accept
#: the same bytes — and, symmetrically, an older artifact would be called
#: *invalid* merely for lacking fields that its own generation never defined.
#:
#: Every ``(major, minor)`` that ``compatibility.SUPPORTED_ARTIFACT_SCHEMAS``
#: declares supported and non-native MUST appear here. Absence does not mean
#: "no mapping needed"; it silently means "judge it against the newest schema",
#: which is the exact defect this table exists to prevent.
#: ``tests/test_artifact_io_v35.py`` enforces that invariant mechanically.
#: The five documents Artifact Schema 1.9 changed, mapped back to the 1.7 bytes
#: that every earlier supported generation actually declares. Shared by (1,8) and
#: (1,7) rather than repeated.
_DOCUMENTS_CHANGED_BY_1_9: dict[str, str] = {
    "run_manifest": "run_manifest_1_7_historical",
    "run_status": "run_status_1_7_historical",
    "analysis": "analysis_1_7_historical",
    "repository_document": "repository_document_1_7_historical",
    "contribution_row": "contribution_row_1_7_historical",
}

#: The five documents Artifact Schema 1.10 changed, and their 1.9 predecessors.
#: `callable_row` is here and was NOT in the 1.9 set, because the callables
#: artifact did not exist before 1.9: there is nothing older to remap it to.
_DOCUMENTS_CHANGED_BY_1_10: dict[str, str] = {
    "run_manifest": "run_manifest_1_9_historical",
    "run_status": "run_status_1_9_historical",
    "analysis": "analysis_1_9_historical",
    "repository_document": "repository_document_1_9_historical",
    "callable_row": "callable_row_1_9_historical",
}

#: The four documents Artifact Schema 1.11 changed, mapped back to the bytes
#: each earlier generation actually declares. `analysis` and
#: `repository_document` are absent BECAUSE THEY DID NOT MOVE: R0 adds a
#: qualification authority beside measurement and changes no measurement
#: document, so there is nothing to remap for them.
#:
#: The three row contracts resolve to their 1.7 bytes, which every generation
#: from 1.7 through 1.10 actually declares. Without these entries a 1.10 run
#: would be judged against the 1.11 row schemas and reported invalid purely for
#: lacking qualification columns its own generation never defined -- the exact
#: failure mode this table exists to prevent.
_DOCUMENTS_CHANGED_BY_1_11: dict[str, str] = {
    "run_manifest": "run_manifest_1_10_historical",
    "catalog_row": "catalog_row_1_7_historical",
    "sheet_metrics_row": "sheet_metrics_row_1_7_historical",
    "language_metrics_row": "language_metrics_row_1_7_historical",
}

#: The two provenance documents Artifact Schema 1.12 changed, mapped to the
#: exact bytes every earlier generation declared.
_DOCUMENTS_CHANGED_BY_1_12: dict[str, str] = {
    "run_manifest": "run_manifest_1_11_historical",
    "environment": "environment_1_5_historical",
}

#: The three row contracts as published at 1.7, for generations older than 1.11
#: that must ALSO remap the documents their own generation changed.
_ROW_SCHEMAS_1_7: dict[str, str] = {
    "catalog_row": "catalog_row_1_7_historical",
    "sheet_metrics_row": "sheet_metrics_row_1_7_historical",
    "language_metrics_row": "language_metrics_row_1_7_historical",
}

HISTORICAL_SCHEMA_NAMES: dict[tuple[int, int], dict[str, str]] = {
    (1, 11): dict(_DOCUMENTS_CHANGED_BY_1_12),
    # 1.10 differs from the native 1.11 in exactly the four documents
    # qualification touched.
    (1, 10): {**_DOCUMENTS_CHANGED_BY_1_12, **_DOCUMENTS_CHANGED_BY_1_11},
    # 1.9 differs from the native 1.11 in the four documents qualification
    # touched AND the five cognitive complexity touched. `run_manifest` appears
    # in both sets; the 1.9 mapping wins because it is the more specific
    # generation, which is why this merge is ordered rather than a plain union.
    # `contribution_row` is native-shared at 1.9 and needs no remap.
    (1, 9): {
        **_DOCUMENTS_CHANGED_BY_1_12,
        **_ROW_SCHEMAS_1_7,
        **_DOCUMENTS_CHANGED_BY_1_10,
    },
    # 1.8 differs in the five documents complexity touched, plus the three row
    # contracts qualification later touched. `recoveries_row` is already
    # native-shared at 1.8 and needs no remap here.
    (1, 8): {
        **_DOCUMENTS_CHANGED_BY_1_12,
        **_ROW_SCHEMAS_1_7,
        **_DOCUMENTS_CHANGED_BY_1_9,
    },
    # 1.7 differs in those, plus `recoveries_row`, which 1.8 corrected.
    (1, 7): {
        **_DOCUMENTS_CHANGED_BY_1_12,
        **_ROW_SCHEMAS_1_7,
        **_DOCUMENTS_CHANGED_BY_1_9,
        "recoveries_row": "recoveries_row_1_7_historical",
    },
    (1, 6): {
        **_DOCUMENTS_CHANGED_BY_1_12,
        **_ROW_SCHEMAS_1_5,
        "run_manifest": "run_manifest_1_6_historical",
        "run_status": "run_status_1_6_historical",
        "analysis": "analysis_1_6_historical",
        "repository_document": "repository_document_1_6_historical",
        # Artifact Schema 1.6 pairs with Inventory Schema 1.7.
    },
    (1, 5): {
        **_DOCUMENTS_CHANGED_BY_1_12,
        **_ROW_SCHEMAS_1_5,
        **_RUN_DOCUMENTS_1_5,
        # Artifact Schema 1.5 pairs with Inventory Schema 1.6.
        "file_inventory": "file_inventory_1_6_historical",
    },
    # Artifact Schema 1.4 and 1.3 (ArchLens 3.4.0 / 3.3.0) are supported through
    # reviewed adapters and preserved complete fixtures, but **no 1.3 or 1.4
    # schema document was ever published**. Artifact Schema 1.5.0 is the oldest
    # published contract, and the preserved 1.3 and 1.4 fixtures satisfy it
    # literally, with zero violations and zero waivers.
    #
    # So 1.5 is the contract these generations are judged against, and that is
    # named here rather than left to the fallthrough. Leaving them unmapped
    # judged them against Artifact 1.7, which requires `subject_key`,
    # `source_mode` and `subject_key_basis` — fields that postdate these
    # artifacts by three generations — reporting a sound historical artifact as
    # `invalid`. That was a defect in the historical-compatibility path, not a
    # property of the fixtures.
    #
    # The 1.5 compatibility waivers are NOT inherited: `known_exceptions`
    # applies them only to an artifact declaring exactly 1.5.0.
    (1, 4): {
        **_DOCUMENTS_CHANGED_BY_1_12,
        **_ROW_SCHEMAS_1_5,
        **_RUN_DOCUMENTS_1_5,
        # Artifact Schema 1.4 pairs with Inventory Schema 1.6.
        "file_inventory": "file_inventory_1_6_historical",
    },
    (1, 3): {
        **_DOCUMENTS_CHANGED_BY_1_12,
        **_ROW_SCHEMAS_1_5,
        **_RUN_DOCUMENTS_1_5,
        # Artifact Schema 1.3 declares Inventory Schema 1.5.0, for which no
        # schema document was ever published either. 1.6 is the oldest published
        # inventory contract and the preserved 1.3 fixtures satisfy it exactly;
        # the current 1.7 inventory schema is deliberately not used here.
        "file_inventory": "file_inventory_1_6_historical",
    },
}


def schema_name_for(logical: str, declared_artifact_schema: Any) -> str:
    """Registry name to validate ``logical`` against, given a declared version."""
    from .compatibility import parse_version

    parsed = parse_version(declared_artifact_schema)
    if parsed is None:
        return logical
    return HISTORICAL_SCHEMA_NAMES.get((parsed[0], parsed[1]), {}).get(logical, logical)


class UnsupportedDocumentFormat(ValueError):
    """A document declares a format version this build publishes no schema for."""


def schema_name_for_format(logical: str, declared_format_version: Any) -> str:
    """Registry name for one document family, given the version it declares.

    **Unknown versions fail loudly.** There is deliberately no fallback to the
    newest schema and none to the oldest: judging a document against a contract
    it did not claim produces either a false pass (the document is missing a
    field the newer schema does not require) or a false failure, and both are
    worse than being told the version is unrecognized. A build that cannot name
    the contract cannot vouch for the document.

    ``None`` and a non-string are refused for the same reason: an absent
    declaration is not a version.
    """
    table = DOCUMENT_FORMAT_SCHEMAS.get(logical)
    if table is None:
        raise UnsupportedDocumentFormat(
            f"{logical!r} is not a version-selected document family; known "
            f"families: {', '.join(sorted(DOCUMENT_FORMAT_SCHEMAS))}"
        )
    name = table.get(declared_format_version) if isinstance(
        declared_format_version, str
    ) else None
    if name is None:
        raise UnsupportedDocumentFormat(
            f"{logical} declares format version {declared_format_version!r}, "
            f"which this build publishes no schema for; supported versions: "
            f"{', '.join(sorted(table))}"
        )
    return name


def schema_name_for_document(logical: str, document: Any) -> str:
    """Registry name for one document, read from its own declared version."""
    field = DOCUMENT_FORMAT_VERSION_FIELD.get(logical)
    if field is None:
        raise UnsupportedDocumentFormat(
            f"{logical!r} is not a version-selected document family; known "
            f"families: {', '.join(sorted(DOCUMENT_FORMAT_VERSION_FIELD))}"
        )
    declared = (
        document.get(field) if isinstance(document, Mapping) else None
    )
    return schema_name_for_format(logical, declared)


class SchemaDependencyUnavailable(RuntimeError):
    """Raised when ``jsonschema`` is not importable."""


def _require_jsonschema():
    """Return the Draft 2020-12 validator class, or fail explicitly.

    Three conditions are checked, in order, and every failure is named:

    1. ``jsonschema`` imports at all;
    2. the installed distribution version is **exactly**
       :data:`REQUIRED_JSONSCHEMA_VERSION`;
    3. ``Draft202012Validator`` exists.

    Importability alone is not sufficient. ``jsonschema`` gained Draft 2020-12
    support in 4.0; earlier releases import cleanly and then have no
    ``Draft202012Validator``, so a guard that only checked the import would let
    an ancient release reach the validator call and fail with an obscure
    ``AttributeError``.

    **No fallback to an older draft ever occurs.** A dialect downgrade silently
    changes which constraints are enforced, which is indistinguishable from a
    passing validation.
    """
    try:
        from jsonschema import validators
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise SchemaDependencyUnavailable(
            "structural schema validation requires the 'jsonschema' package, which "
            f"is a declared ArchLens runtime dependency pinned at "
            f"{REQUIRED_JSONSCHEMA_VERSION}. Validation is refused rather than "
            "skipped, because a skipped check cannot be distinguished from a "
            "passing one."
        ) from exc

    installed = jsonschema_version()
    if installed != REQUIRED_JSONSCHEMA_VERSION:
        raise SchemaDependencyUnavailable(
            f"ArchLens requires jsonschema=={REQUIRED_JSONSCHEMA_VERSION} exactly; "
            f"the installed release is {installed or 'unknown'}. Validation is "
            "refused rather than attempted against an unpinned build, because a "
            "different validator release can change which constraints are "
            "enforced and would make acceptance evidence non-reproducible."
        )

    validator_class = getattr(validators, "Draft202012Validator", None)
    if validator_class is None:
        raise SchemaDependencyUnavailable(
            f"the installed 'jsonschema' release {installed} provides no "
            "Draft202012Validator. ArchLens artifact schemas are Draft 2020-12. "
            "Validation is refused rather than downgraded to an older dialect."
        )
    return validator_class


def jsonschema_available() -> bool:
    """Report availability without raising, for diagnostics such as ``archlens doctor``."""
    try:
        _require_jsonschema()
    except SchemaDependencyUnavailable:
        return False
    return True


def jsonschema_version() -> str | None:
    """Return the installed ``jsonschema`` version, or ``None`` when unavailable."""
    try:
        from importlib import metadata

        return metadata.version("jsonschema")
    except Exception:  # pragma: no cover - environment dependent
        return None


def schema_names() -> tuple[str, ...]:
    return tuple(SCHEMA_REGISTRY)


def _resource_text(filename: str) -> str:
    from importlib import resources

    try:
        resource = resources.files(SCHEMA_PACKAGE).joinpath(SCHEMA_DIRECTORY, filename)
        return resource.read_text(encoding="utf-8")
    except (FileNotFoundError, ModuleNotFoundError, AttributeError) as exc:
        raise_structural(
            StructuralErrorCode.SCHEMA_NOT_FOUND, filename,
            f"schema resource is not installed: {exc}",
        )
        raise  # pragma: no cover


@lru_cache(maxsize=None)
def _load_schema_file(filename: str) -> dict[str, Any]:
    """Parse one packaged schema exactly as stored on disk."""
    try:
        return json.loads(_resource_text(filename))
    except json.JSONDecodeError as exc:
        raise_structural(
            StructuralErrorCode.SCHEMA_INVALID, filename,
            f"schema resource is not valid JSON: {exc.msg}",
            location=f"line {exc.lineno}, column {exc.colno}",
        )
        raise  # pragma: no cover


def schema_identity(name: str) -> str:
    """Return the effective, unique URI for one registered schema resource.

    Published historical bytes are immutable. Seven files carry a predecessor's
    ``$id``, so trusting the embedded value lets a referencing registry silently
    replace one generation with another. The registered filename is already the
    stable public generation identity; the active loaded view therefore uses
    its canonical ArchLens URI while the packaged bytes remain untouched.
    """
    return f"{SCHEMA_BASE_URI}{schema_filename(name)}"


@lru_cache(maxsize=None)
def load_schema(name: str) -> dict[str, Any]:
    """Load one schema with its effective registry identity applied in memory."""
    try:
        filename, _version = SCHEMA_REGISTRY[name]
    except KeyError:
        raise_structural(
            StructuralErrorCode.SCHEMA_NOT_FOUND, name,
            f"unknown schema name {name!r}; known names: {', '.join(schema_names())}",
        )
        raise  # pragma: no cover
    document = dict(_load_schema_file(filename))
    document["$id"] = f"{SCHEMA_BASE_URI}{filename}"
    return document


def schema_version(name: str) -> str:
    try:
        return SCHEMA_REGISTRY[name][1]
    except KeyError:
        raise_structural(
            StructuralErrorCode.SCHEMA_NOT_FOUND, name, f"unknown schema name {name!r}"
        )
        raise  # pragma: no cover


def schema_filename(name: str) -> str:
    try:
        return SCHEMA_REGISTRY[name][0]
    except KeyError:
        raise_structural(
            StructuralErrorCode.SCHEMA_NOT_FOUND, name, f"unknown schema name {name!r}"
        )
        raise  # pragma: no cover


def iter_format_keywords() -> Iterator[tuple[str, str]]:
    """Yield ``(schema_name, json_pointer)`` for every use of ``format``.

    ArchLens does not enforce ``format``. Draft 2020-12 makes it an annotation by
    default, and ``jsonschema`` only enforces it when a ``FormatChecker`` is
    supplied, which ArchLens deliberately does not supply. A schema that appears
    to constrain via ``format`` would therefore be silently unenforced, so the
    packaged schemas use ``pattern`` instead and a test asserts ``format`` is
    absent. Adding ``format`` later requires a deliberate ``FormatChecker``
    policy plus tests (requirement 4).
    """

    def walk(node: Any, pointer: str) -> Iterator[str]:
        if isinstance(node, dict):
            for key, value in node.items():
                child = f"{pointer}/{key}"
                if key == "format" and isinstance(value, str):
                    yield child
                yield from walk(value, child)
        elif isinstance(node, list):
            for index, value in enumerate(node):
                yield from walk(value, f"{pointer}/{index}")

    for name in schema_names():
        for pointer in walk(load_schema(name), ""):
            yield name, pointer


def _schema_resources() -> tuple[tuple[str, str, dict[str, Any]], ...]:
    """Return one effective resource per distinct packaged schema file."""
    resources: list[tuple[str, str, dict[str, Any]]] = []
    seen_filenames: set[str] = set()
    for name in schema_names():
        filename = schema_filename(name)
        if filename in seen_filenames:
            # Several logical contracts deliberately alias one byte-identical
            # schema file. One resource is correct; a second registration adds
            # no identity and must not look like a collision.
            continue
        seen_filenames.add(filename)
        schema = load_schema(name)
        resources.append((schema_identity(name), filename, schema))
    return tuple(resources)


def _identity_problems(
    resources: tuple[tuple[str, str, dict[str, Any]], ...],
) -> list[StructuralError]:
    """Report any effective URI assigned to more than one schema file."""
    problems: list[StructuralError] = []
    owners: dict[str, str] = {}
    for identifier, filename, _schema in resources:
        previous = owners.get(identifier)
        if previous is not None and previous != filename:
            problems.append(StructuralError(
                code=StructuralErrorCode.SCHEMA_INVALID,
                artifact=filename,
                message=(
                    f"schema identity {identifier!r} is already owned by "
                    f"{previous!r}; registry construction is refused"
                ),
                location="/$id",
                detail={"collides_with": previous, "schema_id": identifier},
            ))
        else:
            owners[identifier] = filename
    return problems


def schema_identity_problems() -> list[StructuralError]:
    """Return effective packaged-schema identity collisions, if any."""
    return _identity_problems(_schema_resources())


def schema_declaration_problems() -> list[StructuralError]:
    """Reject unreviewed embedded ``$id`` mismatches in packaged bytes."""
    problems: list[StructuralError] = []
    filenames = sorted({schema_filename(name) for name in schema_names()})
    for filename in filenames:
        declared = _load_schema_file(filename).get("$id")
        expected = f"{SCHEMA_BASE_URI}{filename}"
        if declared == expected:
            if filename in FROZEN_SCHEMA_ID_OVERRIDES:
                problems.append(StructuralError(
                    code=StructuralErrorCode.SCHEMA_INVALID,
                    artifact=filename,
                    message=(
                        "schema now declares its canonical identity but remains "
                        "in FROZEN_SCHEMA_ID_OVERRIDES"
                    ),
                    location="/$id",
                ))
            continue
        if FROZEN_SCHEMA_ID_OVERRIDES.get(filename) == declared:
            continue
        problems.append(StructuralError(
            code=StructuralErrorCode.SCHEMA_INVALID,
            artifact=filename,
            message=(
                f"schema declares $id {declared!r}; expected {expected!r}. "
                "Only reviewed frozen mismatches may be normalized in memory"
            ),
            location="/$id",
            detail={"declared_schema_id": declared, "expected_schema_id": expected},
        ))
    registered = set(filenames)
    for filename in sorted(set(FROZEN_SCHEMA_ID_OVERRIDES) - registered):
        problems.append(StructuralError(
            code=StructuralErrorCode.SCHEMA_INVALID,
            artifact=filename,
            message="frozen schema identity override has no registered resource",
            location="/$id",
        ))
    return problems


@lru_cache(maxsize=None)
def _registry():
    """Build a resolution registry containing only the packaged schemas.

    Cross-file ``$ref`` resolves exclusively against this allowlist. No retrieval
    callable is supplied, so an unknown ``$ref`` target raises instead of being
    fetched: schema resolution performs no network access under any
    circumstances (requirement 3, and plan section 3.6).
    """
    _require_jsonschema()
    from referencing import Registry, Resource
    from referencing.jsonschema import DRAFT202012

    schema_resources = _schema_resources()
    problems = schema_declaration_problems()
    problems.extend(_identity_problems(schema_resources))
    if problems:
        first = problems[0]
        raise_structural(
            first.code, first.artifact, first.message,
            location=first.location, **first.detail,
        )
    resources = [
        (identifier, Resource(contents=schema, specification=DRAFT202012))
        for identifier, _filename, schema in schema_resources
    ]
    # Registry() has no `retrieve` hook, so resolution is closed over these
    # resources alone.
    return Registry().with_resources(resources)


@lru_cache(maxsize=None)
def _all_schemas_checked() -> tuple[StructuralError, ...]:
    """Run ``check_schema`` across every packaged schema exactly once.

    Instance validation must not begin until this passes (requirement 2), so a
    malformed packaged schema is reported as a schema defect rather than
    surfacing later as a confusing instance failure.
    """
    validator_class = _require_jsonschema()
    problems: list[StructuralError] = []
    for name in schema_names():
        filename = schema_filename(name)
        try:
            validator_class.check_schema(load_schema(name))
        except Exception as exc:
            problems.append(
                StructuralError(
                    code=StructuralErrorCode.SCHEMA_INVALID,
                    artifact=filename,
                    message=f"schema is not a valid Draft 2020-12 document: {exc}",
                )
            )
    for name, pointer in iter_format_keywords():
        problems.append(
            StructuralError(
                code=StructuralErrorCode.SCHEMA_INVALID,
                artifact=schema_filename(name),
                message=(
                    "schema uses the 'format' keyword, which ArchLens does not "
                    "enforce. Use 'pattern', or add a deliberate FormatChecker "
                    "policy and tests first."
                ),
                location=pointer,
            )
        )
    problems.extend(schema_declaration_problems())
    problems.extend(_identity_problems(_schema_resources()))
    return tuple(problems)


def check_all_schemas() -> list[StructuralError]:
    """Validate every packaged schema against the Draft 2020-12 metaschema."""
    return list(_all_schemas_checked())


@lru_cache(maxsize=None)
def _validator_for(name: str):
    """Build and cache a Draft 2020-12 validator for one registered schema."""
    validator_class = _require_jsonschema()
    problems = _all_schemas_checked()
    if problems:
        first = problems[0]
        raise_structural(
            StructuralErrorCode.SCHEMA_INVALID, first.artifact,
            f"packaged schemas failed check_schema: {first.message}",
            location=first.location,
            total_schema_problems=len(problems),
        )
    return validator_class(load_schema(name), registry=_registry())


def _pointer(error: Any) -> str:
    """Render a JSON pointer for one library validation error."""
    parts = [str(part) for part in getattr(error, "absolute_path", [])]
    return "/" + "/".join(parts) if parts else "/"


def iter_violations(
    name: str, document: Any, artifact: str
) -> Iterator[StructuralError]:
    """Yield every Draft 2020-12 violation, deterministically ordered.

    Ordering is by JSON pointer then message so that two runs over the same
    document produce byte-identical reports.
    """
    validator = _validator_for(name)
    found = [
        StructuralError(
            code=StructuralErrorCode.SCHEMA_VIOLATION,
            artifact=artifact,
            message=error.message,
            location=_pointer(error),
            detail={
                "schema": name,
                "keyword": str(getattr(error, "validator", "")),
                "schema_pointer": "/" + "/".join(
                    str(part) for part in getattr(error, "absolute_schema_path", [])
                ),
            },
        )
        for error in validator.iter_errors(document)
    ]
    yield from sorted(found, key=lambda item: (item.location or "", item.message))


def validate_document(name: str, document: Any, artifact: str) -> list[StructuralError]:
    """Validate one document, returning every violation rather than the first."""
    return list(iter_violations(name, document, artifact))


def export_schemas(destination) -> list[str]:
    """Write every registered schema to a directory. Returns written filenames."""
    from pathlib import Path

    target = Path(destination)
    target.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for name in schema_names():
        filename = schema_filename(name)
        payload = json.dumps(load_schema(name), ensure_ascii=False, indent=2, sort_keys=True)
        (target / filename).write_text(payload + "\n", encoding="utf-8", newline="\n")
        written.append(filename)
    return sorted(written)
