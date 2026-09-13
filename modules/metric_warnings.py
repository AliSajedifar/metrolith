"""Non-fatal sanity checks for suspicious extracted metrics."""

from modules.repository_files import iter_repository_files, read_text


WEB_MARKERS = {
    "express",
    "fastify",
    "flask",
    "fastapi",
    "gin-gonic",
    "github.com/labstack/echo",
    "github.com/gorilla/mux",
    "spring-boot-starter-web",
    "spring-webmvc",
    "jakarta.ws.rs",
    "javax.ws.rs",
}

REFINED_FAILURE_STATUSES = {
    "source_read_failure",
    "source_encoding_failure",
    "source_oversized",
    "parser_execution_failure",
    "unsupported_language_version",
    "syntax_failed",
    "syntax_partial",
}


def detect_framework_markers(repo_path, inventory=None):
    markers = set()
    extensions = {
        ".go", ".gradle", ".java", ".js", ".json", ".kts", ".py",
        ".toml", ".ts", ".tsx", ".xml",
    }
    for path in iter_repository_files(
        repo_path, extensions=extensions, include_tests=False, inventory=inventory
    ):
        content = read_text(path, inventory=inventory)
        if not content:
            continue
        lowered = content.lower()
        markers.update(marker for marker in WEB_MARKERS if marker in lowered)
    return sorted(markers)


def assess_metric_warnings(static, endpoints, framework_markers=None):
    reasons = []
    source_files = static.get("source_files") or 0
    loc = static.get("loc")
    functions = static.get("methods_functions", static.get("methods"))
    classes = static.get("classes_structs", static.get("classes"))
    parse_failures = static.get("parse_failure_count", 0)

    if source_files >= 25 and functions == 0:
        reasons.append("large repository has zero detected methods/functions")
    class_status = static.get("class_detection_status")
    if class_status == "suspicious_zero":
        reasons.append(
            "JavaScript/TypeScript audit classified the zero class count as suspicious"
        )
    elif class_status in REFINED_FAILURE_STATUSES:
        reasons.append(
            "JavaScript/TypeScript audit could not fully confirm the zero class count"
        )
    elif source_files >= 50 and classes == 0 and class_status is None:
        reasons.append(
            "many source files have zero detected classes/structs/interfaces; "
            "this may be valid for functional JavaScript or Go packages"
        )
    go_status = static.get("go_metric_detection_status")
    if go_status == "suspicious_zero":
        reasons.append("Go audit found production files but zero named functions/methods")
    elif go_status in REFINED_FAILURE_STATUSES:
        reasons.append("Go audit could not fully validate one or more source files")
    if loc is not None and source_files >= 10 and loc < source_files * 3:
        reasons.append("very low LOC relative to source file count")
    if loc is not None and loc >= 200 and functions is not None and functions > loc * 0.45:
        reasons.append(
            "function density is unusually high; inspect callbacks or generated code"
        )
    if parse_failures:
        reasons.append(f"{parse_failures} source files could not be confidently parsed")
    js_parse_failures = static.get("js_ts_parse_failed_files", 0)
    if js_parse_failures:
        reasons.append(
            f"{js_parse_failures} JavaScript/TypeScript files failed lexical validation"
        )

    normalized_markers = {marker.lower() for marker in framework_markers or []}
    if normalized_markers & WEB_MARKERS and not endpoints:
        reasons.append("web framework markers were found but zero endpoints were detected")

    return {
        "metric_warning": bool(reasons),
        "metric_warning_reason": "; ".join(reasons),
    }
