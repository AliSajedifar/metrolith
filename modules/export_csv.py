# -*- coding: utf-8 -*-

"""
export_csv.py
--------------
Exports the full repository dataset into a CSV file:
output/csv/apps_catalog.csv

Each row corresponds to one repository and includes:
- basic metadata
- static analysis results
- deployability info
- db schema info
- coverage estimate
- endpoint count
- classification label
"""

import csv
import json
from pathlib import Path


def _json_field(mapping, key):
    if key not in mapping:
        return None
    return json.dumps(mapping[key], ensure_ascii=False, sort_keys=True)


def export_catalog(repos, output_path=None):
    """
    Writes all repository information into:
    output/csv/apps_catalog.csv
    """

    output_path = Path(output_path) if output_path else (
        Path(__file__).parent.parent / "output" / "csv" / "apps_catalog.csv"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Columns for CSV — extracted from repo dictionary
    headers = [
        "url",
        "type",
        "name",
        "full_name",
        "language",
        "stars",
        "forks",
        "loc",
        "source_files",
        "classes",
        "methods",
        "classes_structs",
        "methods_functions",
        "primary_language",
        "parse_failure_count",
        "parse_failure_files",
        "metric_extraction_status",
        "endpoint_count",
        "db_type",
        "coverage_ratio",
        "test_case_density",
        "category",
        "metric_warning",
        "metric_warning_reason",
        "js_ts_scanned_files",
        "js_ts_parse_failed_files",
        "js_ts_skipped_files",
        "js_ts_skipped_generated_files",
        "js_ts_skipped_dependency_files",
        "js_ts_skipped_test_files",
        "js_ts_skipped_by_category",
        "js_ts_extension_counts",
        "js_ts_class_declaration_search",
        "js_ts_typescript_interfaces_counted",
        "js_ts_typescript_type_aliases_counted",
        "js_ts_detected_classes_structs",
        "js_ts_detected_methods_functions",
        "class_detection_status",
        "class_detection_reason",
        "class_detection_sample_files",
        "class_detection_skipped_sample_files",
        "class_detection_keyword_sample_files",
        "class_detection_strong_keyword_sample_files",
        "class_detection_metadata_evidence",
        "js_ts_parse_failed_sample_files",
        "function_detection_status",
        "metric_confidence",
        "go_scanned_files",
        "go_parse_failed_files",
        "go_skipped_files",
        "go_skipped_generated_files",
        "go_skipped_dependency_files",
        "go_skipped_test_files",
        "go_skipped_by_category",
        "go_metric_detection_status",
        "go_metric_detection_reason",
        "go_struct_detection_status",
        "go_function_detection_status",
        "go_metric_confidence",
        "go_detected_classes_structs",
        "go_detected_methods_functions",
        "go_detection_sample_files",
        "go_skipped_sample_files",
        "go_parse_failed_sample_files",
        "fetch_status",
        "fetch_method",
        "fetch_error_type",
        "fetch_error_message",
        "analysis_status",
        "analysis_skip_reason",
        "status",
        "error",
    ]

    temporary_path = output_path.with_suffix(".csv.tmp")
    with open(temporary_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(headers)

        for repo in repos:
            meta = repo.get("metadata", {})
            static = repo.get("static", {})
            endpoints = repo.get("endpoints", [])
            db = repo.get("db_schema", {})
            cov = repo.get("coverage", {})

            row = [
                repo.get("url"),
                repo.get("type"),
                meta.get("name") or repo.get("repo_name"),
                meta.get("full_name")
                or (
                    f"{repo.get('owner')}/{repo.get('repo_name')}"
                    if repo.get("owner") and repo.get("repo_name")
                    else None
                ),
                meta.get("language"),
                meta.get("stars"),
                meta.get("forks"),
                static.get("loc"),
                static.get("source_files"),
                static.get("classes"),
                static.get("methods"),
                static.get("classes_structs"),
                static.get("methods_functions"),
                static.get("primary_language"),
                static.get("parse_failure_count"),
                _json_field(static, "parse_failure_files"),
                static.get("metric_extraction_status"),
                (
                    len(endpoints)
                    if repo.get("analysis_status") == "analyzed"
                    else None
                ),
                db.get("db_type"),
                cov.get("estimated_coverage_ratio"),
                cov.get("test_case_density"),
                repo.get("category"),
                repo.get("metric_warning"),
                repo.get("metric_warning_reason"),
                static.get("js_ts_scanned_files"),
                static.get("js_ts_parse_failed_files"),
                static.get("js_ts_skipped_files"),
                static.get("js_ts_skipped_generated_files"),
                static.get("js_ts_skipped_dependency_files"),
                static.get("js_ts_skipped_test_files"),
                _json_field(static, "js_ts_skipped_by_category"),
                _json_field(static, "js_ts_extension_counts"),
                static.get("js_ts_class_declaration_search"),
                static.get("js_ts_typescript_interfaces_counted"),
                static.get("js_ts_typescript_type_aliases_counted"),
                static.get("js_ts_detected_classes_structs"),
                static.get("js_ts_detected_methods_functions"),
                static.get("class_detection_status"),
                static.get("class_detection_reason"),
                _json_field(static, "class_detection_sample_files"),
                _json_field(static, "class_detection_skipped_sample_files"),
                _json_field(static, "class_detection_keyword_sample_files"),
                _json_field(static, "class_detection_strong_keyword_sample_files"),
                _json_field(static, "class_detection_metadata_evidence"),
                _json_field(static, "js_ts_parse_failed_sample_files"),
                static.get("function_detection_status"),
                static.get("metric_confidence"),
                static.get("go_scanned_files"),
                static.get("go_parse_failed_files"),
                static.get("go_skipped_files"),
                static.get("go_skipped_generated_files"),
                static.get("go_skipped_dependency_files"),
                static.get("go_skipped_test_files"),
                _json_field(static, "go_skipped_by_category"),
                static.get("go_metric_detection_status"),
                static.get("go_metric_detection_reason"),
                static.get("go_struct_detection_status"),
                static.get("go_function_detection_status"),
                static.get("go_metric_confidence"),
                static.get("go_detected_classes_structs"),
                static.get("go_detected_methods_functions"),
                _json_field(static, "go_detection_sample_files"),
                _json_field(static, "go_skipped_sample_files"),
                _json_field(static, "go_parse_failed_sample_files"),
                repo.get("fetch_status"),
                repo.get("fetch_method"),
                repo.get("fetch_error_type"),
                repo.get("fetch_error_message"),
                repo.get("analysis_status"),
                repo.get("analysis_skip_reason"),
                repo.get("status"),
                repo.get("error"),
            ]

            writer.writerow(row)

    temporary_path.replace(output_path)
    print(f"   -> Export complete: {output_path}")
