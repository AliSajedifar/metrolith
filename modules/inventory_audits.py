"""Deterministic JS/TS and Go audit evidence derived from the shared inventory."""

from __future__ import annotations

import re
from collections import Counter

from modules.inventory import RepositoryInventory


CLASS_HINT = re.compile(
    r"\bclass\b|\binterface\b|@Entity\b|@Controller\b|@Injectable\b|\bextends\b|\bnew\s+Schema\b"
)
STRONG_CLASS_HINT = re.compile(
    r"\bclass\s+[A-Za-z_$]|\binterface\s+[A-Za-z_$]|@Entity\b|@Controller\b|@Injectable\b"
)

FAILURE_CATEGORY_PRIORITY = (
    "source_read_failure",
    "source_encoding_failure",
    "source_oversized",
    "parser_execution_failure",
    "unsupported_language_version",
    "syntax_failed",
    "syntax_partial",
)


def _failure_categories(records) -> list[str]:
    """Return stable, refined categories for records whose analysis was incomplete."""

    categories = {
        record.error_category
        or ("syntax_partial" if record.parse_status == "partial" else "parser_execution_failure")
        for record in records
    }
    return sorted(categories)


def _dominant_failure_category(records) -> str:
    categories = _failure_categories(records)
    return next(
        (category for category in FAILURE_CATEGORY_PRIORITY if category in categories),
        categories[0] if categories else "parser_execution_failure",
    )


def collect_inventory_audits(inventory: RepositoryInventory, metrics: dict) -> dict:
    result = {}
    by_language = metrics["by_language"]
    pruned = Counter(
        item["exclusion_reason"] for item in inventory.pruned_directories
    )

    js_records = [
        record for record in inventory if record.detected_language in {"JavaScript", "TypeScript"}
    ]
    js_included = [record for record in js_records if record.included_in_metrics]
    js_skipped = Counter(record.exclusion_reason or "other" for record in js_records if not record.included_in_metrics)
    js_failed = [record for record in js_included if record.parse_status in {"partial", "failed"}]
    js_failure_categories = _failure_categories(js_failed)
    js_failure_status = _dominant_failure_category(js_failed)
    js_classes = sum(
        by_language[name]["classes_structs"] or 0 for name in ("javascript", "typescript")
    )
    js_functions = sum(
        by_language[name]["methods_functions"] or 0 for name in ("javascript", "typescript")
    )
    hints = []
    strong_hints = []
    function_framework = False
    metadata_evidence = []
    for record in inventory:
        if record.filename.lower() not in {"package.json", "readme", "readme.md"}:
            continue
        content = inventory.read_text(record)
        if content and any(marker in content.lower() for marker in ("express", "fastify", "koa", "react", "next.js")):
            function_framework = True
            metadata_evidence.append(record.relative_path)
    for record in js_included:
        content = inventory.read_text(record)
        if content and CLASS_HINT.search(content):
            hints.append(record.relative_path)
        if content and STRONG_CLASS_HINT.search(content):
            strong_hints.append(record.relative_path)
    if not js_included:
        class_status = "not_applicable_no_js_ts"
    elif js_failed and not js_classes:
        class_status = js_failure_status
    elif js_classes:
        class_status = "classes_detected"
    elif strong_hints or by_language["typescript"]["source_files"] >= 5:
        class_status = "suspicious_zero"
    elif js_functions and function_framework:
        class_status = "not_applicable_function_based_js"
    else:
        class_status = "checked_no_classes_found"
    class_reasons = {
        "not_applicable_no_js_ts": "No included JavaScript/TypeScript files were found",
        "classes_detected": f"Detected {js_classes} named or stably assigned JavaScript/TypeScript classes",
        "suspicious_zero": "TypeScript volume or class-oriented markers make the zero worthy of review",
        "not_applicable_function_based_js": "Function-oriented framework evidence and named functions explain the zero",
        "checked_no_classes_found": "Included files parsed without named or stably assigned classes",
    }
    class_reason = class_reasons.get(
        class_status,
        "Incomplete files prevent confirmation of a zero class count "
        f"({', '.join(js_failure_categories)})",
    )
    result.update(
        {
            "js_ts_scanned_files": len(js_included),
            "js_ts_files_scanned": len(js_included),
            "js_ts_skipped_files": len(js_records) - len(js_included),
            "js_ts_files_skipped": len(js_records) - len(js_included),
            "js_ts_skipped_by_category": dict(sorted(js_skipped.items())),
            "js_ts_extension_counts": dict(
                sorted(Counter(record.extension for record in js_included).items())
            ),
            "js_ts_parse_failed_files": len(js_failed),
            "js_ts_parse_failed_sample_files": [record.relative_path for record in js_failed[:5]],
            "js_ts_error_categories": js_failure_categories,
            "class_detection_status": class_status,
            "class_detection_reason": class_reason,
            "class_detection_sample_files": hints[:5],
            "class_detection_keyword_sample_files": hints[:5],
            "class_detection_strong_keyword_sample_files": strong_hints[:5],
            "class_detection_skipped_sample_files": [
                {"file": record.relative_path, "reason": record.exclusion_reason}
                for record in js_records if not record.included_in_metrics
            ][:5],
            "js_ts_skipped_dependency_files": sum(
                value for key, value in js_skipped.items() if key in {"dependency", "vendor", "excluded_directory"}
            ),
            "js_ts_skipped_generated_files": sum(
                value for key, value in js_skipped.items() if key in {"generated", "build_output", "minified_or_bundled"}
            ),
            "js_ts_skipped_test_files": js_skipped.get("test", 0),
            "js_ts_pruned_dependency_directories": sum(
                pruned[key] for key in ("dependency", "vendor", "cache", "excluded_directory")
            ),
            "js_ts_pruned_generated_directories": sum(
                pruned[key] for key in ("generated", "build_output")
            ),
            "js_ts_class_declaration_search": bool(js_included),
            "js_ts_typescript_interfaces_counted": False,
            "js_ts_typescript_type_aliases_counted": False,
            "js_ts_detected_classes_structs": js_classes,
            "js_ts_detected_methods_functions": js_functions,
            "class_detection_metadata_evidence": metadata_evidence[:5],
            "function_detection_status": (
                "functions_detected" if js_functions else js_failure_status if js_failed else "checked_no_functions_found"
            ),
            "metric_confidence": (
                "not_applicable" if not js_included else "low" if len(js_failed) == len(js_included) else "medium" if js_failed or class_status == "suspicious_zero" else "high"
            ),
            "js_ts_audit_status": (
                "not_applicable" if not js_included else "partial" if js_failed else "complete"
            ),
            "js_ts_audit_note": "Evidence comes from the canonical inventory and Tree-sitter parse statuses.",
        }
    )

    go_records = [record for record in inventory if record.detected_language == "Go"]
    go_included = [record for record in go_records if record.included_in_metrics]
    go_skipped = Counter(record.exclusion_reason or "other" for record in go_records if not record.included_in_metrics)
    go_failed = [record for record in go_included if record.parse_status in {"partial", "failed"}]
    go_failure_categories = _failure_categories(go_failed)
    go_failure_status = _dominant_failure_category(go_failed)
    go_metric = by_language["go"]
    structs = go_metric["classes_structs"] or 0
    functions = go_metric["methods_functions"] or 0
    if not go_included:
        go_status = "not_applicable_no_go"
    elif go_failed:
        go_status = go_failure_status
    elif not functions:
        go_status = "suspicious_zero"
    elif not structs:
        go_status = "checked_no_structs_found"
    else:
        go_status = "metrics_detected"
    go_struct_status = (
        "not_applicable_no_go" if not go_included else go_failure_status if go_failed and not structs
        else "structs_detected" if structs else "checked_no_structs_found"
    )
    go_function_status = (
        "not_applicable_no_go" if not go_included else go_failure_status if go_failed and not functions
        else "functions_detected" if functions else "suspicious_zero"
    )
    result.update(
        {
            "go_scanned_files": len(go_included),
            "go_files_scanned": len(go_included),
            "go_skipped_files": len(go_records) - len(go_included),
            "go_files_skipped": len(go_records) - len(go_included),
            "go_skipped_by_category": dict(sorted(go_skipped.items())),
            "go_parse_failed_files": len(go_failed),
            "go_parse_failed_sample_files": [record.relative_path for record in go_failed[:5]],
            "go_error_categories": go_failure_categories,
            "go_detection_sample_files": [record.relative_path for record in go_included[:5]],
            "go_metric_detection_status": go_status,
            "go_struct_detection_status": go_struct_status,
            "go_function_detection_status": go_function_status,
            "go_interfaces_counted_in_main_metric": False,
            "go_skipped_dependency_files": sum(
                value for key, value in go_skipped.items() if key in {"dependency", "vendor", "excluded_directory"}
            ),
            "go_skipped_generated_files": sum(
                value for key, value in go_skipped.items() if key in {"generated", "build_output", "minified_or_bundled"}
            ),
            "go_skipped_test_files": go_skipped.get("test", 0),
            "go_pruned_dependency_directories": sum(
                pruned[key] for key in ("dependency", "vendor", "cache", "excluded_directory")
            ),
            "go_pruned_generated_directories": sum(
                pruned[key] for key in ("generated", "build_output")
            ),
            "go_metric_confidence": (
                "not_applicable" if not go_included else "low" if len(go_failed) == len(go_included) else "medium" if go_failed else "high"
            ),
            "go_audit_status": (
                "not_applicable" if not go_included else "partial" if go_failed else "complete"
            ),
            "go_audit_note": (
                "Main Classes / Structs counts named module-scope structs only; "
                "Go interfaces are secondary evidence. Data comes from the canonical "
                "inventory and Tree-sitter Go grammar."
            ),
        }
    )
    return result
