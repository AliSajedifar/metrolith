"""Backward-compatible entry point for the versioned core metric engine."""

from __future__ import annotations

from pathlib import Path

from modules.core_metrics import (
    ParserRegistry,
    compatibility_static_result,
    compute_repository_metrics,
    validate_parser_initialization,
)
from modules.inventory import RepositoryInventory
from modules.inventory_audits import collect_inventory_audits


def perform_static_analysis(
    repo_path,
    inventory: RepositoryInventory | None = None,
    expected_language: str | None = None,
    parser_registry: ParserRegistry | None = None,
):
    """Compute the four core metrics without executing repository code.

    The flat return keys are retained for callers of the pre-2.0 API. The
    complete contract is available under ``result["metrics"]``.
    """
    repo_path = Path(repo_path)
    if not repo_path.is_dir():
        raise FileNotFoundError(f"Repository path does not exist: {repo_path}")
    shared_inventory = inventory or RepositoryInventory(repo_path)
    metrics = compute_repository_metrics(
        shared_inventory,
        expected_language=expected_language,
        parser_registry=parser_registry,
    )
    result = compatibility_static_result(metrics)
    result.update(collect_inventory_audits(shared_inventory, metrics))
    return result


__all__ = [
    "ParserRegistry",
    "perform_static_analysis",
    "validate_parser_initialization",
]
