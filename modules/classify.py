# -*- coding: utf-8 -*-

"""
classify.py
-----------
Classifies a repository into high-level categories based on:
- Metadata
- Static analysis (LOC, class/method counts)
- Deployability characteristics
- Database presence
- API endpoints
- Test coverage

This classification is used for:
- Fact sheet generation
- Research benchmarks
- Filtering viable candidate systems
"""


# =====================================================================
# MAIN CLASSIFIER
# =====================================================================
def classify_application(repo):
    """
    Returns a string label representing the category of the application.
    """

    # Extract components from repo dictionary
    meta = repo.get("metadata", {})
    static = repo.get("static", {})
    deploy = repo.get("deployability", {})
    db_info = repo.get("db_schema", {})
    coverage = repo.get("coverage", {})
    endpoints = repo.get("endpoints", [])

    # ------------------------------------------------------------------
    # HEURISTICS FOR CLASSIFICATION
    # ------------------------------------------------------------------

    # If DB exists and many SQL/ORM files detected → database-heavy
    if db_info and (
        db_info.get("db_type") not in {None, "redis"}
        or len(db_info.get("sql_schema_files", [])) > 3
        or len(db_info.get("orm_definition_files", [])) > 5
    ):
        return "database-heavy"

    # If many API endpoints and high method count → service/API heavy
    if len(endpoints) >= 15 and (static.get("methods") or 0) > 50:
        return "api-heavy"

    # If test coverage is strong → well-tested
    if (
        coverage.get("test_file_count", 0) >= 3
        and coverage.get("estimated_coverage_ratio", 0) >= 0.25
    ):
        return "test-strong"

    # If Dockerfile exists → likely deployable service
    if deploy and deploy.get("dockerfile"):
        return "deployable"

    # Very small repositories → toy / tutorial code
    if (static.get("loc") or 0) < 500 and (static.get("source_files") or 0) < 10:
        return "toy-example"

    # If metadata.language indicates typical microservices stack
    if meta.get("language") in {"Go", "Java", "JavaScript"}:
        if len(endpoints) >= 8:
            return "likely-microservice"

    # Default fallback
    return "general-application"
