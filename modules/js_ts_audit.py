"""Compatibility facade for JS/TS audit evidence from the shared inventory."""

from __future__ import annotations

from modules.config import SUPPORTED_LANGUAGES
from modules.inventory import RepositoryInventory
from modules.inventory_audits import collect_inventory_audits


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


def collect_js_ts_audit(repo_path, static_metrics, inventory=None):
    """Return deterministic evidence without performing another filesystem walk."""
    shared = inventory or RepositoryInventory(repo_path)
    audit = collect_inventory_audits(shared, _metrics_contract(static_metrics))
    return {key: value for key, value in audit.items() if key.startswith("js_ts") or key.startswith("class_") or key in {"function_detection_status", "metric_confidence"}}
