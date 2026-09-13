"""Packaged ArchLens data resources.

Contains no executable logic. Exists so that ``validation/resources/schemas``
resolves through ``importlib.resources`` from an installed wheel, outside any
source checkout (plan section 7.3).

This adds no new top-level namespace: ``validation`` is already an installed
package in ArchLens 3.4.0.
"""

from __future__ import annotations

__all__: list[str] = []
