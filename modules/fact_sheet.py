# -*- coding: utf-8 -*-

"""
fact_sheet.py
--------------
Generates human-readable fact sheets for each repository.

Output format:
- Markdown files stored in: output/fact_sheets/<repo_name>.md

Fact Sheets include:
- Metadata
- Static analysis summary
- API endpoints summary
- Deployability overview
- Database details
- Coverage estimation
- Classification label
"""

from pathlib import Path
import re


# =====================================================================
# UTIL: SAFE STRING
# =====================================================================
def safe(x):
    from modules.presentation import scalar
    from modules.summary import escape_markdown

    return escape_markdown(scalar(x, absent="unavailable"))


def safe_filename(value):
    value = "unknown_repo" if value is None else str(value)
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("_") or "unknown_repo"


def fact_sheet_filename(repo):
    name = (
        repo.get("metadata", {}).get("name")
        or repo.get("url", "unknown_repo").rstrip("/").split("/")[-1]
    )
    if name.endswith(".git"):
        name = name[:-4]
    filename_key = repo.get("storage_name") or name
    return name, f"{safe_filename(filename_key)}.md"


# =====================================================================
# RENDER SINGLE FACT SHEET (MARKDOWN)
# =====================================================================
def generate_fact_sheet(repo, output_dir=None):
    """
    Generates a markdown fact sheet for a single repository.
    """

    name, filename = fact_sheet_filename(repo)

    output_dir = Path(output_dir) if output_dir else (
        Path(__file__).parent.parent / "output" / "fact_sheets"
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    path = output_dir / filename

    meta = repo.get("metadata", {})
    static = repo.get("static", {})
    endpoints = repo.get("endpoints", [])
    deploy = repo.get("deployability", {})
    db = repo.get("db_schema", {})
    cov = repo.get("coverage", {})
    analyzed = repo.get("analysis_status") == "analyzed"

    # Markdown document
    md = []

    md.append(f"# Fact Sheet: {safe(meta.get('name') or name)}\n")
    md.append("---\n")
    md.append("## Fetch and Analysis Status\n")
    md.append(f"- **Fetch Status:** {safe(repo.get('fetch_status'))}")
    md.append(f"- **Fetch Method:** {safe(repo.get('fetch_method'))}")
    md.append(f"- **Fetch Error Type:** {safe(repo.get('fetch_error_type'))}")
    md.append(
        f"- **Fetch Error Message:** {safe(repo.get('fetch_error_message') or '')}"
    )
    md.append(f"- **Analysis Status:** {safe(repo.get('analysis_status'))}")
    md.append(
        f"- **Analysis Skip Reason:** {safe(repo.get('analysis_skip_reason') or '')}\n"
    )

    # --------------------------------------------------------------
    # METADATA
    # --------------------------------------------------------------
    md.append("## 1. Repository Metadata\n")
    md.append(f"- **Full Name:** {safe(meta.get('full_name'))}")
    md.append(f"- **Description:** {safe(meta.get('description'))}")
    md.append(f"- **Language:** {safe(meta.get('language'))}")
    md.append(f"- **Stars:** {safe(meta.get('stars'))}")
    md.append(f"- **Forks:** {safe(meta.get('forks'))}")
    md.append(f"- **Watchers:** {safe(meta.get('watchers'))}")
    md.append(f"- **Open Issues:** {safe(meta.get('open_issues'))}")
    md.append(f"- **Default Branch:** {safe(meta.get('default_branch'))}")
    md.append(f"- **License:** {safe(meta.get('license'))}\n")

    # --------------------------------------------------------------
    # STATIC ANALYSIS
    # --------------------------------------------------------------
    md.append("## 2. Static Analysis Summary\n")
    md.append(f"- **LOC:** {safe(static.get('loc'))}")
    md.append(f"- **Source Files:** {safe(static.get('source_files'))}")
    md.append(f"- **Classes:** {safe(static.get('classes'))}")
    md.append(f"- **Methods:** {safe(static.get('methods'))}")
    md.append(
        f"- **Classes / Structs / Interfaces:** "
        f"{safe(static.get('classes_structs'))}"
    )
    md.append(
        f"- **Methods / Functions:** {safe(static.get('methods_functions'))}"
    )
    md.append(
        f"- **Metric Extraction Status:** "
        f"{safe(static.get('metric_extraction_status'))}"
    )
    md.append(f"- **Inventory Status:** {safe(static.get('inventory_status'))}")
    md.append(f"- **Source Files Status:** {safe(static.get('source_files_status'))}")
    md.append(f"- **LOC Status:** {safe(static.get('loc_status'))}")
    md.append(
        f"- **Classes / Structs Status:** {safe(static.get('classes_structs_status'))}"
    )
    md.append(
        f"- **Methods / Functions Status:** {safe(static.get('methods_functions_status'))}"
    )
    md.append(f"- **Parse Failures:** {safe(static.get('parse_failure_count'))}\n")
    md.append("### Metric Sanity Check")
    md.append(f"- **Warning:** {safe(repo.get('metric_warning'))}")
    md.append(
        f"- **Reason:** {safe(repo.get('metric_warning_reason') or '')}\n"
    )
    md.append("### JavaScript / TypeScript Metric Audit")
    md.append(
        f"- **Scanned Files:** {safe(static.get('js_ts_scanned_files'))}"
    )
    md.append(
        f"- **Parse-Failed Files:** "
        f"{safe(static.get('js_ts_parse_failed_files'))}"
    )
    md.append(
        f"- **Skipped Generated/Bundled Files:** "
        f"{safe(static.get('js_ts_skipped_generated_files'))}"
    )
    md.append(
        f"- **Skipped Dependency Files:** "
        f"{safe(static.get('js_ts_skipped_dependency_files'))}"
    )
    md.append(
        f"- **Class Detection Status:** "
        f"{safe(static.get('class_detection_status'))}"
    )
    md.append(
        f"- **Class Detection Reason:** "
        f"{safe(static.get('class_detection_reason'))}"
    )
    md.append(
        f"- **Function Detection Status:** "
        f"{safe(static.get('function_detection_status'))}"
    )
    md.append(
        f"- **Metric Confidence:** {safe(static.get('metric_confidence'))}"
    )
    md.append(
        "- **TypeScript Interfaces Counted:** "
        f"{safe(static.get('js_ts_typescript_interfaces_counted'))}"
    )
    md.append(
        "- **TypeScript Type Aliases Counted:** "
        f"{safe(static.get('js_ts_typescript_type_aliases_counted'))}"
    )
    samples = static.get("class_detection_sample_files", [])
    if samples:
        md.append("- **Scanned Sample Files:** " + ", ".join(f"`{x}`" for x in samples))
    keyword_samples = static.get("class_detection_keyword_sample_files", [])
    if keyword_samples:
        md.append(
            "- **Class-Keyword Sample Files:** "
            + ", ".join(f"`{x}`" for x in keyword_samples)
        )
    skipped_samples = static.get("class_detection_skipped_sample_files", [])
    if skipped_samples:
        md.append(
            "- **Skipped Sample Files:** "
            + ", ".join(
                f"`{item['file']}` ({item['reason']})"
                for item in skipped_samples
            )
        )
    md.append("")
    md.append("### Go Metric Audit")
    md.append(f"- **Scanned Files:** {safe(static.get('go_scanned_files'))}")
    md.append(
        f"- **Parse-Failed Files:** {safe(static.get('go_parse_failed_files'))}"
    )
    md.append(
        "- **Skipped Generated Files:** "
        f"{safe(static.get('go_skipped_generated_files'))}"
    )
    md.append(
        "- **Skipped Dependency Files:** "
        f"{safe(static.get('go_skipped_dependency_files'))}"
    )
    md.append(
        "- **Metric Detection Status:** "
        f"{safe(static.get('go_metric_detection_status'))}"
    )
    md.append(
        "- **Metric Detection Reason:** "
        f"{safe(static.get('go_metric_detection_reason'))}"
    )
    md.append(
        "- **Struct/Interface Status:** "
        f"{safe(static.get('go_struct_detection_status'))}"
    )
    md.append(
        "- **Function/Method Status:** "
        f"{safe(static.get('go_function_detection_status'))}"
    )
    md.append(
        f"- **Metric Confidence:** {safe(static.get('go_metric_confidence'))}"
    )
    go_samples = static.get("go_detection_sample_files", [])
    if go_samples:
        md.append(
            "- **Scanned Sample Files:** "
            + ", ".join(f"`{item}`" for item in go_samples)
        )
    md.append("")

    # --------------------------------------------------------------
    # ENDPOINT SUMMARY
    # --------------------------------------------------------------
    md.append("## 3. API Endpoints\n")
    endpoint_count = (
        len(endpoints) if repo.get("analysis_status") == "analyzed" else "not applicable"
    )
    md.append(f"- **Endpoint Count:** {endpoint_count}")

    if endpoints:
        md.append("\n### Endpoint List:")
        for ep in endpoints:
            source = f" (`{ep['file']}`)" if ep.get("file") else ""
            md.append(f"- **{ep['method']}** {ep['route']}{source}")
    md.append("")

    # --------------------------------------------------------------
    # DEPLOYABILITY
    # --------------------------------------------------------------
    md.append("## 4. Deployability Overview\n")

    if deploy:
        md.append(f"- **Dockerfile:** {safe(deploy.get('dockerfile'))}")
        md.append(f"- **docker-compose:** {safe(deploy.get('docker_compose'))}")
        md.append(
            f"- **Kubernetes Manifests:** {safe(deploy.get('kubernetes_manifests'))}")
        md.append("\n### Build Tools:")
        for tool, present in deploy.get("build_tools", {}).items():
            md.append(f"- {safe(tool)}: {safe(present)}")

        md.append("\n### Run Scripts:")
        for script, present in deploy.get("run_scripts", {}).items():
            md.append(f"- {safe(script)}: {safe(present)}")
    elif not analyzed:
        md.append("- Analysis skipped; deployability was not assessed.")

    md.append("")

    # --------------------------------------------------------------
    # DATABASE
    # --------------------------------------------------------------
    md.append("## 5. Database Usage\n")
    md.append(f"- **Detected DB Type:** {safe(db.get('db_type'))}")

    md.append("\n### SQL Schema Files:")
    sqls = db.get("sql_schema_files", [])
    if sqls:
        for s in sqls:
            md.append(f"- {s}")
    elif analyzed:
        md.append("- None found")
    else:
        md.append("- Not applicable (analysis skipped)")

    md.append("\n### ORM Model Files:")
    orms = db.get("orm_definition_files", [])
    if orms:
        for o in orms:
            md.append(f"- {o}")
    elif analyzed:
        md.append("- None found")
    else:
        md.append("- Not applicable (analysis skipped)")

    md.append("")

    # --------------------------------------------------------------
    # COVERAGE
    # --------------------------------------------------------------
    md.append("## 6. Test Coverage\n")
    md.append(f"- **Test Files:** {safe(cov.get('test_file_count'))}")
    md.append(f"- **Test Cases:** {safe(cov.get('test_cases'))}")
    md.append(
        f"- **Estimated Coverage Ratio:** {safe(cov.get('estimated_coverage_ratio'))}\n")
    md.append(f"- **Test Case Density:** {safe(cov.get('test_case_density'))}")
    md.append(f"- **Estimate Type:** {safe(cov.get('coverage_kind'))}\n")

    # --------------------------------------------------------------
    # CLASSIFICATION
    # --------------------------------------------------------------
    md.append("## 7. Classification\n")
    md.append(f"- **Category:** {safe(repo.get('category'))}\n")

    # --------------------------------------------------------------
    # WRITE FILE
    # --------------------------------------------------------------
    temporary_path = path.with_suffix(".md.tmp")
    with open(temporary_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    temporary_path.replace(path)

    print(f"   -> Fact sheet written: {path}")
    return path


# =====================================================================
# GENERATE FACT SHEETS FOR ALL REPOS
# =====================================================================
def generate_fact_sheets(repos, output_dir=None):
    output_dir = Path(output_dir) if output_dir else (
        Path(__file__).parent.parent / "output" / "fact_sheets"
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    expected = set()
    for repo in repos:
        path = generate_fact_sheet(repo, output_dir=output_dir)
        expected.add(path.resolve())
    for path in output_dir.glob("*.md"):
        if path.resolve() not in expected:
            try:
                path.unlink()
            except OSError as exc:
                print(f"[WARN] Could not remove stale fact sheet {path}: {exc}")
