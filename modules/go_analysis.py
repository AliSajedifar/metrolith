"""Compatibility facade for Go parsing and shared-inventory audit evidence."""

from __future__ import annotations

from modules.config import SUPPORTED_LANGUAGES
from modules.core_metrics import ParserRegistry, _go_entities
from modules.inventory import RepositoryInventory
from modules.inventory_audits import collect_inventory_audits


def analyze_go_source(content):
    """Return main-metric Go structs and callables using the canonical definitions.

    Interfaces remain secondary evidence and are not folded into the first
    value. This aligns the legacy tuple with Metric Contract 3.0.0.
    """
    source = content.encode("utf-8") if isinstance(content, str) else bytes(content)
    try:
        root = ParserRegistry().get("Go", ".go").parse(source).root_node
        entities = _go_entities(root)
    except Exception:
        return None, None, False
    declarations = entities["structs"]
    callables = entities["module_functions"] + entities["receiver_methods"]
    return declarations, callables, not root.has_error


def _metrics_contract(static_metrics):
    if static_metrics.get("metrics", {}).get("by_language"):
        return static_metrics["metrics"]
    breakdown = static_metrics.get("language_breakdown", {})
    return {
        "by_language": {
            language.lower(): {
                "source_files": breakdown.get(language, {}).get("source_files", 0),
                "classes_structs": breakdown.get(language, {}).get("classes_structs", 0),
                "methods_functions": breakdown.get(language, {}).get("methods_functions", 0),
            }
            for language in SUPPORTED_LANGUAGES
        }
    }


def collect_go_audit(repo_path, static_metrics, inventory=None):
    """Return deterministic Go evidence without another filesystem traversal."""
    shared = inventory or RepositoryInventory(repo_path)
    audit = collect_inventory_audits(shared, _metrics_contract(static_metrics))
    return {key: value for key, value in audit.items() if key.startswith("go_")}
